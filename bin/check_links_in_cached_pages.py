#! /usr/bin/env python3

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Check links found in cached HTML pages for 404 / 5xx responses.

Runs in a workflow_run-triggered workflow after a site-scanning run completes.
Loads the page_cache/ dump produced by scan_site.py --export-cache, extracts
every <a>/<script>/<link> URL, fetches each one, and opens a GitHub issue per
status code (404 / 5xx) listing the dead links and the pages they appear on.
"""

import json
import os
import subprocess
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha512
from typing import Dict, List, Optional, Set
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import click
import requests
from bs4 import BeautifulSoup

# Awkward hack to allow importing into tests
try:
    from utils import _print, load_html_pages, ping_slack
except ImportError:
    from .utils import _print, load_html_pages, ping_slack

GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "NO-REPOSITORY-IN-USE")
GITHUB_SERVER_URL = os.environ.get("GITHUB_SERVER_URL", "NO-GITHUB")
GITHUB_RUN_ID = os.environ.get("GITHUB_RUN_ID", "NO-RUN-NUMBER")
USER_AGENT = os.environ.get("USER_AGENT")

SITE_CHECKER_ISSUES_API_URL = os.environ.get(
    "SITE_CHECKER_ISSUES_API_URL",
    "https://api.github.com/repos/mozmeao/www-site-checker/issues",
)

PAGE_CACHE_DIR = "page_cache"
REQUEST_TIMEOUT_SECONDS = 15
RETRY_WAIT_SECONDS = 4
MAX_RETRIES_FOR_5XX = 1
DEFAULT_CONCURRENCY = 10

# Hrefs we never try to fetch
SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "data:", "ftp:")

ERROR_STATUS_LABELS = {
    404: "Not Found",
    500: "Internal Server Error",
    501: "Not Implemented",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}


def _is_reportable_status(status_code: int) -> bool:
    return status_code == 404 or 500 <= status_code < 600


def _filename_to_url(filename: str) -> str:
    """Best-effort reverse of scan_site._export_cache filename encoding.

    Encoding was: quote(url).replace("/", "_") + optional ".html" suffix on
    paths that originally ended in "/". The "_.html" suffix is therefore the
    tell that the original URL ended with a slash (since the trailing "/"
    became "_" before ".html" was appended). A natural ".html" in the URL
    path is preceded by "_" representing the prior "/", so it shows up as
    "_foo.html" and we keep the ".html".
    """
    if filename.endswith("_.html"):
        core = filename[: -len(".html")]
        return unquote(core.replace("_", "/"))
    return unquote(filename.replace("_", "/"))


def _strip_fragment(url: str) -> str:
    parsed = urlparse(url)
    if parsed.fragment:
        parsed = parsed._replace(fragment="")
        return urlunparse(parsed)
    return url


def _collect_links_from_cache(cache_dir: str) -> Dict[str, Set[str]]:
    """Walk every cached HTML page and return {link_url: {page_url, ...}}."""
    links: Dict[str, Set[str]] = defaultdict(set)
    pages = load_html_pages(cache_dir)
    _print(f"Loaded {len(pages)} cached HTML page(s) from {cache_dir}")

    for filename, html in pages.items():
        page_url = _filename_to_url(filename)
        soup = BeautifulSoup(html, "html5lib")
        for tag_name, attr in (
            ("a", "href"),
            ("script", "src"),
            ("link", "href"),
            ("link", "src"),
        ):
            for node in soup.find_all(tag_name):
                raw = node.attrs.get(attr)
                if not raw:
                    continue
                href = raw.strip()
                if not href or href.startswith("#"):
                    continue
                if href.lower().startswith(SKIP_SCHEMES):
                    continue
                absolute = _strip_fragment(urljoin(page_url, href))
                parsed = urlparse(absolute)
                if parsed.scheme not in ("http", "https"):
                    continue
                if not parsed.netloc:
                    continue
                links[absolute].add(page_url)
    return links


def _check_url(url: str) -> Optional[int]:
    """Return the final status code of `url`, or None on transport failure."""
    headers = {"User-Agent": USER_AGENT} if USER_AGENT else {}
    for attempt in range(MAX_RETRIES_FOR_5XX + 1):
        try:
            resp = requests.head(
                url,
                headers=headers,
                allow_redirects=True,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            # Some servers refuse HEAD; retry with GET.
            if resp.status_code in (405, 501):
                resp = requests.get(
                    url,
                    headers=headers,
                    allow_redirects=True,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    stream=True,
                )
                resp.close()
            status = resp.status_code
        except requests.RequestException as exc:
            _print(f"transport error for {url}: {exc}")
            return None

        if 500 <= status < 600 and attempt < MAX_RETRIES_FOR_5XX:
            time.sleep(RETRY_WAIT_SECONDS)
            continue
        return status
    return None


def _redact_page_url(url: str, in_scope_hostname: str) -> str:
    """If `url` shares the in-scope hostname, drop scheme+host for compactness."""
    parsed = urlparse(url)
    if parsed.netloc == in_scope_hostname:
        result = parsed.path or "/"
        if parsed.query:
            result += f"?{parsed.query}"
        return result
    return url


def _check_links_concurrently(
    links: Dict[str, Set[str]],
    concurrency: int,
) -> List[Dict]:
    """Check every link in parallel; return one record per reportable error."""
    errors: List[Dict] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_check_url, url): url for url in links}
        for future in as_completed(futures):
            url = futures[future]
            status = future.result()
            if status is not None and _is_reportable_status(status):
                errors.append(
                    {
                        "url": url,
                        "status_code": status,
                        "containing_pages": sorted(links[url]),
                    }
                )
    return errors


def _site_scoped_fingerprint(site_label: str, urls: List[str]) -> str:
    """Hash that scopes dedup to one site so a 404 on example.org/dead found
    by mozorg doesn't suppress the same finding on firefox.com."""
    parts = [site_label] + sorted(urls)
    return sha512("-".join(parts).encode("utf-8")).hexdigest()[:32]


def _get_current_github_issues() -> List:
    try:
        resp = requests.get(SITE_CHECKER_ISSUES_API_URL)
        return json.loads(resp.content)
    except (requests.RequestException, json.JSONDecodeError) as exc:
        _print(f"could not fetch current issues: {exc}")
        return []


def _build_issue_body(
    site_label: str,
    status_code: int,
    error_records: List[Dict],
    action_url: str,
    in_scope_hostname: str,
    fingerprint: str,
) -> str:
    label = ERROR_STATUS_LABELS.get(status_code, f"HTTP {status_code}")
    urls = sorted({r["url"] for r in error_records})
    pages_by_url = {r["url"]: r["containing_pages"] for r in error_records}

    lines = [
        f"{len(urls)} outbound link(s) returned {status_code} {label} when checked "
        f"from the cached HTML for **{site_label}**.",
        "",
        "**Affected URLs:**",
    ]
    for url in urls:
        lines.append(f"- {url}")
        lines.append("  Found on:")
        for page_url in pages_by_url.get(url, []):
            redacted = _redact_page_url(page_url, in_scope_hostname)
            lines.append(f"  - {redacted}")
    lines += [
        "",
        f"**Scan details and artifacts:** {action_url}",
        "",
        "--",
        "",
        f"Fingerprint: {fingerprint}",
    ]
    return "\n".join(lines)


def _open_issue_for_status_code(
    site_label: str,
    status_code: int,
    error_records: List[Dict],
    action_url: str,
    in_scope_hostname: str,
    current_issues: List[Dict],
) -> Optional[str]:
    urls = sorted({r["url"] for r in error_records})
    fingerprint = _site_scoped_fingerprint(site_label, urls)

    if any(fingerprint in (issue.get("body") or "") for issue in current_issues):
        _print(
            f"Skipping {status_code} issue for {site_label}: fingerprint already in an open issue"
        )
        return None

    label = ERROR_STATUS_LABELS.get(status_code, f"HTTP {status_code}")
    title = (
        f"{site_label}: {len(urls)} outbound link(s) returning {status_code} {label}"
    )
    body = _build_issue_body(
        site_label=site_label,
        status_code=status_code,
        error_records=error_records,
        action_url=action_url,
        in_scope_hostname=in_scope_hostname,
        fingerprint=fingerprint,
    )

    _print(f"Opening issue for {len(urls)} {status_code} error(s) on {site_label}")
    result = subprocess.check_output(
        [
            "gh",
            "issue",
            "create",
            "--title",
            title,
            "--body",
            body,
            "--label",
            "bug",
        ],
        stderr=subprocess.STDOUT,
    )
    return result.decode().strip()


@click.command()
@click.option(
    "--site-label",
    required=True,
    help="Human-friendly site identifier used in issue titles and fingerprints (e.g. www.mozilla.org)",
)
@click.option(
    "--in-scope-hostname",
    default=None,
    help="Hostname whose pages get their scheme+host stripped in 'Found on' lists. Defaults to --site-label.",
)
@click.option(
    "--cache-dir",
    default=PAGE_CACHE_DIR,
    help="Directory containing the dumped HTML pages",
)
@click.option(
    "--concurrency",
    default=DEFAULT_CONCURRENCY,
    type=int,
)
@click.option(
    "--no-issues",
    is_flag=True,
    default=False,
    help="If set, just report findings to stdout and skip GitHub issue creation",
)
def main(
    site_label: str,
    in_scope_hostname: Optional[str],
    cache_dir: str,
    concurrency: int,
    no_issues: bool,
) -> None:
    in_scope_hostname = in_scope_hostname or site_label
    links = _collect_links_from_cache(cache_dir)
    if not links:
        _print("No links found in cached HTML - nothing to check")
        return
    _print(f"Discovered {len(links)} unique link URL(s) to check")

    errors = _check_links_concurrently(links, concurrency)
    errors_by_status: Dict[int, List[Dict]] = defaultdict(list)
    for err in errors:
        errors_by_status[err["status_code"]].append(err)

    if not errors_by_status:
        _print(f"All {len(links)} link(s) returned non-reportable statuses")
        return

    _print(f"Reportable errors: {len(errors)}")
    for status, records in sorted(errors_by_status.items()):
        _print(f"  {status}: {len(records)} URL(s)")

    if no_issues:
        for status, records in sorted(errors_by_status.items()):
            for r in records:
                _print(
                    f"  {status}  {r['url']}  found on {len(r['containing_pages'])} page(s)"
                )
        return

    action_url = (
        f"{GITHUB_SERVER_URL}/{GITHUB_REPOSITORY}/actions/runs/{GITHUB_RUN_ID}/"
    )
    current_issues = _get_current_github_issues()
    opened: List[str] = []
    for status, records in sorted(errors_by_status.items()):
        issue_url = _open_issue_for_status_code(
            site_label=site_label,
            status_code=status,
            error_records=records,
            action_url=action_url,
            in_scope_hostname=in_scope_hostname,
            current_issues=current_issues,
        )
        if issue_url:
            opened.append(issue_url)

    message_parts = [
        f"Broken outbound link(s) found while scanning {site_label}.",
        f"Details and output: {action_url}",
    ]
    if opened:
        message_parts.append("Issue(s) opened:\n" + "\n".join(opened))
    else:
        message_parts.append(
            "NB: No new issues opened - existing open issues already track these findings."
        )
    message = "\n".join(message_parts)
    _print(message)
    ping_slack(message)


if __name__ == "__main__":
    main()

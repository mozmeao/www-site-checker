#! /usr/bin/env python3

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import datetime
import json
import os
import re
import subprocess
import sys
from hashlib import sha512
from typing import Dict, List

import requests
import ruamel.yaml
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from utils import _print, get_output_path, ping_slack

GITHUB_ACTION = os.environ.get("GITHUB_ACTION", "NO-ACTION-IN-USE")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "NO-REPOSITORY-IN-USE")
GITHUB_SERVER_URL = os.environ.get("GITHUB_SERVER_URL", "NO-GITHUB")
GITHUB_RUN_ID = os.environ.get("GITHUB_RUN_ID", "NO-RUN-NUMBER")

RELATIVE_URL_REGEX = re.compile(r"^[^\/]+\/[^\/].*$|^\/[^\/].*$")

MEAO_IDENTITY_EMAIL = os.environ.get("MEAO_IDENTITY_EMAIL")

RESULTS_CACHE = {}

SITE_CHECKER_PULL_REQUESTS_API_URL = os.environ.get(
    "SITE_CHECKER_PULL_REQUESTS_API_URL",
    "https://api.github.com/repos/mozmeao/www-site-checker/pulls",
)
SITE_CHECKER_ISSUES_API_URL = os.environ.get(
    "SITE_CHECKER_ISSUES_API_URL",
    "https://api.github.com/repos/mozmeao/www-site-checker/issues",
)

UNEXPECTED_URLS_FILENAME_FRAGMENT = "unexpected_urls_for"


def _load_template(filepath):
    filepath = f"templates/{filepath}"
    if not str(os.getcwd()).endswith("/bin"):
        filepath = f"bin/{filepath}"

    with open(filepath) as fp:
        template = fp.read()
    return template


MAX_ISSUE_TITLE_URL_LENGTH = 20  # 20 + length of ISSUE_TITLE_TEMPLATE's string == 50
ISSUE_TITLE_TEMPLATE = "Malformed hyperlink found: {malformed_url}..."
ISSUE_BODY_TEMPLATE = _load_template("issue_template.txt")
PR_TITLE_TEMPLATE = "Automatic updates to allowlist - {timestamp}"
PR_BODY_TEMPLATE = _load_template("pr_template.txt")


def _load_results_json(filename: str) -> dict:
    data = {}
    fp = open(filename)
    raw = fp.read()
    fp.close()
    if raw:
        data = json.loads(raw)
        RESULTS_CACHE.update(data)
    return data


def _assemble_results(output_path: str) -> set:
    """Pull together all the results from the run, de-duplicating where possible"""

    output = set()
    unexpected_url_data = {}

    # assume we have multiple output files, all scanning different sources of the website data
    for filename in os.listdir(output_path):
        if filename.startswith("unexpected_urls_for") and filename.endswith(".json"):
            unexpected_url_data.update(
                _load_results_json(os.path.join(output_path, filename))
            )

    for hostname in unexpected_url_data.keys():
        output.update(set(unexpected_url_data[hostname]))

    return output


def _is_valid_url(url: str) -> bool:
    "Is the given URL a valid absolute or relative URL?"

    url_validator = URLValidator()
    try:
        url_validator(url)
        return True
    except ValidationError:
        if url.startswith("/") and RELATIVE_URL_REGEX.match(url):
            return True
    return False


def _get_hashed_value(iterable: List) -> str:
    return sha512("-".join(sorted(iterable)).encode("utf-8")).hexdigest()[:32]


def _get_current_github_prs() -> List:
    return json.loads(requests.get(SITE_CHECKER_PULL_REQUESTS_API_URL).content)


def _get_current_github_issues() -> List:
    # NB /issues also returns pull requests
    # https://docs.github.com/en/rest/issues/issues?apiVersion=2022-11-28#list-repository-issues
    return json.loads(requests.get(SITE_CHECKER_ISSUES_API_URL).content)


def _matching_github_entity_exists(
    current_entities: List, candidates: List[str]
) -> bool:
    """Search all entities (open PRs or Issues) to see if we have one featuring this hash"""
    hashed_value = _get_hashed_value(candidates)

    try:
        for entity in current_entities:
            body = entity.get("body")
            if body is not None and hashed_value in body:
                return True
    except AttributeError as ae:
        _print(str(ae))
        _print(f"Current entities: {current_entities}")
        sys.exit(1)
    return False


def _build_structured_url_list_for_pr_description(pr_candidates: List[str]) -> str:
    """Returns a Markdown-format bulleted list of unexpected URLs with the page(s)
    that refernce them as nested bulleted lists"""

    output = "\n"
    for url in pr_candidates:
        output += f"* {url}\nFound in:\n"
        for referencing_url in _get_containing_pages_for_url(url, redact_domain=True):
            output += f"  * {referencing_url}\n"
    output += "\n"
    return output


def _update_allowlist(pr_candidates: List[str]) -> str:
    """Update the allowlist with the candidate URLs for a PR"""
    output = ""
    allowlist_path = os.environ.get("ALLOWLIST_FILEPATH")
    timestamp = datetime.datetime.utcnow().isoformat(timespec="seconds")
    unexpected_urls_structured = _build_structured_url_list_for_pr_description(
        pr_candidates
    )

    if not pr_candidates:
        _print("No candidate URLs to add to the allowlist")
        return output

    if _matching_github_entity_exists(
        current_entities=_get_current_github_prs(),
        candidates=pr_candidates,
    ):
        _print(
            "Not opening a new PR - existing one for same unexpected URLs exists already"
        )
        return output

    # 0. Make a new branch
    os.system(f'git config user.email "{MEAO_IDENTITY_EMAIL}"')
    os.system('git config user.name "www-site-checker bot"')

    branchname = (
        f'update-{allowlist_path.replace("/","-")}--{timestamp.replace(":","-")}'
    )
    os.system(f"git switch -c {branchname}")

    # 1. Update the allowlist
    yaml = ruamel.yaml.YAML()
    yaml.preserve_quotes = True
    with open(allowlist_path) as fp:
        data = yaml.load(fp)
    for candidate_url in pr_candidates:
        data["allowed_outbound_url_literals"].append(candidate_url)
    yaml.indent(offset=2)
    yaml.width = 10000  # Avoid wrapping-induced breakage of very long lines
    with open(allowlist_path, "w") as fp:
        yaml.dump(data, fp)

    # 2. Commit it to git on the new branch
    os.system(f'git commit --all -m "Automatic allowlist updates: {timestamp}"')

    # 3. Push the branch up to origin
    os.system(f"git push origin {branchname}")

    # 4. Prepare the Pull Request
    pr_title = PR_TITLE_TEMPLATE.format(timestamp=timestamp)
    pr_body = PR_BODY_TEMPLATE.format(
        unexpected_urls_structured=unexpected_urls_structured,
        fingerprint=_get_hashed_value(pr_candidates),
    )
    new_pr_command = (
        f'gh pr create --head {branchname} --title "{pr_title}" --body "{pr_body}"'
    )
    _print("Opening PR")
    try:
        output = subprocess.check_output(
            new_pr_command, stderr=subprocess.STDOUT, shell=True
        )
    except subprocess.CalledProcessError as e:
        _print(f"Failed to create PR: {e.output.decode()}")
        sys.exit(e.returncode)
    output = output.decode()
    return output


def _drop_scheme_and_domain(url: str) -> str:
    return "/" + "/".join(url.split("//")[1].split("/")[1:])


def _get_containing_pages_for_url(
    malformed_url: str,
    redact_domain: bool,
) -> List[str]:
    page_urls = set()
    for page_url, unexpected_urls in RESULTS_CACHE.items():
        for unexpected_url in unexpected_urls:
            if unexpected_url == malformed_url:
                if redact_domain:
                    page_url = _drop_scheme_and_domain(page_url)
                page_urls.add(page_url)

    return page_urls


def _open_new_issues(issue_candidates: List[str]) -> List[str]:
    """Open GH issues for each unknown non-URL-like found
    as a hyperlink."""

    output = []

    for problematic_url in issue_candidates:
        # Do we already have an issue open for this problematic URL?
        if _matching_github_entity_exists(
            current_entities=_get_current_github_issues(),
            candidates=[problematic_url],
        ):
            _print(
                f"Not opening a new Issue - existing one for '{problematic_url}' exists already"
            )
            continue

        issue_title = ISSUE_TITLE_TEMPLATE.format(
            malformed_url=problematic_url[:MAX_ISSUE_TITLE_URL_LENGTH],
        )
        issue_body = ISSUE_BODY_TEMPLATE.format(
            malformed_url=problematic_url,
            containing_page_urls="\n".join(
                _get_containing_pages_for_url(
                    problematic_url,
                    redact_domain=True,
                ),
            ),
            fingerprint=_get_hashed_value([problematic_url]),
        )
        new_issue_command = f'gh issue create --title "{issue_title}" --body "{issue_body}" --label "bug"'
        _print("Opening new issue")
        result = subprocess.check_output(
            new_issue_command, stderr=subprocess.STDOUT, shell=True
        )
        output.append(result.decode())

    return output


def raise_prs_or_issues(output_path: str) -> Dict:
    """Raises a PR, if possible, from the results stored in the output path.

    Not all results will be able to be turned into a PR, but we'll try, based
    on these rules:

    1. If the detected new string is definitely a URL (either absolute or
       relative), we add it to a PR to update the allowlist
    2. If it isn't a URL, we show a message (Slack / Sentry) and open a new
       GH Issue, indicating malformed content

    Returns a dictionary with whether or not PRs and Issues were opened
    """

    unexpected_urls = _assemble_results(output_path)

    issue_candidates = set()
    pr_candidates = set()

    for url in unexpected_urls:
        if _is_valid_url(url):
            pr_candidates.add(url)
        else:
            issue_candidates.add(url)

    pr_url = _update_allowlist(pr_candidates)
    issue_url_list = _open_new_issues(issue_candidates)

    return {
        "pr_url": pr_url,
        "issue_urls": issue_url_list,
    }


def main():
    """Look for a report of unexpected URLs found during the scan.
    If we find one, alert via Slack.

    Note that a separate Sentry ping is sent up when the unexpected
    URLs are found, so the Slack message isn't the only alert.
    """
    message = ""
    output_path = get_output_path()
    artifact_found = False

    # Do we have any artifacts available? If we _don't_, that's good news
    for filename in os.listdir(output_path):
        if UNEXPECTED_URLS_FILENAME_FRAGMENT in filename:
            artifact_found = True
            break

    if not artifact_found:
        _print("No artifact detected")
        return

    github_urls = raise_prs_or_issues(output_path)

    _action_url = (
        f"{GITHUB_SERVER_URL}/{GITHUB_REPOSITORY}/actions/runs/{GITHUB_RUN_ID}/"
    )
    message = "Unexpected outbound URL found when scanning site content.\nDetails and output: {}".format(
        _action_url
    )
    if github_urls.get("pr_url"):
        message += "\nPR to amend allowlist: {}".format(github_urls["pr_url"])
    if github_urls.get("issue_urls"):
        message += "\nIssue(s) opened: \n{}".format(
            "\n".join(github_urls["issue_urls"])
        )
    if not github_urls.get("pr_url") and not github_urls.get("issue_urls"):
        message += "\nNB: No new Issues or PRs opened - there will be existing ones on www-site-checker"

    _print(message)
    ping_slack(message)

    # Now that we reliably have PR generation, don't count the detection of unexpected content as
    # a "failure" - we gracefully exit
    sys.exit(0)


if __name__ == "__main__":
    main()

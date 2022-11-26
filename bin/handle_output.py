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
from functools import cache
from hashlib import sha512
from typing import Dict, List

import requests
import ruamel.yaml
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from slack_sdk.webhook import WebhookClient as SlackWebhookClient

GITHUB_ACTION = os.environ.get("GITHUB_ACTION", "NO-ACTION-IN-USE")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "NO-REPOSITORY-IN-USE")
GITHUB_SERVER_URL = os.environ.get("GITHUB_SERVER_URL", "NO-GITHUB")
GITHUB_RUN_ID = os.environ.get("GITHUB_RUN_ID", "NO-RUN-NUMBER")
SLACK_NOTIFICATION_WEBHOOK_URL = os.environ.get("SLACK_NOTIFICATION_WEBHOOK_URL")

SITE_CHECKER_PULL_REQUESTS_API_URL = os.environ.get(
    "SITE_CHECKER_PULL_REQUESTS_API_URL",
    "https://api.github.com/repos/mozmeao/www-site-checker/pulls",
)
SITE_CHECKER_ISSUES_API_URL = os.environ.get(
    "SITE_CHECKER_ISSUES_API_URL",
    "https://api.github.com/repos/mozmeao/www-site-checker/issues",
)

UNEXPECTED_URLS_FILENAME_FRAGMENT = "unexpected_urls_for"

RELATIVE_URL_REGEX = re.compile(r"^[^\/]+\/[^\/].*$|^\/[^\/].*$")

ISSUE_TITLE_TEMPLATE = "Malformed hyperlink detected: {malformed_url}"
ISSUE_BODY_TEMPLATE = """
While scanning the site, the following string was found in a href attribute for a hyperlink:

    {malformed_url}

URL of page(s) with this malformed link in it:

    {containing_page_urls}

This cannot be fixed via an automatic pull request - it needs to be checked and remedied.

Keep on rocking the free Web,

CheckerBot

--

Fingerprint: {fingerprint}
"""

PR_TITLE_TEMPLATE = "Automatic updates to allowlist - {timestamp}"
PR_BODY_TEMPLATE = """
While scanning the site, the following outbound URLs were detected in the site:

{unexpected_urls_bulleted}

They have been automatically added to the allowlist, as featured in this pull request.

**Please do not just approve and merge without checking**

For each URL mentioned above:

* Does it work?
* Does it link somewhere appropriate?
* Could it be replaced with a regex? (If so, close this PR and open a new one where you edit 'allowed_outbound_url_regexes' in the allowlist)

Hopefully this PR saves you time and effort.

CheckerBot

--

Fingerprint: {fingerprint}
"""


RESULTS_CACHE = {}


# TODO: import from run_checks.py or move to shared code
def _get_output_path() -> os.PathLike:
    # Get the path, allowing for this being called from the project root or the bin/ dir
    path_components = [
        "output",
    ]
    working_dir = os.getcwd()
    if str(working_dir).endswith("/bin"):
        path_components = [working_dir, ".."] + path_components
    return os.path.join("", *path_components)


def _print(message: str) -> None:
    sys.stdout.write(message)
    sys.stdout.write("\n")


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
        if filename.endswith(".json"):
            unexpected_url_data.update(_load_results_json(os.path.join(output_path, filename)))

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


@cache
def _get_current_github_prs() -> List:
    return json.loads(requests.get(SITE_CHECKER_PULL_REQUESTS_API_URL).content)


@cache
def _get_current_github_issues() -> List:
    return json.loads(requests.get(SITE_CHECKER_ISSUES_API_URL).content)


def _matching_github_entity_exists(current_entities: List, candidates: List[str]) -> bool:
    """Search all entities (open PRs or Issues) to see if we have one featuring this hash"""
    hashed_value = _get_hashed_value(candidates)
    for pr in current_entities:
        if hashed_value in pr.get("body", ""):
            return True
    return False


def _update_allowlist(pr_candidates: List[str]) -> str:
    """Update the allowlist with the candidate URLs for a PR"""
    if _matching_github_entity_exists(
        current_entities=_get_current_github_prs(),
        candidates=pr_candidates,
    ):
        _print("Not opening a new PR - existing one for same unexpected URLs exists already")
        return -1

    allowlist_path = os.environ.get("ALLOWLIST_FILEPATH")
    timestamp = datetime.datetime.utcnow().isoformat(timespec="seconds")
    unexpected_urls_bulleted = "\n".join([f"* {x}" for x in pr_candidates])

    # 0. Make a new branch
    branchname = f'update-{allowlist_path.replace("/","-")}--{timestamp.replace(":","-")}'
    os.system(f"git switch -c {branchname}")

    # 1. Update the allowlist
    yaml = ruamel.yaml.YAML()
    yaml.preserve_quotes = True
    with open(allowlist_path) as fp:
        data = yaml.load(fp)
    for candidate_url in pr_candidates:
        data["allowed_outbound_url_literals"].append(candidate_url)
    yaml.indent(offset=2)
    with open(allowlist_path, "w") as fp:
        yaml.dump(data, fp)

    # 2. Commit it to git on the new branch
    os.system(f'git commit --all -m "Automatic allowlist updates: {timestamp}"')

    # 3. Push the branch up to otigin
    os.system(f"git push origin {branchname}")

    # 4. Prepare the Pull Request
    pr_title = PR_TITLE_TEMPLATE.format(timestamp=timestamp)
    pr_body = PR_BODY_TEMPLATE.format(
        unexpected_urls_bulleted=unexpected_urls_bulleted,
        fingerprint=_get_hashed_value(pr_candidates),
    )
    new_pr_command = f'gh pr create --title "{pr_title}" --body "{pr_body}" --label "bug"'
    _print("Opening PR")
    output = subprocess.check_output(new_pr_command, stderr=subprocess.STDOUT, shell=True)
    _print(output)

    return output


def _drop_scheme_and_domain(url: str) -> str:
    return "/" + "/".join(url.split("//")[1].split("/")[1:])


def _get_containing_pages_for_malformed_url(
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


def _open_new_issues(issue_candidates: List[str]) -> None:
    """Open GH issues for each unknown non-URL-like found
    as a hyperlink."""

    retval = 0

    for problematic_url in issue_candidates:
        # Do we already have an issue open for this problematic URL?
        if _matching_github_entity_exists(
            current_entities=_get_current_github_issues(),
            candidates=[problematic_url],
        ):
            _print(f"Not opening a new Issue - existing one for '{problematic_url}' exists already")
            retval = -1
            continue

        issue_title = ISSUE_TITLE_TEMPLATE.format(
            malformed_url=problematic_url,
        )
        issue_body = ISSUE_BODY_TEMPLATE.format(
            malformed_url=problematic_url,
            containing_page_urls="\n".join(
                _get_containing_pages_for_malformed_url(
                    problematic_url,
                    redact_domain=True,
                ),
            ),
            fingerprint=_get_hashed_value([problematic_url]),
        )
        new_issue_command = f'gh issue create --title "{issue_title}" --body "{issue_body}" --label "bug"'
        _print("Opening new issue")
        status = os.system(new_issue_command)
        if status != 0:
            _print(f"Problem submitting issue for malformed url {problematic_url} - {status}")

    return retval


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

    # We don't get detailed results back out of these
    _update_allowlist(pr_candidates)
    _open_new_issues(issue_candidates)

    return {
        "issue_creation": bool(issue_candidates),
        "pull_request_creation": bool(pr_candidates),
    }


def main():
    """Look for a report of unexpected URLs found during the scan.
    If we find one, alert via Slack.

    Note that a separate Sentry ping is sent up when the unexpected
    URLs are found, so the Slack message isn't the only alert.
    """
    message = ""
    output_path = _get_output_path()
    artifact_found = False
    # Do we have any artifacts available? If we _don't_, that's good news
    for filename in os.listdir(output_path):
        if UNEXPECTED_URLS_FILENAME_FRAGMENT in filename:
            artifact_found = True
            break

    if not artifact_found:
        _print("No artifact detected")
        return

    if SLACK_NOTIFICATION_WEBHOOK_URL:
        slack_client = SlackWebhookClient(SLACK_NOTIFICATION_WEBHOOK_URL)
        _action_url = f"{GITHUB_SERVER_URL}/{GITHUB_REPOSITORY}/actions/runs/{GITHUB_RUN_ID}/"
        message = f"Unexpected outbound URL found when scanning page content. See {_action_url} for details and saved report."
        slack_client.send(text=message)

    if artifact_found:
        raise_prs_or_issues(output_path)

    sys.exit(message)


if __name__ == "__main__":
    main()

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
from typing import Dict, List, Set, Union

import requests
import ruamel.yaml
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from slack_sdk.webhook import WebhookClient as SlackWebhookClient

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

SLACK_NOTIFICATION_WEBHOOK_URL = os.environ.get("SLACK_NOTIFICATION_WEBHOOK_URL")

UNEXPECTED_URLS_FILENAME_FRAGMENT = "unexpected_urls_for"
UNKNOWN_WORDS_FILENAME_FRAGMENT = "unknown_words_for"


def _load_template(filepath):
    filepath = f"templates/{filepath}"
    if not str(os.getcwd()).endswith("/bin"):
        filepath = f"bin/{filepath}"

    with open(filepath) as fp:
        template = fp.read()
    return template


MAX_ISSUE_TITLE_URL_LENGTH__MALFORMED_URL = 20  # 20 + length of ISSUE_TITLE_TEMPLATE__MALFORMED_URL's string == 50
ISSUE_TITLE_TEMPLATE__MALFORMED_URL = "Malformed hyperlink found: {malformed_url}..."
ISSUE_TITLE_TEMPLATE__UNKNOWN_WORDS = "Unexpected words found during spellcheck sweep"

ISSUE_BODY_TEMPLATE__MALFORMED_URL = _load_template("malformed_url_issue_template.txt")
ISSUE_BODY_TEMPLATE__UNKNOWN_WORDS = _load_template("unknown_words_issue_template.txt")

PR_TITLE_TEMPLATE_NEW_URLS = "Automatic updates to allowlist - {timestamp}"
PR_BODY_TEMPLATE_NEW_URLS = _load_template("new_urls_pr_template.txt")


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


def _assemble_results(
    output_path: str,
    urls: bool = False,
    words: bool = False,
    redact_domain_for_unknown_words: bool = True,
) -> Union[Set, Dict]:
    """Pull together all the results from the run, de-duplicating if appropriate.
    Will return either a set of URLs or a dict mapping URLs to words, depending on what's specified"""

    if (not urls and not words) or (urls and words):
        raise Exception("_assemble_results() called with `urls` or `words` flags inappropriately set")

    output = set()

    _unexpected_url_data = {}
    _unexpected_words_data = {}

    # Assume we have multiple output files, all scanning different
    # sources of the website data and/or in small batches
    for filename in os.listdir(output_path):
        if filename.endswith(".json"):
            if urls and filename.startswith("unexpected_urls_for"):
                _unexpected_url_data.update(_load_results_json(os.path.join(output_path, filename)))
            elif words and filename.startswith("unknown_words_for"):
                results = _load_results_json(os.path.join(output_path, filename))
                if redact_domain_for_unknown_words:
                    # drop the domain from the URL keys
                    dict_keys = list(results.keys())
                    for url in dict_keys:
                        path = _drop_scheme_and_domain(url)
                        results[path] = results.pop(url)
                _unexpected_words_data.update(results)

    if urls:
        for hostname in _unexpected_url_data.keys():
            output.update(set(_unexpected_url_data[hostname]))

    if words:
        output = _unexpected_words_data

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
    for entity in current_entities:
        if hashed_value in entity.get("body", ""):
            return True
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


def _update_allowlist_with_new_urls(pr_candidates: List[str]) -> str:
    """Update the allowlist with the candidate URLs for a PR"""
    output = ""
    allowlist_path = os.environ.get("ALLOWLIST_FILEPATH")
    timestamp = datetime.datetime.utcnow().isoformat(timespec="seconds")
    unexpected_urls_structured = _build_structured_url_list_for_pr_description(pr_candidates)

    if not pr_candidates:
        return ""

    if _matching_github_entity_exists(
        current_entities=_get_current_github_prs(),
        candidates=pr_candidates,
    ):
        _print("Not opening a new PR - existing one for same unexpected URLs exists already")
        return output

    # 0. Make a new branch
    os.system(f'git config --global user.email "{MEAO_IDENTITY_EMAIL}"')
    os.system('git config --global user.name "www-site-checker bot"')

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
    pr_title = PR_TITLE_TEMPLATE_NEW_URLS.format(timestamp=timestamp)
    pr_body = PR_BODY_TEMPLATE_NEW_URLS.format(
        unexpected_urls_structured=unexpected_urls_structured,
        fingerprint=_get_hashed_value(pr_candidates),
    )
    new_pr_command = f'gh pr create --title "{pr_title}" --body "{pr_body}"'
    _print("Opening PR")
    output = subprocess.check_output(new_pr_command, stderr=subprocess.STDOUT, shell=True)
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


def _open_new_issues_for_malformed_urls(issue_candidates: List[str]) -> List[str]:
    """Open GH issues for each unknown non-URL-like found
    as a hyperlink."""

    if not issue_candidates:
        return []

    output = []

    for problematic_url in issue_candidates:
        # Do we already have an issue open for this problematic URL?
        if _matching_github_entity_exists(
            current_entities=_get_current_github_issues(),
            candidates=[problematic_url],
        ):
            _print(f"Not opening a new Issue - existing one for '{problematic_url}' exists already")
            continue

        issue_title = ISSUE_TITLE_TEMPLATE__MALFORMED_URL.format(
            malformed_url=problematic_url[:MAX_ISSUE_TITLE_URL_LENGTH__MALFORMED_URL],
        )
        issue_body = ISSUE_BODY_TEMPLATE__MALFORMED_URL.format(
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
        result = subprocess.check_output(new_issue_command, stderr=subprocess.STDOUT, shell=True)
        output.append(result.decode())

    return output


def _get_structured_unknown_words(unknown_words: Dict) -> str:

    output = []
    for page_url, words in unknown_words.items():
        output.append(page_url)
        for word in words:
            output.append(f"* {word}")
        output.append("")

    return "\n".join(output)


def _open_new_issue_for_unknown_words(unknown_words: List[str]) -> str:
    """Open a GH issue for all the unknown words in the site"""

    output = []

    _unknown_words_for_fingerprint = [f"{k}:{v}" for k, v in unknown_words.items()]

    # Do we already have an issue open for misspellings?
    if _matching_github_entity_exists(
        current_entities=_get_current_github_issues(),
        candidates=_unknown_words_for_fingerprint,
    ):
        _print("Not opening a new Issue - existing one for these unknown words exists already")
        return output

    issue_title = ISSUE_TITLE_TEMPLATE__UNKNOWN_WORDS
    issue_body = ISSUE_BODY_TEMPLATE__UNKNOWN_WORDS.format(
        unknown_words__structured=_get_structured_unknown_words(unknown_words),
        fingerprint=_get_hashed_value(_unknown_words_for_fingerprint),
    )
    new_issue_command = f'gh issue create --title "{issue_title}" --body "{issue_body}" --label "bug"'
    _print("Opening new issue for unknown words")
    result = subprocess.check_output(new_issue_command, stderr=subprocess.STDOUT, shell=True)
    output = result.decode()

    return output


def raise_prs_or_issues(output_path: str) -> Dict:
    """Raises a PR, if possible, from the results stored in the output path.

    Not all results for unexpected URLs will be able to be turned into a PR,
    but we'll try, based on these rules:

    1. If the detected new string is definitely a URL (either absolute or
       relative), we add it to a PR to update the allowlist
    2. If it isn't a URL, we show a message (Slack / Sentry) and open a new
       GH Issue, indicating malformed content

    For all unexpected words, we'll just open an Issue.

    Returns a dictionary with whether or not PRs and Issues were opened
    """

    unexpected_urls = _assemble_results(output_path, urls=True)
    unexpected_words = _assemble_results(output_path, words=True)

    url_issue_candidates = set()
    url_pr_candidates = set()
    for url in unexpected_urls:
        if _is_valid_url(url):
            url_pr_candidates.add(url)
        else:
            url_issue_candidates.add(url)
    allowlist_pr_url = _update_allowlist_with_new_urls(url_pr_candidates)
    allowlist_issue_url_list = _open_new_issues_for_malformed_urls(url_issue_candidates)

    unknown_words_issue_url = _open_new_issue_for_unknown_words(unexpected_words)

    return {
        "allowlist_pr_url": allowlist_pr_url,
        "allowlist_issues_urls": allowlist_issue_url_list,
        "unknown_words_issue_url": unknown_words_issue_url,
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
        if UNEXPECTED_URLS_FILENAME_FRAGMENT in filename or UNKNOWN_WORDS_FILENAME_FRAGMENT in filename:
            artifact_found = True
            break

    if not artifact_found:
        _print("No artifact detected")
        return

    github_urls = raise_prs_or_issues(output_path)

    output_message_lookup = {
        "allowlist_pr_url": "PR to amend allowlist: {}".format(github_urls["allowlist_pr_url"]),
        "allowlist_issues_urls": "Issue(s) opened: \n{}".format("\n".join(github_urls["allowlist_issues_urls"])),
        "unknown_words_issue_url": "Issue opened: \n{}".format(github_urls["unknown_words_issue_url"]),
    }

    _action_url = f"{GITHUB_SERVER_URL}/{GITHUB_REPOSITORY}/actions/runs/{GITHUB_RUN_ID}/"
    message = "Unexpected content found when scanning site content.\nDetails and output: {}".format(_action_url)

    for result_key, result_values in github_urls.items():
        if result_values:
            message += "\n" + output_message_lookup[result_key]

    if not any(github_urls.values()):
        message += "\nNB: No new Issues or PRs opened - there will be existing ones on www-site-checker"

    _print(message)

    if SLACK_NOTIFICATION_WEBHOOK_URL:
        slack_client = SlackWebhookClient(SLACK_NOTIFICATION_WEBHOOK_URL)
        slack_client.send(text=message)

    sys.exit(1)


if __name__ == "__main__":
    main()

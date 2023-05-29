#! /usr/bin/env python3

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import os
import sys

from bs4 import BeautifulSoup
from utils import _print, load_from_dumped_cache, ping_slack

GITHUB_ACTION = os.environ.get("GITHUB_ACTION", "NO-ACTION-IN-USE")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "NO-REPOSITORY-IN-USE")
GITHUB_SERVER_URL = os.environ.get("GITHUB_SERVER_URL", "NO-GITHUB")
GITHUB_RUN_ID = os.environ.get("GITHUB_RUN_ID", "NO-RUN-NUMBER")


def check_geo_consistency():
    # If there are any pages dumped from the main site-scanning cache,
    # inspect them to ensure they all have the same geo code in the
    # html node's data-country-code attribute. Doesn't matter what it is,
    # as long as it's consistent across all pages in the cache

    html_documents = load_from_dumped_cache("page_cache")
    geo_codes_seen = set()

    for doc in html_documents:
        html_node = BeautifulSoup(doc, features="lxml").html
        geo_code = html_node.attrs.get("data-country-code", "NOT_PRESENT")
        geo_codes_seen.add(geo_code)

    if "NOT_PRESENT" in geo_codes_seen:
        _print("Found no geo codes in at least one CDN page!")
        message = "No data-country-code values found in CDN content. See {GITHUB_SERVER_URL}/{GITHUB_REPOSITORY}/actions/runs/{GITHUB_RUN_ID}"
        ping_slack(message)
        sys.exit(98)

    elif len(geo_codes_seen) != 1:
        _print(f"Found {len(geo_codes_seen)} geo codes in CDN pages: {geo_codes_seen}")
        message = (
            "Inconsistent number of data-country-code values found from CDN content. "
            f"See {GITHUB_SERVER_URL}/{GITHUB_REPOSITORY}/actions/runs/{GITHUB_RUN_ID}"
        )
        ping_slack(message)
        sys.exit(99)

    else:
        _print(f"All OK: Only one single geo code seen in CDN pages from cache. Checked {len(html_documents)} documents")
        _print(f"(Code seen: {geo_codes_seen.pop()})")


if __name__ == "__main__":
    check_geo_consistency()

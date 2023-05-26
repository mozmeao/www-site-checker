#! /usr/bin/env python3

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import os
import sys
from collections import defaultdict

from bs4 import BeautifulSoup
from utils import _print, load_html_pages, ping_slack

GITHUB_ACTION = os.environ.get("GITHUB_ACTION", "NO-ACTION-IN-USE")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "NO-REPOSITORY-IN-USE")
GITHUB_SERVER_URL = os.environ.get("GITHUB_SERVER_URL", "NO-GITHUB")
GITHUB_RUN_ID = os.environ.get("GITHUB_RUN_ID", "NO-RUN-NUMBER")

FILENAMES_TO_SKIP = [
    # Filenames we know will lack the data-country-code attr im their html root node
    "https%3A__www.mozilla.org_en-US_book_.html",
    "https%3A__www.mozilla.org_en-US_MPL_2.0_.html",
    "https%3A__www.mozilla.org_en-US_MPL_1.1_.html",
    "https%3A__www.mozilla.org_en-US_MPL_1.1_annotated_.html",
    "https%3A__www.mozilla.org_en-US_MPL_2.0_differences_.html",
]


def check_geo_consistency():
    # If there are any pages dumped from the main site-scanning cache,
    # inspect them to ensure they all have the same geo code in the
    # html node's data-country-code attribute. Doesn't matter what it is,
    # as long as it's consistent across all pages in the cache

    html_document_lookup = load_html_pages("page_cache")
    geo_codes_seen = defaultdict(list)
    if len(html_document_lookup.values()) == 0:
        _print("No HTML documents extracted from page cache")
        sys.exit(97)

    for filename, doc in html_document_lookup.items():
        if filename in FILENAMES_TO_SKIP:
            _print(f"Skipping {filename}")
            continue
        html_node = BeautifulSoup(doc, features="html5lib").html
        geo_code = html_node.attrs.get("data-country-code", "NOT_PRESENT")
        geo_codes_seen[geo_code].append(filename)

    if "NOT_PRESENT" in geo_codes_seen.keys():
        not_present_pages = geo_codes_seen["NOT_PRESENT"]
        _print(f"Found no geo codes in at least one CDN page! ({not_present_pages})")
        message = (
            f"WARNING: No data-country-code values found in CDN content. See {GITHUB_SERVER_URL}/{GITHUB_REPOSITORY}/actions/runs/{GITHUB_RUN_ID}"
        )
        ping_slack(message)
        sys.exit(98)

    elif len(geo_codes_seen.keys()) != 1:
        _print(f"Found {len(geo_codes_seen.keys())} geo codes in CDN pages: {geo_codes_seen.keys()}")
        message = (
            "WARNING: Inconsistent number of data-country-code values found from CDN content. "
            f"See {GITHUB_SERVER_URL}/{GITHUB_REPOSITORY}/actions/runs/{GITHUB_RUN_ID}"
        )
        ping_slack(message)
        sys.exit(99)

    else:
        _print(f"All OK: Only one single geo code seen in CDN pages from cache. Checked {len(html_document_lookup.keys())} documents")
        _print(f"(Code seen: {list(geo_codes_seen.keys())[0]})")


if __name__ == "__main__":
    check_geo_consistency()

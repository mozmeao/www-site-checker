#! /usr/bin/env python3

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Collect image alt text from cached HTML pages.

Runs in a workflow_run-triggered workflow after a site-scanning run completes.
Loads the page_cache/ dump produced by scan_site.py --export-cache, extracts
every <img> tag's src and alt attributes, and writes a CSV report.
"""

import csv
import os
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import click
from bs4 import BeautifulSoup

try:
    from utils import _print, filename_to_url, load_html_pages
except ImportError:
    from .utils import _print, filename_to_url, load_html_pages

PAGE_CACHE_DIR = "page_cache"
ALT_TEXT_OUTPUT_DIR = os.environ.get("ALT_TEXT_OUTPUT_DIR", "/tmp")


def _redact_page_url(url: str, in_scope_hostname: str) -> str:
    """If `url` shares the in-scope hostname, drop scheme+host for compactness."""
    parsed = urlparse(url)
    if parsed.netloc == in_scope_hostname:
        result = parsed.path or "/"
        if parsed.query:
            result += f"?{parsed.query}"
        return result
    return url


def _collect_images_from_cache(cache_dir: str) -> List[Dict]:
    """Walk every cached HTML page and return a list of image records."""
    records: List[Dict] = []
    pages = load_html_pages(cache_dir)
    _print(f"Loaded {len(pages)} cached HTML page(s) from {cache_dir}")

    for filename, html in pages.items():
        page_url = filename_to_url(filename)
        soup = BeautifulSoup(html, "html5lib")
        for img in soup.find_all("img"):
            src = img.attrs.get("src", "").strip()
            if not src:
                continue
            absolute_src = urljoin(page_url, src)
            alt_present = "alt" in img.attrs
            alt_text = img.attrs.get("alt", "")
            records.append(
                {
                    "page_url": page_url,
                    "img_src": absolute_src,
                    "alt_text": alt_text,
                    "alt_attribute_present": alt_present,
                }
            )
    return records


def _write_csv(
    site_label: str,
    records: List[Dict],
    in_scope_hostname: str,
) -> str:
    """Write image alt text records to a CSV and return the file path."""
    os.makedirs(ALT_TEXT_OUTPUT_DIR, exist_ok=True)
    path = os.path.join(ALT_TEXT_OUTPUT_DIR, f"image-alt-text-{site_label}.csv")

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["page_url", "img_src", "alt_text", "alt_attribute_present"])
        for record in sorted(records, key=lambda r: (r["page_url"], r["img_src"])):
            writer.writerow(
                [
                    _redact_page_url(record["page_url"], in_scope_hostname),
                    record["img_src"],
                    record["alt_text"],
                    str(record["alt_attribute_present"]).lower(),
                ]
            )

    return path


@click.command()
@click.option(
    "--site-label",
    required=True,
    help="Human-friendly site identifier used in the output filename (e.g. www.mozilla.org)",
)
@click.option(
    "--in-scope-hostname",
    default=None,
    help="Hostname whose pages get their scheme+host stripped in page_url column. Defaults to --site-label.",
)
@click.option(
    "--cache-dir",
    default=PAGE_CACHE_DIR,
    help="Directory containing the dumped HTML pages",
)
def main(
    site_label: str,
    in_scope_hostname: Optional[str],
    cache_dir: str,
) -> None:
    in_scope_hostname = in_scope_hostname or site_label
    records = _collect_images_from_cache(cache_dir)
    if not records:
        _print("No images found in cached HTML pages")
        return

    _print(f"Found {len(records)} image(s) across cached pages")
    missing_alt = sum(1 for r in records if not r["alt_attribute_present"])
    if missing_alt:
        _print(f"  {missing_alt} image(s) missing alt attribute")

    path = _write_csv(site_label, records, in_scope_hostname)
    _print(f"CSV written to {path}")


if __name__ == "__main__":
    main()

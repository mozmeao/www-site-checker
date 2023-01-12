#! /usr/bin/env python3

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
This is a tool to help verify the content of the specified website.
Run the specified checks on specified URLs and issue a report.

Initially, we're checking that all outbound URLs are ones we expect.
"""
import datetime
import json
import logging
import math
import os
import re
import time
from collections import defaultdict
from functools import cache
from typing import Dict, Iterable, List, Tuple
from urllib.parse import urlparse

import click
import requests
import sentry_sdk
from bs4 import BeautifulSoup
from hunspell import Hunspell
from pyaml_env import parse_config
from requests.exceptions import ChunkedEncodingError, ConnectionError, HTTPError
from sentry_sdk.integrations.logging import LoggingIntegration

GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "NO-REPOSITORY-IN-USE")
SENTRY_DSN = os.environ.get("SENTRY_DSN")
ALLOWLIST_FILEPATH = os.environ.get("ALLOWLIST_FILEPATH")

if SENTRY_DSN:
    # Set up Sentry logging if we can.
    sentry_logging = LoggingIntegration(
        level=logging.DEBUG,  # Capture debug and above as breadcrumbs
        event_level=logging.ERROR,  # Send errors and above as events
    )
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[sentry_logging],
    )

DEFAULT_BATCH__NOOP = "1:1"  # By default treat all URLs as a single batch
UNEXPECTED_URLS_FILENAME_FRAGMENT = "unexpected_urls_for"
UNKNOWN_WORDS_FILENAME_FRAGMENT = "unknown_words_for"
URL_RETRY_LIMIT = 3
URL_RETRY_WAIT_SECONDS = 4

# Run a simple cache of the pages we've already pulled down, to avoid getting them twice
# Size wise, ballparking at 25Kb per page, with ~3500 enpages => 85MB
PAGE_CONTENT_LOOKUP = dict()

LOCALES_TO_CACHE = ("en-US",)
LOCALES_FOR_SPELLCHECK = LOCALES_TO_CACHE
CUSTOM_DICTIONARIES = {
    "en-US",
}
CUSTOM_DICTIONARY_FILE_TEMPLATE = "./data/custom_dictionaries/{}.txt"


@click.command()
@click.option(
    "--sitemap-url",
    default=None,
    help="URL of an XML sitemap to use as source data",
)
@click.option(
    "--maintain-hostname",
    default=False,
    is_flag=True,
    help="If the sitemap points to a different domain (eg a CDN domain), override it and replace it with the hostname that served the sitemap",
)
@click.option(
    "--specific-url",
    default=None,
    help="Specific URL/page to check. This flag can be used multiple times, once per URL",
    multiple=True,
)
@click.option(
    "--batch",
    default=DEFAULT_BATCH__NOOP,
    help=(
        "Batch all the gathered URLs and work on one specific batch. Format is {chunk_number}:{total_chunks}. "
        "For example --batch=1:2 means chop the overall set into two and work on the first batch, "
        "2:3 means do the second batch of three, 4:4 means do the final batch of four, etc"
    ),
)
@click.option(
    "--allowlist",
    default=ALLOWLIST_FILEPATH,
    help="Path to a YAML-formatted allowlist. If none is provided, the default of data/allowlist.yml will be used",
)
def run_checks(
    sitemap_url: str,
    maintain_hostname: bool,
    specific_url: Iterable,
    batch: str,
    allowlist: str,
) -> None:

    # Let's tidy up that variable name we get from the input option
    specific_urls = specific_url

    if not sitemap_url and not specific_urls:
        raise Exception("No sitemap or input URLs specified. Cannot proceed.")

    host_url = sitemap_url or specific_urls[0]  # TODO: ensure all specific URLs use the same hostname
    hostname = urlparse(host_url).netloc

    allowlist_config = _get_allowlist_config(
        hostname,
        allowlist_pathname=allowlist,
    )

    urls_to_check = _build_urls_to_check(
        sitemap_url=sitemap_url,
        specific_urls=specific_url,
        maintain_hostname=maintain_hostname,
    )

    # Do we need to chunk these down?
    if batch != DEFAULT_BATCH__NOOP:
        urls_to_check = _get_batched_urls(urls_to_check, batch)

    check_for_unexpected_urls(
        urls_to_check=urls_to_check,
        allowlist_config=allowlist_config,
        hostname=hostname,
        batch=batch,
    )
    check_spelling(
        urls_to_check=urls_to_check,
        allowlist_config=allowlist_config,
        hostname=hostname,
        batch=batch,
    )


def check_for_unexpected_urls(
    urls_to_check: List[str],
    allowlist_config: dict,
    hostname: str,
    batch: str,
) -> None:
    click.echo("Checking pages for unexpected URLs")
    results = _check_pages_for_outbound_links(urls_to_check, allowlist_config)

    if results:
        click.echo(f"Unexpected outbound URLs found on {hostname}!")
        _dump_unexpected_urls_to_files(
            results=results,
            hostname=hostname,
            batch_label="all" if batch == DEFAULT_BATCH__NOOP else batch.split(":")[0],
        )
        if SENTRY_DSN:
            message = f"Unexpected content found on {hostname} - see Github Action in {GITHUB_REPOSITORY} for output data"
            sentry_sdk.capture_message(
                message=message,
                level="error",
            )
    else:
        click.echo("Checks completed and no unexpected outbound URLs found")


def _get_batched_urls(urls_to_check: List[str], batch: str) -> List[str]:
    # TODO: optimise to avoid making a new list - just return the indices and work with them in a loop
    url_count = len(urls_to_check)
    chunk_num, total_chunks = [int(x) for x in batch.split(":")]
    if chunk_num < 1 or total_chunks < 1 or chunk_num > total_chunks:
        raise Exception(f"--batch parameter {batch} was nonsensical")

    chunk_size = math.ceil(url_count / total_chunks)  # better to make the chunk one element
    start_index = (chunk_num - 1) * chunk_size
    end_index = start_index + chunk_size
    click.echo(f"Working on batch {chunk_num}/{total_chunks}: {chunk_size} items")
    return urls_to_check[start_index:end_index]


def _page_content_is_cacheable(url):
    for locale in LOCALES_TO_CACHE:
        if f"/{locale}/" in url:
            return True
    return False


def _get_url_with_retry(
    url: str,
    try_count: int = 0,
    limit: int = URL_RETRY_LIMIT,
    cache_html: bool = True,
) -> requests.Response:
    exceptions_to_retry = (
        ChunkedEncodingError,
        ConnectionError,
        HTTPError,  # GOTCHA? This might be too permissive because many Requests exceptions inherit it
    )
    try:
        resp = PAGE_CONTENT_LOOKUP.get(url)
        if resp:
            click.echo(f"Getting {url} from cache")
        else:
            click.echo(f"Pulling down {url}")
            resp = requests.get(url)
            resp.raise_for_status()
            if cache_html and _page_content_is_cacheable(url):
                PAGE_CONTENT_LOOKUP[url] = resp
        return resp

    except exceptions_to_retry as re:
        if try_count < limit:
            click.echo(f"Waiting {URL_RETRY_WAIT_SECONDS} seconds before retrying, following {re}")
            time.sleep(URL_RETRY_WAIT_SECONDS)
            return _get_url_with_retry(url, try_count=try_count + 1)
        else:
            click.echo(f"Max retries ({URL_RETRY_LIMIT}) reached. Raising exception {re}")
            raise re


def _dump_unexpected_urls_to_files(
    results: Dict[str, set],
    hostname: str,
    batch_label: str,
) -> Tuple[str, str, str]:
    """Output files of results specific to the current hostname and batch:

    * Text output:
        * flat: just the unexpected urls
        * nested: for each unexpected URL, show were in the site we found it

    * JSON output

    """
    _output_path = _get_output_path()
    _now = datetime.datetime.utcnow().isoformat(timespec="seconds").replace(":", "-")  # Github actions doesn't like colons in filenames
    _base_filename = f"{UNEXPECTED_URLS_FILENAME_FRAGMENT}_{hostname}_{batch_label}_{_now}.txt"
    flat_output_filepath = os.path.join(_output_path, _base_filename.replace(".txt", "_flat.txt"))
    nested_output_filepath = flat_output_filepath.replace("_flat", "_nested")
    json_output_filepath = flat_output_filepath.replace("_flat", "_structured").replace(".txt", ".json")

    fp_flat = open(flat_output_filepath, "w")
    fp_flat.write("\n".join([key for key in results.keys()]))
    fp_flat.close
    click.echo(f"List of unexpected URLs output to {flat_output_filepath}")

    fp_nested = open(nested_output_filepath, "w")
    for unexpected_url, occurrences in results.items():
        line = "\nUnexpected URL: {unexpected_url}\nFound in:\n\t{occurrences}\n".format(
            unexpected_url=unexpected_url,
            occurrences="\n\t".join(occurrences),
        )
        fp_nested.write(line)
    fp_nested.close()
    click.echo(f"List of unexpected URLs and their source pages output to {nested_output_filepath}")

    # Can't serialize a set, so make it a list and flip it around
    inverted_results = defaultdict(list)
    for key, values in results.items():
        for value in values:
            inverted_results[value].append(key)

    fp_json = open(json_output_filepath, "w")
    fp_json.write(json.dumps(inverted_results))
    fp_json.close()
    click.echo(f"JSON version of results output to {json_output_filepath}")

    return flat_output_filepath, nested_output_filepath, json_output_filepath


def _get_output_path() -> os.PathLike:
    # Get the path, allowing for this being called from the project root or the bin/ dir
    path_components = [
        "output",
    ]
    working_dir = os.getcwd()
    if str(working_dir).endswith("/bin"):
        path_components = [working_dir, ".."] + path_components
    return os.path.join("", *path_components)


def _get_allowlist_path(allowlist_pathname: str) -> os.PathLike:
    # Get the path, allowing for this being called from the project root or the bin/ dir
    path_components = allowlist_pathname.split("/")
    working_dir = os.getcwd()
    if str(working_dir).endswith("/bin"):
        path_components = [working_dir, ".."] + path_components
    return os.path.join("", *path_components)


@cache
def _get_allowlist_config(hostname: str, allowlist_pathname: str) -> dict:
    """Load the allowlist from the YAML file, optimise the direct like-for-like lookups
    and warm up any regexes."""

    click.echo(f"Seeking an appropriate allowlist in file {allowlist_pathname}")
    config_data = parse_config(_get_allowlist_path(allowlist_pathname))

    site_config = None

    # Find the appropriate allowlist for the allowlist_config node
    for candidate_hostname in config_data.get("relevant_hostnames"):
        if candidate_hostname == hostname:
            site_config = config_data
            break

    if not site_config:
        click.echo(f"Could not find an allowlist for {hostname}, so treating all outbound URLs as unexpected")
        site_config = {
            "allowed_outbound_url_literals": set(),
            "allowed_outbound_url_regexes": set(),
        }
    else:
        # While we're here, turn the list of full strings to match into a set, to optimise lookups later.
        # We _could_ mark this up as sets in YAML, but that gets parsed as {key_x: null, ...} so
        # still would need cleaning up
        site_config["allowed_outbound_url_literals"] = set(site_config.get("allowed_outbound_url_literals", []))

        # Also, let's pre-compile our regexes, at least:
        compiled_regexes = set()
        for raw_regex in site_config.get("allowed_outbound_url_regexes", []):
            compiled_regexes.add(re.compile(raw_regex))

        # Warning: re-using same key but with [slightly] different data than sourced from YAML
        site_config["allowed_outbound_url_regexes"] = compiled_regexes
    return site_config


def _verify_url_allowed(url: str, allowlist_config: dict) -> bool:

    # Quickest check first - set membership.

    # Temporary measure: adjust for line breaks in hrefs
    if "\n" in url:
        _url = url.replace("\n", "\\n")
    else:
        _url = url

    if _url in allowlist_config["allowed_outbound_url_literals"]:
        return True

    # If no luck, try our regex rules
    for compiled_regex in allowlist_config["allowed_outbound_url_regexes"]:
        if compiled_regex.match(url):  # NB: testing the original, untweaked URL
            return True

    # Belt and braces:
    return False


def _check_pages_for_outbound_links(urls: List[str], allowlist_config: Dict) -> Dict:

    unlisted_outbound_urls = defaultdict(set)
    # oubound url is they key, a set of pages it's on is the value

    for page_url in urls:
        resp = _get_url_with_retry(page_url)
        html_content = resp.text
        soup = BeautifulSoup(html_content, "html5lib")
        anchor_tags = soup.find_all("a")
        script_tags = soup.find_all("script")
        link_tags = soup.find_all("link")

        for nodelist, attr in [
            (anchor_tags, "href"),
            (script_tags, "src"),
            (link_tags, "src"),
        ]:
            for node in nodelist:
                _url = node.attrs.get("href")
                if _url and not _verify_url_allowed(_url, allowlist_config):
                    unlisted_outbound_urls[_url].add(page_url)

        # TODO: OPTIMISE THE ABOVE
        # TODO: find URLS in rendered content, too
    return unlisted_outbound_urls


def _build_urls_to_check(
    sitemap_url: str,
    specific_urls: Iterable,
    maintain_hostname: bool,
) -> List[str]:
    """Given a sitemap URL and/or specific URLs to check, put together a list
    of overall URLs whose content wen want to check"""

    urls = []
    if sitemap_url:
        urls += _get_urls_from_sitemap(sitemap_url, maintain_hostname)
    if specific_urls:
        # Don't forget any manually specified URLs
        urls += specific_urls
    click.echo(f"Discovered {len(urls)} URLs to check")
    return urls


def _get_urls_from_sitemap(
    sitemap_url: str,
    maintain_hostname: bool,
) -> List[str]:
    """Extract URLs to explore from a sitemap, optionally ensuring the hostname in
    any URLs found is swapped ('maintained') to be the same as that of the source
    sitemap –- this is needed when checking an origin server whose sitemap returns
    the CDN's hostname"""

    urls = []

    _parsed_origin_sitemap_url = urlparse(sitemap_url)
    origin_hostname_with_scheme = f"{_parsed_origin_sitemap_url.scheme}://{_parsed_origin_sitemap_url.netloc}"

    resp = _get_url_with_retry(sitemap_url)

    sitemap_xml = resp.text
    soup = BeautifulSoup(sitemap_xml, "lxml")

    # Look for a <sitemap> node, and get each as a URL for a locale-specific sitemap
    sitemap_nodes = soup.find_all("sitemap")
    if len(sitemap_nodes):
        click.echo(f"Discovered {len(sitemap_nodes)} child sitemaps")

    for sitemap_node in sitemap_nodes:
        sitemap_url = sitemap_node.loc.text

        if maintain_hostname:
            sitemap_url = _update_hostname(
                origin_hostname_with_scheme=origin_hostname_with_scheme,
                urls=[sitemap_url],
            )[0]

        click.echo(f"Diving into {sitemap_url}")
        urls.extend(_get_urls_from_sitemap(sitemap_url, maintain_hostname))

    # look for regular URL nodes, which may or may not co-exist alongside sitemap nodes
    url_nodes = soup.find_all("url")
    if url_nodes:
        click.echo(f"Adding {len(url_nodes)} URLs")
        for url in url_nodes:
            try:
                urls.append(url.loc.text)
            except AttributeError as ae:
                sentry_sdk.capture_message(f"URL node {url} missing '<loc>' - exception to follow")
                sentry_sdk.capture_exception(ae)

    # Also remember to update the hostname on the final set of URLs, if required
    if maintain_hostname:
        urls = _update_hostname(
            origin_hostname_with_scheme=origin_hostname_with_scheme,
            urls=urls,
        )
    return urls


def _update_hostname(origin_hostname_with_scheme: str, urls: List[str]) -> List[str]:
    """If the urls start with a different hostname than the one we're exploring,
    replace it in each of them.

    This is so that if sitemap_url is on an origin server but its sitemap references
    the CDN domain, we can actually hit the origin to test its pages directly."""

    # This assumes all URLs in the sitemap have the same hostname, so we can use the first
    # as our source of truth. This doesn't seem unreasonable.
    _parsed_sample = urlparse(urls[0])
    candidate_hostname_with_scheme = f"{_parsed_sample.scheme}://{_parsed_sample.netloc}"

    if origin_hostname_with_scheme == candidate_hostname_with_scheme:
        click.echo(f"No need to replace the hostname on this batch of URLs: {candidate_hostname_with_scheme}")

    return [url.replace(candidate_hostname_with_scheme, origin_hostname_with_scheme) for url in urls]


def check_spelling(
    urls_to_check: List[str],
    allowlist_config: dict,
    hostname: str,
    batch: str,
) -> None:
    click.echo("Checking pages for spelling errors")

    grouped_urls = _filter_urls_to_available_locales(
        urls=urls_to_check,
        locales=LOCALES_FOR_SPELLCHECK,
    )
    unknown_words = {}

    for locale, urls in grouped_urls.items():
        for page_url in urls:
            resp = _get_url_with_retry(page_url)
            html_content = resp.text
            soup = BeautifulSoup(html_content, "html5lib")
            tags_to_drop = soup(["head", "script", "style"])
            [s.extract() for s in tags_to_drop]
            page_text = soup.getText()
            if spelling_errors := _check_pages_for_spelling_errors(page_text, locale):
                unknown_words[page_url] = list(spelling_errors)

    if unknown_words:
        click.echo("Writing unknown words to a file")
        _dump_unknown_words_to_file(
            results=unknown_words,
            hostname=hostname,
            batch_label="all" if batch == DEFAULT_BATCH__NOOP else batch.split(":")[0],
        )
    else:
        click.echo("No unknown words found")


def _load_unicode_words(filepath):
    with open(filepath, "r") as fp:
        words = fp.read().splitlines()
    return words


def _run_spellcheck(spellchecker: Hunspell, words: List[str]) -> set:
    unknown = set()
    for word in words:
        # IMPROVE ME: Avoid blowing up on non-Latin-1 words :/
        checkable_word = word.encode("utf-8").decode("latin-1")
        if not spellchecker.spell(checkable_word):
            unknown.add(word)
    return unknown


def _boostrap_spellchecker(locale: str) -> Hunspell:

    # Main dictionary
    dictionary_data_dir = f"data/base_dictionaries/{locale}/"
    if str(os.getcwd()).endswith("/bin"):
        dictionary_data_dir = os.path.join("..", dictionary_data_dir)

    spellchecker = Hunspell(locale, hunspell_data_dir=dictionary_data_dir)

    # Custom dictionary
    custom_dictionary_data_dir = dictionary_data_dir.replace("base_dictionaries", "custom_dictionaries")
    if locale in CUSTOM_DICTIONARIES:
        custom_dictionary_path = os.path.join(custom_dictionary_data_dir, "custom.dic")

        # According to the docs (https://github.com/MSeal/cython_hunspell/blob/master/README.md?plain=1#L166),
        # this should work but it does not:
        # spellchecker.add_dic(custom_dictionary_path)

        # so, instead load the words individually
        for custom_word in _load_unicode_words(custom_dictionary_path):
            spellchecker.add(custom_word.encode("utf-8").decode("latin-1"))

    return spellchecker


def _check_pages_for_spelling_errors(text: str, locale: str) -> List[str]:

    spellchecker = _boostrap_spellchecker(locale)
    words = _clean_up_text_before_spellchecking(text)
    return _run_spellcheck(spellchecker, words)


def _clean_up_text_before_spellchecking(text: str, locale="en") -> str:
    # Initially a naive-ish manual tuning job, because we don't want
    # to over-correct things, either

    chars_to_drop = {
        "en": [
            "(",
            ")",
        ]
    }
    chars_to_swap = {
        "en": [
            ("”", '"'),  # spellchecker doesn't like curly quotes
            ("’", "'"),  # spellchecker doesn't like curly quotes
            # ("-", " "),  # de-hyphenate things
            ("–", " "),  # remove en-dashes
            ("—", " "),  # remove em-dashes
        ]
    }

    trailing_chars_to_drop = {
        "en": [
            ".",
            ",",
            ":",
            ";",
            "?",
            "!",
            "¶",
            "'",
            "®",
            "…",
        ]
    }
    leading_chars_to_drop = {
        "en": [
            "$",
            "£",
            "€",
            "©",
        ]
    }
    trailing_sequences_to_drop = {
        "en": [
            "'s",
            "...",
        ],
    }

    for char in chars_to_drop.get(locale):
        text = text.replace(char, "")

    for current, replacement in chars_to_swap.get(locale):
        text = text.replace(current, replacement)

    text = text.replace("\n", " ").split()
    for _ in range(2):
        # Do it twice to deal with `foo."` etc
        for i, word in enumerate(text):
            if not word:
                continue
            if word[0] in leading_chars_to_drop.get(locale):
                text[i] = word[1:]

            if word[-1] in trailing_chars_to_drop.get(locale):
                word = word[:-1]
                text[i] = word

            for trailing_sequence in trailing_sequences_to_drop.get(locale):
                if word.endswith(trailing_sequence):
                    word = word[: -len(trailing_sequence)]
                    text[i] = word

    return text


def _filter_urls_to_available_locales(urls: List[str], locales: List[str]) -> Dict:

    grouped = defaultdict(list)

    # Playground: https://regex101.com/r/j7lMf1/2
    pattern = re.compile(r"http(?:s*):\/\/.*\/([a-z]{2}-*[A-Z]{0,2})\/")
    for url in urls:
        if match := pattern.match(url):
            locale = match.groups()[0]
            if locale in locales:
                grouped[locale].append(url)

    return grouped


def _dump_unknown_words_to_file(
    results: Dict[str, set],
    hostname: str,
    batch_label: str,
) -> str:
    """Output files of results specific to the current hostname and batch to JSON"""
    _output_path = _get_output_path()
    _now = datetime.datetime.utcnow().isoformat(timespec="seconds").replace(":", "-")  # Github actions doesn't like colons in filenames
    _base_filename = f"{UNKNOWN_WORDS_FILENAME_FRAGMENT}_{hostname}_{batch_label}_{_now}.json"
    output_filepath = os.path.join(_output_path, _base_filename)

    fp_json = open(output_filepath, "w")
    fp_json.write(json.dumps(results))
    fp_json.close()
    click.echo(f"JSON version of results output to {output_filepath}")

    return output_filepath


if __name__ == "__main__":
    run_checks()

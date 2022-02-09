# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
This is a tool to help verify the content of the specified website.
Run the specified checks on specified URLs and issue a report
"""

import os
import re
from collections import defaultdict
from functools import cache
from tempfile import NamedTemporaryFile
from typing import Dict, Iterable, List
from urllib.parse import urlparse

import click
import requests
from bs4 import BeautifulSoup
from yaml import safe_load


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
    help=("If the sitemap points to a different domain (eg a CDN domain), override it and replace it with the hostname that served the " "sitemap,"),
)
@click.option(
    "--specific-url",
    default=None,
    help="Specific URL to check. This flag can be used multiple times, once per URL",
    multiple=True,
)
@click.option(
    "--dump",
    default=True,
    is_flag=True,
    help="Dump the results of unexpected links to a file",
)
def run_checks(
    sitemap_url: str,
    maintain_hostname: bool,
    specific_url: Iterable,
    dump: bool,
) -> None:

    # Let's tidy up that variable name
    specific_urls = specific_url

    if not sitemap_url and not specific_urls:
        raise Exception("No sitemap or input URLs specified")

    host_url = sitemap_url or specific_urls[0]

    hostname = urlparse(host_url).netloc

    config = _get_allowlist_config(hostname)

    urls_to_check = _build_urls_to_check(
        sitemap_url=sitemap_url,
        specific_urls=specific_url,
        maintain_hostname=maintain_hostname,
    )

    results = _check_pages(urls_to_check, config)

    flat_results_path, nested_results_path = None, None

    if dump:
        flat_results_path, nested_results_path = _dump_to_file(results)

    if results:
        if nested_results_path and flat_results_path:
            click.echo(f"Unexpected outbound URLs found! Simple report: {flat_results_path} Nested report: {nested_results_path} ")
        raise Exception("Unexpected oubound URLs detected.")

    click.echo("Checks completed and no unexpected outbound URLs found")


def _dump_to_file(results: Dict) -> None:
    # results is a dictionary where the values are a {set}
    fp = NamedTemporaryFile(delete=False)
    for unexpected_url, occurrences in results.items():
        line = "\n\n{unexpected_url}\n, {occurrences}".format(
            unexpected_url=unexpected_url,
            occurrences="\n\t\t".join(occurrences),
        )
        fp.write(line.encode("utf-8"))
    fp.close()
    click.echo(f"Nested debug data dumped to {fp.name}")

    fp2 = NamedTemporaryFile(delete=False)
    fp2.write("\n".join([key for key in results.keys()]).encode("utf-8"))
    fp2.close
    click.echo(f"Flat list of unexpected URLs dumped to {fp2.name}")


def _get_allowlist_path() -> os.PathLike:
    path_components = ["data", "allowlist.yml"]
    working_dir = os.getcwd()
    if str(working_dir).endswith("/bin"):
        path_components = [working_dir, ".."] + path_components
    return os.path.join("./", *path_components)


@cache
def _get_allowlist_config(hostname) -> dict:
    click.echo("Loading allowlist from file")
    fp = open(_get_allowlist_path())
    config_data = safe_load(fp)

    site_config = None

    # Find the appropriate config for the config node
    for site_identifier, config_dict in config_data.items():
        if config_dict.get("hostname") == hostname:
            site_config = config_dict
            break

    if not site_config:
        raise Exception(f"Could not find an allowlist for {hostname}")

    # While we're here, turn the list of full strings to match into a set, to optimise lookups later.
    # We _could_ mark this up as sets in YAML, but that gets parsed as {key_x: null, ...} so
    # still would need cleaning up
    site_config["allowed_outbound_url_literals"] = set(site_config["allowed_outbound_url_literals"])

    # Also, let's pre-compile our regexes, at least:
    compiled_regexes = set()
    for raw_regex in site_config["allowed_outbound_url_regexes"]:
        compiled_regexes.add(re.compile(raw_regex))

    # Warning: re-using same key but with [slightly] different data than sourced from YAML
    site_config["allowed_outbound_url_regexes"] = compiled_regexes
    return site_config


def _verify_url_allowed(url: str, config: dict) -> bool:

    # quickest check first
    if url in config["allowed_outbound_url_literals"]:
        return True

    for compiled_regex in config["allowed_outbound_url_regexes"]:
        if compiled_regex.match(url):
            return True

    # Belt and braces:
    return False


def _check_pages(urls: List[str], config: Dict) -> Dict:

    unlisted_outbound_urls = defaultdict(set)
    # oubound url is they key, a set of pages it's on is the value

    for page_url in urls:
        click.echo(f"Pulling down {page_url}")
        resp = requests.get(page_url)
        resp.raise_for_status()
        content = resp.text
        soup = BeautifulSoup(content, "html5lib")
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
                if _url and not _verify_url_allowed(_url, config):
                    unlisted_outbound_urls[_url].add(page_url)

        # TODO: OPTIMISE THE ABOVE - it's marvellously inefficient

        # TODO: find URLS in rendered content, too

    print(f"unlisted_outbound_urls: {len(unlisted_outbound_urls)}")

    return unlisted_outbound_urls


def _build_urls_to_check(
    sitemap_url: str,
    specific_urls: Iterable,
    maintain_hostname: bool,
) -> List[str]:
    urls = []
    if sitemap_url:
        urls += _get_urls_from_sitemap(sitemap_url)
    if specific_urls:
        # Don't forget any manually specified URLs
        urls += specific_urls
    urls = _update_hostname_if_required(
        maintain_hostname,
        sitemap_url,
        urls,
    )
    click.echo(f"Discovered {len(urls)} URLs to check")
    return urls


def _get_urls_from_sitemap(sitemap_url: str) -> List[str]:

    urls = []

    resp = requests.get(sitemap_url)
    resp.raise_for_status()

    sitemap_xml = resp.text
    soup = BeautifulSoup(sitemap_xml, "lxml")

    # Look for a <sitemap> node, and get each as a URL for a locale-specific sitemap
    sitemap_nodes = soup.find_all("sitemap")
    if len(sitemap_nodes):
        click.echo(f"Discovered {len(sitemap_nodes)} child sitemaps")
    for sitemap_node in sitemap_nodes:
        sitemap_url = sitemap_node.loc.text
        click.echo(f"Diving into {sitemap_url}")
        urls.extend(_get_urls_from_sitemap(sitemap_url))

    # look for regular URL nodes, which may or may not co-exist alongside sitemap nodes
    url_nodes = soup.find_all("url")
    if url_nodes:
        click.echo(f"Adding {len(url_nodes)} URLs")
        urls.extend([url.loc.text for url in url_nodes])

    return urls


def _update_hostname_if_required(maintain_hostname: bool, sitemap_url: str, urls: List[str]) -> List[str]:
    """If the urls start with a different hostname than in the sitemap_url,
    replace it in each of them.

    This is so that if sitemap_url is on an origin server but its sitemap references
    the CDN domain, we can actually hit the origin to test its pages directly."""

    if not maintain_hostname:
        return urls
    parsed_original = urlparse(sitemap_url)
    origin_hostname_with_scheme = f"{parsed_original.scheme}://{parsed_original.netloc}"

    # This assumes all URLs in the sitemap have the same hostname, so we can use the first
    # as our source of truth. This doesn't seem unreasonable.
    parsed_current = urlparse(urls[0])
    candidate_hostname_with_scheme = f"{parsed_current.scheme}://{parsed_current.netloc}"

    if origin_hostname_with_scheme == candidate_hostname_with_scheme:
        click.echo(f"No need to replace the hostname on this batch of URLs: {candidate_hostname_with_scheme}")

    return [url.replace(candidate_hostname_with_scheme, origin_hostname_with_scheme) for url in urls]


if __name__ == "__main__":
    run_checks()

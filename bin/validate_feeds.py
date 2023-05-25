#! /usr/bin/env python3

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import os
import sys
from functools import cache
from typing import Union

import click
import feedparser
import requests
from pyaml_env import parse_config

# Awkward hack to allow importing into tests
try:
    from utils import _get_configuration_path, _print
except ImportError:
    from .utils import _get_configuration_path, _print


@click.command()
@click.option(
    "--hostname",
    help="Hostname of site where the feeds are located - eg www.mozilla.org",
)
def validate_feeds(hostname) -> None:

    feed_config = _load_config(
        hostname=hostname,
        filename=os.environ.get("FEED_CONFIG_FILENAME", "data/feeds-to-check.yaml"),
    )
    failures = {}

    for feed_path in feed_config["feed_paths"]:
        if hostname.startswith("localhost:"):
            scheme = "http://"
        else:
            scheme = "https://"
        feed_url = f"{scheme}{hostname}/{feed_path}"

        result = _check_feed(feed_url)

        if result is not None:
            failures[feed_url] = result

    if len(failures) > 0:
        _print("Invalid feed detected:")
        for url, failure in failures.items():
            _print(f"{url} {failure}")
        sys.exit(1)
    else:
        _print("No issues found.")


def _load_feed(url: str) -> str:
    response = requests.get(url)
    response.raise_for_status()
    return response.content.decode("utf-8")


def _check_feed(feed_url: str) -> Union[str, None]:
    feed_data = _load_feed(feed_url)

    parser = feedparser.parse(feed_data)
    if parser.bozo > 0:
        exc = parser.bozo_exception
        return f"{exc.getMessage()} @ L:{exc.getLineNumber()}"
    return None


@cache
def _load_config(hostname: str, filename: str) -> str:
    _print(f"Seeking a config in {filename}")
    config_data = parse_config(_get_configuration_path(filename))
    feed_config = None

    for candidate_hostname in config_data.get("relevant_hostnames"):
        if candidate_hostname == hostname:
            feed_config = config_data
            break

    if not feed_config:
        _print(f"Could not find a config for {hostname}. Quitting")
        sys.exit(1)

    return feed_config


if __name__ == "__main__":
    validate_feeds()

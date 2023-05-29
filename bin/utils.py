# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import os
import sys
from typing import List

from slack_sdk.webhook import WebhookClient as SlackWebhookClient

SLACK_NOTIFICATION_WEBHOOK_URL = os.environ.get("SLACK_NOTIFICATION_WEBHOOK_URL")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(SCRIPT_DIR))


def _get_configuration_path(pathname: str) -> os.PathLike:
    # Get the path, allowing for this being called from the project root or the bin/ dir
    path_components = pathname.split("/")
    working_dir = os.getcwd()
    if str(working_dir).endswith("/bin"):
        path_components = [working_dir, ".."] + path_components
    return os.path.join("", *path_components)


def _print(message: str) -> None:
    sys.stdout.write(message)
    sys.stdout.write("\n")


def get_output_path(directory_name="output") -> os.PathLike:
    # Get the path, allowing for this being called from the project root or the bin/ dir
    path_components = [
        directory_name,
    ]
    working_dir = os.getcwd()
    if str(working_dir).endswith("/bin"):
        path_components = [working_dir, ".."] + path_components
    return os.path.join("", *path_components)


def load_from_dumped_cache(directory_name) -> List[str]:
    # Load all the HTML files from disk
    output = []
    for filename in os.listdir(directory_name):
        if not filename.startswith("."):
            with open(f"{directory_name}/{filename}", "r") as fp:
                output.append(fp.read())
    return output


def ping_slack(message):
    if SLACK_NOTIFICATION_WEBHOOK_URL:
        message = "No data-country-code values found in CDN content. See {GITHUB_SERVER_URL}/{GITHUB_REPOSITORY}/actions/runs/{GITHUB_RUN_ID}"
        slack_client = SlackWebhookClient(SLACK_NOTIFICATION_WEBHOOK_URL)
        slack_client.send(text=message)

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import os
import sys

from slack_sdk.webhook import WebhookClient as SlackWebhookClient


def _print(message: str) -> None:
    sys.stdout.write(message)
    sys.stdout.write("\n")


# TODO: De-duplicate and move to shared code
def _get_output_path() -> os.PathLike:
    # Get the path, allowing for this being called from the project root or the bin/ dir
    path_components = [
        "output",
    ]
    working_dir = os.getcwd()
    working_dir_components = [working_dir]
    if str(working_dir).endswith("/bin"):
        working_dir_components.append("..")
    path_components = working_dir_components + path_components
    return os.path.join("", *path_components)


def main():
    """Look for a report of unexpected URLs found during the scan.
    If we find one, alert via Slack.

    Note that a separate Sentry ping is sent up when the unexpected
    URLs are found, so the Slack message isn't the only alert.
    """

    GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "NO-REPOSITORY-IN-USE")
    GITHUB_SERVER_URL = os.environ.get("GITHUB_SERVER_URL", "NO-GITHUB")
    GITHUB_RUN_ID = os.environ.get("GITHUB_RUN_ID", "NO-RUN-NUMBER")
    SLACK_NOTIFICATION_WEBHOOK_URL = os.environ.get("SLACK_NOTIFICATION_WEBHOOK_URL")

    UNEXPECTED_URLS_FILENAME_FRAGMENT = "unexpected_for"

    artifact_found = False
    # Do we have any artifacts available? If we _don't_, that's good news
    for filename in os.listdir(_get_output_path()):
        if UNEXPECTED_URLS_FILENAME_FRAGMENT in filename:
            artifact_found = True
            break

    if not artifact_found:
        _print("No artifact detected")
        return

    _action_url = f"{GITHUB_SERVER_URL}/{GITHUB_REPOSITORY}/actions/runs/{GITHUB_RUN_ID}/"
    message = f"Unexpected outbound URL found when scanning page content. See {_action_url} for details and saved report."

    if SLACK_NOTIFICATION_WEBHOOK_URL:
        slack_client = SlackWebhookClient(SLACK_NOTIFICATION_WEBHOOK_URL)
        slack_client.send(text=message)

    sys.exit(message)


if __name__ == "__main__":
    main()

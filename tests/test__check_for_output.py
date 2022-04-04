# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from unittest.mock import Mock, patch

import pytest

from bin.check_for_output import _print, main
from bin.run_checks import _get_output_path


@pytest.mark.parametrize(
    "cwd_val, expected",
    (
        ("/foo/bar/bin", "/foo/bar/bin/../output"),
        ("/foo/bar/", "/foo/bar/output"),
    ),
)
@patch("os.getcwd")
def test__get_output_path(mock_getcwd, cwd_val, expected):
    mock_getcwd.return_value = cwd_val
    assert _get_output_path() == expected


@patch("sys.stdout.write")
def test__print(mock_stdout):
    _print("Hello, World!")
    assert mock_stdout.call_count == 2
    assert mock_stdout.call_args_list[0][0][0] == "Hello, World!"
    assert mock_stdout.call_args_list[1][0][0] == "\n"


@pytest.mark.parametrize(
    "mock_listdir_retval,slack_url,expect_alert",
    (
        (
            ["foo", "bar", "unexpected_for_baz"],
            "https://api.example.com/slack/",
            True,
        ),
        (
            ["foo", "bar", "unexpected_for_baz"],
            None,
            True,
        ),
        (
            ["foo", "bar", "baz"],
            None,
            False,
        ),
    ),
    ids=[
        "Artifact found, Slack pinged",
        "Artifact found, Slack NOT pinged",
        "No artifact found, Slack NOT pinged",
    ],
)
@patch("bin.check_for_output.SlackWebhookClient")
@patch("bin.check_for_output._print")
@patch("sys.exit")
def test_main(
    mock_exit,
    mock_print,
    mock_slack_client_class,
    mock_listdir_retval,
    slack_url,
    expect_alert,
    monkeypatch,
):

    mock_slack_client = Mock(name="mock_slack_client")
    mock_slack_client_class.return_value = mock_slack_client

    monkeypatch.setenv("SLACK_NOTIFICATION_WEBHOOK_URL", slack_url)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.example.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "testtesttest")
    monkeypatch.setenv("GITHUB_RUN_ID", "123abc123")

    with patch("bin.check_for_output.os.listdir") as mock_listdir:
        mock_listdir.return_value = mock_listdir_retval
        main()

    if not expect_alert:
        mock_print.assert_called_once_with("No artifact detected")
        assert not mock_exit.called

    else:
        _action_url = "https://github.example.com/testtesttest/actions/runs/123abc123/"
        error_message = f"Unexpected outbound URL found when scanning page content. See {_action_url} for details and saved report."
        mock_exit.assert_called_once_with(error_message)

        if slack_url:
            mock_slack_client.send.assert_called_once_with(text=error_message)

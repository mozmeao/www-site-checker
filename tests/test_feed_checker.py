# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from unittest.mock import patch

from bin.validate_feeds import _check_feed


@patch("bin.validate_feeds._load_feed")
def test_check_feed__invalid_feed(mock_load_feed):
    fp = open("tests/data/broken_feed.xml")
    feed_data = fp.read()
    mock_load_feed.return_value = feed_data

    retval = _check_feed("https://www.mozilla.org")

    assert retval == "mismatched tag @ L:40"
    mock_load_feed.assert_called_once_with("https://www.mozilla.org")


@patch("bin.validate_feeds._load_feed")
def test_check_feed__valid_feed(mock_load_feed):
    fp = open("tests/data/working_feed.xml")
    feed_data = fp.read()
    mock_load_feed.return_value = feed_data

    retval = _check_feed("https://www.mozilla.org")

    assert retval is None
    mock_load_feed.assert_called_once_with("https://www.mozilla.org")

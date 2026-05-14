# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json
from unittest.mock import patch

import bin.scan_site as scan_site


class TestRecordHttpError:
    def setup_method(self):
        scan_site._HTTP_ERRORS.clear()

    def teardown_method(self):
        scan_site._HTTP_ERRORS.clear()

    def test_appends_error_with_correct_shape(self):
        scan_site._record_http_error("https://www.mozilla.org/en-US/missing/", 404)
        assert len(scan_site._HTTP_ERRORS) == 1
        error = scan_site._HTTP_ERRORS[0]
        assert error["url"] == "/en-US/missing/"
        assert error["status_code"] == 404
        assert "timestamp" in error

    def test_strips_hostname_and_preserves_query_string(self):
        scan_site._record_http_error("https://cdn.example.com/some/path?q=1", 404)
        assert scan_site._HTTP_ERRORS[0]["url"] == "/some/path?q=1"

    def test_accumulates_multiple_errors(self):
        scan_site._record_http_error("https://www.mozilla.org/a/", 404)
        scan_site._record_http_error("https://www.mozilla.org/b/", 502)
        assert len(scan_site._HTTP_ERRORS) == 2
        assert scan_site._HTTP_ERRORS[0]["status_code"] == 404
        assert scan_site._HTTP_ERRORS[1]["status_code"] == 502


class TestDumpHttpErrorsToFile:
    def setup_method(self):
        scan_site._HTTP_ERRORS.clear()

    def teardown_method(self):
        scan_site._HTTP_ERRORS.clear()

    def test_no_file_written_when_no_errors(self, tmp_path):
        with patch("bin.scan_site.get_output_path", return_value=str(tmp_path)):
            scan_site._dump_http_errors_to_file("example.com", "1")
        assert list(tmp_path.iterdir()) == []

    def test_writes_json_file_with_correct_content(self, tmp_path):
        scan_site._HTTP_ERRORS.append(
            {"url": "/gone/", "status_code": 404, "timestamp": "2026-01-01T00:00:00"}
        )
        with patch("bin.scan_site.get_output_path", return_value=str(tmp_path)):
            scan_site._dump_http_errors_to_file("example.com", "3")

        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name.startswith("http_errors_for_example.com_3_")
        assert files[0].name.endswith(".json")
        data = json.loads(files[0].read_text())
        assert data == [
            {"url": "/gone/", "status_code": 404, "timestamp": "2026-01-01T00:00:00"}
        ]

    def test_filename_uses_batch_label(self, tmp_path):
        scan_site._HTTP_ERRORS.append(
            {"url": "/x/", "status_code": 404, "timestamp": "2026-01-01T00:00:00"}
        )
        with patch("bin.scan_site.get_output_path", return_value=str(tmp_path)):
            scan_site._dump_http_errors_to_file("cdn.mozilla.net", "5")

        files = list(tmp_path.iterdir())
        assert "cdn.mozilla.net_5_" in files[0].name

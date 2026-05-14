# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json
from unittest.mock import patch

from bin.handle_site_scan_output import (
    _create_http_error_issues,
    _get_hashed_value,
    _load_http_errors,
)


class TestLoadHttpErrors:
    def test_deduplicates_same_url_across_batch_files(self, tmp_path):
        batch1 = [{"url": "/missing/", "status_code": 404, "timestamp": "T1"}]
        batch2 = [
            {"url": "/missing/", "status_code": 404, "timestamp": "T2"},
            {"url": "/other/", "status_code": 404, "timestamp": "T2"},
        ]
        (tmp_path / "http_errors_for_cdn_1_2026.json").write_text(json.dumps(batch1))
        (tmp_path / "http_errors_for_cdn_2_2026.json").write_text(json.dumps(batch2))

        result = _load_http_errors(str(tmp_path))

        assert {(e["url"], e["status_code"]) for e in result} == {
            ("/missing/", 404),
            ("/other/", 404),
        }

    def test_ignores_unexpected_url_json_files(self, tmp_path):
        (tmp_path / "unexpected_urls_for_cdn_structured.json").write_text(
            json.dumps({})
        )
        assert _load_http_errors(str(tmp_path)) == []

    def test_returns_empty_when_no_files(self, tmp_path):
        assert _load_http_errors(str(tmp_path)) == []

    def test_same_url_different_status_codes_kept_separately(self, tmp_path):
        batch = [
            {"url": "/flaky/", "status_code": 404, "timestamp": "T"},
            {"url": "/flaky/", "status_code": 502, "timestamp": "T"},
        ]
        (tmp_path / "http_errors_for_cdn_1_2026.json").write_text(json.dumps(batch))

        result = _load_http_errors(str(tmp_path))
        assert len(result) == 2


class TestCreateHttpErrorIssues:
    def test_creates_one_issue_per_status_code(self):
        errors = [
            {"url": "/a/", "status_code": 404, "timestamp": "T"},
            {"url": "/b/", "status_code": 404, "timestamp": "T"},
            {"url": "/c/", "status_code": 502, "timestamp": "T"},
        ]
        with (
            patch(
                "bin.handle_site_scan_output._get_current_github_issues",
                return_value=[],
            ),
            patch(
                "subprocess.check_output",
                return_value=b"https://github.com/org/repo/issues/1\n",
            ) as mock_gh,
        ):
            result = _create_http_error_issues(errors, "https://actions/run/1")

        assert len(result) == 2  # one for 404s, one for 502
        assert mock_gh.call_count == 2

    def test_skips_when_fingerprint_already_in_open_issue(self):
        errors = [{"url": "/gone/", "status_code": 404, "timestamp": "T"}]
        fingerprint = _get_hashed_value(["/gone/"])
        existing_issues = [{"body": f"Some text\n\nFingerprint: {fingerprint}"}]

        with (
            patch(
                "bin.handle_site_scan_output._get_current_github_issues",
                return_value=existing_issues,
            ),
            patch("subprocess.check_output") as mock_gh,
        ):
            result = _create_http_error_issues(errors, "https://actions/run/1")

        assert result == []
        mock_gh.assert_not_called()

    def test_returns_empty_list_for_no_errors(self):
        with patch(
            "bin.handle_site_scan_output._get_current_github_issues", return_value=[]
        ):
            result = _create_http_error_issues([], "https://actions/run/1")
        assert result == []

    def test_issue_body_contains_all_affected_urls(self):
        errors = [
            {"url": "/en-US/missing/", "status_code": 404, "timestamp": "T"},
            {"url": "/fr/missing/", "status_code": 404, "timestamp": "T"},
        ]
        captured = []

        def fake_check_output(cmd, **kwargs):
            captured.append(cmd)
            return b"https://github.com/org/repo/issues/99\n"

        with (
            patch(
                "bin.handle_site_scan_output._get_current_github_issues",
                return_value=[],
            ),
            patch("subprocess.check_output", side_effect=fake_check_output),
        ):
            _create_http_error_issues(errors, "https://actions/run/42")

        assert len(captured) == 1
        args = captured[0]
        body = args[args.index("--body") + 1]
        assert "/en-US/missing/" in body
        assert "/fr/missing/" in body
        assert "https://actions/run/42" in body

    def test_issue_title_includes_count_and_status_code(self):
        errors = [
            {"url": "/a/", "status_code": 404, "timestamp": "T"},
            {"url": "/b/", "status_code": 404, "timestamp": "T"},
        ]
        captured = []

        def fake_check_output(cmd, **kwargs):
            captured.append(cmd)
            return b"https://github.com/org/repo/issues/5\n"

        with (
            patch(
                "bin.handle_site_scan_output._get_current_github_issues",
                return_value=[],
            ),
            patch("subprocess.check_output", side_effect=fake_check_output),
        ):
            _create_http_error_issues(errors, "https://actions/run/1")

        args = captured[0]
        title = args[args.index("--title") + 1]
        assert "2" in title  # URL count
        assert "404" in title

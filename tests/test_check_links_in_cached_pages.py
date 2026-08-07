# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from unittest.mock import MagicMock, patch

from bin.check_links_in_cached_pages import (
    _build_issue_body,
    _check_url,
    _collect_links_from_cache,
    _filename_to_url,
    _is_reportable_status,
    _redact_page_url,
    _site_scoped_fingerprint,
    _strip_fragment,
)


class TestFilenameToUrl:
    def test_path_ending_in_slash_round_trips(self):
        # encoded form of https://www.mozilla.org/en-US/firefox/
        assert (
            _filename_to_url("https%3A__www.mozilla.org_en-US_firefox_.html")
            == "https://www.mozilla.org/en-US/firefox/"
        )

    def test_path_with_natural_html_extension_preserves_html(self):
        # encoded form of https://www.mozilla.org/en-US/security/advisories/mfsa2024-01.html
        assert (
            _filename_to_url(
                "https%3A__www.mozilla.org_en-US_security_advisories_mfsa2024-01.html"
            )
            == "https://www.mozilla.org/en-US/security/advisories/mfsa2024-01.html"
        )

    def test_path_without_html_or_slash(self):
        # encoded form of http://localhost:8000/en-US
        assert (
            _filename_to_url("http%3A__localhost%3A8000_en-US")
            == "http://localhost:8000/en-US"
        )


class TestIsReportableStatus:
    def test_404_is_reportable(self):
        assert _is_reportable_status(404) is True

    def test_5xx_is_reportable(self):
        for code in (500, 502, 503, 504, 599):
            assert _is_reportable_status(code) is True

    def test_2xx_3xx_not_reportable(self):
        for code in (200, 201, 204, 301, 302, 304):
            assert _is_reportable_status(code) is False

    def test_other_4xx_not_reportable(self):
        # 403/429 are common bot-blocking responses; we don't report them
        for code in (400, 401, 403, 410, 429):
            assert _is_reportable_status(code) is False


class TestStripFragment:
    def test_drops_fragment(self):
        assert (
            _strip_fragment("https://example.com/path?q=1#section")
            == "https://example.com/path?q=1"
        )

    def test_leaves_fragmentless_url_unchanged(self):
        assert (
            _strip_fragment("https://example.com/path?q=1")
            == "https://example.com/path?q=1"
        )


class TestRedactPageUrl:
    def test_same_host_strips_scheme_and_netloc(self):
        assert (
            _redact_page_url(
                "https://www.mozilla.org/en-US/firefox/", "www.mozilla.org"
            )
            == "/en-US/firefox/"
        )

    def test_same_host_preserves_query_string(self):
        assert (
            _redact_page_url("https://www.mozilla.org/page?q=1", "www.mozilla.org")
            == "/page?q=1"
        )

    def test_different_host_leaves_url_intact(self):
        assert (
            _redact_page_url("https://example.com/foo", "www.mozilla.org")
            == "https://example.com/foo"
        )


class TestSiteScopedFingerprint:
    def test_different_site_labels_produce_different_hashes_for_same_urls(self):
        urls = ["https://example.org/dead"]
        mozorg_fp = _site_scoped_fingerprint("www.mozilla.org", urls)
        firefox_fp = _site_scoped_fingerprint("www.firefox.com", urls)
        assert mozorg_fp != firefox_fp

    def test_url_order_does_not_affect_hash(self):
        assert _site_scoped_fingerprint(
            "site", ["b", "a", "c"]
        ) == _site_scoped_fingerprint("site", ["a", "b", "c"])


class TestCollectLinksFromCache:
    def _write_page(self, tmp_path, filename, html):
        (tmp_path / filename).write_text(html)

    def test_extracts_anchor_script_link_tags(self, tmp_path):
        html = """
        <html><head>
        <link rel="stylesheet" href="/styles.css">
        <script src="https://cdn.example.com/lib.js"></script>
        </head><body>
        <a href="https://example.org/dead">dead</a>
        <a href="/en-US/about/">about</a>
        </body></html>
        """
        self._write_page(
            tmp_path, "https%3A__www.mozilla.org_en-US_firefox_.html", html
        )

        links = _collect_links_from_cache(str(tmp_path))

        # Relative hrefs resolved against the page URL
        assert "https://www.mozilla.org/styles.css" in links
        assert "https://cdn.example.com/lib.js" in links
        assert "https://example.org/dead" in links
        assert "https://www.mozilla.org/en-US/about/" in links

    def test_records_containing_pages_per_link(self, tmp_path):
        page_a = "<a href='https://example.org/x'>x</a>"
        page_b = "<a href='https://example.org/x'>x again</a>"
        self._write_page(tmp_path, "https%3A__www.mozilla.org_en-US_a_.html", page_a)
        self._write_page(tmp_path, "https%3A__www.mozilla.org_en-US_b_.html", page_b)

        links = _collect_links_from_cache(str(tmp_path))

        assert links["https://example.org/x"] == {
            "https://www.mozilla.org/en-US/a/",
            "https://www.mozilla.org/en-US/b/",
        }

    def test_skips_mailto_tel_javascript_data_and_fragment(self, tmp_path):
        html = """
        <a href="mailto:user@example.com">m</a>
        <a href="tel:+1234">t</a>
        <a href="javascript:void(0)">j</a>
        <a href="data:image/png;base64,XXXX">d</a>
        <a href="#section-2">f</a>
        <a href="">empty</a>
        """
        self._write_page(tmp_path, "https%3A__www.mozilla.org_en-US_x_.html", html)
        links = _collect_links_from_cache(str(tmp_path))
        assert links == {}

    def test_strips_fragments_so_same_target_dedups(self, tmp_path):
        html = (
            '<a href="https://example.org/page#one">a</a>'
            '<a href="https://example.org/page#two">b</a>'
        )
        self._write_page(tmp_path, "https%3A__www.mozilla.org_en-US_x_.html", html)
        links = _collect_links_from_cache(str(tmp_path))
        assert list(links.keys()) == ["https://example.org/page"]


class TestCheckUrl:
    def test_returns_head_status_when_head_succeeds(self):
        with patch("bin.check_links_in_cached_pages.requests.head") as mock_head:
            mock_head.return_value = MagicMock(status_code=200)
            assert _check_url("https://example.com/") == 200

    def test_falls_back_to_get_when_head_returns_405(self):
        with (
            patch("bin.check_links_in_cached_pages.requests.head") as mock_head,
            patch("bin.check_links_in_cached_pages.requests.get") as mock_get,
        ):
            mock_head.return_value = MagicMock(status_code=405)
            mock_get.return_value = MagicMock(status_code=200)
            assert _check_url("https://example.com/") == 200
            mock_get.assert_called_once()

    def test_returns_404_status(self):
        with patch("bin.check_links_in_cached_pages.requests.head") as mock_head:
            mock_head.return_value = MagicMock(status_code=404)
            assert _check_url("https://example.com/gone") == 404

    def test_returns_none_on_transport_error(self):
        import requests as real_requests

        with patch("bin.check_links_in_cached_pages.requests.head") as mock_head:
            mock_head.side_effect = real_requests.ConnectionError("boom")
            assert _check_url("https://example.com/") is None

    def test_retries_5xx_once_then_returns_final_status(self):
        with (
            patch("bin.check_links_in_cached_pages.requests.head") as mock_head,
            patch("bin.check_links_in_cached_pages.time.sleep"),
        ):
            mock_head.side_effect = [
                MagicMock(status_code=503),
                MagicMock(status_code=503),
            ]
            assert _check_url("https://example.com/") == 503
            assert mock_head.call_count == 2


class TestBuildIssueBody:
    def test_body_includes_urls_pages_action_url_and_fingerprint(self):
        records = [
            {
                "url": "https://example.org/dead",
                "status_code": 404,
                "containing_pages": [
                    "https://www.mozilla.org/en-US/firefox/",
                    "https://www.mozilla.org/en-US/about/",
                ],
            }
        ]
        body = _build_issue_body(
            site_label="www.mozilla.org",
            status_code=404,
            error_records=records,
            action_url="https://gh/actions/run/42",
            in_scope_hostname="www.mozilla.org",
            fingerprint="abc123",
        )
        assert "https://example.org/dead" in body
        # in-scope hostnames are redacted to path-only
        assert "/en-US/firefox/" in body
        assert "/en-US/about/" in body
        assert "https://gh/actions/run/42" in body
        assert "Fingerprint: abc123" in body
        assert "www.mozilla.org" in body
        assert "404 Not Found" in body

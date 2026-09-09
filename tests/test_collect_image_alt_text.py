# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import csv

from bin.collect_image_alt_text import (
    _collect_images_from_cache,
    _redact_page_url,
    _write_csv,
)


class TestRedactPageUrl:
    def test_same_host_strips_scheme_and_netloc(self):
        assert (
            _redact_page_url(
                "https://www.mozilla.org/en-US/firefox/", "www.mozilla.org"
            )
            == "/en-US/firefox/"
        )

    def test_different_host_leaves_url_intact(self):
        assert (
            _redact_page_url("https://example.com/foo", "www.mozilla.org")
            == "https://example.com/foo"
        )


class TestCollectImagesFromCache:
    def _write_page(self, tmp_path, filename, html):
        (tmp_path / filename).write_text(html)

    def test_extracts_img_with_alt(self, tmp_path):
        html = '<html><body><img src="/logo.png" alt="Mozilla logo"></body></html>'
        self._write_page(
            tmp_path, "https%3A__www.mozilla.org_en-US_firefox_.html", html
        )

        records = _collect_images_from_cache(str(tmp_path))

        assert len(records) == 1
        assert records[0]["img_src"] == "https://www.mozilla.org/logo.png"
        assert records[0]["alt_text"] == "Mozilla logo"
        assert records[0]["alt_attribute_present"] is True

    def test_detects_missing_alt_attribute(self, tmp_path):
        html = '<html><body><img src="/hero.jpg"></body></html>'
        self._write_page(
            tmp_path, "https%3A__www.mozilla.org_en-US_firefox_.html", html
        )

        records = _collect_images_from_cache(str(tmp_path))

        assert len(records) == 1
        assert records[0]["alt_text"] == ""
        assert records[0]["alt_attribute_present"] is False

    def test_empty_alt_is_present(self, tmp_path):
        html = '<html><body><img src="/spacer.gif" alt=""></body></html>'
        self._write_page(
            tmp_path, "https%3A__www.mozilla.org_en-US_firefox_.html", html
        )

        records = _collect_images_from_cache(str(tmp_path))

        assert len(records) == 1
        assert records[0]["alt_text"] == ""
        assert records[0]["alt_attribute_present"] is True

    def test_resolves_relative_src(self, tmp_path):
        html = '<html><body><img src="images/photo.png" alt="photo"></body></html>'
        self._write_page(
            tmp_path, "https%3A__www.mozilla.org_en-US_firefox_.html", html
        )

        records = _collect_images_from_cache(str(tmp_path))

        assert (
            records[0]["img_src"]
            == "https://www.mozilla.org/en-US/firefox/images/photo.png"
        )

    def test_skips_img_without_src(self, tmp_path):
        html = '<html><body><img alt="no src"></body></html>'
        self._write_page(
            tmp_path, "https%3A__www.mozilla.org_en-US_firefox_.html", html
        )

        records = _collect_images_from_cache(str(tmp_path))

        assert records == []

    def test_no_images_returns_empty_list(self, tmp_path):
        html = "<html><body><p>No images here</p></body></html>"
        self._write_page(
            tmp_path, "https%3A__www.mozilla.org_en-US_firefox_.html", html
        )

        records = _collect_images_from_cache(str(tmp_path))

        assert records == []

    def test_multiple_images_on_one_page(self, tmp_path):
        html = """
        <html><body>
        <img src="/a.png" alt="A">
        <img src="/b.png" alt="B">
        <img src="/c.png">
        </body></html>
        """
        self._write_page(
            tmp_path, "https%3A__www.mozilla.org_en-US_firefox_.html", html
        )

        records = _collect_images_from_cache(str(tmp_path))

        assert len(records) == 3
        srcs = {r["img_src"] for r in records}
        assert "https://www.mozilla.org/a.png" in srcs
        assert "https://www.mozilla.org/b.png" in srcs
        assert "https://www.mozilla.org/c.png" in srcs


class TestWriteCsv:
    def test_writes_sorted_csv(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ALT_TEXT_OUTPUT_DIR", str(tmp_path))
        import bin.collect_image_alt_text as mod

        monkeypatch.setattr(mod, "ALT_TEXT_OUTPUT_DIR", str(tmp_path))

        records = [
            {
                "page_url": "https://www.mozilla.org/en-US/b/",
                "img_src": "https://www.mozilla.org/b.png",
                "alt_text": "B",
                "alt_attribute_present": True,
            },
            {
                "page_url": "https://www.mozilla.org/en-US/a/",
                "img_src": "https://www.mozilla.org/a.png",
                "alt_text": "",
                "alt_attribute_present": False,
            },
        ]

        path = _write_csv("www.mozilla.org", records, "www.mozilla.org")

        with open(path, newline="") as f:
            reader = csv.reader(f)
            rows = list(reader)

        assert rows[0] == ["page_url", "img_src", "alt_text", "alt_attribute_present"]
        assert rows[1][0] == "/en-US/a/"
        assert rows[1][3] == "false"
        assert rows[2][0] == "/en-US/b/"
        assert rows[2][3] == "true"

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from unittest.mock import patch

import pytest

from bin import handle_site_scan_output
from bin.handle_site_scan_output import (
    MAX_GITHUB_BODY_LENGTH,
    _build_pr_body,
    _build_structured_url_list_for_pr_description,
    _is_valid_url,
    _open_new_issues,
    raise_prs_or_issues,
)
from bin.mailto_links import (
    decode_mailto_fields,
    infer_languages,
    is_valid_mailto,
    mailto_problems,
    render_mailto_details,
    make_single_line_inert,
    render_untrusted_text,
)

# Real examples previously raised as "malformed hyperlink" Issues
GERMAN_SHARE_LINK = (
    "mailto:?subject=Firefox%20l%C3%A4dt%20zum%20Internet-Klassentreffen.%20Und%20du%20bist"
    "%20eingeladen.&body=Firefox%20bringt%20Numa%20Numa%20Guy%2C%20Overly%20Attached"
    "%20Girlfriend%2C%20Apparently%20Kid%20und%20eine%20ganze%20Crew%20von%20Meme-Ikonen"
    "%20aus%20den%20fr%C3%BChen%202000ern%20zusammen"
)
LATVIAN_SHARE_LINK = (
    "mailto:?subject=P%C4%81rsl%C4%93gties%20uz%20Firefox&body=Hei%2C%0D%0A%0D%0AFirefox"
    "%20padara%20p%C4%81rsl%C4%93g%C5%A1anos%20no%20Chrome%20%C4%BCoti%20%C4%81tru."
)
ENGLISH_SHARE_LINK = (
    "mailto:?subject=You%27re%20invited%20to%20Firefox%E2%80%99s%20internet%20reunion"
    "&body=Firefox%20is%20teaming%20up%20with%20Numa%20Numa%20Guy"
)


class TestIsValidMailto:
    @pytest.mark.parametrize(
        "url",
        [
            "mailto:help@anonymco.com",
            "mailto:eurep@itgovernance.eu",
            "MAILTO:security@mozilla.org",
            "mailto:a@mozilla.org,b@mozilla.org",
            "mailto:a%40mozilla.org",
            "mailto:a@mozilla.org?subject=Hello",
            "mailto:?subject=Hi&cc=a@mozilla.org&bcc=b@mozilla.org&to=c@mozilla.org",
            "mailto:?subject=&body=",
            "mailto:?subject=Gr%C3%B6%C3%9Fe%20(beta)%20it's%20*great*",
            "mailto:?body=%D9%85%DB%8C%E2%80%8C%D8%AE%D9%88%D8%A7%D9%87%D9%85",  # Persian ZWNJ
            "mailto:?body=%F0%9F%91%A9%E2%80%8D%F0%9F%92%BB",  # emoji with ZWJ
            GERMAN_SHARE_LINK,
            LATVIAN_SHARE_LINK,
            ENGLISH_SHARE_LINK,
        ],
    )
    def test_valid(self, url):
        assert mailto_problems(url) == []
        assert is_valid_mailto(url)
        assert _is_valid_url(url)

    @pytest.mark.parametrize(
        "url, expected_problem",
        [
            ("https://example.com", "not a mailto"),
            ("mailto:", "no recipient"),
            ("mailto:not-an-email", "invalid email address"),
            ("mailto:a@mozilla.org,", "empty email address"),
            ("mailto:?cc=nope", "invalid email address in 'cc'"),
            ("mailto:?subject=Hello there", "percent-encoded"),
            ("mailto:?subject=Hello\nthere", "percent-encoded"),
            ("mailto:?subject=<script>", "percent-encoded"),
            ('mailto:?subject="x"', "percent-encoded"),
            ("mailto:?subject=`x`", "percent-encoded"),
            ("mailto:?subject=100%", "malformed percent-escape"),
            ("mailto:?subject=%zz", "malformed percent-escape"),
            ("mailto:?subject=%C3%28", "not valid UTF-8"),
            ("mailto:?x-evil=1", "unsupported mailto header"),
            ("mailto:?subject=a&subject=b", "duplicate 'subject'"),
            ("mailto:?subject", "query part without a value"),
            ("mailto:a@b.com#frag", "raw '#'"),
            ("mailto:?subject=@octocat", "raw '@'"),
            ("mailto:?body=evil%E2%80%AEexe.txt", "bidi-override"),  # RLO
            ("mailto:?body=hi%E2%81%A6x", "bidi-override"),  # LRI
            ("mailto:?body=hi%F3%A0%81%81", "invisible"),  # Unicode tag char
            ("mailto:?body=hi%00", "control"),
            ("mailto:?body=hi%EF%BB%BF", "invisible"),  # BOM
            # Mail-header injection: line breaks only belong in the body
            ("mailto:?subject=Hi%0D%0ABcc:%20x%40example.com", "line break"),
            ("mailto:?subject=Hi%0ABcc:%20x%40example.com", "line break"),
            ("mailto:?subject=Hi%E2%80%A8there", "line break"),  # U+2028
            ("mailto:?cc=a%40mozilla.org%0D%0ABcc:b%40mozilla.org", "invalid email"),
        ],
    )
    def test_invalid(self, url, expected_problem):
        problems = mailto_problems(url)
        assert any(expected_problem in p for p in problems), problems
        assert not is_valid_mailto(url)

    def test_non_mailto_urls_still_validated_as_before(self):
        assert _is_valid_url("https://www.mozilla.org/en-US/")
        assert _is_valid_url("/en-US/firefox/")
        assert not _is_valid_url("javascript:alert(1)")
        assert not _is_valid_url("https//broken")


class TestDecodeMailtoFields:
    def test_decodes_to_unicode(self):
        fields = dict(decode_mailto_fields(LATVIAN_SHARE_LINK))
        assert fields["subject"] == "Pārslēgties uz Firefox"
        assert fields["body"].startswith("Hei,\r\n\r\nFirefox padara pārslēgšanos")

    def test_plus_is_literal_not_a_space(self):
        assert decode_mailto_fields("mailto:?subject=1+1") == [("subject", "1+1")]

    def test_lenient_for_bad_utf8(self):
        assert decode_mailto_fields("mailto:?subject=%C3%28") == [("subject", "�(")]

    def test_no_fields_for_plain_address(self):
        assert decode_mailto_fields("mailto:a@mozilla.org") == []

    def test_non_mailto(self):
        assert decode_mailto_fields("https://example.com/?subject=x") == []


class TestInferLanguages:
    def test_uses_locale_prefix_of_pages(self):
        assert infer_languages(["/de/firefox/", "/lv/firefox/switch/"]) == [
            "German (de)",
            "Latvian (lv)",
        ]

    def test_region_locale_falls_back_to_language(self):
        assert infer_languages(["/en-US/firefox/"]) == ["English (en-US)"]
        assert infer_languages(["/es-AR/"]) == ["Argentinian Spanish (es-AR)"]

    def test_unknown_locale_shows_code(self):
        assert infer_languages(["/hsb/firefox/"]) == ["Upper Sorbian (hsb)"]
        assert infer_languages(["/xx/firefox/"]) == ["xx"]

    def test_ignores_non_locale_paths(self):
        assert infer_languages(["/firefox/", "/", ""]) == []

    def test_deduplicates(self):
        assert infer_languages(["/fr/a/", "/fr/b/"]) == ["French (fr)"]


class TestRenderUntrustedText:
    @pytest.mark.parametrize(
        "payload",
        [
            "<script>alert(1)</script>",
            "<img src=x onerror=alert(1)>",
            "[click](javascript:alert(1))",
            "https://evil.example/",
            "**bold** _it_ ~~s~~",
            "```\ncode\n```",
            "# Heading",
            "| a | b |",
        ],
    )
    def test_ascii_punctuation_is_all_escaped(self, payload):
        rendered = render_untrusted_text(payload)
        for line in rendered.split("\n"):
            # drop the "> " blockquote marker and our own @/# code spans
            body = line[2:].replace("`#`", "#").replace("`@`", "@")
            for i, char in enumerate(body):
                if char in "<>[]()*_~`|:/":
                    assert i > 0 and body[i - 1] == "\\", (char, body)

    def test_mentions_and_refs_put_in_code_spans(self):
        rendered = render_untrusted_text("@octocat see #1 and foo@example.com")
        assert rendered == "> `@`octocat see `#`1 and foo`@`example\\.com"

    def test_preserves_line_breaks_as_blockquote(self):
        rendered = render_untrusted_text("Hi,\r\n\r\nLine one\r\nLine two")
        assert rendered == "> Hi\\,\n>\n> Line one\\\n> Line two"

    def test_strips_indentation_that_would_make_a_code_block(self):
        assert render_untrusted_text("     indented") == "> indented"

    def test_makes_invisible_chars_visible(self):
        assert (
            render_untrusted_text("a‮b\U000e0041") == "> a\\[U\\+202E\\]b\\[U\\+E0041\\]"
        )

    def test_keeps_legitimate_joiners(self):
        assert "‌" in render_untrusted_text("می‌خواهم")

    def test_truncates_long_text(self):
        rendered = render_untrusted_text("a" * 5000)
        assert len(rendered) < 2100
        assert rendered.endswith("\\[\\.\\.\\.truncated\\]")

    def test_indent(self):
        assert render_untrusted_text("a\nb", indent="  ") == "  > a\\\n  > b"


class TestMakeSingleLineInert:
    def test_line_breaks_and_hidden_chars_made_visible(self):
        assert (
            make_single_line_inert("a\r\nb\tc\u2028d\u202ee\U000e0041")
            == "a[U+000D][U+000A]b[U+0009]c[U+2028]d[U+202E]e[U+E0041]"
        )

    def test_ordinary_text_unchanged(self):
        url = "mailto:?subject=P%C4%81rsl%C4%93gties&body=Pārslēgties می\u200cخواهم"
        assert make_single_line_inert(url) == url


class TestRenderMailtoDetails:
    def test_non_mailto_renders_nothing(self):
        assert render_mailto_details("https://example.com", ["/de/"]) == ""

    def test_plain_address_renders_nothing(self):
        assert render_mailto_details("mailto:a@mozilla.org", ["/de/"]) == ""

    def test_includes_decoded_fields_and_language(self):
        rendered = render_mailto_details(GERMAN_SHARE_LINK, ["/de/firefox/reunion/"])
        assert "untrusted text" in rendered
        assert "German \\(de\\)" in rendered
        assert "**subject**" in rendered
        assert "> Firefox lädt zum Internet\\-Klassentreffen\\." in rendered

    def test_problems_only_listed_when_requested(self):
        url = "mailto:?subject=Hi&x-evil=1"
        assert "Not auto-allowlisted" not in render_mailto_details(url, [])
        rendered = render_mailto_details(url, [], include_problems=True)
        assert "Not auto-allowlisted" in rendered
        assert "unsupported mailto header" in rendered
        assert "**x\\-evil**" in rendered


@pytest.fixture
def results_cache():
    handle_site_scan_output.RESULTS_CACHE.clear()
    yield handle_site_scan_output.RESULTS_CACHE
    handle_site_scan_output.RESULTS_CACHE.clear()


class TestHandleSiteScanOutputIntegration:
    def test_valid_mailto_links_go_to_pr_not_issue(self, results_cache):
        results_cache.update(
            {
                "https://www.mozilla.org/de/firefox/reunion/": [GERMAN_SHARE_LINK],
                "https://www.mozilla.org/en-US/anonym/terms/": [
                    "mailto:help@anonymco.com",
                    "mailto:?x-evil=1",
                ],
            }
        )
        with (
            patch(
                "bin.handle_site_scan_output._assemble_results",
                return_value=set().union(*results_cache.values()),
            ),
            patch(
                "bin.handle_site_scan_output._update_allowlist", return_value="pr"
            ) as mock_pr,
            patch(
                "bin.handle_site_scan_output._open_new_issues", return_value=[]
            ) as mock_issues,
        ):
            raise_prs_or_issues("unused")

        assert mock_pr.call_args.args[0] == {
            GERMAN_SHARE_LINK,
            "mailto:help@anonymco.com",
        }
        assert mock_issues.call_args.args[0] == {"mailto:?x-evil=1"}

    def test_pr_description_includes_decoded_mailto_content(self, results_cache):
        results_cache.update(
            {
                "https://www.mozilla.org/lv/firefox/switch/": [LATVIAN_SHARE_LINK],
                "https://www.mozilla.org/en-US/": ["https://example.com/"],
            }
        )
        output = _build_structured_url_list_for_pr_description(
            [LATVIAN_SHARE_LINK, "https://example.com/"]
        )
        assert f"* {LATVIAN_SHARE_LINK}\nFound in:\n  * /lv/firefox/switch/\n" in output
        assert "Latvian \\(lv\\)" in output
        assert "  > Pārslēgties uz Firefox" in output
        assert "* https://example.com/\nFound in:\n  * /en-US/\n" in output

    def test_issue_body_includes_problems_and_decoded_content(self, results_cache):
        bad_url = "mailto:?subject=Bonjour&body=evil%E2%80%AEtxt.exe"
        results_cache["https://www.mozilla.org/fr/page/"] = [bad_url]
        captured = {}

        def fake_check_output(cmd, **kwargs):
            with open(cmd[cmd.index("--body-file") + 1]) as fp:
                captured["body"] = fp.read()
            return b"https://github.com/org/repo/issues/1\n"

        with (
            patch(
                "bin.handle_site_scan_output._get_current_github_issues",
                return_value=[],
            ),
            patch("subprocess.check_output", side_effect=fake_check_output),
        ):
            _open_new_issues([bad_url])

        body = captured["body"]
        assert f"    {bad_url}\n" in body
        assert "bidi\\-override" in body
        assert "French \\(fr\\)" in body
        assert "> evil\\[U\\+202E\\]txt\\.exe" in body
        assert "‮" not in body

    def test_issue_body_unchanged_for_non_mailto(self, results_cache):
        results_cache["https://www.mozilla.org/en-US/"] = ["https//broken"]
        captured = {}

        def fake_check_output(cmd, **kwargs):
            with open(cmd[cmd.index("--body-file") + 1]) as fp:
                captured["body"] = fp.read()
            return b"https://github.com/org/repo/issues/1\n"

        with (
            patch(
                "bin.handle_site_scan_output._get_current_github_issues",
                return_value=[],
            ),
            patch("subprocess.check_output", side_effect=fake_check_output),
        ):
            _open_new_issues(["https//broken"])

        assert "    /en-US/\n\nThis cannot be fixed" in captured["body"]
        assert "mailto" not in captured["body"]

    def test_issue_body_cannot_break_out_of_code_blocks(self, results_cache):
        bad_url = "javascript:x\n\n# Injected @octocat \u202e"
        results_cache["https://www.mozilla.org/en-US/a/"] = [bad_url]
        results_cache["https://www.mozilla.org/en-US/b/"] = [bad_url]
        captured = {}

        def fake_check_output(cmd, **kwargs):
            captured["title"] = cmd[cmd.index("--title") + 1]
            with open(cmd[cmd.index("--body-file") + 1]) as fp:
                captured["body"] = fp.read()
            return b"https://github.com/org/repo/issues/1\n"

        with (
            patch(
                "bin.handle_site_scan_output._get_current_github_issues",
                return_value=[],
            ),
            patch("subprocess.check_output", side_effect=fake_check_output),
        ):
            _open_new_issues([bad_url])

        body = captured["body"]
        assert "    javascript:x[U+000A][U+000A]# Injected @octocat [U+202E]\n" in body
        assert "    /en-US/a/\n    /en-US/b/\n" in body
        assert "\u202e" not in body
        assert "\n#" not in body
        assert "\n" not in captured["title"]


class TestPrBodyLength:
    def test_includes_mailto_details_when_short(self, results_cache):
        results_cache["https://www.mozilla.org/lv/x/"] = [LATVIAN_SHARE_LINK]
        body = _build_pr_body([LATVIAN_SHARE_LINK])
        assert "Decoded mailto: content" in body
        assert "omitted" not in body

    def test_drops_mailto_details_rather_than_exceed_github_limit(self, results_cache):
        # The raw URLs alone fit, but not with their decoded content as well
        urls = [f"mailto:?subject={i}{'a' * 1800}&body={'b' * 1800}" for i in range(15)]
        assert sum(len(url) for url in urls) < MAX_GITHUB_BODY_LENGTH
        for i, url in enumerate(urls):
            results_cache[f"https://www.mozilla.org/de/{i}/"] = [url]

        body = _build_pr_body(urls)

        assert len(body) <= MAX_GITHUB_BODY_LENGTH
        assert "Decoded mailto: content omitted" in body
        assert "**Decoded mailto: content**" not in body
        for url in urls:
            assert f"* {url}\n" in body

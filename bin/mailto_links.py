# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Validation and safe, human-readable decoding of mailto: links.

Django's URLValidator only accepts http(s)/ftp(s) schemes, so mailto: links
need their own validation before they can be proposed for the allowlist.

Everything decoded from a mailto: link is untrusted site content. When it's
rendered for a GitHub PR/Issue body, it's escaped so that it can't inject
Markdown/HTML, @-mentions, autolinks or invisible characters, and it's
labelled as untrusted so that anyone (or any agent) reading it treats it
as data rather than instructions.
"""

import re
import string
import unicodedata
from typing import Iterable, List, Tuple
from urllib.parse import unquote

from django.conf.locale import LANG_INFO
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator

MAILTO_SCHEME = "mailto:"

ADDRESS_HEADERS = frozenset({"to", "cc", "bcc"})
TEXT_HEADERS = frozenset({"subject", "body"})
ALLOWED_HEADERS = ADDRESS_HEADERS | TEXT_HEADERS

# Characters that must be percent-encoded in a well-formed mailto: URL
# (RFC 6068 / RFC 3986). Any of these appearing raw means a human should look.
_DISALLOWED_RAW_CHARS = re.compile(r'[\s<>"`{}|\\^\x00-\x1f\x7f]')
_MALFORMED_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
# An '@' not preceded by an email-local-part character - eg "=@someone"
_POSSIBLE_MENTION = re.compile(r"(?<![A-Za-z0-9._+-])@")

# Format (Cf) characters that legitimately appear in natural-language text:
# ZWNJ/ZWJ (Persian, Indic scripts, emoji sequences), LRM/RLM and soft hyphen.
# Every other Cf character - bidi embeddings/overrides/isolates (Trojan Source),
# Unicode tag characters (invisible "ASCII smuggling" of prompt text),
# BOM, etc - is treated as suspicious.
_ALLOWED_FORMAT_CHARS = frozenset({"­", "‌", "‍", "‎", "‏"})

# Code points that are effectively line breaks
_LINE_BREAKS = re.compile(r"\r\n|[\r  \x85]")

_LOCALE_SEGMENT = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")

# Keep PR/Issue bodies well under GitHub's 65536-char body limit
MAX_DISPLAY_FIELD_LENGTH = 2000

_email_validator = EmailValidator()


def is_mailto(url: str) -> bool:
    return url[: len(MAILTO_SCHEME)].lower() == MAILTO_SCHEME


def _is_suspicious_char(char: str) -> bool:
    if char in "\r\n\t":
        return False
    category = unicodedata.category(char)
    if category == "Cf":
        return char not in _ALLOWED_FORMAT_CHARS
    # Cc: control, Co: private use, Cs: surrogate, Cn: unassigned
    return category in {"Cc", "Co", "Cs", "Cn"}


def _split_mailto(url: str) -> Tuple[str, List[str]]:
    """Return the raw (still percent-encoded) recipient part and query parts"""
    remainder = url[len(MAILTO_SCHEME) :]
    recipients, _, query = remainder.partition("?")
    query_parts = query.split("&") if query else []
    return recipients, query_parts


def _strict_decode(value: str) -> str:
    return unquote(value, encoding="utf-8", errors="strict")


def _check_addresses(raw: str, source: str) -> List[str]:
    problems = []
    for address in _strict_decode(raw).split(","):
        address = address.strip()
        if not address:
            problems.append(f"empty email address in {source}")
            continue
        try:
            _email_validator(address)
        except ValidationError:
            problems.append(f"invalid email address in {source}")
    return problems


def mailto_problems(url: str) -> List[str]:
    """Explain why a mailto: link isn't well-formed. An empty list means it is.

    Deliberately conservative: anything unusual is left for a human to check
    via an Issue rather than being proposed for the allowlist.
    """
    if not is_mailto(url):
        return ["not a mailto: link"]

    if _DISALLOWED_RAW_CHARS.search(url):
        return ["contains whitespace or characters that should be percent-encoded"]

    if _MALFORMED_PERCENT_ESCAPE.search(url):
        return ["contains a malformed percent-escape"]

    recipients, query_parts = _split_mailto(url)
    problems = []

    if "#" in url:
        problems.append("contains a raw '#' (fragments are meaningless in mailto:)")
    if any(_POSSIBLE_MENTION.search(part) for part in query_parts):
        # The raw URL is shown unescaped in the allowlist PR, where this
        # could become a GitHub @-mention. Encoded as %40 is fine.
        problems.append("contains a raw '@' that isn't part of an email address")

    try:
        if recipients:
            problems.extend(_check_addresses(recipients, "recipient list"))

        seen_headers = set()
        for part in query_parts:
            raw_key, sep, raw_value = part.partition("=")
            key = _strict_decode(raw_key).lower()
            if not sep:
                problems.append("query part without a value")
                continue
            if key not in ALLOWED_HEADERS:
                problems.append("unsupported mailto header")
                continue
            if key in seen_headers:
                problems.append(f"duplicate '{key}' header")
                continue
            seen_headers.add(key)

            if key in ADDRESS_HEADERS:
                problems.extend(_check_addresses(raw_value, f"'{key}' header"))
            elif any(_is_suspicious_char(c) for c in _strict_decode(raw_value)):
                problems.append(
                    f"'{key}' contains control, bidi-override or invisible characters"
                )
    except UnicodeDecodeError:
        return ["percent-encoded text is not valid UTF-8"]

    if not recipients and not query_parts:
        problems.append("no recipient, subject or body")

    return problems


def is_valid_mailto(url: str) -> bool:
    return is_mailto(url) and not mailto_problems(url)


def decode_mailto_fields(url: str) -> List[Tuple[str, str]]:
    """Leniently decode a mailto: link's query fields into (name, text) pairs.

    Lenient because this is also used for display in Issues about mailto:
    links that failed validation. Nothing returned here is safe to render
    as-is - see render_untrusted_text().
    """
    if not is_mailto(url):
        return []
    _, query_parts = _split_mailto(url)
    fields = []
    for part in query_parts:
        raw_key, _, raw_value = part.partition("=")
        key = unquote(raw_key, errors="replace").lower()
        # NB: unlike form-encoding, '+' is a literal plus in mailto: (RFC 6068)
        fields.append((key, unquote(raw_value, errors="replace")))
    return fields


# GitHub's @-mention, #-reference and email-autolink filters ignore backslash
# escapes but don't run inside code spans, so these get wrapped in one instead
_CODE_SPAN_CHARS = {"@": "`@`", "#": "`#`"}


def _escape_markdown_line(line: str) -> str:
    """Neutralise every ASCII punctuation character.

    CommonMark lets any ASCII punctuation be backslash-escaped, and doing so
    for all of it means none of the text can be interpreted as Markdown,
    raw HTML, a link or an autolink. '@' and '#' are special-cased so they
    can't trigger GitHub @-mentions (notifying people) or #-references.
    """
    return "".join(
        _CODE_SPAN_CHARS.get(char, f"\\{char}" if char in string.punctuation else char)
        for char in line
    )


def _make_suspicious_chars_visible(text: str) -> str:
    return "".join(
        f"[U+{ord(char):04X}]" if _is_suspicious_char(char) else char for char in text
    )


def render_untrusted_text(text: str, indent: str = "") -> str:
    """Render untrusted text as an inert Markdown blockquote.

    Line breaks are preserved, invisible/control characters are made
    visible, and the rendered result stays as plain prose so that
    in-browser translation (eg Firefox Translations) still works on it.
    """
    text = _LINE_BREAKS.sub("\n", text).replace("\t", " ")
    if len(text) > MAX_DISPLAY_FIELD_LENGTH:
        text = text[:MAX_DISPLAY_FIELD_LENGTH] + " [...truncated]"
    text = _make_suspicious_chars_visible(text)

    lines = [_escape_markdown_line(line.strip()) for line in text.split("\n")]
    output = []
    for i, line in enumerate(lines):
        next_line_has_content = i + 1 < len(lines) and lines[i + 1]
        hard_break = "\\" if line and next_line_has_content else ""
        output.append(f"{indent}> {line}{hard_break}".rstrip())
    return "\n".join(output)


def infer_languages(page_paths: Iterable[str]) -> List[str]:
    """Guess the language(s) of a link's text from the locale prefix of
    the page(s) it was found on, eg /de/firefox/ -> 'German (de)'"""
    languages = set()
    for path in page_paths:
        segments = [s for s in path.split("/") if s]
        if not segments or not _LOCALE_SEGMENT.match(segments[0]):
            continue
        locale = segments[0]
        info = LANG_INFO.get(locale.lower()) or LANG_INFO.get(
            locale.split("-")[0].lower()
        )
        name = info.get("name") if info else None
        languages.add(f"{name} ({locale})" if name else locale)
    return sorted(languages)


def render_mailto_details(
    url: str,
    page_paths: Iterable[str],
    indent: str = "",
    include_problems: bool = False,
) -> str:
    """Markdown describing a mailto: link's decoded subject/body etc, or ''
    if there's nothing worth decoding."""
    if not is_mailto(url):
        return ""

    blocks = []

    if include_problems:
        problems = mailto_problems(url)
        if problems:
            blocks.append(f"{indent}Not auto-allowlisted because the mailto: link:")
            blocks.append(
                "\n".join(f"{indent}* {_escape_markdown_line(p)}" for p in problems)
            )

    fields = decode_mailto_fields(url)
    if fields:
        languages = infer_languages(page_paths)
        language_note = (
            f"page locale suggests: {_escape_markdown_line(', '.join(languages))}"
            if languages
            else "language could not be inferred from the page locale"
        )
        blocks.append(
            f"{indent}**Decoded mailto: content** "
            f"(untrusted text from the page; {language_note}):"
        )
        for key, value in fields:
            blocks.append(f"{indent}**{_escape_markdown_line(key)}**")
            blocks.append(render_untrusted_text(value, indent=indent))

    return "\n\n".join(blocks)

"""Remove authentication material before formatting or clipping display data."""

import re


_AUTHORIZATION_NAMES = frozenset({"authorization", "proxy-authorization"})
_REPLACEMENT = "***"
_SCHEME = re.compile(r"(?:Bearer|Basic|Digest|Negotiate|NTLM|DPoP|Token)[ \t]+", re.IGNORECASE)
_REFERENCE = re.compile(r"\$\{(?:env:)?[A-Za-z_]\w*\}|\{[A-Za-z_]\w*\}")
_QUOTED_HEADER = re.compile(
    r"""(?:(?<!\\)(?P<key_escape>\\{0,7})(?P<key_quote>["'])(?:Proxy-)?Authorization"""
    r"""(?P=key_escape)(?P=key_quote)\s*:\s*|(?<![\w.-])(?:Proxy-)?Authorization[ \t]*:[ \t]*)"""
    r"""(?P<escape>\\{0,7})(?P<quote>["'])""",
    re.IGNORECASE,
)
_QUOTED_END = re.compile(r"""(?<!\\)(?P<escape>\\*+)(?P<quote>["'])|[\r\n]""")
_HEADER = re.compile(
    r"""(?P<header>(?<![\w.-])(?:Proxy-)?Authorization[ \t]*:[ \t]*)"""
    r"""(?P<value>(?!["'])(?:[A-Za-z][\w.+-]*[ \t]+)?[^\s"'<>]+)""",
    re.IGNORECASE,
)
# An unlabelled "bearer of ..." is prose. Without an explicit header, require
# either an opaque token-sized run or the shape of an earlier display mask.
_BARE_BEARER = re.compile(
    r"(?P<scheme>\bBearer[ \t]+)(?:"
    r"[A-Za-z0-9_~+/-]{6}(?:\.{3}|…)[A-Za-z0-9_~+/-]{4}(?![A-Za-z0-9_~+/-])"
    r"|[A-Za-z0-9._~+/-]{20,}=*)",
    re.IGNORECASE,
)


def _mask_header_value(value: str) -> str:
    scheme = _SCHEME.match(value)
    start = scheme.end() if scheme else 0
    credential = value[start:]
    if not credential or _REFERENCE.fullmatch(credential):
        return value
    return value[:start] + _REPLACEMENT


def _redact_quoted_headers(text: str) -> str:
    parts = []
    cursor = 0
    while header := _QUOTED_HEADER.search(text, cursor):
        start = header.end()
        escape = len(header["escape"])
        end = len(text)
        for boundary in _QUOTED_END.finditer(text, start):
            if boundary["quote"] is None:
                end = boundary.start()
                break
            if boundary["quote"] != header["quote"]:
                continue
            # After k JSON string encodings a delimiter has 2**k - 1
            # backslashes. An escaped inner quote adds an odd number of
            # 2**k runs. An even number belongs to the preceding value.
            extra = len(boundary["escape"]) - escape
            if extra >= 0 and extra % (2 * (escape + 1)) == 0:
                end = boundary.end() - 1 - escape
                break
        parts.extend((text[cursor:start], _mask_header_value(text[start:end])))
        cursor = end
    return "".join((*parts, text[cursor:])) if parts else text


def redact_authorization_text(text: str) -> str:
    """Remove complete Authorization values and recognizable Bearer material."""
    if not text:
        return text
    lower = text.lower()
    if "authorization" in lower:
        text = _redact_quoted_headers(text)
        text = _HEADER.sub(lambda match: match["header"] + _mask_header_value(match["value"]), text)
    if "bearer" in lower:
        text = _BARE_BEARER.sub(lambda match: match["scheme"] + _REPLACEMENT, text)
    return text


def redact_authorization_payload(value, *, _depth: int = 0):
    """Scrub model-facing text and nested JSON while retaining value and key types."""
    if isinstance(value, str):
        return redact_authorization_text(value)
    if not isinstance(value, (dict, list)):
        return value
    if _depth >= 320:
        return "[redacted: nesting limit]"
    if isinstance(value, list):
        return [redact_authorization_payload(item, _depth=_depth + 1) for item in value]
    return {
        key: (
            _mask_header_value(item)
            if isinstance(item, str) and isinstance(key, str) and key.lower() in _AUTHORIZATION_NAMES
            else redact_authorization_payload(item, _depth=_depth + 1)
        )
        for key, item in value.items()
    }

"""Tests for llms.adapters._content — shared multimodal helpers."""

import pytest

from omnigent.llms.adapters._content import (
    parse_data_uri,
    redact_binary_payloads,
    redact_inline_data_uris,
)


def test_parse_data_uri_png() -> None:
    """Standard image/png data URI parses correctly."""
    result = parse_data_uri("data:image/png;base64,iVBORw0KGgo=")
    assert result is not None
    assert result.media_type == "image/png"
    assert result.data == "iVBORw0KGgo="


def test_parse_data_uri_pdf() -> None:
    """Application/pdf data URI parses correctly."""
    result = parse_data_uri("data:application/pdf;base64,JVBERi0=")
    assert result is not None
    assert result.media_type == "application/pdf"
    assert result.data == "JVBERi0="


def test_parse_data_uri_returns_none_for_https() -> None:
    """External HTTPS URLs return None — not a data URI."""
    assert parse_data_uri("https://example.com/photo.png") is None


def test_parse_data_uri_returns_none_for_http() -> None:
    """External HTTP URLs return None — not a data URI."""
    assert parse_data_uri("http://example.com/photo.png") is None


def test_parse_data_uri_returns_none_for_missing_base64() -> None:
    """
    Data URI without ;base64, separator returns None —
    we only support base64-encoded data URIs.
    """
    assert parse_data_uri("data:text/plain,Hello") is None


@pytest.mark.parametrize(
    ("uri", "expected_type", "expected_data"),
    [
        pytest.param(
            "data:image/jpeg;base64,/9j/4AAQ",
            "image/jpeg",
            "/9j/4AAQ",
            id="jpeg",
        ),
        pytest.param(
            "data:image/webp;base64,UklGR",
            "image/webp",
            "UklGR",
            id="webp",
        ),
        pytest.param(
            "data:audio/wav;base64,UklF",
            "audio/wav",
            "UklF",
            id="audio",
        ),
    ],
)
def test_parse_data_uri_various_types(
    uri: str,
    expected_type: str,
    expected_data: str,
) -> None:
    """Various media types parse correctly."""
    result = parse_data_uri(uri)
    assert result is not None
    assert result.media_type == expected_type
    assert result.data == expected_data


@pytest.mark.parametrize(
    ("value", "expected_marker"),
    [
        pytest.param(
            "data:image/png;charset=binary;base64,QUJD",
            "[image/png:4]",
            id="mime-parameters",
        ),
        pytest.param("data:;base64,QUJD", "[:4]", id="empty-mime"),
        pytest.param("DATA:image/png;BASE64,QUJD", "[image/png:4]", id="uppercase"),
        pytest.param("data:image/png;base64, QUJD", "[image/png:4]", id="space"),
    ],
)
def test_redact_inline_data_uris_accepts_common_variants(
    value: str,
    expected_marker: str,
) -> None:
    """Inline data URI redaction handles safe real-world syntax variants."""
    assert redact_inline_data_uris(value, lambda media_type, size: f"[{media_type}:{size}]") == (
        expected_marker
    )


def test_redact_inline_data_uris_preserves_following_prose() -> None:
    """Redaction stops at the base64 token instead of consuming later prose."""
    value = "preview data:image/png;base64,AAAA then describe the screenshot"

    assert redact_inline_data_uris(value, lambda media_type, size: f"[{media_type}:{size}]") == (
        "preview [image/png:4] then describe the screenshot"
    )


def test_redact_binary_payloads_clears_nested_provider_native_images() -> None:
    """Raw base64 is cleared inside a source wrapper nested in a tool_result.

    This is the shape ``claude-native`` emits for a pasted screenshot: the
    payload is bare base64 under ``source.data`` (not a ``data:`` URI, so
    :func:`redact_inline_data_uris` cannot see it) and it sits two levels deep
    inside a ``tool_result`` block.
    """
    messages = [
        {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_screenshot_01",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "iVBORw0KGgo" + "A" * 4000,
                            },
                        }
                    ],
                }
            ],
        }
    ]

    redacted = redact_binary_payloads(messages, "[cleared]")

    source = redacted[0]["content"][0]["content"][0]["source"]
    assert source["data"] == "[cleared]"
    # Sibling metadata survives so the block still describes what was removed.
    assert source["media_type"] == "image/png"
    assert redacted[0]["content"][0]["tool_use_id"] == "toolu_screenshot_01"
    # The original is untouched — callers may still need the real payload.
    assert messages[0]["content"][0]["content"][0]["source"]["data"].startswith("iVBORw0KGgo")


def test_redact_binary_payloads_leaves_unrelated_data_keys_alone() -> None:
    """Only binary blocks are rewritten; a plain ``data`` field is preserved.

    Blanket ``data``-key replacement would corrupt tool arguments and results
    that legitimately use that key name.
    """
    value = {
        "type": "function_call",
        "name": "query_rows",
        "data": {"rows": 12, "table": "users"},
        "content": [{"type": "text", "data": "not a payload"}],
    }

    assert redact_binary_payloads(value, "[cleared]") == value

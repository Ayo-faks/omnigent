"""Versioned JSON-lines protocol for runner extension subprocesses."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

RUNNER_EXTENSION_PROTOCOL_VERSION = 1
MAX_RUNNER_EXTENSION_FRAME_BYTES = 1024 * 1024


class RunnerExtensionProtocolError(ValueError):
    """A runner extension frame is malformed or incompatible."""


@dataclass(frozen=True)
class RunnerRequest:
    request_id: str
    generation: str
    method: str
    params: dict[str, Any]
    version: int = RUNNER_EXTENSION_PROTOCOL_VERSION


@dataclass(frozen=True)
class RunnerResponse:
    request_id: str
    generation: str
    result: Any = None
    error: dict[str, str] | None = None
    version: int = RUNNER_EXTENSION_PROTOCOL_VERSION


def encode_frame(value: RunnerRequest | RunnerResponse) -> bytes:
    """Encode one bounded protocol frame including its line terminator."""
    payload = json.dumps(value.__dict__, separators=(",", ":"), ensure_ascii=False).encode()
    if len(payload) > MAX_RUNNER_EXTENSION_FRAME_BYTES:
        raise RunnerExtensionProtocolError("runner extension frame exceeds 1 MB")
    return payload + b"\n"


def decode_request(line: bytes, *, allow_version_mismatch: bool = False) -> RunnerRequest:
    """Decode and validate one host-to-worker request."""
    raw = _decode_object(line)
    _validate_common(raw, check_version=not allow_version_mismatch)
    method = raw.get("method")
    params = raw.get("params")
    if not isinstance(method, str) or not isinstance(params, dict):
        raise RunnerExtensionProtocolError("runner extension request is malformed")
    return RunnerRequest(
        request_id=raw["request_id"],
        generation=raw["generation"],
        method=method,
        params=params,
        version=raw["version"],
    )


def decode_response(line: bytes) -> RunnerResponse:
    """Decode and validate one worker-to-host response."""
    raw = _decode_object(line)
    _validate_common(raw, check_version=True)
    error = raw.get("error")
    if error is not None and (
        not isinstance(error, dict)
        or not isinstance(error.get("code"), str)
        or not isinstance(error.get("message"), str)
    ):
        raise RunnerExtensionProtocolError("runner extension error envelope is malformed")
    return RunnerResponse(
        request_id=raw["request_id"],
        generation=raw["generation"],
        result=raw.get("result"),
        error=error,
        version=raw["version"],
    )


def _decode_object(line: bytes) -> dict[str, Any]:
    if len(line) > MAX_RUNNER_EXTENSION_FRAME_BYTES + 1:
        raise RunnerExtensionProtocolError("runner extension frame exceeds 1 MB")
    try:
        raw = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RunnerExtensionProtocolError("runner extension frame is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise RunnerExtensionProtocolError("runner extension frame must be an object")
    return raw


def _validate_common(raw: dict[str, Any], *, check_version: bool) -> None:
    if check_version and raw.get("version") != RUNNER_EXTENSION_PROTOCOL_VERSION:
        raise RunnerExtensionProtocolError(
            f"unsupported runner extension protocol version {raw.get('version')!r}"
        )
    if not isinstance(raw.get("version"), int):
        raise RunnerExtensionProtocolError("runner extension protocol version is malformed")
    if not isinstance(raw.get("request_id"), str) or not isinstance(raw.get("generation"), str):
        raise RunnerExtensionProtocolError("runner extension frame identity is malformed")

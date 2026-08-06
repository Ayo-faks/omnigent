"""Codex model catalog for the gateway servlet.

Translates the workspace's Unity Catalog model-services inventory into the
``ModelsResponse`` shape the Codex CLI consumes from a provider ``/models``
endpoint. Pipeline: filter services by declared API surface, convert
``system.ai.*`` ids to the bare slug Codex has native metadata for, then
dress each slug in Codex's own catalog metadata (injected by the host via a
``codex debug models`` probe) so picker rows, effort ladders, and base
instructions stay native.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

import httpx

# Codex speaks the Responses API; only services declaring this surface are
# servable through the codex gateway path (name-matching would wrongly admit
# chat-only models like gpt-oss-*).
CODEX_API_TYPE = "openai/v1/responses"

_MODEL_SERVICE_PREFIX = "model-services/"
_SYSTEM_PREFIX = "system.ai."
# Mainline GPT service ids (``gpt-<major>[-<minor>][-<suffix>]``) convert to
# the dotted OpenAI slug (``gpt-5.6-sol``); anything else stays verbatim —
# the gateway resolves both spellings, but only bare slugs carry native
# Codex metadata.
_MAINLINE_SERVICE_RE = re.compile(r"gpt-(\d+)(?:-(\d+))?(-[a-z][a-z0-9-]*)?$")
_MAINLINE_SLUG_RE = re.compile(r"gpt-(\d+)(?:\.(\d+))?(-[a-z][a-z0-9.-]*)?$")

_PAGE_SIZE = 100
_MAX_PAGES = 20

# Efforts allowed on entries synthesized for slugs Codex has no native
# metadata for; keeps unknown models off effort levels the upstream rejects.
_SYNTHETIC_EFFORTS = ("low", "medium", "high")


async def fetch_codex_service_ids(
    client: httpx.AsyncClient,
    workspace_host: str,
    bearer: str,
) -> list[str]:
    """
    List ``system.ai.*`` ids the workspace serves on Codex's API surface.

    :param client: Shared async HTTP client.
    :param workspace_host: Workspace origin, e.g. ``"https://x.databricks.com"``.
    :param bearer: Databricks access token.
    :returns: Sorted, de-duplicated service ids (without the
        ``model-services/`` resource prefix).
    :raises httpx.HTTPError: On listing failures (callers treat the catalog
        as unavailable).
    """
    ids: list[str] = []
    page_token: str | None = None
    for _ in range(_MAX_PAGES):
        params: dict[str, str] = {"page_size": str(_PAGE_SIZE)}
        if page_token:
            params["page_token"] = page_token
        resp = await client.get(
            f"{workspace_host}/api/2.1/unity-catalog/model-services",
            params=params,
            headers={"Authorization": f"Bearer {bearer}"},
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            break
        for svc in payload.get("model_services", []):
            if not isinstance(svc, dict):
                continue
            api_types = svc.get("supported_api_types")
            if not isinstance(api_types, list) or CODEX_API_TYPE not in api_types:
                continue
            name = svc.get("name")
            if isinstance(name, str) and name:
                ids.append(name.removeprefix(_MODEL_SERVICE_PREFIX))
        page_token = payload.get("next_page_token") or None
        if not page_token:
            break
    return sorted(set(ids))


def codex_slug(service_id: str) -> str:
    """
    Convert one service id to the slug Codex should see.

    :param service_id: e.g. ``"system.ai.gpt-5-6-sol"``.
    :returns: ``"gpt-5.6-sol"`` for mainline GPT ids; the id verbatim
        otherwise.
    """
    tail = service_id.removeprefix(_SYSTEM_PREFIX)
    match = _MAINLINE_SERVICE_RE.fullmatch(tail)
    if match is None:
        return service_id
    major, minor, suffix = match.groups()
    version = major if minor is None else f"{major}.{minor}"
    return f"gpt-{version}{suffix or ''}"


def _slug_sort_key(slug: str) -> tuple[int, int, int, str]:
    """Order mainline GPT slugs newest-first; non-mainline ids last, alpha."""
    match = _MAINLINE_SLUG_RE.fullmatch(slug)
    if match is None:
        return (1, 0, 0, slug)
    major, minor, suffix = match.groups()
    return (0, -int(major), -(int(minor) if minor else 0), suffix or "")


def build_models_response(
    service_ids: list[str],
    native_catalog: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Build Codex's ``ModelsResponse`` for the servable inventory.

    Known slugs adopt Codex's own catalog entry (native display names,
    descriptions, effort ladders, base instructions); unknown slugs clone the
    first native entry as a template with a clamped effort ladder. Ordering:
    native (OpenAI-priority) order first so the default matches what Codex
    would pick natively when servable, then remaining slugs newest-first.

    :param service_ids: Servable ``system.ai.*`` ids.
    :param native_catalog: ``codex debug models`` output, or ``None``.
    :returns: ``{"models": [...]}`` ready to serve, or ``None`` when nothing
        can be built (no native metadata / empty inventory) — callers fail
        open so Codex keeps its bundled catalog.
    """
    if not isinstance(native_catalog, dict):
        return None
    native_models = [
        m
        for m in native_catalog.get("models", [])
        if isinstance(m, dict) and isinstance(m.get("slug"), str) and m.get("slug")
    ]
    if not native_models:
        return None
    service_by_slug: dict[str, str] = {}
    for service_id in service_ids:
        service_by_slug.setdefault(codex_slug(service_id), service_id)
    if not service_by_slug:
        return None
    native_by_slug = {m["slug"]: m for m in native_models}
    ordered: list[str] = [m["slug"] for m in native_models if m["slug"] in service_by_slug]
    ordered.extend(
        sorted((s for s in service_by_slug if s not in set(ordered)), key=_slug_sort_key)
    )
    template = native_models[0]
    models: list[dict[str, Any]] = []
    for priority, slug in enumerate(ordered):
        native = native_by_slug.get(slug)
        entry = copy.deepcopy(native if native is not None else template)
        entry["slug"] = slug
        entry["priority"] = priority
        entry["visibility"] = "list"
        if native is None:
            entry["display_name"] = slug
            entry["description"] = f"Served by the workspace AI Gateway ({service_by_slug[slug]})"
            entry["supported_reasoning_levels"] = [
                level
                for level in template.get("supported_reasoning_levels", [])
                if isinstance(level, dict) and level.get("effort") in _SYNTHETIC_EFFORTS
            ]
            entry["default_reasoning_level"] = "medium"
            entry["availability_nux"] = None
            entry["upgrade"] = None
        models.append(entry)
    return {"models": models}


def catalog_etag(payload: bytes) -> str:
    """
    Strong ETag for a serialized catalog.

    :param payload: Serialized ``ModelsResponse`` bytes.
    :returns: Quoted ETag value.
    """
    return '"' + hashlib.sha256(payload).hexdigest()[:32] + '"'


def picker_options(models_response: dict[str, Any]) -> list[dict[str, object]]:
    """
    Web-picker rows for a built catalog (first entry is the default).

    :param models_response: Output of :func:`build_models_response`.
    :returns: ``[{"id", "displayName", "isDefault"?}, ...]``.
    """
    options: list[dict[str, object]] = []
    for index, entry in enumerate(models_response.get("models", [])):
        slug = entry.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        display = entry.get("display_name")
        options.append(
            {
                "id": slug,
                "displayName": display if isinstance(display, str) and display else slug,
                **({"isDefault": True} if index == 0 else {}),
            }
        )
    return options


def routable_models(models_response: dict[str, Any]) -> list[str]:
    """
    Every slug the catalog serves (router-launchable even without a row).

    :param models_response: Output of :func:`build_models_response`.
    :returns: Slug list in catalog order.
    """
    return [
        entry["slug"]
        for entry in models_response.get("models", [])
        if isinstance(entry.get("slug"), str)
    ]


def dumps_catalog(models_response: dict[str, Any]) -> bytes:
    """
    Serialize a catalog deterministically (stable ETags across rebuilds).

    :param models_response: Output of :func:`build_models_response`.
    :returns: UTF-8 JSON bytes.
    """
    return json.dumps(models_response, sort_keys=True).encode("utf-8")

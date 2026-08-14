"""Tests for the announcements routes (``/v1/announcements``).

Two auth setups, mirroring the projects/sharing route tests:

- **Single-user** (``client``) — no auth provider / permission store, so the
  admin gate is skipped. Covers the read/write happy path and validation.
- **Multi-user** (``admin_client`` + ``X-Forwarded-Email``) — header auth with a
  permission store, so the editor endpoints are admin-gated. Proves a non-admin
  gets 403 while the public GET stays open.

The announcements file lives under ``resolve_data_dir()``; every test redirects
that at ``tmp_path`` (via ``OMNIGENT_ADMIN_CREDENTIALS_PATH``) so nothing touches
the real ``~/.omnigent``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from omnigent.runtime.agent_cache import AgentCache
from omnigent.server import announcements_settings
from omnigent.server.app import create_app
from omnigent.server.auth import UnifiedAuthProvider
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.permission_store.sqlalchemy_store import (
    SqlAlchemyPermissionStore,
)

ADMIN = "admin@example.com"
MEMBER = "member@example.com"


@pytest.fixture(autouse=True)
def announce_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point ``resolve_data_dir()`` at ``tmp_path`` and clear the read cache.

    Keeps every test's announcements file isolated in its own temp dir and never
    writes to the developer's real data directory.
    """
    monkeypatch.setenv(
        "OMNIGENT_ADMIN_CREDENTIALS_PATH", str(tmp_path / "data" / "admin-credentials")
    )
    monkeypatch.delenv("OMNIGENT_ADMIN_LIST_PATH", raising=False)
    announcements_settings._cache.clear()
    yield
    announcements_settings._cache.clear()


def _base_app(db_uri: str, tmp_path: Path, **overrides: object) -> FastAPI:
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache"),
        **overrides,  # type: ignore[arg-type]
    )


@pytest.fixture()
def single_user_app(runtime_init: None, db_uri: str, tmp_path: Path) -> FastAPI:
    """App with no auth — the admin gate is skipped (OSS single-user default)."""
    return _base_app(db_uri, tmp_path)


@pytest_asyncio.fixture()
async def client(single_user_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=single_user_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture()
def multi_user_app(runtime_init: None, db_uri: str, tmp_path: Path) -> FastAPI:
    """Header-auth app with a permission store, so the editor is admin-gated."""
    permission_store = SqlAlchemyPermissionStore(db_uri)
    permission_store.ensure_user(ADMIN, is_admin=True)
    permission_store.ensure_user(MEMBER, is_admin=False)
    return _base_app(
        db_uri,
        tmp_path,
        permission_store=permission_store,
        auth_provider=UnifiedAuthProvider(source="header", local_single_user=False),
    )


@pytest_asyncio.fixture()
async def admin_client(multi_user_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=multi_user_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _payload(**overrides: object) -> dict:
    base: dict = {"message": "Scheduled maintenance tonight", "level": "warning"}
    base.update(overrides)  # type: ignore[arg-type]
    return base


# ── Single-user: read/write happy path + validation ───────────────────


async def test_empty_by_default(client: httpx.AsyncClient) -> None:
    resp = await client.get("/v1/announcements")
    assert resp.status_code == 200
    assert resp.json() == {"object": "list", "data": []}


async def test_put_then_get_assigns_id_and_timestamps(client: httpx.AsyncClient) -> None:
    resp = await client.put("/v1/announcements", json={"announcements": [_payload()]})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    row = data[0]
    assert row["id"].startswith("anc_")
    assert row["message"] == "Scheduled maintenance tonight"
    assert row["level"] == "warning"
    assert row["active"] is True
    assert row["dismissible"] is True
    assert isinstance(row["created_at"], int)

    # It now shows on the public active list.
    active = (await client.get("/v1/announcements")).json()["data"]
    assert [a["id"] for a in active] == [row["id"]]


async def test_inactive_hidden_from_public_but_in_all(client: httpx.AsyncClient) -> None:
    body = {
        "announcements": [
            _payload(message="Live one", active=True),
            _payload(message="Hidden one", active=False),
        ]
    }
    await client.put("/v1/announcements", json=body)

    active = (await client.get("/v1/announcements")).json()["data"]
    assert [a["message"] for a in active] == ["Live one"]

    all_rows = (await client.get("/v1/announcements/all")).json()["data"]
    assert {a["message"] for a in all_rows} == {"Live one", "Hidden one"}


async def test_edit_preserves_created_at(client: httpx.AsyncClient) -> None:
    first = (await client.put("/v1/announcements", json={"announcements": [_payload()]})).json()[
        "data"
    ][0]

    edited = {
        "announcements": [
            {"id": first["id"], "message": "Edited text", "level": "info", "active": True}
        ]
    }
    row = (await client.put("/v1/announcements", json=edited)).json()["data"][0]
    assert row["id"] == first["id"]
    assert row["message"] == "Edited text"
    assert row["created_at"] == first["created_at"]
    assert row["updated_at"] >= first["updated_at"]


async def test_put_replaces_whole_list(client: httpx.AsyncClient) -> None:
    await client.put(
        "/v1/announcements",
        json={"announcements": [_payload(message="A"), _payload(message="B")]},
    )
    # A subsequent PUT with a single row drops the others.
    row = (
        await client.put("/v1/announcements", json={"announcements": [_payload(message="C")]})
    ).json()["data"]
    assert [a["message"] for a in row] == ["C"]


async def test_empty_message_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.put("/v1/announcements", json={"announcements": [{"message": "  "}]})
    # Pydantic min_length is on the trimmed-then-stored value; a whitespace-only
    # message has length after strip == 0 and is rejected as a bad request.
    assert resp.status_code in (400, 422)


async def test_unknown_level_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.put(
        "/v1/announcements",
        json={"announcements": [_payload(level="critical")]},
    )
    assert resp.status_code == 400


async def test_unsafe_link_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.put(
        "/v1/announcements",
        json={"announcements": [_payload(link_url="javascript:alert(1)")]},
    )
    assert resp.status_code == 400


async def test_relative_and_https_links_accepted(client: httpx.AsyncClient) -> None:
    body = {
        "announcements": [
            _payload(message="rel", link_url="/docs", link_label="Docs"),
            _payload(message="abs", link_url="https://example.com"),
        ]
    }
    data = (await client.put("/v1/announcements", json=body)).json()["data"]
    by_msg = {a["message"]: a for a in data}
    assert by_msg["rel"]["link_url"] == "/docs"
    assert by_msg["rel"]["link_label"] == "Docs"
    # A link with no label stores label as None (banner uses the URL as text).
    assert by_msg["abs"]["link_url"] == "https://example.com"
    assert by_msg["abs"]["link_label"] is None


async def test_too_many_rejected(client: httpx.AsyncClient) -> None:
    over = announcements_settings.MAX_ANNOUNCEMENTS + 1
    body = {"announcements": [_payload(message=f"n{i}") for i in range(over)]}
    resp = await client.put("/v1/announcements", json=body)
    assert resp.status_code == 400


# ── Multi-user: admin gating ───────────────────────────────────────────


async def test_public_get_open_to_anyone(admin_client: httpx.AsyncClient) -> None:
    # No identity header at all — the active list is still readable.
    resp = await admin_client.get("/v1/announcements")
    assert resp.status_code == 200


async def test_non_admin_cannot_edit(admin_client: httpx.AsyncClient) -> None:
    resp = await admin_client.put(
        "/v1/announcements",
        json={"announcements": [_payload()]},
        headers={"X-Forwarded-Email": MEMBER},
    )
    assert resp.status_code == 403

    # And can't read the admin-only "all" list.
    resp = await admin_client.get(
        "/v1/announcements/all", headers={"X-Forwarded-Email": MEMBER}
    )
    assert resp.status_code == 403


async def test_admin_can_edit(admin_client: httpx.AsyncClient) -> None:
    resp = await admin_client.put(
        "/v1/announcements",
        json={"announcements": [_payload()]},
        headers={"X-Forwarded-Email": ADMIN},
    )
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1

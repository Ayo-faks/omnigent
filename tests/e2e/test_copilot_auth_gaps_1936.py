"""Regression e2e for issue #1936 — copilot harness auth gaps.

The bug report is a compound bug with two independent sub-symptoms; each gets
its own test so a partially-landed fix can't hide the still-broken half.

Facet 1 — CLI login not honoured
    A user logged in via ``gh auth login`` with NO ``COPILOT_GITHUB_TOKEN`` /
    ``GH_TOKEN`` / ``GITHUB_TOKEN`` in the environment gets a 401. Root cause:
    :class:`~omnigent.inner.copilot_executor.CopilotExecutor` constructs the SDK
    ``CopilotClient`` without ``use_logged_in_user=True`` and has no ``gh`` CLI
    fallback, so ``github_token`` resolves to ``None`` and the SDK rejects the
    empty credential. ``omnigent setup`` -> Copilot also offers no
    "Login via GitHub CLI" path.

Facet 2 — no GitHub Enterprise host configuration
    There is no ``copilot.github_host`` config field, no ``omnigent setup``
    prompt for a GHE hostname, and the host is never forwarded to the SDK, so an
    org whose Copilot lives on a GHE instance (e.g. ``shs.ghe.com``) cannot be
    targeted.

These drive the REAL executor client construction and the REAL ``copilot_auth``
config surface against a spy ``copilot`` SDK module (no network, no bundled
CLI), so they are deterministic and unskipped. They FAIL on the pre-fix build
(no ``use_logged_in_user``, no ``resolve_copilot_github_host``) and PASS once the
harness honours the gh-CLI login and forwards a configured GHE host.

Run::

    pytest tests/e2e/test_copilot_auth_gaps_1936.py -v
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import pytest

# Ambient GitHub-token env vars the executor consults; cleared per-test so we
# faithfully simulate "logged in via gh CLI, but no token exported".
_TOKEN_ENV_VARS = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")


def _install_spy_copilot(monkeypatch: pytest.MonkeyPatch, capture: dict[str, Any]) -> None:
    """Install a spy ``copilot`` SDK module that records ``CopilotClient`` kwargs.

    ``start()`` mimics the real SDK: with no ``github_token`` AND no
    ``use_logged_in_user`` opt-in, credential validation fails with the exact
    401 the report shows. Once either is supplied, ``start()`` succeeds.
    """
    module = types.ModuleType("copilot")

    class _SpySession: ...

    class _SpyClient:
        def __init__(self, **kwargs: Any) -> None:
            capture["client_kwargs"] = kwargs

        async def start(self) -> None:
            kw = capture["client_kwargs"]
            if not kw.get("github_token") and not kw.get("use_logged_in_user"):
                raise RuntimeError(
                    "JSON-RPC Error -32603: Request session.create failed with "
                    "message: Authentication failed: Failed to validate SDK "
                    "token (401): GitHub returned: Bad credentials"
                )

        async def stop(self) -> None: ...

        async def create_session(self, **kwargs: Any) -> _SpySession:
            capture["create_kwargs"] = kwargs
            return _SpySession()

    class _Tool:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    class _ToolResult:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    module.CopilotClient = _SpyClient  # type: ignore[attr-defined]
    module.Tool = _Tool  # type: ignore[attr-defined]
    module.ToolResult = _ToolResult  # type: ignore[attr-defined]
    module.PermissionHandler = types.SimpleNamespace(approve_all="approve_all")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "copilot", module)

    rpc = types.ModuleType("copilot.rpc")
    rpc.PermissionDecisionApproveOnce = type("A", (), {"kind": "approve-once"})  # type: ignore[attr-defined]
    rpc.PermissionDecisionReject = type(  # type: ignore[attr-defined]
        "R", (), {"__init__": lambda self, feedback=None: None}
    )
    monkeypatch.setitem(sys.modules, "copilot.rpc", rpc)


def _build_client_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Drive the real executor bring-up; return (captured, start_error)."""
    for var in _TOKEN_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    capture: dict[str, Any] = {}
    _install_spy_copilot(monkeypatch, capture)

    import omnigent.inner.copilot_executor as ce

    executor = ce.CopilotExecutor()  # no token: relies on the gh CLI login
    state = ce._CopilotSessionState()

    async def _drive() -> Exception | None:
        try:
            await executor._ensure_session(state, model=None, tools=[], system_prompt="")
        except Exception as exc:
            return exc
        return None

    error = asyncio.run(_drive())
    capture["start_error"] = error
    capture["resolved_token"] = executor._github_token
    return capture


def test_copilot_honours_gh_cli_login_without_token_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Facet 1: gh-CLI login must authenticate the harness without a token env var."""
    capture = _build_client_kwargs(monkeypatch)

    # Precondition sanity: with no env token the executor resolves None.
    assert capture["resolved_token"] is None

    # The harness must opt into the SDK's logged-in-user auth so a gh-CLI login
    # with no token env var authenticates instead of 401-ing (Bad credentials).
    assert capture["client_kwargs"].get("use_logged_in_user") is True
    assert capture["start_error"] is None


def test_copilot_supports_github_enterprise_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Facet 2: a GHE hostname must be configurable and forwarded to the SDK."""
    import omnigent.onboarding.copilot_auth as copilot_auth

    # A GHE hostname must be configurable (a resolver exists) and forwarded to
    # the SDK so an org whose Copilot lives on a GHE instance can be targeted.
    assert hasattr(copilot_auth, "resolve_copilot_github_host")
    monkeypatch.setattr(copilot_auth, "resolve_copilot_github_host", lambda *a, **k: "shs.ghe.com")
    capture = _build_client_kwargs(monkeypatch)
    forwarded = capture["client_kwargs"]
    host_seen = forwarded.get("github_host") or ((forwarded.get("env") or {}).get("GH_HOST"))
    assert host_seen == "shs.ghe.com"

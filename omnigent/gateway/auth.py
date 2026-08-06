"""Databricks bearer minting for the gateway servlet.

The servlet holds credentials in memory and *mints* them (via the Databricks
CLI's own OAuth cache) instead of re-reading a token file — the design that
keeps long sessions from serving an expired bearer forever. Minting happens
off the per-request hot path via a short-TTL cache; access tokens live ~1h,
so a 15-minute re-mint cadence always stays ahead of expiry.
"""

from __future__ import annotations

import asyncio
import configparser
import json
import logging
import os
import time
from pathlib import Path

_logger = logging.getLogger(__name__)

# Re-mint cadence; matches the harness-side seams (claude apiKeyHelper TTL /
# codex refresh_interval_ms are both 900s).
_TOKEN_TTL_S = 900.0
_MINT_TIMEOUT_S = 30.0


def databrickscfg_host_for_profile(profile: str) -> str | None:
    """
    Resolve a profile's workspace host from ``~/.databrickscfg``.

    Registration passes only a profile *name* (a pointer into shared host
    config); the servlet resolves the host itself from the same file the
    launcher read.

    :param profile: Profile section name, e.g. ``"oss"``.
    :returns: The workspace origin without a trailing slash, or ``None`` when
        the file/section/host is absent or unreadable.
    """
    parser = configparser.ConfigParser()
    try:
        parser.read(Path.home() / ".databrickscfg")
    except (OSError, configparser.Error):
        return None
    if not parser.has_section(profile):
        return None
    host = parser.get(profile, "host", fallback="").strip().rstrip("/")
    return host or None


class TokenMinter:
    """Per-profile Databricks bearer cache with async minting."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def bearer(self, profile: str) -> str:
        """
        Return a fresh-enough bearer for *profile*.

        :param profile: ``~/.databrickscfg`` profile name, e.g. ``"oss"``.
        :returns: An access token.
        :raises RuntimeError: When minting fails (dead auth); the caller
            surfaces this as a 502 with the real cause.
        """
        env_bearer = os.environ.get("DATABRICKS_BEARER", "").strip()
        if env_bearer:
            return env_bearer
        cached = self._cache.get(profile)
        if cached is not None and cached[1] > time.monotonic():
            return cached[0]
        lock = self._locks.setdefault(profile, asyncio.Lock())
        async with lock:
            cached = self._cache.get(profile)
            if cached is not None and cached[1] > time.monotonic():
                return cached[0]
            token = await self._mint(profile)
            self._cache[profile] = (token, time.monotonic() + _TOKEN_TTL_S)
            return token

    async def _mint(self, profile: str) -> str:
        """Mint one token via ``databricks auth token`` (never re-reads a file)."""
        env = {k: v for k, v in os.environ.items() if k != "DATABRICKS_CONFIG_PROFILE"}
        proc = await asyncio.create_subprocess_exec(
            "databricks",
            "auth",
            "token",
            "--profile",
            profile,
            "--output",
            "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_MINT_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            raise RuntimeError(
                f"databricks auth token timed out for profile {profile!r}"
            ) from None
        if proc.returncode != 0:
            detail = stderr.decode(errors="replace").strip()[:300]
            raise RuntimeError(
                f"databricks auth token failed for profile {profile!r}: {detail} "
                f"(run `databricks auth login --profile {profile}` to re-authenticate)"
            )
        try:
            token = json.loads(stdout).get("access_token")
        except json.JSONDecodeError:
            token = None
        if not isinstance(token, str) or not token:
            raise RuntimeError(f"databricks auth token returned no access_token for {profile!r}")
        return token

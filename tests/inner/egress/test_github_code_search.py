"""CONNECT-path tests for the query-aware GitHub code-search gate."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import ssl
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from omnigent.inner.credential_proxy import CredentialRewriteRule
from omnigent.inner.datamodel import GitHubCodeSearchSpec
from omnigent.inner.egress.ca import ensure_ca, ensure_ca_bundle
from omnigent.inner.egress.certs import HostCertCache
from omnigent.inner.egress.proxy import EgressProxy
from omnigent.inner.egress.rules import parse_rules


@pytest.fixture()
def ca_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    cert_path, key_path = ensure_ca(cache_dir=tmp_path)
    bundle_path = ensure_ca_bundle(cert_path, cache_dir=tmp_path)
    return cert_path, key_path, bundle_path


@dataclass(frozen=True)
class _CapturedRequest:
    request_line: bytes
    headers: tuple[bytes, ...]


def _mitm_request(
    proxy_port: int,
    ca_bundle: Path,
    inner_request: bytes,
    *,
    connect_target: str = "api.github.com:443",
    server_hostname: str = "api.github.com",
) -> bytes:
    raw = socket.create_connection(("127.0.0.1", proxy_port), timeout=10)
    try:
        raw.sendall(
            (f"CONNECT {connect_target} HTTP/1.1\r\nHost: {server_hostname}\r\n\r\n").encode(
                "latin-1"
            )
        )
        established = b""
        while b"\r\n\r\n" not in established:
            chunk = raw.recv(4096)
            if not chunk:
                return established
            established += chunk
        if not established.startswith(b"HTTP/1.1 200"):
            return established

        context = ssl.create_default_context(cafile=str(ca_bundle))
        tls = context.wrap_socket(raw, server_hostname=server_hostname)
        try:
            tls.sendall(inner_request)
            response = b""
            while True:
                chunk = tls.recv(4096)
                if not chunk:
                    return response
                response += chunk
        finally:
            with contextlib.suppress(Exception):
                tls.close()
    finally:
        with contextlib.suppress(Exception):
            raw.close()


async def _start_upstream(
    *,
    cert_path: Path,
    key_path: Path,
    captured: list[_CapturedRequest],
) -> asyncio.Server:
    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        captured.append(_CapturedRequest(request_line=lines[0], headers=tuple(lines[1:])))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()

    context = HostCertCache(cert_path, key_path).get_ssl_context("api.github.com")
    return await asyncio.start_server(_handle, "127.0.0.1", 0, ssl=context)


def _proxy(
    *,
    cert_path: Path,
    key_path: Path,
    bundle_path: Path,
) -> EgressProxy:
    return EgressProxy(
        parse_rules(["* api.github.com/**"]),
        cert_path,
        key_path,
        upstream_ca_bundle=bundle_path,
        block_private_destinations=False,
        credential_rewrites=[
            CredentialRewriteRule(
                host="api.github.com",
                scheme="bearer",
                real_secret="trusted-github-token",
                synthetic=None,
            )
        ],
        github_code_search=GitHubCodeSearchSpec(
            host="api.github.com",
            control_header="x-omnigent-github-org",
            organizations=("databricks-eng", "databricks-security"),
        ),
    )


@pytest.mark.asyncio
async def test_github_search_rewrites_query_and_injects_trusted_auth(
    ca_paths: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cert_path, key_path, bundle_path = ca_paths
    captured: list[_CapturedRequest] = []
    upstream = await _start_upstream(cert_path=cert_path, key_path=key_path, captured=captured)
    upstream_port = upstream.sockets[0].getsockname()[1]
    real_open_connection = asyncio.open_connection

    async def _redirect(host: str, port: int, *args: object, **kwargs: object):
        if host == "api.github.com" and port == 443 and kwargs.get("ssl") is not None:
            return await real_open_connection(
                "127.0.0.1",
                upstream_port,
                *args,
                **kwargs,
            )
        return await real_open_connection(host, port, *args, **kwargs)

    monkeypatch.setattr(asyncio, "open_connection", _redirect)
    proxy = _proxy(cert_path=cert_path, key_path=key_path, bundle_path=bundle_path)
    proxy_port = await proxy.start_tcp()
    request = (
        b"GET   /search/code?q=needle+or+not+language%3Apython&page=2&per_page=10"
        b"&sort=indexed&order=desc   HTTP/1.0\r\n"
        b"Host: API.GitHub.com:443\r\n"
        b"X-Omnigent-GitHub-Org: Databricks-Eng\r\n"
        b"Authorization: Bearer oa_cred_caller-controlled\r\n"
        b"authorization: token caller-controlled\r\n"
        b"\r\n"
    )
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(_mitm_request, proxy_port, bundle_path, request),
            timeout=15,
        )
    finally:
        await proxy.stop()
        upstream.close()
        await upstream.wait_closed()

    assert b"200 OK" in response
    assert b"trusted-github-token" not in response
    assert b"oa_cred_" not in response
    assert len(captured) == 1
    forwarded = captured[0]
    method, target, version = forwarded.request_line.decode().strip().split()
    assert (method, version) == ("GET", "HTTP/1.1")
    query = parse_qs(urlsplit(target).query, strict_parsing=True)
    assert urlsplit(target).path == "/search/code"
    assert query == {
        "q": ["needle or not language:python org:databricks-eng"],
        "page": ["2"],
        "per_page": ["10"],
        "sort": ["indexed"],
        "order": ["desc"],
    }
    lowered = [line.lower() for line in forwarded.headers]
    assert b"authorization: bearer trusted-github-token" in lowered
    assert lowered.count(b"host: api.github.com") == 1
    assert not any(line.startswith(b"x-omnigent-github-org:") for line in lowered)
    assert not any(b"oa_cred_" in line for line in lowered)


@pytest.mark.asyncio
async def test_github_search_connect_denials_are_generic_and_never_forwarded(
    ca_paths: tuple[Path, Path, Path],
) -> None:
    cert_path, key_path, bundle_path = ca_paths
    proxy = _proxy(cert_path=cert_path, key_path=key_path, bundle_path=bundle_path)
    proxy_port = await proxy.start_tcp()
    cases = {
        "missing-host": (
            b"GET /search/code?q=x HTTP/1.1\r\nX-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "duplicate-host": (
            b"GET /search/code?q=x HTTP/1.1\r\n"
            b"Host: api.github.com\r\nHost: api.github.com:443\r\n"
            b"X-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "mismatched-host": (
            b"GET /search/code?q=x HTTP/1.1\r\nHost: github.com\r\n"
            b"X-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "other-host-port": (
            b"GET /search/code?q=x HTTP/1.1\r\nHost: api.github.com:8443\r\n"
            b"X-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "wrong-method": (
            b"POST /search/code?q=x HTTP/1.1\r\nX-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "wrong-path": (
            b"GET /search/issues?q=x HTTP/1.1\r\nX-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "fragment": (
            b"GET /search/code?q=x#fragment HTTP/1.1\r\n"
            b"X-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "empty-fragment": (
            b"GET /search/code?q=x# HTTP/1.1\r\nX-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "missing-org": b"GET /search/code?q=x HTTP/1.1\r\n\r\n",
        "duplicate-org": (
            b"GET /search/code?q=x HTTP/1.1\r\n"
            b"X-Omnigent-GitHub-Org: databricks-eng\r\n"
            b"x-omnigent-github-org: databricks-security\r\n\r\n"
        ),
        "unknown-org": (b"GET /search/code?q=x HTTP/1.1\r\nX-Omnigent-GitHub-Org: other\r\n\r\n"),
        "missing-q": (
            b"GET /search/code?page=1 HTTP/1.1\r\nX-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "duplicate-q": (
            b"GET /search/code?q=x&q=y HTTP/1.1\r\nX-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "unsupported-param": (
            b"GET /search/code?q=x&type=code HTTP/1.1\r\n"
            b"X-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "duplicate-param": (
            b"GET /search/code?q=x&page=1&page=2 HTTP/1.1\r\n"
            b"X-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "ownership-org": (
            b"GET /search/code?q=needle+org%3Aother HTTP/1.1\r\n"
            b"X-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "negative-ownership-org": (
            b"GET /search/code?q=needle+-org%3Aother HTTP/1.1\r\n"
            b"X-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "ownership-user": (
            b"GET /search/code?q=user%3Asomeone HTTP/1.1\r\n"
            b"X-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "ownership-repo": (
            b"GET /search/code?q=repo%3Aorg%2Frepo HTTP/1.1\r\n"
            b"X-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "boolean-or": (
            b"GET /search/code?q=x+OR+y HTTP/1.1\r\nX-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "boolean-not": (
            b"GET /search/code?q=NOT+x HTTP/1.1\r\nX-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "ascii-control": (
            b"GET /search/code?q=x%0Ay HTTP/1.1\r\nX-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "empty-q": (
            b"GET /search/code?q=+++ HTTP/1.1\r\nX-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "malformed-percent": (
            b"GET /search/code?q=x%ZZ HTTP/1.1\r\nX-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
        "too-long": (
            b"GET /search/code?q=" + (b"x" * 257) + b" HTTP/1.1\r\n"
            b"X-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        ),
    }
    try:
        for name, request in cases.items():
            if name != "missing-host" and b"\r\nhost:" not in request.lower():
                request = request.replace(
                    b"\r\n",
                    b"\r\nHost: api.github.com\r\n",
                    1,
                )
            response = await asyncio.wait_for(
                asyncio.to_thread(_mitm_request, proxy_port, bundle_path, request),
                timeout=15,
            )
            assert b"HTTP/1.1 403 Forbidden" in response, (name, response[:200])
            assert response.endswith(b"403 Forbidden\r\n"), (name, response[:200])
            assert b"trusted-github-token" not in response
            assert b"oa_cred_" not in response
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_github_search_rejects_cleartext_and_non_443_connect(
    ca_paths: tuple[Path, Path, Path],
) -> None:
    cert_path, key_path, bundle_path = ca_paths
    proxy = _proxy(cert_path=cert_path, key_path=key_path, bundle_path=bundle_path)
    proxy_port = await proxy.start_tcp()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        writer.write(
            b"GET http://api.github.com/search/code?q=secret-query HTTP/1.1\r\n"
            b"Host: api.github.com\r\n"
            b"X-Omnigent-GitHub-Org: databricks-eng\r\n\r\n"
        )
        await writer.drain()
        cleartext_response = await asyncio.wait_for(reader.read(4096), timeout=5)
        writer.close()

        non_443_response = await asyncio.wait_for(
            asyncio.to_thread(
                _mitm_request,
                proxy_port,
                bundle_path,
                b"",
                connect_target="api.github.com:80",
            ),
            timeout=10,
        )
        wrong_host_response = await asyncio.wait_for(
            asyncio.to_thread(
                _mitm_request,
                proxy_port,
                bundle_path,
                b"",
                connect_target="github.com:443",
                server_hostname="github.com",
            ),
            timeout=10,
        )
    finally:
        await proxy.stop()

    for response in (cleartext_response, non_443_response, wrong_host_response):
        assert b"HTTP/1.1 403 Forbidden" in response
        assert b"secret-query" not in response
    assert cleartext_response.endswith(b"403 Forbidden\r\n")
    assert non_443_response.endswith(b"403 Forbidden\r\n")

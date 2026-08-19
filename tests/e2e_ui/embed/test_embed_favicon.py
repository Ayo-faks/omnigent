"""E2E: the embed island points the host page's tab favicon at Otto.

The standalone suite drives ``main.tsx``; the embed entry (``embed.tsx``)
only ever runs inside a host application, so embed-only behavior has no
coverage in the rest of the suite. These tests mount the real
``OmnigentApp`` in a minimal host shell (``web/embed-harness.html`` +
``web/vite.embed-harness.config.ts`` — the same component the Databricks
monolith renders, bundled with its own React + react-router), serve that
build over a local HTTP server, and assert the host page's
``link[rel="icon"]``:

- becomes the Otto starfish (an inlined SVG data URI) while the embed is
  mounted, replacing the host page's own icon,
- prefers the operator branding favicon when ``/v1/info`` advertises one,
- is restored (href AND type) when the host navigates away and the island
  unmounts — the host must get its own tab icon back.

Part of the gated e2e suite (needs ``npm`` + a vite build); see this
package's ``conftest`` module docstring for how the suite is run and
excluded from the default ``pytest`` run.
"""

from __future__ import annotations

import base64
import functools
import http.server
import os
import re
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WEB_DIR = _REPO_ROOT / "web"

# The host shell's own icon, declared in embed-harness.html — what the
# workspace tab would show without the embed.
_HOST_FAVICON_HREF = "/host-favicon.ico"
_HOST_FAVICON_TYPE = "image/x-icon"
_BRANDING_FAVICON_HREF = "/v1/branding/logo/favicon"

# Otto is inlined as a data: URI by the `?inline` import in
# web/src/lib/documentFavicon.ts; the URL-encoded magenta fill is the
# starfish's signature color (#F43BA6).
_OTTO_DATA_URI = re.compile(r"^data:image/svg\+xml")
_OTTO_MAGENTA = "%23F43BA6"

_LOGO_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture(scope="module")
def embed_harness_build(built_spa: None, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the embed host-shell page into an isolated dir.

    :param built_spa: Depended on only to guarantee the toolchain is
        installed (``pnpm install``); the harness build does not use the
        standalone output.
    :param tmp_path_factory: Isolated ``--outDir`` so this never clobbers a
        real build.
    :returns: Directory containing ``embed-harness.html`` + hashed assets.
    """
    out = tmp_path_factory.mktemp("embed-harness")
    subprocess.run(
        ["pnpm", "run", "build:embed-harness", "--outDir", str(out)],
        cwd=_WEB_DIR,
        check=True,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"},
    )
    # Guard against a vacuous pass: if the `--outDir` override is ever
    # dropped, the build lands elsewhere and the server below would 404
    # every request — fail here with the real cause instead.
    if not (out / "embed-harness.html").is_file():
        pytest.fail(
            f"embed harness build produced no embed-harness.html in {out} — the "
            "--outDir override was not honored"
        )
    return out


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler without the per-request stderr log."""

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def embed_harness_url(embed_harness_build: Path) -> Iterator[str]:
    """Serve the harness build over an ephemeral loopback HTTP server.

    A real server (rather than ``page.route`` file interception) keeps
    module-script MIME handling and lazy-chunk loading identical to how a
    host page serves the island.
    """
    handler = functools.partial(_QuietHandler, directory=str(embed_harness_build))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _stub_info(page: Page, branding: object) -> None:
    """Serve ``/v1/info`` carrying only ``branding``; the SPA defaults the rest."""
    page.route("**/v1/info", lambda route: route.fulfill(json={"branding": branding}))
    page.route(
        "**/v1/branding/logo/**",
        lambda route: route.fulfill(status=200, content_type="image/png", body=_LOGO_PNG),
    )


def test_embed_tab_favicon_is_otto_while_mounted(page: Page, embed_harness_url: str) -> None:
    """The embed swaps the host's tab icon to Otto and restores it on unmount."""
    _stub_info(page, None)
    page.goto(f"{embed_harness_url}/embed-harness.html")

    icon = page.locator('head link[rel="icon"]')
    # Once the island mounts, the host icon is replaced by the inlined Otto
    # SVG and the host's type hint is cleared (it described the .ico).
    expect(icon).to_have_attribute("href", _OTTO_DATA_URI, timeout=30_000)
    href = icon.get_attribute("href") or ""
    assert _OTTO_MAGENTA in href.upper(), (
        f"favicon data URI is not the Otto starfish: {href[:120]}"
    )
    assert icon.get_attribute("type") is None

    # Host navigates away → island unmounts → the host's own icon (href and
    # type) is restored.
    page.get_by_test_id("host-nav-toggle").click()
    expect(icon).to_have_attribute("href", re.compile(re.escape(_HOST_FAVICON_HREF) + r"$"))
    assert icon.get_attribute("type") == _HOST_FAVICON_TYPE

    # Navigating back re-mounts the island → Otto again.
    page.get_by_test_id("host-nav-toggle").click()
    expect(icon).to_have_attribute("href", _OTTO_DATA_URI)


def test_embed_tab_favicon_prefers_operator_branding(page: Page, embed_harness_url: str) -> None:
    """An operator-configured branding favicon wins over the Otto default."""
    _stub_info(
        page,
        {
            "app_name": "Acme Agent",
            "heading": None,
            "logos": {"main": None, "loading": None, "favicon": _BRANDING_FAVICON_HREF},
            "powered_by": True,
        },
    )
    page.goto(f"{embed_harness_url}/embed-harness.html")

    icon = page.locator('head link[rel="icon"]')
    # The Otto default applies at mount; once the stubbed /v1/info resolves,
    # the branding favicon replaces it.
    expect(icon).to_have_attribute(
        "href", re.compile(re.escape(_BRANDING_FAVICON_HREF) + r"$"), timeout=30_000
    )

    # Unmount still restores the host icon, not the branding one.
    page.get_by_test_id("host-nav-toggle").click()
    expect(icon).to_have_attribute("href", re.compile(re.escape(_HOST_FAVICON_HREF) + r"$"))
    assert icon.get_attribute("type") == _HOST_FAVICON_TYPE

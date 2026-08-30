"""E2E: links to agent-created files must open the file, even from a remote session.

When a session runs
on a remote host, the browser cannot reach that host's filesystem directly, so
a link the agent emits for a file it just created must open through the app
(the FileViewer, which fetches over the server<->runner connection) rather
than being treated as a browser-resolvable location.

The journey is the real one from the report: the user asks the agent to create
a file; the agent writes it with a tool call (a scripted ``sys_os_shell`` on
the mock LLM -- so the file genuinely lands in the runner's workspace and the
changed-files watchdog sees it) and replies with a markdown link to it. The
suite's spawned runner is a separate out-of-process runner connected over the
WebSocket tunnel -- exactly the remote-session architecture: the page has no
access to the runner's filesystem except through the server API.

Two link forms agents commonly emit for a created file, one test each:

  - ``file://<abs>`` URI  -> ``test_created_file_uri_link_opens_file``.
    A raw ``file://`` URL could never work from a remote client (it names the
    runner host's disk), so the app must rewrite it to the workspace path and
    open it through the FileViewer. Left unhandled, the markdown sanitizer
    strips the ``file:`` href before the workspace-file marking pass runs and
    the link renders as an inert grey ``... [blocked]`` span -- the user
    cannot open the created file at all.
  - ``<abs>`` plain path  -> ``test_created_file_absolute_path_link_opens_file``.
    The path is handed to the FileViewer renderer and clicking opens the file
    through the session connection. Kept here as the remote-accessible
    baseline the ``file://`` form must match.
"""

from __future__ import annotations

import gzip
import io
import json
import re
import shutil
import tarfile
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, expect

from tests.e2e_ui.conftest import configure_mock_llm, set_fallback_mock_llm

_FILE = "report.md"
_COMPOSER = "Send a message…"
_ASSISTANT = '[data-testid="message-bubble"][data-role="assistant"]'
_WORKING = '[data-testid="working-indicator"]'

# The agent spec: a deterministic file-creating assistant. The model name is
# substituted per-fixture so each test gets an isolated mock-LLM queue.
_AGENT_YAML = """\
name: {name}
prompt: |
  You are a deterministic test assistant. When asked to create a report you
  write the file with a shell command and then reply with a link to it.

executor:
  model: {model}
  harness: openai-agents

os_env:
  type: caller_process
  cwd: {cwd}
  sandbox:
    type: none
"""


def _agent_bundle(name: str, model: str, cwd: str) -> bytes:
    """Gzip-tar the agent YAML for multipart upload.

    The ``<name>.yaml`` arcname routes the bundle through the omnigent compat
    adapter (translating the ``executor.harness`` shorthand), matching the
    sibling ``test_chat_file_path_links.py`` helper.

    :param name: Agent name (also the archive member name).
    :param model: Mock-LLM queue key baked in as the executor model.
    :param cwd: Absolute workspace directory the runner should use as root.
    :returns: ``.tar.gz`` bytes for multipart upload.
    """
    yaml_text = _AGENT_YAML.format(name=name, model=model, cwd=cwd)
    buf = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w") as tar,
    ):
        data = yaml_text.encode()
        info = tarfile.TarInfo(name=f"{name}.yaml")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def created_file_session(
    live_server: str,
    runner_id: str,
    mock_llm_server_url: str,
) -> Iterator[tuple[str, str, str, str]]:
    """A runner-bound session whose next turn creates ``report.md``.

    Creates a fresh workspace, registers a session-scoped agent pinned to it,
    binds the suite's spawned (tunnel-connected) runner, reads the live
    ``metadata.root``, and scripts the mock LLM: first call returns a
    ``sys_os_shell`` tool call that writes ``report.md`` into the workspace;
    the reply text is configured by the test (it decides which link form the
    agent emits).

    :param live_server: Spawned server base URL.
    :param runner_id: Token-bound runner id to bind the session to.
    :param mock_llm_server_url: Mock LLM base URL for queue configuration.
    :returns: ``(base_url, session_id, root, model)`` -- ``model`` is the
        per-fixture mock queue key the test uses to enqueue the reply.
    """
    ws = Path(tempfile.mkdtemp(prefix="omnigent-e2e-created-file-links-"))
    name = f"created_file_probe_{uuid.uuid4().hex[:8]}"
    model = f"created-file-probe-{uuid.uuid4().hex[:8]}"

    create_resp = httpx.post(
        f"{live_server}/v1/sessions",
        data={"metadata": json.dumps({})},
        files={
            "bundle": (
                "agent.tar.gz",
                _agent_bundle(name, model, str(ws)),
                "application/gzip",
            )
        },
        timeout=30.0,
    )
    create_resp.raise_for_status()
    session_id = create_resp.json()["session_id"]

    try:
        httpx.patch(
            f"{live_server}/v1/sessions/{session_id}",
            json={"runner_id": runner_id},
            timeout=10.0,
        ).raise_for_status()

        env_resp = httpx.get(
            f"{live_server}/v1/sessions/{session_id}/resources/environments/default",
            timeout=10.0,
        )
        env_resp.raise_for_status()
        root = env_resp.json().get("metadata", {}).get("root")
        assert root, "environment must report metadata.root"

        yield (live_server, session_id, root, model)
    finally:
        httpx.delete(f"{live_server}/v1/sessions/{session_id}", timeout=10.0)
        shutil.rmtree(ws, ignore_errors=True)


def _drive_create_turn(
    page: Page,
    base_url: str,
    session_id: str,
    mock_llm_server_url: str,
    model: str,
    reply_text: str,
) -> None:
    """Run the user journey: ask for the report, let the agent create + link it.

    Enqueues *reply_text* as the second LLM response (after the tool result),
    sends the user turn through the composer, and waits for the turn to finish
    (assistant bubble rendered, working shimmer gone).

    :param page: Playwright page.
    :param base_url: Server base URL.
    :param session_id: The session to drive.
    :param mock_llm_server_url: Mock LLM base URL.
    :param model: The session's mock queue key.
    :param reply_text: The assistant reply containing the file link.
    """
    # One queue for the whole turn: first LLM call returns the sys_os_shell
    # tool call that really writes report.md into the runner's workspace
    # (registering with the changed-files watchdog exactly as a real created
    # file does); the second call (after the tool result) returns the reply
    # carrying the link. Configured together because a second configure on
    # the same key resets the queue.
    configure_mock_llm(
        mock_llm_server_url,
        [
            {
                "tool_calls": [
                    {
                        "call_id": "call_write_report",
                        "name": "sys_os_shell",
                        "arguments": json.dumps(
                            {"command": f'printf "# Report\\n\\nDone.\\n" > {_FILE}'}
                        ),
                    }
                ]
            },
            {"text": reply_text},
        ],
        key=model,
    )
    set_fallback_mock_llm(mock_llm_server_url, model, "Report created.")

    page.goto(f"{base_url}/c/{session_id}")
    composer = page.get_by_placeholder(_COMPOSER)
    expect(composer).to_be_visible(timeout=30_000)
    composer.fill("Create report.md and give me a link to it.")
    page.get_by_role("button", name="Send", exact=True).click()

    expect(page.locator(_ASSISTANT).last).to_contain_text(_FILE, timeout=60_000)
    expect(page.locator(_WORKING)).to_have_count(0, timeout=60_000)


def _assert_link_opens_file_viewer(page: Page) -> None:
    """The created-file link is clickable and opens the FileViewer on the file.

    The workspace-file link renderer exposes an openable link as a
    ``role="button"`` anchor named by the link text; clicking writes the opened
    path to the ``?file=`` query (the deterministic signal the sibling
    ``test_chat_file_path_links.py`` also asserts).

    :param page: Playwright page, on the session with the reply rendered.
    """
    link = page.get_by_test_id("assistant-text-section").last.get_by_role("button", name=_FILE)
    # Generous timeout: linkification settles after the changed-files list /
    # existence check loads. A dead span here (e.g. "report.md [blocked]")
    # means the created-file link is not openable -- the reported bug.
    expect(link).to_be_visible(timeout=30_000)
    link.click()
    page.wait_for_url(re.compile(r"[?&]file=report\.md(?:&|$)"), timeout=15_000)
    expect(page.get_by_test_id("file-viewer").last).to_contain_text("Report", timeout=15_000)


def test_created_file_uri_link_opens_file(
    page: Page,
    created_file_session: tuple[str, str, str, str],
    mock_llm_server_url: str,
) -> None:
    """A ``file://`` link to the created file must open it, not render blocked.

    Agents routinely link a created file as
    ``[report.md](file:///abs/path/report.md)``. A ``file://`` URL names the
    runner host's disk, which a remote client browser can never reach, so the
    app must route it to the FileViewer like any other workspace path. Left
    unhandled, the sanitizer strips the ``file:`` href before the
    workspace-file marking pass and the link renders as an inert grey
    ``report.md [blocked]`` span: the user cannot open the file they asked
    for at all.
    """
    base_url, session_id, root, model = created_file_session
    _drive_create_turn(
        page,
        base_url,
        session_id,
        mock_llm_server_url,
        model,
        f"I created the report for you: [{_FILE}](file://{root}/{_FILE})",
    )

    # Must not render the sanitizer's blocked-link fallback -- that is the
    # exact failure mode of this bug (inaccessible created-file link).
    expect(page.get_by_text("[blocked]")).to_have_count(0)
    _assert_link_opens_file_viewer(page)


def test_created_file_absolute_path_link_opens_file(
    page: Page,
    created_file_session: tuple[str, str, str, str],
    mock_llm_server_url: str,
) -> None:
    """An absolute-path link to the created file opens it through the app.

    The plain-path form: the href is handed to the FileViewer renderer, and
    clicking fetches the file over the server<->runner connection --
    accessible from a remote client. Guards the baseline the ``file://`` form
    must match.
    """
    base_url, session_id, root, model = created_file_session
    _drive_create_turn(
        page,
        base_url,
        session_id,
        mock_llm_server_url,
        model,
        f"I created the report for you: [{_FILE}]({root}/{_FILE})",
    )
    _assert_link_opens_file_viewer(page)

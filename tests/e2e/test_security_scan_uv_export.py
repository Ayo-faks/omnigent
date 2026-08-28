"""Security Scan's OSV step must export the lockfile without conflict errors.

The ``Security Scan`` workflow's "OSV advisory scan (uv.lock)" step used to
run ``uv export --frozen --format requirements-txt --all-extras``, but the
project declares the ``antigravity`` extra as conflicting with
``cwsandbox``/``modal``/``databricks`` and the ``lint`` group, so the export
exits 2 with::

    error: Extras `antigravity` and `cwsandbox` are incompatible with the
    declared conflicts: {`omnigent[antigravity]`, `omnigent[cwsandbox]`}

before ``pip-audit`` ever runs — failing the scan for every untrusted PR
that touches ``uv.lock`` and blocking all downstream gated jobs.

These tests replay the workflow's own export commands (parsed out of
``.github/workflows/security-scan.yml`` so the test always exercises what CI
actually runs) against the unmodified repository lockfile:

- every ``uv export`` the step runs must succeed, and
- the exports must collectively cover both sides of the declared conflict
  fork (the ``cwsandbox`` side and the ``antigravity`` side), so a fix
  cannot "pass" by simply dropping extras from the audit.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_RELPATH = ".github/workflows/security-scan.yml"
OSV_STEP_NAME = "OSV advisory scan (uv.lock)"

# One pinned package unique to each side of the declared conflict fork
# (see ``[tool.uv] conflicts`` in pyproject.toml). ``cwsandbox`` only
# resolves when antigravity is off; ``google-antigravity`` only resolves in
# the antigravity fork. Auditing both requires exporting both resolutions.
CONFLICT_FORK_SENTINELS = ("cwsandbox==", "google-antigravity==")


def _workflow_text() -> str:
    """
    Return the security-scan workflow's YAML source.

    Reads the working tree; falls back to ``git show HEAD:`` for sparse
    checkouts that strip ``.github/`` from the worktree.

    :returns: The workflow file contents.
    """
    path = REPO_ROOT / WORKFLOW_RELPATH
    if path.is_file():
        return path.read_text(encoding="utf-8")
    proc = subprocess.run(
        ["git", "show", f"HEAD:{WORKFLOW_RELPATH}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"{WORKFLOW_RELPATH} missing from both the working tree and HEAD: {proc.stderr.strip()}"
    )
    return proc.stdout


def _osv_step_script() -> str:
    """
    Return the ``run`` script of the workflow's OSV advisory scan step.

    :returns: The step's shell script, exactly as CI executes it.
    """
    doc = yaml.safe_load(_workflow_text())
    for job in doc.get("jobs", {}).values():
        for step in job.get("steps", []):
            if step.get("name") == OSV_STEP_NAME:
                run = step.get("run")
                assert run, f"step {OSV_STEP_NAME!r} has no run script"
                return run
    raise AssertionError(
        f"step {OSV_STEP_NAME!r} not found in {WORKFLOW_RELPATH}; if the "
        "step was renamed, update OSV_STEP_NAME so this regression test "
        "keeps tracking the workflow's real export commands"
    )


def _export_commands(script: str) -> list[list[str]]:
    """
    Extract every ``uv export`` invocation from the step's shell script.

    Joins backslash line-continuations and strips output redirections, so
    each returned argv is runnable directly via subprocess.

    :param script: The step's ``run`` script.
    :returns: One argv per ``uv export`` command, in script order.
    """
    joined = script.replace("\\\n", " ")
    commands: list[list[str]] = []
    for raw_line in joined.splitlines():
        line = raw_line.strip()
        if not line.startswith("uv export"):
            continue
        # Drop any `> file` / `| cmd` tail; we capture stdout ourselves.
        for splitter in (">", "|"):
            if splitter in line:
                line = line.split(splitter, 1)[0].strip()
        commands.append(shlex.split(line))
    return commands


@pytest.fixture(scope="module")
def export_results() -> list[tuple[list[str], subprocess.CompletedProcess[str]]]:
    """
    Run each of the workflow step's ``uv export`` commands from the repo root.

    :returns: ``(argv, completed_process)`` pairs, one per export command.
    """
    if shutil.which("uv") is None:
        pytest.skip("uv is not on PATH")
    commands = _export_commands(_osv_step_script())
    assert commands, (
        f"no `uv export` commands found in the {OSV_STEP_NAME!r} step; the "
        "OSV scan no longer exports the lockfile — update this test to "
        "follow the workflow's new audit mechanism"
    )
    results = []
    for argv in commands:
        proc = subprocess.run(
            argv,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        results.append((argv, proc))
    return results


def test_osv_step_exports_resolve_on_unmodified_lockfile(
    export_results: list[tuple[list[str], subprocess.CompletedProcess[str]]],
) -> None:
    """
    Every ``uv export`` the OSV step runs must succeed on a clean checkout.

    Before the fix the step's single ``--all-extras`` export exits 2 with an
    incompatible-extras error (antigravity vs cwsandbox), so the security
    scan fails before ``pip-audit`` runs on any PR that changes ``uv.lock``.
    """
    failures = [
        f"$ {shlex.join(argv)}\n  exit code {proc.returncode}\n  stderr: {proc.stderr.strip()}"
        for argv, proc in export_results
        if proc.returncode != 0
    ]
    assert not failures, (
        "the Security Scan workflow's OSV step runs uv export command(s) "
        "that fail on the unmodified lockfile, so the scan blocks every "
        "uv.lock-changing PR before pip-audit runs:\n" + "\n".join(failures)
    )


def test_osv_step_exports_cover_both_conflict_forks(
    export_results: list[tuple[list[str], subprocess.CompletedProcess[str]]],
) -> None:
    """
    The step's exports must collectively pin packages from both conflict forks.

    Guards the expected behavior ("audit every compatible lockfile
    resolution"): a fix that merely drops the antigravity or cwsandbox side
    from the audit would leave lockfile pins unaudited and must fail here.
    """
    combined = "\n".join(proc.stdout for _, proc in export_results if proc.returncode == 0)
    missing = [s for s in CONFLICT_FORK_SENTINELS if s not in combined]
    assert not missing, (
        "the OSV step's successful exports never pin these conflict-fork "
        f"packages, so they would go unaudited: {missing}. The step must "
        "export each compatible resolution set (e.g. --all-extras "
        "--no-extra antigravity, plus --extra antigravity) so every pinned "
        "third-party package is covered by pip-audit."
    )

"""Demo hygiene automation must match the published PR policy.

CONTRIBUTING.md, the PR template, and ``.github/scripts/pr-template/validate.py``
publish one rule: demo media (screenshot / recording) is required only when the
"UI / frontend change" box is checked; ``N/A`` or a written reproduction is
explicitly allowed for non-visual changes. The hourly sweep in
``.github/workflows/demo-check.js`` must enforce that same rule, and must clear
``needs-demo`` once an updated PR body satisfies it.

These tests run the real workflow script under Node with a mocked GitHub
client (the same approach as the ``.github/workflows/*.test.js`` suites) and
assert the published policy:

* a backend bug fix with a ``N/A`` / written-repro Demo section is not flagged;
* a UI / frontend change without recognized media is flagged;
* a UI / frontend change with media is not flagged;
* ``needs-demo`` is removed when the PR body becomes policy-compliant.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_RELPATH = Path(".github") / "workflows" / "demo-check.js"


def _script_path(tmp_path: Path) -> Path:
    """Locate demo-check.js, falling back to the committed copy.

    Some sandboxes strip ``.github`` from the working tree; the committed
    version is still the code under test, so extract it via ``git show``.
    """
    working_copy = REPO_ROOT / SCRIPT_RELPATH
    if working_copy.exists():
        return working_copy
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"HEAD:{SCRIPT_RELPATH.as_posix()}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip(f"{SCRIPT_RELPATH} not present in working tree or HEAD")
    extracted = tmp_path / "demo-check.js"
    extracted.write_text(proc.stdout)
    return extracted


# Node driver: feeds fixture PRs to the unmodified production script through a
# mocked GitHub client and reports every write it attempted as JSON.
DRIVER = """
const path = require("path");
const script = require(process.argv[2]);
const prs = JSON.parse(require("fs").readFileSync(0, "utf8"));

const nodes = prs.map((p) => ({
  number: p.number,
  author: { login: "outside-contributor" },
  authorAssociation: "CONTRIBUTOR",
  isDraft: false,
  labels: { nodes: (p.labels || []).map((name) => ({ name })) },
  body: p.body,
}));

const commented = [];
const labeled = [];
const removed = [];

const github = {
  // Route by search query the way GitHub would: a `label:needs-demo` search
  // returns only PRs actually carrying the label; the recency search returns
  // every fixture PR.
  graphql: async (_query, vars) => {
    const labeledOnly = (vars?.searchQuery ?? "").includes("label:");
    const matched = labeledOnly
      ? nodes.filter((n) => n.labels.nodes.some((l) => l.name === "needs-demo"))
      : nodes;
    return {
      rateLimit: { remaining: 5000, resetAt: "n/a" },
      search: { pageInfo: { hasNextPage: false, endCursor: null }, nodes: matched },
    };
  },
  rest: {
    repos: {
      getContent: async () => ({
        data: { content: Buffer.from("").toString("base64") },
      }),
    },
    issues: {
      createLabel: async () => {
        const err = new Error("already exists");
        err.status = 422;
        throw err;
      },
      createComment: async ({ issue_number }) => commented.push(issue_number),
      addLabels: async ({ issue_number, labels }) =>
        labeled.push({ number: issue_number, labels }),
      removeLabel: async ({ issue_number, name }) =>
        removed.push({ number: issue_number, name }),
    },
  },
};

script({
  context: { repo: { owner: "omnigent-ai", repo: "omnigent" } },
  github,
  core: { warning: () => {} },
})
  .then(() => {
    console.log(JSON.stringify({ commented, labeled, removed }));
  })
  .catch((err) => {
    console.error(err && err.stack ? err.stack : String(err));
    process.exit(1);
  });
"""


def _pr_body(
    *,
    bug_fix: bool = False,
    feature: bool = False,
    ui_change: bool = False,
    demo: str | None = None,
) -> str:
    """A PR body following the repository template."""
    demo_section = "" if demo is None else f"\n## Demo\n\n{demo}\n"

    def box(checked: bool) -> str:
        return "[x]" if checked else "[ ]"

    return f"""
## Related issue

Closes #1234

## Summary

- Fixes the stale cursor math in the agent handoff flow.

## Test Plan

Added focused unit coverage and an E2E regression for the handoff path.
{demo_section}
## Type of change

- {box(bug_fix)} Bug fix
- {box(feature)} Feature
- {box(ui_change)} UI / frontend change
- [ ] Refactor / chore
- [ ] Docs
- [ ] Test / CI
- [ ] Breaking change

## Test coverage

- [x] Unit tests added / updated
- [ ] Integration tests added / updated
- [x] E2E tests added / updated
- [ ] Manual verification completed
- [ ] Existing tests cover this change
- [ ] Not applicable

## Changelog

Stale polling no longer blocks the REPL handoff
"""


def _sweep(tmp_path: Path, prs: list[dict]) -> dict:
    """Run the real demo-check sweep over fixture PRs; return its API writes."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to run the workflow script")
    script = _script_path(tmp_path)
    driver = tmp_path / "driver.js"
    driver.write_text(DRIVER)
    proc = subprocess.run(
        [node, str(driver), str(script)],
        input=json.dumps(prs),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, f"driver failed:\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _flagged(result: dict) -> set[int]:
    return {entry["number"] for entry in result["labeled"]}


def test_backend_bug_with_na_demo_is_not_flagged(tmp_path: Path) -> None:
    """A non-visual bug fix with Demo `N/A` satisfies the published policy."""
    body = _pr_body(
        bug_fix=True,
        demo="N/A — orchestration metadata and prompt behavior; no visual surface.",
    )
    result = _sweep(tmp_path, [{"number": 101, "body": body, "labels": []}])
    assert 101 not in _flagged(result), (
        "the published policy allows `N/A` for non-visual changes, but the "
        "demo sweep flagged a compliant backend bug fix with needs-demo"
    )
    assert 101 not in result["commented"], (
        "the demo sweep nudge-commented on a policy-compliant backend bug fix"
    )


def test_backend_pr_with_written_reproduction_is_not_flagged(tmp_path: Path) -> None:
    """A written, runnable reproduction satisfies the policy for non-visual PRs."""
    body = _pr_body(
        bug_fix=True,
        feature=True,
        demo=(
            "Backend-only auth flow; there is no visual artifact. Reproduce with:\n\n"
            "```bash\n"
            "uv run pytest tests/server/test_device_auth.py -q\n"
            "```\n"
        ),
    )
    result = _sweep(tmp_path, [{"number": 102, "body": body, "labels": []}])
    assert 102 not in _flagged(result), (
        "the published policy allows a written reproduction for non-visual "
        "changes, but the demo sweep flagged the PR with needs-demo"
    )


def test_ui_change_missing_media_is_flagged(tmp_path: Path) -> None:
    """A UI / frontend change without recognized media must be flagged."""
    body = _pr_body(ui_change=True, demo="Looks better now, trust me.")
    result = _sweep(tmp_path, [{"number": 103, "body": body, "labels": []}])
    assert 103 in _flagged(result), (
        "a UI / frontend change without a screenshot or recording must get "
        "the needs-demo label"
    )
    assert 103 in result["commented"]


def test_ui_change_with_media_is_not_flagged(tmp_path: Path) -> None:
    """A UI / frontend change carrying real media passes the policy."""
    body = _pr_body(
        ui_change=True,
        demo="![new settings panel](https://github.com/org/repo/assets/1/shot.png)",
    )
    result = _sweep(tmp_path, [{"number": 104, "body": body, "labels": []}])
    assert 104 not in _flagged(result)
    assert 104 not in result["commented"]


def test_needs_demo_removed_when_body_becomes_compliant(tmp_path: Path) -> None:
    """The sweep clears needs-demo once the PR body satisfies the policy."""
    body = _pr_body(
        ui_change=True,
        demo="![fixed layout](https://github.com/org/repo/assets/1/after.png)",
    )
    result = _sweep(
        tmp_path,
        [{"number": 105, "body": body, "labels": ["needs-demo"]}],
    )
    removed = {(entry["number"], entry["name"]) for entry in result["removed"]}
    assert (105, "needs-demo") in removed, (
        "a PR whose updated body satisfies the demo policy must have the "
        "needs-demo label removed; the sweep currently skips already-labeled "
        "PRs so the label outlives the condition it flags"
    )
    assert 105 not in _flagged(result), "compliant PR must not be re-flagged"

#!/usr/bin/env python3
"""Assert merge-ready REQUIRED names match real workflow check names.

``evaluate-checks.sh`` treats a missing ALLOW_SKIP check whose workflow
succeeded as a PASS. Stale names in ``required.sh`` therefore auto-pass
forever, and newly added CI shards never gate until listed. This script
extracts check names from the workflows that feed the merge gate and
fails if REQUIRED (minus external apps like DCO) is not exactly that set.

Stdlib only (no PyYAML) so it can run in the lean Lint job before a full
``uv sync`` if needed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
REQUIRED_SH = Path(__file__).resolve().parent / "required.sh"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# Checks produced outside Actions job ``name:`` fields (GitHub Apps, etc.).
EXTERNAL_CHECKS = frozenset({"DCO"})

# Job names that run under gate workflows but are not PR merge checks
# (setup/matrix helpers, artifact rollups, path-detect probes).
NON_GATE_JOB_NAMES = frozenset(
    {
        "setup",
        "Security Gate",
        "Coverage report",
        "build codex-parity sidecar",
        "Detect render-affecting changes",
        "evaluate",  # merge-ready.yml's own job
    }
)


def _parse_bash_string_array(text: str, name: str) -> list[str]:
    """Extract `"..."` entries from a ``NAME=( ... )`` bash array."""
    m = re.search(rf"^{name}=\((.*?)^\)", text, flags=re.MULTILINE | re.DOTALL)
    if not m:
        raise SystemExit(f"could not find {name}=( ... ) in {REQUIRED_SH}")
    return re.findall(r'"([^"]*)"', m.group(1))


def load_required(path: Path = REQUIRED_SH) -> tuple[list[str], list[str]]:
    text = path.read_text(encoding="utf-8")
    return _parse_bash_string_array(text, "REQUIRED"), _parse_bash_string_array(
        text, "ALLOW_SKIP"
    )


def _strip_yaml_comments(line: str) -> str:
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i]
    return line


def _job_names_and_matrix_groups(workflow: Path) -> tuple[list[str], dict[str, list[str]]]:
    """Return static job names and matrix.group values keyed by job name template.

    Only understands the patterns this repo uses for merge-gate checks:
    - ``name: Literal``
    - ``name: Pytest (${{ matrix.group }})`` + ``- group: foo``
    - ``name: E2E ... (${{ matrix.shard_id }}/${{ matrix.num_shards }})``
    - ``name: Integration (${{ matrix.name }})``
    """
    lines = workflow.read_text(encoding="utf-8").splitlines()
    names: list[str] = []
    # template job name -> list of matrix.group values seen under that job
    groups_by_template: dict[str, list[str]] = {}
    current_name: str | None = None
    in_matrix_include = False
    indent_matrix = -1

    for raw in lines:
        line = _strip_yaml_comments(raw).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))

        m_name = re.match(r"^(\s*)name:\s*(.+)$", line)
        # Job-level name is nested under ``jobs:<id>:`` at indent 4.
        if m_name and len(m_name.group(1)) == 4:
            current_name = m_name.group(2).strip().strip("'\"")
            names.append(current_name)
            in_matrix_include = False
            continue

        if current_name and re.search(r"\bmatrix:\s*$", line):
            in_matrix_include = False
            indent_matrix = indent
            continue

        if current_name and indent_matrix >= 0 and indent <= indent_matrix and not line.strip().startswith("-"):
            # Left the matrix block.
            if not re.search(r"\b(include|group|name|shard_id|num_shards|paths|workers|dist|extra|markexpr|timeout)\b", line):
                in_matrix_include = False
                indent_matrix = -1

        if current_name and indent_matrix >= 0:
            if re.match(r"^\s+include:\s*$", line):
                in_matrix_include = True
                continue
            if in_matrix_include:
                gm = re.match(r"^\s+-\s+group:\s*(\S+)\s*$", line)
                if gm:
                    groups_by_template.setdefault(current_name, []).append(gm.group(1))

    return names, groups_by_template


def _expand_matrix_name(template: str, *, group: str | None = None, shard_id: int | None = None, num_shards: int | None = None, name: str | None = None) -> str:
    out = template
    if group is not None:
        out = out.replace("${{ matrix.group }}", group)
    if shard_id is not None and num_shards is not None:
        out = out.replace("${{ matrix.shard_id }}", str(shard_id))
        out = out.replace("${{ matrix.num_shards }}", str(num_shards))
    if name is not None:
        out = out.replace("${{ matrix.name }}", name)
    return out


def _num_shards_from_workflow(workflow: Path) -> int | None:
    text = workflow.read_text(encoding="utf-8")
    m = re.search(r"NUM_SHARDS:\s*[\"']?(\d+)", text)
    return int(m.group(1)) if m else None


def _integration_names() -> list[str]:
    script = REPO_ROOT / ".github" / "scripts" / "ci" / "integration-matrix.sh"
    text = script.read_text(encoding="utf-8")
    # Pull the JSON blob between the heredoc markers.
    m = re.search(r"read -r -d '' matrix <<'JSON'.*?(\{.*?\})\s*JSON", text, re.DOTALL)
    if not m:
        raise SystemExit(f"could not parse integration matrix JSON from {script}")
    raw = re.sub(r"\s+", "", m.group(1))
    data = json.loads(raw)
    return [row["name"] for row in data.get("include", [])]


def collect_defined_checks() -> set[str]:
    """Check names the merge gate may require (workflow jobs + externals)."""
    defined: set[str] = set(EXTERNAL_CHECKS)

    # --- CI pytest matrix + standalone store/parity jobs ---
    ci = WORKFLOWS / "ci.yml"
    names, groups = _job_names_and_matrix_groups(ci)
    for n in names:
        if "${{ matrix.group }}" in n:
            for g in groups.get(n, []):
                defined.add(_expand_matrix_name(n, group=g))
        elif n not in NON_GATE_JOB_NAMES and "${{" not in n:
            defined.add(n)

    # --- Lint ---
    lint_names, _ = _job_names_and_matrix_groups(WORKFLOWS / "lint.yml")
    for n in lint_names:
        if n not in NON_GATE_JOB_NAMES and "${{" not in n:
            defined.add(n)

    # --- Docker build ---
    docker_names, _ = _job_names_and_matrix_groups(WORKFLOWS / "docker-build.yml")
    for n in docker_names:
        if n not in NON_GATE_JOB_NAMES and "${{" not in n:
            defined.add(n)

    # --- E2E / E2E UI shards ---
    for wf_name, label in (("e2e.yml", "E2E Tests"), ("e2e-ui.yml", "E2E UI Tests")):
        wf = WORKFLOWS / wf_name
        num = _num_shards_from_workflow(wf)
        if num is None:
            raise SystemExit(f"NUM_SHARDS not found in {wf}")
        names, _ = _job_names_and_matrix_groups(wf)
        for n in names:
            if "matrix.shard_id" in n:
                for i in range(num):
                    defined.add(_expand_matrix_name(n, shard_id=i, num_shards=num))
            elif n not in NON_GATE_JOB_NAMES and "${{" not in n and not n.startswith("build "):
                # Keep non-matrix jobs only if they look like user-facing checks.
                if label.split()[0] in n or n in ("UI Snapshot (visual baselines)",):
                    defined.add(n)

    # --- UI Snapshot ---
    snap_names, _ = _job_names_and_matrix_groups(WORKFLOWS / "ui-snapshot.yml")
    for n in snap_names:
        if n not in NON_GATE_JOB_NAMES and "${{" not in n:
            defined.add(n)

    # --- Integration (names from the matrix script, not a static YAML list) ---
    integ_names, _ = _job_names_and_matrix_groups(WORKFLOWS / "integration.yml")
    harnesses = _integration_names()
    for n in integ_names:
        if "${{ matrix.name }}" in n:
            for h in harnesses:
                defined.add(_expand_matrix_name(n, name=h))
        elif n not in NON_GATE_JOB_NAMES and "${{" not in n:
            defined.add(n)

    return defined


def validate(required: list[str], defined: set[str]) -> list[str]:
    """Return human-readable error lines (empty means OK)."""
    errors: list[str] = []
    req_set = set(required)

    stale = sorted(req_set - defined)
    if stale:
        errors.append("REQUIRED entries with no matching workflow/app check name:")
        errors.extend(f"  - {n}" for n in stale)

    # Every defined merge-gate check must appear in REQUIRED so new shards
    # cannot land ungated (the omission half of the original bug).
    missing = sorted(defined - req_set)
    if missing:
        errors.append("Live check names missing from REQUIRED:")
        errors.extend(f"  - {n}" for n in missing)

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--required",
        type=Path,
        default=REQUIRED_SH,
        help="path to required.sh",
    )
    parser.add_argument(
        "--print-defined",
        action="store_true",
        help="print the extracted defined check set and exit",
    )
    args = parser.parse_args(argv)

    required, _allow_skip = load_required(args.required)
    defined = collect_defined_checks()

    if args.print_defined:
        for n in sorted(defined):
            print(n)
        return 0

    errors = validate(required, defined)
    if errors:
        print("::error::merge-ready REQUIRED is out of sync with workflow job names")
        print("\n".join(errors))
        print(
            "\nUpdate .github/scripts/merge-ready/required.sh to match live "
            "job names (REQUIRED and ALLOW_SKIP). Stale names auto-pass; "
            "omitted names never gate."
        )
        return 1

    print(f"OK: {len(required)} REQUIRED check(s) match defined workflow/app names.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

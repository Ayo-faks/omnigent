#!/usr/bin/env python3
"""Fail CI on *new* mypy errors; known errors live in ``mypy-baseline.txt``.

Full-tree ``mypy omnigent`` currently reports thousands of pre-existing
errors, so we cannot gate the whole package cleanly yet. This script runs
mypy with the project config, fingerprints each error (path + code +
message, line-agnostic), and exits non-zero only when the run produces
signatures that are not covered by the committed baseline multiset.

Graduate the rest by fixing errors and rewriting the baseline::

    uv run python scripts/mypy_gate.py --write-baseline

See the header of ``mypy-baseline.txt`` for the fingerprint format.
"""

from __future__ import annotations

import argparse
import collections
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPO_ROOT / "mypy-baseline.txt"
DEFAULT_TARGET = "omnigent"

# path:line: error: message [code]
_ERROR_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+): error: (?P<msg>.+?)(?:  \[(?P<code>[^\]]+)\])?$"
)


def _fingerprint(path: str, code: str, msg: str) -> str:
    return f"{path}\t{code}\t{msg}"


def parse_mypy_output(text: str) -> list[str]:
    """Return one fingerprint per mypy error line (order preserved)."""
    out: list[str] = []
    for line in text.splitlines():
        m = _ERROR_RE.match(line)
        if not m:
            continue
        out.append(
            _fingerprint(
                m.group("path"),
                m.group("code") or "unknown",
                m.group("msg"),
            )
        )
    return out


def load_baseline(path: Path) -> list[str]:
    if not path.is_file():
        return []
    sigs: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        sigs.append(line)
    return sigs


def write_baseline(path: Path, fingerprints: list[str]) -> None:
    header = (
        "# mypy error baseline (path<TAB>code<TAB>message). Line numbers are\n"
        "# omitted so edits that only shift lines do not create false 'new'\n"
        "# errors. Maintained by scripts/mypy_gate.py --write-baseline.\n"
        "# Failures: signatures present in a mypy run but not in this multiset.\n"
    )
    body = "\n".join(fingerprints)
    path.write_text(header + body + ("\n" if body else ""), encoding="utf-8")


def run_mypy(target: str) -> tuple[int, list[str], str]:
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", target],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    text = proc.stdout + proc.stderr
    return proc.returncode, parse_mypy_output(text), text


def new_errors(current: list[str], baseline: list[str]) -> list[str]:
    """Multiset difference: fingerprints in current not covered by baseline."""
    left = collections.Counter(current)
    left.subtract(collections.Counter(baseline))
    extras: list[str] = []
    for sig, count in sorted(left.items()):
        if count > 0:
            extras.extend([sig] * count)
    return extras


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help=f"baseline file (default: {DEFAULT_BASELINE})",
    )
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        help=f"mypy target (default: {DEFAULT_TARGET})",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="rewrite the baseline from the current mypy run and exit 0",
    )
    args = parser.parse_args(argv)

    rc, fingerprints, raw = run_mypy(args.target)
    if args.write_baseline:
        write_baseline(args.baseline, fingerprints)
        print(
            f"Wrote {len(fingerprints)} error fingerprint(s) to {args.baseline} "
            f"(mypy exit {rc})."
        )
        return 0

    baseline = load_baseline(args.baseline)
    if not baseline and fingerprints:
        print(
            f"::error::No baseline at {args.baseline} but mypy reported "
            f"{len(fingerprints)} error(s). Run with --write-baseline first.",
            file=sys.stderr,
        )
        return 1

    extras = new_errors(fingerprints, baseline)
    resolved = new_errors(baseline, fingerprints)

    print(
        f"mypy: {len(fingerprints)} error(s); "
        f"baseline: {len(baseline)}; "
        f"new: {len(extras)}; "
        f"resolved-since-baseline: {len(resolved)}"
    )
    if extras:
        print("::error::New mypy errors not in mypy-baseline.txt:", file=sys.stderr)
        for sig in extras:
            path, code, msg = sig.split("\t", 2)
            print(f"  {path}: [{code}] {msg}", file=sys.stderr)
        print(
            "Fix the new errors, or if they are intentional debt run "
            "`uv run python scripts/mypy_gate.py --write-baseline` "
            "(review the diff).",
            file=sys.stderr,
        )
        return 1

    if rc not in (0, 1):
        # mypy crashed / couldn't run — surface the raw output.
        print(raw, file=sys.stderr)
        return rc

    print("No new mypy errors vs baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

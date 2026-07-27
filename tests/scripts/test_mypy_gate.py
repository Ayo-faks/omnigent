"""Tests for scripts/mypy_gate.py baseline comparison."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts/mypy_gate.py"


def _load():
    spec = importlib.util.spec_from_file_location("mypy_gate", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_mypy_output_fingerprints() -> None:
    mod = _load()
    text = (
        'omnigent/foo.py:10: error: Explicit "Any" is not allowed  [explicit-any]\n'
        "omnigent/foo.py:11: note: something\n"
        "Found 1 error in 1 file\n"
    )
    assert mod.parse_mypy_output(text) == [
        'omnigent/foo.py\texplicit-any\tExplicit "Any" is not allowed'
    ]


def test_new_errors_uses_multiset() -> None:
    mod = _load()
    baseline = ["a\tx\tm", "a\tx\tm"]
    current = ["a\tx\tm", "a\tx\tm", "a\tx\tm"]
    assert mod.new_errors(current, baseline) == ["a\tx\tm"]
    assert mod.new_errors(baseline, current) == []


def test_resolved_errors_do_not_fail() -> None:
    mod = _load()
    baseline = ["old\tx\tm", "keep\ty\tn"]
    current = ["keep\ty\tn"]
    assert mod.new_errors(current, baseline) == []

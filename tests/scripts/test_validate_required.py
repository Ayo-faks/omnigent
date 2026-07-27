"""Tests for .github/scripts/merge-ready/validate-required.py."""

from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github/scripts/merge-ready/validate-required.py"


def _load():
    spec = importlib.util.spec_from_file_location("validate_required", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def vr():
    return _load()


def test_repo_required_matches_defined_checks(vr) -> None:
    """The committed required.sh must stay in sync with live workflow names."""
    required, _ = vr.load_required()
    defined = vr.collect_defined_checks()
    assert vr.validate(required, defined) == []


def test_bogus_required_name_is_rejected(vr, tmp_path: Path) -> None:
    """A stale/typo REQUIRED entry must fail — that is the auto-pass bug."""
    required_sh = tmp_path / "required.sh"
    required_sh.write_text(
        textwrap.dedent(
            """\
            REQUIRED=(
              "DCO"
              "Pre-commit checks"
              "Pytest (this-shard-does-not-exist)"
            )
            ALLOW_SKIP=(
              "Pytest (this-shard-does-not-exist)"
            )
            """
        ),
        encoding="utf-8",
    )
    required, _ = vr.load_required(required_sh)
    defined = vr.collect_defined_checks()
    errors = vr.validate(required, defined)
    assert any("this-shard-does-not-exist" in line for line in errors)


def test_omitted_live_check_is_rejected(vr) -> None:
    """Dropping a live Pytest shard from REQUIRED must fail (omission bug)."""
    required, _ = vr.load_required()
    defined = vr.collect_defined_checks()
    # Remove one live name that is definitely in defined.
    trimmed = [n for n in required if n != "Pytest (codex-parity)"]
    assert "Pytest (codex-parity)" in defined
    errors = vr.validate(trimmed, defined)
    assert any("Pytest (codex-parity)" in line for line in errors)

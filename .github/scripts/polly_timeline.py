"""Summarize where a Polly run's wall clock went, from its process logs.

``omnigent run -p`` prints only the final assistant text, so the process logs
under ``$OMNIGENT_DATA_DIR/logs`` are the only per-turn record. Rather than
parse prose log messages (which change freely), this merges every timestamped
log line and reports the largest gaps between consecutive lines: a long gap is
a stall, and the lines either side of it bracket whatever was slow.

One file can carry two clocks: Omnigent's formatter emits local time-only
("02:12:55"), while httpx/stdlib lines carry a UTC date ("2026-07-14T01:12:53
+0000"). Those are offset by the machine's UTC offset, so interleaving them
invents huge fake gaps. Each file therefore keeps only its dominant stamp
style and reports how many lines that skipped.

Usage: python polly_timeline.py <logs-dir> [top-n]
"""

from __future__ import annotations

import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Omnigent process logs come in three shapes, sometimes in one file:
#   "INFO  08-26 01:20:32.625 logger func | msg"  (level first, MM-DD)
#   "06:19:37 INFO  [logger] msg"                 (time only)
#   "2026-07-14T01:12:53+0000 INFO:logger:msg"    (UTC, full date)
# Each is matched separately so the day scale is never guessed.
_LEVEL = r"(?:TRACE|DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL|FATAL)"
_FRAC = r"(?:[.,](\d{1,6}))?"
_PATTERNS = (
    # level, MM-DD, clock  -> day scale is month*31+day
    ("md", re.compile(rf"^{_LEVEL}\s+(\d{{2}})-(\d{{2}})\s+(\d{{2}}):(\d{{2}}):(\d{{2}}){_FRAC}")),
    # full ISO date -> day scale is the proleptic ordinal
    (
        "iso",
        re.compile(rf"^\[?(\d{{4}})-(\d{{2}})-(\d{{2}})[T ](\d{{2}}):(\d{{2}}):(\d{{2}}){_FRAC}"),
    ),
    # clock only, optionally after a level -> no day scale, wrap heuristic
    ("tod", re.compile(rf"^(?:{_LEVEL}\s+)?(\d{{2}}):(\d{{2}}):(\d{{2}}){_FRAC}")),
)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_DAY = 86400.0


def _stamp(line: str) -> tuple[str, float | None, float] | None:
    """Classify one line's timestamp.

    :returns: ``(kind, day_or_None, seconds_within_day)``, or ``None`` when the
        line carries no timestamp. ``day`` is ``None`` only for ``"tod"``.
    """
    text = _ANSI.sub("", line).lstrip()
    for kind, pattern in _PATTERNS:
        match = pattern.match(text)
        if match is None:
            continue
        groups = match.groups()
        frac = groups[-1]
        micros = int(frac.ljust(6, "0")) / 1_000_000 if frac else 0.0
        if kind == "md":
            month, day, hh, mm, ss = (int(g) for g in groups[:5])
            return kind, float(month * 31 + day), hh * 3600 + mm * 60 + ss + micros
        if kind == "iso":
            year, month, day, hh, mm, ss = (int(g) for g in groups[:6])
            return (
                kind,
                float(datetime(year, month, day, tzinfo=timezone.utc).toordinal()),
                (hh * 3600 + mm * 60 + ss + micros),
            )
        hh, mm, ss = (int(g) for g in groups[:3])
        return kind, None, hh * 3600 + mm * 60 + ss + micros
    return None


def _file_events(path: Path) -> tuple[list[tuple[float, str, str]], int]:
    """Parse one log file into (absolute_seconds, label, text) tuples.

    Keeps only the file's dominant timestamp shape, so two clocks in different
    timezones (or on different day scales) cannot invent gaps. Clock-only
    stamps have no date, so a near-midnight backwards step is read as a wrap.

    :returns: The kept events, and how many stamped lines were skipped as
        belonging to a minority shape.
    """
    label = f"{path.parent.name}/{path.name}"
    parsed: list[tuple[str, float | None, float, str]] = []
    for raw in path.read_text(errors="replace").splitlines():
        stamp = _stamp(raw)
        if stamp is not None:
            kind, day, seconds = stamp
            parsed.append((kind, day, seconds, raw.strip()))
    if not parsed:
        return [], 0

    counts: dict[str, int] = {}
    for kind, _, _, _ in parsed:
        counts[kind] = counts.get(kind, 0) + 1
    dominant = max(counts, key=lambda kind: counts[kind])
    kept = [(day, seconds, text) for kind, day, seconds, text in parsed if kind == dominant]

    events: list[tuple[float, str, str]] = []
    if dominant == "tod":
        offset = 0.0
        previous = None
        for _, seconds, text in kept:
            # Only a near-midnight wrap counts. Any other backwards step is
            # out-of-order interleaving, which the sort below handles; treating
            # it as a new day would invent a 24h gap.
            if previous is not None and previous > 23 * 3600 and seconds < 3600:
                offset += _DAY
            previous = seconds
            events.append((seconds + offset, label, text))
    else:
        for day, seconds, text in kept:
            assert day is not None
            events.append((day * _DAY + seconds, label, text))
    return events, len(parsed) - len(kept)


def _selftest() -> int:
    """Assert the three stamp shapes parse and that gaps come out right."""
    md = _stamp("INFO  08-26 01:20:32.625 logger func | msg")
    assert md is not None and md[0] == "md", md
    assert md[2] == 1 * 3600 + 20 * 60 + 32.625, md

    tod = _stamp("06:19:37 INFO  [logger] msg")
    assert tod is not None and tod[0] == "tod" and tod[1] is None, tod
    assert tod[2] == 6 * 3600 + 19 * 60 + 37, tod

    iso = _stamp("2026-07-14T01:12:53+0000 INFO:httpx:msg")
    assert iso is not None and iso[0] == "iso" and iso[1] is not None, iso

    assert _stamp("no timestamp here") is None
    assert _stamp("Traceback (most recent call last):") is None

    # A file mixing shapes keeps only the dominant one.
    tmp = Path(tempfile.mkdtemp()) / "runner.log"
    tmp.write_text(
        "INFO  08-26 01:00:00.000 a f | one\n"
        "INFO  08-26 01:00:05.000 a f | two\n"
        "2026-07-14T01:12:53+0000 INFO:httpx:minority\n"
    )
    events, skipped = _file_events(tmp)
    assert skipped == 1, skipped
    assert len(events) == 2, events
    assert events[1][0] - events[0][0] == 5.0, events

    # Clock-only wrap: 23:59:59 -> 00:00:01 is 2s, not -86398s.
    wrap = Path(tempfile.mkdtemp()) / "cli.log"
    wrap.write_text("23:59:59 INFO  [a] late\n00:00:01 INFO  [a] early\n")
    wrapped, _ = _file_events(wrap)
    assert wrapped[1][0] - wrapped[0][0] == 2.0, wrapped
    print("selftest OK")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        return _selftest()
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 25
    files = sorted(p for p in root.rglob("*.log") if p.is_file())
    if not files:
        print(f"no .log files under {root}")
        return 0

    # Gaps are computed WITHIN each file. Different files can use different
    # clocks (local time-only vs UTC-dated), so merging their timelines would
    # compare incompatible scales; a stall shows up in whichever file was
    # waiting, which is what we want to see anyway.
    all_gaps: list[tuple[float, tuple[float, str, str], tuple[float, str, str]]] = []
    total_span = 0.0
    print(f"## {len(files)} log file(s)\n")
    for path in files:
        events, skipped = _file_events(path)
        # Threads append out of order; gaps mean nothing unless chronological.
        events.sort(key=lambda event: event[0])
        label = f"{path.parent.name}/{path.name}"
        note = f"  (skipped {skipped} on the minority clock)" if skipped else ""
        span = events[-1][0] - events[0][0] if len(events) > 1 else 0.0
        total_span = max(total_span, span)
        size = path.stat().st_size
        print(f"  {size:>10,}B {len(events):>6} lines {span:>9.1f}s  {label}{note}")
        all_gaps.extend(
            (events[i + 1][0] - events[i][0], events[i], events[i + 1])
            for i in range(len(events) - 1)
        )

    if not all_gaps:
        print("\nnot enough timestamped lines to build a timeline")
        return 0

    all_gaps.sort(key=lambda gap: gap[0], reverse=True)
    shown = [gap for gap in all_gaps[:top_n] if gap[0] >= 1.0]
    covered = sum(gap[0] for gap in shown)
    share = 100 * covered / total_span if total_span else 0
    print(
        f"\n## longest file spans {total_span:.1f}s; top {len(shown)} gap(s) >=1s "
        f"cover {covered:.1f}s ({share:.0f}% of that span)\n"
    )
    for secs, before, after in shown:
        print(f"  {secs:7.1f}s gap  [{before[1]}]")
        print(f"      before {before[2][:100]}")
        print(f"      after  {after[2][:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

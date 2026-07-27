"""OpenClaw/acpx config bridge for ``omnigent setup``.

Reads a user's acpx/OpenClaw agent registry and converts it into Omnigent's
generic ``acp:`` agent entries. The bridge stores only launch commands; each
agent keeps its own authentication.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from omnigent.onboarding.acp_auth import AcpAgentEntry, acp_agents, slugify

SourceKind = Literal["acpx", "openclaw"]


@dataclass(frozen=True)
class OpenClawAgentEntry:
    """One ACP agent discovered in an acpx/OpenClaw config file."""

    name: str
    command: str
    args: tuple[str, ...]
    source: SourceKind
    path: Path

    @property
    def command_line(self) -> str:
        """Return the shell command Omnigent should persist."""
        parts = [self.command, *(shlex.quote(arg) for arg in self.args)]
        return " ".join(part for part in parts if part).strip()


@dataclass(frozen=True)
class OpenClawConfigError:
    """A config file existed but could not be parsed or did not match schema."""

    path: Path
    message: str


@dataclass(frozen=True)
class OpenClawDiscovery:
    """Result of reading the supported OpenClaw/acpx config locations."""

    agents: tuple[OpenClawAgentEntry, ...]
    errors: tuple[OpenClawConfigError, ...] = ()


def default_config_paths(home: Path | None = None) -> tuple[Path, Path]:
    """Return the default ``(acpx, OpenClaw)`` config paths."""
    root = Path.home() if home is None else home
    return root / ".acpx" / "config.json", root / ".openclaw" / "openclaw.json"


def discover_openclaw_agents(
    *,
    acpx_path: Path | None = None,
    openclaw_path: Path | None = None,
) -> OpenClawDiscovery:
    """Read supported acpx/OpenClaw config files and return normalized agents.

    Missing files are ignored. Malformed files are reported as soft errors so
    setup can show a hint without blocking imports from the other location.
    """
    default_acpx_path, default_openclaw_path = default_config_paths()
    paths: tuple[tuple[SourceKind, Path], ...] = (
        ("acpx", acpx_path or default_acpx_path),
        ("openclaw", openclaw_path or default_openclaw_path),
    )
    agents: list[OpenClawAgentEntry] = []
    errors: list[OpenClawConfigError] = []
    for source, path in paths:
        if not path.exists():
            continue
        try:
            raw = _load_config(path, json5=source == "openclaw")
            agents.extend(_extract_agents(raw, source=source, path=path))
        except ValueError as exc:
            errors.append(OpenClawConfigError(path=path, message=str(exc)))
    return OpenClawDiscovery(agents=tuple(agents), errors=tuple(errors))


def openclaw_agents_to_acp_entries(
    agents: tuple[OpenClawAgentEntry, ...] | list[OpenClawAgentEntry],
) -> list[AcpAgentEntry]:
    """Translate normalized OpenClaw/acpx agents into ACP config entries."""
    entries: list[AcpAgentEntry] = []
    seen: dict[str, int] = {}
    for agent in agents:
        base = slugify(agent.name)
        count = seen.get(base, 0) + 1
        seen[base] = count
        slug = base if count == 1 else f"{base}-{count}"
        entries.append(AcpAgentEntry(slug=slug, name=agent.name, command=agent.command_line))
    return entries


def merge_imported_acp_entries(
    imported: tuple[AcpAgentEntry, ...] | list[AcpAgentEntry],
    *,
    existing: list[AcpAgentEntry] | None = None,
) -> tuple[list[AcpAgentEntry], list[AcpAgentEntry]]:
    """Append imported ACP entries, skipping slugs that already exist.

    Returns ``(merged, added)``. The check is slug-based so rerunning the import
    is idempotent and preserves any existing user-authored ACP agents.
    """
    current = list(acp_agents() if existing is None else existing)
    seen = {entry.slug for entry in current}
    added: list[AcpAgentEntry] = []
    for entry in imported:
        if entry.slug in seen:
            continue
        current.append(entry)
        added.append(entry)
        seen.add(entry.slug)
    return current, added


def _load_config(path: Path, *, json5: bool) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as json_exc:
        if not json5:
            raise ValueError(f"invalid JSON: {json_exc.msg}") from json_exc
    try:
        return yaml.safe_load(_quote_json5_keys(_strip_json5_comments(text)))
    except yaml.YAMLError as yaml_exc:
        raise ValueError(f"invalid JSON5: {yaml_exc}") from yaml_exc


def _extract_agents(raw: Any, *, source: SourceKind, path: Path) -> list[OpenClawAgentEntry]:
    if not isinstance(raw, dict):
        raise ValueError("config root must be an object")

    raw_agents: Any
    if source == "acpx":
        raw_agents = raw.get("agents")
    else:
        plugins = _object(raw.get("plugins"))
        entries = _object(plugins.get("entries"))
        acpx = _object(entries.get("acpx"))
        config = _object(acpx.get("config"))
        raw_agents = config.get("agents")
    if raw_agents is None:
        return []
    if not isinstance(raw_agents, dict):
        raise ValueError("agents must be an object mapping name to config")

    agents: list[OpenClawAgentEntry] = []
    for name, config in raw_agents.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(config, dict):
            continue
        command = config.get("command")
        if not isinstance(command, str) or not command.strip():
            continue
        args = _normalize_args(config.get("args"))
        agents.append(
            OpenClawAgentEntry(
                name=name.strip(),
                command=command.strip(),
                args=args,
                source=source,
                path=path,
            )
        )
    return agents


def _normalize_args(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list):
        return tuple(str(item) for item in raw if item is not None)
    return ()


def _object(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _strip_json5_comments(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments while preserving quoted strings."""
    out: list[str] = []
    i = 0
    quote: str | None = None
    escaped = False
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if quote is not None:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            i += 1
            continue
        if ch in {'"', "'"}:
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = min(len(text), i + 2)
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _quote_json5_keys(text: str) -> str:
    """Quote simple JSON5 object keys so PyYAML can load OpenClaw configs."""
    out: list[str] = []
    i = 0
    quote: str | None = None
    escaped = False
    while i < len(text):
        ch = text[i]
        if quote is not None:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            i += 1
            continue
        if ch in {'"', "'"}:
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch in "{,":
            out.append(ch)
            i += 1
            while i < len(text) and text[i].isspace():
                out.append(text[i])
                i += 1
            start = i
            if i < len(text) and (text[i].isalpha() or text[i] in "_$"):
                i += 1
                while i < len(text) and (text[i].isalnum() or text[i] in "_$"):
                    i += 1
                end = i
                j = i
                while j < len(text) and text[j].isspace():
                    j += 1
                if j < len(text) and text[j] == ":":
                    out.append(f'"{text[start:end]}"')
                    continue
            out.append(text[start:i])
            continue
        out.append(ch)
        i += 1
    return "".join(out)

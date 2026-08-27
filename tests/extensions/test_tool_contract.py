from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import omnigent.extensions.registry as registry
from omnigent.extensions import (
    EXTENSION_API_VERSION,
    EnablementScope,
    ExtensionEntrypoints,
    ExtensionManifest,
    RunnerPermission,
    ToolContribution,
    check_extension_package,
)
from omnigent.extensions.assets import ExtensionAssetError
from omnigent.extensions.enablement import resolve_enabled_tools
from omnigent.extensions.tool_names import TOOL_NAME_RE, extension_tool_prefix
from omnigent.tool_namespaces import EXTENSION_TOOL_MARKER
from omnigent.tools.base import TOOL_NAME_RE as RUNTIME_TOOL_NAME_RE
from omnigent.tools.builtins import BUILTIN_NAMES

_ACME_PREFIX = extension_tool_prefix("acme.review")


def _tool(
    extension_id: str = "acme.review",
    *,
    tool_name: str | None = None,
    enablement: EnablementScope = EnablementScope.DEPLOYMENT,
) -> ToolContribution:
    return ToolContribution(
        id=f"{extension_id}.review-tool",
        tool_name=tool_name or f"{extension_tool_prefix(extension_id)}review",
        title="Review",
        description="Review the current workspace.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        runner_permissions=frozenset({RunnerPermission.FILESYSTEM_READ}),
        enablement=enablement,
    )


def _manifest(
    extension_id: str = "acme.review",
    *,
    tool: ToolContribution | None = None,
) -> ExtensionManifest:
    return ExtensionManifest(
        id=extension_id,
        display_name="Review",
        distribution=f"{extension_id}-distribution",
        version="1.0.0",
        requires_omnigent=">=0.11,<1",
        extension_api=EXTENSION_API_VERSION,
        entrypoints=ExtensionEntrypoints(runner="acme_review.runner:activate"),
        tools=(tool or _tool(extension_id),),
    )


def test_accepts_namespaced_tool_contract() -> None:
    registry.validate_manifest(_manifest())


@pytest.mark.parametrize(
    ("tool", "message"),
    [
        (_tool(tool_name="review"), f"must start with '{_ACME_PREFIX}'"),
        (_tool(tool_name="acme.review.tool"), "must match"),
        (
            replace(_tool(), input_schema=[]),  # type: ignore[arg-type]
            "input_schema must be an object",
        ),
        (
            replace(_tool(), input_schema={"type": "array"}),
            "input_schema type must be 'object'",
        ),
        (
            replace(_tool(), runner_permissions=frozenset({"root"})),  # type: ignore[arg-type]
            "unsupported runner permissions",
        ),
        (
            replace(_tool(), enablement="global"),  # type: ignore[arg-type]
            "unsupported enablement scope",
        ),
    ],
)
def test_rejects_invalid_tool_contract(tool: ToolContribution, message: str) -> None:
    with pytest.raises(registry.ExtensionValidationError, match=message):
        registry.validate_manifest(_manifest(tool=tool))


def test_requires_runner_entrypoint_and_tools_together() -> None:
    with pytest.raises(registry.ExtensionValidationError, match="declared together"):
        registry.validate_manifest(replace(_manifest(), entrypoints=ExtensionEntrypoints()))
    with pytest.raises(registry.ExtensionValidationError, match="declared together"):
        registry.validate_manifest(
            replace(
                _manifest(),
                tools=(),
                entrypoints=ExtensionEntrypoints(runner="acme_review.runner:activate"),
            )
        )


def test_rejects_invalid_runner_entrypoint() -> None:
    with pytest.raises(registry.ExtensionValidationError, match=r"package\.module:factory"):
        registry.validate_manifest(
            replace(_manifest(), entrypoints=ExtensionEntrypoints(runner="shell command"))
        )


def test_tool_prefix_encoding_is_injective_and_claimed() -> None:
    first_id = "acme.foo-bar"
    second_id = "acme.foo.bar"
    assert extension_tool_prefix(first_id) != extension_tool_prefix(second_id)
    first = _manifest(first_id)
    second = _manifest(second_id)
    registry.validate_manifest(first)
    registry.validate_manifest(second)

    assert registry._collision_errors((), [("first", first, None), ("second", second, None)]) == {}
    assert f"tool-prefix:{extension_tool_prefix(first_id)}" in registry._manifest_claims(first)


def test_enablement_requires_deployment_and_declared_scope_allowlists() -> None:
    tools = (
        _tool(enablement=EnablementScope.DEPLOYMENT),
        replace(
            _tool(),
            id="acme.review.user-tool",
            tool_name=f"{_ACME_PREFIX}user",
            enablement=EnablementScope.USER,
        ),
        replace(
            _tool(),
            id="acme.review.agent-tool",
            tool_name=f"{_ACME_PREFIX}agent",
            enablement=EnablementScope.AGENT,
        ),
        replace(
            _tool(),
            id="acme.review.session-tool",
            tool_name=f"{_ACME_PREFIX}session",
            enablement=EnablementScope.SESSION,
        ),
    )
    manifest = replace(_manifest(), tools=tools)
    deployment = {tool.tool_name for tool in tools}

    assert resolve_enabled_tools(
        [manifest],
        deployment_allow=deployment,
        user_allow={f"{_ACME_PREFIX}user"},
        agent_allow={f"{_ACME_PREFIX}agent"},
        session_allow={f"{_ACME_PREFIX}session"},
    ) == frozenset(deployment)
    assert resolve_enabled_tools([manifest], deployment_allow=deployment) == frozenset(
        {f"{_ACME_PREFIX}review"}
    )
    assert resolve_enabled_tools([manifest], deployment_allow=()) == frozenset()


def test_source_conformance_checks_runner_module_ownership(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    package_root = project_root / "acme_review"
    package_root.mkdir(parents=True)
    (project_root / "pyproject.toml").write_text(
        '[project]\nname="acme.review-distribution"\nversion="1.0.0"\n',
        encoding="utf-8",
    )
    (package_root / "runner.py").write_text("def activate(): pass\n", encoding="utf-8")

    assert (
        check_extension_package(
            _manifest(),
            package_root=package_root,
            project_root=project_root,
        )
        is None
    )
    wrong = replace(
        _manifest(),
        entrypoints=ExtensionEntrypoints(runner="other.runner:activate"),
    )
    with pytest.raises(ExtensionAssetError, match="not owned"):
        check_extension_package(
            wrong,
            package_root=package_root,
            project_root=project_root,
        )


def test_extension_tool_name_contract_stays_disjoint_from_runtime_builtins() -> None:
    assert TOOL_NAME_RE.pattern == RUNTIME_TOOL_NAME_RE.pattern
    assert not any(name.startswith(EXTENSION_TOOL_MARKER) for name in BUILTIN_NAMES)
    assert extension_tool_prefix("acme.review") == "ext__acme_d_review__"

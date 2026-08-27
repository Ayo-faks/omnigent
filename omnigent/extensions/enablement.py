"""Pure enablement resolution for extension-contributed tools."""

from __future__ import annotations

from collections.abc import Iterable

from omnigent.extensions.api import EnablementScope, ExtensionManifest


def resolve_enabled_tools(
    manifests: Iterable[ExtensionManifest],
    *,
    deployment_allow: Iterable[str],
    user_allow: Iterable[str] = (),
    agent_allow: Iterable[str] = (),
    session_allow: Iterable[str] = (),
) -> frozenset[str]:
    """Resolve enabled tool names from deployment and scope-specific allowlists.

    The deployment allowlist is always required. Tools narrower than deployment
    additionally require approval at their declared minimum scope.
    """
    deployment = frozenset(deployment_allow)
    scope_allows = {
        EnablementScope.DEPLOYMENT: deployment,
        EnablementScope.USER: frozenset(user_allow),
        EnablementScope.AGENT: frozenset(agent_allow),
        EnablementScope.SESSION: frozenset(session_allow),
    }
    return frozenset(
        tool.tool_name
        for manifest in manifests
        for tool in manifest.tools
        if tool.tool_name in deployment and tool.tool_name in scope_allows[tool.enablement]
    )

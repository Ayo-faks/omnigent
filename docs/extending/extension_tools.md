# Extension tools

Extension packages may declare runner-hosted tool schemas. Declaration is
separate from execution: schemas are validated and visible in the extension
catalog, while runner activation and `ToolManager` wiring are applied only when
the tool is enabled.

```python
from omnigent.extensions import (
    EnablementScope,
    RunnerPermission,
    ToolContribution,
)

ToolContribution(
    id="acme.review.review-tool",
    tool_name="ext__acme_d_review__review",
    title="Review workspace",
    description="Review files in the current workspace.",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    runner_permissions=frozenset({RunnerPermission.FILESYSTEM_READ}),
    enablement=EnablementScope.AGENT,
)
```

The manifest must also declare a runner factory:

```python
ExtensionEntrypoints(runner="acme_review.runner:activate")
```

The runner module must live in the same verified Python package as the manifest
entry point. A manifest must declare both the runner entrypoint and at least one
tool, or neither.

## Names

Tool names use an injective, reserved namespace derived from the extension ID.
Dots become `_d_`, hyphens become `_h_`, and `ext__` reserves the source from
MCP and local tools:

```text
acme.review       -> ext__acme_d_review__review
acme.foo-bar      -> ext__acme_d_foo_h_bar__run
```

The local suffix is lowercase snake case. MCP server and agent-local tool names
cannot begin with `ext__`.

## Permissions and enablement

Runner permissions are declarative and start with a deliberately small set:
`process.spawn`, `fs.read`, and `net.http`. They describe requested authority;
a subprocess alone is not an OS sandbox.

Every tool requires deployment allowlisting. A tool whose minimum enablement is
`user`, `agent`, or `session` additionally requires its name in that exact
scope's allowlist. Broader-scope approval does not imply a narrower scope. This
explicit intersection prevents an extension from widening its own availability.

```bash
omni extensions tools
omni extensions doctor acme.review
```

The catalog and these commands are declarative; they do not activate runner
code merely to display schemas. For an extension that contributes both UI and
tools, a broken browser bundle suppresses only its pages/slots; runner tool
metadata remains available because it has an independent execution boundary.

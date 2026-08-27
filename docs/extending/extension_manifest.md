# Extension manifest

An extension registers a lightweight factory under the
`omnigent.extensions` Python entry-point group:

```toml
[project.entry-points."omnigent.extensions"]
review = "acme_review.plugin:get_manifest"
```

The factory returns immutable values from `omnigent.extensions` and should not
import runtime or UI implementation modules.

```python
from omnigent.extensions import (
    EXTENSION_API_VERSION,
    EnablementScope,
    ExtensionEntrypoints,
    ExtensionManifest,
    ExtensionPermission,
    PageContribution,
    PrimaryNavigationContribution,
    RunnerPermission,
    SlotId,
    SlotItemContribution,
    SlotItemKind,
    ToolContribution,
)


def get_manifest() -> ExtensionManifest:
    return ExtensionManifest(
        id="acme.review",
        display_name="Acme Review",
        distribution="omnigent-acme-review",
        version="1.0.0",
        requires_omnigent=">=0.11,<1",
        extension_api=EXTENSION_API_VERSION,
        entrypoints=ExtensionEntrypoints(
            browser="dist/extension.js",
            browser_css="dist/extension.css",
            runner="acme_review.runner:activate",
        ),
        permissions=frozenset({ExtensionPermission.NAVIGATION}),
        pages=(
            PageContribution(
                id="acme.review.dashboard",
                title="Reviews",
                route="reviews",
                view="dashboard",
            ),
        ),
        primary_navigation=(
            PrimaryNavigationContribution(
                id="acme.review.primary-nav",
                label="Reviews",
                page="acme.review.dashboard",
                icon="search",
                order=500,
            ),
        ),
        slot_items=(
            SlotItemContribution(
                id="acme.review.composer-action",
                slot=SlotId.COMPOSER_ACTIONS,
                kind=SlotItemKind.ACTION,
                label="Review session",
                page="acme.review.dashboard",
            ),
        ),
        tools=(
            ToolContribution(
                id="acme.review.review-tool",
                tool_name="ext__acme_d_review__review",
                title="Review workspace",
                description="Review files in the current workspace.",
                input_schema={"type": "object"},
                runner_permissions=frozenset({RunnerPermission.FILESYSTEM_READ}),
                enablement=EnablementScope.AGENT,
            ),
        ),
    )
```

## Rules

- IDs are lowercase, publisher-qualified, immutable, and globally collision
  checked. Contribution IDs begin with the owning extension ID.
- Page routes contain exactly one safe segment and are always namespaced.
- The manifest distribution/version must match installed package metadata.
- `requires_omnigent` is a PEP 440 release-line range.
- Browser paths are fixed to `dist/extension.js` and optional
  `dist/extension.css` inside the entry point's verified import package.
- Runner entrypoints use `package.module:factory`, live in the manifest entry
  point's verified package, and are declared together with tools.
- Tool names use the reserved injective `ext__...__tool` namespace, schemas are
  JSON objects, and permissions/enablement use closed enums. See
  [Extension tools](extension_tools.md).
- Slot items use a supported slot/kind pairing, reference a page owned by the
  same extension, and cannot reorder core UI. See
  [Extension UI slots](extension_ui_slots.md).
- Built-ins are reserved. All community extensions participating in a collision
  are disabled deterministically.
- One invalid field rejects the whole extension; one rejected extension does not
  prevent others from loading.

## Compatibility

The extension API major is independent of the Omnigent package version. V1
changes are additive; authors must ignore unknown catalog fields. Breaking
changes require a new extension API major. Deprecations name the Omnigent
release in which removal is planned and remain supported through the stated
window.

Extension packages can run the same manifest and bundle checks in their own
focused tests without starting a server:

```python
from pathlib import Path
from omnigent.extensions import check_extension_package

check_extension_package(
    get_manifest(),
    project_root=Path(__file__).parents[2],
    package_root=Path(__file__).parent,
)
```

`activation_events`, `when`, and command records are reserved metadata only in
V1. Do not rely on them until a later API explicitly documents execution.

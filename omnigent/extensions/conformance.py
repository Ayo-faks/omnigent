"""Small conformance entry point for extension package tests."""

from __future__ import annotations

import importlib.metadata
import importlib.util
from pathlib import Path

import tomllib
from packaging.utils import canonicalize_name
from packaging.version import Version

from omnigent.extensions.api import ExtensionManifest
from omnigent.extensions.assets import ExtensionAssetError, ResolvedBundle, resolve_bundle
from omnigent.extensions.registry import validate_manifest


def _validate_project_metadata(manifest: ExtensionManifest, project_root: Path) -> None:
    project_file = project_root / "pyproject.toml"
    try:
        project = tomllib.loads(project_file.read_text(encoding="utf-8"))["project"]
        distribution = str(project["name"])
        version = str(project["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise ExtensionAssetError(f"could not read extension project metadata: {exc}") from exc
    if canonicalize_name(distribution) != canonicalize_name(manifest.distribution):
        raise ExtensionAssetError("manifest distribution does not match pyproject.toml")
    if Version(version) != Version(manifest.version):
        raise ExtensionAssetError("manifest version does not match pyproject.toml")


def _validate_installed_metadata(manifest: ExtensionManifest, package: str) -> None:
    try:
        distribution = importlib.metadata.distribution(manifest.distribution)
    except importlib.metadata.PackageNotFoundError as exc:
        raise ExtensionAssetError(
            f"installed distribution {manifest.distribution!r} is unavailable"
        ) from exc
    if Version(distribution.version) != Version(manifest.version):
        raise ExtensionAssetError("manifest version does not match installed distribution")
    top_level = distribution.read_text("top_level.txt") or ""
    if package not in {line.strip() for line in top_level.splitlines()}:
        raise ExtensionAssetError("asset package is not owned by the manifest distribution")


def _validate_runner_entrypoint(
    manifest: ExtensionManifest,
    *,
    package: str | None,
    package_root: Path | None,
) -> None:
    runner = manifest.entrypoints.runner
    if runner is None:
        return
    module, _separator, _factory = runner.partition(":")
    runner_package = module.split(".", 1)[0]
    expected_package = package or (package_root.name if package_root is not None else None)
    if expected_package is None or runner_package != expected_package:
        raise ExtensionAssetError("runner entrypoint is not owned by the extension package")
    if package is not None and importlib.util.find_spec(module) is None:
        raise ExtensionAssetError(f"runner module {module!r} is unavailable")
    if package_root is not None:
        target = package_root.joinpath(*module.split(".")[1:])
        if not target.with_suffix(".py").is_file() and not (target / "__init__.py").is_file():
            raise ExtensionAssetError(f"runner module {module!r} is unavailable")


def check_extension_package(
    manifest: ExtensionManifest,
    *,
    package: str | None = None,
    package_root: Path | None = None,
    project_root: Path | None = None,
) -> ResolvedBundle | None:
    """Validate a manifest, distribution identity, and optional browser bundle."""
    validate_manifest(manifest)
    if package_root is not None:
        if project_root is None:
            raise ExtensionAssetError("project_root is required with package_root")
        _validate_project_metadata(manifest, project_root)
    elif package is not None:
        _validate_installed_metadata(manifest, package)
    _validate_runner_entrypoint(manifest, package=package, package_root=package_root)
    if manifest.entrypoints.browser is None:
        return None
    return resolve_bundle(manifest, package=package, root_override=package_root)

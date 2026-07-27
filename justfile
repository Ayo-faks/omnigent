default:
    @just --list

export FASTLANE_SKIP_UPDATE_CHECK := "1"

# iOS device override (default: iPhone 17 Pro)
DEVICE := env("OMNIGENT_IOS_SIMULATOR", "iPhone 17 Pro")

# --- uv Python env ---

_check-uv:
    uv run --no-sync ruff --version
    uv run --no-sync pre-commit --version

# Sync from the committed lockfile without rewriting it.
# `--frozen` installs pinned versions and never updates uv.lock — required
# because a corporate proxy in ~/.config/uv makes `uv sync --locked` treat
# public-PyPI registry URLs as stale (and forcing UV_INDEX_URL=pypi.org
# breaks machines that can only reach the proxy). CI still gates freshness
# with `uv sync --locked`. `--inexact` keeps optional harness extras
# (cursor / copilot / antigravity) that `omnigent setup` may have installed;
# `--extra all` only adds databricks-sdk, not those.
_ensure-uv:
    #!/usr/bin/env bash
    set -euo pipefail
    set +e
    uv sync --frozen --inexact --extra all --extra dev
    status=$?
    set -e
    if [[ "${status}" -eq 0 ]]; then
        exit 0
    fi
    echo "" >&2
    echo "error: \`uv sync --frozen\` failed (exit ${status})." >&2
    echo "  Recovery:" >&2
    echo "    • Lock rewritten by an older \`just ensure\` (proxy URLs)?" >&2
    echo "        git checkout -- uv.lock && just normalize-locks" >&2
    echo "    • pyproject.toml changed and the lock needs re-resolving?" >&2
    echo "        just relock && just normalize-locks" >&2
    echo "  Then re-run: just ensure" >&2
    exit "${status}"

# Intentional re-resolve (updates uv.lock). Day-to-day setup uses
# `_ensure-uv` / `just ensure` with `--frozen` instead.
[group('setup')]
relock:
    uv sync --inexact --extra all --extra dev
    @echo "uv.lock may point at your local index; run \`just normalize-locks\` before committing."

# --- iOS Ruby dependencies ---

_check-ios:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ "$(uname -s)" != "Darwin" ]]; then
        echo "Skipping iOS check (not macOS)."
        exit 0
    fi
    if ! command -v bundle >/dev/null 2>&1; then
        echo "Skipping iOS check (Bundler not found)."
        exit 0
    fi
    cd web/ios && bundle check

_ensure-ios:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ "$(uname -s)" != "Darwin" ]]; then
        echo "Skipping iOS setup (not macOS)."
        exit 0
    fi
    if ! command -v bundle >/dev/null 2>&1; then
        echo "Skipping iOS setup (Bundler not found)."
        exit 0
    fi
    cd web/ios && (bundle check || bundle install)

# --- omnidev Rust dev tool ---

_install-omnidev:
    cargo install --path dev/omnidev --locked --force

_check-omnidev:
    command -v omnidev >/dev/null 2>&1

_ensure-omnidev:
    command -v omnidev >/dev/null 2>&1 || just _install-omnidev

# --- Aggregate setup checks / installs ---

[group('setup')]
check: _check-uv _check-ios _check-omnidev

[group('setup')]
ensure: _ensure-uv _ensure-ios _ensure-omnidev

# --- Local dev ---

[group('dev')]
dev: _ensure-omnidev
    omnidev

[group('dev')]
dev-mobile: _ensure-omnidev
    omnidev --vite-host 0.0.0.0 --trust-lan-origins

# --- Mobile builds ---

[group('mobile')]
run-ios: _ensure-ios
    cd web/ios && bundle exec fastlane simulator device:"{{ DEVICE }}"

[group('mobile')]
run-android:
    cd web/android && ./gradlew installDebug runDebug

[group('mobile')]
android-reverse:
    cd web/android && ./gradlew reverseProxy

# --- Electron desktop app ---

_ensure-web:
    cd web && test -d node_modules || npm install --no-audit --no-fund

_ensure-electron:
    cd web/electron && test -d node_modules || npm install --no-audit --no-fund

[group('electron')]
electron-dev: _ensure-web _ensure-electron
    npm --prefix web/electron run dev

[group('electron')]
electron-build: _ensure-web _ensure-electron
    npm --prefix web/electron run build

# --- Lint ---

[group('lint')]
lint: _ensure-uv
    uv run --no-sync pre-commit run

[group('lint')]
lint-all: _ensure-uv
    uv run --no-sync pre-commit run --all-files

# --- Lockfile maintenance ---

# Fixers exit 1 when they rewrite (pre-commit convention). Treat that as
# success here; real errors use exit code 2+ from the scripts.
# Always `--no-sync`: a bare `uv run` would re-resolve against the local
# index and rewrite uv.lock (undoing `--frozen` in `_ensure-uv`).
[group('lint')]
normalize-locks: _ensure-uv
    #!/usr/bin/env bash
    set -euo pipefail
    run_fixer() {
        local ec=0
        uv run --no-sync "$@" || ec=$?
        if [[ "${ec}" -eq 0 || "${ec}" -eq 1 ]]; then
            return 0
        fi
        return "${ec}"
    }
    run_fixer scripts/normalize_package_lock_registry.py \
        web/package-lock.json web/electron/package-lock.json editors/vscode/package-lock.json
    run_fixer scripts/normalize_uv_lock_registry.py uv.lock

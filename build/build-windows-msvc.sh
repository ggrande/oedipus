#!/usr/bin/env bash
set -euo pipefail

export CARGO_TARGET_DIR=/out/target-0171
export XWIN_CACHE_DIR=/out/xwin-native
export XWIN_ARCH=x86_64
export RUST_MIN_STACK=16777216
export CARGO_BUILD_JOBS=1

patch=/builder/patches/steward-cas-child-prefix-0171.patch
export CODEX_STEWARD_PATCH_SHA256
CODEX_STEWARD_PATCH_SHA256=$(sha256sum "$patch" | cut -d ' ' -f 1)

source_dir="/out/source-0171-$CODEX_STEWARD_PATCH_SHA256"
mkdir -p /out/cargo-home/git /out/cargo-home/registry "$source_dir"
if [ ! -e /usr/local/cargo/git ]; then ln -s /out/cargo-home/git /usr/local/cargo/git; fi
if [ ! -e /usr/local/cargo/registry ]; then ln -s /out/cargo-home/registry /usr/local/cargo/registry; fi

cd "$source_dir"
if ! git apply --reverse --check --exclude=codex-rs/Cargo.lock "$patch" 2>/dev/null; then
    git --git-dir=/checkout/.git archive 581161ab41e643d942e1e1b5f561034060bf18e3 | tar -xf -
    git apply --check "$patch"
    git apply "$patch"
fi

# Use the Windows MSVC V8 artifact and checksum manifest from the pinned source.
v8_cache=/out/v8-152.2.0-msvc
v8_profile=ptrcomp_sandbox_release_x86_64-pc-windows-msvc
v8_archive="rusty_v8_$v8_profile.lib.gz"
v8_bindings="src_binding_$v8_profile.rs"
v8_manifest="rusty_v8_$v8_profile.sha256"
v8_release=https://github.com/openai/codex/releases/download/rusty-v8-v152.2.0
mkdir -p "$v8_cache"
for asset in "$v8_manifest" "$v8_archive" "$v8_bindings"; do
    if [ ! -f "$v8_cache/$asset" ]; then
        curl -fsSL --retry 3 "$v8_release/$asset" -o "$v8_cache/$asset.tmp"
        mv "$v8_cache/$asset.tmp" "$v8_cache/$asset"
    fi
done
expected_manifest="$(awk -v name="$v8_manifest" '$2 == name {print $1}' third_party/v8/rusty_v8_152_2_0_release_manifests.sha256)"
printf '%s  %s\n' "$expected_manifest" "$v8_cache/$v8_manifest" | sha256sum --check
(cd "$v8_cache" && tr -d '\r' < "$v8_manifest" | sha256sum --check)
export RUSTY_V8_ARCHIVE="$v8_cache/$v8_archive"
export RUSTY_V8_SRC_BINDING_PATH="$v8_cache/$v8_bindings"
rustup toolchain install 1.99.1 --profile minimal --target x86_64-pc-windows-msvc --no-self-update

cd codex-rs
# SQLx hashes the source bytes. The packaged Windows backend embeds its
# state migrations with CRLF, including the separate thread history store.
python3 - <<'PY'
from pathlib import Path

state = Path("state")
for folder in (state / "migrations", *sorted(state.glob("*_migrations"))):
    for migration in folder.glob("*.sql"):
        original = migration.read_bytes()
        windows = original.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        if original != windows:
            migration.write_bytes(windows)
PY
# The release tag stamps Cargo.toml. Cargo resolves all 174 local 0.0.0
# lockfile entries to the tag version with no external dependency changes.
sed -i 's/^version = "0\.0\.0"$/version = "0.171.0-alpha.13"/' Cargo.lock
cargo +1.99.1 xwin build --locked --profile dev-small --target x86_64-pc-windows-msvc -p codex-cli --bin codex
cp "$CARGO_TARGET_DIR/x86_64-pc-windows-msvc/dev-small/codex.exe" /deliver/.codex-childfix-0171-crlf.exe.tmp
mv /deliver/.codex-childfix-0171-crlf.exe.tmp /deliver/codex-childfix-0171-crlf.exe
sha256sum /deliver/codex-childfix-0171-crlf.exe

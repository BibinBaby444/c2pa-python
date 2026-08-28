# Building the Patched c2pa-python

This is a fork of [contentauth/c2pa-python](https://github.com/contentauth/c2pa-python) with patches for CAWG identity assertion support (DynamicAssertion callback API, multi-identity support, and settings-based signer fixes).

## Quick Start

```bash
# 1. Install Rust (one-time, ~2 min)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"

# 2. Clone with the c2pa-rs submodule
git clone --recurse-submodules https://github.com/mstattma/c2pa-python.git
cd c2pa-python
git checkout feat/dynamic-assertion-callback

# 3. Build the native library from source
python build_native.py

# 4. Install the package
pip install -e .
```

## What Gets Built

The `build_native.py` script:

1. Finds `cargo` (Rust compiler) on your PATH or in `~/.cargo/bin/`
2. Builds the `c2pa-c-ffi` crate from the `c2pa-rs/` git submodule
3. Copies the resulting native library to `src/c2pa/libs/`:
   - Linux: `libc2pa_c.so`
   - macOS: `libc2pa_c.dylib`
   - Windows: `c2pa_c.dll`

First build takes ~3-5 minutes (compiles all dependencies). Incremental rebuilds take ~30 seconds.

## Platform Support

| Platform | Architecture | Tested |
|----------|-------------|--------|
| Linux | x86_64 | Yes |
| Linux | aarch64 | No (should work) |
| macOS | ARM64 (Apple Silicon) | No (should work) |
| macOS | x86_64 | No (should work) |
| Windows | x86_64 | No (should work) |

The build uses Rust's cross-platform support via `cargo build`. No platform-specific configuration is needed.

## Troubleshooting

### `cargo: command not found`

Install Rust:
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
```

### Build fails with OpenSSL errors (Linux)

Install OpenSSL development headers:
```bash
# Debian/Ubuntu
sudo apt-get install libssl-dev pkg-config

# Fedora/RHEL
sudo dnf install openssl-devel pkg-config
```

### Submodule is empty

```bash
git submodule update --init --recursive
```

### Rebuild after updating c2pa-rs

```bash
cd c2pa-rs
git pull origin feat/dynamic-assertion-ffi
cd ..
python build_native.py
pip install -e .
```

## How It Works

This fork bundles a patched version of `c2pa-rs` as a git submodule. The patches add:

- **C FFI**: `c2pa_signer_add_dynamic_assertion()` — allows Python to register callbacks invoked during C2PA manifest signing with the `PartialClaim` (assertion hashes)
- **SDK**: Support for multiple `DynamicAssertion`s with the same label (e.g. two `cawg.identity` assertions)
- **SDK**: `__N` suffix stripping in `cawg_x509_signer` referenced assertion matching

These patches enable the [stardustproof-c2pa-signer-vibe](https://github.com/mstattma/stardustproof-c2pa-signer-vibe) library to produce CAWG identity assertions with correct `referenced_assertions` hashes during manifest signing.

## Relationship to Upstream

This fork is intended as a staging area for patches that will be submitted as PRs to:
- [contentauth/c2pa-rs](https://github.com/contentauth/c2pa-rs) — Rust SDK and C FFI
- [contentauth/c2pa-python](https://github.com/contentauth/c2pa-python) — Python bindings

Until the PRs are accepted, this fork provides the patched functionality.

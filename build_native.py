#!/usr/bin/env python3
"""Build the patched c2pa-rs native library from the git submodule.

Usage:
    # One-time setup
    git submodule update --init --recursive
    
    # Build native library (requires Rust toolchain)
    python build_native.py
    
    # Then install the package
    pip install -e .

This script builds the c2pa-c-ffi crate from the c2pa-rs submodule
and copies the resulting native library to src/c2pa/libs/.
"""

import subprocess
import shutil
import sys
import platform
import os
from pathlib import Path

C2PA_RS_DIR = Path(__file__).parent / "c2pa-rs"
LIBS_DIR = Path(__file__).parent / "src" / "c2pa" / "libs"

NATIVE_LIB_NAME = {
    "linux": "libc2pa_c.so",
    "darwin": "libc2pa_c.dylib",
    "win32": "c2pa_c.dll",
}


def main():
    # Check submodule
    cargo_toml = C2PA_RS_DIR / "c2pa_c_ffi" / "Cargo.toml"
    if not cargo_toml.exists():
        print("ERROR: c2pa-rs submodule not found. Run:")
        print("  git submodule update --init --recursive")
        sys.exit(1)

    # Find cargo
    cargo = shutil.which("cargo")
    if cargo is None:
        home = Path.home() / ".cargo" / "bin" / "cargo"
        if home.exists():
            cargo = str(home)
        else:
            print("ERROR: Rust toolchain not found. Install via:")
            print("  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh")
            sys.exit(1)

    lib_name = NATIVE_LIB_NAME.get(sys.platform)
    if lib_name is None:
        print(f"ERROR: Unsupported platform: {sys.platform}")
        sys.exit(1)

    print(f"Building c2pa-c-ffi ({platform.system()} {platform.machine()})...")
    print(f"  Source: {C2PA_RS_DIR}")
    print(f"  Cargo:  {cargo}")
    print()

    result = subprocess.run(
        [cargo, "build", "--release", "--locked", "-p", "c2pa-c-ffi", "--features", "file_io"],
        cwd=str(C2PA_RS_DIR),
        timeout=int(os.environ.get("C2PA_CARGO_BUILD_TIMEOUT_SECONDS", "600")),
    )
    if result.returncode != 0:
        print("\nERROR: Build failed.")
        sys.exit(1)

    built_lib = C2PA_RS_DIR / "target" / "release" / lib_name
    if not built_lib.exists():
        print(f"\nERROR: Built library not found at {built_lib}")
        sys.exit(1)

    LIBS_DIR.mkdir(parents=True, exist_ok=True)
    dest = LIBS_DIR / lib_name
    shutil.copy2(str(built_lib), str(dest))
    print(f"\nSuccess: {dest} ({dest.stat().st_size // 1024} KB)")
    print(f"\nNow install with: pip install -e .")


if __name__ == "__main__":
    main()

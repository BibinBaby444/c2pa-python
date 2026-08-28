# Copyright 2025 Adobe. All rights reserved.
# This file is licensed to you under the Apache License,
# Version 2.0 (http://www.apache.org/licenses/LICENSE-2.0)
# or the MIT license (http://opensource.org/licenses/MIT),
# at your option.

# Unless required by applicable law or agreed to in writing,
# this software is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR REPRESENTATIONS OF ANY KIND, either express or
# implied. See the LICENSE-MIT and LICENSE-APACHE files for the
# specific language governing permissions and limitations under
# each license.

from setuptools import setup, find_namespace_packages
import subprocess
import sys
import platform
import shutil
import os
from pathlib import Path
import toml

# Read version from pyproject.toml
def get_version():
    pyproject = toml.load("pyproject.toml")
    return pyproject["project"]["version"]

VERSION = get_version()
PACKAGE_NAME = "c2pa-python"  # Define package name as a constant

# Define platform to library extension mapping (for reference only)
PLATFORM_EXTENSIONS = {
    'win_amd64': 'dll',
    'win_arm64': 'dll',
    'apple-darwin': 'dylib', # universal
    'linux_x86_64': 'so',
    'linux_aarch64': 'so',
}

# Based on what c2pa-rs repo publishes
PLATFORM_FOLDERS = {
    'universal-apple-darwin': 'dylib',
    'aarch64-apple-darwin': 'dylib',
    'x86_64-apple-darwin': 'dylib',
    'x86_64-pc-windows-msvc': 'dll',
    'x86_64-unknown-linux-gnu': 'so',
    'aarch64-unknown-linux-gnu': 'so',
}

# Directory structure
ARTIFACTS_DIR = Path('artifacts')  # Where downloaded libraries are stored
PACKAGE_LIBS_DIR = Path('src/c2pa/libs')  # Where libraries will be copied for the wheel
C2PA_RS_SUBMODULE = Path('c2pa-rs')  # Optional git submodule with patched c2pa-rs

# Native library file name per platform
NATIVE_LIB_NAME = {
    'linux': 'libc2pa_c.so',
    'darwin': 'libc2pa_c.dylib',
    'win32': 'c2pa_c.dll',
}


def build_native_from_source() -> bool:
    """Build the native library from the c2pa-rs submodule.

    Returns True if the build succeeded and the library was copied into
    ``PACKAGE_LIBS_DIR``, False otherwise (Rust not installed, submodule
    not present, build failed, etc.).
    """
    cargo_toml = C2PA_RS_SUBMODULE / 'c2pa_c_ffi' / 'Cargo.toml'
    if not cargo_toml.exists():
        return False

    # Check for cargo
    cargo = shutil.which('cargo')
    if cargo is None:
        # Try common rustup location
        home = Path.home()
        cargo_candidate = home / '.cargo' / 'bin' / 'cargo'
        if cargo_candidate.exists():
            cargo = str(cargo_candidate)
        else:
            print("Rust toolchain not found. Install via: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh")
            return False

    print(f"Building native library from source ({C2PA_RS_SUBMODULE}) ...")
    try:
        build_timeout = int(os.environ.get('C2PA_CARGO_BUILD_TIMEOUT_SECONDS', '600'))
        result = subprocess.run(
            [cargo, 'build', '--release', '--locked', '-p', 'c2pa-c-ffi', '--features', 'file_io'],
            cwd=str(C2PA_RS_SUBMODULE),
            capture_output=True,
            text=True,
            timeout=build_timeout,
        )
        if result.returncode != 0:
            print(f"Cargo build failed:\n{result.stderr[-2000:]}")
            return False
    except FileNotFoundError:
        print("cargo not found on PATH")
        return False
    except subprocess.TimeoutExpired:
        print(f"Cargo build timed out (>{build_timeout}s)")
        return False

    # Determine the library file name for this platform
    lib_name = NATIVE_LIB_NAME.get(sys.platform)
    if lib_name is None:
        print(f"Unsupported platform for source build: {sys.platform}")
        return False

    built_lib = C2PA_RS_SUBMODULE / 'target' / 'release' / lib_name
    if not built_lib.exists():
        print(f"Built library not found at {built_lib}")
        return False

    # Copy to package libs directory
    PACKAGE_LIBS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(built_lib), str(PACKAGE_LIBS_DIR / lib_name))
    print(f"Copied {built_lib} -> {PACKAGE_LIBS_DIR / lib_name}")
    return True


def get_platform_identifier(target_arch=None) -> str:
    """Get a platform identifier (arch-os) for the current system or target architecture.

    Args:
        target_arch: Optional target architecture.
          If provided, overrides auto-detection.
          For macOS: 'universal2', 'arm64', or 'x86_64'
          For Linux: 'aarch64' or 'x86_64'
          For Windows: 'arm64' or 'x64'

    Returns one of:
    - universal-apple-darwin (for macOS universal)
    - aarch64-apple-darwin (for macOS ARM64)
    - x86_64-apple-darwin (for macOS x86_64)
    - x86_64-pc-windows-msvc (for Windows 64-bit)
    - x86_64-unknown-linux-gnu (for Linux 64-bit)
    - aarch64-unknown-linux-gnu (for Linux ARM64)
    """
    system = platform.system().lower()

    if system == "darwin":
        if target_arch == "arm64":
            return "aarch64-apple-darwin"
        elif target_arch == "x86_64":
            return "x86_64-apple-darwin"
        else:
            return "universal-apple-darwin"
    elif system == "windows":
        if target_arch == "arm64":
            return "aarch64-pc-windows-msvc"
        else:
            return "x86_64-pc-windows-msvc"
    elif system == "linux":
        if target_arch == "aarch64" or platform.machine() == "aarch64":
            return "aarch64-unknown-linux-gnu"
        else:
            return "x86_64-unknown-linux-gnu"
    else:
        raise ValueError(f"Unsupported operating system: {system}")

def get_platform_classifier(platform_name):
    """Get the appropriate classifier for a platform."""
    if platform_name.startswith('win') or platform_name.endswith('windows-msvc'):
        return "Operating System :: Microsoft :: Windows"
    elif platform_name.startswith('macosx') or platform_name.endswith('apple-darwin'):
        return "Operating System :: MacOS"
    elif platform_name.startswith('linux') or platform_name.endswith('linux-gnu'):
        return "Operating System :: POSIX :: Linux"
    else:
        raise ValueError(f"Unknown platform: {platform_name}")

def get_current_platform():
    """Determine the current platform name."""
    if sys.platform == "win32":
        if platform.machine() == "ARM64":
            return "win_arm64"
        return "win_amd64"
    elif sys.platform == "darwin":
        if platform.machine() == "arm64":
            return "macosx_aarch64"
        return "macosx_x86_64"
    else:  # Linux
        if platform.machine() == "aarch64":
            return "linux_aarch64"
        return "linux_x86_64"

def copy_platform_libraries(platform_name, clean_first=False):
    """Copy libraries for a specific platform to the package libs directory.

    Args:
        platform_name: The platform to copy libraries for
        clean_first: If True, remove existing files in PACKAGE_LIBS_DIR first
    """
    platform_dir = ARTIFACTS_DIR / platform_name

    # Ensure the platform directory exists and contains files
    if not platform_dir.exists():
        raise ValueError(f"Platform directory not found: {platform_dir}")

    # Get list of all files in the platform directory
    platform_files = list(platform_dir.glob('*'))
    if not platform_files:
        raise ValueError(f"No files found in platform directory: {platform_dir}")

    # Clean and recreate the package libs directory if requested
    if clean_first and PACKAGE_LIBS_DIR.exists():
        shutil.rmtree(PACKAGE_LIBS_DIR)

    # Ensure the package libs directory exists
    PACKAGE_LIBS_DIR.mkdir(parents=True, exist_ok=True)

    # Copy files from platform-specific directory to the package libs directory
    for file in platform_files:
        if file.is_file():
            shutil.copy2(file, PACKAGE_LIBS_DIR / file.name)

def find_available_platforms():
    """Scan the artifacts directory for available platform-specific libraries."""
    if not ARTIFACTS_DIR.exists():
        print(f"Warning: Artifacts directory not found: {ARTIFACTS_DIR}")
        return []

    available_platforms = []
    for platform_name in PLATFORM_FOLDERS.keys():
        platform_dir = ARTIFACTS_DIR / platform_name
        if platform_dir.exists() and any(platform_dir.iterdir()):
            available_platforms.append(platform_name)

    if not available_platforms:
        print("Warning: No platform-specific libraries found in artifacts directory")
        return []

    return available_platforms

# For development installation
if 'develop' in sys.argv or 'install' in sys.argv:
    # Try building from the c2pa-rs submodule first (patched version)
    if not build_native_from_source():
        # Fall back to pre-built artifacts
        current_platform = get_platform_identifier()
        print("Falling back to pre-built artifacts for platform ", current_platform)
        copy_platform_libraries(current_platform)

# For wheel building (both bdist_wheel and build)
if 'bdist_wheel' in sys.argv or 'build' in sys.argv:
    # Check if we're building for a specific architecture
    # This is mostly to support macOS wheel builds
    target_arch = None
    for i, arg in enumerate(sys.argv):
        if arg == '--plat-name':
            if i + 1 < len(sys.argv):
                plat_name = sys.argv[i + 1]
                if 'arm64' in plat_name:
                    target_arch = 'arm64'
                elif 'x86_64' in plat_name:
                    target_arch = 'x86_64'
                elif 'universal2' in plat_name:
                    target_arch = 'universal2'
                break

    # Get the platform identifier for the target architecture
    target_platform = get_platform_identifier(target_arch)
    print(f"Building wheel for target platform: {target_platform}")

    # Try building from source first, fall back to pre-built artifacts
    built_from_source = build_native_from_source()

    if not built_from_source:
        # Check if we have libraries for this platform
        platform_dir = ARTIFACTS_DIR / target_platform
        if not platform_dir.exists() or not any(platform_dir.iterdir()):
            print(f"Warning: No libraries found for platform {target_platform}")
            print("Available platforms:")
            for platform_name in find_available_platforms():
                print(f"  - {platform_name}")

    # Copy libraries for the target platform (only if not built from source)
    try:
        if not built_from_source:
            copy_platform_libraries(target_platform, clean_first=True)

        # Build the wheel
        setup(
            name=PACKAGE_NAME,
            version=VERSION,
            package_dir={"": "src"},
            packages=find_namespace_packages(where="src"),
            include_package_data=True,
            package_data={
                "c2pa": ["libs/*"],
            },
            classifiers=[
                "Programming Language :: Python :: 3",
                get_platform_classifier(target_platform),
            ],
            python_requires=">=3.10",
            long_description=open("README.md").read(),
            long_description_content_type="text/markdown",
            license="MIT OR Apache-2.0",
        )
    finally:
        # Clean up
        if PACKAGE_LIBS_DIR.exists():
            shutil.rmtree(PACKAGE_LIBS_DIR)
    sys.exit(0)

# Ensure native library is available for any install path
# (PEP 517 builds may not trigger the develop/install/bdist_wheel branches above)
if not PACKAGE_LIBS_DIR.exists() or not any(PACKAGE_LIBS_DIR.glob('*')):
    if not build_native_from_source():
        # Try pre-built artifacts as last resort
        try:
            current_platform = get_platform_identifier()
            copy_platform_libraries(current_platform)
        except (ValueError, FileNotFoundError):
            print("WARNING: No native library available. Install Rust and ensure the c2pa-rs submodule is initialized:")
            print("  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh")
            print("  git submodule update --init --recursive")

# For sdist and development installation
setup(
    name=PACKAGE_NAME,
    version=VERSION,
    package_dir={"": "src"},
    packages=find_namespace_packages(where="src"),
    include_package_data=True,
    package_data={
        "c2pa": ["libs/*"],  # Include all files in libs directory
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        get_platform_classifier(get_current_platform()),
    ],
    python_requires=">=3.10",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    license="MIT OR Apache-2.0",
)

# Stable fMP4 Hotfix Release

This lane is independent of the live-video VSI lane. It keeps the Python stable
runtime from `e00831db`, with separately reviewed native single-file fMP4 and
legacy TFRA fixes on the `84208af` baseline. It does not merge VSI APIs or change
crypto providers.

| Contract | Value |
| --- | --- |
| Python package | `c2pa-python==0.31.0+stardustproof.5` |
| Castlabs tag | `castlabs-v0.31.0+stardustproof.5` |
| Release context | `castlabs-stable-fmp4` |
| Profile ID | `stable-fmp4-v1` |
| Native SDK/FFI | `0.80.0` |
| Python release branch | `fix/stable-single-file-fmp4` |
| Native build | defaults enabled; `--features file_io` |
| Crypto | baseline vendored OpenSSL, not `rust_native_crypto` |
| Platforms | Linux x86-64 manylinux_2_28 and Windows x86-64 |
| Wheel tests | Python 3.10, 3.11, 3.12, 3.13, both platforms |
| Evidence | schema 2, standard shape, no VSI capability inference |

## Approval Gate

The previous `.3` candidate run
[`35862631757`, attempt 2](https://github.com/castlabs/c2pa-python/actions/runs/35862631757)
passed both platform builds and all installed-wheel tests at Python source
`44437a5ca56e6e921cd116ce04db7cc7fd786b0c`. Its reserved tag
`castlabs-v0.31.0+stardustproof.3` remains fixed at that source and was **never
published as a release**. The unescaped `+` in its Actions tag filter prevented
the release run from starting. Do not delete, move, reuse, or publish that tag.
The `.4` repair uses a single-quoted filter with one literal backslash before
`+`; exact event refs, evidence and asset names retain the unescaped `+`.
The published `.4` source is `c916e8a82df97f740dd05ec58911dd02ea58325d`;
its tag and release remain immutable. The `.5` TFRA hotfix retains the corrected
literal tag filter and advances only the reviewed stable native pin and gates.

The native audit is cleared and the approved source is committed and pushed in
`castlabs/c2pa-rs`, branch `fix/stable-tfra-offsets`:

- Commit: `589174898eca4c2c42289d3251c0619420806f43`.
- Cargo.lock SHA-256: `fc10bef635df091377d02cfa9f1597462aa016c3db3439d43451a3b93c37edcf`.
- Native runtime version: `0.80.0`.

These actual pins are recorded in `castlabs-stable-fmp4-inputs.lock.json` and the
helper's `RUST_COMMIT` / `CARGO_LOCK_SHA256` constants. The Python `c2pa-rs` gitlink
points to the same commit; `.gitmodules` uses the canonical Castlabs HTTPS URL.
Future pin changes must update all three together after review. Negative tests
remove approval only via explicit in-memory monkeypatches; missing approvals,
changed source pins and null lock entries still fail closed.

Do not substitute the old baseline or the modern VSI SHA, and do not regenerate
Cargo.lock during release. The workflow
reads the native checkout ref only from this validated lock; there is no native
SHA workflow input or automatic upstream binary fallback.

The lock is the explicit release profile. Consumers select `stable-fmp4-v1` by
reviewed release facts with `releaseContext: castlabs-stable-fmp4`, then match the
exact Python/native source and Cargo.lock pins, artifact URLs and digests, native
version, requested feature list and real resolved feature report. They must not
infer this profile from a version prefix, a missing VSI capability, or a smoke
skip. Artifact evidence retains the standard schema-2 keys:
`schemaVersion`, `artifact`, `sources`, `builds`, `runner`, `workflow`.
The context/profile identifiers live in the approved lock and downstream release
facts, not new ad-hoc fields in the artifact evidence.

## Build And Acceptance

Both platform builds must execute all 10 focused Rust
`asset_handlers::bmff_io::tfra_tests` with Rust 1.88.0, `--locked`, defaults and
`file_io`. The `cargo-tfra-tests` helper requires the exact test names to pass
with zero failures or ignored tests, not merely Cargo exit status. These include
the no-`moov` legacy writer/XMP/placeholder synthetics; the existing single-file
Merkle wheel smokes bypass that legacy path and are not TFRA qualification.
The diagnosis, original per-entry fix and unchanged fixture are credited to
BibinBaby444. The focused backport leaves the single-file Merkle implementation
unchanged and does not repair already corrupted TFRA tables: regenerate affected
assets from the unsigned master.

The workflow is `.github/workflows/castlabs-stable-fmp4-release.yml`. Pushes to
the exact `fix/stable-single-file-fmp4` branch run non-publishing candidate builds,
so the lane can be qualified without registering it on the default branch. Manual
dispatch from `fix/stable-single-file-fmp4` accepts that branch-tip `source_sha`,
which must also match the workflow's `GITHUB_SHA`, and builds/tests candidates
without publication. Dispatch from the default branch or a tag is rejected
before checkout. Only a **push** of the exact Castlabs tag enters the protected
`castlabs-stable-fmp4-release` publication environment. This is a GitHub artifact
release with `prerelease=false`, not a PyPI upload (the package has a local version).
The legacy workflows explicitly refuse this version/tag.

Native builds use the pinned Rust 1.88.0 toolchain and manylinux image, Cargo
`--locked`, default features enabled, and the baseline vendored OpenSSL profile.
Linux installs the required full Perl modules; Windows requires the runner's
Strawberry Perl via `OPENSSL_SRC_PERL`. Actual `cargo tree --edges features` output
is published as `features-<target>.txt` and its digest appears in build evidence.
The SDK report must include OpenSSL, HTTP reqwest/blocking, thumbnails and file IO;
the FFI report must include default/http/add_thumbnails/file_io. VSI and
rust-native crypto features are rejected.
The requested evidence feature list is exactly `["file_io"]`; HTTP and thumbnails
are enabled only by the unchanged defaults, not extra requested flags.

The actual Linux Cargo 1.88.0 report is preserved without changes in
`tests/fixtures/stable-fmp4/features-x86_64-unknown-linux-gnu.txt` for regression
tests (not as release evidence). It confirms that `http` exists on the FFI line,
while SDK `c2pa v0.80.0` has `http_reqwest` and `http_reqwest_blocking` instead.
The sibling signer consumer originally required an SDK `http` token and rejected
this report. Its validator now checks the real SDK/FFI names, and a read-only
cross-repo probe accepts the unmodified actual report in the consumer's synthetic
schema-2 fixture. No native defaults or report bytes were changed to achieve
acceptance. This checks the contract, not approval of real release artifacts.

`CASTLABS_STABLE_RELEASE_TARGET` makes `setup.py` consume only the staged native
artifact for that exact host target. Missing staged bytes are fatal. It never
invokes Cargo or a downloader in this mode. The final audited wheel must contain
the exact qualified library bytes, canonical package metadata and wheel tag,
complete SHA-256 RECORD coverage, and the correct ELF64/PE32+ x86-64 architecture.
Unexpected auditwheel grafts, symlinks, traversal, duplicate members or metadata,
and digest mismatches fail. Both final wheels and both native bundles are bound
by schema-2 source/lock/build evidence, feature reports, and checksum sidecars.

`tests/test_castlabs_release_smoke.py` always executes real Python/native APIs.
It has no environment opt-in, capability skips, mocks, or xfails:

- Ordinary `Builder.sign()` on a single file assembled from the committed
  synthetic `tests/fixtures/tiny-segmented` init and two media fragments.
- Raw JUMBF/CBOR checks for the BMFF Merkle map, `initHash`, fragment `count`, and
  one decoded C2PA Merkle proof UUID per moof, with correct IDs and locations.
- Exact preservation of both mdat payloads and clean native validation.
- Tampering in the later fragment must produce `assertion.bmffHash.mismatch`.
- Real DynamicAssertion callback execution through ordinary JPEG signing.
- Real `Builder.sign_fragmented()` / `Reader.from_fragmented_files()` round-trip,
  payload preservation, and later-fragment tamper rejection.

Only fixture credential trust is excluded from the content-validity verdict;
signature, content, and assertion failures remain fatal. No customer inputs,
secrets, ffmpeg installation, or timestamp service are needed by these smokes.
The ordinary upstream tests additionally run on Python 3.10 on each platform.

The stable loader has `C2PA_LIBRARY_NAME` and standard package/LD_LIBRARY_PATH
search locations, but **no `C2PA_LIBRARY_PATH` override**. This lane does not change
the runtime loader. Installed-wheel CI starts from a clean venv and verifies the
package/native versions before acceptance.

## Publication Safety

All requested platform builds and installed-wheel tests must succeed before any
GitHub draft is created. Publication checks the immutable tag against the exact
Python SHA. Draft reconciliation uses exact release IDs, source SHA, marker,
name, stable flag, and a closed asset-name set. Reused assets must have matching
GitHub SHA-256 digests and are also downloaded by exact asset ID for byte checks.
Missing API digests fail closed. Uploads are no-clobber, exact-ID REST calls;
asset names are URL-encoded (including the local-version `+`).

After upload, all assets are re-queried and verified, then checked again immediately
before publication. The complete asset-ID/digest snapshot must be unchanged.
Only then is the exact release ID PATCHed to `draft=false`. The response identity,
IDs and digests are verified and the tag is checked again. Cleanup can delete
only a newly created, validated, still-empty owned draft before the first upload;
reconciled drafts, ambiguous API responses, drafts with assets and published
releases are preserved. Failed/ambiguous publication requires operator inspection,
never destructive retry cleanup.

## Local Checks

Tooling tests do not load c2pa and use clearly synthetic pins only in test memory:

```bash
python3 -m pytest -q tests/test_castlabs_release_tooling.py
python3 scripts/castlabs_release.py validate-lock
```

Both commands must now succeed. Full `validate-sources` additionally requires a
committed, clean Python checkout whose gitlink and immutable event SHA match the
lock; uncommitted Python edits cannot qualify for release.

Post-audit Linux qualification executed all four mandatory smokes successfully using
this worktree's editable `.3` candidate install and the rebuilt native
library at `/root/opencode-worktrees/c2pa-rs-stable-fmp4/target/debug/libc2pa_c.so`,
SHA-256 `4bbaf1078fdd7b005b7d28cf49234c6e29714abcf5cc5aa027c2c18895c98094`.
The synthetic tiny-segmented fixture works for both ordinary single-file and
segmented signing; no replacement media was needed. This is a real-native test,
not installed platform-wheel qualification. The stable signer-info constructor
requires bytes, so the tests initialize `ta_url=b""` then set the native field to
NULL to disable external timestamp requests.

A local `py3-none-linux_x86_64` wheel was then rebuilt through staged-only
packaging using those exact bytes and the approved lock, installed into
`target/wheel-smoke-venv`, and tested from an empty working directory with
`C2PA_LIBRARY_NAME`, `C2PA_LIBRARY_PATH`, `PYTHONPATH`, and `LD_LIBRARY_PATH` unset.
All four mandatory smokes passed again. The loaded library was the wheel's own
`site-packages/c2pa/libs/libc2pa_c.so`, with the same SHA-256 as the staged input;
the wheel's RECORD hashes and sizes were checked. An isolated copy of the wheel's
bindings with the native library omitted failed import, rather than falling back
to a source-worktree or system library. These local probes are repeatable with
`python3 target/check_local_wheel.py` while the local artifacts remain available.

This debug-native development wheel is **not** the manylinux release candidate or
schema-2 release evidence. Packaging uses
`CASTLABS_STABLE_RELEASE_TARGET=x86_64-unknown-linux-gnu` and never rebuilds or
downloads native bytes. Approval of source pins does not replace clean-source
validation or release-profile build evidence. Windows, both release-profile
wheels and the full release workflow remain outstanding.

Local qualification can be repeated without a native rebuild or fake release pin:

```bash
python3 -m venv --system-site-packages target/python-smoke-venv
target/python-smoke-venv/bin/python -m pip install --no-build-isolation --no-deps -e .
PYTHONDONTWRITEBYTECODE=1 \
C2PA_LIBRARY_NAME=/root/opencode-worktrees/c2pa-rs-stable-fmp4/target/debug/libc2pa_c.so \
target/python-smoke-venv/bin/python -m pytest -q tests/test_castlabs_release_smoke.py
```

After installing a candidate wheel and `pytest`, `cbor2`:

```bash
python -m pytest -q tests/test_castlabs_release_smoke.py
```

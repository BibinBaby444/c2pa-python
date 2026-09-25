# C2PA Python library

The [c2pa-python](https://github.com/contentauth/c2pa-python) repository provides a Python library that can:

- Read and validate C2PA manifest data from media files in supported formats.
- Create and sign manifest data, and attach it to media files in supported formats.

Features:

- Create and sign C2PA manifests using various signing algorithms.
- Verify C2PA manifests and extract metadata.
- Add assertions and ingredients to assets.
- Examples and unit tests to demonstrate usage.

<div style={{display: 'none'}}>

For the best experience, read the docs on the [CAI Open Source SDK documentation website](https://opensource.contentauthenticity.org/docs/c2pa-c).

If you want to view the documentation in GitHub, see:
- [Using the Python library](docs/usage.md)
- [Supported formats](https://github.com/contentauth/c2pa-rs/blob/main/docs/supported-formats.md)
- [Configuring the SDK using `Context` and `Settings`](docs/context-settings.md)
- [Using Builder intents](docs/intents.md) to ensure spec-compliant manifests
- Using [working stores and archvies](docs/working-stores.md)
- Selectively constructing manifests by [filtering actions and ingredients](docs/selective-manifests.md)
- [Diagram of public classes in the Python library and their relationships](docs/class-diagram.md)
- [Release notes](docs/release-notes.md)

</div>

## Prerequisites

This library requires Python version 3.10+.

## Package installation

Install the c2pa-python package from PyPI by running:

```bash
pip install c2pa-python
```

To use the module in Python code, import the module like this:

```python
import c2pa
```

## Examples

See the [`examples` directory](https://github.com/contentauth/c2pa-python/tree/main/examples) for some helpful examples:

- `examples/read.py` shows how to read and verify an asset with a C2PA manifest.
- `examples/sign.py` shows how to sign and verify an asset with a C2PA manifest.
- `examples/training.py` demonstrates how to add a "Do Not Train" assertion to an asset and verify it.

## API reference documentation

Documentation is published at [github.io/c2pa-python/api/c2pa](https://contentauth.github.io/c2pa-python/api/c2pa/index.html).

To build documentation locally, refer to [this section in Contributing to the project](https://github.com/contentauth/c2pa-python/blob/main/docs/project-contributions.md#api-reference-documentation).

## Contributing

Contributions are welcome!  For more information, see [Contributing to the project](https://github.com/contentauth/c2pa-python/blob/main/docs/project-contributions.md).

## License

This project is licensed under the Apache License 2.0 and the MIT License. See the [LICENSE-MIT](https://github.com/contentauth/c2pa-python/blob/main/LICENSE-MIT) and [LICENSE-APACHE](https://github.com/contentauth/c2pa-python/blob/main/LICENSE-APACHE) files for details.

## Castlabs Stable fMP4 Hotfix

The dedicated `stable-fmp4-v1` release profile targets
`c2pa-python==0.31.0+stardustproof.5` and native `0.80.0`, preserving default
OpenSSL crypto without importing the live-video VSI runtime. Release context:
`castlabs-stable-fmp4`. The approved native source is
`589174898eca4c2c42289d3251c0619420806f43`; its exact Cargo.lock digest is pinned
in the stable schema-2 release lock. See
[the stable release contract and runbook](release/STABLE-FMP4.md) for the exact
approval gate, artifacts, mandatory real-native tests and publication safeguards.

### ABR ladder signing (`Builder.sign_ladder`)

`Builder.sign_ladder(signer, sources, dests) -> bytes` signs a ladder of
*single-file* fragmented MP4s -- one file per rendition -- into **one**
manifest: one Merkle tree per rendition in the shared `c2pa.hash.bmff.v3`
assertion, the identical manifest embedded in every output. It wraps the
`c2pa_builder_sign_ladder` C API from castlabs/c2pa-rs#9.

That native symbol is **optional**: the binding imports against any stable
library, records whether the symbol is present in `c2pa.c2pa._HAS_SIGN_LADDER`,
and `sign_ladder` raises `C2paError.NotSupported` when it is absent. The
`0.31.0+stardustproof.5` library does not carry it; a release built from a
native source that includes castlabs/c2pa-rs#9 does. The builder is borrowed
by the call and stays usable afterwards; release it with `close()` as usual.

Outputs must not exist yet -- the native writer creates each one and never
overwrites -- and every output it created is removed again if the call fails.
Sources that already carry a C2PA manifest are refused.

`tests/test_builder_sign_ladder.py` covers the binding against a stand-in for
the native function on any library, and signs a real two-rendition ladder when
the loaded library has the symbol. Set `C2PA_REQUIRE_SIGN_LADDER=1` in a lane
whose library is supposed to carry it, so its absence fails instead of
skipping.

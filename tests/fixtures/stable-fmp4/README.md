# Stable Native Feature-Report Fixture

`features-x86_64-unknown-linux-gnu.txt` is actual stdout from Rust/Cargo 1.88.0,
captured on the uncommitted stable native worktree based on
`84208af42320d4a0208f6bbfaf3339c94b9e0cdb`. It is a test fixture, **not approved
release evidence**. No fixed native commit or release artifact pin is implied.
Source paths are retained exactly as Cargo printed them.

After native approval, the same command was rerun at committed source
`c1282d33a8fd1145c32d93c27b313a16523f1dbd`; its output is byte-identical to this
fixture. The report remains test data, not a release artifact approval.

Command (run from the Python worktree):

```sh
cargo +1.88.0 tree --locked --offline \
  --manifest-path /root/opencode-worktrees/c2pa-rs-stable-fmp4/c2pa_c_ffi/Cargo.toml \
  --package c2pa-c-ffi --target x86_64-unknown-linux-gnu --features file_io \
  --edges features --charset ascii --format '{p}_features=[{f}]'
```

`--offline` only disables registry network access for this local capture; the
release command retains the exact command array in the stable input contract.
The feature selection is identical. The fixture SHA-256 is
`94fea6aa8fcbcda8bca4aa2e8b2ab02d856f35ee29317fbe3c55688de7a0f61e`.

The resolved FFI features are `add_thumbnails,default,file_io,http`. The SDK has
`add_thumbnails,fetch_remote_manifests,file_io,http_reqwest,http_reqwest_blocking,image,openssl,pdf`.
The SDK has no literal `http` feature in this native baseline; that is the FFI
feature which enables the SDK's reqwest features. Consumer validators must check
those real names, not require or manufacture an SDK `http` feature. OpenSSL is
resolved with `vendored`, and neither VSI nor rust-native crypto is enabled.

Single-file and segmented smoke media continue to use the existing committed
`../tiny-segmented` synthetic fixture. Both native signing paths pass with these
bytes, so no customer media or replacement fixture is needed.

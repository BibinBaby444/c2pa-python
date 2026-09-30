"""Tests for ``Builder.sign_ladder`` (wraps ``c2pa_builder_sign_ladder``).

The native symbol is optional -- it exists only in libraries carrying
castlabs/c2pa-rs#9 -- so most of this file drives the Python side against a
fake native function and checks the contract the binding promises:

* a library without the symbol imports, and ``sign_ladder`` fails with
  ``C2paError.NotSupported`` at call time;
* argument problems are refused *before* any native call;
* paths reach C in order, UTF-8 encoded, and never with an embedded NUL;
* a negative native result is an error, a copy failure is an error (not
  empty bytes), and the native buffer is released exactly once;
* the builder is borrowed, not consumed: it survives success and failure.

The last test is the real thing. It signs two renditions through the native
library and validates each output. It skips when the loaded library lacks the
symbol -- unless ``C2PA_REQUIRE_SIGN_LADDER=1`` is set, in which case the
absence is a failure: that is how a release lane that is *supposed* to carry
the symbol proves it does.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
from pathlib import Path
from unittest import mock

import pytest

import c2pa.c2pa as binding
from c2pa import Builder, C2paError, C2paSignerInfo, Reader, Signer

FIXTURES = Path(__file__).parent / "fixtures"
RENDITION = FIXTURES / "single-file-fragmented" / "single_file_fragments.mp4"
C2PA_UUID = bytes.fromhex("d8fec3d61b0e483c92975828877ec481")


def _boxes(data: bytes):
    """Yield ``(kind, payload)`` for each BMFF box in ``data``, in order."""
    offset = 0
    while offset < len(data):
        assert len(data) - offset >= 8, "truncated box header"
        size = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        header = 8
        if size == 1:
            size = int.from_bytes(data[offset + 8 : offset + 16], "big")
            header = 16
        elif size == 0:
            size = len(data) - offset
        assert header <= size <= len(data) - offset, "invalid box bounds"
        yield kind, data[offset + header : offset + size]
        offset += size


def _embedded_manifest(data: bytes) -> bytes:
    """The JUMBF bytes c2pa embedded in a BMFF file, read straight from the
    boxes rather than through any Reader presentation: the top-level ``uuid``
    box carrying the C2PA UUID, purpose ``manifest``. Its payload is the
    16-byte UUID, a 4-byte version, the NUL-terminated purpose, then -- for
    ``manifest`` only -- an 8-byte big-endian ``merkle_offset`` to the first
    ``merkle`` box, and only then the manifest itself."""
    found = []
    for kind, payload in _boxes(data):
        if kind == b"uuid" and payload[:16] == C2PA_UUID:
            purpose, separator, content = payload[20:].partition(b"\0")
            assert separator, "malformed C2PA uuid box"
            if purpose == b"manifest":
                merkle_offset = int.from_bytes(content[:8], "big")
                assert 0 < merkle_offset < len(data), (
                    f"implausible merkle_offset {merkle_offset}"
                )
                found.append(content[8:])
    assert len(found) == 1, (
        f"expected exactly one embedded manifest, found {len(found)}"
    )
    return found[0]


def _merkle_maps(manifest: bytes) -> list:
    """The ``merkle`` array of the one ``c2pa.hash.bmff*`` assertion, decoded
    from the manifest's own CBOR so the check does not depend on how a given
    Reader version renders hash assertions as JSON. Only the real-library test
    needs ``cbor2``, so it is imported here rather than at module level: the
    stand-in tests must keep running in a lane without it."""
    import cbor2  # noqa: PLC0415 -- see the docstring

    maps = []

    def walk(data: bytes) -> None:
        for kind, payload in _boxes(data):
            if kind != b"jumb":
                continue
            children = list(_boxes(payload))
            labels = []
            for child_kind, child in children:
                # jumd: 16-byte type, one toggles byte, then the label when
                # toggle bit 0x02 (Label Present) is set. Bit 0x01 is
                # Requestable, which c2pa also sets, so testing it would pass
                # by accident.
                if child_kind == b"jumd" and child[16] & 0x02:
                    labels.append(child[17:].split(b"\0", 1)[0])
            if labels and labels[0].startswith(b"c2pa.hash.bmff"):
                cbors = [c for k, c in children if k == b"cbor"]
                assert len(cbors) == 1, "hash assertion without exactly one cbor box"
                maps.append(cbor2.loads(cbors[0])["merkle"])
            walk(payload)

    walk(manifest)
    assert len(maps) == 1, f"expected one bmff hash assertion, found {len(maps)}"
    return maps[0]


_NATIVE_SIGNATURE = ctypes.CFUNCTYPE(
    ctypes.c_int64,
    ctypes.POINTER(binding.C2paBuilder),
    ctypes.POINTER(binding.C2paSigner),
    ctypes.POINTER(ctypes.c_char_p),
    ctypes.POINTER(ctypes.c_char_p),
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
)


def _manifest_definition() -> dict:
    return {
        "claim_generator_info": [{"name": "python_test_sign_ladder", "version": "0.0.1"}],
        "format": "video/mp4",
        "title": "python test sign_ladder",
        "assertions": [
            {
                "label": "c2pa.actions.v2",
                "data": {"actions": [{"action": "c2pa.created", "digitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/digitalCreation"}]},
            }
        ],
    }


@pytest.fixture
def signer():
    info = C2paSignerInfo(
        alg=b"es256",
        sign_cert=(FIXTURES / "es256_certs.pem").read_bytes(),
        private_key=(FIXTURES / "es256_private.key").read_bytes(),
        ta_url=b"http://timestamp.digicert.com",
    )
    s = Signer.from_info(info)
    yield s
    s.close()


@pytest.fixture
def builder():
    b = Builder(_manifest_definition())
    yield b
    b.close()


class _FakeNative:
    """A stand-in for ``c2pa_builder_sign_ladder`` that records what C would
    have seen and returns what the test dictates."""

    def __init__(self, result: int, manifest: bytes = b"", set_out: bool = True):
        self.result = result
        self.set_out = set_out
        self.calls: list[dict] = []
        self._buffer = ctypes.create_string_buffer(manifest, max(len(manifest), 1))
        self.callback = _NATIVE_SIGNATURE(self._call)

    def _call(self, _builder, _signer, sources, dests, count, out):
        self.calls.append(
            {
                "sources": [sources[i] for i in range(count)],
                "dests": [dests[i] for i in range(count)],
                "count": count,
            }
        )
        if self.result > 0 and self.set_out:
            out[0] = ctypes.cast(self._buffer, ctypes.POINTER(ctypes.c_ubyte))
        return self.result


@pytest.fixture
def fake_native(monkeypatch):
    """Install ``fake`` as the native function and count buffer releases."""

    def install(result: int, manifest: bytes = b"") -> tuple[_FakeNative, mock.Mock]:
        fake = _FakeNative(result, manifest)
        free = mock.Mock(name="c2pa_manifest_bytes_free")
        monkeypatch.setattr(binding, "_HAS_SIGN_LADDER", True)
        monkeypatch.setattr(binding._lib, "c2pa_builder_sign_ladder", fake.callback, raising=False)
        monkeypatch.setattr(binding._lib, "c2pa_manifest_bytes_free", free)
        return fake, free

    return install


# --- missing symbol -----------------------------------------------------------


def test_the_symbol_is_optional_at_import():
    assert "c2pa_builder_sign_ladder" not in binding._REQUIRED_FUNCTIONS
    assert isinstance(binding._HAS_SIGN_LADDER, bool)


def test_a_library_without_the_symbol_fails_at_call_time_not_import(monkeypatch, builder, signer):
    monkeypatch.setattr(binding, "_HAS_SIGN_LADDER", False)
    native = mock.Mock(name="c2pa_builder_sign_ladder")
    monkeypatch.setattr(binding._lib, "c2pa_builder_sign_ladder", native, raising=False)
    with pytest.raises(C2paError.NotSupported, match="c2pa_builder_sign_ladder is absent"):
        builder.sign_ladder(signer=signer, sources=["a.mp4"], dests=["b.mp4"])
    native.assert_not_called()
    # The builder was not touched, let alone consumed.
    assert builder._handle


# --- preflight ---------------------------------------------------------------


@pytest.mark.parametrize(
    "sources, dests, message",
    [
        (["a.mp4"], ["x.mp4", "y.mp4"], "same length"),
        ([], [], "at least one rendition"),
        (["a\0.mp4"], ["x.mp4"], r"sources\[0\] contains a NUL byte"),
        ([b"a\0.mp4"], ["x.mp4"], r"sources\[0\] contains a NUL byte"),
        (["a.mp4"], ["x.mp4\0ignored"], r"dests\[0\] contains a NUL byte"),
        (["a\udcff.mp4"], ["x.mp4"], r"sources\[0\] cannot be encoded as UTF-8"),
        ([7], ["x.mp4"], r"sources\[0\] is not a path"),
    ],
)
def test_argument_problems_are_refused_before_any_native_call(
    fake_native, builder, signer, sources, dests, message
):
    fake, free = fake_native(result=-1)
    with pytest.raises(C2paError, match=message):
        builder.sign_ladder(signer=signer, sources=sources, dests=dests)
    assert fake.calls == []
    free.assert_not_called()
    assert builder._handle


def test_a_closed_signer_is_refused_before_any_native_call(fake_native, builder, signer):
    fake, _free = fake_native(result=-1)
    signer.close()
    with pytest.raises(C2paError, match="Invalid or closed signer"):
        builder.sign_ladder(signer=signer, sources=["a.mp4"], dests=["x.mp4"])
    assert fake.calls == []


def test_a_closed_builder_is_refused_before_any_native_call(fake_native, signer):
    fake, _free = fake_native(result=-1)
    b = Builder(_manifest_definition())
    b.close()
    with pytest.raises(C2paError):
        b.sign_ladder(signer=signer, sources=["a.mp4"], dests=["x.mp4"])
    assert fake.calls == []


# --- marshalling -------------------------------------------------------------


def test_paths_reach_c_in_order_as_utf8(fake_native, builder, signer, tmp_path):
    fake, free = fake_native(result=5, manifest=b"jumbf")
    sources = [tmp_path / "1080p.mp4", "720p/vidéo.mp4", os.fspath(tmp_path / "360p.mp4")]
    dests = ["out/1080p.mp4", Path("out/720p.mp4"), b"out/360p.mp4"]  # bytes are paths too
    manifest = builder.sign_ladder(signer=signer, sources=sources, dests=dests)
    assert manifest == b"jumbf"
    (call,) = fake.calls
    assert call["count"] == 3
    assert call["sources"] == [os.fspath(p).encode("utf-8") for p in sources]
    assert call["dests"] == [b"out/1080p.mp4", b"out/720p.mp4", b"out/360p.mp4"]
    free.assert_called_once()


# --- native results -----------------------------------------------------------


def test_a_negative_native_result_is_an_error(fake_native, builder, signer):
    fake, free = fake_native(result=-1)
    with pytest.raises(C2paError):
        builder.sign_ladder(signer=signer, sources=["a.mp4"], dests=["x.mp4"])
    assert len(fake.calls) == 1
    free.assert_not_called()


def test_a_positive_result_without_a_buffer_is_an_error(fake_native, builder, signer):
    fake = _FakeNative(result=9, set_out=False)  # claims nine bytes, sets no out-pointer
    free = mock.Mock()
    with mock.patch.object(binding, "_HAS_SIGN_LADDER", True), mock.patch.object(
        binding._lib, "c2pa_builder_sign_ladder", fake.callback, create=True
    ), mock.patch.object(binding._lib, "c2pa_manifest_bytes_free", free):
        with pytest.raises(C2paError, match="returned no buffer"):
            builder.sign_ladder(signer=signer, sources=["a.mp4"], dests=["x.mp4"])
    free.assert_not_called()


def test_a_copy_failure_is_an_error_and_the_buffer_is_freed_once(fake_native, builder, signer):
    fake, free = fake_native(result=5, manifest=b"jumbf")
    with mock.patch.object(ctypes, "string_at", side_effect=MemoryError("no room")):
        with pytest.raises(MemoryError):
            builder.sign_ladder(signer=signer, sources=["a.mp4"], dests=["x.mp4"])
    assert len(fake.calls) == 1
    free.assert_called_once()
    assert builder._handle


def test_a_zero_result_is_empty_bytes_with_nothing_to_free(fake_native, builder, signer):
    _fake, free = fake_native(result=0)
    assert builder.sign_ladder(signer=signer, sources=["a.mp4"], dests=["x.mp4"]) == b""
    free.assert_not_called()


# --- ownership ----------------------------------------------------------------


def test_the_builder_survives_success_and_failure(fake_native, builder, signer):
    fake, _free = fake_native(result=5, manifest=b"jumbf")
    assert builder.sign_ladder(signer=signer, sources=["a.mp4"], dests=["x.mp4"]) == b"jumbf"
    assert builder._handle, "the builder was consumed on success"
    fake.result = -1
    with pytest.raises(C2paError):
        builder.sign_ladder(signer=signer, sources=["a.mp4"], dests=["x.mp4"])
    assert builder._handle, "the builder was consumed on failure"
    fake.result = 5
    assert builder.sign_ladder(signer=signer, sources=["a.mp4"], dests=["x.mp4"]) == b"jumbf"
    builder.close()
    assert builder._handle is None
    builder.close()  # idempotent: no double free


# --- the real thing -----------------------------------------------------------


def test_a_real_ladder_signs_and_validates(signer, tmp_path):
    if not binding._HAS_SIGN_LADDER:
        if os.environ.get("C2PA_REQUIRE_SIGN_LADDER"):
            pytest.fail(
                "C2PA_REQUIRE_SIGN_LADDER is set but the loaded native library "
                "has no c2pa_builder_sign_ladder"
            )
        pytest.skip("native library lacks c2pa_builder_sign_ladder")

    sources = []
    for name in ("1080p", "720p"):
        source = tmp_path / f"{name}.mp4"
        shutil.copy2(RENDITION, source)
        sources.append(source)
    before = [s.read_bytes() for s in sources]
    dests = [tmp_path / "out" / f"{s.stem}.mp4" for s in sources]
    dests[0].parent.mkdir()

    builder = Builder(_manifest_definition())
    try:
        manifest = builder.sign_ladder(signer=signer, sources=sources, dests=dests)
        assert manifest and b"c2pa" in manifest
        assert builder._handle, "the builder was consumed"
    finally:
        builder.close()

    assert [s.read_bytes() for s in sources] == before, "a source was modified"

    # The strong check first, straight from the bytes: what came back is what
    # was embedded, byte for byte, in every rendition -- and the assertion in
    # it carries one Merkle map per rendition. Neither depends on how Reader
    # happens to render the manifest as JSON.
    for dest in dests:
        assert _embedded_manifest(dest.read_bytes()) == manifest, (
            f"{dest.name}: the embedded manifest differs from the returned bytes"
        )
    assert len(_merkle_maps(manifest)) == 2, "one Merkle map per rendition"

    manifests = []
    for dest in dests:
        with open(dest, "rb") as stream:
            reader = Reader("video/mp4", stream)
            try:
                report = json.loads(reader.json())
            finally:
                reader.close()
        active = report["active_manifest"]
        assert active, f"{dest.name}: no active manifest: {report}"
        # The test certificate is untrusted, which is informational; the
        # manifest itself must validate, and its binding must match.
        assert report["validation_state"] in ("Valid", "Trusted"), report["validation_state"]
        results = report["validation_results"]["activeManifest"]
        codes = {entry["code"] for entry in results.get("success", [])}
        assert "assertion.bmffHash.match" in codes, f"{dest.name}: {codes}"
        assert "claimSignature.validated" in codes, f"{dest.name}: {codes}"
        failures = {entry["code"] for entry in results.get("failure", [])}
        assert failures <= {"signingCredential.untrusted"}, f"{dest.name}: {failures}"
        manifests.append((active, json.dumps(report["manifests"], sort_keys=True)))
    assert manifests[0] == manifests[1], "the renditions do not carry the identical manifest"

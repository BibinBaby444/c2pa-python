"""Mandatory real-native acceptance for stable-fmp4-v1. No skip switches.

Uses only the committed synthetic tiny-segmented media and test credentials.
Run against an installed candidate wheel, not a modern VSI source checkout.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
from importlib.metadata import version
from pathlib import Path

import cbor2

import c2pa
import c2pa.c2pa as binding


FIXTURES = Path(__file__).parent / "fixtures"
SEGMENTS = FIXTURES / "tiny-segmented"
C2PA_UUID = bytes.fromhex("d8fec3d61b0e483c92975828877ec481")


def boxes(data: bytes):
    offset = 0
    while offset < len(data):
        assert len(data) - offset >= 8, "truncated box header"
        size = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        header = 8
        if size == 1:
            assert len(data) - offset >= 16, "truncated extended header"
            size = int.from_bytes(data[offset + 8 : offset + 16], "big")
            header = 16
        elif size == 0:
            size = len(data) - offset
        assert header <= size <= len(data) - offset, "invalid box bounds"
        yield kind, offset, data[offset : offset + size], header
        offset += size


def bmff_assertions(manifest: bytes) -> list[dict]:
    found = []
    for kind, _, box, header in boxes(manifest):
        if kind != b"jumb":
            continue
        children = list(boxes(box[header:]))
        descriptions = [child[h:] for typ, _, child, h in children if typ == b"jumd"]
        assert len(descriptions) == 1
        description = descriptions[0]
        assert len(description) >= 17
        label = description[17:].split(b"\0", 1)[0] if description[16] & 1 else b""
        if label.startswith(b"c2pa.hash.bmff"):
            payloads = [child[h:] for typ, _, child, h in children if typ == b"cbor"]
            assert len(payloads) == 1
            found.append(cbor2.loads(payloads[0]))
        found.extend(bmff_assertions(box[header:]))
    return found


def proof_boxes(data: bytes) -> list[tuple[int, object]]:
    proofs = []
    for kind, offset, box, header in boxes(data):
        payload = box[header:]
        if kind == b"uuid" and payload[:16] == C2PA_UUID:
            assert payload[16:20] == bytes(4)
            purpose, separator, content = payload[20:].partition(b"\0")
            assert separator
            if purpose == b"merkle":
                proof = cbor2.loads(content)
                assert proof, "empty per-fragment Merkle map"
                proofs.append((offset, proof))
    return proofs


def definition(format: str) -> dict:
    return {
        "claim_generator_info": [
            {"name": "castlabs-stable-fmp4-smoke", "version": "1"}
        ],
        "claim_version": 2,
        "format": format,
        "title": "synthetic stable hotfix acceptance",
        "assertions": [
            {
                "label": "c2pa.actions",
                "data": {
                    "actions": [
                        {
                            "action": "c2pa.created",
                            "digitalSourceType": "http://c2pa.org/digitalsourcetype/empty",
                        }
                    ]
                },
            }
        ],
    }


def signer() -> c2pa.Signer:
    info = c2pa.C2paSignerInfo(
        alg=b"es256",
        sign_cert=(FIXTURES / "es256_certs.pem").read_bytes(),
        private_key=(FIXTURES / "es256_private.key").read_bytes(),
        ta_url=b"",
    )
    # Stable's constructor requires bytes; the native optional URL must be NULL
    # rather than an empty URL to disable external timestamp requests.
    info.ta_url = None
    return c2pa.Signer.from_info(info)


def failures(report: dict) -> set[str]:
    # Trust of fixture credentials is not the property under test. All content,
    # signature and assertion failures remain fatal, including on later fragments.
    return {
        item["code"]
        for item in report.get("validation_status", [])
        if item["code"] != "signingCredential.untrusted"
    }


def assert_clean(reader: c2pa.Reader) -> dict:
    report = json.loads(reader.json())
    assert report.get("active_manifest")
    assert not failures(report), report
    assert reader.get_validation_state() != "Invalid"
    return report


def test_stable_installed_wheel_identity():
    assert version("c2pa-python") == "0.31.0+stardustproof.4"
    assert re.search(r"(?<![\d.])0\.80\.0(?![\d.\w-])", c2pa.sdk_version())
    assert not hasattr(c2pa, "LiveVideoVsiSession")
    assert callable(c2pa.Signer.add_dynamic_assertion)
    assert callable(c2pa.Builder.sign_fragmented)
    assert callable(c2pa.Reader.from_fragmented_files)
    library = Path(binding._lib._name).resolve()
    explicit_library = os.environ.get("C2PA_LIBRARY_NAME")
    if explicit_library:
        # Source qualification must use the requested candidate, not the loader's
        # permissive fallback. Release CI leaves the override unset.
        assert library == Path(explicit_library).resolve()
    else:
        name = "c2pa_c.dll" if sys.platform == "win32" else "libc2pa_c.so"
        assert library == (Path(c2pa.__file__).parent / "libs" / name).resolve()


def test_single_file_fmp4_real_merkle_and_later_tamper():
    # Retain ftyp/moov and moof/mdat only; DASH sidx/styp are not needed in a
    # single file. The fixture uses default-base-is-moof, so no offsets change.
    init = b"".join(
        box
        for typ, _, box, _ in boxes((SEGMENTS / "init.m4s").read_bytes())
        if typ in (b"ftyp", b"moov")
    )
    fragments = [(SEGMENTS / f"seg-{i:04d}.m4s").read_bytes() for i in (1, 2)]
    source = init + b"".join(
        box
        for fragment in fragments
        for typ, _, box, _ in boxes(fragment)
        if typ in (b"moof", b"mdat")
    )
    original_media = [box[h:] for typ, _, box, h in boxes(source) if typ == b"mdat"]
    assert len(original_media) == 2
    output = io.BytesIO()
    with signer() as claim_signer, c2pa.Builder(definition("video/mp4")) as builder:
        manifest = builder.sign(claim_signer, "video/mp4", io.BytesIO(source), output)
    signed = output.getvalue()
    assertions = bmff_assertions(manifest)
    assert len(assertions) == 1
    merkle = assertions[0]["merkle"]
    assert len(merkle) == 1
    assert merkle[0]["count"] == len(original_media)
    assert isinstance(merkle[0]["initHash"], bytes) and len(merkle[0]["initHash"]) == 32
    assert merkle[0]["hashes"]
    moofs = [offset for typ, offset, _, _ in boxes(signed) if typ == b"moof"]
    proofs = proof_boxes(signed)
    assert len(proofs) == len(moofs) == len(original_media)
    # Each proof must accompany its own moof, not be duplicated beside the first.
    for index, moof in enumerate(moofs):
        start = 0 if index == 0 else moofs[index - 1]
        assert sum(start <= offset < moof for offset, _ in proofs) == 1
        proof = proofs[index][1]
        assert proof["location"] == index
        assert proof["uniqueId"] == merkle[0]["uniqueId"]
        assert proof["localId"] == merkle[0]["localId"]
    assert [
        box[h:] for typ, _, box, h in boxes(signed) if typ == b"mdat"
    ] == original_media
    with c2pa.Reader("video/mp4", io.BytesIO(signed)) as reader:
        assert_clean(reader)
    mdats = [
        (offset, len(box), header)
        for typ, offset, box, header in boxes(signed)
        if typ == b"mdat"
    ]
    offset, size, header = mdats[-1]
    assert size > header + 16
    tampered = bytearray(signed)
    tampered[offset + header + 16] ^= 1
    with c2pa.Reader("video/mp4", io.BytesIO(tampered)) as reader:
        report = json.loads(reader.json())
        assert "assertion.bmffHash.mismatch" in failures(report), report
        assert reader.get_validation_state() == "Invalid"


def test_dynamic_assertion_real_builder_round_trip():
    calls = []
    content = cbor2.dumps({"id": 1, "pad": bytes(53)})
    assert len(content) == 64

    def callback(label, reserve_size, partial_claim):
        assert label == "com.castlabs.stable-smoke"
        assert reserve_size == len(content)
        assert any("/c2pa.actions" in entry["url"] for entry in partial_claim)
        calls.append(partial_claim)
        return content

    output = io.BytesIO()
    with signer() as claim_signer, c2pa.Builder(definition("image/jpeg")) as builder:
        claim_signer.add_dynamic_assertion(
            callback, label="com.castlabs.stable-smoke", reserve_size=64
        )
        manifest = builder.sign(
            claim_signer,
            "image/jpeg",
            io.BytesIO((FIXTURES / "A.jpg").read_bytes()),
            output,
        )
    assert manifest and len(calls) == 1
    with c2pa.Reader("image/jpeg", io.BytesIO(output.getvalue())) as reader:
        report = assert_clean(reader)
        active = report["manifests"][report["active_manifest"]]
        assertion = next(
            a for a in active["assertions"] if a["label"] == "com.castlabs.stable-smoke"
        )
        assert assertion["data"]["id"] == 1


def test_segmented_real_round_trip_and_later_tamper(tmp_path):
    with signer() as claim_signer, c2pa.Builder(definition("video/mp4")) as builder:
        manifest = builder.sign_fragmented(
            claim_signer, SEGMENTS / "init.m4s", "seg-*.m4s", tmp_path
        )
    output = tmp_path / SEGMENTS.name
    fragments = [output / f"seg-{i:04d}.m4s" for i in (1, 2)]
    assert manifest
    for original, signed in zip([SEGMENTS / p.name for p in fragments], fragments):
        assert [
            b[h:] for t, _, b, h in boxes(original.read_bytes()) if t == b"mdat"
        ] == [b[h:] for t, _, b, h in boxes(signed.read_bytes()) if t == b"mdat"]
        assert proof_boxes(signed.read_bytes())
    with c2pa.Reader.from_fragmented_files(output / "init.m4s", fragments) as reader:
        assert_clean(reader)
    data = bytearray(fragments[-1].read_bytes())
    offset, header = next((o, h) for t, o, _, h in boxes(data) if t == b"mdat")
    data[offset + header + 16] ^= 1
    fragments[-1].write_bytes(data)
    with c2pa.Reader.from_fragmented_files(output / "init.m4s", fragments) as reader:
        assert "assertion.bmffHash.mismatch" in failures(json.loads(reader.json()))

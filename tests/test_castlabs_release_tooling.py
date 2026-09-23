"""Pure-Python stable release policy tests; no native library is loaded.

All synthetic pins and binary headers below are test inputs only, never release
facts. The committed production lock is tested separately against approved pins.
"""

from __future__ import annotations

import base64
import ast
import copy
import csv
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import cbor2
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stable_release", ROOT / "scripts/castlabs_release.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
LINUX = "x86_64-unknown-linux-gnu"
WINDOWS = "x86_64-pc-windows-msvc"
SOURCE = "a" * 40
REPORT = (
    "c2pa-c-ffi v0.80.0 (/source/c2pa_c_ffi)_features=[add_thumbnails,default,file_io,http]\n"
    "`-- c2pa v0.80.0 (/source/sdk)_features=[add_thumbnails,file_io,openssl,http_reqwest,http_reqwest_blocking,pdf]\n"
    "    `-- openssl v0.10.72_features=[default,vendored]\n"
)
ACTUAL_REPORT = (
    ROOT / "tests/fixtures/stable-fmp4/features-x86_64-unknown-linux-gnu.txt"
)


@pytest.fixture
def approved(monkeypatch):
    lock = release.load_lock()
    lock["rustSource"]["commit"] = "b" * 40
    lock["rustSource"]["cargoLockSha256"] = "c" * 64
    monkeypatch.setattr(release, "RUST_COMMIT", lock["rustSource"]["commit"])
    monkeypatch.setattr(
        release, "CARGO_LOCK_SHA256", lock["rustSource"]["cargoLockSha256"]
    )
    monkeypatch.setattr(release, "load_lock", lambda: copy.deepcopy(lock))
    return lock


def native(target):
    payload = bytearray(128)
    if target == LINUX:
        payload[:6] = b"\x7fELF\x02\x01"
        payload[16:20] = b"\x03\x00\x3e\x00"
    else:
        payload[:2] = b"MZ"
        payload[60:64] = (64).to_bytes(4, "little")
        payload[64:70] = b"PE\0\0\x64\x86"
        payload[86:90] = b"\x00\x20\x0b\x02"
    return bytes(payload)


def wheel(target, mutate=None):
    lock = release.load_lock()
    dist = f"c2pa_python-{release.RELEASE_VERSION}.dist-info"
    files = {
        "c2pa/__init__.py": b"# synthetic test package\n",
        f"c2pa/libs/{lock['targets'][target]['library']}": native(target),
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: c2pa-python\nVersion: {release.RELEASE_VERSION}\n".encode(),
        f"{dist}/WHEEL": f"Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: py3-none-{lock['targets'][target]['wheelPlatformTag']}\n".encode(),
    }
    rows = []
    for name, data in files.items():
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        )
        rows.append([name, f"sha256={digest}", str(len(data))])
    rows.append([f"{dist}/RECORD", "", ""])
    record = io.StringIO()
    csv.writer(record, lineterminator="\n").writerows(rows)
    files[f"{dist}/RECORD"] = record.getvalue().encode()
    if mutate:
        mutate(files)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    name = f"c2pa_python-{release.RELEASE_VERSION}-py3-none-{lock['targets'][target]['wheelPlatformTag']}.whl"
    return name, output.getvalue()


def test_committed_approval_matches_reviewed_native_pins():
    lock = release.load_lock()
    assert lock["releaseContext"] == "castlabs-stable-fmp4"
    assert lock["profileId"] == "stable-fmp4-v1"
    assert lock["rustSource"]["commit"] == "c1282d33a8fd1145c32d93c27b313a16523f1dbd"
    assert (
        lock["rustSource"]["cargoLockSha256"]
        == "fc10bef635df091377d02cfa9f1597462aa016c3db3439d43451a3b93c37edcf"
    )
    release.validate_lock(lock)
    schema = json.loads(
        (ROOT / "release/castlabs-stable-fmp4-inputs.schema.json").read_text()
    )
    jsonschema.validate(lock, schema)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/castlabs_release.py"),
            "lock-value",
            "rustSource.commit",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == lock["rustSource"]["commit"]


@pytest.mark.parametrize("constant", ["RUST_COMMIT", "CARGO_LOCK_SHA256"])
def test_missing_approval_fails_closed(monkeypatch, constant):
    lock = release.load_lock()
    monkeypatch.setattr(release, constant, None)
    with pytest.raises(SystemExit, match="missing approved stable"):
        release.validate_lock(lock)


def test_approved_profile_schemas_and_version(approved):
    release.validate_lock(approved)
    assert release.project_version(ROOT) == release.RELEASE_VERSION
    assert approved["rustSource"]["version"] == "0.80.0"
    schema = json.loads(
        (ROOT / "release/castlabs-stable-fmp4-inputs.schema.json").read_text()
    )
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(approved, schema)
    for target in approved["targets"]:
        command = release.cargo_command(approved, target)
        assert "--locked" in command
        assert "--no-default-features" not in command
        assert command[-1] == "file_io"


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("rustSource", "commit", "d" * 40),
        ("rustSource", "commit", None),
        ("rustSource", "cargoLockSha256", "d" * 64),
        ("rustSource", "cargoLockSha256", None),
        ("rustSource", "version", "0.91.0-dev"),
        ("nativeBuild", "noDefaultFeatures", True),
        ("nativeBuild", "features", ["http", "add_thumbnails", "file_io"]),
        ("nativeBuild", "features", ["rust_native_crypto", "unstable_live_video"]),
        ("pythonSource", "releaseBranch", "feat/live-video-vsi"),
        ("package", "version", "0.31.0+stardustproof.2"),
    ],
)
def test_profile_drift_is_rejected(approved, section, key, value):
    approved[section][key] = value
    with pytest.raises(SystemExit):
        release.validate_lock(approved)


@pytest.mark.parametrize(
    "field,value",
    [
        ("releaseContext", "historical-0.31.0-signing"),
        ("profileId", "vsi"),
        ("unknown", True),
    ],
)
def test_profile_is_explicit_not_inferred(approved, field, value):
    approved[field] = value
    with pytest.raises(SystemExit):
        release.validate_lock(approved)


def test_duplicate_lock_keys_rejected(tmp_path, monkeypatch):
    path = tmp_path / "duplicate.json"
    path.write_text('{"schemaVersion":1,"schemaVersion":1}')
    monkeypatch.setattr(release, "LOCK_PATH", path)
    with pytest.raises(SystemExit, match="duplicate JSON key"):
        release.load_lock()


@pytest.mark.parametrize("target", [LINUX, WINDOWS])
def test_wheel_metadata_record_and_native(approved, target):
    name, data = wheel(target)
    details = release.wheel_details_bytes(name, data, target, "Python 3.10")
    assert details["nativeSha256"] == hashlib.sha256(native(target)).hexdigest()
    assert details["sha256"] == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    "mutation",
    [
        "record-missing",
        "record-tamper",
        "extra-file",
        "native-tamper",
        "metadata-duplicate",
        "wheel-extra-tag",
        "dist-info",
        "traversal",
        "graft",
    ],
)
def test_invalid_wheels_rejected(approved, mutation):
    dist = f"c2pa_python-{release.RELEASE_VERSION}.dist-info"

    def mutate(files):
        if mutation == "record-missing":
            del files[f"{dist}/RECORD"]
        elif mutation == "record-tamper":
            files[f"{dist}/RECORD"] += files[f"{dist}/RECORD"].splitlines(
                keepends=True
            )[0]
        elif mutation == "extra-file":
            files["unrecorded"] = b"extra"
        elif mutation == "native-tamper":
            files["c2pa/libs/libc2pa_c.so"] += b"changed"
        elif mutation == "metadata-duplicate":
            files[f"{dist}/METADATA"] += b"Name: different\n"
        elif mutation == "wheel-extra-tag":
            files[f"{dist}/WHEEL"] += b"Tag: py3-none-any\n"
        elif mutation == "dist-info":
            files["wrong.dist-info/METADATA"] = files.pop(f"{dist}/METADATA")
        elif mutation == "traversal":
            files["../bad"] = b"bad"
        else:
            files["c2pa_python.libs/crypto.so"] = b"unapproved graft"

    name, data = wheel(LINUX, mutate)
    with pytest.raises(SystemExit):
        release.wheel_details_bytes(name, data, LINUX, "Python 3.10")


@pytest.mark.parametrize("target", [LINUX, WINDOWS])
def test_native_architecture_rejected(target):
    release.validate_native(native(target), target)
    with pytest.raises(SystemExit):
        release.validate_native(native(WINDOWS if target == LINUX else LINUX), target)
    with pytest.raises(SystemExit):
        release.validate_native(b"not a library", target)


@pytest.mark.parametrize(
    "bad",
    [
        REPORT.replace("openssl,", ""),
        REPORT.replace("default,", ""),
        REPORT.replace("vendored", "system"),
        REPORT.replace("0.80.0", "0.91.0-dev"),
        REPORT.replace("file_io,http]", "file_io,http,unstable_live_video]"),
        REPORT.replace("file_io,openssl", "file_io,rust_native_crypto,openssl"),
    ],
)
def test_feature_report_rejects_nonstable_or_missing_features(bad):
    release.validate_feature_report(REPORT)
    with pytest.raises(SystemExit):
        release.validate_feature_report(bad)


def test_actual_cargo_tree_preserves_baseline_default_features():
    assert (
        release.sha256_file(ACTUAL_REPORT)
        == "94fea6aa8fcbcda8bca4aa2e8b2ab02d856f35ee29317fbe3c55688de7a0f61e"
    )
    report = ACTUAL_REPORT.read_text(encoding="utf-8")
    release.validate_feature_report(report)
    sdk = re.findall(r"\bc2pa v0\.80\.0[^\n]*?_features=\[([^\]]*)\]", report)
    assert sdk
    for features in sdk:
        resolved = set(features.split(","))
        assert {
            "openssl",
            "http_reqwest",
            "http_reqwest_blocking",
            "add_thumbnails",
            "file_io",
        } <= resolved
        assert "http" not in resolved  # http is the FFI feature, not an SDK feature.
    for changed in (
        report + "\nc2pa v0.80.0_features=[openssl,file_io]\n",
        report + "\nc2pa-c-ffi v0.80.0_features=[file_io]\n",
        report + '\nc2pa feature "unstable_live_video"\n',
        report + '\nc2pa feature "rust_native_crypto"\n',
    ):
        with pytest.raises(SystemExit):
            release.validate_feature_report(changed)


def test_actual_cargo_commands_feed_evidence(approved, monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=REPORT.replace("\n", "\r\n").encode())

    monkeypatch.setattr(release.subprocess, "run", run)
    monkeypatch.setenv("CARGO_BUILD_JOBS", "1")
    monkeypatch.setenv("CARGO_INCREMENTAL", "0")
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    report = tmp_path / f"features-{LINUX}.txt"
    release.command_cargo_build(
        SimpleNamespace(rust_root=tmp_path, target=LINUX, feature_report=report)
    )
    assert calls == [
        release.cargo_tree_command(approved, LINUX),
        release.cargo_command(approved, LINUX),
    ]
    assert report.read_text() == REPORT
    output = tmp_path / "facts.json"
    release.command_build_facts(
        SimpleNamespace(
            target=LINUX,
            feature_report=report,
            epoch=1700000000,
            interpreter="Python 3.10",
            container=f"{release.MANYLINUX_IMAGE}@{release.MANYLINUX_DIGEST}",
            output=output,
        )
    )
    facts = json.loads(output.read_text())
    assert facts["cargoCommand"] == calls[1]
    assert facts["cargoTreeCommand"] == calls[0]
    assert facts["noDefaultFeatures"] is False
    assert (
        facts["resolvedFeatureReportSha256"]
        == hashlib.sha256(REPORT.encode()).hexdigest()
    )


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "gitlink",
        "native-head",
        "cargo-lock",
        "native-origin",
        "dirty",
        "branch-tip",
        "event-ref",
        "python-version",
        "tag",
        "tag-source",
    ],
)
def test_validate_source_pins_and_clean_checkouts(
    approved, monkeypatch, tmp_path, mutation
):
    python_root, rust_root = tmp_path / "python", tmp_path / "rust"
    python_root.mkdir()
    rust_root.mkdir()
    (python_root / "pyproject.toml").write_text(
        f'version = "{release.RELEASE_VERSION}"\n'
    )
    (rust_root / "Cargo.lock").write_text("# synthetic test lock\n")
    (rust_root / "Cargo.toml").write_text('[workspace.package]\nversion = "0.80.0"\n')
    approved["rustSource"]["cargoLockSha256"] = release.sha256_file(
        rust_root / "Cargo.lock"
    )
    monkeypatch.setattr(
        release, "CARGO_LOCK_SHA256", approved["rustSource"]["cargoLockSha256"]
    )
    responses = {
        (python_root, "remote", "get-url", "origin"): approved["pythonSource"]["url"],
        (rust_root, "remote", "get-url", "origin"): approved["rustSource"]["url"],
        (python_root, "rev-parse", "HEAD"): SOURCE,
        (rust_root, "rev-parse", "HEAD"): approved["rustSource"]["commit"],
        (
            python_root,
            "rev-parse",
            "refs/remotes/origin/fix/stable-single-file-fmp4",
        ): SOURCE,
        (
            python_root,
            "ls-tree",
            "HEAD",
            "c2pa-rs",
        ): f"160000 commit {approved['rustSource']['commit']}\tc2pa-rs",
        (python_root, "status", "--porcelain"): "",
        (rust_root, "status", "--porcelain"): "",
    }
    args = SimpleNamespace(
        python_root=python_root,
        rust_root=rust_root,
        source_sha=SOURCE,
        repository="castlabs/c2pa-python",
        event="manual",
        ref="refs/heads/fix/stable-single-file-fmp4",
    )
    if mutation in ("tag", "tag-source"):
        args.event = "tag"
        args.ref = f"refs/tags/{release.RELEASE_TAG}"
        responses[(python_root, "rev-parse", f"{args.ref}^{{commit}}")] = (
            SOURCE if mutation == "tag" else "d" * 40
        )
    if mutation == "gitlink":
        responses[(python_root, "ls-tree", "HEAD", "c2pa-rs")] = (
            "160000 commit " + "d" * 40 + "\tc2pa-rs"
        )
    elif mutation == "native-head":
        responses[(rust_root, "rev-parse", "HEAD")] = "d" * 40
    elif mutation == "cargo-lock":
        (rust_root / "Cargo.lock").write_text("changed lock")
    elif mutation == "native-origin":
        responses[(rust_root, "remote", "get-url", "origin")] = (
            "https://github.com/contentauth/c2pa-rs.git"
        )
    elif mutation == "dirty":
        responses[(rust_root, "status", "--porcelain")] = " M sdk/src/store.rs"
    elif mutation == "branch-tip":
        responses[
            (
                python_root,
                "rev-parse",
                "refs/remotes/origin/fix/stable-single-file-fmp4",
            )
        ] = (
            "d" * 40
        )
    elif mutation == "event-ref":
        args.ref = "refs/heads/feat/live-video-vsi"
    elif mutation == "python-version":
        (python_root / "pyproject.toml").write_text('version = "0.37.8.dev5"\n')
    monkeypatch.setattr(
        release, "run_git", lambda root, *args: responses[(root, *args)]
    )
    if mutation in (None, "tag"):
        release.command_validate_sources(args)
    else:
        with pytest.raises(SystemExit):
            release.command_validate_sources(args)


def test_release_packaging_never_rebuilds_or_falls_back(
    approved, monkeypatch, tmp_path
):
    tree = ast.parse((ROOT / "setup.py").read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_native_from_source"
    )
    namespace = {
        "os": os,
        "shutil": shutil,
        "ARTIFACTS_DIR": tmp_path / "artifacts",
        "PACKAGE_LIBS_DIR": tmp_path / "libs",
        "get_platform_identifier": lambda: LINUX,
    }
    monkeypatch.setitem(sys.modules, "scripts.castlabs_release", release)
    monkeypatch.setenv("CASTLABS_STABLE_RELEASE_TARGET", LINUX)
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "setup.py", "exec"),
        namespace,
    )
    build = namespace["build_native_from_source"]
    with pytest.raises(
        RuntimeError, match="qualified stable native artifact is missing"
    ):
        build()
    artifact = tmp_path / "artifacts" / LINUX / "libc2pa_c.so"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(native(LINUX))
    assert build() is True
    assert (tmp_path / "libs/libc2pa_c.so").read_bytes() == native(LINUX)
    monkeypatch.setenv("CASTLABS_STABLE_RELEASE_TARGET", WINDOWS)
    with pytest.raises(RuntimeError, match="target differs from build host"):
        build()


@pytest.fixture(params=["synthetic", "actual-cargo-tree"])
def evidence(approved, tmp_path, monkeypatch, request):
    for name, value in (
        ("CARGO_BUILD_JOBS", "1"),
        ("CARGO_INCREMENTAL", "0"),
        ("PYTHONHASHSEED", "0"),
    ):
        monkeypatch.setenv(name, value)
    members, mappings, facts = [], [], []
    for target in (LINUX, WINDOWS):
        name, data = wheel(target)
        path = tmp_path / name
        path.write_bytes(data)
        members.append((path, name))
        mappings.append(f"{target}={path}")
        report = tmp_path / f"features-{target}.txt"
        report.write_text(
            ACTUAL_REPORT.read_text(encoding="utf-8")
            if request.param == "actual-cargo-tree" and target == LINUX
            else REPORT
        )
        fact = tmp_path / f"build-{target}.json"
        release.command_build_facts(
            SimpleNamespace(
                target=target,
                feature_report=report,
                epoch=1700000000,
                interpreter="Python 3.10",
                container=(
                    f"{release.MANYLINUX_IMAGE}@{release.MANYLINUX_DIGEST}"
                    if target == LINUX
                    else None
                ),
                output=fact,
            )
        )
        facts.append(str(fact))
    artifact = (
        tmp_path / f"c2pa-python-{release.RELEASE_VERSION}-py3-none-wheels.tar.gz"
    )
    release.deterministic_tar_gz(artifact, members, 1700000000)
    output = tmp_path / "evidence.json"
    release.command_evidence(
        SimpleNamespace(
            artifact=artifact,
            source_sha=SOURCE,
            build_facts=facts,
            member=mappings,
            type="wheel-bundle",
            output=output,
        )
    )
    return json.loads(output.read_text()), tmp_path


def test_schema2_standard_shape_and_strict_evidence(evidence):
    data, directory = evidence
    schema = json.loads(
        (ROOT / "release/castlabs-release-evidence.schema.json").read_text()
    )
    jsonschema.validate(data, schema)
    assert set(data) == {
        "schemaVersion",
        "artifact",
        "sources",
        "builds",
        "runner",
        "workflow",
    }
    release.validate_evidence(data, directory, SOURCE)


@pytest.mark.parametrize(
    "mutation",
    [
        "source",
        "lock",
        "feature-digest",
        "default-features",
        "command",
        "native-digest",
        "missing-target",
        "version",
        "unknown",
    ],
)
def test_evidence_substitution_fails(evidence, mutation):
    data, directory = evidence
    if mutation == "source":
        data["sources"]["c2paRs"]["commit"] = "d" * 40
    elif mutation == "lock":
        data["sources"]["c2paRs"]["cargoLockSha256"] = "d" * 64
    elif mutation == "feature-digest":
        data["builds"][0]["resolvedFeatureReportSha256"] = "d" * 64
    elif mutation == "default-features":
        data["builds"][0]["noDefaultFeatures"] = True
    elif mutation == "command":
        data["builds"][0]["cargoCommand"].append("--no-default-features")
    elif mutation == "native-digest":
        data["artifact"]["members"][0]["nativeSha256"] = "d" * 64
    elif mutation == "missing-target":
        data["artifact"]["members"].pop()
    elif mutation == "version":
        data["artifact"]["version"] = "0.37.8.dev5"
    else:
        data["releaseContext"] = "castlabs-stable-fmp4"
    with pytest.raises(SystemExit):
        release.validate_evidence(data, directory, SOURCE)


def test_deterministic_archive_and_unsafe_members(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"payload")
    first, second = tmp_path / "one.tar.gz", tmp_path / "two.tar.gz"
    release.deterministic_tar_gz(first, [(source, "z"), (source, "a")], 1700000000)
    release.deterministic_tar_gz(second, [(source, "a"), (source, "z")], 1700000000)
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes()[3] & 8 == 0
    for name in ("../escape", "/escape", "a\\b"):
        with pytest.raises(SystemExit):
            release.deterministic_tar_gz(
                tmp_path / "unsafe.tar.gz", [(source, name)], 1700000000
            )


@pytest.fixture
def draft(approved, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    downloaded = tmp_path / "downloaded"
    downloaded.mkdir()
    remote = []
    for index, name in enumerate(sorted(release.expected_release_asset_names()), 1):
        data = name.encode()
        (assets / name).write_bytes(data)
        (downloaded / name).write_bytes(data)
        remote.append(
            {
                "id": index,
                "name": name,
                "size": len(data),
                "state": "uploaded",
                "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
            }
        )
    response = {
        "id": 99,
        "tag_name": release.RELEASE_TAG,
        "target_commitish": SOURCE,
        "draft": True,
        "prerelease": False,
        "name": release.RELEASE_NAME,
        "body": release.RELEASE_BODY,
        "assets": remote,
    }
    return assets, downloaded, response


def test_release_exact_asset_bytes_and_published_response(draft, tmp_path):
    assets, downloaded, response = draft
    plan = release.inspect_draft_release(
        assets, response, SOURCE, expected_release_id=99
    )
    assert (
        release.verify_draft_release(assets, downloaded, plan, require_complete=True)
        == []
    )
    plan_path, response_path = tmp_path / "plan.json", tmp_path / "response.json"
    plan_path.write_text(json.dumps(plan))
    response["draft"] = False
    response_path.write_text(json.dumps(response))
    args = SimpleNamespace(
        release_json=response_path,
        plan=plan_path,
        source_sha=SOURCE,
        expected_release_id=99,
    )
    release.command_validate_published_release(args)
    response["assets"][0]["id"] += 1000
    response_path.write_text(json.dumps(response))
    with pytest.raises(SystemExit, match="asset IDs or digests changed"):
        release.command_validate_published_release(args)


@pytest.mark.parametrize(
    "mutation",
    [
        "published",
        "source",
        "marker",
        "id",
        "duplicate",
        "extra",
        "size",
        "digest",
        "missing-digest",
    ],
)
def test_draft_reconciliation_refuses_unsafe_releases(draft, mutation):
    assets, _, response = draft
    if mutation == "published":
        response["draft"] = False
    elif mutation == "source":
        response["target_commitish"] = "main"
    elif mutation == "marker":
        response["body"] = "unowned"
    elif mutation == "id":
        response["id"] = 100
    elif mutation == "duplicate":
        response["assets"].append(response["assets"][0])
    elif mutation == "extra":
        response["assets"][0]["name"] = "unapproved"
    elif mutation == "size":
        response["assets"][0]["size"] += 1
    elif mutation == "digest":
        response["assets"][0]["digest"] = "sha256:" + "d" * 64
    else:
        response["assets"][0]["digest"] = None
    with pytest.raises(SystemExit):
        release.inspect_draft_release(assets, response, SOURCE, expected_release_id=99)


def test_equal_size_download_is_not_enough(draft):
    assets, downloaded, response = draft
    plan = release.inspect_draft_release(assets, response, SOURCE)
    path = downloaded / response["assets"][0]["name"]
    path.write_bytes(b"x" * path.stat().st_size)
    with pytest.raises(SystemExit, match="differs from local output"):
        release.verify_draft_release(assets, downloaded, plan, require_complete=True)


@pytest.mark.parametrize(
    "mutation", [None, "assets", "target", "published", "marker", "id"]
)
def test_cleanup_requires_exact_owned_empty_draft(draft, tmp_path, mutation):
    _, _, response = draft
    response["assets"] = []
    if mutation == "assets":
        response["assets"] = [{"id": 1}]
    elif mutation == "target":
        response["target_commitish"] = "d" * 40
    elif mutation == "published":
        response["draft"] = False
    elif mutation == "marker":
        response["body"] = "unowned"
    elif mutation == "id":
        response["id"] = 100
    path = tmp_path / "cleanup.json"
    path.write_text(json.dumps(response))
    args = SimpleNamespace(release_json=path, source_sha=SOURCE, expected_release_id=99)
    if mutation is None:
        release.command_validate_owned_empty_draft(args)
    else:
        with pytest.raises(SystemExit):
            release.command_validate_owned_empty_draft(args)


def test_workflow_is_dedicated_pinned_all_platform_and_no_skip():
    path = ROOT / ".github/workflows/castlabs-stable-fmp4-release.yml"
    text = path.read_text()
    workflow = yaml.safe_load(text)
    jobs = workflow["jobs"]
    assert (
        jobs["release"]["if"]
        == "github.event_name == 'push' && github.ref == 'refs/tags/castlabs-v0.31.0+stardustproof.3'"
    )
    assert set(jobs["release"]["needs"]) == {
        "prepare",
        "linux",
        "windows",
        "test-linux-wheel",
        "test-windows-wheel",
    }
    assert all("timeout-minutes" in job for job in jobs.values())
    assert len(re.findall(r"uses: [^@\s]+@[0-9a-f]{40}", text)) == text.count("uses:")
    assert "9b80687f" not in text and "feat/live-video-vsi" not in text
    assert "--no-default-features" not in text
    assert "download_artifacts" not in text and "continue-on-error" not in text
    assert "CASTLABS_RELEASE_SMOKE_REQUIRED" not in text
    assert "OPENSSL_SRC_PERL" in text and "perl-IPC-Cmd" in text
    assert (
        "steps.pin.outputs.sha" in text
        and text.count("needs.prepare.outputs.native_sha") == 2
    )
    assert text.index("validate-lock") < text.index(
        "Checkout pinned Castlabs Rust source"
    )
    assert text.count("CASTLABS_STABLE_RELEASE_TARGET") == 2
    assert "cmp release-final-plan.json release-prepublish-plan.json" in text
    assert text.index(
        "--require-complete", text.index("release-prepublish.json")
    ) < text.index("gh api --method PATCH")
    assert "validate-published-release" in text
    assert "--clobber" not in text
    for job in jobs.values():
        for step in job["steps"]:
            if step.get("shell") == "bash" and "run" in step:
                subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)
    smoke = (ROOT / "tests/test_castlabs_release_smoke.py").read_text()
    assert "pytest.skip" not in smoke and "skipif" not in smoke and "xfail" not in smoke
    for required in (
        '["initHash"]',
        '["count"]',
        "proof_boxes(signed)",
        "tampered",
        "builder.sign(",
        "add_dynamic_assertion",
        "sign_fragmented",
    ):
        assert required in smoke


@pytest.mark.parametrize(
    "event,ref,source,success",
    [
        ("workflow_dispatch", "refs/heads/fix/stable-single-file-fmp4", SOURCE, True),
        ("push", f"refs/tags/{release.RELEASE_TAG}", "", True),
        ("push", "refs/heads/fix/stable-single-file-fmp4", "", True),
        ("workflow_dispatch", "refs/heads/main", SOURCE, False),
        ("workflow_dispatch", f"refs/tags/{release.RELEASE_TAG}", SOURCE, False),
        (
            "workflow_dispatch",
            "refs/heads/fix/stable-single-file-fmp4",
            "d" * 40,
            False,
        ),
        ("push", "refs/heads/main", "", False),
        ("push", "refs/tags/castlabs-v0.31.0+stardustproof.2", "", False),
        ("push", "refs/tags/v0.31.0+stardustproof.3", "", False),
        ("pull_request", "refs/pull/1/merge", SOURCE, False),
    ],
)
def test_workflow_source_selection_rejects_unowned_events(
    tmp_path, event, ref, source, success
):
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/castlabs-stable-fmp4-release.yml").read_text()
    )
    script = workflow["jobs"]["prepare"]["steps"][0]["run"]
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GITHUB_EVENT_NAME": event,
            "GITHUB_REF": ref,
            "GITHUB_SHA": SOURCE,
            "INPUT_SOURCE_SHA": source,
            "GITHUB_OUTPUT": str(output),
        },
    )
    assert (result.returncode == 0) is success, result.stderr
    if success:
        assert output.read_text() == f"sha={SOURCE}\n"
    else:
        assert not output.exists()


def test_smoke_fixture_and_cbor_inspection_are_self_contained():
    # Exercise only binary inspection helpers without importing/loading c2pa.
    tree = ast.parse((ROOT / "tests/test_castlabs_release_smoke.py").read_text())
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"boxes", "bmff_assertions", "proof_boxes"}
    ]
    uuid = bytes.fromhex("d8fec3d61b0e483c92975828877ec481")
    namespace = {"cbor2": cbor2, "C2PA_UUID": uuid}
    exec(
        compile(ast.Module(body=functions, type_ignores=[]), "smoke_helpers", "exec"),
        namespace,
    )
    boxes = namespace["boxes"]
    fixture = ROOT / "tests/fixtures/tiny-segmented"
    init = (fixture / "init.m4s").read_bytes()
    assert {b"ftyp", b"moov"} <= {t for t, _, _, _ in boxes(init)}
    for index in (1, 2):
        media = (fixture / f"seg-{index:04d}.m4s").read_bytes()
        moof = next(b[h:] for t, _, b, h in boxes(media) if t == b"moof")
        traf = next(b[h:] for t, _, b, h in boxes(moof) if t == b"traf")
        tfhd = next(b[h:] for t, _, b, h in boxes(traf) if t == b"tfhd")
        flags = int.from_bytes(tfhd[1:4], "big")
        assert flags & 0x020000 and not flags & 1, "fixture must be moof-relative"
        assert any(t == b"mdat" and len(b) > h + 16 for t, _, b, h in boxes(media))

    def box(kind, payload):
        return (len(payload) + 8).to_bytes(4, "big") + kind + payload

    assertion = {"merkle": [{"initHash": bytes(32), "count": 2}]}
    description = bytes(16) + b"\x01c2pa.hash.bmff.v3\0"
    jumbf = box(
        b"jumb", box(b"jumd", description) + box(b"cbor", cbor2.dumps(assertion))
    )
    assert namespace["bmff_assertions"](jumbf) == [assertion]
    proof = {"uniqueId": 0, "localId": 0, "location": 1, "hashes": None}
    content = box(b"uuid", uuid + bytes(4) + b"merkle\0" + cbor2.dumps(proof))
    assert namespace["proof_boxes"](content) == [(0, proof)]

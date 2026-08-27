"""Invariants for the release-wheel workflow.

These exist because a review of the windows leg found three defects that no
test could have caught: the job installed Rust but never obtained the native
library (`setup.py` only *copies* one out of `artifacts/<triple>` and raises
otherwise), the evidence read a `c2pa-rs` checkout this repository does not
have, and the wheel build omitted `SOURCE_DATE_EPOCH` while the bundle claimed
byte-reproducibility. Each assertion below corresponds to one of those.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-release-wheel.yml"
NATIVE_VERSION_FILE = REPO_ROOT / "c2pa-native-version.txt"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _wheel_building_jobs() -> dict:
    """Jobs that invoke `setup.py bdist_wheel`, keyed by job id."""
    jobs = {}
    for job_id, spec in _workflow()["jobs"].items():
        runs = "\n".join(s.get("run", "") for s in spec.get("steps", []))
        if "setup.py bdist_wheel" in runs:
            jobs[job_id] = {"spec": spec, "runs": runs}
    assert jobs, "no wheel-building job found -- has the workflow been restructured?"
    return jobs


def test_the_workflow_builds_both_platforms():
    jobs = _wheel_building_jobs()
    assert "build-wheel" in jobs
    assert "build-wheel-windows" in jobs


@pytest.mark.parametrize("job_id", sorted(_wheel_building_jobs()))
def test_native_library_is_obtained_before_the_wheel_is_built(job_id):
    """setup.py never builds; it copies from artifacts/<triple>.

    Without a download the build fails inside copy_platform_libraries() with
    "Platform directory not found", so every wheel-building job has to fetch
    the library first.
    """
    runs = _wheel_building_jobs()[job_id]["runs"]
    assert "download_artifacts.py" in runs, (
        f"{job_id} builds a wheel without populating artifacts/"
    )
    assert runs.index("download_artifacts.py") < runs.index("setup.py bdist_wheel"), (
        f"{job_id} downloads the native library after building the wheel"
    )


@pytest.mark.parametrize("job_id", sorted(_wheel_building_jobs()))
def test_native_version_comes_from_the_pin_file(job_id):
    # Restating the tag in the workflow would let it drift from the pin the
    # rest of the repo builds against.
    runs = _wheel_building_jobs()[job_id]["runs"]
    assert "c2pa-native-version.txt" in runs, (
        f"{job_id} does not read the native version from the pin file"
    )
    assert not re.search(r"c2pa-v\d+\.\d+\.\d+", runs), (
        f"{job_id} hard-codes a c2pa-rs tag instead of reading the pin file"
    )


def test_pin_file_holds_a_single_usable_tag():
    tag = NATIVE_VERSION_FILE.read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"c2pa-v\d+\.\d+\.\d+", tag), tag


@pytest.mark.parametrize("job_id", sorted(_wheel_building_jobs()))
def test_wheel_build_sets_source_date_epoch(job_id):
    # The bundles are packed with `tar --mtime` and `gzip -n` and their digests
    # are recorded as evidence; a wheel whose ZIP timestamps come from the
    # clock makes that digest unreproducible.
    runs = _wheel_building_jobs()[job_id]["runs"]
    assert "SOURCE_DATE_EPOCH" in runs, f"{job_id} does not set SOURCE_DATE_EPOCH"


@pytest.mark.parametrize("job_id", sorted(_wheel_building_jobs()))
def test_evidence_does_not_assume_a_c2pa_rs_checkout(job_id):
    """This repository has no c2pa-rs directory and no .gitmodules entry.

    An unconditional `git -C c2pa-rs ...` fails under `set -e`, so any such
    call has to be guarded by a directory test.
    """
    runs = _wheel_building_jobs()[job_id]["runs"]
    for match in re.finditer(r"git (?:-C c2pa-rs|submodule status)", runs):
        preceding = runs[: match.start()]
        assert "if [ -d c2pa-rs ]" in preceding, (
            f"{job_id} reads a c2pa-rs checkout without checking it exists"
        )


def test_repository_really_has_no_c2pa_rs_checkout():
    # If this ever becomes false the guard above is still correct, but the
    # reasoning behind it has changed and the evidence shape should be revisited.
    assert not (REPO_ROOT / ".gitmodules").exists()
    assert not (REPO_ROOT / "c2pa-rs").exists()


@pytest.mark.parametrize("job_id", sorted(_wheel_building_jobs()))
def test_evidence_records_the_native_artifact_provenance(job_id):
    # With no source checkout, the library's release tag and digest are the
    # only provenance there is -- downstream consumers verify against them.
    runs = _wheel_building_jobs()[job_id]["runs"]
    for field in (
        "c2pa_native_source_repository",
        "c2pa_native_release_tag",
        "c2pa_native_library_sha256",
    ):
        assert field in runs, f"{job_id} evidence omits {field}"


def test_windows_leg_is_opt_in():
    # A Linux-only release must behave exactly as before, and the dispatch API
    # rejects an undeclared input, so the caller opts in explicitly.
    workflow = _workflow()
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert "build_windows_wheel" in inputs
    assert workflow["jobs"]["build-wheel-windows"]["if"] == "inputs.build_windows_wheel == 'true'"
    # GitHub caps workflow_dispatch at 10 inputs; exceeding it invalidates the
    # whole file, including the pre-existing Linux path.
    assert len(inputs) <= 10, f"{len(inputs)} inputs, limit is 10"

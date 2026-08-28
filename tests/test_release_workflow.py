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
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-release-wheel.yml"
NATIVE_VERSION_FILE = REPO_ROOT / "c2pa-native-version.txt"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[tuple[str, str, str]]:
    """Every (job id, step name, script) in the workflow.

    Steps are kept separate on purpose: an earlier version of this file joined
    a job's scripts together, so a guard in one step satisfied an unguarded
    call in another and the test passed while the defect was still there.
    """
    out = []
    for job_id, spec in _workflow()["jobs"].items():
        for step in spec.get("steps", []):
            if "run" in step:
                out.append((job_id, step.get("name", "?"), step["run"]))
    return out


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


def test_every_c2pa_rs_call_is_guarded_in_its_own_step():
    """No unguarded `git -C c2pa-rs` anywhere.

    This repository has no c2pa-rs checkout, so an unguarded call either fails
    under `set -e` or -- inside a heredoc, where the status is discarded --
    silently publishes an empty value.
    """
    calls = 0
    for job_id, name, run in _steps():
        for match in re.finditer(r"git (?:-C c2pa-rs|submodule status)", run):
            calls += 1
            assert "if [ -e c2pa-rs/.git ]" in run[: match.start()], (
                f"{job_id} / {name}: c2pa-rs read without a guard in the same step"
            )
    assert calls, "no c2pa-rs calls found -- has the evidence shape changed?"


def test_the_guard_tests_for_a_repository_not_a_directory():
    """`-d c2pa-rs` is not good enough.

    With an empty c2pa-rs/ directory git walks up and `rev-parse HEAD` returns
    *this* repository's HEAD, which would then be recorded as the c2pa-rs
    commit -- false provenance, silently.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "if [ -e c2pa-rs/.git ]" in text
    assert "if [ -d c2pa-rs ]" not in text


def test_download_steps_install_whichever_requirements_declare_requests():
    # download_artifacts.py imports requests; installing the wrong requirements
    # file fails at import time on a clean runner.
    declaring = [
        f.name
        for f in (REPO_ROOT / "requirements.txt", REPO_ROOT / "requirements-dev.txt")
        if f.exists() and re.search(r"^requests\b", f.read_text(encoding="utf-8"), re.M)
    ]
    assert declaring, "no requirements file declares requests"

    for job_id, name, run in _steps():
        if "download_artifacts.py" not in run:
            continue
        for f in declaring:
            assert f in run, f"{job_id} / {name} does not install {f}"


def test_download_steps_authenticate_the_release_lookup():
    # Unauthenticated GitHub API calls share the runner's IP rate limit and can
    # 403 in the middle of a release; the script sends the header when set.
    for job_id, spec in _workflow()["jobs"].items():
        for step in spec.get("steps", []):
            if "download_artifacts.py" in step.get("run", ""):
                assert "GITHUB_TOKEN" in (step.get("env") or {}), (
                    f"{job_id} / {step.get('name')} fetches releases unauthenticated"
                )


# The runtime library each platform's wheel must contain.
RUNTIME_LIBRARY = {
    "x86_64-unknown-linux-gnu": "libc2pa_c.so",
    "x86_64-pc-windows-msvc": "c2pa_c.dll",
}


def _download_steps() -> list[tuple[str, str, str]]:
    """(job id, NATIVE_TRIPLE, script) for every step that fetches the library."""
    out = []
    for job_id, spec in _workflow()["jobs"].items():
        triple = (spec.get("env") or {}).get("NATIVE_TRIPLE")
        for step in spec.get("steps", []):
            if "download_artifacts.py" in step.get("run", ""):
                assert triple, f"{job_id} downloads a library without declaring NATIVE_TRIPLE"
                out.append((job_id, triple, step["run"]))
    assert out, "no download step found"
    return out


@pytest.mark.parametrize(
    "job_id,triple,run", _download_steps(), ids=[j for j, _, _ in _download_steps()]
)
def test_each_leg_verifies_its_own_platform_library(job_id, triple, run):
    # build-wheel must handle libc2pa_c.so and build-wheel-windows c2pa_c.dll;
    # a swap leaves the wheel without a runtime library.
    expected = RUNTIME_LIBRARY[triple]
    match = re.search(r'lib="artifacts/\$NATIVE_TRIPLE/([^"]+)"', run)
    assert match, f"{job_id} does not define the library path"
    assert match.group(1) == expected, (
        f"{job_id} targets {triple} but verifies {match.group(1)}, expected {expected}"
    )


@pytest.mark.parametrize(
    "job_id,triple,run", _download_steps(), ids=[j for j, _, _ in _download_steps()]
)
def test_pruning_keeps_exactly_the_verified_library(job_id, triple, run):
    """The kept name must derive from the verified library, not be restated.

    Restating it is how the two came apart: a patch matched text identical in
    both legs and the pruning patterns ended up swapped, so each leg deleted
    its own runtime library and kept the other platform's.
    """
    prune = re.search(r"find \"artifacts/\$NATIVE_TRIPLE\" -type f ! -name (\S+)", run)
    assert prune, f"{job_id} does not prune non-runtime files"
    kept = prune.group(1)
    assert "$lib" in kept or "basename" in kept, (
        f"{job_id} prunes against the literal {kept}; derive it from \"$lib\" so the "
        f"kept file cannot diverge from the verified one"
    )
    for other in set(RUNTIME_LIBRARY.values()) - {RUNTIME_LIBRARY[triple]}:
        assert other not in kept, f"{job_id} ({triple}) keeps another platform's {other}"


def test_only_the_runtime_library_reaches_the_wheel():
    """setup.py globs the whole platform directory into the wheel.

    The release asset also carries link-time and debug files -- c2pa_c.lib is
    245 MB -- and the published v0.31.0+stardustproof.1 wheel contains only the
    runtime library, so the extras are pruned before the build.
    """
    pruning = [
        (job_id, name)
        for job_id, name, run in _steps()
        if "download_artifacts.py" in run and re.search(r"find \"artifacts.*-delete", run)
    ]
    downloads = [(j, n) for j, n, r in _steps() if "download_artifacts.py" in r]
    assert len(pruning) == len(downloads), (
        f"{len(downloads) - len(pruning)} download step(s) do not prune non-runtime files"
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

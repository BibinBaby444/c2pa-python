"""Invariants for the release-wheel workflow.

These exist because building the windows leg on a real Windows host found
defects no inspection caught: a wheel built from an unpatched native library
signs successfully while silently omitting the cawg.identity assertion, and
`setup.py` falls back to prebuilt libraries from `artifacts/` when the source
build fails -- the same silent degradation, one layer down. Each assertion
below pins one of the invariants that prevent a recurrence:

- both wheels are built from the `c2pa-rs` submodule (the patched fork), and
  nothing in the release workflow downloads a prebuilt native library;
- the windows leg refuses to run where the `artifacts/` fallback is possible,
  and its smoke test resolves the patched APIs, not just `import c2pa`;
- evidence records the submodule commit unconditionally, because the signer's
  windows release leg requires it;
- wheel builds set SOURCE_DATE_EPOCH, or the bundle digest is not
  reproducible for identical inputs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-release-wheel.yml"

# The APIs the StardustProof signer calls that exist only on the patched
# fork: an unpatched build lacks all three, and signing degrades silently.
PATCHED_APIS = (
    "add_dynamic_assertion",
    "from_fragmented_files",
    "sign_fragmented",
)


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


def test_repository_carries_the_patched_submodule():
    """The wheel is only correct when built from the patched fork.

    v0.31.0+stardustproof.2 (test-only) shipped upstream's prebuilt DLL: it
    signed successfully and verified Trusted while omitting cawg.identity from
    every asset. The submodule is the mechanism that prevents that, so its
    presence and origin are release invariants, not implementation details.
    """
    gitmodules = (REPO_ROOT / ".gitmodules").read_text(encoding="utf-8")
    assert "url = https://github.com/castlabs/c2pa-rs.git" in gitmodules
    assert "branch = fix/stable-single-file-fmp4" in gitmodules
    assert "path = c2pa-rs" in gitmodules
    assert (REPO_ROOT / "c2pa-rs").exists()


def test_nothing_in_the_release_workflow_downloads_a_native_library():
    """download_artifacts.py exists for development, not for releases.

    A downloaded library is upstream's build: correct C2PA, no StardustProof
    patches. The release workflow must never reference the download path, the
    version pin file it reads, or upstream's repository.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    for needle in ("download_artifacts", "c2pa-native-version.txt", "contentauth/c2pa-rs"):
        assert needle not in text, f"release workflow references {needle}"


@pytest.mark.parametrize("job_id", sorted(_wheel_building_jobs()))
def test_wheel_jobs_check_out_the_submodule(job_id):
    spec = _wheel_building_jobs()[job_id]["spec"]
    checkout = next(
        (s for s in spec["steps"] if str(s.get("uses", "")).startswith("actions/checkout")),
        None,
    )
    assert checkout is not None, f"{job_id} has no checkout step"
    assert checkout.get("with", {}).get("submodules") == "recursive", (
        f"{job_id} checks out without submodules -- setup.py would fall back "
        f"to prebuilt artifacts or fail"
    )


@pytest.mark.parametrize("job_id", sorted(_wheel_building_jobs()))
def test_wheel_build_sets_source_date_epoch(job_id):
    runs = _wheel_building_jobs()[job_id]["runs"]
    assert "SOURCE_DATE_EPOCH" in runs, (
        f"{job_id} builds a wheel without SOURCE_DATE_EPOCH -- the bundle "
        f"digest depends on the clock"
    )


@pytest.mark.parametrize("job_id", sorted(_wheel_building_jobs()))
def test_wheel_build_raises_the_cargo_timeout(job_id):
    """setup.py's default cargo timeout is 600s; the release build takes ~16
    minutes on a 4-vcpu runner. Without the override the build dies mid-way
    and setup.py reaches for the artifacts/ fallback."""
    spec = _wheel_building_jobs()[job_id]
    text = spec["runs"] + yaml.safe_dump(spec["spec"])
    assert "C2PA_CARGO_BUILD_TIMEOUT_SECONDS" in text, (
        f"{job_id} builds with setup.py's 600s default cargo timeout"
    )


def test_windows_build_refuses_the_prebuilt_fallback():
    """setup.py silently prefers artifacts/<triple> when the source build
    fails. The windows job must fail closed instead: refuse to run at all if
    that directory exists."""
    runs = _wheel_building_jobs()["build-wheel-windows"]["runs"]
    assert "artifacts" in runs and "refusing" in runs, (
        "windows build no longer guards against the artifacts/ fallback"
    )


def test_windows_smoke_asserts_the_patched_apis():
    """`import c2pa` succeeds on an unpatched build; the wheel is only usable
    when the fork's APIs resolve. All three are called by the signer."""
    runs = _wheel_building_jobs()["build-wheel-windows"]["runs"]
    for api in PATCHED_APIS:
        assert api in runs, f"windows smoke test does not assert {api}"


def test_windows_wheel_contents_are_recorded_with_the_dll_present():
    """There is no auditwheel on Windows; the recorded contents listing is the
    substitute, and it must hard-fail when the native library is missing."""
    runs = _wheel_building_jobs()["build-wheel-windows"]["runs"]
    assert "wheel-contents.txt" in runs
    assert "c2pa_c.dll" in runs


@pytest.mark.parametrize("job_id,step_name,run", _steps(),
                         ids=[f"{j}:{n}" for j, n, _ in _steps()])
def test_no_step_guards_the_submodule_conditionally(job_id, step_name, run):
    """On this lineage the submodule always exists, and the signer's windows
    release leg refuses evidence without c2pa_rs_submodule_commit. A
    conditional guard would let a broken checkout produce evidence that
    silently omits the field instead of failing the release."""
    if "c2pa-rs" not in run:
        pytest.skip("step does not touch the submodule")
    assert "if [ -e c2pa-rs/.git ]" not in run, (
        f"{job_id}/{step_name} guards the submodule -- on this lineage a "
        f"missing checkout is an error, not a variant"
    )


@pytest.mark.parametrize("job_id", sorted(_wheel_building_jobs()))
def test_evidence_records_the_submodule_commit(job_id):
    runs = _wheel_building_jobs()[job_id]["runs"]
    assert "c2pa_rs_submodule_commit" in runs, (
        f"{job_id} evidence omits c2pa_rs_submodule_commit -- the signer's "
        f"windows release leg requires it"
    )
    assert "rev-parse HEAD" in runs


def test_windows_evidence_names_the_fork_as_the_native_source():
    runs = _wheel_building_jobs()["build-wheel-windows"]["runs"]
    assert "mstattma/c2pa-rs" in runs
    assert "c2pa_native_library_sha256" in runs


@pytest.mark.parametrize("job_id", sorted(_wheel_building_jobs()))
def test_nothing_reads_src_c2pa_libs_after_bdist_wheel(job_id):
    """setup.py's bdist_wheel path removes src/c2pa/libs in a finally block
    after packaging (see AGENTS.md, "the src/c2pa/libs wipe"), so any
    post-build check under that path fails on every successful build. Review
    caught exactly that in an earlier revision of the windows job: the DLL
    was hashed from src/c2pa/libs after setup.py had already deleted it."""
    spec = _wheel_building_jobs()[job_id]["spec"]
    scripts = "\n".join(s.get("run", "") for s in spec["steps"])
    idx = scripts.find("setup.py bdist_wheel")
    assert idx != -1
    tail_code = [
        line for line in scripts[idx:].splitlines()
        if not line.strip().startswith("#")
    ]
    offenders = [line for line in tail_code if "src/c2pa/libs" in line]
    assert not offenders, (
        f"{job_id} touches src/c2pa/libs after bdist_wheel -- setup.py has "
        f"already deleted it: {offenders}"
    )


def test_windows_native_digest_is_taken_from_the_wheel():
    """The evidence must hash the bytes that ship. The wheel is the shipped
    artifact; anything else is a copy that can drift or vanish."""
    runs = _wheel_building_jobs()["build-wheel-windows"]["runs"]
    assert "hashlib.sha256(zf.read(" in runs, (
        "windows job no longer hashes the DLL out of the completed wheel"
    )


def test_windows_evidence_refuses_an_empty_native_digest():
    """A missing step output arrives as an empty string, not an error, and
    evidence written with an empty digest attests nothing."""
    spec = _wheel_building_jobs()["build-wheel-windows"]["spec"]
    evidence_runs = "\n".join(
        s.get("run", "") for s in spec["steps"]
        if s.get("name", "") == "Validate wheel and write evidence"
    )
    assert 'if [ -z "$NATIVE_LIB_SHA256" ]' in evidence_runs, (
        "windows evidence step no longer guards against an empty digest"
    )


def test_windows_leg_is_opt_in():
    """An accidental windows build on a tag that only expects a linux asset
    would race the release upload; the leg runs only when asked."""
    spec = _workflow()["jobs"]["build-wheel-windows"]
    assert spec.get("if") == "inputs.build_windows_wheel == 'true'"
    assert spec.get("needs") == "build-wheel"


def test_windows_openssl_build_pins_a_full_perl():
    """openssl-src runs `perl ./Configure`; under Git Bash the bare name
    resolves to MSYS's minimal perl, which lacks Configure's modules, and
    the first release run failed exactly there. The job must pin a full
    perl via OPENSSL_SRC_PERL and probe the module MSYS perl lacks before
    starting the 15-minute build."""
    spec = _wheel_building_jobs()["build-wheel-windows"]["spec"]
    runs = "\n".join(s.get("run", "") for s in spec["steps"])
    assert "OPENSSL_SRC_PERL=" in runs, "windows job no longer pins OPENSSL_SRC_PERL"
    assert "Locale::Maketext::Simple" in runs, (
        "windows job no longer probes the perl module set before building"
    )
    build_idx = runs.find("setup.py bdist_wheel")
    assert runs.find("OPENSSL_SRC_PERL=") < build_idx, (
        "the perl pin must land before the wheel build"
    )


def test_windows_leg_pins_the_shared_rust_toolchain():
    """Both legs must compile the same submodule with the same toolchain, or
    the two wheels' native libraries drift for reasons no evidence records."""
    workflow = _workflow()
    toolchain = workflow.get("env", {}).get("RUST_TOOLCHAIN")
    assert toolchain, "workflow no longer pins RUST_TOOLCHAIN at the top level"
    win_runs = "\n".join(
        s.get("run", "") for s in workflow["jobs"]["build-wheel-windows"]["steps"]
    )
    assert "rustup toolchain install" in win_runs and "RUST_TOOLCHAIN" in win_runs

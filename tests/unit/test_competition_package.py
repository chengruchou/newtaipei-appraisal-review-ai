"""Runtime build contexts include required versioned data and exclude private extras."""

import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TABLES = ("services-20260722.json", "quotas-20260722.json")


@pytest.mark.parametrize("variant", ["Dockerfile", "lambda.Dockerfile"])
def test_docker_data_allowlist_preserves_only_versioned_policy_tables(variant):
    patterns = (ROOT / "infra/runtime" / f"{variant}.dockerignore").read_text().splitlines()
    assert patterns[0] == "**"
    assert {line for line in patterns if line.startswith("!") and ".json" in line} == {
        f"!src/appraisal_review/data/competition/{name}" for name in TABLES
    }
    assert "!src/appraisal_review/**" not in patterns
    assert "!src/**" not in patterns


def test_runtime_context_retains_exact_competition_package_resources(tmp_path):
    build = runpy.run_path(str(ROOT / "scripts/build_runtime_image.py"))["build_context"]
    build(tmp_path / "context")
    for name in TABLES:
        relative = Path("src/appraisal_review/data/competition") / name
        assert (tmp_path / "context" / relative).read_bytes() == (ROOT / relative).read_bytes()


def fixture(tmp_path):
    root = tmp_path / "repository"
    source = root / "src/appraisal_review"
    data = source / "data/competition"
    data.mkdir(parents=True)
    (source / "__init__.py").write_text("")
    for name in TABLES:
        (data / name).write_text("{}")
    (data / "local-review-records.json").write_text("local-only")
    infra = root / "infra/runtime"
    infra.mkdir(parents=True)
    for name in (
        "Dockerfile",
        "Dockerfile.dockerignore",
        "lambda.Dockerfile",
        "lambda.Dockerfile.dockerignore",
        "requirements.lock",
        "build-tooling.lock",
    ):
        (infra / name).write_text("synthetic")
    build = runpy.run_path(str(ROOT / "scripts/build_runtime_image.py"))["build_context"]
    build.__globals__["ROOT"] = root
    return data, build


def test_runtime_data_allowlist_never_includes_local_reviews(tmp_path):
    _, build = fixture(tmp_path)
    build(tmp_path / "context")
    assert not list((tmp_path / "context").rglob("local-review-records.json"))
    assert sorted(p.name for p in (tmp_path / "context").rglob("*.json")) == sorted(TABLES)


@pytest.mark.parametrize("change", ["missing", "symlink"])
def test_runtime_build_refuses_incomplete_or_unconfined_policy_resources(tmp_path, change):
    data, build = fixture(tmp_path)
    target = data / TABLES[0]
    target.unlink()
    if change == "symlink":
        private = tmp_path / "private.json"
        private.write_text("local-only")
        target.symlink_to(private)
    with pytest.raises((ValueError, FileNotFoundError)):
        build(tmp_path / "context")

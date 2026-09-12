"""The assembled app must evaluate readiness without event-loop faults.

Regression for a composition bug: the wired confirmed-references reader used
asyncio.run() inside the running request loop, so any basis or submit call on a
job with a registered snapshot died with a nested-event-loop RuntimeError (500).

The snapshot registered here is a SYNTHETIC mechanism fixture - it exercises the
real composition (prepare_workbench -> FastAPI app -> approval service -> SQLite
stores) and is not a real-case acceptance.
"""

import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from appraisal_review.adapters.local.export_composition import discover_export_assets
from appraisal_review.adapters.local.snapshot_registry import RegisteredSnapshots
from appraisal_review.adapters.local.synthetic_workbench import prepare_workbench
from appraisal_review.domain.calculation_snapshot import (
    CalculationSnapshot,
    SnapshotEntry,
    SnapshotSubject,
)

_REPO = Path(__file__).resolve().parents[2]
#: The digest-matched official templates are an untracked operator asset; the
#: composition refuses mismatched templates, so the test uses whichever local
#: copy actually matches the mappings and skips honestly when none does.
_TEMPLATE_CANDIDATES = (
    _REPO / "artifacts" / "official-templates",
    _REPO.parents[1] / "official-templates",
    _REPO.parents[2] / "deploy" / "payload" / "templates",
)


def _matching_templates_dir() -> Path | None:
    for candidate in _TEMPLATE_CANDIDATES:
        if candidate.is_dir() and discover_export_assets(templates_dir=candidate) is not None:
            return candidate
    return None


def test_basis_and_submit_survive_the_running_event_loop(tmp_path, monkeypatch):
    templates = _matching_templates_dir()
    if templates is None:
        pytest.skip("no digest-matched official templates available on this machine")
    monkeypatch.setenv("REVIEW_TEMPLATES_DIR", str(templates))
    asyncio.run(_basis_and_submit(tmp_path))


async def _basis_and_submit(tmp_path):
    workbench = await prepare_workbench(tmp_path / "workbench", port=18771)
    await workbench.settle()
    manifest = workbench.manifest()
    job_id = manifest["completed_job_id"]
    headers = {"Authorization": f"Bearer {manifest['session_token']}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=workbench.app), base_url=manifest["api_base_url"]
    ) as client:
        status = (await client.get(f"/v1/review-jobs/{job_id}", headers=headers)).json()
        revision = status["current_run"]["revision"]
        snapshot = CalculationSnapshot(
            revision=revision,
            district="Synthetic",
            valuation_date=date(2022, 9, 1),
            rule_bundle_id="synthetic-mechanism",
            rule_bundle_version="v1",
            subjects=(
                SnapshotSubject(subject_id="P001", role="comparison_base", label="base"),
                SnapshotSubject(subject_id="P002", role="comparable", label="c1"),
            ),
            entries={
                "table_4.P002.normal_unit_price": SnapshotEntry(
                    state="present",
                    value=Decimal("120000"),
                    unit="TWD_per_m2",
                    origin="computed",
                    trace="synthetic mechanism entry",
                )
            },
        )
        RegisteredSnapshots(tmp_path / "workbench" / "snapshots").register(snapshot)

        # Pre-fix this returned 500: asyncio.run() cannot be called from a running
        # event loop, raised by the wired confirmed-references reader.
        basis = await client.get(f"/v1/review-jobs/{job_id}/exports/basis", headers=headers)
        assert basis.status_code == 200, basis.text
        body = basis.json()
        assert body["readiness"] is not None
        # The synthetic snapshot is nowhere near complete: readiness reports honest
        # named blockers instead of crashing or claiming readiness.
        assert body["readiness"]["state"] == "pending_data"
        assert body["readiness"]["blockers"]

        # Submitting against a blocked case must refuse with a conflict, not a 500.
        submit = await client.post(
            f"/v1/review-jobs/{job_id}/report-approvals",
            headers=headers,
            json={
                "schema_version": "service-v1",
                "idempotency_key": f"loop-regression-{uuid4()}",
                "run": {
                    "schema_version": "service-v1",
                    "run_id": status["current_run"]["run_id"],
                    "revision": revision,
                },
                "calculation_snapshot_digest": body["calculation_snapshot_digest"],
                "template_bundle": body["template_bundle"],
            },
        )
        assert submit.status_code == 409, submit.text

        # A second read exercises the same path again (idempotent, still no fault).
        again = await client.get(f"/v1/review-jobs/{job_id}/exports/basis", headers=headers)
        assert again.status_code == 200, again.text

        # Without authentication the route still refuses outright.
        assert (await client.get(f"/v1/review-jobs/{job_id}/exports/basis")).status_code == 403

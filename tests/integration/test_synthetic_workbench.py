"""Actual synthetic launcher, durable publication and restart regression."""

import asyncio
import json
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from appraisal_review.adapters.local.synthetic_workbench import (
    SyntheticMaterialGrant,
    prepare_workbench,
)
from appraisal_review.domain.artifact_publication import SourceVersion
from appraisal_review.domain.service_contracts import HumanResponse, ResponseAction, RunReference


def test_seven_cases_publish_and_restart(tmp_path):
    asyncio.run(_seven_cases_publish_and_restart(tmp_path))


async def _seven_cases_publish_and_restart(tmp_path):
    directory = tmp_path / "workbench"
    workbench = await prepare_workbench(directory, port=18761)
    await workbench.settle()
    manifest = workbench.manifest()
    assert set(manifest["tasks"]) == {"confirm", "correct", "reject", "conflict", "lost_response"}
    assert len(set(workbench.case_ids.values())) == 7
    assert all(manifest["tasks"].values())
    token = manifest["session_token"]
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=workbench.app), base_url=manifest["api_base_url"]
    ) as client:
        result = await client.get(
            f"/v1/review-jobs/{manifest['completed_job_id']}/result", headers=headers
        )
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["business_status"] == "completed"
        artifact = body["artifacts"][0]
        assert artifact["schema_version"] == "artifact-manifest-v2"
        assert len(artifact["contexts"]) == 2
        url = (
            f"/v1/review-jobs/{manifest['completed_job_id']}"
            f"/artifacts/{artifact['artifact_id']}/content"
        )
        response = await client.get(url, headers=headers)
        assert response.status_code == 200
        assert response.content.startswith(b"%PDF")
        assert (await client.get(url)).status_code == 403
    script = Path(__file__).resolve().parents[2] / "scripts/run_local_workbench.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--directory",
            str(directory),
            "--port",
            "18761",
            "--prepare",
        ],
        capture_output=True,
        text=True,
        timeout=90,
        check=True,
    )
    assert token not in result.stdout + result.stderr
    assert json.loads((directory / "fixture.json").read_text()) == manifest
    reopened = await prepare_workbench(directory, port=18761)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=reopened.app), base_url=manifest["api_base_url"]
    ) as client:
        restored = await client.get(url, headers=headers)
        assert restored.status_code == 200
        assert restored.content == response.content

        job_id = reopened.state["job_ids"]["confirm"]
        for number in range(4):
            listing = await client.get(f"/v1/review-jobs/{job_id}/tasks", headers=headers)
            tasks = [v["task"] for v in listing.json()["tasks"] if v["task"]["state"] == "open"]
            assert len(tasks) == 4 - number
            task = tasks[0]
            command = HumanResponse(
                task_id=task["task_id"],
                expected_version=task["version"],
                revision=task["run"]["revision"],
                side_digest=task["side"]["input_digest"],
                action=ResponseAction.CONFIRM,
                idempotency_key=f"real-http-confirm-{number}",
            ).model_dump(mode="json")
            endpoint = f"/v1/review-tasks/{task['task_id']}/responses"
            accepted = await client.post(endpoint, json=command, headers=headers)
            assert accepted.status_code == 200, accepted.text
            replay = await client.post(endpoint, json=command, headers=headers)
            assert replay.json() == accepted.json()
            await reopened.drain()
        record = await reopened.store.read_job(job_id=UUID(job_id))
        assert record.status.value == "succeeded"
        snapshot = await reopened.store.read_snapshot(revision=record.current_run.revision)
        for pair in snapshot.material.facts.pairs:
            for side in ("target", "comparable"):
                assert getattr(pair.pair, side).confidence == 0
                assert (
                    getattr(pair, side + "_reliability").confirmation.reviewer
                    == reopened.principal.actor.actor_id
                )
        with closing(reopened.store._connect()) as db:
            grant_row = db.execute(
                "SELECT material_digest,scope,run_json FROM synthetic_material_grants "
                "WHERE run_id=?",
                (str(record.current_run.run_id),),
            ).fetchone()
            assert tuple(grant_row[:2]) == (
                snapshot.revision.reference.material_digest,
                "synthetic-only",
            )
        capability = reopened.fixtures["confirm"].authorize(snapshot)
        granted_run = RunReference.model_validate_json(grant_row[2])
        assert granted_run.run_id == record.current_run.run_id
        assert granted_run.revision == record.current_run.revision
        assert granted_run.attempt_id is not None
        grant = SyntheticMaterialGrant(reopened.store, granted_run, capability)
        assert not SyntheticMaterialGrant(reopened.store, record.current_run, capability).permits(
            snapshot.material
        )
        assert grant.permits(snapshot.material)
        changed = snapshot.material.model_copy(deep=True)
        changed.facts.pairs[0].pair.target.confidence = 1
        assert not grant.permits(changed)
    consumed = await prepare_workbench(directory, port=18761)
    await consumed.settle()
    assert consumed.manifest() == manifest


def test_dynamic_case_admission_is_not_confirmation_and_survives_restart(tmp_path):
    asyncio.run(_dynamic_case(tmp_path))


async def _dynamic_case(tmp_path):

    root = tmp_path / "dynamic"
    context = await prepare_workbench(root)
    await context.settle()
    from appraisal_review.testing.integration_fixture import create_integration_fixture

    fixture = await create_integration_fixture(root / "new-inputs")
    fixture = replace(
        fixture,
        principal=replace(
            fixture.principal,
            actor=context.principal.actor,
        ),
    )
    native_versions = tuple(
        SourceVersion(document_id=d.document_id, version=d.version, content_hash=d.content_hash)
        for d in fixture.snapshot.revision.documents
    )
    with pytest.raises(ValueError, match="Exact authored raster source and template pins"):
        await context.register_synthetic_case(fixture, raster_source_versions=native_versions)
    assert fixture.snapshot.revision.reference.case_id not in context.by_case
    admitted = await context.register_synthetic_case(fixture)
    before = await context.case_status(admitted["case_id"])
    assert before["job_status"] == "queued"
    assert before["task_ids"] == []
    assert "completed_job_id" not in before
    await context.drain()
    after = await context.case_status(admitted["case_id"])
    assert after["job_status"] == "waiting_for_human"
    assert len(after["task_ids"]) == 4
    restarted = await prepare_workbench(root)
    assert await restarted.case_status(admitted["case_id"]) == after

"""Opaque restore handles are created only from actual current publications."""

import asyncio
import importlib
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from appraisal_review.adapters.local.synthetic_workbench import prepare_workbench
from appraisal_review.domain.artifact_publication import PublicationError


def test_restore_handle_requires_current_completed_owned_result(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    launcher = importlib.import_module("run_integration_rehearsal")

    async def scenario():
        context = await prepare_workbench(tmp_path / "core")
        await context.settle()

        def forms(case_id):
            fixture = context.by_case[case_id]
            reference = next(r for r in fixture.snapshot.revision.documents if r.purpose == "forms")
            return fixture.documents.read(context.principal, reference).metadata

        results = launcher.PublishedResults(context, forms)
        pending = UUID(context.state["job_ids"]["confirm"])
        with pytest.raises(ValueError):
            results.bind(pending)
        complete = UUID(context.state["job_ids"]["completed"])
        handle = results.bind(complete)
        assert results.bind(complete) == handle
        publication = results(context.principal.actor.actor_id, handle)
        assert publication.job_id == complete
        assert publication.pdf.startswith(b"%PDF")
        assert len(publication.artifact.contexts) == 2
        with pytest.raises(ValueError):
            results(str(uuid4()), handle)
        restored = launcher.PublishedResults(context, forms)
        assert restored(context.principal.actor.actor_id, handle).pdf == publication.pdf
        context.manifests.revoke(publication.run.revision.case_id)
        with pytest.raises(PublicationError):
            results(context.principal.actor.actor_id, handle)

    asyncio.run(scenario())

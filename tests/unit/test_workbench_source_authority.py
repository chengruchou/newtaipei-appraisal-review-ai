"""Isolated authority regressions; fixture material is not real-case acceptance."""

import asyncio
import json
from contextlib import closing
from types import SimpleNamespace
from uuid import uuid4

import pytest

from appraisal_review.adapters.local.integrated_service import (
    LocalDirectory,
    LocalMaterialCatalog,
    LocalWorkbenchJobService,
    LocalWorkbenchReadAccess,
)
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.document_transfer import (
    DocumentErrorCode,
    DocumentFault,
    DocumentOperation,
)
from appraisal_review.domain.service_contracts import ReviewSubmission, ServiceErrorCode
from tests.unit.test_human_task_service import caller


@pytest.fixture
def configured(tmp_path):
    store = SQLiteReviewStore(tmp_path / "state.sqlite")
    catalog = LocalMaterialCatalog(store)
    principal = caller()
    snapshot = RevisionSnapshot.capture(synthetic_material(), "prepared-r1")
    catalog.register(principal, snapshot)
    directory = LocalDirectory({"session-" + "x" * 32: principal})
    state = SimpleNamespace(denied=False, unavailable=False, read=[], checked=[])

    class DocumentPorts:
        def __init__(self):
            self.authorization = self

        def require(self, actor, case_id, purpose, operation):
            state.checked.append((actor, case_id, purpose, operation))
            if state.denied:
                raise DocumentFault(DocumentErrorCode.UNAUTHORIZED)

        def read(self, actor, reference):
            self.require(actor, reference.case_id, reference.purpose, DocumentOperation.READ)
            state.read.append(reference)
            if state.unavailable:
                raise DocumentFault(DocumentErrorCode.UNAVAILABLE)

    access = LocalWorkbenchReadAccess(directory, catalog, DocumentPorts())
    return SimpleNamespace(
        store=store,
        catalog=catalog,
        principal=principal,
        snapshot=snapshot,
        directory=directory,
        state=state,
        access=access,
    )


def test_prepared_material_is_readable_before_human_task_registration(configured):
    h = configured
    actual = asyncio.run(h.access.read(h.principal, h.snapshot.revision.reference))
    assert actual == h.snapshot
    assert h.state.read == list(h.snapshot.revision.documents)
    assert all(call[-1] == DocumentOperation.READ for call in h.state.checked)


@pytest.mark.parametrize("part", ["rules", "documents"])
def test_prepared_catalog_reconstructs_all_canonical_bindings(configured, part):
    h = configured
    with closing(h.store._connect()) as connection:
        raw = json.loads(
            connection.execute("SELECT revision FROM prepared_materials").fetchone()[0]
        )
        raw[part][0]["content_hash"] = "f" * 64
        connection.execute("UPDATE prepared_materials SET revision=?", (json.dumps(raw),))
        connection.commit()
    with pytest.raises(ServiceFault) as failure:
        asyncio.run(h.access.read(h.principal, h.snapshot.revision.reference))
    assert failure.value.problem.code == ServiceErrorCode.CONFLICT
    assert h.state.read == []


def test_purpose_revocation_is_rechecked_after_final_directory_await(configured):
    h = configured
    original = h.directory.read

    async def revoke(actor_id, case_id):
        current = await original(actor_id, case_id)
        h.state.denied = True
        return current

    h.directory.read = revoke
    with pytest.raises(ServiceFault) as failure:
        asyncio.run(h.access.require_current(h.principal, (h.snapshot.revision.reference,)))
    assert failure.value.problem.code == ServiceErrorCode.UNAUTHORIZED
    assert h.state.read == []


def test_acknowledgment_authority_does_not_depend_on_source_storage_liveness(configured):
    h = configured
    h.state.unavailable = True
    asyncio.run(h.access.require_current(h.principal, (h.snapshot.revision.reference,)))
    assert h.state.read == []
    assert len(h.state.checked) == len(h.snapshot.revision.documents)
    h.state.denied = True
    with pytest.raises(ServiceFault):
        asyncio.run(h.access.require_current(h.principal, (h.snapshot.revision.reference,)))


def test_result_projection_cannot_bypass_source_access(configured):
    h = configured

    async def scenario():
        job_id = uuid4()
        await h.store.create_job(
            h.principal,
            ReviewSubmission(
                revision=h.snapshot.revision.reference,
                documents=h.snapshot.revision.documents,
                idempotency_key="unit-job",
            ),
            job_id=job_id,
            run_id=uuid4(),
            now=1000,
        )
        service = LocalWorkbenchJobService(h.store, h.store.results)
        service.workbench_access = h.access
        h.state.denied = True
        with pytest.raises(ServiceFault) as failure:
            await service.result(h.principal, job_id)
        assert failure.value.problem.code == ServiceErrorCode.UNAUTHORIZED

    asyncio.run(scenario())

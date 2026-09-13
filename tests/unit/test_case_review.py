"""The start-a-review read: it describes admitted material, and grants nothing."""

from __future__ import annotations

import asyncio

import pytest

from appraisal_review.application.case_review import CaseReviewService
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.review_contracts import ComparisonContext
from appraisal_review.domain.service_contracts import (
    ActorReference,
    DocumentReference,
    MaterialRevision,
    Permission,
    RevisionReference,
    RuleReference,
    ServiceErrorCode,
)

CASE = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


def material(case_id: str = CASE) -> MaterialRevision:
    return MaterialRevision(
        reference=RevisionReference(
            case_id=case_id,
            revision_id="33333333-3333-4333-8333-333333333333",
            material_digest="a" * 64,
        ),
        rules=(
            RuleReference(
                rule_set_id="shulin-regional",
                version="2026.09",
                context=ComparisonContext(scope="regional", target_id="P001", comparable_id="P002"),
                content_hash="d" * 64,
            ),
        ),
        documents=(
            DocumentReference(
                case_id=case_id,
                document_id="44444444-4444-4444-8444-444444444444",
                version="55555555-5555-4555-8555-555555555555",
                content_hash="b" * 64,
                purpose="forms",
            ),
            DocumentReference(
                case_id=case_id,
                document_id="66666666-6666-4666-8666-666666666666",
                version="77777777-7777-4777-8777-777777777777",
                content_hash="c" * 64,
                purpose="criteria",
            ),
        ),
    )


class Jobs:
    def __init__(self, records: tuple[object, ...] = ()) -> None:
        self.records = records

    async def jobs_for_case(self, *, case_id: str, principal_id: str) -> tuple[object, ...]:
        return self.records


class Record:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id


class Materials:
    def __init__(self, by_case: dict[str, MaterialRevision]) -> None:
        self.by_case = by_case
        self.asked: list[str] = []

    def latest_for_case(self, case_id: str) -> MaterialRevision | None:
        self.asked.append(case_id)
        return self.by_case.get(case_id)


def human(*cases: str, permissions: frozenset[Permission] | None = None) -> Principal:
    return Principal(
        actor=ActorReference(actor_id="88888888-8888-5888-8888-888888888888", kind="human"),
        case_ids=frozenset(cases),
        permissions=permissions if permissions is not None else frozenset({Permission.REVIEW}),
    )


def test_ready_case_reports_the_exact_revision_and_documents() -> None:
    service = CaseReviewService(materials=Materials({CASE: material()}))
    basis = asyncio.run(service.basis(human(CASE), CASE))
    assert basis.state == "ready"
    assert basis.reason is None
    assert basis.revision is not None
    assert basis.revision.revision_id == "33333333-3333-4333-8333-333333333333"
    assert {d.purpose for d in basis.documents} == {"forms", "criteria"}


def test_case_without_admitted_material_refuses_in_words_not_silence() -> None:
    service = CaseReviewService(materials=Materials({}))
    basis = asyncio.run(service.basis(human(CASE), CASE))
    assert basis.state == "no_material"
    assert basis.revision is None
    assert basis.documents == ()
    assert basis.reason and "admitted" in basis.reason


def test_a_non_member_learns_nothing_not_even_that_material_exists() -> None:
    materials = Materials({CASE: material()})
    service = CaseReviewService(materials=materials)
    with pytest.raises(ServiceFault) as refusal:
        asyncio.run(service.basis(human(OTHER), CASE))
    assert refusal.value.problem.code == ServiceErrorCode.UNAUTHORIZED
    # The refusal happens before any lookup, so membership cannot be probed by timing.
    assert materials.asked == []


def test_review_permission_is_required_not_just_membership() -> None:
    service = CaseReviewService(materials=Materials({CASE: material()}))
    confirm_only = human(CASE, permissions=frozenset({Permission.CONFIRM}))
    with pytest.raises(ServiceFault):
        asyncio.run(service.basis(confirm_only, CASE))


def test_reviewable_lists_only_this_principals_ready_cases() -> None:
    service = CaseReviewService(materials=Materials({CASE: material(), OTHER: material(OTHER)}))
    listed = asyncio.run(service.reviewable(human(CASE)))
    assert [entry.case_id for entry in listed.cases] == [CASE]


def test_reviewable_omits_member_cases_that_have_no_material() -> None:
    service = CaseReviewService(materials=Materials({CASE: material()}))
    listed = asyncio.run(service.reviewable(human(CASE, OTHER)))
    assert [entry.case_id for entry in listed.cases] == [CASE]
    assert all(entry.state == "ready" for entry in listed.cases)


def test_an_existing_review_is_named_so_a_second_is_never_opened() -> None:
    service = CaseReviewService(
        materials=Materials({CASE: material()}),
        jobs=Jobs((Record("99999999-9999-4999-8999-999999999999"),)),
    )
    basis = asyncio.run(service.basis(human(CASE), CASE))
    assert basis.state == "ready"
    assert str(basis.existing_job_id) == "99999999-9999-4999-8999-999999999999"


def test_a_case_with_no_review_yet_reports_no_existing_job() -> None:
    service = CaseReviewService(materials=Materials({CASE: material()}), jobs=Jobs(()))
    basis = asyncio.run(service.basis(human(CASE), CASE))
    assert basis.existing_job_id is None


def test_without_a_job_reader_the_basis_still_describes_the_material() -> None:
    service = CaseReviewService(materials=Materials({CASE: material()}))
    basis = asyncio.run(service.basis(human(CASE), CASE))
    assert basis.state == "ready" and basis.existing_job_id is None


def test_reviews_are_case_records_a_member_may_open_not_private_drafts() -> None:
    """The reader returns the case's reviews whoever submitted them; membership is
    the gate (checked by the service before the lookup), so the fixture-seeded
    demo review is reachable by the member who did not submit it."""
    service = CaseReviewService(
        materials=Materials({CASE: material()}),
        jobs=Jobs((Record("11111111-2222-4333-8444-555555555555"),)),
    )
    basis = asyncio.run(service.basis(human(CASE), CASE))
    assert str(basis.existing_job_id) == "11111111-2222-4333-8444-555555555555"

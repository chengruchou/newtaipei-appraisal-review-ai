"""Run the shared JobStore conformance suite against the in-memory adapter.

The same suite runs against DynamoDB in cloud_tests once that adapter exists, which is
how #29's "local contract tests and opt-in cloud tests use one state machine" is kept
true rather than asserted.
"""

from __future__ import annotations

import asyncio

import pytest

from appraisal_review.adapters.local.job_store import InMemoryJobStore
from appraisal_review.testing.job_store_contract import CONTRACT_CHECKS, JobStoreContract


@pytest.mark.parametrize("check", CONTRACT_CHECKS)
def test_in_memory_store_satisfies_the_job_store_contract(check: str) -> None:
    contract = JobStoreContract()
    asyncio.run(getattr(contract, check)(InMemoryJobStore()))


def test_the_suite_covers_every_declared_check() -> None:
    # A check added to the kit must reach both adapters, so the count is asserted here
    # rather than left to collection order.
    assert len(CONTRACT_CHECKS) == len(set(CONTRACT_CHECKS)) >= 20

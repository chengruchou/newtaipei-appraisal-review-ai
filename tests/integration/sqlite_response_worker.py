"""Subprocess helper exercising synthetic response commit and crash boundaries."""

import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from appraisal_review.adapters.local.review_database import ReviewTransaction, SQLiteReviewDatabase
from appraisal_review.adapters.local.sqlite_human_task_store import SQLiteHumanTaskStore
from appraisal_review.adapters.local.sqlite_job_store import SQLiteJobStore
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import HumanResponse
from appraisal_review.testing.job_store_contract import NOW
from tests.unit.test_human_task_service import caller


async def main() -> None:
    request = json.load(sys.stdin)
    store = SQLiteHumanTaskStore(SQLiteJobStore(SQLiteReviewDatabase(Path(request["database"]))))
    if request["mode"] == "crash_during_commit":
        original = ReviewTransaction.put

        def interrupted(self, kind, key, value, subkey="", *, insert=False):
            original(self, kind, key, value, subkey, insert=insert)
            if kind == "response":
                os._exit(91)

        ReviewTransaction.put = interrupted
    try:
        result = await HumanTaskService(store, clock=lambda: NOW + 2).respond(
            caller(), UUID(request["task_id"]), HumanResponse.model_validate(request["command"])
        )
        if request["mode"] == "crash_after_commit":
            os._exit(92)
        print(json.dumps({"receipt": result.model_dump(mode="json")}))
    except ServiceFault as error:
        print(json.dumps({"error": error.problem.code}))


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
from uuid import uuid4

import httpx
import pytest

from cloud_tests import runtime


@pytest.mark.parametrize("scenario", ["verified", "completed", "needs_review", "error"])
def test_background_health_duplicate_dispatch_and_terminal_result(monkeypatch, scenario) -> None:
    async def exercise() -> None:
        release = asyncio.Event()
        original = runtime.invoke
        calls = 0

        async def slow_invoke(*args, **kwargs):
            nonlocal calls
            calls += 1
            await release.wait()
            if scenario == "error":
                raise RuntimeError("private details must not escape")
            return await original(*args, **kwargs)

        monkeypatch.setattr(runtime, "invoke", slow_invoke)
        app = runtime.create_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):
            payload = {
                "action": "start",
                "run_id": str(uuid4()),
                "scenario": "verified" if scenario == "error" else scenario,
            }
            assert (await client.get("/ping")).json() == {"status": "Healthy"}
            start = await client.post("/invocations", json=payload)
            assert start.status_code == 200
            assert start.json()["execution_status"] == "running"
            assert start.json()["durable"] is False
            assert (await client.get("/ping")).json()["status"] == "HealthyBusy"
            duplicate = await client.post("/invocations", json=payload)
            assert duplicate.json()["execution_status"] == "running"
            changed = payload | {
                "scenario": "completed" if payload["scenario"] != "completed" else "verified"
            }
            assert (await client.post("/invocations", json=changed)).status_code == 409
            release.set()
            for _ in range(10):
                await asyncio.sleep(0)
                status = (
                    await client.post("/invocations", json=payload | {"action": "status"})
                ).json()
                if status["execution_status"] != "running":
                    break
            assert calls == 1
            assert (await client.get("/ping")).json()["status"] == "Healthy"
            if scenario == "error":
                assert status["execution_status"] == "failed"
                assert "private details" not in str(status)
            else:
                assert status["execution_status"] == "succeeded"
                assert status["result"]["status"] == scenario
            assert (await client.post("/invocations", json={})).status_code == 422
            unknown = payload | {"run_id": str(uuid4()), "action": "status"}
            assert (await client.post("/invocations", json=unknown)).status_code == 404

    asyncio.run(exercise())


def test_lifespan_cancellation_clears_busy_health(monkeypatch) -> None:
    async def exercise() -> None:
        started = asyncio.Event()

        async def blocked_invoke(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(runtime, "invoke", blocked_invoke)
        app = runtime.create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = {"action": "start", "run_id": str(uuid4())}
            async with app.router.lifespan_context(app):
                await client.post("/invocations", json=payload)
                await started.wait()
                assert (await client.get("/ping")).json()["status"] == "HealthyBusy"
            assert (await client.get("/ping")).json()["status"] == "Healthy"
            final = (await client.post("/invocations", json=payload | {"action": "status"})).json()
            assert final["error_code"] == "smoke_cancelled"

    asyncio.run(exercise())

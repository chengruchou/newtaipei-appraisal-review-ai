"""Start a loopback-only synthetic API, check actual HTTP, and stop the child."""

import os
import socket
import subprocess
import sys
import time

import httpx

from appraisal_review.adapters.local.synthetic import synthetic_request


def main() -> None:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = dict(os.environ, RUNTIME_MODE="local", SYNTHETIC_DEMO="true")
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "appraisal_review.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "error",
        ],
        env=env,
    )
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=5.0, trust_env=False
        ) as client:
            for _ in range(100):
                if child.poll() is not None:
                    raise RuntimeError("API child exited during startup")
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("API startup timed out")
            for scenario in ("verified", "completed", "needs_review"):
                response = client.post(
                    "/v1/reviews", json=synthetic_request(scenario).model_dump(mode="json")
                )
                response.raise_for_status()
                data = response.json()
                assert data["status"] == scenario
                if scenario == "completed":
                    assert "no file was created" in data["pdf_result"]["warnings"][0]
                else:
                    assert data["output_pdf_uri"] is None
                print(f"HTTP smoke: {scenario} -> 200 / {data['status']}")
            assert client.post("/v1/reviews", json={}).status_code == 422
            print("HTTP smoke: malformed input -> 422; no AWS or real PDFs used.")
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


if __name__ == "__main__":
    main()

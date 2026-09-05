"""Live entry smoke: explicit profile/role/Region and exact tagged stack required."""

import argparse
import json
import re
import time
from pathlib import Path
from uuid import uuid4

PROJECT_TAG = "appraisal-review"


def verify_identity(identity: dict, account: str, role: str) -> None:
    arn = identity.get("Arn", "")
    expected = f":sts::{account}:assumed-role/{role}/"
    if identity.get("Account") != account or expected not in arn:
        raise ValueError("Caller does not match the explicitly designated test account/role")


def resolve_runtime(session, smoke_id: str, region: str, account: str) -> str:
    stack = session.client("cloudformation").describe_stacks(
        StackName=f"appraisal-review-smoke-runtime-{smoke_id}"
    )["Stacks"][0]
    tags = {tag["Key"]: tag["Value"] for tag in stack.get("Tags", [])}
    if tags.get("Project") != PROJECT_TAG or tags.get("SmokeId") != smoke_id:
        raise ValueError("Stack is not tagged for this project smoke")
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}
    arn = outputs["RuntimeArn"]
    if (
        f":bedrock-agentcore:{region}:{account}:runtime/appraisal_review_smoke_{smoke_id}-"
        not in arn
    ):
        raise ValueError("Runtime does not belong to the designated smoke environment")
    return arn


def exercise(
    client, runtime_arn: str, *, poll_limit: int = 30, poll_delay: float = 1.0
) -> list[dict]:
    evidence = []
    for scenario in ("verified", "completed", "needs_review"):
        session_id, run_id = str(uuid4()), str(uuid4())

        def invoke(
            action: str,
            *,
            session_id: str = session_id,
            run_id: str = run_id,
            scenario: str = scenario,
        ) -> dict:
            response = client.invoke_agent_runtime(
                agentRuntimeArn=runtime_arn,
                runtimeSessionId=session_id,
                contentType="application/json",
                accept="application/json",
                payload=json.dumps(
                    {"action": action, "run_id": run_id, "scenario": scenario}
                ).encode(),
            )
            stream = response["response"]
            try:
                return json.loads(stream.read())
            finally:
                stream.close()

        accepted = invoke("start")
        if accepted["execution_status"] not in {"running", "succeeded"}:
            raise RuntimeError("Runtime did not accept the synthetic run")
        # A duplicate start must not create an additional execution.
        current = invoke("start")
        for _ in range(poll_limit):
            if current["execution_status"] in {"succeeded", "failed"}:
                break
            time.sleep(poll_delay)
            current = invoke("status")
        if current["execution_status"] not in {"succeeded", "failed"}:
            raise TimeoutError("Runtime did not reach a terminal state within smoke limits")
        if current["execution_status"] != "succeeded" or current["result"]["status"] != (
            "verified" if scenario == "completed" else scenario
        ):
            raise RuntimeError("Unexpected synthetic terminal result")
        if current["result"]["output_pdf_uri"] is not None:
            raise RuntimeError("Synthetic runs cannot publish output PDFs")
        if scenario == "completed":
            if (
                current["result"]["artifact_status"] != "simulated"
                or current["result"]["pdf_result"]["artifact_created"]
            ):
                raise RuntimeError("Fake writer was reported as a real artifact")
            if "no file was created" not in current["result"]["pdf_result"]["warnings"][0]:
                raise RuntimeError("Synthetic warning metadata was lost")
        elif current["result"]["output_pdf_uri"] is not None:
            raise RuntimeError("Unexpected output on a review-only or unresolved result")
        evidence.append(
            {
                "scenario": scenario,
                "session_id": session_id,
                "run_id": run_id,
                "acceptance": accepted,
                "terminal": current,
            }
        )
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("profile", "region", "expected-account", "expected-role", "smoke-id"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts/runtime-smoke.json"))
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9]{8,20}", args.smoke_id):
        parser.error("smoke-id must contain 8-20 lowercase letters/digits")
    if not re.fullmatch(r"[0-9]{12}", args.expected_account):
        parser.error("expected-account must be the designated 12-digit account")
    if args.output.exists():
        parser.error("output already exists; choose a new evidence path")
    # AWS-specific imports and client construction occur only after explicit arguments.
    import boto3
    from botocore.config import Config

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    verify_identity(
        session.client("sts").get_caller_identity(), args.expected_account, args.expected_role
    )
    runtime_arn = resolve_runtime(session, args.smoke_id, args.region, args.expected_account)
    client = session.client(
        "bedrock-agentcore",
        config=Config(
            connect_timeout=10,
            read_timeout=60,
            retries={"mode": "standard", "total_max_attempts": 3},
        ),
    )
    evidence = exercise(client, runtime_arn)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(
            {
                "kind": "synthetic-runtime-entry-only",
                "live": True,
                "smoke_id": args.smoke_id,
                "runtime_arn": runtime_arn,
                "runs": evidence,
            },
            output,
            indent=2,
        )
    print("Live synthetic entry smoke passed. No model extraction or PDF creation was tested.")
    print(f"Evidence saved to {args.output}. Clean up only the recorded smoke resources.")


if __name__ == "__main__":
    main()

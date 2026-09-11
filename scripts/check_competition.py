#!/usr/bin/env python3
"""Check an exact competition profile and templates without contacting AWS."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from appraisal_review.application.competition_preflight import (
    CompetitionFinding,
    check_profile,
    check_template,
)
from appraisal_review.domain.competition_profile import (
    PDF_SHA256,
    WORKBOOK_SHA256,
    CompetitionProfile,
)


class CfnLoader(yaml.SafeLoader):
    pass


def intrinsic(loader: CfnLoader, tag: str, node: yaml.Node) -> dict[str, Any]:
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    elif isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node)
    else:
        raise ValueError("Unsupported template node")
    if tag == "GetAtt" and isinstance(value, str):
        value = value.split(".", 1)
    return {tag if tag == "Ref" else f"Fn::{tag}": value}


CfnLoader.add_multi_constructor("!", intrinsic)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--template", type=Path, action="append", required=True)
    parser.add_argument("--source-pdf", type=Path)
    parser.add_argument("--source-workbook", type=Path)
    parser.add_argument(
        "--parameters-file", type=Path, help="Exact private CloudFormation parameters"
    )
    parser.add_argument(
        "--identity-file", type=Path, help="Trusted, private offline identity snapshot"
    )
    args = parser.parse_args()
    issues: list[CompetitionFinding] = []
    digests: dict[str, str] = {}
    profile_digest: str | None = None
    try:
        profile = CompetitionProfile.model_validate_json(args.profile.read_bytes())
        profile_digest = profile.digest
        parameters: dict[str, Any] = {}
        if args.parameters_file:
            parameter_bytes = args.parameters_file.read_bytes()
            digests["parameters"] = hashlib.sha256(parameter_bytes).hexdigest()
            entries = json.loads(parameter_bytes)
            if not isinstance(entries, list):
                raise ValueError("Parameters require explicit CloudFormation entries")
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {"ParameterKey", "ParameterValue"}:
                    raise ValueError("Unresolved parameter")
                key, value = entry["ParameterKey"], entry["ParameterValue"]
                if not isinstance(key, str) or not isinstance(value, str) or key in parameters:
                    raise ValueError("Invalid parameter value")
                parameters[key] = value
        model_arns = {d.arn for model in profile.models for d in model.destinations}
        for model in profile.models:
            if model.kind != "foundation":
                kind = (
                    "inference-profile"
                    if model.kind == "system_profile"
                    else ("application-inference-profile")
                )
                model_arns.add(
                    model.model_id
                    if model.model_id.startswith("arn:")
                    else (
                        f"arn:aws:bedrock:{profile.primary_region}:{profile.account_id}:{kind}/{model.model_id}"
                    )
                )
        identity = json.loads(args.identity_file.read_bytes()) if args.identity_file else {}
        if not isinstance(identity, dict) or set(identity) - {"account_id", "role_arn"}:
            raise ValueError("Unsupported identity snapshot")
        issues.extend(
            check_profile(
                profile,
                trusted_profile_digest=os.environ.get("REVIEW_COMPETITION_PROFILE_SHA256"),
                trusted_data_policy_digest=os.environ.get("REVIEW_COMPETITION_DATA_POLICY_SHA256"),
                observed_account_id=identity.get("account_id"),
                observed_role_arn=identity.get("role_arn"),
            )
        )
        for name, path, expected in (
            ("source_pdf", args.source_pdf, PDF_SHA256),
            ("source_workbook", args.source_workbook, WORKBOOK_SHA256),
        ):
            if path is None:
                issues.append(CompetitionFinding("source_file_missing", name, "source integrity"))
            else:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                digests[name] = digest
                if digest != expected:
                    issues.append(
                        CompetitionFinding("source_file_changed", name, "source integrity")
                    )
        for index, path in enumerate(args.template):
            raw = path.read_bytes()
            digests[f"template_{index}"] = hashlib.sha256(raw).hexdigest()
            if len(raw) > 1_048_576:
                raise ValueError("Template size limit")
            template = yaml.load(raw, Loader=CfnLoader)
            if not isinstance(template, dict):
                raise ValueError("Template root must be a mapping")
            issues.extend(
                CompetitionFinding(f.code, f"template_{index}.{f.location}", f.source)
                for f in check_template(
                    template,
                    primary_region=profile.primary_region,
                    parameters=parameters,
                    approved_model_arns=frozenset(model_arns),
                )
            )
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError, ValidationError):
        # No paths, source text, credentials or Pydantic input values in reports.
        issues.append(CompetitionFinding("invalid_input", "input", "configuration schema"))
    print(
        json.dumps(
            {
                "schema_version": "competition-check-v1",
                "status": "blocked" if issues else "offline_checks_passed",
                "live_acceptance": "not_performed",
                "profile_digest": profile_digest,
                "input_sha256": digests,
                "findings": [asdict(f) for f in issues],
            },
            indent=2,
        )
    )
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())

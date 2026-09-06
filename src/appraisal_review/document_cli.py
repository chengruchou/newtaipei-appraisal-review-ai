"""Opt-in document preparation, bounded model extraction and local human review."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import Field

from appraisal_review.adapters.aws.document_extraction import (
    BedrockDocumentExtractor,
    ExtractionConfig,
    ExtractionError,
)
from appraisal_review.adapters.local.approval import LocalApprovalStore, current_reviewer
from appraisal_review.adapters.local.native_candidates import native_candidates
from appraisal_review.adapters.local.pdf_parser import DocumentInput, LocalPDFParser
from appraisal_review.adapters.local.reviewer_platform import (
    UnsupportedReviewerPlatform,
    require_reviewer_platform,
)
from appraisal_review.application.bootstrap import build_controller
from appraisal_review.application.document_review import assemble, document_adapters
from appraisal_review.config import Settings
from appraisal_review.domain.confidence import confirm_side
from appraisal_review.domain.document_models import (
    Digest,
    DocumentModel,
    SourceDocument,
    SourceRegistry,
)
from appraisal_review.domain.extraction_models import PageExtraction
from appraisal_review.domain.factor_models import AgentReviewRequest, ReviewMaterial
from appraisal_review.domain.golden import GoldenSet, check_fields
from appraisal_review.domain.review_contracts import CaseIdentity, content_digest


class InputSpec(DocumentModel):
    path: Path
    document_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    version: str
    role: str
    expected_hash: Digest
    document_date: str | None = None


class InputManifest(DocumentModel):
    identity: CaseIdentity
    documents: list[InputSpec] = Field(min_length=2)

    def parser(self) -> LocalPDFParser:
        specs = []
        for spec in self.documents:
            if spec.role not in {"criteria", "forms", "reference", "brief"}:
                raise ValueError("Unsupported source role")
            specs.append(DocumentInput(**spec.model_dump()))
        return LocalPDFParser(specs)


def private_json(path: Path, value: DocumentModel | dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = value.model_dump(mode="json") if isinstance(value, DocumentModel) else value
    # Outputs are explicit local artifacts, never published automatically.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)


def reviewable(material: ReviewMaterial, path: Path) -> None:
    lines = [
        "# Candidate review material",
        "",
        f"Digest: `{content_digest(material)}`",
        "",
        "Approval is pending. Confirm source versions, complete inventory, applicability,",
        "facts, observed values, intervals and every matrix cell before approving this digest.",
        "",
    ]
    for scoped in material.policy.rule_sets:
        lines += [f"## {scoped.context.key()}", "", f"Version: {scoped.rules.version}", ""]
        for rule in scoped.rules.rules:
            lines += [f"### {rule.id}", "", "```json", rule.model_dump_json(indent=2), "```", ""]
        for ref in scoped.evidence:
            lines += [
                f"Source: {ref.document_id}, version {ref.version}, page {ref.page}, "
                f"region {ref.region_id}, box {ref.bbox}",
                "",
                ref.excerpt,
                "",
            ]
    lines += ["## Complete material", "", "```json", material.model_dump_json(indent=2), "```", ""]
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write("\n".join(lines))


def bedrock_client(args: argparse.Namespace) -> Any:
    """No implicit default profile and no credential discovery before explicit opt-in."""
    import boto3
    from botocore.config import Config

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    caller = session.client("sts").get_caller_identity()
    expected_prefix = f"arn:aws:sts::{args.expected_account}:assumed-role/{args.expected_role}/"
    if caller["Account"] != args.expected_account or not caller["Arn"].startswith(expected_prefix):
        raise PermissionError("AWS account/role does not match explicit project identity")
    control = session.client("bedrock")
    model_id = args.model_id
    if (
        model_id.startswith(("us.", "eu.", "apac.", "au.", "jp.", "global."))
        or ":inference-profile/" in model_id
    ):
        if not args.allow_cross_region:
            raise PermissionError("Inference profiles require explicit cross-region opt-in")
        profile = control.get_inference_profile(inferenceProfileIdentifier=model_id)
        model_id = profile["models"][0]["modelArn"].split("/", 1)[1]
    model = control.get_foundation_model(modelIdentifier=model_id)["modelDetails"]
    if "IMAGE" not in model["inputModalities"] or "TEXT" not in model["outputModalities"]:
        raise ValueError("Selected model does not support image input and text output")
    return session.client(
        "bedrock-runtime",
        config=Config(
            connect_timeout=10, read_timeout=args.timeout, retries={"total_max_attempts": 1}
        ),
    )


async def preparation(args: argparse.Namespace) -> None:
    manifest = InputManifest.model_validate_json(args.manifest.read_text())
    parser = manifest.parser()
    documents = []
    for spec in manifest.documents:
        document = await parser.parse_document(spec.path.resolve().as_uri())
        assert document.source is not None
        documents.append(document.source)
    registry = SourceRegistry(documents=documents)
    if args.command == "check-golden":
        golden = GoldenSet.model_validate_json(args.golden.read_text())
        private_json(args.output / "golden-metrics.json", check_fields(registry, golden))
        return
    if args.command == "parse":
        private_json(args.output / "registry.json", registry)
        return
    if args.command == "native-candidates":
        candidates = [
            c
            for source in registry.documents
            if source.role == "criteria"
            for c in native_candidates(source)
        ]
        private_json(
            args.output / "native-candidates.json",
            {
                "candidates": [c.model_dump(mode="json") for c in candidates],
                "detected_matrices": len(candidates),
                "supported_candidates": sum(c.candidate is not None for c in candidates),
                "approval": "pending",
                "semantic_accuracy": "not established",
            },
        )
        return
    if args.command == "assemble":
        extracted = [
            PageExtraction.model_validate_json(path.read_text())
            for path in sorted(args.extractions.glob("*-page-*.json"))
        ]
        material = assemble(manifest.identity, registry, extracted)
        private_json(args.output / "material.json", material.model_dump(mode="json"))
        reviewable(material, args.output / "review.md")
        return
    selections: list[tuple[SourceDocument, int]] = []
    for selection in args.pages:
        document_id, page_numbers = selection.split(":", 1)
        source = next(d for d in documents if d.document_id == document_id)
        selections.extend((source, int(number)) for number in page_numbers.split(","))
    if not 0 < len(selections) <= args.page_limit <= 30:
        raise ValueError("Page selection exceeds explicit extraction budget")
    if len({(d.document_id, p) for d, p in selections}) != len(selections):
        raise ValueError("Duplicate extraction page selection")
    client = bedrock_client(args)
    config = ExtractionConfig(
        model_id=args.model_id,
        region=args.region,
        max_output_tokens=args.max_output_tokens,
        attempts=args.attempts,
        timeout_seconds=args.timeout,
    )
    extractor = BedrockDocumentExtractor(client, config)
    metrics = []
    context = manifest.identity.model_dump_json()
    for source, page in selections:
        try:
            result = await extractor.extract_page(
                source, page, await parser.render(source.uri, page), context=context
            )
            private_json(args.output / f"{source.document_id}-page-{page}.json", result)
            metrics.append(result.model_dump(mode="json", exclude={"proposal"}))
            context = (
                manifest.identity.model_dump_json()
                + "\nKnown explicit entity contexts: "
                + json.dumps([c.context.model_dump() for c in result.proposal.contexts])
            )
        except ExtractionError as error:
            metrics.append({"document_id": source.document_id, "page": page, "error": error.code})
            private_json(
                args.output / "metrics.json",
                {"configuration": config.model_dump(), "pages": metrics},
            )
            raise
    private_json(
        args.output / "metrics.json", {"configuration": config.model_dump(), "pages": metrics}
    )


def _main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="command", required=True)
    for command in ("parse", "extract", "assemble", "native-candidates", "check-golden"):
        parser = sub.add_parser(command)
        parser.add_argument("--manifest", type=Path, required=True)
        parser.add_argument("--output", type=Path, required=True)
        if command == "check-golden":
            parser.add_argument("--golden", type=Path, required=True)
        if command == "assemble":
            parser.add_argument("--extractions", type=Path, required=True)
        if command == "extract":
            for name in ("profile", "region", "expected-account", "expected-role", "model-id"):
                parser.add_argument(f"--{name}", required=True)
            parser.add_argument("--pages", nargs="+", required=True)
            parser.add_argument("--page-limit", type=int, default=4)
            parser.add_argument("--max-output-tokens", type=int, default=12000)
            parser.add_argument("--attempts", type=int, default=2)
            parser.add_argument("--timeout", type=float, default=180)
            parser.add_argument("--allow-cross-region", action="store_true")
    init = sub.add_parser("init-store")
    init.add_argument("--store", type=Path, required=True)
    for command in ("inspect", "confirm-facts", "approve", "review"):
        parser = sub.add_parser(command)
        parser.add_argument("--material", type=Path, required=True)
        if command in {"inspect", "confirm-facts", "review"}:
            parser.add_argument("--output", type=Path, required=True)
        if command in {"approve", "review"}:
            parser.add_argument("--store", type=Path, required=True)
        if command in {"confirm-facts", "approve"}:
            parser.add_argument("--expected-digest", required=True)
    args = cli.parse_args()
    if args.command in {"init-store", "confirm-facts", "approve", "review"}:
        require_reviewer_platform()
    if args.command in {"parse", "extract", "assemble", "native-candidates", "check-golden"}:
        asyncio.run(preparation(args))
        return
    if args.command == "init-store":
        LocalApprovalStore.initialize(args.store, current_reviewer())
        return
    material = ReviewMaterial.model_validate_json(args.material.read_text())
    if args.command == "inspect":
        reviewable(material, args.output)
    elif args.command == "confirm-facts":
        if content_digest(material) != args.expected_digest:
            raise ValueError("Inspected material changed")
        reviewer = current_reviewer()
        for pair in material.facts.pairs:
            confirm_side(pair, "target", reviewer=f"{reviewer.uid}:{reviewer.name}")
            confirm_side(pair, "comparable", reviewer=f"{reviewer.uid}:{reviewer.name}")
        private_json(args.output, material.model_dump(mode="json"))
    elif args.command == "approve":
        LocalApprovalStore(args.store).approve(material, expected_digest=args.expected_digest)
    else:
        inputs = [
            DocumentInput(
                _file_path(d.uri), d.document_id, d.version, d.role, d.content_hash, d.document_date
            )
            for d in material.policy.registry.documents
        ]
        controller = build_controller(
            Settings.model_validate({"runtime_mode": "local"}),
            adapters=document_adapters(
                LocalPDFParser(inputs), material, LocalApprovalStore(args.store)
            ),
        )
        criteria = next(d for d in material.policy.registry.documents if d.role == "criteria")
        forms = next(d for d in material.policy.registry.documents if d.role == "forms")
        run = asyncio.run(
            controller.review(
                AgentReviewRequest(
                    case_id=material.policy.identity.case_id,
                    criteria_document_uri=criteria.uri,
                    case_document_uri=forms.uri,
                )
            )
        )
        private_json(args.output, run.model_dump(mode="json"))


def _file_path(uri: str) -> Path:
    import re
    from urllib.parse import unquote, urlsplit

    from appraisal_review.domain.pdf_types import document_identity

    parts = urlsplit(uri)
    path = unquote(parts.path)
    if (
        parts.scheme != "file"
        or parts.netloc not in {"", "localhost"}
        or re.match(r"^/[A-Za-z]:", path)
        or path.startswith("//")
        or "\\" in path
    ):
        raise ValueError("unsupported_local_file_uri: POSIX absolute file URI required")
    return Path(document_identity(uri)[2])


def main() -> None:
    try:
        _main()
    except UnsupportedReviewerPlatform as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()

"""Explicitly opted-in two-page synthetic Bedrock probe, never a case approval."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from appraisal_review.adapters.aws.document_extraction import ExtractionConfig
from appraisal_review.adapters.aws.extraction_preflight import (
    BedrockAccessPolicy,
)
from appraisal_review.adapters.aws.probe_budget import PricedClients, ProbePricing
from appraisal_review.adapters.aws.snapshot_extraction import BedrockSnapshotBackend
from appraisal_review.adapters.document_extraction import DocumentSnapshotResolver
from appraisal_review.adapters.local.document_authority import (
    ConfiguredDocumentAuthorization,
    DocumentGrant,
    Ed25519ExportVerifier,
    TrustedExportKey,
)
from appraisal_review.adapters.local.document_storage import SQLiteDocumentStorage
from appraisal_review.adapters.local.snapshot_renderer import SnapshotPDFRenderer
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.extraction import AuthorizedExtractionService
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.document_transfer import (
    DocumentOperation,
    ExportClaims,
    PrivacyAttestation,
    canonical_bytes,
    digest_bytes,
)
from appraisal_review.domain.extraction_contracts import (
    ContractModel,
    ExecutionBudget,
    ExtractionContext,
    PageRequest,
    SanitizedSourceReference,
)
from appraisal_review.domain.privacy_models import PrivacyManifest, PrivacyPage
from appraisal_review.domain.review_contracts import ComparisonContext
from appraisal_review.domain.service_contracts import (
    ActorReference,
    MaterialRevision,
    Permission,
    RevisionReference,
    RuleReference,
    RunReference,
)
from appraisal_review.ports.document_extraction import ExtractionBoundaryError


def synthetic_source(
    directory: Path,
) -> tuple[DocumentTransferService, Principal, tuple[PageRequest, ...]]:
    """Only fixed generated content; isolated ephemeral reviewer/key for synthetic testing."""
    import pymupdf

    with pymupdf.open() as pdf:  # type: ignore[no-untyped-call]
        for label in ("合成測試 寬度 10 公尺", "合成測試 修正率 5 百分點"):
            page = pdf.new_page(width=300, height=200)
            page.insert_text((30, 60), label, fontname="china-t", fontsize=14)
        content = pdf.tobytes(no_new_id=True)
    actor, case, key_id = uuid4(), uuid4(), uuid4()
    key = Ed25519PrivateKey.generate()
    principal = Principal(
        ActorReference(actor_id=str(actor), kind="human"),
        frozenset({str(case)}),
        frozenset({Permission.REVIEW}),
    )
    grants = ConfiguredDocumentAuthorization(
        (
            DocumentGrant(
                actor, case, frozenset({"forms", "criteria"}), frozenset(DocumentOperation)
            ),
        )
    )
    verifier = Ed25519ExportVerifier(
        (TrustedExportKey(key_id, key.public_key(), actor, frozenset({case})),)
    )
    store = SQLiteDocumentStorage(directory / "synthetic.sqlite")
    documents = DocumentTransferService(store, grants, verifier, store)
    metadata = []
    for purpose in ("criteria", "forms"):
        now = datetime.now(UTC)
        manifest = PrivacyManifest(
            case_id=case,
            document_id=uuid4(),
            sanitized_digest=digest_bytes(content),
            byte_size=len(content),
            pages=tuple(PrivacyPage(number=i, width=300, height=200) for i in (1, 2)),
            occurrences=(),
        )
        claims = ExportClaims(
            key_id=key_id,
            export_id=uuid4(),
            principal_id=actor,
            manifest=manifest,
            purpose=purpose,
            confirmed_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        metadata.append(
            documents.ingest(
                principal,
                content,
                PrivacyAttestation(
                    claims=claims, signature_hex=key.sign(canonical_bytes(claims)).hex()
                ),
            )
        )
    revision = MaterialRevision(
        reference=RevisionReference(
            case_id=str(case),
            revision_id=str(uuid4()),
            material_digest=digest_bytes(b"synthetic extraction material v1"),
        ),
        documents=tuple(m.reference for m in metadata),
        rules=(
            RuleReference(
                rule_set_id="synthetic-candidate",
                version="1",
                content_hash=digest_bytes(b"candidate-only"),
                context=ComparisonContext(
                    scope="regional",
                    target_id="synthetic-target",
                    comparable_id="synthetic-comparable",
                ),
            ),
        ),
    )
    run = RunReference(run_id=uuid4(), revision=revision.reference)
    documents.create_snapshot(principal, run, revision)
    forms = metadata[1]
    source = SanitizedSourceReference(
        document=forms.reference,
        privacy_contract_version="privacy-v1",
        privacy_manifest_digest=digest_bytes(canonical_bytes(forms.attestation.claims.manifest)),
        page_count=2,
    )
    requests = tuple(
        PageRequest(
            run=run,
            source=source,
            page=p,
            context=ExtractionContext(language="zh-Hant", task="propose_case"),
        )
        for p in (1, 2)
    )
    return documents, principal, requests


class LiveConfiguration(ContractModel):
    policy: BedrockAccessPolicy
    extraction: ExtractionConfig
    budget: ExecutionBudget
    pricing: ProbePricing


async def probe(profile: str, config: LiveConfiguration, directory: Path) -> dict[str, object]:
    documents, principal, requests = synthetic_source(directory)
    clients = PricedClients(profile=profile, pricing=config.pricing)
    backend = BedrockSnapshotBackend(
        clients=clients,
        policy=config.policy,
        config=config.extraction,
        budget=config.budget,
        renderer=SnapshotPDFRenderer(),
    )
    clients.can_invoke = lambda: not backend.ledger.stopped and backend.ledger.remaining > 0
    service = AuthorizedExtractionService(
        resolver=DocumentSnapshotResolver(documents), backend=backend
    )
    outcomes = []
    failures = []
    for request in requests:
        try:
            outcomes.append(await service.extract(principal, request))
        except Exception as error:
            failures.append(
                {
                    "page": request.page,
                    "status": "failed",
                    "failure": error.code
                    if isinstance(error, ExtractionBoundaryError)
                    else "provider_error",
                    "attempts": None,
                }
            )
            break
    attempts = [a for o in outcomes for a in o.telemetry.attempts]
    known = sum(
        Decimal(tokens) * rate / Decimal(1_000_000)
        for a in attempts
        for tokens, rate in (
            (a.input_tokens, config.pricing.input_per_million_usd),
            (a.output_tokens, config.pricing.output_per_million_usd),
        )
        if tokens is not None
    )
    complete_usage = not failures and all(
        a.input_tokens is not None and a.output_tokens is not None for a in attempts
    )
    return {
        "dataset_kind": "synthetic",
        "execution_kind": "live",
        "production_accuracy": None,
        "all_candidates": len(outcomes) == 2 and all(o.status == "candidate" for o in outcomes),
        "pages": [
            {
                "page": o.request.page,
                "status": o.status,
                "failure": o.failure,
                "source_digest": o.request.source.document.content_hash,
                "proposal_digest": digest_bytes(o.proposal.model_dump_json().encode())
                if o.proposal
                else None,
                "configuration_digest": digest_bytes(
                    o.telemetry.configuration.model_dump_json().encode()
                ),
                "attempts": [a.model_dump(mode="json") for a in o.telemetry.attempts],
                "elapsed_seconds": o.telemetry.elapsed_seconds,
            }
            for o in outcomes
        ]
        + failures,
        "scheduled_pages": 2,
        "not_run_pages": 2 - len(outcomes) - len(failures),
        "known_token_cost_usd": str(known),
        "estimated_token_cost_usd": str(known) if complete_usage else None,
        "pricing_digest": digest_bytes(config.pricing.model_dump_json().encode()),
        "count_tokens_calls": sum(r.count_calls for r in clients.runtimes),
        "converse_calls": sum(r.converse_calls for r in clients.runtimes),
        "cost_scope": "Dated token-price estimate only; excludes non-token charges and taxes.",
        "approved_material": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--approved-budget-usd", type=Decimal)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"mode": "dry_run", "provider_calls": 0, "ready_for_live": False}))
        return 0
    try:
        if (
            not args.profile
            or not args.config
            or not args.approved_budget_usd
            or not args.approved_budget_usd.is_finite()
            or args.approved_budget_usd <= 0
        ):
            raise ValueError("Explicit environment and bounded budget required")
        config = LiveConfiguration.model_validate_json(args.config.read_bytes())
        if (
            config.policy.model_kind != "foundation"
            or config.policy.allow_cross_region
            or config.policy.allow_global
            or config.policy.model_id != config.pricing.model_id
            or config.extraction.model_id != config.pricing.model_id
            or config.policy.region != config.pricing.region
            or config.extraction.region != config.pricing.region
        ):
            raise ValueError("Exact single-region foundation price evidence required")
        ceiling = config.pricing.ceiling(
            config.budget.max_calls, config.budget.max_output_tokens, args.approved_budget_usd
        )
        if (
            config.budget.max_pages != 2
            or config.budget.max_calls > 4
            or config.budget.max_output_tokens > 8192
            or config.budget.max_elapsed_seconds > 180
        ):
            raise ValueError("Synthetic probe limits exceeded")
        with TemporaryDirectory(prefix="synthetic-extraction-") as directory:
            report = asyncio.run(probe(args.profile, config, Path(directory)))
        report["maximum_token_cost_estimate_usd"] = str(ceiling)
        print(json.dumps(report, sort_keys=True))
        return 0 if report["all_candidates"] is True else 1
    except Exception:
        print(json.dumps({"status": "failed", "code": "extraction_probe_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

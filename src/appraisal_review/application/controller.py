"""Tool-based workflow controller with explicit safe completion branches."""

from pydantic import ValidationError

from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.document_models import SourceRegistry
from appraisal_review.domain.factor_engine import FactorRuleEngine
from appraisal_review.domain.factor_models import (
    AgentReviewRequest,
    AgentReviewRun,
    AuditEvent,
    CaseFacts,
    CaseReviewResult,
    EvaluationStatus,
    FactorEvaluationRequest,
    FactorReviewResult,
    ReviewPolicy,
    VerificationReport,
    WorkflowStatus,
)
from appraisal_review.domain.pdf_models import (
    InvalidPDFResultError,
    PDFErrorCode,
    PDFProblem,
    PDFWriteError,
    PDFWriteRequest,
    PDFWriteResult,
    document_identity,
)
from appraisal_review.domain.verification import ReviewVerifier
from appraisal_review.ports.approval import ReviewAuthorization
from appraisal_review.ports.workflow import (
    AuditLogger,
    DocumentParser,
    FactExtractor,
    PDFWriter,
    RuleSetProvider,
)


class ReviewAgentController:
    """Coordinate tools and prevent unsafe case completion."""

    def __init__(
        self,
        *,
        parser: DocumentParser,
        fact_extractor: FactExtractor,
        rule_provider: RuleSetProvider,
        verifier: ReviewVerifier | None = None,
        pdf_writer: PDFWriter | None = None,
        audit_logger: AuditLogger | None = None,
        minimum_confidence: float = 0.85,
        authorization: ReviewAuthorization | None = None,
    ) -> None:
        self.authorization = authorization
        self.parser = parser
        self.fact_extractor = fact_extractor
        self.rule_provider = rule_provider
        self.verifier = verifier or ReviewVerifier()
        self.pdf_writer = pdf_writer
        self.audit_logger = audit_logger
        self.minimum_confidence = minimum_confidence

    async def review(self, request: AgentReviewRequest) -> AgentReviewRun:
        events: list[AuditEvent] = []
        await self._record(
            events, request.case_id, "workflow_started", WorkflowStatus.RECEIVED, "controller"
        )

        criteria = await self.parser.parse_document(request.criteria_document_uri)
        await self._record(events, request.case_id, "criteria_parsed", "ok", "parse_document")
        rule_set = await self.rule_provider.load_or_build_rules(criteria)
        await self._record(
            events,
            request.case_id,
            "rules_loaded",
            "candidate" if isinstance(rule_set, ReviewPolicy) else rule_set.status,
            "load_or_build_rules",
            rule_ids=[],
        )
        if not isinstance(rule_set, ReviewPolicy) and rule_set.status != "approved":
            await self._record(
                events,
                request.case_id,
                "human_review_required",
                WorkflowStatus.NEEDS_REVIEW,
                "controller",
                details={"reason": "rule set is not approved"},
            )
            return AgentReviewRun(
                case_id=request.case_id,
                status=WorkflowStatus.NEEDS_REVIEW,
                audit_events=events,
            )

        case_document = await self.parser.parse_document(request.case_document_uri)
        await self._record(events, request.case_id, "case_parsed", "ok", "parse_document")
        factors = await self.fact_extractor.extract_facts(case_document, case_id=request.case_id)
        await self._record(events, request.case_id, "facts_extracted", "ok", "extract_facts")

        case_review = None
        if isinstance(rule_set, ReviewPolicy) and isinstance(factors, CaseFacts):
            if request.case_id != rule_set.identity.case_id:
                raise ValueError("Request case does not match configured review material")
            sources = [criteria.source, case_document.source]
            known = {
                criteria.document_uri,
                case_document.document_uri,
                *(s.uri for s in sources if s is not None),
            }
            for source in rule_set.registry.documents:
                if source.uri not in known:
                    sources.append((await self.parser.parse_document(source.uri)).source)
            if any(source is None for source in sources):
                verification = VerificationReport(
                    status=EvaluationStatus.NEEDS_REVIEW,
                    critical_errors=["A typed current source registry is required"],
                )
                return AgentReviewRun(
                    case_id=request.case_id,
                    status=WorkflowStatus.NEEDS_REVIEW,
                    verification=verification,
                    audit_events=events,
                )
            case_review = CaseReviewer(self.authorization).review(
                rule_set, factors, SourceRegistry(documents=[s for s in sources if s is not None])
            )
            verification = VerificationReport(
                status=case_review.status,
                critical_errors=[f.trace for f in case_review.findings if f.status != "verified"],
            )
            result = case_review.comparisons[0] if len(case_review.comparisons) == 1 else None
        else:
            # Legacy adapter shapes still load, but cannot prove complete case review.
            if isinstance(rule_set, ReviewPolicy) or isinstance(factors, CaseFacts):
                raise ValueError("Rule and fact adapters must use the same schema generation")
            evaluation_request = FactorEvaluationRequest(
                case_id=request.case_id, rule_set_id=rule_set.rule_set_id, factors=factors
            )
            result = FactorRuleEngine(
                rule_set, minimum_confidence=self.minimum_confidence
            ).evaluate(evaluation_request)
            verification = self.verifier.verify(result, rule_set)
        await self._record(
            events,
            request.case_id,
            "results_verified",
            verification.status,
            "verify_results",
            details={"critical_errors": verification.critical_errors},
        )
        if not verification.can_complete:
            return AgentReviewRun(
                case_id=request.case_id,
                status=WorkflowStatus.FAILED
                if verification.status is EvaluationStatus.FAILED
                else WorkflowStatus.NEEDS_REVIEW,
                review=result,
                case_review=case_review,
                verification=verification,
                audit_events=events,
            )
        if result is None:
            return AgentReviewRun(
                case_id=request.case_id,
                status=WorkflowStatus.VERIFIED,
                case_review=case_review,
                verification=verification,
                audit_events=events,
                artifact_status="unsupported_contexts"
                if request.output_pdf_uri
                else "not_requested",
            )
        if request.output_pdf_uri is None:
            return AgentReviewRun(
                case_id=request.case_id,
                status=WorkflowStatus.VERIFIED,
                review=result,
                case_review=case_review,
                verification=verification,
                audit_events=events,
            )
        if self.pdf_writer is None:
            return AgentReviewRun(
                case_id=request.case_id,
                status=WorkflowStatus.VERIFIED,
                review=result,
                case_review=case_review,
                verification=verification,
                artifact_status="unavailable",
                audit_events=events,
            )
        if request.field_map is None:
            await self._record(
                events,
                request.case_id,
                "pdf_write_blocked",
                WorkflowStatus.FAILED,
                "write_pdf",
                details={"reason": "PDF writer and field map are required"},
            )
            return AgentReviewRun(
                case_id=request.case_id,
                status=WorkflowStatus.FAILED,
                review=result,
                case_review=case_review,
                verification=verification,
                pdf_error=PDFProblem(code=PDFErrorCode.WRITE),
                audit_events=events,
            )

        try:
            pdf_request = PDFWriteRequest(
                source_uri=request.case_document_uri,
                destination_uri=request.output_pdf_uri,
                result=result,
                field_map=request.field_map,
            )
        except ValidationError as error:
            code = PDFErrorCode.PLACEMENT
            for detail in error.errors():
                cause = detail.get("ctx", {}).get("error")
                if isinstance(cause, PDFWriteError):
                    code = cause.code
                    break
            return await self._pdf_failed(
                request.case_id, result, verification, events, code, case_review
            )

        try:
            raw_output = await self.pdf_writer.write_pdf(pdf_request)
            if not isinstance(raw_output, PDFWriteResult):
                raise InvalidPDFResultError("Writer must return PDFWriteResult")
            output = PDFWriteResult.model_validate(raw_output.model_dump())
            if (
                document_identity(output.output_uri)
                != document_identity(pdf_request.destination_uri)
                or output.page_count != case_document.page_count
                or set(output.written_field_ids)
                != {f.field_id for f in pdf_request.field_map.fields}
            ):
                raise InvalidPDFResultError("Writer result does not match the write request")
        except PDFWriteError as error:
            return await self._pdf_failed(
                request.case_id, result, verification, events, error.code, case_review
            )
        except ValidationError:
            return await self._pdf_failed(
                request.case_id,
                result,
                verification,
                events,
                PDFErrorCode.INVALID_RESULT,
                case_review,
            )
        except Exception:
            # An adapter bug must not discard findings or expose document/credential text.
            return await self._pdf_failed(
                request.case_id, result, verification, events, PDFErrorCode.WRITE, case_review
            )

        written = output.artifact_created
        await self._record(
            events,
            request.case_id,
            "pdf_written" if written else "pdf_simulated",
            WorkflowStatus.COMPLETED if written else WorkflowStatus.VERIFIED,
            "write_pdf",
            details={"output_uri": output.output_uri},
        )
        return AgentReviewRun(
            case_id=request.case_id,
            status=WorkflowStatus.COMPLETED if written else WorkflowStatus.VERIFIED,
            case_review=case_review,
            artifact_status="written" if written else "simulated",
            review=result,
            verification=verification,
            output_pdf_uri=output.output_uri if written else None,
            pdf_result=output,
            audit_events=events,
        )

    async def _pdf_failed(
        self,
        case_id: str,
        result: FactorReviewResult,
        verification: VerificationReport,
        events: list[AuditEvent],
        code: PDFErrorCode,
        case_review: CaseReviewResult | None = None,
    ) -> AgentReviewRun:
        await self._record(
            events,
            case_id,
            "pdf_write_failed",
            WorkflowStatus.FAILED,
            "write_pdf",
            details={"error_code": code.value},
        )
        return AgentReviewRun(
            case_id=case_id,
            status=WorkflowStatus.FAILED,
            case_review=case_review,
            review=result,
            verification=verification,
            audit_events=events,
            pdf_error=PDFProblem(code=code),
        )

    async def _record(
        self,
        events: list[AuditEvent],
        case_id: str,
        event_type: str,
        status: str,
        tool: str,
        *,
        rule_ids: list[str] | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        event = AuditEvent(
            case_id=case_id,
            sequence=len(events) + 1,
            event_type=event_type,
            status=status,
            tool=tool,
            rule_ids=rule_ids or [],
            details=details or {},
        )
        events.append(event)
        if self.audit_logger is not None:
            await self.audit_logger.append(event)

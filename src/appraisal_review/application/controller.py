"""Tool-based workflow controller with explicit safe completion branches."""

from appraisal_review.domain.factor_engine import FactorRuleEngine
from appraisal_review.domain.factor_models import (
    AgentReviewRequest,
    AgentReviewRun,
    AuditEvent,
    EvaluationStatus,
    FactorEvaluationRequest,
    WorkflowStatus,
)
from appraisal_review.domain.verification import ReviewVerifier
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
    ) -> None:
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
            rule_set.status,
            "load_or_build_rules",
            rule_ids=[rule.id for rule in rule_set.rules],
        )
        if rule_set.status != "approved":
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

        evaluation_request = FactorEvaluationRequest(
            case_id=request.case_id,
            rule_set_id=rule_set.rule_set_id,
            factors=factors,
        )
        result = FactorRuleEngine(
            rule_set,
            minimum_confidence=self.minimum_confidence,
        ).evaluate(evaluation_request)
        await self._record(
            events,
            request.case_id,
            "factors_evaluated",
            result.summary.status,
            "evaluate_factors",
            rule_ids=[item.rule_id for item in result.results if item.rule_id is not None],
        )

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
            status = (
                WorkflowStatus.FAILED
                if verification.status is EvaluationStatus.FAILED
                else WorkflowStatus.NEEDS_REVIEW
            )
            return AgentReviewRun(
                case_id=request.case_id,
                status=status,
                review=result,
                verification=verification,
                audit_events=events,
            )

        if request.output_pdf_uri is None:
            return AgentReviewRun(
                case_id=request.case_id,
                status=WorkflowStatus.VERIFIED,
                review=result,
                verification=verification,
                audit_events=events,
            )
        if self.pdf_writer is None or request.field_map is None:
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
                verification=verification,
                audit_events=events,
            )

        output_uri = await self.pdf_writer.write_pdf(
            request.case_document_uri,
            request.output_pdf_uri,
            result,
            request.field_map,
        )
        await self._record(
            events,
            request.case_id,
            "pdf_written",
            WorkflowStatus.COMPLETED,
            "write_pdf",
            details={"output_uri": output_uri},
        )
        return AgentReviewRun(
            case_id=request.case_id,
            status=WorkflowStatus.COMPLETED,
            review=result,
            verification=verification,
            output_pdf_uri=output_uri,
            audit_events=events,
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

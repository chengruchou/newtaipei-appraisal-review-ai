"""Explicit dependency composition. No cloud clients or document I/O at import."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from appraisal_review.application.controller import ReviewAgentController
from appraisal_review.config import Settings
from appraisal_review.ports.approval import ReviewAuthorization
from appraisal_review.ports.pdf import PDFWriter
from appraisal_review.ports.workflow import (
    AuditLogger,
    DocumentParser,
    FactExtractor,
    RuleSetProvider,
)


class ConfigurationError(Exception):
    """Stable, sanitized configuration failure; never includes setting values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ReviewAdapters:
    mode: Literal["local", "aws"]
    parser: DocumentParser
    fact_extractor: FactExtractor
    rule_provider: RuleSetProvider
    authorization: ReviewAuthorization | None = None
    pdf_writer: PDFWriter | None = None
    audit_logger: AuditLogger | None = None


ControllerFactory = Callable[[], ReviewAgentController]


def build_controller(
    settings: Settings | None = None, *, adapters: ReviewAdapters | None = None
) -> ReviewAgentController:
    try:
        # Revalidate even a Settings instance modified through model_copy/construct.
        config = (
            Settings.model_validate(settings.model_dump()) if settings is not None else Settings()
        )
    except ValidationError as error:
        raise ConfigurationError("invalid_configuration") from error
    if config.runtime_mode == "aws":
        if config.synthetic_demo:
            raise ConfigurationError("synthetic_aws_forbidden")
        if not all(
            (
                config.aws_region.strip(),
                config.bedrock_model_id.strip(),
                config.input_bucket.strip(),
                config.result_bucket.strip(),
                config.cases_table.strip(),
            )
        ):
            raise ConfigurationError("missing_aws_configuration")
        # Actual parser/extractor/provider arrive in #7; never substitute fixtures.
        if adapters is None:
            raise ConfigurationError("missing_aws_adapters")
    elif adapters is None and config.synthetic_demo:
        from appraisal_review.adapters.local.synthetic import synthetic_adapters

        adapters = synthetic_adapters()
    if adapters is None:
        raise ConfigurationError("missing_local_adapters")
    if adapters.mode != config.runtime_mode:
        raise ConfigurationError("adapter_mode_mismatch")
    required = [
        (adapters.parser, "parse_document"),
        (adapters.fact_extractor, "extract_facts"),
        (adapters.rule_provider, "load_or_build_rules"),
    ]
    if adapters.pdf_writer is not None:
        required.append((adapters.pdf_writer, "write_pdf"))
    if adapters.audit_logger is not None:
        required.append((adapters.audit_logger, "append"))
    if any(not callable(getattr(adapter, method, None)) for adapter, method in required):
        raise ConfigurationError("invalid_adapter")
    return ReviewAgentController(
        authorization=adapters.authorization,
        parser=adapters.parser,
        fact_extractor=adapters.fact_extractor,
        rule_provider=adapters.rule_provider,
        pdf_writer=adapters.pdf_writer,
        audit_logger=adapters.audit_logger,
        minimum_confidence=config.min_extraction_confidence,
    )

"""Static error mapping; provider exceptions never become public messages."""

from typing import cast

from appraisal_review.domain.extraction_contracts import FailureCode


class ExtractionError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def safe_code(code: str) -> FailureCode:
    aliases: dict[str, FailureCode] = {
        "image_limit": "unsupported_input",
        "page_context_limit": "unsupported_input",
        "unsupported_response": "malformed_output",
        "conflicting_legacy_evidence": "invalid_source_reference",
        "retry_exhausted": "budget_exhausted",
    }
    known = {
        "unauthorized_source",
        "privacy_unavailable",
        "source_changed",
        "unsupported_input",
        "unsupported_capability",
        "budget_exhausted",
        "access_denied",
        "configuration_error",
        "throttled",
        "service_unavailable",
        "timeout",
        "refused",
        "truncated_output",
        "malformed_output",
        "invalid_source_reference",
        "provider_error",
    }
    return aliases.get(code, cast(FailureCode, code) if code in known else "provider_error")


def provider_failure(error: Exception) -> FailureCode:
    response = getattr(error, "response", None)
    details = response.get("Error", {}) if isinstance(response, dict) else {}
    code = details.get("Code") if isinstance(details, dict) else None
    if not isinstance(code, str):
        code = None
    if code in {"ThrottlingException", "TooManyRequestsException"}:
        return "throttled"
    if code in {"ServiceUnavailableException", "InternalServerException", "ModelNotReadyException"}:
        return "service_unavailable"
    if code in {
        "AccessDeniedException",
        "UnrecognizedClientException",
        "ExpiredTokenException",
        "InvalidSignatureException",
        "UnauthorizedException",
    }:
        return "access_denied"
    if code in {"ValidationException", "ResourceNotFoundException"} or type(error).__name__ in {
        "NoCredentialsError",
        "PartialCredentialsError",
        "ProfileNotFound",
    }:
        return "configuration_error"
    if (
        isinstance(error, TimeoutError)
        or code == "ModelTimeoutException"
        or type(error).__name__
        in {
            "ReadTimeoutError",
            "ConnectTimeoutError",
            "EndpointConnectionError",
            "ConnectionClosedError",
        }
    ):
        # Conservatively unknown completion; never automatically retry a transport loss.
        return "timeout"
    return "provider_error"

"""Explicit, lazy workstation clients and injected Runtime clients; no default session."""

from __future__ import annotations

import re
import time
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from appraisal_review.adapters.aws.bedrock_dispatch import (
    install_bedrock_dispatch,
    require_bedrock_dispatch,
)
from appraisal_review.adapters.aws.extraction_errors import provider_failure
from appraisal_review.application.model_dispatch import SharedModelDispatcher
from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.extraction_contracts import ContractModel, PositiveCount
from appraisal_review.ports.competition_data import CompetitionDataAdmission
from appraisal_review.ports.document_extraction import ExtractionBoundaryError
from appraisal_review.ports.model_dispatch import (
    DispatchDenied,
    DispatchGuard,
    current_dispatch_guard,
    dispatch_guard,
    inherited_dispatch_authority,
)


class BedrockAccessPolicy(ContractModel):
    """Operator-reviewed policy, never constructed from document/model content.

    Metadata does not expose Converse compatibility. The exact foundation-model
    allowlist must come from a reviewed capability record; invocation proves access.
    """

    schema_version: Literal["bedrock-access-v1"] = "bedrock-access-v1"
    api: Literal["converse"] = "converse"
    account: str = Field(pattern=r"^[0-9]{12}$")
    role_name: str = Field(pattern=r"^[A-Za-z0-9+=,.@_-]{1,64}$")
    region: str = Field(pattern=r"^[a-z]{2}-[a-z]+-[0-9]+$")
    model_id: str = Field(min_length=1, max_length=2048)
    model_kind: Literal["foundation", "system_profile", "application_profile"]
    allowed_regions: tuple[str, ...] = Field(min_length=1)
    allowed_foundation_models: tuple[str, ...] = Field(min_length=1)
    converse_capability_digest: Digest
    allow_cross_region: bool = Field(default=False, strict=True)
    allow_global: bool = Field(default=False, strict=True)
    timeout_seconds: float = Field(gt=0, le=600)
    max_output_tokens: PositiveCount
    max_image_bytes: int = Field(gt=0, le=3_750_000, strict=True)
    max_image_width: int = Field(gt=0, le=8000, strict=True)
    max_image_height: int = Field(gt=0, le=8000, strict=True)
    max_image_pixels: int = Field(gt=0, le=16_000_000, strict=True)

    @model_validator(mode="after")
    def explicit_routing(self) -> BedrockAccessPolicy:
        if len(set(self.allowed_regions)) != len(self.allowed_regions) or any(
            not re.fullmatch(r"[a-z]{2}-[a-z]+-[0-9]+", region) for region in self.allowed_regions
        ):
            raise ValueError("Invalid or duplicate allowed region")
        if len(set(self.allowed_foundation_models)) != len(self.allowed_foundation_models):
            raise ValueError("Duplicate foundation model")
        if any(not re.fullmatch(r"[a-zA-Z0-9.:-]+", m) for m in self.allowed_foundation_models):
            raise ValueError("Foundation allowlist requires model IDs")
        if any(r != self.region for r in self.allowed_regions) and not self.allow_cross_region:
            raise ValueError("Cross-region routing requires explicit opt-in")
        if not self.allow_global and self.model_id.split("/")[-1].startswith("global."):
            raise ValueError("Global routing requires explicit opt-in")
        return self


class AWSClients(Protocol):
    def client(self, service: str, region: str, timeout: float) -> Any:
        """Trusted configuration supplies credentials/endpoints, never document input."""
        ...


class WorkstationClients:
    """No SDK import until explicitly requested after source validation."""

    def __init__(
        self,
        *,
        profile: str,
        dispatcher: SharedModelDispatcher | None = None,
        competition_admission: CompetitionDataAdmission | None = None,
    ) -> None:
        if not profile.strip() or profile != profile.strip() or profile == "default":
            raise ExtractionBoundaryError("configuration_error")
        self.profile = profile
        self.dispatcher = dispatcher
        self.competition_admission = competition_admission
        self._session: Any = None

    def client(self, service: str, region: str, timeout: float) -> Any:
        import boto3
        from botocore.config import Config

        if service in {"bedrock", "bedrock-runtime"} and self.dispatcher is None:
            raise ExtractionBoundaryError("configuration_error")
        if self._session is None:
            self._session = boto3.Session(profile_name=self.profile, region_name=region)
        client = self._session.client(
            service,
            region_name=region,
            config=Config(
                connect_timeout=min(10, timeout / 2),
                read_timeout=timeout / 2,
                retries={"total_max_attempts": 1},
            ),
        )
        if service in {"bedrock", "bedrock-runtime"} and self.dispatcher is not None:
            return install_bedrock_dispatch(
                client, self.dispatcher, competition_admission=self.competition_admission
            )
        return client


def _foundation(arn: str) -> tuple[str, str]:
    match = re.fullmatch(r"arn:aws:bedrock:([a-z0-9-]+)::foundation-model/([a-zA-Z0-9.:-]+)", arn)
    if match is None:
        raise ExtractionBoundaryError("unsupported_capability")
    return match.group(1), match.group(2)


def preflight(clients: AWSClients, policy: BedrockAccessPolicy) -> Any:
    """Metadata-only preflight. Return runtime client without invoking a model.

    Runtime supplies an AWSClients implementation bound to its designated role;
    this function verifies STS identity identically and never creates a session.
    """
    routing_client: Any = None
    try:
        policy = BedrockAccessPolicy.model_validate(policy)
        from appraisal_review.adapters.aws.competition_clients import (
            CompetitionAWSClients,
            CompetitionModelClients,
        )

        deadline = time.monotonic() + policy.timeout_seconds
        try:
            inherited = current_dispatch_guard()
        except DispatchDenied:
            inherited = None
        if inherited is not None:
            deadline = min(deadline, inherited.deadline)
        if isinstance(clients, CompetitionAWSClients):
            clients = clients.for_model(policy.model_id, policy.model_kind, deadline)
            routing_client = clients

        def metadata(call: Any, **kwargs: Any) -> Any:
            guard = DispatchGuard(deadline, inherited_dispatch_authority())
            if inherited is not None:
                guard.authority = inherited.authority
                guard.cancelled = inherited.cancelled
            with dispatch_guard(guard):
                return call(**kwargs)

        def client(service: str, region: str) -> Any:
            result = clients.client(service, region, policy.timeout_seconds)
            if result.meta.region_name != region:
                raise ExtractionBoundaryError("configuration_error")
            if service in {"bedrock", "bedrock-runtime"}:
                require_bedrock_dispatch(result)
            return result

        caller = client("sts", policy.region).get_caller_identity()
        prefix = f"arn:aws:sts::{policy.account}:assumed-role/{policy.role_name}/"
        if caller["Account"] != policy.account or not re.fullmatch(
            re.escape(prefix) + r"[^/]+", caller["Arn"]
        ):
            raise ExtractionBoundaryError("access_denied")
        control = client("bedrock", policy.region)
        if policy.model_kind == "foundation":
            if policy.model_id.startswith("arn:"):
                models = [_foundation(policy.model_id)]
                if models[0][0] != policy.region:
                    raise ExtractionBoundaryError("configuration_error")
            else:
                models = [(policy.region, policy.model_id)]
        else:
            profile = metadata(
                control.get_inference_profile, inferenceProfileIdentifier=policy.model_id
            )
            kind = "SYSTEM_DEFINED" if policy.model_kind == "system_profile" else "APPLICATION"
            resource = (
                "inference-profile" if kind == "SYSTEM_DEFINED" else "application-inference-profile"
            )
            arn = profile["inferenceProfileArn"]
            expected = f"arn:aws:bedrock:{policy.region}:{policy.account}:{resource}/"
            if (
                profile["status"] != "ACTIVE"
                or profile["type"] != kind
                or not arn.startswith(expected)
                or arn[len(expected) :] != profile["inferenceProfileId"]
                or policy.model_id not in {arn, profile["inferenceProfileId"]}
            ):
                raise ExtractionBoundaryError("configuration_error")
            if profile["inferenceProfileId"].startswith("global.") and not policy.allow_global:
                raise ExtractionBoundaryError("configuration_error")
            if not 1 <= len(profile["models"]) <= 5:
                raise ExtractionBoundaryError("unsupported_capability")
            models = [_foundation(item["modelArn"]) for item in profile["models"]]
        if not models or len(set(models)) != len(models):
            raise ExtractionBoundaryError("unsupported_capability")
        if isinstance(clients, CompetitionModelClients):
            clients.validate_destinations(
                tuple(
                    f"arn:aws:bedrock:{region}::foundation-model/{model}"
                    for region, model in models
                )
            )
        # Validate the entire destination set before fetching any model metadata.
        if any(
            region not in policy.allowed_regions
            or model not in policy.allowed_foundation_models
            or (region != policy.region and not policy.allow_cross_region)
            for region, model in models
        ):
            raise ExtractionBoundaryError("unsupported_capability")
        # The required inference type follows the invocation route, not preference.
        # A direct foundation request needs ON_DEMAND. A profile-routed request reaches
        # each destination through the profile, and such destinations legitimately
        # publish only INFERENCE_PROFILE; demanding ON_DEMAND there rejects every
        # profile-only model, while accepting ON_DEMAND alone would admit a destination
        # the profile cannot route to.
        required_inference_type = (
            "ON_DEMAND" if policy.model_kind == "foundation" else "INFERENCE_PROFILE"
        )
        for region, model in models:
            details = metadata(
                client("bedrock", region).get_foundation_model, modelIdentifier=model
            )["modelDetails"]
            if (
                details["modelId"] != model
                or _foundation(details["modelArn"]) != (region, model)
                or not {"TEXT", "IMAGE"} <= set(details["inputModalities"])
                or "TEXT" not in details["outputModalities"]
                or required_inference_type not in details["inferenceTypesSupported"]
                or details["modelLifecycle"]["status"] != "ACTIVE"
            ):
                raise ExtractionBoundaryError("unsupported_capability")
        return client("bedrock-runtime", policy.region)
    except ExtractionBoundaryError:
        if routing_client is not None:
            routing_client.invalidate()
        raise
    except Exception as error:
        if routing_client is not None:
            routing_client.invalidate()
        code = provider_failure(error)
        raise ExtractionBoundaryError(
            "configuration_error" if code == "provider_error" else code
        ) from None

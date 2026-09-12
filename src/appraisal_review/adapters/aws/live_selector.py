"""Factory for a live, dispatch-guarded Bedrock Converse action selector.

Every physical send from the returned selector crosses the transport boundary that
``install_bedrock_dispatch`` installs, whose real signature is
``install_bedrock_dispatch(client, dispatcher, *, competition_admission=None,
allow_loopback_for_testing=False)``:

- ``dispatcher`` is a ``SharedModelDispatcher`` over a ``SqliteModelDispatchStore``,
  the minimal store this codebase offers that is constructible offline. It enforces
  the shared monotonic interval between physical sends, and because the guard wraps
  the botocore transport seam it also covers SDK retries (the client additionally
  pins ``total_max_attempts=1`` so botocore injects none of its own).
- ``competition_admission`` cannot be legitimately constructed offline (it requires
  a reviewed-data authority with actual admission records), so it is accepted as an
  optional argument and defaults to ``None``. The dispatch transport then FAILS
  CLOSED: any physical send raises ``DispatchDenied("competition_admission_required")``
  until a composition supplies a real ``ReviewedCompetitionAdmission``. Nothing here
  weakens that guard.

``boto3`` is imported lazily inside the factory so importing this module stays cheap
and unit tests never require the SDK.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from appraisal_review.adapters.aws.action_selector import (
    BedrockActionSelector,
    ModelSelectorConfig,
)
from appraisal_review.adapters.aws.bedrock_dispatch import install_bedrock_dispatch
from appraisal_review.adapters.local.sqlite_model_dispatch import SqliteModelDispatchStore
from appraisal_review.application.model_dispatch import SharedModelDispatcher
from appraisal_review.ports.competition_data import CompetitionDataAdmission

#: Host-local fallback throttle ledger when the composition supplies no path.
_DEFAULT_STORE = Path.home() / ".appraisal-review" / "model_dispatch.sqlite3"


def live_action_selector(
    model_id: str,
    region: str,
    min_interval_seconds: float = 1.2,
    *,
    dispatch_store_path: Path | None = None,
    competition_admission: CompetitionDataAdmission | None = None,
    provenance_for_case: Callable[[str], str | None] | None = None,
) -> BedrockActionSelector:
    """Build a real bedrock-runtime Converse selector behind the dispatch guard.

    ``attempts=1`` keeps the selector to a single model call per selection; the
    shared interval (at least 1.2 seconds) separates physical sends across every
    process using the same dispatch store.
    """
    if not model_id.strip() or model_id != model_id.strip():
        raise ValueError("An exact non-empty Bedrock model id is required")
    if not region.strip() or region != region.strip():
        raise ValueError("An exact non-empty AWS region is required")
    if not min_interval_seconds >= 1.2:
        raise ValueError("The shared physical-send interval must be at least 1.2 seconds")
    import boto3
    from botocore.config import Config

    path = dispatch_store_path if dispatch_store_path is not None else _DEFAULT_STORE
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    dispatcher = SharedModelDispatcher(
        SqliteModelDispatchStore(path), interval_seconds=min_interval_seconds
    )
    client = boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(
            connect_timeout=10,
            read_timeout=60,
            retries={"total_max_attempts": 1},
        ),
    )
    install_bedrock_dispatch(client, dispatcher, competition_admission=competition_admission)
    return BedrockActionSelector(
        client,
        ModelSelectorConfig(model_id=model_id, attempts=1),
        provenance_for_case=provenance_for_case,
    )

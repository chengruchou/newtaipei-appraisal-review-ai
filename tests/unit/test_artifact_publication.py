"""Publication regression cases execute real SDK requests through Moto/Stubber."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from pypdf import PdfReader
from test_formal_pdf_output import TOKEN, placeholder_map, placeholder_request, render_config
from test_publication_authority import environment as environment

from appraisal_review.adapters.aws.artifacts.publication import (
    AttemptArtifactPublisher,
    DynamoDBManifestStore,
)
from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.artifact_publication import (
    ArtifactKey,
    PublicationError,
    PublishedArtifact,
)
from appraisal_review.domain.service_contracts import Permission


def test_key_layout_separates_case_run_attempt_and_rejects_foreign_keys(environment):
    h = environment
    artifact = h.candidate.artifacts[0]
    parsed = ArtifactKey.parse(artifact.key)
    assert (parsed.case_id, parsed.run_id, parsed.attempt_id) == (
        "case-1",
        h.run.run_id,
        h.run.attempt_id,
    )
    for bad in (
        "cases/case-1/../case-2/artifact.pdf",
        f"cases/case-1/runs/{h.run.run_id}/artifacts/{artifact.artifact_id}.pdf",
        "cases/case-1/runs/not-a-uuid/attempts/also-bad/artifacts/x.pdf",
        artifact.key + ".bak",
    ):
        with pytest.raises(ValueError, match="attempt-scoped artifact layout"):
            ArtifactKey.parse(bad)
    with pytest.raises(ValueError, match="must bind an attempt"):
        ArtifactKey.for_run(h.run.model_copy(update={"attempt_id": None}), uuid4())
    foreign = h.candidate.model_dump(mode="json")
    foreign["run"]["revision"]["case_id"] = "case-2"
    with pytest.raises(ValueError, match="this exact attempt"):
        type(h.candidate).model_validate(foreign)


def test_two_attempts_race_only_current_fencing_token_publishes(environment):
    h = environment
    first = h.candidate
    committed_one = h.publish()
    h.run = h.run.model_copy(update={"attempt_id": uuid4()})
    h.row.update(attempt_id=str(h.run.attempt_id), fencing_token=2)
    h.attempt_row.update(
        sk=f"ATTEMPT#{h.run.attempt_id}", attempt_id=str(h.run.attempt_id), fencing_token=2
    )
    h.put(h.row)
    h.put(h.attempt_row)
    second, data = h.new_candidate()
    second = h.stage(second, data)
    h.approve(second)
    committed_two = h.publish(second, token=2)
    with pytest.raises(PublicationError, match="stale_publication"):
        h.publish(first)
    assert h.publish(second, token=2) == committed_two
    replacement, data = h.new_candidate()
    replacement = h.stage(replacement, data)
    h.approve(replacement)
    with pytest.raises(PublicationError, match="manifest_conflict"):
        h.publish(replacement, token=2)
    assert h.resolver.result(h.principal, "case-1", h.run.run_id) == committed_two
    assert committed_one.fencing_token == 1 and committed_two.fencing_token == 2


def test_object_presence_or_worker_claim_is_not_success(environment):
    h = environment
    artifact = h.candidate.artifacts[0]
    assert h.s3.head_object(Bucket="result-bucket", Key=artifact.key)["ContentLength"] == len(
        h.data
    )
    assert h.resolver.result(h.principal, "case-1", h.run.run_id) is None
    with pytest.raises(PublicationError, match="unpublished"):
        h.resolver.fetch_verified(
            h.principal, "case-1", h.run.run_id, artifact.artifact_id, h.tmp_path / "out.pdf"
        )


def test_tampered_digest_size_or_content_is_rejected(environment):
    h = environment
    for update in (
        {"size_bytes": 5},
        {"content_hash": "9" * 64},
        {"page_count": 2},
        {"writer_version": "invalid"},
    ):
        bad = h.candidate.artifacts[0].model_copy(update=update)

        class PlaceholderWriter:
            reveals_placeholders = False

        with pytest.raises(PublicationError, match="artifact_mismatch"):
            h.publisher.stage(
                h.tmp_path / "staged.pdf", run=h.run, artifact=bad, writer=PlaceholderWriter()
            )
    committed = h.publish()
    row = {
        **h.store.manifest_key("case-1", h.run.run_id),
        "manifest_digest": committed.manifest_digest,
        "fencing_token": 1,
        "manifest": committed.model_dump_json().replace(
            h.candidate.artifacts[0].content_hash, "3" * 64
        ),
    }
    h.put(row)
    with pytest.raises(PublicationError, match="manifest_corrupt"):
        h.resolver.result(h.principal, "case-1", h.run.run_id)


def test_unauthorized_principals_cannot_query_or_download(environment):
    h = environment
    h.publish()
    for principal in (
        replace(h.principal, case_ids=frozenset({"case-2"})),
        replace(h.principal, permissions=frozenset({Permission.CONFIRM})),
    ):
        with pytest.raises(ServiceFault):
            h.resolver.result(principal, "case-1", h.run.run_id)
        with pytest.raises(ServiceFault):
            h.resolver.download_url(
                principal, "case-1", h.run.run_id, h.candidate.artifacts[0].artifact_id
            )
        with pytest.raises(ServiceFault):
            h.resolver.fetch_verified(
                principal,
                "case-1",
                h.run.run_id,
                h.candidate.artifacts[0].artifact_id,
                h.tmp_path / "denied.pdf",
            )


def test_download_authority_is_short_lived_and_manifest_bound(environment):
    h = environment
    h.access["expires_at"] = 9999
    h.put(h.access)
    h.publish()
    artifact = h.candidate.artifacts[0]
    for requested, expected in ((99999, 900), (30, 30)):
        url = h.resolver.download_url(
            h.principal, "case-1", h.run.run_id, artifact.artifact_id, expires_in=requested
        )
        query = parse_qs(urlparse(url).query)
        assert query["versionId"] == [artifact.object_version]
        assert query["X-Amz-Expires"] == [str(expected)]
    with pytest.raises(PublicationError, match="unpublished"):
        h.resolver.download_url(h.principal, "case-1", h.run.run_id, uuid4())


def test_reconciliation_recommits_after_crash_and_retries_transient_reads(environment, monkeypatch):
    h = environment
    # The real SDK transaction fails once; no fabricated persistence semantics.
    real = h.client.transact_write_items

    def fail_before(**kwargs):
        raise TimeoutError("synthetic transport interruption")

    monkeypatch.setattr(h.client, "transact_write_items", fail_before)
    with pytest.raises(PublicationError, match="manifest_store_unavailable"):
        h.publish()
    assert h.store.read("case-1", h.run.run_id) is None
    monkeypatch.setattr(h.client, "transact_write_items", real)
    committed = h.publish()
    # Reconstruct every adapter against persistent emulator state.
    fresh = DynamoDBManifestStore(
        h.client, table_name="publication", job_table_name="publication", clock=h.clock
    )
    publisher = AttemptArtifactPublisher(
        object_store=h.publisher.object_store,
        manifests=fresh,
        result_bucket="result-bucket",
        work_directory=h.tmp_path,
    )
    assert (
        publisher.publish(h.candidate, fencing_token=1, principal=h.principal, attempt=h.attempt)
        == committed
    )
    calls = 0
    original = h.s3.get_object

    def transient(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("synthetic read failure")
        return original(**kwargs)

    monkeypatch.setattr(h.s3, "get_object", transient)
    downloaded = h.tmp_path / "fetched.pdf"
    assert (
        h.resolver.fetch_verified(
            h.principal, "case-1", h.run.run_id, h.candidate.artifacts[0].artifact_id, downloaded
        )
        == h.candidate.artifacts[0]
    )
    assert calls == 2 and downloaded.read_bytes() == h.data


def test_revealed_backfill_output_is_never_publishable(environment):
    h = environment
    for flag in (True, "false", 0, None):

        class Revealing:
            reveals_placeholders = flag

        with pytest.raises(PublicationError, match="revealed_output_forbidden"):
            h.publisher.stage(
                h.tmp_path / "staged.pdf",
                run=h.run,
                artifact=h.candidate.artifacts[0],
                writer=Revealing(),
            )
    with pytest.raises(ValueError, match="placeholder_only"):
        PublishedArtifact.model_validate(
            {**h.candidate.artifacts[0].model_dump(mode="json"), "placeholder_only": False}
        )


def test_cjk_placeholder_pdf_uploads_commits_downloads_and_reopens(environment):
    h = environment
    field_map = placeholder_map()
    request, policy = placeholder_request(h.tmp_path, field_map, "attempt-local.pdf")
    config = render_config()
    writer = LocalPDFWriter(render_config=config, template_policy=policy)
    written = asyncio.run(writer.write_pdf(request))
    assert written.artifact_created
    local = h.tmp_path / "attempt-local.pdf"
    data = local.read_bytes()
    identity = uuid4()
    artifact = PublishedArtifact(
        artifact_id=identity,
        key=ArtifactKey.for_run(h.run, identity).key(),
        content_hash=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        writer_version="2",
        template_id=policy.template_id,
        template_version="2026.09",
        template_hash=policy.template_sha256,
        field_map_hash=policy.field_map_sha256,
        font_hash=hashlib.sha256(config.font_path.read_bytes()).hexdigest(),
        field_ids=tuple(written.written_field_ids),
        page_count=written.page_count,
        source_versions=(h.source,),
    )
    artifact = h.publisher.stage(local, run=h.run, artifact=artifact, writer=writer)
    candidate = h.candidate.model_copy(update={"artifacts": (artifact,)})
    h.approve(candidate)
    h.publish(candidate)
    downloaded = h.tmp_path / "downloaded.pdf"
    h.resolver.fetch_verified(h.principal, "case-1", h.run.run_id, identity, downloaded)
    assert TOKEN in PdfReader(downloaded).pages[0].extract_text()
    assert downloaded.read_bytes() == data and local.read_bytes() == data

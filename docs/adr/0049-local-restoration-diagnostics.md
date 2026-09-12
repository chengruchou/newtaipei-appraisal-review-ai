# ADR 0049: Bounded diagnostics for local restoration and download

Status: Implemented locally; broader operator and corpus acceptance remains open.

## Context

The local bridge intentionally returns generic rejection bodies. This protected
private values but also made distinct restoration, mapping, publication and
review-lifetime failures indistinguishable. Some current-publication predicates
catch exceptions and return false; logging only the outer bridge exception loses
the observed failure. Historical browser timeout and later 409 evidence cannot
be assigned a cause retroactively. A fresh successful download demonstrated
that repeated full trust checks can occupy a substantial part of a browser wait,
without proving a defective predicate or permitting weaker validation.

The historical and repeated original inputs are the failing synthetic CJK
rehearsal PDFs authored by `write_privacy_sources` in
`scripts/privacy_raster_fixture.py`. Their exact input hashes are preserved,
including the two-page form's eight output slots. They are not the user's
official competition attachments or real valuation forms. Local execution of
real OCR and PDF libraries is synthetic workflow evidence, not real-case,
official-asset, formal-material or AWS acceptance.

## Decision

Add a request-local diagnostic scope after the existing authentication, origin,
host and body boundary. The server correlation remains a fresh UUID. Preserve
all response bodies and statuses; expose only finite failure stage/code headers
to the configured origin. Record the first innermost observed fault before a
publisher can swallow it. Known mapping and privacy fault enums remain distinct;
unknown exceptions become fixed generic codes without their messages.

Use a typed `ValueError` subtype for known review expiry, revocation, engine and
evidence comparisons. It preserves existing refusal semantics while supplying a
stable diagnostic code. Generic callback failures do not claim a more precise
cause than the callback actually supplies. A mapping expiry and a review lease
expiry therefore remain different observations.

An optional local callback receives one terminal record with server correlation,
operation, response-start status, failure and bounded aggregate phase timings.
Context variables propagate through the existing worker dispatch and isolate
concurrent requests. Nested spans are inclusive, not additive. Preserve existing
bounded OCR refusal counts while leaving every raw measurement unchanged.
Never emit exception strings, paths, credentials, private identifiers, OCR text,
transcriptions or evidence contents. Callback failure cannot modify access or
response bytes. A cancellation is propagated and does not release a session lock
while its worker still runs.

No diagnostic grants authority. Keep both full current-authority passes around
final file reading and every existing review check before and after slow work.
Preserve exact source, publication, encrypted mapping, plan, pixel, engine,
receipt and retained-evidence bindings, and wall/monotonic expiry. Do not cache
authority, extend TTL, increase OCR confidence or drop observations to improve
latency. Any later performance change requires measured evidence and regressions
proving the same checks still apply across concurrent expiry and revocation.

## Validation and consequences

Focused preimplementation tests reproduced missing diagnostic stage/code for
bridge, validation and mapping faults, including swallowed publisher failures.
Regressions now exercise completed reads with a barrier inside actual file I/O:
unchanged authority permits a byte-verified reopened PDF, while review expiry,
monotonic expiry, review revocation or publication loss during the read refuses
the response after rechecking current authority. Diagnostic sink failure and
malformed exception attributes cannot disclose a private canary or change the
download decision. Concurrent scopes and bounded aggregation retain no raw OCR.

The wire vocabulary and local evidence limits are documented in
[local OCR review](../local-privacy-ocr-review.md). A status captured at response
start does not prove client delivery, PDF reopening, real OCR accuracy or human
approval. Browser evidence must independently preserve those assertions. The
operator provisioner, real keys/materials, lifecycle policy, corpus acceptance
and cloud deployment remain separately gated work.

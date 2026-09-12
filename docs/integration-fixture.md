# Synthetic integration fixture boundary

`appraisal_review.testing.integration_fixture` (also exposed through
`scripts/integration_fixture.py`) creates a fresh, isolated CJK case. It accepts
only a new directory and the explicit `approve_synthetic` boolean, never source
documents, caller-selected identities, rules, templates, or an approval store.
Each case, actor, source and revision receives an independent UUID4 identity.
Generated documents pass real C2 ingestion/readback and native parsing. Original
text, page, bounding box, source identity and confidence zero remain intact.

The fixed rules are explicitly approved **synthetic test rules** for this case.
This does not confirm observations or authorize material. The default fixture
has no side confirmations and no material approval store. The optional
`approve_synthetic=True` branch confirms only the just-generated fixed synthetic
observations and writes an exact receipt in a newly created local test store.
It is never a business approval or evidence of real model quality.

## Resumed revisions

After the actual human-task service records confirmations, the trusted local
launcher may call `fixture.authorize(stored_snapshot)`. This returns a
`SyntheticRevisionAuthorization` implementing `permits(material)` for that
exact digest. It does not call `confirm_side`, edit a snapshot, sign a receipt,
advance a revision, pin a run, or publish a result. It does not authorize an
unconfirmed side or import a proposed confidence/citation as established fact.

The helper revalidates the serialized snapshot/revision relationship and checks:

- The original generated case identity, documents/registry, rules, comparison
  identities, inventory and reported values are unchanged. Revision version and
  confirmed numeric side values may change; their original raw text, evidence,
  units, provenance and confidence zero remain unchanged.
- Every side is already `reviewer_confirmed`, with its exact current side digest
  and the fixture principal's actor identity. Confirmations present in the
  original opt-in fixture may retain their original OS reviewer identity.
  Unknown reviewers, stale confirmations and missing confirmations are rejected.
- The original writer configuration, field map, template hash and pinned font
  bytes remain intact. C2 currently authorizes each exact document and its bytes
  match the local parser input. `permits` repeats these checks so source revocation
  or replacement invalidates a previously returned synthetic capability.

The caller supplies the authoritative stored snapshot. This helper cannot prove
that an arbitrary Python-created confirmation came from an actual HTTP human
response: Python code in the local test process is trusted. The launcher must
load revisions from the real canonical human-task/revision service, retain its
receipt evidence, and keep this helper off public request and model-tool paths.
Current revision ancestry, owner authorization, cancellation/fencing, and exact
new-run C2 snapshot checks remain the integrated service's responsibilities.

This is an in-process test capability. The initial OS-signed local test store
and the resumed API-principal capability are distinct; the helper never rewrites
API actor confirmations into OS identities to satisfy the local store.

## PDF scope and invocation

PyMuPDF's bundled Droid Sans Fallback bytes must match
`ee38813ea00c3e32add4268fff7fff9e39417b4913cb13be2415164a47807cc2`.
ReportLab verifies and embeds that actual font in CJK inputs and written grades.
The empty template uses Base14 text because the current source-template
preflight explicitly rejects embedded TrueType source metrics. No preflight
restriction is relaxed. The real writer fills two contexts and reopens output;
unconfirmed fixtures produce `needs_review` without calling the writer.

From the integration checkout, using its available repository virtual environment:

```sh
.venv/bin/python -m appraisal_review.testing.integration_fixture --directory artifacts/new-synthetic-case
```

The directory must not already exist. Add `--approve-synthetic` only for the
explicit fixed synthetic success case. Generated files and local approval
material remain ignored. Use the returned `fixture.principal`, `fixture.documents`
and `fixture.snapshot` in the parent composition; never send local configuration,
paths, keys or approval-store contents to a browser or cloud request.

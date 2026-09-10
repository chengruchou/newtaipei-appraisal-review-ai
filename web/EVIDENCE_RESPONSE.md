# Canonical evidence response adapter

The integration API's canonical `HumanTask` adds `reason_code` and
`affected_subject_ids` and advertises `evidence_supply` / `supply_evidence`.
The existing `TaskView` envelope remains unchanged. OpenAPI and generated types
are exported from the integration API union, including revision and placeholder
extensions; the strict runtime validator reads that same schema.

`ResponseForm` treats `supply_evidence` as an explicit correction command. It
requires a matching authoritative subject/revision, supported type, usable
numeric unit and at least one explicitly selected citation. Choices come only
from the task or authoritative stored observation. Missing/degenerate locations
are excluded; the browser cannot invent a page, rectangle, excerpt or source.

The confirmed command includes the exact selected citation objects and retains
the original confidence, including zero. Display, submission and unknown-outcome
retry all use the same frozen command. The service must still resolve every
citation against its current authorized source catalog; being offered by the
frontend alone does not establish source authority. If no source can be located,
admission or evidence resolution must happen before the task can be answered.

`tests/evidence-response.test.tsx` first failed on all three cases before this
adapter, then passed with the full integration frontend checks. The cases cover
explicit citation selection and exact payload, missing subject metadata, and
absence of a resolving source region. Real configured API/browser acceptance
remains a separate integration check.

The artifact consumer also accepts the canonical `ServiceResult.artifacts`
union: the unchanged legacy manifest and `artifact-manifest-v2`. The latter
describes a fenced publication and carries every covered comparison in
`contexts`. The result panel displays all of those contexts, including target
and comparable identifiers, rather than displaying only the primary `context`.
Both versions retain authenticated download and exact content-hash validation.
`tests/artifact-contexts.test.tsx` validates the v2 response against the exported
schema and verifies that both comparison rows are visible; it failed before the
rendering change. The publication contract is documented by the integration
owner in `docs/artifact-publication.md`.

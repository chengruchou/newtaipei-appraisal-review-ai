# ADR 0025: Resolve admitted run bytes before candidate extraction

Status: proposed, pending independent review and live acceptance.

## Decision

`DocumentSnapshotResolver` reads #27's immutable run snapshot with the current
principal, case, purpose and exact document reference. It verifies the privacy
manifest digest, bytes and page bounds, parses those admitted bytes with the
existing native PDF parser, and checks page geometry against the admitted manifest.
It rechecks authorization and snapshot content after parsing, before returning
provider-visible bytes. No caller-controlled path or temporary original PDF is
opened. Parser source identities use an opaque `urn:document` URI.

The existing bounded Bedrock adapter receives that snapshot. Parsed regions and
rendered images remain tied to the same document/page. Low-confidence,
model-proposed, missing, conflicting and unsupported candidates produce located
`HandoffRequest` objects. These request work from the human-task owner; they are
not persisted tasks, accepted answers or material approvals. Candidate confidence
and evidence are unchanged. The reviewed confirmation and exact material-approval
protocols retain their authority, including legitimate confirmation of
confidence-zero observations.

The evaluation runner consumes frozen manifests and independent #23 golden
artifacts, and writes separate reports. It cannot rewrite or approve the goldens.
Unknown usage and unpriced calls remain unknown. A quoted budget ceiling is an
estimate, not an AWS billing control. Synthetic two-page probes are an explicit
opt-in CLI and cannot establish real-case extraction accuracy.

The probe requires exact dated positive token rates and rejects an approved
amount below maximum call/input/output reservations. CountTokens checks each
actual request before Converse. Unsupported counting fails closed; inference
profile pricing is not guessed. Partial-page failures preserve known telemetry.

## Consequences

Legacy HTTP/invocation DTOs and local parser path behavior remain unchanged.
This supplies the C2-to-A2 adapter, not the browser privacy application or durable
human-task database. Unreviewed #34-#39 integrations and real AWS rehearsal remain
separate gates. No real source, private export map, signing key, receipt,
provider output or PDF artifact belongs in the repository.

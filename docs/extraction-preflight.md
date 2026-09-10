# Authorized extraction and offline preflight

Phase 3 update: the assembly now returns `PageOutcome` with per-attempt telemetry
and a shared run budget. Reviewed image ceilings are required in access policy.
See [bounded execution](extraction-execution.md); the Phase 2 verification below
is historical evidence for its original implementation.

Status: Issue 21 Phase 2 local implementation. The application assembly and
preflight are implemented and tested with synthetic injected dependencies.
Actual #22/#27 privacy/source integration is still absent on this branch.
[ADR 0022](adr/0022-authorized-extraction-preflight.md) records the boundary.

## Offline request plan

Use a `PageRequest`, `ProviderConfiguration` and `ExecutionBudget` JSON file:

```text
python -m appraisal_review.document_cli extract-plan --request examples/extraction-v1/request.json --configuration artifacts/provider-configuration.json --budget artifacts/extraction-budget.json
```

For a synthetic local check, copy the `configuration` and `budget` objects from
`examples/extraction-v1/evaluation.json` to the two ignored artifact files. These
are contract fixtures, not a real model selection. This command performs no
provider preflight, source resolution, document read or SDK import. It prints a
JSON plan with zero provider calls, `ready_for_live=false`, privacy integration
unavailable, source authorization unchecked and model access unchecked. Invalid
configuration prints only a static code to stderr and exits 2.

Legacy `extract` exits 2 with `privacy_unavailable` before opening its manifest.
It cannot be enabled by supplying account/profile flags. Local `parse`,
`native-candidates`, `check-golden`, `assemble` and reviewer commands remain
available under their existing platform and authority requirements.

## Programmatic composition

1. Trusted server configuration supplies `Principal`, a
   `SanitizedSnapshotResolver`, a snapshot renderer and a backend. The application
   requires existing `Permission.REVIEW` and case access; the resolver must still
   verify document-specific rights, exact purpose, privacy evidence and version.
   No body-provided principal or fabricated manifest is trusted by design.
2. `AuthorizedExtractionService.extract` revalidates the request and resolved
   snapshot. With no resolver it raises `privacy_unavailable` before AWS work.
3. `BedrockSnapshotBackend` rechecks configuration/budget and byte size, then
   `SnapshotPDFRenderer` renders the immutable bytes in the existing isolated PDF
   worker. It checks page count, geometry and rendered bounds without opening the
   source URI. Injected renderers are trusted code; test renderers are labelled mocks.
4. The backend validates image header/dimensions/size and exact request character
   length before any AWS client is constructed. It sends only the closed context
   and allowlisted sanitized source projection. Full decoder and model-specific
   image-limit validation remain Phase 3 work.
5. Explicit preflight validates identity and all permitted routing destinations;
   only then does the private extraction core call Converse and preserve its
   existing canonical citation and confidence rules. The application rechecks
   returned page identity and canonical citations before returning candidates.

`WorkstationClients(profile=...)` rejects empty/default profiles and imports the
SDK lazily. The host must have the optional AWS extra installed before an
authorized live run. No package or credential setup is performed automatically.
Runtime supplies an `AWSClients` implementation whose `client(service, region,
timeout)` uses designated execution-role credentials and matching endpoints;
the same STS checks apply. Injection does not authorize a different role.

## Reviewed access policy

`BedrockAccessPolicy` is strict `bedrock-access-v1` operator configuration, not
model input. Required values include exact account, assumed-role name, region,
model ID/kind, approved regions/foundation-model IDs, Converse capability-record
digest, timeout and token ceiling. The only supported API is `converse`.
Use explicit separate booleans for cross-region/global routing. No wildcard
regions, speculative model fallback or default credentials are supplied.

The capability record must pin the actual model/API documentation reviewed by the
operator; its digest alone is not proof of access. Live metadata verifies profile
status/type/identity and every model/destination. Metadata/capability checks do
not establish successful invocation, Chinese extraction accuracy or billing.
Actual permissions/access are finally exercised only by the approved bounded
smoke. The CLI dry run does not call STS or these metadata APIs.

Production source credentials, privacy/export validation, capability record,
approved model/routing/data/budget and later live acceptance must all be supplied
before using the assembly with real clients. Phase 2 does not implement a second
privacy processor or document storage resolver to replace those dependencies.

## Errors and remaining work

Public boundary errors contain static codes with suppressed raw exception chains.
Provider responses, local paths, prompts and source text are never copied into
operational errors. Direct access to private helpers is unsupported; they are
retained for the trusted assembly and low-level synthetic regression tests.
Legacy explanation and raw Textract factories/operations fail closed as well.

The authorized result uses `PageOutcome`; the private legacy helper remains for
success-only regression/assembly consumers. Shared run limits, nullable usage
and complete image validation are described in the Phase 3 execution document.
These controls do not claim hard billing limits or SDK thread cancellation.
Phase 1 wire schemas are unchanged. Linux remains the runtime baseline.

## Phase 2 verification (2026-09-10)

- Authorized assembly/preflight, existing extraction core and Phase 1 contracts:
  126 passed. Tests include every profile destination, second-destination/model
  rejection, explicit profiles, identity/region/modality checks, zero-call source
  denials, closed legacy entrypoints, original-metadata canaries, real synthetic
  PDF byte rendering, and safe dry-run/CLI errors.
- Other relevant CLI/reviewer/source-purpose/service/integration regressions:
  55 passed, 20 existing Windows/sandbox failures. Four fail at multiprocessing
  pipe permissions, thirteen at the POSIX reviewer gate, one in an unconditional
  `os.getuid` test operation, and two on POSIX absolute-path fixtures. No assertions
  were weakened to make these pass. Phase 0 contains the outside-sandbox diagnosis.
- Ruff and format checks passed. Linux-target mypy passed for 81 source files;
  Windows mypy retains three existing POSIX API errors in local approval.
- Full submission checks passed with the configured identity and functional
  branch, zero outgoing commits. Source and legacy schema checks remain intact.

Ignored `artifacts/issue-21-p2/validation.json` records commands and actual exit
codes with separate logs. The process removed inherited AWS variables, disabled
metadata lookup and configured nonexistent SDK credential/config paths. No real
AWS calls, installs, commits, pushes or remote mutations were made. These focused
offline checks are not full CI or Linux runtime/live integration evidence.

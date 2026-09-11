# Dependency security and connection ownership

This record covers the local repair scope. Final integration, wheel, image,
browser and hosted checks must be bound to the frozen candidate commit. Earlier
image and npm counts are historical and are not current release scan results.

## SQLite ownership

SQLite connection context managers commit or roll back a transaction but do not
close the connection. The affected test helpers now use
`with closing(sqlite3.connect(...)) as connection, connection:`. Contexts exit in
reverse order, so transaction completion still precedes closing. SQL, mutation
scenarios and assertions are unchanged.

The changes cover `test_document_transfer`, `test_integrated_publication`,
`test_integrated_workflow`, `test_local_runtime_authority`,
`test_privacy_document_sink`, `test_sqlite_publication` and
`test_sqlite_review_store`: 34 allocation sites in seven modules. The existing
`test_document_storage_lifecycle` commit/rollback regressions remain intact.

An allocation-tracking pytest fixture using real `sqlite3.Connection` instances
reproduced 40 failing teardown checks in the four initially inspected modules.
After repair, the seven modules passed 261 tests without an unclosed tracked
connection. They also passed with pytest 9.0.3, httpx2 and pytest-cov enabled.
The tracker and full commands/logs are local evidence under
`artifacts/resource-hardening/`; these are focused results, not a full-suite claim.

See the [Python transaction/connection distinction](https://docs.python.org/3/library/sqlite3.html#how-to-use-the-connection-context-manager).

## Dependencies and licenses

- The ARM64 runtime lock moves PyMuPDF from 1.26.0 to 1.28.2, matching the
  document implementation already exercised in the development environment.
  The documents and dev extras now require at least 1.28.2.
- Build-only pip moves from 26.2 to 26.2.1. Both runtime Dockerfiles continue to
  preserve pip's complete notices before uninstalling the installer. All
  application metadata and notices remain installed.
- Development pytest moves from the vulnerable 8.x range to `>=9.0.3,<10`.
  [GHSA-6w46-j5rx-g56g](https://github.com/advisories/GHSA-6w46-j5rx-g56g)
  identifies 9.0.3 as the first fixed version. This is a development environment
  finding; pytest is absent from the production runtime lock.
- The dev extra declares httpx2, the supported Starlette TestClient transport.
  Existing application and network tests that use httpx retain that dependency.

All 33 runtime/build wheels were downloaded from public PyPI with
`--require-hashes --only-binary=:all:` for CPython 3.12 / Linux ARM64, and every
wheel contains license or notice files. PyMuPDF's licensing requirements still
apply; this update does not change or grant a distribution license. No bundled
native-library coverage is inferred from Python package advisories alone.

Reproduce the target dependency verification:

```sh
.venv/bin/python -m pip download --require-hashes --only-binary=:all: \
  --index-url https://pypi.org/simple \
  --platform manylinux_2_28_aarch64 --platform manylinux2014_aarch64 \
  --python-version 3.12 --implementation cp --abi cp312 \
  --dest artifacts/security/runtime-wheels \
  -r infra/runtime/requirements.lock -r infra/runtime/build-tooling.lock
.venv/bin/python -m pip check
```

## Residual upstream findings

As checked on 2026-09-11, the official `python:3.12-slim-trixie` Linux ARM64
manifest still resolves to
`sha256:3949e4271b0a3ff82afac7306764c313dcc8edeeb89c0376a3c2ac6007c66b1d`.
There is no newer manifest behind that stable tag to adopt at this checkpoint.
Both deployment Dockerfiles still require an explicit digest-pinned base.

Debian's stable trixie and bookworm packages remain listed as affected by
[CVE-2026-13221](https://security-tracker.debian.org/tracker/CVE-2026-13221),
[CVE-2026-42496](https://security-tracker.debian.org/tracker/CVE-2026-42496) and
[CVE-2026-8376](https://security-tracker.debian.org/tracker/CVE-2026-8376).
The tracker lists fixed packages in testing/unstable. Mixing those repositories
into the stable runtime is not a validated compatible patch. The 32-bit scope
described for CVE-2026-8376 is an impact distinction, not permission to suppress
the scanner finding. Obtain a compatible vendor fix or independently review a
patched base, rebuild, execute the ARM64 acceptance scenarios and rescan before
clearing the image security gate.

The focused patched-environment tests retain six dependency deprecation warnings:
PyMuPDF 1.28.2's SWIG types lack `__module__`, and Starlette 1.6.0 uses the deprecated
AnyIO BlockingPortal alias. Those are the latest released packages checked at
this checkpoint. The httpx fallback warning is removed by installing httpx2.
No warning filters, assertion changes or vendored-package edits are used to hide
the remaining warnings. The
[PyMuPDF upstream report](https://github.com/pymupdf/PyMuPDF/issues/3931) identifies
the same SWIG warning family; a closed issue does not establish that the installed
wheel is free of the warning.

## Content-redacted release-input checks

`scripts/scan_secret_exposure.py` performs offline credential-pattern checks over
all reachable Git history, the index, tracked/unignored working files and
explicit release inputs. It inspects ZIP, gzip and tar members in place, including
earlier image layers containing subsequently deleted files. It never extracts
archive paths or follows filesystem symlinks. Missing, oversized or malformed
inputs are marked incomplete and fail the command. Reports contain paths and
aggregate digests, never matched lines, values or credential-validation results.

```sh
.venv/bin/python scripts/scan_secret_exposure.py --repository . \
  --path artifacts/final-release/image-context \
  --path artifacts/final-release/image.tar \
  --path artifacts/final-release/ci-artifacts \
  --path artifacts/final-release/logs \
  --report artifacts/final-release/secret-review.json
```

Provide the actual existing paths for a release. Do not create empty directories
to stand in for missing CI artifacts or logs. A hosted job that never started
has no produced artifact to scan; record that absence separately. Bound the
image tar and build context to the actual final image digest and code commit.

This is a heuristic review aid and does not replace the existing submission gate
or the final vulnerability scanner. `no_pattern_matches` is not proof of secret
absence. A nonzero report can include public SDK examples or private-key format
markers in dependency code; review each exact path against its hash-verified
public wheel. Preserve raw redacted findings and record any adjudication
separately. Never remove license files, SDK models or scanner metadata to clear a
report. No credential provider, secret-store API or cloud service is called.

The final release review must still include fresh dependency and actual image
scans with all severities, no ignore list and unchanged failure thresholds.
Keep production, build-only and development findings distinct, and retain the
full advisory evidence for unresolved items.

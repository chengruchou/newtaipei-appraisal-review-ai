# Local competition data review

Source: environment rules 20260722, page 1 general rule 2; see
[ADR 0042](adr/0042-competition-data-admission.md) for the exact source digest.
This procedure creates no real approval during automated validation.

1. Keep the original document, measurements, computation results, encrypted
   mapping and restored PDF local. Do not upload an original for cloud OCR or
   upload first and redact later.
2. Establish origin from evidence. A document derived from a real case remains
   a real-case derivative after renaming, value scaling or redaction. The current
   competition policy blocks that origin. Create demonstrations from scratch.
3. Enumerate every actual outbound part and its full content hash. Inspect PDF
   streams and raster pages as well as native text. Include names, metadata,
   extracted JSON, prompts, human text and logs when those will be transmitted.
4. Record each of the thirteen categories as present, absent or unknown for
   every part. Keep inspection evidence local. Uncertain or uninspected content
   stays unknown, even when format/label detectors found nothing.
5. An authorized independent reviewer checks the precise envelope and provenance.
   The deployment operator supplies the trusted policy/record digest and scoped
   reviewer authorization separately from the record. Neither an uploaded record
   nor the export-confirmation button grants this authority.
6. Check expiry and revocation immediately before sending. Changing any content,
   adding a metadata field or building a different model message requires review
   of the new exact envelope. Do not reuse the old record's digest.

The categories are personal data, regulated data, financial information, race or
ethnicity, political views, religious or philosophical views, trade union
membership, genetic data, biometric data or identifiers, sexual orientation or
sex life, health data, payment processing data and malicious code or malware.
Financial classification includes amounts, valuation prices, balances and similar
content, not only ownership or share labels.

Prepare the independent local arithmetic example in an ignored directory:

```sh
mkdir -p artifacts/competition-demo
python scripts/prepare_competition_demo.py --output artifacts/competition-demo/example.json
```

This example intentionally remains `cloud_admission: unapproved`. Its financial
values are invented; whether synthetic financial content is allowed still needs
an organizer clarification. Tests of a synthetic policy fixture are not that
clarification. Existing whole-case goldens separately test the case-review flow.

Coverage lives in `test_competition_data.py` (all thirteen categories across all
ten supported surfaces, exact binding, origin, expiry, revocation and authority)
and `test_competition_export_gate.py` (actual PDF/bridge/C2 rejection, confirmation
revocation and monetary detector candidates with unchanged confidence). Physical
SDK coverage is recorded separately with the shared dispatch tests.

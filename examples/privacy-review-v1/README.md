# Synthetic local privacy consumer requests

These independent examples target `schemas/local-privacy-review-v1.json`:

| File | Definition |
| --- | --- |
| add.json | AddPrivacyRegion |
| remove.json | RemovePrivacyRegion |
| review-page.json | ReviewPrivacyPage |
| confirm-request.json | ConfirmPrivacyReview |

All IDs, reasons and digests are synthetic parsing fixtures, not replayable
transactions, current reviews or human approvals. Send no real local request,
view, source identity or digest to a public/cloud endpoint. Regenerate with
`python scripts/export_privacy_review_contracts.py`.

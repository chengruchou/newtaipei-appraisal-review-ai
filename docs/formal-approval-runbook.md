# Formal report approval - shortest operator path

System-formal means: a person holding the publish permission approved one exact report
version. It claims no statutory certification. All statuses are server-decided; the
page only displays them.

## Approve and download (happy path)

1. Sign in on the workbench and open the case's results page (結果總覽).
2. In 報表與核准: readiness must show 可送核 (N/N 必要項). If it shows 資料待補,
   every blocker lists the missing source key, why, and the corrective action - fix
   those through the normal correction flow first; the panel re-evaluates on refresh.
3. Press 送出核准申請. This pins the current revision, snapshot digest, template
   bundle and the sha256 of each filled workbook - what you approve is exactly what
   will be delivered.
4. A publish-permission holder reviews the shown binding and presses 核准
   (or 退回 with a reason). The decision records the authenticated actor and the
   server time.
5. In 報表匯出: pick 正式, pick Excel (.xlsx) or PDF (.pdf), press 產生並下載.
   Formal downloads carry the 正式（已核准） badge; every artifact's hash equals the
   approved binding hash.

## After a change

Any correction that changes the calculation snapshot makes the old approval useless
for new output: formal export refuses (409) because the fresh workbook hashes no
longer match the approved binding. Re-run steps 2-4 on the new content. History and
already-recorded decisions are never rewritten.

## Withdraw

核准者 presses 撤回 (reason required). From that moment new formal publications
refuse and the content route stops authorizing downloads of that operation's formal
files. Files someone already saved cannot be recalled; the record shows the
withdrawal, its actor, time and reason.

## Statuses

資料待補 → 可送核 → 待核准 → 已核准 / 已退回 → (已撤回 / 已失效-版本已變更)

Draft exports are untouched by all of this and never require an approval.

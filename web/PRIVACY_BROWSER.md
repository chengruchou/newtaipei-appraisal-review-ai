# Local privacy route and real browser rehearsal

`/privacy` mounts the optional privacy workbench independently of cloud sign-in.
It obtains `privacy_bridge_base` from the same frontend server's public
`GET /local-config.json`. Only a numeric loopback HTTP origin is accepted;
credentials, paths, query strings, fragments and hostnames are rejected. The
request carries no credentials and follows no redirect. Configuration failures
leave the bridge connection unavailable. The bridge token is entered separately
and never comes from this public configuration or a URL parameter.

The privacy transport keeps one deadline through headers, body reading, parsing
and validation, including a final wall-clock check if synchronous processing
delays the timer callback. A response completed after that deadline remains an
unknown outcome. Transfers are not retried automatically.

Sanitized previews use the bundled PDF.js renderer rather than the browser's
native PDF plugin. Every page must finish rendering before the explicit approval
checkbox becomes available; a failed page keeps approval blocked. The exact PDF
is hash-checked before its local blob URL is given to the renderer. This addresses
the real Chromium failure where a native PDF iframe never finished loading.

For the isolated rehearsal, `scripts/browser-proxy.mjs` reads `bridge_url` from
the private file named by `PRIVACY_BROWSER_FIXTURE` and publishes only the
validated `privacy_bridge_base`. The private manifest and its token are never
served. The existing `REVIEW_BROWSER_FIXTURE` configures the real review API and
its independently issued session. Both fixture files belong under ignored
artifacts; browser traces, screenshots and video remain disabled.

The private privacy manifest contains `bridge_url`, `origin`, `token`,
`app_path`, and `sources`, each source with an opaque `source_id` and an authored
`add_region` containing `page`, `bbox` and `category`. The real bridge launcher
owns these synthetic source fixtures. After both explicit transfers, the
backend registration callback atomically adds `review_job_id`. It later adds
`completed_job_id` and `restore_result_id` only after actual publication and
live authorization. Updates preserve the original configuration fields.

`e2e-real/privacy-flow.spec.ts` uses actual browser/API traffic without route
mocks. It reviews every rendered source page, adds the configured region,
confirms the latest review digest, verifies sanitized preview bytes, explicitly
approves each payload, and transfers it once. A separate review tab confirms
four current sides through the real response form, fetching the current task
list after each response. The test then checks all published comparison
contexts, downloads and hashes the committed artifact, and restores the
authorized result through the still-connected local privacy tab. It cannot
pass with a missing publication callback or a simulated restoration.

`tests/privacy-route.test.tsx` records the route's failing regression before
implementation and checks its configuration boundary. Those local tests are
separate from the real browser acceptance above. Use `npm run e2e:real --
privacy-flow.spec.ts` with both private fixture environment variables only
after the actual services and publication callback are configured.

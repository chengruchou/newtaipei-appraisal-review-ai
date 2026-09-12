# Cloud runbook: EC2 workbench deployment

Updated 2026-09-13 (overnight round). This runbook covers the live demo
deployment only. Formal business delivery gates (fact/receipt binding, named
approver, calculation conventions) are separate and are NOT satisfied by
anything here.

## Resources

| Resource | Value |
|---|---|
| Account / region | 242971039848 / us-west-2 |
| Instance | `i-07d809310e547668a` (t3.large, x86_64, AL2023, us-west-2c) |
| Elastic IP | 54.212.57.12 (site: http://54.212.57.12/ - HTTP only, no DNS/TLS) |
| Security group | `sg-02df3f970f13d6d0b` - tcp/80 from named /32 sources only |
| Instance role | `appraisal-workbench-runtime` (SSM core + Bedrock pinned + S3 prefixes + SES send) |
| Data volume | host `/srv/workbench` -> container `/app/artifacts` (survives container swaps; DeleteOnTermination=true on the root volume, so instance termination destroys it) |
| Backup | EBS snapshot `snap-0426cb6befccc1d1a` (2026-09-12 ~21:40, pre-update) |
| S3 bucket | `s3bucket-242971039848-us-west-2-an` (deploy/, cases/, acceptance-reference/, runtime-verify/) |
| Container | `workbench`, image `appraisal-workbench:cand-<git sha>` |

## Model route (decided empirically 2026-09-12 night)

- `us.anthropic.claude-sonnet-4-6` is INFERENCE_PROFILE-only and its system
  profile routes to us-east-2, which the venue rules do not permit; the
  workload role deliberately omits us-east-2, so those sends fail closed with
  AccessDenied (observed live). A single-region application profile cannot be
  created for it (no on-demand support), and Claude 3 profiles are
  provider-locked as Legacy for this account.
- Deployed route: `mistral.mistral-large-3-675b-instruct`, ON_DEMAND,
  us-west-2 only, verified from the workload role. The role policy pins
  exactly the permitted us-west-2 foundation-model ARNs.
- Every physical send passes the shared SQLite dispatcher (>=1.2 s between
  sends, SDK retries pinned to 1 attempt) and the TrustedAssemblyAdmission
  (only envelopes the trusted selector itself assembled for synthetic fixture
  cases; real-case content stays undeclared and is refused).

## Container environment

```
REVIEW_MODEL_CLIENT=bedrock            # or synthetic (default) - misconfig refuses startup
REVIEW_MODEL_ID=mistral.mistral-large-3-675b-instruct
REVIEW_MODEL_REGION=us-west-2
REVIEW_EMAIL_LOGIN=ses                 # optional; requires REVIEW_MAIL_FROM
REVIEW_MAIL_FROM=<verified SES identity>
REVIEW_MAIL_REGION=us-west-2
```

SES is in sandbox (200/day, verified recipients only). Verified identity:
the team's test mailbox.

## Update (container swap - keeps /srv/workbench data)

Run with operator credentials; needs docker + buildx locally.

```bash
git -C <worktree> rev-parse --short HEAD   # candidate sha
docker buildx build --platform linux/amd64 -f deploy/Dockerfile \
  --target workbench -t appraisal-workbench:cand-<sha> --load .
docker save appraisal-workbench:cand-<sha> | gzip > /tmp/wb.tgz
aws s3 cp /tmp/wb.tgz s3://s3bucket-242971039848-us-west-2-an/deploy/workbench-cand-<sha>.tgz
URL=$(aws s3 presign s3://s3bucket-242971039848-us-west-2-an/deploy/workbench-cand-<sha>.tgz \
  --expires-in 900 --endpoint-url https://s3.us-west-2.amazonaws.com)
aws ssm send-command --instance-ids i-07d809310e547668a \
  --document-name AWS-RunShellScript --parameters commands='[
    "curl -fsSL '"'$URL'"' -o /tmp/new.tgz",
    "docker load -i /tmp/new.tgz",
    "docker rm -f workbench",
    "docker run -d --name workbench --restart unless-stopped -v /srv/workbench:/app/artifacts -p 80:8080 -e REVIEW_MODEL_CLIENT=bedrock -e REVIEW_MODEL_ID=mistral.mistral-large-3-675b-instruct -e REVIEW_MODEL_REGION=us-west-2 appraisal-workbench:cand-<sha>",
    "sleep 12", "curl -fsS -o /dev/null -w \"%{http_code}\" http://127.0.0.1/"]'
```

## Rollback

Previous images stay loaded on the instance. To roll back:

```bash
aws ssm send-command --instance-ids i-07d809310e547668a --document-name AWS-RunShellScript \
  --parameters commands='["docker rm -f workbench","docker run -d --name workbench --restart unless-stopped -v /srv/workbench:/app/artifacts -p 80:8080 appraisal-workbench:<previous tag>","sleep 10","curl -fsS -o /dev/null -w \"%{http_code}\" http://127.0.0.1/"]'
```

`docker images` on the instance lists available tags. The pre-update data
state is restorable from the EBS snapshot (create volume, attach, copy
`/srv/workbench`).

## Access

- Site sources: SG ingress is per-/32. Add a new operator location with
  `aws ec2 authorize-security-group-ingress --group-id sg-02df3f970f13d6d0b --ip-permissions "IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=<ip>/32}]"`.
  Never open 0.0.0.0/0; 443 stays closed until real TLS exists (no DNS today).
- Management: SSM Session Manager / send-command (agent registered).
- Sign-in: demo fixture token (never expires, full permissions - demo only)
  or email one-time code (limited principal, 8 h expiry, revocable via
  DELETE /v1/session). Email login users create their own cases via intake.

## Verification quickchecks

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://54.212.57.12/          # 200
# authenticated: GET /v1/session, POST /v1/cases, upload material, list;
# draft exports on the demo job; docker logs workbench for the selector line.
```

## Known limits (do not overstate)

- Draft pipeline only; formal submit/approve/download stays server-blocked
  until readiness passes and a named approver decides.
- External candidates require a named human confirmation receipt before any
  adoption; adoption into snapshots/tables is not wired yet.
- Model tool-loop retrieval (B02) is not wired into the deployed executor.
- Single instance, single container, SQLite - no HA claims; venue demo scale.

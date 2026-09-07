# AWS CDK baseline

This stack creates only durable data foundations:

- encrypted, private S3 bucket for source documents;
- encrypted, private S3 bucket for derived results;
- on-demand DynamoDB table for case state.

The complete pipeline is tracked in #9; see docs/architecture.md and
docs/aws-smoke-plan.md. This baseline is not a deployed review service. The
organizer supplies required services; explicit project access, Region, model
capabilities and API limits must still be verified.

```bash
cd infra/cdk
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cdk synth
```

Do not place credentials, account IDs, or fixed globally unique bucket names in
this directory.

## M0 handoff to D

The current Cases table is a baseline definition, not the job/task/outbox/lease
transaction model. Use [service-v1 ports](../../docs/service-contracts.md) and the
[target architecture](../../docs/architecture.md) to design that model, authorized
document resolution, conditional publication and Runtime recovery. M0 local
composition and the merged #19 writer do not deploy these definitions or configure
production S3/Runtime clients. No CDK resource changes are part of M0.

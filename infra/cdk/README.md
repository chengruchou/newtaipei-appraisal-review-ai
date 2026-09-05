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

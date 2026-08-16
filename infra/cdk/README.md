# AWS CDK baseline

This stack creates only durable data foundations:

- encrypted, private S3 bucket for source documents;
- encrypted, private S3 bucket for derived results;
- on-demand DynamoDB table for case state.

Textract, Bedrock, orchestration, API, and UI resources will be added after the
organizer provides the competition account, Region, service quotas, and allowed
model IDs.

```bash
cd infra/cdk
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cdk synth
```

Do not place credentials, account IDs, or fixed globally unique bucket names in
this directory.

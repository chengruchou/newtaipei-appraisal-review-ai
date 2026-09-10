# Runtime infrastructure boundary

Status: proposed for integration review under #30. The integrated decision is
[ADR 0027](../../docs/adr/0027-runtime-composition.md); this file records the
packaging and infrastructure subset.

The scheduler executes a synchronous reconciler using DynamoDB and SQS. It has
no result-store access. A separate synchronous Lambda receives one SQS reference,
invokes the IAM-protected DEFAULT Runtime endpoint, and acknowledges only a
bounded, matching response. Partial failures return to SQS. The Runtime owns
execution, conditional claims, result writes and fencing. Neither a queue
acknowledgement nor a healthy process establishes completed review.

The job table has `pk`/`sk` and the sparse `job-recovery` index. Index results
identify candidates only; conditional base-table writes establish authority.
Versioned results are confined to `results/` in a dedicated bucket. Sanitized
source reads require a version and one operator-approved namespace. Models and
an optional document catalog use exact supplied resource ARNs. Raw originals,
identity mappings and locally restored output are outside this role boundary.

ECR is bootstrapped separately because both Runtime and Lambda need existing
images. Two digest-pinned ARM64 images separate HTTP startup from the Lambda
runtime interface. The private, non-secret execution configuration belongs only
in the HTTP image. It does not create missing providers or activate an executor.
No workstation profile or credentials are copied into either image.

Runtime log groups are service-created. A narrowly scoped, idempotent custom
resource manages retention on the exact DEFAULT group and retains it on deletion.
It avoids a conflicting `AWS::Logs::LogGroup` create and does not grant workload
roles account-wide log discovery or administration.

This is an offline infrastructure and packaging slice. Missing reviewed
revision/human-task/output providers keep the default application unavailable
and the delivery Draft. PUBLIC networking is explicitly sandbox-only; a reviewed
VPC/endpoints/egress policy is a separate prerequisite for a production topology.
No account, region, owner, deployment prefix or image digest has an implicit
deployment value. Enabling triggers requires a deliberate parameter change.

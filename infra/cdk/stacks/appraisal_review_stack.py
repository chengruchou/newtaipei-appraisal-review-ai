from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_s3 as s3,
)
from constructs import Construct


class AppraisalReviewStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)

        input_bucket = s3.Bucket(
            self,
            "InputDocuments",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        result_bucket = s3.Bucket(
            self,
            "ReviewResults",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                # Incomplete uploads have no published object version. Retain
                # all completed versions, including noncurrent ones: manifests
                # pin exact versions, and safe deletion requires checking every
                # committed reference before an explicit cleanup decision.
                s3.LifecycleRule(
                    id="attempt-output-hygiene",
                    abort_incomplete_multipart_upload_after=Duration.days(7),
                )
            ],
        )
        cases_table = dynamodb.Table(
            self,
            "Cases",
            partition_key=dynamodb.Attribute(
                name="case_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )

        CfnOutput(self, "InputBucketName", value=input_bucket.bucket_name)
        CfnOutput(self, "ResultBucketName", value=result_bucket.bucket_name)
        CfnOutput(self, "CasesTableName", value=cases_table.table_name)

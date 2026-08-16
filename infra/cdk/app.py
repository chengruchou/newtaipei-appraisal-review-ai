#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.appraisal_review_stack import AppraisalReviewStack

app = cdk.App()
AppraisalReviewStack(app, "AppraisalReviewStack")
app.synth()

"""AWS service adapters imported only when the `aws` extra is installed."""

from appraisal_review.adapters.aws.bedrock import BedrockExplanationGenerator
from appraisal_review.adapters.aws.textract import TextractDocumentAnalyzer

__all__ = ["BedrockExplanationGenerator", "TextractDocumentAnalyzer"]

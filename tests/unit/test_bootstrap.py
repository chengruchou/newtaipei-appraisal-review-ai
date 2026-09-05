import os
import subprocess
import sys
from dataclasses import replace

import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.synthetic import synthetic_adapters
from appraisal_review.application.bootstrap import ConfigurationError, build_controller
from appraisal_review.config import Settings


def test_required_adapters_mode_and_unknown_configuration() -> None:
    with pytest.raises(ConfigurationError, match="missing_local_adapters"):
        build_controller(Settings(_env_file=None, synthetic_demo=False))
    with pytest.raises(ValidationError):
        Settings(_env_file=None, runtime_mode="typo")
    invalid = Settings(_env_file=None).model_copy(update={"runtime_mode": "typo"})
    with pytest.raises(ConfigurationError, match="invalid_configuration"):
        build_controller(invalid)
    with pytest.raises(ConfigurationError, match="invalid_adapter"):
        build_controller(
            Settings(_env_file=None), adapters=replace(synthetic_adapters(), parser=None)
        )
    with pytest.raises(ConfigurationError, match="adapter_mode_mismatch"):
        build_controller(
            Settings(_env_file=None), adapters=replace(synthetic_adapters(), mode="aws")
        )


def test_aws_never_falls_back_to_fixtures() -> None:
    config = Settings(
        _env_file=None,
        runtime_mode="aws",
        aws_region="test-region",
        bedrock_model_id="test-model",
        input_bucket="test-input",
        result_bucket="test-output",
        cases_table="test-cases",
    )
    with pytest.raises(ConfigurationError, match="missing_aws_adapters"):
        build_controller(config)
    with pytest.raises(ConfigurationError, match="synthetic_aws_forbidden"):
        build_controller(config.model_copy(update={"synthetic_demo": True}))
    with pytest.raises(ConfigurationError, match="adapter_mode_mismatch"):
        build_controller(config, adapters=synthetic_adapters())
    # Explicitly injected doubles test composition only; they are not live AWS adapters.
    supplied = replace(synthetic_adapters(), mode="aws")
    assert build_controller(config, adapters=supplied).parser is supplied.parser


def test_import_and_local_runner_do_not_import_aws_sdk_or_require_credentials() -> None:
    code = """
import sys, runpy
class BlockCloudSDK:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'boto3', 'botocore', 'bedrock_agentcore'}:
            raise AssertionError('Unexpected cloud SDK import')
sys.meta_path.insert(0, BlockCloudSDK())
import appraisal_review.api.app
sys.argv = ['demo', 'needs_review']
runpy.run_module('appraisal_review.demo', run_name='__main__')
"""
    env = {key: value for key, value in os.environ.items() if not key.startswith("AWS_")}
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    )
    assert '"status": "needs_review"' in result.stdout
    assert "SYNTHETIC DEMO" in result.stderr

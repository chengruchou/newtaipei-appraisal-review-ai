ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RUNTIME_MODE=aws \
    SYNTHETIC_DEMO=false
COPY infra/runtime/requirements.lock /tmp/requirements.lock
COPY infra/runtime/build-tooling.lock /tmp/build-tooling.lock
RUN python -c "import sys; assert sys.version_info[:2] == (3, 12)" \
    && python -m pip install --no-cache-dir --require-hashes --only-binary=:all: \
    --index-url https://pypi.org/simple -r /tmp/build-tooling.lock
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: \
    --index-url https://pypi.org/simple \
    --platform manylinux_2_28_aarch64 --platform manylinux2014_aarch64 \
    --python-version 3.12 --implementation cp --abi cp312 \
    --target ${LAMBDA_TASK_ROOT} -r /tmp/requirements.lock
RUN python -c "from importlib.metadata import distribution; import shutil; dist = distribution('pip'); shutil.copytree(dist.locate_file('pip-' + dist.version + '.dist-info/licenses'), '/usr/share/licenses/appraisal-build/pip')" \
    && python -m pip uninstall --yes pip \
    && python -c "import importlib.util; assert importlib.util.find_spec('pip') is None"
COPY src/appraisal_review/ ${LAMBDA_TASK_ROOT}/appraisal_review/
CMD ["appraisal_review.adapters.aws.runtime_jobs.worker_handler"]

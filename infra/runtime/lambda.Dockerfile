ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RUNTIME_MODE=aws \
    SYNTHETIC_DEMO=false
COPY infra/runtime/requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: \
    --platform manylinux2014_aarch64 --python-version 3.11 --implementation cp --abi cp311 \
    --target ${LAMBDA_TASK_ROOT} -r /tmp/requirements.lock
COPY src/appraisal_review/ ${LAMBDA_TASK_ROOT}/appraisal_review/
CMD ["appraisal_review.adapters.aws.runtime_jobs.worker_handler"]

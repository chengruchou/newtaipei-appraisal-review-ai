# syntax=docker/dockerfile:1
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/appraisal/deps:/opt/appraisal/src \
    RUNTIME_MODE=aws \
    SYNTHETIC_DEMO=false \
    REVIEW_RUNTIME_CONFIG=/opt/appraisal/runtime.json
WORKDIR /opt/appraisal
COPY infra/runtime/requirements.lock ./requirements.lock
COPY infra/runtime/build-tooling.lock ./build-tooling.lock
RUN python -c "import sys; assert sys.version_info[:2] == (3, 12)" \
    && python -m pip install --no-cache-dir --require-hashes --only-binary=:all: \
    --index-url https://pypi.org/simple -r build-tooling.lock
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: \
    --index-url https://pypi.org/simple \
    --platform manylinux_2_28_aarch64 --platform manylinux2014_aarch64 \
    --python-version 3.12 --implementation cp --abi cp312 \
    --target /opt/appraisal/deps -r requirements.lock
RUN python -c "from importlib.metadata import distribution; import shutil; dist = distribution('pip'); shutil.copytree(dist.locate_file('pip-' + dist.version + '.dist-info/licenses'), '/usr/share/licenses/appraisal-build/pip')" \
    && python -m pip uninstall --yes pip \
    && python -c "import importlib.util; assert importlib.util.find_spec('pip') is None"
COPY src/appraisal_review/ ./src/appraisal_review/
RUN --mount=type=secret,id=runtime_config,required=true \
    cp /run/secrets/runtime_config /opt/appraisal/runtime.json \
    && chmod 0444 /opt/appraisal/runtime.json
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ping', timeout=2)"
CMD ["python", "-m", "uvicorn", "appraisal_review.adapters.aws.runtime_app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]

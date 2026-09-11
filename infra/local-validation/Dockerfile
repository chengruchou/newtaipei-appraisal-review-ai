# syntax=docker/dockerfile:1
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/appraisal/deps:/opt/appraisal/src \
    AWS_EC2_METADATA_DISABLED=true \
    AWS_CONFIG_FILE=/dev/null \
    AWS_SHARED_CREDENTIALS_FILE=/dev/null
WORKDIR /opt/appraisal
COPY requirements.lock build-tooling.lock ./
RUN python -c "import sys; assert sys.version_info[:2] == (3, 12)" \
    && python -m pip install --no-cache-dir --require-hashes --only-binary=:all: \
    --index-url https://pypi.org/simple -r build-tooling.lock \
    && python -m pip install --no-cache-dir --require-hashes --only-binary=:all: \
    --index-url https://pypi.org/simple --platform manylinux_2_28_aarch64 \
    --platform manylinux2014_aarch64 --python-version 3.12 --implementation cp --abi cp312 \
    --target /opt/appraisal/deps -r requirements.lock
RUN python -c "from importlib.metadata import distribution; import shutil; dist = distribution('pip'); shutil.copytree(dist.locate_file('pip-' + dist.version + '.dist-info/licenses'), '/usr/share/licenses/appraisal-build/pip')" \
    && python -m pip uninstall --yes pip
COPY --chown=10001:10001 src/ ./src/
COPY --chown=10001:10001 web/ ./web/
COPY --chown=10001:10001 server.py ./local_validation_server.py
RUN groupadd --gid 10001 appraisal \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
    --shell /usr/sbin/nologin appraisal \
    && install -d -o 10001 -g 10001 -m 0700 /state
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["python", "/opt/appraisal/local_validation_server.py"]
# Missing an explicit mode and Origin must fail rather than prepare a demo silently.
CMD []

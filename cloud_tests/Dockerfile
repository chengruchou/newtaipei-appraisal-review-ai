FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .
COPY cloud_tests/__init__.py cloud_tests/runtime.py ./cloud_tests/
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ping', timeout=2)"
CMD ["python", "-m", "uvicorn", "cloud_tests.runtime:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

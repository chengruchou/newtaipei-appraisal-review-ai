#!/bin/sh
# Run the repository checks in order and report every outcome, including failures.
set -u

failures=0
summary=""

step() {
    name="$1"
    shift
    echo ""
    echo "=== $name ==="
    if "$@"; then
        summary="${summary}PASS  ${name}\n"
    else
        summary="${summary}FAIL  ${name}\n"
        failures=$((failures + 1))
    fi
}

step "ruff check" ruff check .
step "ruff format --check" ruff format --check .
step "mypy src" mypy src
# Test modules cross-import `tests.unit.*`, which needs the checkout root on
# sys.path; the console script does not put it there, `python -m pytest` does.
step "pytest" python -m pytest --cov=appraisal_review --cov-config=pyproject.toml \
    --cov-report=term-missing
step "reviewed full-case goldens" python scripts/generate_goldens.py
step "cloud_tests" python -m pytest cloud_tests
step "synthetic API HTTP smoke" python scripts/http_smoke.py
step "local service smoke" python scripts/local_service_smoke.py
step "CloudFormation templates" cfn-lint \
    cloud_tests/image-stack.json cloud_tests/runtime-stack.json infra/documents/stack.json

echo ""
echo "=== summary ==="
printf "%b" "$summary"
if [ "$failures" -ne 0 ]; then
    echo "$failures step(s) failed."
    exit 1
fi
echo "All steps passed. Synthetic checks do not establish real-case accuracy."

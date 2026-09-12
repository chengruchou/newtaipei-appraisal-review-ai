#!/bin/sh
# Prepare the disposable synthetic demo, or serve the existing local HTTP factory.
set -eu

directory="${APPRAISAL_DEMO_DIRECTORY:-/app/artifacts/local-demo}"
config="${APPRAISAL_DEMO_CONFIG:-config.json}"

case "${1:-serve}" in
fixture)
    if [ -f "$directory/config.json" ] && [ "${APPRAISAL_DEMO_RESET:-0}" != "1" ]; then
        echo "Synthetic demo already present in $directory; set APPRAISAL_DEMO_RESET=1 to recreate."
        exit 0
    fi
    # The generator requires a fresh directory and only ever writes synthetic material.
    case "$directory" in
    / | /app | /app/artifacts)
        echo "Refusing to reset $directory; point APPRAISAL_DEMO_DIRECTORY at a demo path." >&2
        exit 1
        ;;
    esac
    rm -rf "$directory"
    exec python /app/scripts/local_service_fixture.py --directory "$directory"
    ;;
serve)
    if [ ! -f "$directory/$config" ]; then
        echo "Missing $directory/$config. Run the fixture service before the reviewer." >&2
        exit 1
    fi
    APPRAISAL_LOCAL_CONFIG="$directory/$config"
    export APPRAISAL_LOCAL_CONFIG
    exec python -m uvicorn appraisal_review.local_service:app_from_environment \
        --factory --host 0.0.0.0 --port "${PORT:-8000}" --workers 1
    ;;
*)
    exec "$@"
    ;;
esac

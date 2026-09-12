#!/bin/sh
# Serve the synthetic workbench API on container loopback, with the built frontend
# in front of it. The API is never published outside this container.
set -eu

directory="${APPRAISAL_WORKBENCH_DIRECTORY:-/app/artifacts/workbench}"
port="${APPRAISAL_WORKBENCH_PORT:-8765}"
launcher="/app/scripts/run_local_workbench.py"

prepare() {
    # Mutation scenarios consume tasks, so durable state is reopened, never reset here.
    if [ -f "$directory/fixture.json" ]; then
        echo "Reopening the existing workbench in $directory."
    else
        echo "Preparing a new synthetic workbench in $directory."
        python "$launcher" --directory "$directory" --port "$port" --prepare
    fi
}

case "${1:-serve}" in
prepare)
    prepare
    ;;
fixture)
    # The private manifest holds a session token; printing it is an explicit request.
    cat "$directory/fixture.json"
    ;;
serve)
    prepare
    python "$launcher" --directory "$directory" --port "$port" --serve &
    api=$!
    trap 'kill "$api" 2>/dev/null || true' INT TERM
    ready=0
    i=0
    while [ "$i" -lt 120 ]; do
        if ! kill -0 "$api" 2>/dev/null; then
            echo "The workbench API exited during startup." >&2
            wait "$api" || true
            exit 1
        fi
        if python /app/deploy/probe.py "127.0.0.1:$port"; then
            ready=1
            break
        fi
        i=$((i + 1))
        sleep 1
    done
    if [ "$ready" -ne 1 ]; then
        echo "The workbench API did not answer on 127.0.0.1:$port." >&2
        kill "$api" 2>/dev/null || true
        exit 1
    fi
    echo "Workbench API ready on container loopback; serving the frontend on 8080."
    nginx -c /etc/nginx/nginx.conf -g 'daemon off;' &
    web=$!
    # Either process failing must stop the container rather than serve half a system.
    # POSIX sh has no `wait -n`, so watch both and exit as soon as one is gone.
    while kill -0 "$api" 2>/dev/null && kill -0 "$web" 2>/dev/null; do
        sleep 2
    done
    echo "A workbench process exited; stopping the container." >&2
    kill "$api" "$web" 2>/dev/null || true
    wait "$api" 2>/dev/null || true
    wait "$web" 2>/dev/null || true
    exit 1
    ;;
*)
    exec "$@"
    ;;
esac

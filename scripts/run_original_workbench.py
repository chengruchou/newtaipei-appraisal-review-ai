"""Start the controlled local-original workbench; no AWS or model calls."""

import argparse
import asyncio
import json
import secrets
from pathlib import Path

import uvicorn

from appraisal_review.adapters.local.original_preview import create_original_preview
from appraisal_review.adapters.local.original_workbench import prepare_workbench, private_state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--material", type=Path, required=True)
    parser.add_argument("--data-directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--session-hours", type=float, default=4)
    parser.add_argument("--preview-port", type=int, default=8769)
    parser.add_argument("--web-origin", default="http://127.0.0.1:4176")
    args = parser.parse_args()
    if not 1024 <= args.preview_port <= 65535 or args.preview_port == args.port:
        parser.error("Preview requires a distinct bounded loopback port")

    async def serve() -> None:
        app = await prepare_workbench(
            manifest_path=args.manifest,
            material_path=args.material,
            data_directory=args.data_directory,
            port=args.port,
            session_hours=args.session_hours,
        )
        session = json.loads((args.data_directory / "session.json").read_text())
        configured_job = json.loads((args.data_directory / "configured-job.json").read_text())
        browser_configuration = {
            "api_base_url": f"http://127.0.0.1:{args.port}",
            "session_token": session["token"],
            "mode": "local_original",
            "configured_job_id": configured_job["job_id"],
        }
        browser_path = args.data_directory / "browser-config.json"
        if not browser_path.exists():
            private_state(browser_path, browser_configuration)
        if (
            browser_path.is_symlink()
            or browser_path.stat().st_mode & 0o077
            or json.loads(browser_path.read_text()) != browser_configuration
        ):
            raise ValueError("Private browser configuration differs; use a new runtime directory")
        pairing = args.data_directory / "preview-pairing.json"
        if not pairing.exists():
            private_state(pairing, {"token": secrets.token_urlsafe(48)})
        if pairing.is_symlink() or pairing.stat().st_mode & 0o077:
            raise ValueError("Preview pairing configuration must be private")
        preview = create_original_preview(
            authority=f"127.0.0.1:{args.preview_port}",
            origin=args.web_origin,
            pairing_token=json.loads(pairing.read_text())["token"],
            directory=app.state.local_original_directory,
            documents=app.state.local_original_documents,
        )
        servers = [
            uvicorn.Server(
                uvicorn.Config(application, host="127.0.0.1", port=port, access_log=False)
            )
            for application, port in [(app, args.port), (preview, args.preview_port)]
        ]
        await asyncio.gather(*(server.serve() for server in servers))

    asyncio.run(serve())


if __name__ == "__main__":
    main()

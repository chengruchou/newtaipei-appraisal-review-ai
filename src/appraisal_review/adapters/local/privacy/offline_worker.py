"""Python network denial before worker imports; OS isolation remains separate."""

from __future__ import annotations

import runpy
import socket
import sys


def install_network_guard() -> None:
    """Deny Python network/DNS operations, including imports that try downloading.

    Not a native-code sandbox. Linux acceptance runs the whole process tree in
    a network namespace, including external OCR executables.
    """

    def deny(event: str, args: tuple[object, ...]) -> None:
        # Linux asyncio uses an already-connected anonymous AF_UNIX socketpair.
        # Creating an unconnected local socket is harmless; bind/connect remain
        # denied, including connections to filesystem sockets and proxy services.
        if (
            event == "socket.__new__"
            and len(args) > 1
            and args[1] == getattr(socket, "AF_UNIX", -1)
        ):
            return
        if event.startswith("socket."):
            raise PermissionError("Local privacy network access denied")

    sys.addaudithook(deny)


def main() -> None:
    targets = {
        "appraisal_review.adapters.local.privacy.pdf_worker",
        "appraisal_review.adapters.local.privacy.sanitize_worker",
        "appraisal_review.adapters.local.privacy.refill_worker",
    }
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in targets:
            raise ValueError("Unsupported worker")
        target = sys.argv[1]
        install_network_guard()
        sys.argv = [target]
        runpy.run_module(target, run_name="__main__", alter_sys=True)
    except Exception:
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()

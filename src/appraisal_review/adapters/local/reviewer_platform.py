"""The private local reviewer store requires Linux/macOS POSIX primitives."""

import importlib
import os
import sys


class UnsupportedReviewerPlatform(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "unsupported_reviewer_platform: local reviewer operations require Linux or macOS "
            "with POSIX identity and private file permissions; use a Linux environment."
        )


def require_reviewer_platform() -> None:
    if sys.platform not in {"linux", "darwin"} or not callable(getattr(os, "getuid", None)):
        raise UnsupportedReviewerPlatform
    try:
        importlib.import_module("pwd")
    except ImportError as error:
        raise UnsupportedReviewerPlatform from error

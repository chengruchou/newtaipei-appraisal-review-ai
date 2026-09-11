#!/usr/bin/env python3
"""Write a local, from-scratch arithmetic example. Does not issue admission records."""

import argparse
import hashlib
import json
from pathlib import Path

from appraisal_review.adapters.local.competition_demo import synthetic_financial_example


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    example = synthetic_financial_example()
    raw = (json.dumps(example, indent=2, sort_keys=True) + "\n").encode()
    # Exclusive creation preserves any previous example or unrelated file.
    with args.output.open("xb") as stream:
        stream.write(raw)
    print(json.dumps({"sha256": hashlib.sha256(raw).hexdigest(), "cloud_admission": "unapproved"}))


if __name__ == "__main__":
    main()

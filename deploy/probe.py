"""Report whether the loopback workbench API is answering its authenticated plane.

An unauthenticated /v1/ request is expected to be refused, which proves the service
is up without holding or printing any session token.
"""

import sys
import urllib.error
import urllib.request


def main() -> int:
    authority = sys.argv[1]
    request = urllib.request.Request(
        f"http://{authority}/v1/review-jobs/probe", headers={"Host": authority}
    )
    try:
        urllib.request.urlopen(request, timeout=2)
    except urllib.error.HTTPError as error:
        return 0 if error.code in {401, 403, 404, 422} else 1
    except OSError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

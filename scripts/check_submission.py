"""Check pending text and Git metadata for prohibited attribution before publishing."""

import argparse
import re
import subprocess
from pathlib import Path

PATTERNS = [
    re.compile(
        r"(?:co-authored-by|author|committer)\s*:\s*[^\n]*(?:codex|claude|chatgpt|copilot|openai|anthropic)",
        re.I,
    ),
    re.compile(
        r"(?:generated|assisted|authored|created|powered)\s+(?:by|with)\s+(?:openai\s+|anthropic\s+)?(?:codex|claude|chatgpt|copilot|AI)\b",
        re.I,
    ),
]


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def check(label: str, text: str) -> None:
    if any(pattern.search(text) for pattern in PATTERNS):
        raise SystemExit(f"Prohibited attribution detected in {label}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--publication-file", type=Path)
    args = parser.parse_args()
    expected = f"{git('config', 'user.name')} <{git('config', 'user.email')}>"
    for key in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        identity = git("var", key)
        if not identity.startswith(expected + " "):
            raise SystemExit(f"{key} differs from configured identity")
        check(key, identity)
    commits = git("rev-list", f"{args.base}..HEAD").splitlines()
    for commit in commits:
        check(commit, git("show", "-s", "--format=fuller", commit))
    names = git("ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    for name in names:
        path = Path(name)
        if path.is_file():
            try:
                check(name, path.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                raise SystemExit(f"Manual binary metadata inspection required: {name}") from None
    check("staged diff", git("diff", "--cached"))
    if args.publication_file:
        check(str(args.publication_file), args.publication_file.read_text(encoding="utf-8"))
    print(
        f"Attribution check passed: {len(names)} files, {len(commits)} commits; "
        "configured Git identity."
    )
    print("Necessary technical names and policy text retained; no history rewritten.")


if __name__ == "__main__":
    main()

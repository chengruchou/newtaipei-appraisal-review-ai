"""Inspect publication text, complete snapshots, Git identities and new branch names."""

import argparse
import ast
import re
import subprocess
import unicodedata
from pathlib import Path

# Tokens identify attribution recipients, not arbitrary occurrences of the letters a/i.
TOOL_NAMES = (
    r"ai|artificial intelligence|codex|claude|chatgpt|copilot|gpt(?:[0-9]+(?:\.[0-9]+)?)?"
    r"|gemini|openai|anthropic|deepseek|grok|qwen|llama|perplexity|codeium|tabnine|windsurf"
)
TOOL = re.compile(rf"(?<![a-z0-9])(?:{TOOL_NAMES})(?![a-z0-9])", re.I)
ROLE = re.compile(
    r"\b(?:authors?|committers?|commit|maintainers?|contributors?|collaborators?|"
    r"co[\s-]*authors?|co[\s-]*authored[\s-]*by|signed[\s-]*off[\s-]*by|sign[\s-]*off|"
    r"generator|generated[\s-]*by|assisted[\s-]*by)\b\s*[:=]\s*([^\n]+)",
    re.I,
)
CREDIT = re.compile(
    r"\b(?:(?:written|authored|generated|created|developed|coded|implemented|assisted|"
    r"powered|reviewed|contributed|produced|built|prepared|co[\s-]*written|co[\s-]*developed|"
    r"signed[\s-]*off|co[\s-]*authored|collaborated)[\s-]+(?:by|with|using)|"
    r"in collaboration with|with (?:help|assistance|support) from|thanks to)\b"
    r"(?:\s*[:=-]\s*|\s+)([^\n]+)",
    re.I,
)
BADGE = re.compile(r"!?\[[^\]\n]*\]\([^\)\n]*(?:shields\.io|badge)[^\)\n]*\)", re.I)
HTML_CREDIT = re.compile(
    r"\b(?:name|property)\s*=\s*(?:author|generator)\s+content\s*=\s*([^>\n]+)", re.I
)
POLICY_EXAMPLE = re.compile(r"^\s*(?:- )?Prohibited example \(must fail\): `[^`\n]+`\s*$", re.I)


def git_bytes(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def git(*args: str) -> str:
    return git_bytes(*args).decode("utf-8").strip()


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(
        str.maketrans("\u2010\u2011\u2013\u2014", "----")
    )


def check_branch(name: str) -> None:
    # Check raw segments and CamelCase/acronym word boundaries, without substring matches.
    words = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", words)
    if not name or TOOL.search(normalize(name)) or TOOL.search(normalize(words)):
        raise SystemExit(f"Prohibited attribution marker in branch: {name}")


def tool_recipient(credit: str) -> bool:
    # An employer's email domain is not an author's name; retain the local identity.
    credit = re.sub(r"([\w.+-]+)@[\w.-]+", r"\1", credit)
    return TOOL.search(credit) is not None


def example_spans(text: str, suffix: str) -> list[tuple[int, int]]:
    """Exempt only explicitly labelled examples, never a directory or a whole file."""
    spans = []
    offset = 0
    for line in text.splitlines(keepends=True):
        if POLICY_EXAMPLE.fullmatch(line.rstrip("\n")):
            spans.append((offset, offset + len(line)))
        offset += len(line)
    if suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return spans
        lines = text.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "prohibited_example"
                and len(node.args) == 1
                and not node.keywords
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                literal = node.args[0]
                assert literal.end_lineno is not None and literal.end_col_offset is not None
                # AST columns are UTF-8 byte offsets, whereas spans use characters.
                start = offsets[literal.lineno - 1] + len(
                    lines[literal.lineno - 1].encode()[: literal.col_offset].decode()
                )
                end = offsets[literal.end_lineno - 1] + len(
                    lines[literal.end_lineno - 1].encode()[: literal.end_col_offset].decode()
                )
                spans.append((start, end))
    return spans


def check(label: str, text: str, *, suffix: str = "", examples: bool = True) -> None:
    if examples:
        for start, end in sorted(example_spans(text, suffix), reverse=True):
            mask = "".join("\n" if char == "\n" else " " for char in text[start:end])
            text = text[:start] + mask + text[end:]
    text = normalize(text)
    # Metadata keys and Markdown emphasis must not hide credit labels.
    plain = re.sub(r"[`*_\"']", "", text)
    for pattern in (ROLE, CREDIT, HTML_CREDIT):
        if any(tool_recipient(match.group(1)) for match in pattern.finditer(plain)):
            raise SystemExit(f"Prohibited attribution detected in {label}")
    if any(TOOL.search(match.group()) for match in BADGE.finditer(text)):
        raise SystemExit(f"Prohibited attribution badge detected in {label}")
    if re.search(r"🤖[^\n]*", text) and any(
        TOOL.search(match.group()) for match in re.finditer(r"🤖[^\n]*", text)
    ):
        raise SystemExit(f"Prohibited attribution footer detected in {label}")


def check_data(label: str, data: bytes, suffix: str) -> None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SystemExit(f"Manual binary metadata inspection required: {label}") from None
    if "\x00" in text:
        raise SystemExit(f"Manual binary metadata inspection required: {label}")
    check(label, text, suffix=suffix)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD", help="Exact outgoing commit/ref to inspect")
    parser.add_argument("--branch", action="append", default=[], help="Additional new/pushed ref")
    parser.add_argument("--publication-file", type=Path)
    args = parser.parse_args()
    current = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"], capture_output=True, text=True
    )
    branches = args.branch + ([current.stdout.strip()] if current.returncode == 0 else [])
    if not branches:
        raise SystemExit("Detached HEAD: specify the intended publication branch with --branch")
    for branch in branches:
        check_branch(branch)
    expected = f"{git('config', 'user.name')} <{git('config', 'user.email')}>"
    for key in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        identity = git("var", key)
        if not identity.startswith(expected + " "):
            raise SystemExit(f"{key} differs from configured identity")
        check(key, f"Author: {identity}", examples=False)

    commits = git("rev-list", f"{args.base}..{args.head}").splitlines()
    inspected: set[tuple[str, str]] = set()

    def blob(label: str, oid: str, name: str) -> None:
        suffix = Path(name).suffix
        if (oid, suffix) not in inspected:
            check_data(label, git_bytes("cat-file", "blob", oid), suffix)
            inspected.add((oid, suffix))

    # Inspect every outgoing commit's full content, including files later removed/replaced.
    # Always inspect head as well, even if it is already reachable from base.
    for commit in dict.fromkeys([*commits, git("rev-parse", args.head)]):
        check(commit, git("show", "-s", "--format=fuller", commit), examples=False)
        for record in git_bytes("ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
            if not record:
                continue
            metadata, path = record.decode().split("\t", 1)
            _, kind, oid = metadata.split()
            if kind != "blob":
                raise SystemExit(f"Manual nested repository inspection required: {commit}:{path}")
            blob(f"commit {commit}:{path}", oid, path)

    # Read complete index blobs: staged bad text cannot hide behind an unstaged cleanup.
    for record in git_bytes("ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        metadata, name = record.decode().split("\t", 1)
        mode, oid, stage = metadata.split()
        if stage != "0" or mode == "160000":
            raise SystemExit(f"Unresolved or nested index entry: {name}")
        blob(f"index:{name}", oid, name)
    names = git_bytes("ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")
    for raw_name in names:
        if not raw_name:
            continue
        name = raw_name.decode()
        path = Path(name)
        if path.is_symlink():
            check_data(name, str(path.readlink()).encode(), path.suffix)
        elif path.is_file():
            check_data(name, path.read_bytes(), path.suffix)
    if args.publication_file:
        check_data(str(args.publication_file), args.publication_file.read_bytes(), ".md")
    print(
        f"Attribution and branch checks passed: {len(inspected)} blobs, "
        f"{len(commits)} outgoing commits; configured Git identity."
    )
    print("Full commit snapshots, index, working files and requested publication inspected.")
    print("Necessary technical names and policy examples retained; no history rewritten.")


if __name__ == "__main__":
    main()

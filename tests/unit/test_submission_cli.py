"""Run the actual publication gate against isolated, explicitly synthetic Git fixtures."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts/check_submission.py"


def prohibited_example(value: str) -> str:
    """An explicit negative-test literal; the submitted fixture is not an attribution."""
    return value


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    def command(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, env=git_env(), check=True, capture_output=True)

    command("init", "-b", "main")
    command("config", "user.name", "Test Reviewer")
    command("config", "user.email", "reviewer@example.test")
    command("config", "commit.gpgsign", "false")
    (tmp_path / "README.md").write_text("Synthetic repository for publication gate tests.\n")
    command("add", "README.md")
    command("commit", "-m", "Initialize synthetic fixture")
    return tmp_path


def git_env(**overrides: str) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_", "GIT_CONFIG_"))
    }
    return dict(env, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", **overrides)


def git(repository: Path, *args: str, **env: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repository, env=git_env(**env), text=True, stderr=subprocess.PIPE
    ).strip()


def cli(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--base", "HEAD", *args],
        cwd=repository,
        env=git_env(),
        capture_output=True,
        text=True,
    )


def rejected(result: subprocess.CompletedProcess[str], location: str) -> None:
    assert result.returncode != 0, result.stdout
    assert "passed" not in result.stdout.lower()
    assert location in result.stderr, result.stderr


@pytest.mark.parametrize(
    "credit",
    [
        prohibited_example("Signed-off-by: Codex <tool@example.test>"),
        prohibited_example("sIgNeD-oFf-By: Claude"),
        prohibited_example("Written by ChatGPT"),
        prohibited_example("WRITTEN-BY: Copilot"),
        prohibited_example("Author: GPT-5"),
        prohibited_example("Co-authored-by: Gemini"),
        prohibited_example("Committer: OpenAI"),
        prohibited_example("Collaborator: Anthropic"),
        prohibited_example("Contributors: human, AI assistant"),
        prohibited_example("Generated with Claude"),
        prohibited_example("Assisted by Codex"),
        prohibited_example("Developed using Copilot"),
        prohibited_example("Implemented with ChatGPT"),
        prohibited_example("Built by Gemini"),
        prohibited_example("In collaboration with Claude"),
        prohibited_example("Collaborated with Claude"),
        prohibited_example("With assistance from GPT"),
        prohibited_example("Co-written by AI"),
        prohibited_example('"__author__" = "Codex"'),
        prohibited_example('{"generator": "ChatGPT"}'),
        prohibited_example('<meta name="author" content="Copilot">'),
        prohibited_example("**Signed\u2011off\u2011by:** [Copilot](https://example.test/tool)"),
        prohibited_example("![AI assisted](https://img.shields.io/badge/AI-assisted-blue)"),
        prohibited_example("🤖 [Codex](https://example.test/tool)"),
    ],
)
def test_publication_file_rejects_attribution(repository: Path, credit: str) -> None:
    draft = repository / ".git/publication.md"
    draft.write_text(credit + "\n")
    rejected(cli(repository, "--publication-file", str(draft)), "publication.md")


def test_legitimate_signatures_technical_names_and_license_are_preserved(repository: Path) -> None:
    text = (
        "Signed-off-by: Test Reviewer <reviewer@example.test>\n"
        "Signed-off-by: Test Reviewer <reviewer@openai.com>\n"
        "Written by Test Reviewer\n"
        "Use Bedrock, Claude and GPT models behind adapters. OpenAI API is a technical name.\n"
        "Copyright 2026 OpenAI. SPDX-License-Identifier: MIT\n"
        "Do not add AI authorship, sign-off or collaboration credits.\n"
    )
    (repository / "LICENSE").write_text(text)
    (repository / "pattern.py").write_text(
        'pattern = r"(?:co-authored-by|author|committer)\\s*:[^\\n]*(?:codex|claude)"\n'
    )
    draft = repository / ".git/publication.md"
    draft.write_text(text)
    git(repository, "add", "LICENSE")
    git(
        repository,
        "commit",
        "-m",
        "Document technical models",
        "-m",
        "Signed-off-by: Test Reviewer <reviewer@example.test>",
    )
    result = cli(repository, "--publication-file", str(draft))
    assert result.returncode == 0, result.stderr
    assert "checks passed" in result.stdout


def test_policy_and_test_examples_are_narrow_not_directory_exemptions(repository: Path) -> None:
    credit = prohibited_example("Written by Codex")
    docs = repository / "docs"
    tests = repository / "tests"
    docs.mkdir()
    tests.mkdir()
    policy = docs / "policy.md"
    policy.write_text("Prohibited example (must fail): `" + credit + "`\n")
    test = tests / "test_fixture.py"
    test.write_text("value = prohibited_example(" + repr(credit) + ")\n")
    assert cli(repository).returncode == 0
    for path in (policy, test):
        original = path.read_text()
        path.write_text(original + "\n# " + credit + "\n")
        rejected(cli(repository), str(path.relative_to(repository)))
        path.write_text(original)
    # Merely quoting the signature, or marking the previous line, is not an exemption.
    policy.write_text("Prohibited example (must fail):\n> " + credit + "\n")
    rejected(cli(repository), "docs/policy.md")


def test_staged_violation_cannot_hide_behind_unstaged_cleanup(repository: Path) -> None:
    path = repository / "README.md"
    path.write_text(prohibited_example("Signed-off-by: Claude") + "\n")
    git(repository, "add", "README.md")
    path.write_text("Clean working copy, but index still contains the prohibited signature.\n")
    rejected(cli(repository), "index:README.md")


@pytest.mark.parametrize("identity", ["message", "author", "committer"])
def test_outgoing_commit_metadata_rejects_credits_and_tool_identities(
    repository: Path, identity: str
) -> None:
    base = git(repository, "rev-parse", "HEAD")
    env = {}
    message = "Synthetic metadata test"
    if identity == "message":
        message += "\n\n" + prohibited_example("Signed-off-by: ChatGPT")
    else:
        prefix = f"GIT_{identity.upper()}"
        env = {f"{prefix}_NAME": "Copilot", f"{prefix}_EMAIL": "tool@example.test"}
    git(repository, "commit", "--allow-empty", "-m", message, **env)
    rejected(cli(repository, "--base", base), git(repository, "rev-parse", "HEAD"))


@pytest.mark.parametrize("later_change", ["remove", "replace"])
def test_earlier_outgoing_content_is_checked_after_cleanup(
    repository: Path, later_change: str
) -> None:
    base = git(repository, "rev-parse", "HEAD")
    path = repository / "footer.md"
    path.write_text(prohibited_example("Written by Gemini") + "\n")
    git(repository, "add", "footer.md")
    git(repository, "commit", "-m", "Add synthetic negative fixture")
    bad = git(repository, "rev-parse", "HEAD")
    if later_change == "remove":
        path.unlink()
    else:
        path.write_text("Replacement content.\n")
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "Remove synthetic negative fixture")
    rejected(cli(repository, "--base", base), f"commit {bad}:footer.md")


@pytest.mark.parametrize(
    "branch",
    [
        "fix/ai-entry",
        "codex/entry",
        "feat/CLAUDE",
        "test/chatgpt",
        "fix/copilot",
        "feat/gpt-5",
        "fix/gemini",
        "docs/openai",
        "test/anthropic",
        "fix/useGPTClient",
    ],
)
def test_branch_markers_are_rejected(repository: Path, branch: str) -> None:
    git(repository, "switch", "-c", branch)
    rejected(cli(repository), "branch:")


@pytest.mark.parametrize(
    "branch",
    [
        "main",
        "fix/domain-validation",
        "feat/maintain-api",
        "feat/shared-pdf-contract",
        "feat/member-a-entrypoint",
        "test/runtime-smoke",
    ],
)
def test_legitimate_branch_words_are_allowed(repository: Path, branch: str) -> None:
    if branch != "main":
        git(repository, "switch", "-c", branch)
    result = cli(repository)
    assert result.returncode == 0, result.stderr


def test_explicit_push_branch_is_checked_even_when_checkout_is_valid(repository: Path) -> None:
    rejected(cli(repository, "--branch", "fix/Copilot-helper"), "branch:")
    git(repository, "checkout", "--detach")
    rejected(cli(repository), "Detached HEAD")
    assert cli(repository, "--branch", "feat/member-a-entrypoint").returncode == 0

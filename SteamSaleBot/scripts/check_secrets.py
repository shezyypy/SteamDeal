"""Fail closed when tracked source files contain common credentials.

This intentionally scans only files returned by ``git ls-files``.  It never
reads the untracked local ``.env`` file, which keeps the CI check useful
without exposing developer secrets to logs.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


_SECRET_PATTERNS = (
    ("Telegram bot token", re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")),
    ("private key", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|github_pat)_[A-Za-z0-9_]{20,}\b")),
    (
        "credential assignment",
        re.compile(
            r"(?im)^\s*(?:BOT_TOKEN|STEAM_API_KEY|DATA_ENCRYPTION_KEY|ADMIN_SECRET|POSTGRES_PASSWORD)"
            r"\s*=\s*(?!$|#|change_me\b|your[_-]?(?:token|key|password)\b)[^\s#]{8,}"
        ),
    ),
)

_SKIP_SUFFIXES = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".rar"}


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str | bytes]:
    """Run Git for this repository without changing global safe-directory settings."""
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo_root}", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
    )


def find_secret_matches(text: str, path: str = "") -> list[str]:
    """Return human-readable match labels without returning secret values."""
    matches = [label for label, pattern in _SECRET_PATTERNS if pattern.search(text)]
    if Path(path).name == ".env.example":
        # The example is allowed to contain intentionally empty placeholders.
        matches = [match for match in matches if match != "credential assignment"]
    return matches


def tracked_files(repo_root: Path) -> list[Path]:
    try:
        result = _git(repo_root, "ls-files", "-z")
    except subprocess.CalledProcessError:
        # Local checkouts can place this project inside another Git repository
        # owned by a different Windows account.  In that case scan this project
        # directory only; CI uses the tracked-file path above.
        ignored = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "backups"}
        return [
            path
            for path in repo_root.rglob("*")
            if path.is_file() and not any(part in ignored for part in path.parts) and path.name != ".env"
        ]
    return [repo_root / item for item in result.stdout.decode().split("\0") if item]


def main() -> int:
    root = Path(
        subprocess.run(
            ["git", "-c", "safe.directory=*", "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    findings: list[tuple[str, str]] = []
    for path in tracked_files(root):
        if path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in find_secret_matches(text, str(path)):
            findings.append((str(path.relative_to(root)), match))

    if findings:
        print("Potential credentials found in tracked files:")
        for path, label in findings:
            print(f"- {path}: {label}")
        return 1
    print("Secret scan passed: no known credential patterns in tracked files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Fail when repository-visible files contain private keys or populated runtime secrets."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?m)^(?:ASI_ONE_API_KEY|AGENTVERSE_API_KEY|[A-Z_]*AGENT_SEED)[ \t]*="
    r"[ \t]*([^\s#]+)"
)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")
API_TOKEN = re.compile(r"\bsk_[A-Za-z0-9_-]{32,}\b")


def repository_files() -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(ROOT / value for value in result.stdout.splitlines() if value)


def findings() -> list[str]:
    detected: list[str] = []
    for path in repository_files():
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(ROOT)
        if PRIVATE_KEY.search(content):
            detected.append(f"{relative}: private key material")
        if API_TOKEN.search(content):
            detected.append(f"{relative}: API token")
        for match in SENSITIVE_ASSIGNMENT.finditer(content):
            detected.append(
                f"{relative}:{content.count(chr(10), 0, match.start()) + 1}: secret value"
            )
    return detected


def main() -> None:
    detected = findings()
    if detected:
        raise SystemExit("repository secret check failed:\n" + "\n".join(detected))
    print("repository secret check passed")


if __name__ == "__main__":
    main()

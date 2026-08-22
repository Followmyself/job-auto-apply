import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
WINDOWS_USER_PATH_RE = re.compile(r"[A-Z]:[/\\]Users[/\\]", re.I)
GITHUB_OWNER_RE = re.compile(r"https?://github\.com/([A-Za-z0-9-]+)/", re.I)


def tracked_text_files():
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = ROOT / raw_path.decode("utf-8")
        try:
            yield path, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def test_tracked_files_contain_no_personal_identifiers():
    violations = []
    for path, text in tracked_text_files():
        if EMAIL_RE.search(text) or WINDOWS_USER_PATH_RE.search(text):
            violations.append(path.relative_to(ROOT).as_posix())
    assert not violations, f"personal identifiers found in tracked files: {violations}"


def test_repository_docs_do_not_hardcode_owner_name():
    violations = []
    for path, text in tracked_text_files():
        owners = GITHUB_OWNER_RE.findall(text)
        if any(owner.lower() != "your-github-username" for owner in owners):
            violations.append(path.relative_to(ROOT).as_posix())
    assert not violations, f"repository owner is hardcoded in tracked files: {violations}"

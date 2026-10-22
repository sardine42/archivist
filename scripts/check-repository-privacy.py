#!/usr/bin/env python3
"""Check the Git index or a commit snapshot without reading workspace data.

Run with the project environment: uv run python scripts/check-repository-privacy.py
Use --ref HEAD for a commit and --message-file PATH for a proposed commit message.
This check covers explicit privacy hazards, not arbitrary personal prose/domains.
"""

from __future__ import annotations

import argparse
from pathlib import PurePosixPath
import re
import subprocess
import sys


RUNTIME_ROOTS = {
    "archive", "_site", "logs", "tmp", ".venv", "venv", "env",
    ".ssh", ".aws", ".gnupg",
}
LOCAL_FILES = {
    ".paper-list.json", ".paper-reading-progress.json", ".ds_store",
    "credentials.json", "credentials.yaml", "credentials.yml",
    "secrets.json", "secrets.yaml", "secrets.yml",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    ".git-credentials", ".netrc",
}
PATTERNS = (
    ("user home absolute path", re.compile(
        r"(?:/(?:Users|home)/[^\s/\\\"'`<>]+|[A-Za-z]:[/\\]Users[/\\][^/\\\s\"'`<>]+)"
        r"(?=[/\\\s\"'`<>]|$)", re.IGNORECASE,
    )),
    ("conversation URL", re.compile(
        r"(?:https?://(?:claude\.ai/(?:chat|code|share)|"
        r"(?:chatgpt\.com|chat\.openai\.com)/(?:c|share)|"
        r"codex\.openai\.com/(?:c|chat|tasks?))|codex://threads)/[A-Za-z0-9_-]+",
        re.IGNORECASE,
    )),
    ("conversation identifier", re.compile(
        r"\b(?:session|thread|conversation)_[A-Za-z0-9][A-Za-z0-9_-]{11,}\b|"
        r"\b(?:session|thread|conversation)[_-]?id[\"']?\s*[:=]\s*[\"']?"
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        re.IGNORECASE,
    )),
    ("private key", re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
    )),
    ("access token", re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
        r"sk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{20,}|"
        r"xox[baprs]-[A-Za-z0-9-]{15,}|AKIA[A-Z0-9]{16})\b"
    )),
    ("Lark user identifier", re.compile(r"\bou_[A-Za-z0-9]{20,}\b")),
)


def git(*args: str, root: str | None = None) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE)


def forbidden_path(path: str) -> bool:
    parts = PurePosixPath(path.lower()).parts
    name = parts[-1]
    example = name.endswith(".example")
    return (
        parts[0] in RUNTIME_ROOTS
        or "__pycache__" in parts
        or name in LOCAL_FILES
        or name.endswith((".log", ".pem", ".key", ".p12", ".pfx"))
        or ((name == "config.local" or name.startswith("config.local.")) and not example)
        or ((name == ".env" or name.startswith(".env.")) and not example)
        or path.lower().startswith((".codex/sessions/", ".claude/projects/"))
        or path.lower() in {
            ".codex/history.jsonl", ".codex/auth.json", ".codex/scheduled_tasks.json",
            ".claude/settings.local.json", ".claude/scheduled_tasks.json",
        }
        or (parts[0] == ".codex" and ".local." in name)
    )


def text_issues(label: str, content: str) -> list[str]:
    issues = []
    for reason, pattern in PATTERNS:
        for match in pattern.finditer(content):
            # Explicit placeholder identifiers are safe in documentation.
            value = match.group().removeprefix("ou_")
            if reason in {"Lark user identifier", "conversation identifier"}:
                suffix = value.rsplit("_", 1)[-1]
                if suffix and set(suffix.lower()) <= {"x", "0"}:
                    continue
            line = content.count("\n", 0, match.start()) + 1
            issues.append(f"{label}:{line}: {reason}")
    return issues


def config_issues(path: str, content: str) -> list[str]:
    if path not in {"config.yaml", "config.local.yaml.example"}:
        return []
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required; run this script with uv run python") from exc
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        return [f"{path}: invalid YAML; personal configuration fields cannot be checked"]
    if not isinstance(data, dict):
        return [f"{path}: expected a configuration mapping"]
    issues = []
    for section, key in (("site", "base_url"), ("deploy", "host"), ("lark", "notify_user_id")):
        values = data.get(section, {})
        if not isinstance(values, dict):
            issues.append(f"{path}: {section} must be a mapping")
        elif values.get(key) not in (None, ""):
            issues.append(f"{path}: {section}.{key} must be empty in public configuration")
    deploy = data.get("deploy", {})
    if isinstance(deploy, dict) and deploy.get("extra_hosts") not in (None, []):
        issues.append(f"{path}: deploy.extra_hosts must be empty in public configuration")
    project = data.get("project", {})
    if not isinstance(project, dict):
        issues.append(f"{path}: project must be a mapping")
    else:
        archive = project.get("archive_dir", "archive")
        if not isinstance(archive, str) or not archive or archive.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z]:", archive):
            issues.append(f"{path}: project.archive_dir must be a relative path")
    return issues


def snapshot(ref: str | None, root: str) -> list[tuple[str, str]]:
    if ref:
        commit = git("rev-parse", "--verify", "--end-of-options", ref + "^{commit}", root=root).decode().strip()
        records = git("ls-tree", "-rz", "--full-tree", commit, root=root).split(b"\0")
    else:
        records = git("ls-files", "--stage", "-z", root=root).split(b"\0")
    files = []
    for record in records:
        if not record:
            continue
        metadata, path_bytes = record.split(b"\t", 1)
        fields = metadata.decode("ascii").split()
        path = path_bytes.decode("utf-8", errors="surrogateescape")
        if ref:
            _, kind, oid = fields
            if kind != "blob":
                continue
        else:
            mode, oid, stage = fields
            if stage != "0":
                raise RuntimeError("resolve index conflicts before checking privacy")
            if mode == "160000":
                continue
        files.append((path, oid))
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", help="check a commit snapshot instead of the Git index")
    parser.add_argument("--message-file", help="also check this proposed commit message file")
    args = parser.parse_args()
    try:
        root = git("rev-parse", "--show-toplevel").decode().strip()
        files = snapshot(args.ref, root)
        issues = []
        for path, oid in files:
            if forbidden_path(path):
                issues.append(f"{path}: private or generated file must not be tracked")
                continue
            blob = git("cat-file", "blob", oid, root=root)
            if b"\0" in blob:
                continue
            content = blob.decode("utf-8", errors="replace")
            issues.extend(text_issues(path, content))
            issues.extend(config_issues(path, content))
        if args.message_file:
            with open(args.message_file, encoding="utf-8") as message:
                issues.extend(text_issues("commit message", message.read()))
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        # Do not echo file contents, Git stderr or configuration values.
        print(f"Privacy check could not complete: {type(exc).__name__}", file=sys.stderr)
        return 2
    if issues:
        print("Privacy check failed:", file=sys.stderr)
        for issue in issues:
            print(f"  {issue}", file=sys.stderr)
        return 1
    print(f"Privacy check passed ({args.ref or 'index'}; {len(files)} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

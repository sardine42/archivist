"""Push digest markdown to Feishu/Lark and pin the message.

Single entry point: ``push_digest_to_lark(period_type, digest_id)``.
Centralizes the Lark CLI invocation that was previously hand-rolled in
multiple cron scripts and ad-hoc Python snippets.

Bot-mode delivery requires a bot app authorized with ``lark-cli auth login``.
Use a bot when tenant policy restricts the ``im:message.send_as_user`` scope.

Target user open_id and site base URL come from config.yaml.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from archivist.config import DIGESTS_DIR, get_lark_user_id, get_site_base_url


class LarkPushError(RuntimeError):
    pass


def _digest_md_path(period_type: str, digest_id: str, year: int = 2026) -> Path:
    return DIGESTS_DIR / str(year) / period_type / f"{digest_id}.md"


def _absolutize_links(body: str) -> str:
    """Convert relative /reading/... links to absolute URLs."""
    base = get_site_base_url()
    if not base:
        return body
    return re.sub(r"\]\(/reading/", f"]({base}/reading/", body)


def _send_message(body: str, *, markdown: bool = True) -> str:
    """Send a message to the user via lark-cli (bot identity)."""
    user_id = get_lark_user_id()
    if not user_id:
        raise LarkPushError("lark.notify_user_id not configured in config.yaml")
    flag = "--markdown" if markdown else "--text"
    proc = subprocess.run(
        [
            "lark-cli", "im", "+messages-send",
            "--as", "bot",
            "--user-id", user_id,
            flag, body,
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise LarkPushError(
            f"lark-cli +messages-send exited {proc.returncode}: {proc.stderr[:500]}"
        )
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise LarkPushError(f"lark-cli stdout not JSON: {e}\n{proc.stdout[:500]}") from e
    if not out.get("ok"):
        raise LarkPushError(f"lark send failed: {out!r}")
    return out["data"]["message_id"]


def _pin_message(message_id: str) -> None:
    proc = subprocess.run(
        [
            "lark-cli", "im", "pins", "create",
            "--as", "bot",
            "--data", json.dumps({"message_id": message_id}),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise LarkPushError(
            f"lark-cli pins create exited {proc.returncode}: {proc.stderr[:500]}"
        )
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return  # Pin returns plain success in some versions
    if out.get("code", 0) != 0 and out.get("msg") != "success":
        raise LarkPushError(f"lark pin failed: {out!r}")


def push_digest_to_lark(period_type: str, digest_id: str, year: int = 2026) -> str:
    """Read the digest markdown, send it to Lark, pin it, return message_id.

    Args:
        period_type: "daily" / "weekly" / "monthly"
        digest_id:   "2026-04-10" / "2026-W15" / "2026-04"
        year:        archive year, defaults to 2026
    """
    md_file = _digest_md_path(period_type, digest_id, year)
    if not md_file.exists():
        raise LarkPushError(f"digest md not found: {md_file}")

    body = _absolutize_links(md_file.read_text(encoding="utf-8"))
    base = get_site_base_url()
    if base:
        body += f"\n\n📄 完整日报：{base}/reading/digest/{period_type}/{digest_id}/"

    message_id = _send_message(body)
    _pin_message(message_id)
    return message_id


def send_text_notification(text: str) -> str:
    """Send a plain-text notification to the configured Lark user.

    Used by cron wrappers to report job status (success / lock-skipped / failure).
    Returns the message_id on success.
    """
    return _send_message(text, markdown=False)

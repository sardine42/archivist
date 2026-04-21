"""Thin wrapper around the local `claude -p` CLI.

The digest orchestrator (and any future script that needs LLM judgment)
shells out through this module instead of calling the Anthropic API
directly. Reasons:

- Reuses the user's local auth, settings.json allow-list, and tool budget
- No API keys to manage
- Same backend cost, easier to swap models per call
- Honours `--dangerously-skip-permissions --permission-mode bypassPermissions`
  for cron-friendly execution
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any


class ClaudeRunnerError(RuntimeError):
    """Raised when `claude -p` returns a non-success result or unparsable JSON."""


_CODE_FENCE_RE = re.compile(r"^```(?:json|JSON)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def _strip_md_fence(s: str) -> str:
    """Strip a leading/trailing ```json ... ``` fence if present."""
    s = s.strip()
    m = _CODE_FENCE_RE.match(s)
    if m:
        return m.group(1).strip()
    return s


def run_claude(
    prompt: str,
    *,
    model: str = "sonnet",
    cwd: Path | str | None = None,
    timeout: int = 1800,
) -> str:
    """Invoke `claude -p` and return the model's textual response.

    Always uses ``--output-format json`` so we can inspect the envelope
    (``permission_denials``, ``is_error``) before returning. The caller
    receives the inner ``result`` string with any markdown code fence
    stripped.

    Raises ``ClaudeRunnerError`` if the envelope reports an error or any
    permission denial occurred.
    """
    cmd = [
        "claude",
        "-p",
        prompt,
        "--model",
        model,
        "--dangerously-skip-permissions",
        "--permission-mode",
        "bypassPermissions",
        "--output-format",
        "json",
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise ClaudeRunnerError(
            f"claude -p exited {proc.returncode}\nstderr: {proc.stderr[:500]}"
        )
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ClaudeRunnerError(
            f"claude -p stdout was not JSON: {e}\nfirst 500 chars: {proc.stdout[:500]}"
        ) from e

    if envelope.get("is_error"):
        raise ClaudeRunnerError(
            f"claude -p returned is_error=true: {envelope.get('result', '')[:500]}"
        )
    denials = envelope.get("permission_denials") or []
    if denials:
        raise ClaudeRunnerError(f"claude -p hit permission denials: {denials!r}")

    return _strip_md_fence(envelope.get("result", ""))


def run_claude_json(
    prompt: str,
    *,
    model: str = "sonnet",
    cwd: Path | str | None = None,
    timeout: int = 1800,
    retries: int = 1,
) -> Any:
    """Like ``run_claude`` but parses the response as JSON.

    Retries up to ``retries`` times on JSON parse failure (the model
    occasionally inserts stray prose despite the prompt). Returns the
    parsed structure.
    """
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        text = run_claude(prompt, model=model, cwd=cwd, timeout=timeout)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            last_err = e
            if attempt < retries:
                time.sleep(2)
                continue
            raise ClaudeRunnerError(
                f"claude -p result was not valid JSON after {attempt + 1} tries: {e}\n"
                f"first 500 chars: {text[:500]}"
            ) from e
    # Unreachable
    raise ClaudeRunnerError(f"unreachable: {last_err!r}")

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


def _extract_json_object(text: str) -> str | None:
    """Slice out the first balanced {...} or [...] from text.

    Handles prose before/after the JSON. Tracks string state so braces inside
    string literals don't confuse the depth counter.
    """
    start = -1
    opener = ""
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            opener = ch
            break
    if start < 0:
        return None
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _escape_unescaped_controls_in_strings(text: str) -> str:
    """Replace literal newlines/tabs/CRs inside JSON string literals with their
    escaped forms. Outside string literals, characters are left untouched.
    """
    out: list[str] = []
    in_str = False
    escape = False
    for ch in text:
        if in_str:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == '"':
                out.append(ch)
                in_str = False
                continue
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            out.append(ch)
        else:
            out.append(ch)
            if ch == '"':
                in_str = True
    return "".join(out)


def _try_parse_json(text: str) -> Any | None:
    """Best-effort JSON parse with two salvage steps.

    Returns the parsed value, or ``None`` if every strategy fails.
    The caller is responsible for raising with diagnostic context.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    sliced = _extract_json_object(text)
    if sliced is not None and sliced != text.strip():
        try:
            return json.loads(sliced)
        except json.JSONDecodeError:
            pass

    candidate = sliced if sliced is not None else text
    repaired = _escape_unescaped_controls_in_strings(candidate)
    if repaired != candidate:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    return None


def run_claude(
    prompt: str,
    *,
    model: str = "opus",
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
    model: str = "opus",
    cwd: Path | str | None = None,
    timeout: int = 1800,
    retries: int = 1,
) -> Any:
    """Like ``run_claude`` but parses the response as JSON.

    Retries up to ``retries`` times on JSON parse failure (the model
    occasionally inserts stray prose despite the prompt). Returns the
    parsed structure.
    """
    last_text = ""
    for attempt in range(retries + 1):
        text = run_claude(prompt, model=model, cwd=cwd, timeout=timeout)
        last_text = text
        parsed = _try_parse_json(text)
        if parsed is not None:
            return parsed
        if attempt < retries:
            time.sleep(2)
            continue
        try:
            json.loads(text)  # re-raise to capture the original decode error
        except json.JSONDecodeError as e:
            raise ClaudeRunnerError(
                f"claude -p result was not valid JSON after {attempt + 1} tries: {e}\n"
                f"first 500 chars: {last_text[:500]}"
            ) from e
    # Unreachable
    raise ClaudeRunnerError(
        f"claude -p result unparsable after {retries + 1} tries; "
        f"first 500 chars: {last_text[:500]}"
    )

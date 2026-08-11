"""Thin wrapper around the local ``codex exec`` CLI.

The digest orchestrator (and any future script that needs LLM judgment)
shells out through this module instead of calling the OpenAI API
directly. Reasons:

- Reuses the user's local Codex authentication and repository instructions
- No API keys to manage
- Leaves model selection to the user's Codex default unless explicitly overridden
- Uses an ephemeral, non-interactive workspace-write run with network access
  for cron-friendly execution
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any


class CodexRunnerError(RuntimeError):
    """Raised when ``codex exec`` fails or returns unparsable JSON."""


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


def _escape_unescaped_inner_quotes(text: str) -> str:
    """Escape stray ``"`` characters that appear inside JSON string literals.

    Heuristic: a ``"`` is treated as a string terminator iff the next
    non-whitespace character is one of ``,:]}`` or end-of-text. Otherwise it's
    assumed to be content the model forgot to escape (common when an LLM
    writes Chinese text but quotes a term using ASCII ``"``).
    """
    out: list[str] = []
    in_str = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if escape:
                out.append(ch)
                escape = False
                i += 1
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                i += 1
                continue
            if ch == '"':
                j = i + 1
                while j < n and text[j] in " \t\n\r":
                    j += 1
                if j >= n or text[j] in ",:]}":
                    out.append(ch)
                    in_str = False
                else:
                    out.append("\\")
                    out.append(ch)
                i += 1
                continue
            out.append(ch)
            i += 1
        else:
            out.append(ch)
            if ch == '"':
                in_str = True
            i += 1
    return "".join(out)


def _try_parse_json(text: str) -> Any | None:
    """Best-effort JSON parse with three salvage steps.

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

    quote_repaired = _escape_unescaped_inner_quotes(repaired)
    if quote_repaired != repaired:
        try:
            return json.loads(quote_repaired)
        except json.JSONDecodeError:
            pass

    return None


def run_codex(
    prompt: str,
    *,
    model: str | None = None,
    cwd: Path | str | None = None,
    timeout: int = 1800,
) -> str:
    """Invoke ``codex exec`` and return its final agent message.

    The prompt is passed on stdin so large candidate batches do not hit the
    operating system's command-line length limit. Progress stays on stderr;
    stdout contains only the final message.
    """
    cmd = [
        "codex",
        "exec",
        "--ephemeral",
        "--sandbox",
        "workspace-write",
        "--config",
        'approval_policy="never"',
        "--config",
        "sandbox_workspace_write.network_access=true",
        "--color",
        "never",
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.append("-")
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise CodexRunnerError(
            "codex CLI not found; install it and authenticate before running the digest"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise CodexRunnerError(f"codex exec timed out after {timeout}s") from e
    if proc.returncode != 0:
        raise CodexRunnerError(
            f"codex exec exited {proc.returncode}\nstderr: {proc.stderr[:1000]}"
        )
    return _strip_md_fence(proc.stdout)


def run_codex_json(
    prompt: str,
    *,
    model: str | None = None,
    cwd: Path | str | None = None,
    timeout: int = 1800,
    retries: int = 1,
) -> Any:
    """Like ``run_codex`` but parses the response as JSON.

    Retries up to ``retries`` times on JSON parse failure (the model
    occasionally inserts stray prose despite the prompt). Returns the
    parsed structure.
    """
    last_text = ""
    for attempt in range(retries + 1):
        text = run_codex(prompt, model=model, cwd=cwd, timeout=timeout)
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
            raise CodexRunnerError(
                f"codex exec result was not valid JSON after {attempt + 1} tries: {e}\n"
                f"first 500 chars: {last_text[:500]}"
            ) from e
    # Unreachable
    raise CodexRunnerError(
        f"codex exec result unparsable after {retries + 1} tries; "
        f"first 500 chars: {last_text[:500]}"
    )

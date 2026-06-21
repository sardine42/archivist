"""Notification backend abstraction.

Digest pushes and cron status messages go through a ``Notifier`` instead of
calling a specific channel directly, so the backend can be swapped or disabled
via config (``notify.backend``) without touching the pipeline:

    "lark"  → LarkNotifier  (Feishu via lark-cli; needs lark.notify_user_id)
    "none"  → NullNotifier  (push disabled — no-op)

Consumers (``digest_runner``, the ``archivist notify`` CLI, cron wrappers)
depend only on the ``Notifier`` interface and ``NotifyError``.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from archivist.config import get_lark_user_id, get_notify_backend

log = logging.getLogger("notifier")


class NotifyError(RuntimeError):
    """Raised when a notifier backend fails to deliver a message."""


@runtime_checkable
class Notifier(Protocol):
    name: str

    def send_text(self, text: str) -> str | None:
        """Send a plain-text status message.

        Returns a backend message id, or ``None`` when the backend is
        disabled / not configured (a no-op, not an error).
        """

    def push_digest(self, period_type: str, digest_id: str, *, year: int = 2026) -> str | None:
        """Push a rendered digest (and pin it, where supported).

        Returns a backend message id, or ``None`` when disabled / not
        configured. Raises ``NotifyError`` on delivery failure.
        """


class NullNotifier:
    """Push disabled: every call is a no-op."""

    name = "none"

    def send_text(self, text: str) -> str | None:
        return None

    def push_digest(self, period_type: str, digest_id: str, *, year: int = 2026) -> str | None:
        return None


class LarkNotifier:
    """Feishu/Lark backend via lark-cli.

    Skips silently (returns ``None``) when ``lark.notify_user_id`` is unset;
    raises ``NotifyError`` on a real delivery failure (missing lark-cli, auth,
    network).
    """

    name = "lark"

    def _configured(self) -> bool:
        return bool(get_lark_user_id())

    def send_text(self, text: str) -> str | None:
        if not self._configured():
            return None
        from archivist.services.lark_push import LarkPushError, send_text_notification
        try:
            return send_text_notification(text)
        except LarkPushError as e:
            raise NotifyError(str(e)) from e

    def push_digest(self, period_type: str, digest_id: str, *, year: int = 2026) -> str | None:
        if not self._configured():
            return None
        from archivist.services.lark_push import LarkPushError, push_digest_to_lark
        try:
            return push_digest_to_lark(period_type, digest_id, year)
        except LarkPushError as e:
            raise NotifyError(str(e)) from e


_BACKENDS = {"lark": LarkNotifier, "none": NullNotifier, "": NullNotifier}


def get_notifier() -> Notifier:
    """Return the configured notifier backend (``notify.backend``)."""
    backend = get_notify_backend()
    cls = _BACKENDS.get(backend)
    if cls is None:
        log.warning("unknown notify.backend %r — falling back to 'none'", backend)
        cls = NullNotifier
    return cls()

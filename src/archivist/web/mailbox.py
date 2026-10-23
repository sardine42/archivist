"""Public submit-only mailbox API; inbox management stays on the private CLI."""

from __future__ import annotations

import json
import sqlite3

from flask import Blueprint, Flask, current_app, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from archivist.services.mailbox import (
    REQUEST_LIMIT,
    MailboxError,
    MailboxSettings,
    MailboxStore,
    get_mailbox_settings,
    origin_tuple,
    submission_request_id,
    validate_submission,
)

mailbox_bp = Blueprint("mailbox", __name__, url_prefix="/api/mailbox")


def _settings() -> MailboxSettings:
    settings = current_app.config.get("MAILBOX_SETTINGS")
    if settings is None:
        settings = get_mailbox_settings()
        current_app.config["MAILBOX_SETTINGS"] = settings
    return settings


def _error(error: MailboxError):
    response = jsonify(error={"code": error.code, "message": error.message})
    response.status_code = error.status
    if error.retry_after is not None:
        response.headers["Retry-After"] = str(error.retry_after)
    return response


@mailbox_bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@mailbox_bp.get("/config")
def config():
    settings = _settings()
    return jsonify(enabled=settings.enabled, max_length=settings.max_length,
                   max_messages=settings.max_messages)


@mailbox_bp.post("/messages")
def submit():
    try:
        settings = _settings()
        if not settings.enabled:
            raise MailboxError("unavailable", "信箱暂时不可用，请稍后再来。", 503)
        origin = request.headers.get("Origin", "")
        try:
            allowed = settings.allowed_origins or (settings.public_url or request.host_url,)
            same_origin = origin_tuple(origin) in {origin_tuple(value) for value in allowed}
        except ValueError:
            same_origin = False
        if not same_origin or request.headers.get("Sec-Fetch-Site") == "cross-site":
            raise MailboxError("origin", "请从网站页面提交留言。", 403)
        if request.mimetype != "application/json":
            raise MailboxError("invalid", "留言格式有误，请重新提交。", 415)
        if request.content_length is not None and request.content_length > REQUEST_LIMIT:
            raise MailboxError("too_large", "提交内容过大。", 413)
        raw = request.stream.read(REQUEST_LIMIT + 1)
        if len(raw) > REQUEST_LIMIT:
            raise MailboxError("too_large", "提交内容过大。", 413)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise MailboxError("invalid", "留言格式有误，请重新提交。") from None
        message, page = validate_submission(payload, settings.max_length)
        store = MailboxStore(settings)
        store.submit(message, page, request.remote_addr or "unknown", submission_request_id(payload))
        return jsonify(ok=True), 201
    except MailboxError as error:
        return _error(error)
    except RequestEntityTooLarge:
        return _error(MailboxError("too_large", "提交内容过大。", 413))
    except (sqlite3.Error, OSError):
        # Avoid writing submitted messages or identifying client data into logs.
        current_app.logger.error("Mailbox storage is unavailable")
        return _error(MailboxError("unavailable", "信箱暂时不可用，请稍后再试。", 503))


def register_mailbox(app: Flask, settings: MailboxSettings | None = None) -> None:
    """Attach the public API to an existing Flask application.

    ProxyFix is opt-in. With it enabled, the service must bind only to a private
    socket and the sole trusted reverse proxy must overwrite X-Forwarded-For and
    X-Forwarded-Proto. Client supplied forwarding headers must never pass through.
    """
    settings = settings or get_mailbox_settings()
    app.config["MAILBOX_SETTINGS"] = settings
    if settings.trust_proxy:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0)
    app.register_blueprint(mailbox_bp)


def create_mailbox_app(settings: MailboxSettings | None = None) -> Flask:
    """Small production WSGI app requiring only Flask, PyYAML and stdlib."""
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = REQUEST_LIMIT
    register_mailbox(app, settings)
    return app

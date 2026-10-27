"""Private administrator sessions alongside the anonymous mailbox API."""

from __future__ import annotations

import hmac
import json
import sqlite3
import time
from functools import wraps
from pathlib import Path

from flask import Blueprint, Flask, current_app, g, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from archivist.services.admin_auth import (
    COOKIE_NAME,
    SESSION_LIFETIME,
    AuthRateLimit,
    AuthSettings,
    AuthStore,
    configured_identity,
    get_auth_settings,
    validate_credentials,
    verify_password,
)
from archivist.services.mailbox import REQUEST_LIMIT, MailboxSettings, origin_tuple

PRIVATE_DATA_LIMIT = 8 * 1024 * 1024

auth_bp = Blueprint("admin_auth", __name__)


class AuthRequestError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _settings() -> AuthSettings:
    return current_app.config["AUTH_SETTINGS"]


def _store() -> AuthStore:
    return AuthStore(_settings(), current_app.config.get("AUTH_CLOCK", time.time))


def _origin_required() -> None:
    settings = _settings()
    allowed = settings.allowed_origins or ((settings.public_url,) if settings.public_url else ())
    try:
        same_origin = origin_tuple(request.headers.get("Origin", "")) in {origin_tuple(value) for value in allowed}
    except ValueError:
        same_origin = False
    if not same_origin or request.headers.get("Sec-Fetch-Site") == "cross-site":
        raise AuthRequestError("origin", "请从网站页面操作。", 403)


def _payload(keys: set[str] = frozenset()) -> dict:
    if request.mimetype != "application/json":
        raise AuthRequestError("invalid", "请求格式有误。", 415)
    if request.content_length is not None and request.content_length > REQUEST_LIMIT:
        raise AuthRequestError("too_large", "提交内容过大。", 413)
    raw = request.stream.read(REQUEST_LIMIT + 1)
    if len(raw) > REQUEST_LIMIT:
        raise AuthRequestError("too_large", "提交内容过大。", 413)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise AuthRequestError("invalid", "请求格式有误。") from None
    if not isinstance(value, dict) or set(value) != keys:
        raise AuthRequestError("invalid", "请求格式有误。")
    return value


def _error(code: str, message: str, status: int):
    return jsonify(error={"code": code, "message": message}), status


def _status(admin: dict | None = None):
    enabled = configured_identity(_settings()) is not None
    payload = {"enabled": enabled, "authenticated": bool(enabled and admin)}
    if enabled and admin:
        payload.update(username=admin["username"], csrf_token=admin["csrf_token"])
    return payload


def current_admin() -> dict | None:
    """Get the authenticated administrator, with no development-mode bypass.

    This helper can guard server-rendered private pages. It never creates state
    merely because an anonymous request supplied a cookie.
    """
    if not hasattr(g, "archivist_admin"):
        try:
            g.archivist_admin = _store().session(request.cookies.get(COOKIE_NAME), configured_identity(_settings()))
        except (OSError, sqlite3.Error):
            current_app.logger.error("Administrator session storage is unavailable")
            g.archivist_admin = None
    return g.archivist_admin


def require_admin(view):
    """Guard a Flask view without HTTP Basic authentication or credential prompts."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_admin() is None:
            return _error("unauthorized", "请先登录。", 401)
        return view(*args, **kwargs)
    return wrapped


@auth_bp.app_errorhandler(AuthRequestError)
def request_error(error):
    return _error(error.code, error.message, error.status)


@auth_bp.after_app_request
def protect_auth_response(response):
    if request.path.startswith(("/api/auth/", "/api/admin/")):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.vary.add("Cookie")
        response.headers.pop("WWW-Authenticate", None)
    return response


@auth_bp.post("/api/auth/session")
def session():
    _origin_required()
    _payload()
    return jsonify(_status(current_admin()))


@auth_bp.post("/api/auth/login")
def login():
    _origin_required()
    payload = _payload({"username", "password"})
    settings = _settings()
    identity = configured_identity(settings)
    if identity is None:
        return _error("unavailable", "登录暂时不可用。", 503)
    client = request.remote_addr or "unknown"
    try:
        store = _store()
        store.reserve_attempt(client)
        username, password = payload["username"], payload["password"]
        if not validate_credentials(username, password) or not verify_password(settings, username, password):
            return _error("credentials", "账号或密码不正确。", 401)
        # Re-read the credential fingerprint after verification: a password file
        # changed while verification ran must not receive a stale session.
        verified_identity = configured_identity(settings)
        if verified_identity is None or verified_identity != identity:
            return _error("unavailable", "登录暂时不可用，请重试。", 503)
        token, admin = store.issue_session(identity[0], identity[1], client, request.cookies.get(COOKIE_NAME))
        response = jsonify(_status(admin))
        response.set_cookie(COOKIE_NAME, token, max_age=SESSION_LIFETIME, secure=True,
                            httponly=True, samesite="Lax", path="/")
        return response
    except AuthRateLimit as error:
        response = jsonify(error={"code": "rate_limit", "message": "登录尝试过于频繁，请稍后再试。"})
        response.status_code = 429
        response.headers["Retry-After"] = str(error.retry_after)
        return response
    except (OSError, sqlite3.Error):
        current_app.logger.error("Administrator sign-in is unavailable")
        return _error("unavailable", "登录暂时不可用，请稍后再试。", 503)


@auth_bp.post("/api/auth/logout")
@require_admin
def logout():
    _origin_required()
    _payload()
    admin = current_admin()
    supplied = request.headers.get("X-CSRF-Token", "")
    if (not supplied or len(supplied) > 128
            or not hmac.compare_digest(supplied.encode("utf-8"), admin["csrf_token"].encode("utf-8"))):
        raise AuthRequestError("csrf", "页面状态已变化，请刷新后重试。", 403)
    try:
        _store().revoke(request.cookies.get(COOKIE_NAME))
    except (OSError, sqlite3.Error):
        current_app.logger.error("Administrator sign-out is unavailable")
        return _error("unavailable", "退出暂时不可用，请稍后再试。", 503)
    g.archivist_admin = None
    response = jsonify(_status())
    response.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite="Lax")
    return response


@auth_bp.post("/api/auth/check")
def check():
    # Nginx auth_request uses this cookie-only check as an internal subrequest.
    # It returns neither private data nor CSRF tokens and never mutates state.
    return ("", 204) if current_admin() else ("", 401)


def _read_private_data(path: Path | None) -> dict:
    if path is None:
        raise OSError("Administrator private data is unavailable")
    with path.open("rb") as source:
        raw = source.read(PRIVATE_DATA_LIMIT + 1)
    if len(raw) > PRIVATE_DATA_LIMIT:
        raise ValueError("Administrator private data is invalid")
    value = json.loads(raw)
    if (not isinstance(value, dict) or not isinstance(value.get("papers"), dict)
            or type(value.get("doc_count")) is not int or not 0 <= value["doc_count"] <= 1000000):
        raise ValueError("Administrator private data is invalid")
    papers = {}
    for slug, metadata in value["papers"].items():
        if (not isinstance(slug, str) or not 1 <= len(slug) <= 256
                or not isinstance(metadata, dict)):
            raise ValueError("Administrator private data is invalid")
        rating = metadata.get("rating")
        reason = metadata.get("rating_reason", "")
        if (rating is not None and (type(rating) is not int or not 1 <= rating <= 10)
                or not isinstance(reason, str) or len(reason) > 20000):
            raise ValueError("Administrator private data is invalid")
        papers[slug] = {"rating": rating, "rating_reason": reason}
    return {"papers": papers, "doc_count": value["doc_count"]}


@auth_bp.post("/api/admin/data")
@require_admin
def private_data():
    _origin_required()
    _payload()
    try:
        return jsonify(_read_private_data(_settings().private_data_file))
    except (OSError, ValueError, UnicodeError, RecursionError):
        current_app.logger.error("Administrator private data is unavailable")
        return _error("unavailable", "个人数据暂时不可用，请稍后再试。", 503)


def register_auth(app: Flask, settings: AuthSettings | None = None) -> None:
    app.config["AUTH_SETTINGS"] = settings or get_auth_settings()
    app.register_blueprint(auth_bp)


def create_site_api_app(auth_settings: AuthSettings | None = None,
                        mailbox_settings: MailboxSettings | None = None) -> Flask:
    """Production WSGI service exposing only authentication and mailbox routes."""
    from archivist.web.mailbox import register_mailbox

    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = REQUEST_LIMIT
    register_mailbox(app, mailbox_settings)
    register_auth(app, auth_settings)

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(error):
        return _error("too_large", "提交内容过大。", 413)

    return app

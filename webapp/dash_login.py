"""Login for the dashboard.

One shared username and password, set in the environment (APP_USER,
APP_PASSWORD). Fails closed: if no password is set, the dashboard is open only
to requests from this same computer, and refuses everyone else, so a container
deployed without a password is never public by accident.

    APP_USER         username (default "admin")
    APP_PASSWORD     password -- login is enforced whenever this is set
    APP_SECRET_KEY   signs the session cookie; set it so logins survive a restart
    APP_API_KEY      optional key for machine callers of /api/facebook-leads
                     (send it as "Authorization: Bearer <key>" or "X-API-Key")
    APP_COOKIE_SECURE=1   mark the cookie Secure when served over https
    APP_TRUST_PROXY=1     behind one reverse proxy: use its X-Forwarded-For /
                          -Proto headers, so the lockout counts the real visitor
"""
import hmac
import os
import secrets
import time
from urllib.parse import quote, urlsplit

from flask import Blueprint, abort, jsonify, redirect, render_template, request, session

bp = Blueprint("auth", __name__)

# Pages that need no login. /health is for the container's health check and
# returns nothing but "ok".
_OPEN_PATHS = ("/login", "/health")
_MAX_FAILS = 5
_LOCKOUT_SECONDS = 15 * 60
_fails = {}  # ip -> [timestamps of recent failed logins]


def init_auth(app):
    app.secret_key = os.environ.get("APP_SECRET_KEY") or secrets.token_hex(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("APP_COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    )
    if os.environ.get("APP_TRUST_PROXY") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.register_blueprint(bp)

    @app.before_request
    def _require_login():
        path = request.path
        if path in _OPEN_PATHS or path.startswith("/static/"):
            return None
        if not _password():
            # No password configured: only this computer may use the dashboard.
            # A forwarded-for header means a proxy is relaying someone else.
            if request.remote_addr in ("127.0.0.1", "::1") and "X-Forwarded-For" not in request.headers:
                return None
            return ("Login is not set up on this server. Set APP_PASSWORD.", 503)
        if session.get("user") or _api_key_ok(path):
            return None
        if path.startswith(("/dashboard/status", "/api/")):
            return jsonify({"error": "login required"}), 401
        return redirect("/login?next=" + quote(_safe_next(request.full_path.rstrip("?")), safe="/"))

    @app.after_request
    def _no_cache(resp):
        # Signed-in pages hold lead data: don't let the browser keep them for
        # the back button after sign-out.
        if not request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.context_processor
    def _inject_user():
        return {"auth_user": session.get("user") if _password() else None}


def _password():
    return os.environ.get("APP_PASSWORD", "")


def _api_key_ok(path):
    key = os.environ.get("APP_API_KEY", "")
    if not key or not path.startswith("/api/"):
        return False
    given = request.headers.get("X-API-Key") or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return bool(given) and hmac.compare_digest(given, key)


def _safe_next(target):
    """Only ever send people back to a path on this site."""
    parts = urlsplit(target or "")
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return "/dashboard"
    return target


def _locked(ip):
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < _LOCKOUT_SECONDS]
    if recent:
        _fails[ip] = recent
    else:
        _fails.pop(ip, None)
    return len(recent) >= _MAX_FAILS


def _same(a, b):
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


@bp.route("/health")
def health():
    return "ok"


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not _password():
        abort(404)
    nxt = _safe_next(request.values.get("next", "/dashboard"))
    if request.method == "GET" and session.get("user"):
        return redirect(nxt)
    error = ""
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if _locked(ip):
            error = "Too many wrong attempts. Try again in 15 minutes."
        else:
            user_ok = _same(request.form.get("username", "").strip(), os.environ.get("APP_USER", "admin"))
            pass_ok = _same(request.form.get("password", ""), _password())
            if user_ok and pass_ok:
                _fails.pop(ip, None)
                session.clear()
                session["user"] = os.environ.get("APP_USER", "admin")
                session.permanent = True
                return redirect(nxt)
            _fails.setdefault(ip, []).append(time.time())
            error = "Wrong username or password."
    return render_template("login.html", error=error, next=nxt), (429 if error.startswith("Too many") else 200)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/login")

"""Two-role auth: admin (full access) + viewer (read-only).

Credentials are set via environment variables, not stored in the database.
This keeps things simple and lets you rotate passwords by changing env vars.
"""
import os
import secrets

from fastapi import Request


class NeedsLogin(Exception):
    """Raise from a route to redirect the user to /login."""


class Forbidden(Exception):
    """Raise from a route when the user is logged in but not allowed."""


def _safe_eq(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return secrets.compare_digest(a, b)


def authenticate(username: str, password: str) -> dict | None:
    """Return {'username': ..., 'role': ...} if creds match, else None."""
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("ADMIN_PASSWORD", "")
    viewer_user = os.getenv("VIEWER_USERNAME", "viewer")
    viewer_pass = os.getenv("VIEWER_PASSWORD", "")

    if _safe_eq(username, admin_user) and _safe_eq(password, admin_pass):
        return {"username": admin_user, "role": "admin"}
    if _safe_eq(username, viewer_user) and _safe_eq(password, viewer_pass):
        return {"username": viewer_user, "role": "viewer"}
    return None


def current_user(request: Request) -> dict | None:
    return request.session.get("user")


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise NeedsLogin()
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if user.get("role") != "admin":
        raise Forbidden()
    return user

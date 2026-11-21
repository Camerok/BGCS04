"""Login checks for Flask routes.

The browser signs in with Supabase Auth (publishable key) and sends its access token as
`Authorization: Bearer <token>`. We ask Supabase who that token belongs to, then load the
user's row from `profiles` (role + team). Routes use the decorators below.
"""
from functools import wraps

from flask import g, jsonify, request


def _token():
    header = request.headers.get("Authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else None


def login_required(db_getter, need_profile=True, role=None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            token = _token()
            user = db_getter().get_user(token) if token else None
            if not user or not user.get("id"):
                return jsonify({"error": "Please sign in again."}), 401
            g.user = user
            rows = db_getter().select("profiles", {"id": user["id"]})
            g.profile = rows[0] if rows else None
            if need_profile and not g.profile:
                return jsonify({"error": "Finish setting up your account first.", "needs_onboarding": True}), 403
            if role and (not g.profile or g.profile["role"] != role):
                return jsonify({"error": f"Only a {role} can do that."}), 403
            if need_profile and not g.profile.get("team_id"):
                return jsonify({"error": "Join or create a team first.", "needs_onboarding": True}), 403
            return fn(*args, **kwargs)
        return wrapper
    return deco

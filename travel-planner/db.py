"""Database access. Server-side only.

SupabaseDB talks to Supabase's REST API (PostgREST) with SUPABASE_SERVICE_KEY. That key
bypasses row level security, so it must live only in .env / Render environment variables,
never in frontend code or GitHub. Every route in app.py checks the user's team and role
before it reads or writes, because the database itself trusts this server completely.

MemoryDB has the same interface and keeps everything in Python dicts. Tests use it, and
the app falls back to it (with the bundled catalog) when Supabase isn't configured.
"""
import copy
import os
import uuid
from datetime import datetime, timezone

import requests

TIMEOUT = 15


class DBError(RuntimeError):
    pass


def _matches(row: dict, filters: dict) -> bool:
    for col, want in (filters or {}).items():
        have = row.get(col)
        if isinstance(want, (list, tuple, set)):
            if have not in want:
                return False
        elif have != want:
            return False
    return True


class SupabaseDB:
    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/")
        self.key = key

    # -- helpers -----------------------------------------------------------
    def _headers(self, extra=None, bearer=None):
        h = {"apikey": self.key, "Content-Type": "application/json"}
        if bearer:
            h["Authorization"] = f"Bearer {bearer}"
        elif not self.key.startswith("sb_"):          # legacy service_role JWT also goes in Authorization
            h["Authorization"] = f"Bearer {self.key}"
        h.update(extra or {})
        return h

    @staticmethod
    def _params(filters=None, order=None, columns=None, limit=None):
        p = {}
        if columns:
            p["select"] = columns
        for col, val in (filters or {}).items():
            if isinstance(val, (list, tuple, set)):
                p[col] = "in.(" + ",".join(f'"{v}"' for v in val) + ")"
            elif isinstance(val, bool):
                p[col] = f"eq.{str(val).lower()}"
            elif val is None:
                p[col] = "is.null"
            else:
                p[col] = f"eq.{val}"
        if order:
            p["order"] = order
        if limit:
            p["limit"] = str(limit)
        return p

    def _call(self, method, table, params=None, json=None, prefer="return=representation"):
        try:
            resp = requests.request(method, f"{self.url}/rest/v1/{table}", params=params, json=json,
                                    headers=self._headers({"Prefer": prefer}), timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise DBError(f"Supabase unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise DBError(f"{method} {table} failed ({resp.status_code}): {resp.text[:300]}")
        return resp.json() if resp.content else []

    # -- table API (same shape as MemoryDB) ---------------------------------
    def select(self, table, filters=None, order=None, columns="*", limit=None):
        if filters and any(isinstance(v, (list, tuple, set)) and not v for v in filters.values()):
            return []                                   # "in ()" matches nothing
        return self._call("GET", table, self._params(filters, order, columns, limit))

    def insert(self, table, rows):
        return self._call("POST", table, json=rows)

    def update(self, table, values, filters):
        if not filters:
            raise DBError("refusing to update without filters")
        return self._call("PATCH", table, self._params(filters), json=values)

    def delete(self, table, filters):
        if not filters:
            raise DBError("refusing to delete without filters")
        return self._call("DELETE", table, self._params(filters))

    # -- auth --------------------------------------------------------------
    def get_user(self, access_token: str):
        """Return the Supabase Auth user for a browser session token, or None if it's invalid."""
        try:
            resp = requests.get(f"{self.url}/auth/v1/user", headers=self._headers(bearer=access_token),
                                timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise DBError(f"Supabase unreachable: {exc}") from exc
        return resp.json() if resp.status_code == 200 else None


class MemoryDB:
    """In-memory stand-in with the same methods. Not shared between processes."""
    NO_ID = {"trip_participants", "announcement_reads"}                     # composite primary keys
    NO_CREATED_AT = NO_ID | {"trip_packages"}                               # tables without created_at

    def __init__(self, seed: dict | None = None):
        self.tables = {k: copy.deepcopy(v) for k, v in (seed or {}).items()}
        self.users = {}                                  # access_token -> auth user (tests only)

    def select(self, table, filters=None, order=None, columns="*", limit=None):
        rows = [copy.deepcopy(r) for r in self.tables.get(table, []) if _matches(r, filters)]
        if order:
            col, _, direction = order.partition(".")
            rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=direction == "desc")
        return rows[:limit] if limit else rows

    def insert(self, table, rows):
        rows = rows if isinstance(rows, list) else [rows]
        out = []
        for r in rows:
            r = copy.deepcopy(r)
            if table not in self.NO_ID:
                r.setdefault("id", str(uuid.uuid4()))
            if table not in self.NO_CREATED_AT:
                r.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            self.tables.setdefault(table, []).append(r)
            out.append(copy.deepcopy(r))
        return out

    def update(self, table, values, filters):
        out = []
        for r in self.tables.get(table, []):
            if _matches(r, filters):
                r.update(copy.deepcopy(values))
                out.append(copy.deepcopy(r))
        return out

    def delete(self, table, filters):
        keep, gone = [], []
        for r in self.tables.get(table, []):
            (gone if _matches(r, filters) else keep).append(r)
        self.tables[table] = keep
        return gone

    def get_user(self, access_token):
        return self.users.get(access_token)


def from_env():
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    return SupabaseDB(url, key) if url and key else None

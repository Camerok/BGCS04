"""SupabaseDB builds correct PostgREST requests (no network: requests is faked)."""
import db as dbmod


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._p = status, payload if payload is not None else []
        self.content = b"x"
        self.text = "err"

    def json(self):
        return self._p


def test_select_filters_and_headers(monkeypatch):
    seen = {}

    def fake(method, url, params=None, json=None, headers=None, timeout=None):
        seen.update(method=method, url=url, params=params, headers=headers, json=json)
        return FakeResp(payload=[{"id": 1}])

    monkeypatch.setattr(dbmod.requests, "request", fake)
    d = dbmod.SupabaseDB("https://x.supabase.co/", "sb_secret_abc")
    assert d.select("trips", {"team_id": "t1", "status": ["BOOKED", "PLANNING"], "published": True},
                    order="match_at.asc") == [{"id": 1}]
    assert seen["url"] == "https://x.supabase.co/rest/v1/trips"
    assert seen["params"] == {"select": "*", "team_id": "eq.t1", "status": 'in.("BOOKED","PLANNING")',
                              "published": "eq.true", "order": "match_at.asc"}
    assert seen["headers"]["apikey"] == "sb_secret_abc" and "Authorization" not in seen["headers"]

    dbmod.SupabaseDB("https://x.supabase.co", "eyJlegacy").update("trips", {"status": "BOOKED"}, {"id": "a"})
    assert seen["method"] == "PATCH" and seen["params"] == {"id": "eq.a"}
    assert seen["headers"]["Authorization"] == "Bearer eyJlegacy"


def test_empty_in_filter_short_circuits_and_errors_raise(monkeypatch):
    monkeypatch.setattr(dbmod.requests, "request", lambda *a, **k: FakeResp(400))
    d = dbmod.SupabaseDB("https://x.supabase.co", "k")
    assert d.select("announcement_reads", {"announcement_id": []}) == []
    try:
        d.insert("trips", {"x": 1})
        assert False, "expected DBError"
    except dbmod.DBError as e:
        assert "400" in str(e)


def test_update_without_filter_is_refused():
    d = dbmod.SupabaseDB("https://x.supabase.co", "k")
    for call in (lambda: d.update("trips", {"a": 1}, {}), lambda: d.delete("trips", {})):
        try:
            call()
            assert False
        except dbmod.DBError:
            pass

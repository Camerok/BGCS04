"""End-to-end API flow on the in-memory database, plus a schema check against Supabase."""
import pytest

import app as appmod

# Columns that exist in Supabase (from the migrations). If code writes anything else, Supabase rejects it.
SCHEMA = {
    "teams": {"id", "name", "join_code", "home_name", "home_lat", "home_lng", "owner_id", "created_at"},
    "profiles": {"id", "full_name", "role", "team_id", "diet_tags", "created_at"},
    "trips": {"id", "team_id", "created_by", "opponent", "venue_name", "venue_address", "venue_lat", "venue_lng",
              "match_at", "depart_after", "nights", "budget", "num_players", "num_coaches", "num_staff", "diet_tags",
              "meals", "status", "selected_package_no", "published", "ai_recommendation", "created_at"},
    "trip_packages": {"id", "trip_id", "package_no", "total_cost", "route_km", "detail"},
    "bookings": {"id", "trip_id", "type", "quantity", "check_in", "check_out", "amount", "status", "confirmation_code",
                 "summary", "requested_by", "approved_by", "created_at"},
    "trip_participants": {"trip_id", "player_id", "rsvp", "room_no", "bus_no"},
    "announcements": {"id", "trip_id", "team_id", "author_id", "title", "body", "created_at"},
    "announcement_reads": {"announcement_id", "user_id"},
    "saved_items": {"id", "user_id", "announcement_id", "trip_id", "created_at"},
}
CHECKS = {("trips", "status"): {"DRAFT", "PLANNING", "AWAITING_APPROVAL", "BOOKED", "IN_PROGRESS", "COMPLETED", "CANCELLED"},
          ("bookings", "status"): {"PENDING_APPROVAL", "CONFIRMED", "FAILED", "CANCELLED"},
          ("bookings", "type"): {"hotel", "bus", "meal"},
          ("trip_participants", "rsvp"): {"going", "maybe", "not_going"},
          ("profiles", "role"): {"coach", "player"}}

C, P = {"Authorization": "Bearer demo-coach"}, {"Authorization": "Bearer demo-player"}


@pytest.fixture()
def client(monkeypatch):
    db = appmod.dbmod.MemoryDB()
    planner = appmod.TravelPlanner(db)
    appmod.seed_demo(db, planner)
    monkeypatch.setattr(appmod, "DB", db)
    monkeypatch.setattr(appmod, "planner", planner)
    return appmod.app.test_client(), db


def test_full_trip_flow_and_permissions(client):
    c, db = client
    trip = c.get("/api/trips", headers=C).json[0]
    tid = trip["id"]
    view = c.get(f"/api/trips/{tid}", headers=C).json
    assert view["packages"] and view["ai_recommendation"]["package_no"] in [p["package_no"] for p in view["packages"]]

    # players can't act as coach, and see nothing about lodging before publishing
    assert c.post(f"/api/trips/{tid}/approve", headers=P).status_code == 403
    assert "hotel" not in c.get(f"/api/trips/{tid}", headers=P).json
    assert c.post(f"/api/trips/{tid}/rsvp", json={"rsvp": "going"}, headers=P).status_code == 200

    # publishing before booking is refused
    assert c.post(f"/api/trips/{tid}/publish", json={"title": "x"}, headers=C).status_code == 400

    s = c.post(f"/api/trips/{tid}/select", json={"package_no": 1}, headers=C).json
    assert s["status"] == "AWAITING_APPROVAL"
    assert {b["status"] for b in s["bookings"]} == {"PENDING_APPROVAL"}
    a = c.post(f"/api/trips/{tid}/approve", headers=C).json
    assert a["ok"] and a["trip"]["status"] == "BOOKED"
    assert all(b["confirmation_code"] for b in a["trip"]["bookings"])

    c.post(f"/api/trips/{tid}/rooms", headers=C)
    draft = c.get(f"/api/trips/{tid}/draft", headers=C).json
    assert c.post(f"/api/trips/{tid}/publish", json=draft, headers=C).json["published"]

    pv = c.get(f"/api/trips/{tid}", headers=P).json
    assert pv["hotel"]["name"] and pv["my"]["room_no"] == 1
    assert all("halal" in m["diet_tags"] for m in pv["my_menu"])          # demo player is halal
    text = str(pv)
    for secret in ("total_cost", "budget", "price_per_night", "amount", "costs"):
        assert secret not in text

    feed = c.get("/api/announcements", headers=P).json
    assert feed and not feed[0]["read"]
    c.post(f"/api/announcements/{feed[0]['id']}/read", headers=P)
    assert c.post(f"/api/announcements/{feed[0]['id']}/save", headers=P).json["saved"]
    assert c.get("/api/announcements", headers=P).json[0]["read"]

    assert c.get("/api/trips").status_code == 401
    assert c.get("/api/trips", headers={"Authorization": "Bearer nope"}).status_code == 401

    # every row the app wrote fits the Supabase schema
    for table, rows in db.tables.items():
        for row in rows:
            extra = set(row) - SCHEMA[table]
            assert not extra, f"{table} has columns Supabase doesn't: {extra}"
            for (t, col), allowed in CHECKS.items():
                if t == table and col in row:
                    assert row[col] in allowed, f"{table}.{col}={row[col]!r}"


def test_failed_booking_rolls_back(client):
    c, db = client
    tid = c.get("/api/trips", headers=C).json[0]["id"]
    c.post(f"/api/trips/{tid}/select", json={"package_no": 1}, headers=C)
    pkg = db.select("trip_packages", {"trip_id": tid, "package_no": 1})[0]
    appmod.planner.data.hotels[pkg["detail"]["hotel"]["id"]]["rooms_available"] = 0     # sold out meanwhile
    r = c.post(f"/api/trips/{tid}/approve", headers=C).json
    assert not r["ok"] and "rooms left" in r["error"]
    assert r["trip"]["status"] == "PLANNING"
    assert not [b for b in r["trip"]["bookings"] if b["status"] == "CONFIRMED"]


def test_onboarding_coach_then_player_with_join_code(client):
    c, db = client
    db.users["t-coach"] = {"id": "u1", "email": "c@x"}
    db.users["t-player"] = {"id": "u2", "email": "p@x"}
    hc, hp = {"Authorization": "Bearer t-coach"}, {"Authorization": "Bearer t-player"}
    assert c.get("/api/me", headers=hc).json["profile"] is None
    assert c.get("/api/trips", headers=hc).json["needs_onboarding"]
    r = c.post("/api/onboard", headers=hc, json={"full_name": "Pat", "role": "coach", "team_name": "Rockets",
                                                  "home_lat": 41.6, "home_lng": -83.6})
    assert r.status_code == 200
    code = c.get("/api/me", headers=hc).json["team"]["join_code"]
    assert code.startswith("ROCKET")
    assert c.post("/api/onboard", headers=hp, json={"full_name": "Sam", "role": "player", "join_code": "WRONG"}).status_code == 400
    assert c.post("/api/onboard", headers=hp, json={"full_name": "Sam", "role": "player", "join_code": code.lower(),
                                                     "diet_tags": ["vegan", "bogus"]}).status_code == 200
    me = c.get("/api/me", headers=hp).json
    assert me["profile"]["diet_tags"] == ["vegan"] and "join_code" not in me["team"]


def test_trip_validation(client):
    c, _ = client
    bad = c.post("/api/trips", headers=C, json={"opponent": "", "venue_id": "V002", "match_at": "2026-12-05T12:00"})
    assert bad.status_code == 400
    bad = c.post("/api/trips", headers=C, json={"opponent": "X", "venue_id": "NOPE", "match_at": "2026-12-05T12:00"})
    assert bad.status_code == 400
    ok = c.post("/api/trips", headers=C, json={"opponent": "Toledo", "venue_id": "V002", "match_at": "2026-12-05T12:00",
                                               "nights": 0, "meals": 1})
    assert ok.status_code == 201 and ok.json["packages"][0]["detail"]["hotel"] is None

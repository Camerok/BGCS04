"""Flask entry point. Run locally: python app.py   |   Render: gunicorn app:app

With SUPABASE_URL + SUPABASE_SERVICE_KEY set, data and logins go through Supabase.
Without them the app runs in demo mode: an in-memory database, the bundled catalog and
two one-click demo accounts (coach and player), so anyone can try it with no setup.
"""
import os
import secrets

from dotenv import load_dotenv

load_dotenv()  # must run before the modules below read environment variables

from flask import Flask, g, jsonify, render_template, request  # noqa: E402

import db as dbmod  # noqa: E402
from auth import login_required  # noqa: E402
from demo import seed_demo  # noqa: E402
from travel_planner import PlanError, TravelPlanner  # noqa: E402

DIET_TAGS = ["vegetarian", "vegan", "halal", "gluten_free", "nut_free"]

app = Flask(__name__)
DB = dbmod.from_env()
DEMO = DB is None
if DEMO:
    DB = dbmod.MemoryDB()
planner = TravelPlanner(DB)
if DEMO:
    seed_demo(DB, planner)


def get_db():
    return DB


coach_only = login_required(get_db, role="coach")
player_only = login_required(get_db, role="player")
member = login_required(get_db)


@app.errorhandler(PlanError)
def plan_error(exc):
    return jsonify({"error": str(exc)}), 400


@app.errorhandler(dbmod.DBError)
def db_error(exc):
    return jsonify({"error": f"Database problem: {exc}"}), 502


def body():
    return request.get_json(silent=True) or {}


def my_trip(trip_id):
    return planner.trip(trip_id, g.profile["team_id"])


# -- pages ----------------------------------------------------------------
@app.get("/")
def index():
    config = {"supabaseUrl": os.environ.get("SUPABASE_URL", ""),
              "supabaseKey": os.environ.get("SUPABASE_PUBLISHABLE_KEY", ""),
              "demo": DEMO, "dietTags": DIET_TAGS}
    return render_template("index.html", config=config)


@app.get("/api/health")
def health():
    return jsonify({"database": "memory (demo)" if DEMO else "supabase", "catalog": planner.data.source,
                    "aivana": "configured" if planner.aivana.configured else "local fallback"})


# -- account --------------------------------------------------------------
@app.get("/api/me")
@login_required(get_db, need_profile=False)
def me():
    team = planner.team(g.profile["team_id"]) if g.profile and g.profile.get("team_id") else None
    if team and g.profile["role"] != "coach":
        team = {k: team[k] for k in ("id", "name", "home_name")}
    return jsonify({"user": {"id": g.user["id"], "email": g.user.get("email")}, "profile": g.profile, "team": team})


@app.post("/api/onboard")
@login_required(get_db, need_profile=False)
def onboard():
    b = body()
    if g.profile and g.profile.get("team_id"):
        return jsonify({"error": "Your account is already set up."}), 400
    name = str(b.get("full_name") or "").strip()
    role = b.get("role")
    diet = [t for t in (b.get("diet_tags") or []) if t in DIET_TAGS]
    if not name or role not in ("coach", "player"):
        return jsonify({"error": "Enter your name and choose coach or player."}), 400
    if role == "coach":
        try:
            team_row = {"name": str(b["team_name"]).strip(), "home_name": str(b.get("home_name") or "Home campus"),
                        "home_lat": float(b["home_lat"]), "home_lng": float(b["home_lng"]),
                        "owner_id": g.user["id"], "join_code": new_join_code(str(b["team_name"]))}
        except (KeyError, ValueError, TypeError):
            return jsonify({"error": "Enter a team name and the home campus latitude/longitude."}), 400
        if not team_row["name"]:
            return jsonify({"error": "Enter a team name."}), 400
        team = DB.insert("teams", team_row)[0]
    else:
        code = str(b.get("join_code") or "").strip().upper()
        found = DB.select("teams", {"join_code": code})
        if not found:
            return jsonify({"error": "That join code doesn't match any team. Ask your coach for it."}), 400
        team = found[0]
    profile = {"id": g.user["id"], "full_name": name, "role": role, "team_id": team["id"], "diet_tags": diet}
    if g.profile:
        DB.update("profiles", profile, {"id": g.user["id"]})
    else:
        DB.insert("profiles", profile)
    return jsonify({"ok": True})


@app.patch("/api/me")
@member
def update_me():
    b, values = body(), {}
    if "diet_tags" in b:
        values["diet_tags"] = [t for t in b["diet_tags"] if t in DIET_TAGS]
    if str(b.get("full_name") or "").strip():
        values["full_name"] = b["full_name"].strip()
    if values:
        DB.update("profiles", values, {"id": g.profile["id"]})
    return jsonify({"ok": True})


def new_join_code(team_name: str) -> str:
    prefix = "".join(c for c in team_name.upper() if c.isalpha())[:6] or "TEAM"
    for _ in range(20):
        code = f"{prefix}-{secrets.randbelow(9000) + 1000}"
        if not DB.select("teams", {"join_code": code}):
            return code
    return f"{prefix}-{secrets.token_hex(3).upper()}"


@app.get("/api/team")
@coach_only
def team():
    return jsonify({"team": planner.team(g.profile["team_id"]), "players": planner.team_players(g.profile["team_id"])})


# -- catalog --------------------------------------------------------------
@app.get("/api/venues")
@member
def venues():
    return jsonify(sorted(planner.data.venues.values(), key=lambda v: v["name"]))


@app.get("/api/restaurants/<rid>/menu")
@member
def menu(rid):
    tags = [t for t in request.args.get("diet", "").split(",") if t]
    return jsonify(planner.data.menu(rid, tags))


# -- trips ----------------------------------------------------------------
@app.get("/api/trips")
@member
def trips():
    rows = DB.select("trips", {"team_id": g.profile["team_id"]}, order="match_at.asc")
    if g.profile["role"] == "coach":
        return jsonify(rows)
    return jsonify([planner.player_view(t, g.profile) for t in rows])


@app.post("/api/trips")
@coach_only
def create_trip():
    team = planner.team(g.profile["team_id"])
    return jsonify(planner.coach_view(planner.create_trip(team, g.profile, body()))), 201


@app.get("/api/trips/<trip_id>")
@member
def get_trip(trip_id):
    trip = my_trip(trip_id)
    if g.profile["role"] == "coach":
        return jsonify(planner.coach_view(trip))
    return jsonify(planner.player_view(trip, g.profile))


@app.post("/api/trips/<trip_id>/replan")
@coach_only
def replan(trip_id):
    trip = my_trip(trip_id)
    if trip["status"] in ("AWAITING_APPROVAL", "BOOKED"):
        raise PlanError("Cancel the current bookings before re-planning.")
    return jsonify(planner.coach_view(planner.replan_with_rsvps(trip, planner.team(trip["team_id"]))))


@app.post("/api/trips/<trip_id>/select")
@coach_only
def select(trip_id):
    try:
        no = int(body()["package_no"])
    except (KeyError, ValueError, TypeError):
        raise PlanError("Choose a package.")
    return jsonify(planner.coach_view(planner.select_package(my_trip(trip_id), no, g.profile["id"])))


@app.post("/api/trips/<trip_id>/approve")
@coach_only
def approve(trip_id):
    result = planner.approve(my_trip(trip_id), g.profile["id"])
    return jsonify({"ok": result["ok"], "error": result.get("error"), "trip": planner.coach_view(result["trip"])})


@app.post("/api/trips/<trip_id>/cancel")
@coach_only
def cancel(trip_id):
    return jsonify(planner.coach_view(planner.cancel_bookings(my_trip(trip_id))))


@app.post("/api/trips/<trip_id>/rooms")
@coach_only
def rooms(trip_id):
    trip = my_trip(trip_id)
    planner.assign_rooms(trip)
    return jsonify(planner.coach_view(trip))


@app.get("/api/trips/<trip_id>/draft")
@coach_only
def draft(trip_id):
    return jsonify(planner.draft_announcement(my_trip(trip_id)))


@app.post("/api/trips/<trip_id>/publish")
@coach_only
def publish(trip_id):
    b = body()
    title, text = str(b.get("title") or "").strip(), str(b.get("body") or "").strip()
    if not title:
        raise PlanError("Add a title for the announcement.")
    planner.publish(my_trip(trip_id), g.profile["id"], title, text)
    return jsonify(planner.coach_view(my_trip(trip_id)))


@app.post("/api/trips/<trip_id>/rsvp")
@player_only
def rsvp(trip_id):
    choice = body().get("rsvp")
    if choice not in ("going", "maybe", "not_going"):
        raise PlanError("RSVP must be going, maybe or not_going.")
    trip = my_trip(trip_id)
    key = {"trip_id": trip["id"], "player_id": g.profile["id"]}
    if DB.select("trip_participants", key):
        DB.update("trip_participants", {"rsvp": choice}, key)
    else:
        DB.insert("trip_participants", {**key, "rsvp": choice, "room_no": None, "bus_no": None})
    return jsonify(planner.player_view(trip, g.profile))


# -- announcements --------------------------------------------------------
@app.get("/api/announcements")
@member
def announcements():
    rows = DB.select("announcements", {"team_id": g.profile["team_id"]}, order="created_at.desc", limit=100)
    ids = [a["id"] for a in rows]
    read = {r["announcement_id"] for r in DB.select("announcement_reads", {"user_id": g.profile["id"], "announcement_id": ids})}
    saved = {s["announcement_id"] for s in DB.select("saved_items", {"user_id": g.profile["id"], "announcement_id": ids})}
    authors = {p["id"]: p["full_name"] for p in DB.select("profiles", {"team_id": g.profile["team_id"], "role": "coach"})}
    return jsonify([{**a, "read": a["id"] in read, "saved": a["id"] in saved,
                     "author": authors.get(a.get("author_id"), "Coach")} for a in rows])


@app.post("/api/trips/<trip_id>/announcements")
@coach_only
def post_announcement(trip_id):
    trip = my_trip(trip_id)
    b = body()
    title, text = str(b.get("title") or "").strip(), str(b.get("body") or "").strip()
    if not title:
        raise PlanError("Add a title.")
    return jsonify(DB.insert("announcements", {"trip_id": trip["id"], "team_id": trip["team_id"],
                                                "author_id": g.profile["id"], "title": title, "body": text})[0]), 201


def my_announcement(aid):
    rows = DB.select("announcements", {"id": aid, "team_id": g.profile["team_id"]})
    if not rows:
        raise PlanError("Announcement not found.")
    return rows[0]


@app.post("/api/announcements/<aid>/read")
@member
def mark_read(aid):
    my_announcement(aid)
    key = {"announcement_id": aid, "user_id": g.profile["id"]}
    if not DB.select("announcement_reads", key):
        DB.insert("announcement_reads", key)
    return jsonify({"ok": True})


@app.post("/api/announcements/<aid>/save")
@member
def toggle_save(aid):
    my_announcement(aid)
    key = {"announcement_id": aid, "user_id": g.profile["id"]}
    if DB.select("saved_items", key):
        DB.delete("saved_items", key)
        return jsonify({"saved": False})
    DB.insert("saved_items", key)
    return jsonify({"saved": True})


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))

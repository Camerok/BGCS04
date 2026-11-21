"""Demo mode data: used only when Supabase isn't configured (and by the tests).

Creates one team, a coach, eight players with a mix of diet needs, and one planned trip,
plus two fake login tokens the front end's demo buttons use: demo-coach and demo-player.
"""
from datetime import datetime, timedelta

HOME = {"home_name": "Doyt L. Perry Stadium (BGSU)", "home_lat": 41.37806, "home_lng": -83.62250}
PLAYERS = [("Aiden Carter", []), ("Omar Haddad", ["halal"]), ("Priya Nair", ["vegetarian"]),
           ("Lucas Silva", []), ("Kenji Sato", ["gluten_free"]), ("Malik Ali", ["halal"]),
           ("Sofia Reyes", ["vegan", "vegetarian"]), ("Noah Fischer", ["nut_free"])]


def seed_demo(db, planner):
    team = db.insert("teams", {"name": "BGSU Falcons", "join_code": "FALCON-2026", **HOME,
                               "owner_id": "demo-coach-id"})[0]
    coach = db.insert("profiles", {"id": "demo-coach-id", "full_name": "Coach Morgan", "role": "coach",
                                   "team_id": team["id"], "diet_tags": []})[0]
    players = db.insert("profiles", [{"id": f"demo-player-{i}", "full_name": n, "role": "player",
                                      "team_id": team["id"], "diet_tags": d} for i, (n, d) in enumerate(PLAYERS, 1)])
    db.users["demo-coach"] = {"id": coach["id"], "email": "coach@demo.local"}
    db.users["demo-player"] = {"id": players[1]["id"], "email": "player@demo.local"}   # Omar: halal menu filter
    match = (datetime.now() + timedelta(days=8)).replace(hour=15, minute=30, second=0, microsecond=0)
    planner.create_trip(team, coach, {"opponent": "Ohio State", "venue_id": "V001",
                                      "match_at": match.isoformat(timespec="minutes"),
                                      "depart_after": (match - timedelta(days=1)).replace(hour=11, minute=0).isoformat(timespec="minutes"),
                                      "nights": 1, "budget": 9000, "num_players": 24, "num_coaches": 3,
                                      "num_staff": 2, "meals": 2})
    return team

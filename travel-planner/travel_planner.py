"""TravelPlanner: the coordinator. No math or AI itself; it calls the right class in order.

Trip lifecycle (trips.status):
  PLANNING --select_package--> AWAITING_APPROVAL --approve--> BOOKED
      ^                              |   (provider fails / coach cancels)
      +------------------------------+
  Players see hotel, restaurant and times only once the coach publishes a BOOKED trip.
"""
import math

from aivana_client import AivanaClient
from booking_service import BookingService, MockBookingProvider
from cost_calc import CostCalc
from models import TravelRequest, parse_dt
from travel_data import TravelData


class PlanError(ValueError):
    pass


class TravelPlanner:
    def __init__(self, db, data: TravelData | None = None, aivana: AivanaClient | None = None):
        self.db = db
        self.data = data or TravelData(db)
        self.calc = CostCalc(self.data)
        self.aivana = aivana or AivanaClient()
        self.bookings = BookingService(db, MockBookingProvider(self.data))

    # -- reads -------------------------------------------------------------
    def team(self, team_id):
        rows = self.db.select("teams", {"id": team_id})
        if not rows:
            raise PlanError("Team not found.")
        return rows[0]

    def trip(self, trip_id, team_id):
        rows = self.db.select("trips", {"id": trip_id, "team_id": team_id})
        if not rows:
            raise PlanError("Trip not found.")
        return rows[0]

    def packages(self, trip_id):
        return self.db.select("trip_packages", {"trip_id": trip_id}, order="package_no.asc")

    def package(self, trip, package_no=None):
        no = package_no if package_no is not None else trip.get("selected_package_no")
        rows = self.db.select("trip_packages", {"trip_id": trip["id"], "package_no": no})
        if not rows:
            raise PlanError("Package not found. Re-plan the trip and pick again.")
        return rows[0]

    def team_players(self, team_id):
        return self.db.select("profiles", {"team_id": team_id, "role": "player"}, order="full_name.asc")

    # -- planning ----------------------------------------------------------
    def create_trip(self, team: dict, coach: dict, form: dict) -> dict:
        venue = self._venue_from_form(form)
        players = self.team_players(team["id"])
        diet = sorted({t for p in players for t in (p.get("diet_tags") or [])} | set(form.get("diet_tags") or []))
        try:
            row = {
                "team_id": team["id"], "created_by": coach["id"],
                "opponent": str(form["opponent"]).strip(),
                **venue,
                "match_at": parse_dt(form["match_at"]).isoformat(timespec="minutes"),
                "depart_after": (parse_dt(form.get("depart_after")).isoformat(timespec="minutes")
                                 if form.get("depart_after") else None),
                "nights": int(form.get("nights", 1)),
                "budget": float(form.get("budget") or 0),
                "num_players": int(form.get("num_players") if form.get("num_players") not in (None, "") else len(players)),
                "num_coaches": int(form.get("num_coaches") or 1),
                "num_staff": int(form.get("num_staff") or 0),
                "meals": int(form.get("meals", 1)),
                "diet_tags": diet,
                "status": "PLANNING", "published": False, "selected_package_no": None,
            }
        except KeyError as exc:
            raise PlanError(f"Missing trip detail: {exc.args[0]}.") from exc
        except (ValueError, TypeError, AttributeError) as exc:
            raise PlanError("Check the match date/time and the numbers in the form.") from exc
        if not row["opponent"]:
            raise PlanError("Enter the opponent.")
        if min(row["nights"], row["meals"], row["num_players"], row["num_coaches"], row["num_staff"]) < 0:
            raise PlanError("Numbers can't be negative.")
        if row["num_players"] + row["num_coaches"] + row["num_staff"] == 0:
            raise PlanError("Add at least one traveler.")
        trip = self.db.insert("trips", row)[0]
        if players:
            self.db.insert("trip_participants", [{"trip_id": trip["id"], "player_id": p["id"], "rsvp": "maybe",
                                                  "room_no": None, "bus_no": None}
                                                 for p in players])
        return self.plan(trip, team)

    def _venue_from_form(self, form):
        if form.get("venue_id"):
            v = self.data.venues.get(form["venue_id"])
            if not v:
                raise PlanError("Unknown venue.")
            return {"venue_name": v["name"], "venue_address": v.get("address") or "",
                    "venue_lat": float(v["lat"]), "venue_lng": float(v["lng"])}
        try:
            return {"venue_name": str(form["venue_name"]).strip(), "venue_address": str(form.get("venue_address") or ""),
                    "venue_lat": float(form["venue_lat"]), "venue_lng": float(form["venue_lng"])}
        except (KeyError, ValueError, TypeError) as exc:
            raise PlanError("Pick a venue, or enter a venue name with latitude and longitude.") from exc

    def plan(self, trip: dict, team: dict) -> dict:
        """(Re)build packages and the AI pick. Clears any earlier selection."""
        request = TravelRequest.from_trip(trip, team)
        packages = self.calc.generate_packages(request)
        self.db.delete("trip_packages", {"trip_id": trip["id"]})
        if packages:
            self.db.insert("trip_packages", [p.to_row(trip["id"]) for p in packages])
        pkg_dicts = [{"package_no": p.package_no, "total_cost": p.total_cost, "detail": p.detail} for p in packages]
        brief = {"opponent": request.opponent, "venue": request.venue_name, "travelers": request.total_travelers(),
                 "nights": request.nights, "budget": request.budget, "diet_tags": request.diet_tags}
        rec = self.aivana.recommend(brief, pkg_dicts) if packages else None
        return self.db.update("trips", {"ai_recommendation": rec, "status": "PLANNING", "selected_package_no": None},
                              {"id": trip["id"]})[0]

    def replan_with_rsvps(self, trip: dict, team: dict) -> dict:
        going = self.db.select("trip_participants", {"trip_id": trip["id"], "rsvp": "going"})
        if going:
            trip = self.db.update("trips", {"num_players": len(going)}, {"id": trip["id"]})[0]
        return self.plan(trip, team)

    # -- booking -----------------------------------------------------------
    def select_package(self, trip: dict, package_no: int, coach_id: str) -> dict:
        if trip["status"] == "BOOKED":
            raise PlanError("This trip is already booked. Cancel the bookings first.")
        pkg = self.package(trip, package_no)
        ai = (trip.get("ai_recommendation") or {}).get("package_no") == package_no
        self.bookings.request(trip, pkg, coach_id, ai_pick=ai)
        return self.db.update("trips", {"selected_package_no": package_no, "status": "AWAITING_APPROVAL"},
                              {"id": trip["id"]})[0]

    def approve(self, trip: dict, coach_id: str) -> dict:
        if trip["status"] != "AWAITING_APPROVAL":
            raise PlanError("Pick a package first.")
        result = self.bookings.approve(trip, self.package(trip), coach_id)
        status = "BOOKED" if result["ok"] else "PLANNING"
        result["trip"] = self.db.update("trips", {"status": status}, {"id": trip["id"]})[0]
        if not result["ok"]:
            failed = [b["summary"] for b in result["bookings"] if b["status"] == "FAILED"]
            result["error"] = "Booking failed, nothing was charged: " + "; ".join(failed) + ". Pick another package."
        return result

    def cancel_bookings(self, trip: dict) -> dict:
        self.bookings.cancel(trip)
        return self.db.update("trips", {"status": "PLANNING", "published": False}, {"id": trip["id"]})[0]

    def assign_rooms(self, trip: dict) -> list:
        """Two players per room in name order; buses filled in the same order."""
        if not trip.get("selected_package_no"):
            raise PlanError("Pick a package first.")
        detail = self.package(trip)["detail"]
        parts = self.db.select("trip_participants", {"trip_id": trip["id"]})
        going = [p for p in parts if p["rsvp"] == "going"] or [p for p in parts if p["rsvp"] != "not_going"]
        names = {p["id"]: p["full_name"] for p in self.team_players(trip["team_id"])}
        going.sort(key=lambda p: names.get(p["player_id"], ""))
        seats = int(detail["bus"]["seats"])
        self.db.update("trip_participants", {"room_no": None, "bus_no": None}, {"trip_id": trip["id"]})
        for i, p in enumerate(going):
            values = {"room_no": (i // 2 + 1) if detail.get("hotel") else None, "bus_no": i // seats + 1}
            self.db.update("trip_participants", values, {"trip_id": trip["id"], "player_id": p["player_id"]})
        return self.db.select("trip_participants", {"trip_id": trip["id"]})

    # -- players -----------------------------------------------------------
    def publish(self, trip: dict, author_id: str, title: str, body: str) -> dict:
        if trip["status"] != "BOOKED":
            raise PlanError("Approve the bookings before publishing the trip to players.")
        self.db.update("trips", {"published": True}, {"id": trip["id"]})
        return self.db.insert("announcements", {"trip_id": trip["id"], "team_id": trip["team_id"],
                                                 "author_id": author_id, "title": title, "body": body})[0]

    def draft_announcement(self, trip: dict) -> dict:
        if not trip.get("selected_package_no"):
            raise PlanError("Pick a package first.")
        return self.aivana.draft_announcement(trip, self.package(trip))

    def player_view(self, trip: dict, player: dict) -> dict:
        """What a player may see: never costs or budget; hotel/restaurant only once published."""
        mine = self.db.select("trip_participants", {"trip_id": trip["id"], "player_id": player["id"]})
        view = {k: trip[k] for k in ("id", "opponent", "venue_name", "venue_address", "venue_lat", "venue_lng",
                                     "match_at", "nights", "status", "published")}
        view["my"] = mine[0] if mine else None
        if not (trip["published"] and trip["status"] in ("BOOKED", "IN_PROGRESS", "COMPLETED")):
            return view
        d = self.package(trip)["detail"]
        view.update({"suggested_departure": d["suggested_departure"], "drive_min": d["drive_min"],
                     "bus": {k: d["bus"].get(k) for k in ("company", "vehicle", "wifi")}})
        if d.get("hotel"):
            view["hotel"] = {k: d["hotel"].get(k) for k in ("name", "address", "lat", "lng", "stars", "amenities", "km_to_venue")}
        if d.get("restaurant"):
            r = d["restaurant"]
            view["restaurant"] = {k: r.get(k) for k in ("id", "name", "address", "lat", "lng", "cuisine", "km_to_venue")}
            view["my_menu"] = self.data.menu(r["id"], player.get("diet_tags") or [])
        return view

    def coach_view(self, trip: dict) -> dict:
        parts = self.db.select("trip_participants", {"trip_id": trip["id"]})
        names = {p["id"]: p["full_name"] for p in self.team_players(trip["team_id"])}
        return {**trip,
                "packages": self.packages(trip["id"]),
                "bookings": self.db.select("bookings", {"trip_id": trip["id"]}, order="created_at.asc"),
                "participants": [{**p, "full_name": names.get(p["player_id"], "?")} for p in parts],
                "rsvp_counts": {s: sum(p["rsvp"] == s for p in parts) for s in ("going", "maybe", "not_going")},
                "rooms_needed": math.ceil((trip["num_players"] + trip["num_coaches"] + trip["num_staff"]) / 2)}

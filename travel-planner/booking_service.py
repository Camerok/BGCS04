"""BookingService + MockBookingProvider: the AI can only *request* bookings; the coach approves.

request() writes PENDING_APPROVAL rows to `bookings`. Nothing is reserved until approve(),
which hands each booking to the provider. MockBookingProvider stands in for real hotel and
charter APIs (those need business partnerships) and returns confirmation codes.
"""
import secrets
import string
from datetime import timedelta

from models import parse_dt


class MockBookingProvider:
    PREFIX = {"hotel": "HTL", "bus": "BUS", "meal": "MEL"}

    def __init__(self, data):
        self.data = data

    def reserve(self, booking: dict, detail: dict):
        """Return (True, confirmation_code) or (False, reason)."""
        if booking["type"] == "hotel":
            hotel = self.data.hotels.get((detail.get("hotel") or {}).get("id"))
            if not hotel:
                return False, "Hotel no longer in the catalog"
            if int(hotel["rooms_available"]) < int(booking["quantity"]):
                return False, f"Only {hotel['rooms_available']} rooms left at {hotel['name']}"
        code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        return True, f"{self.PREFIX[booking['type']]}-{code}"


class BookingService:
    def __init__(self, db, provider):
        self.db = db
        self.provider = provider

    @staticmethod
    def stay_dates(trip: dict):
        match_day = parse_dt(trip["match_at"]).date()
        check_in = match_day - timedelta(days=1) if int(trip["nights"]) > 0 else match_day
        return check_in, check_in + timedelta(days=int(trip["nights"]))

    def request(self, trip: dict, package: dict, requested_by: str, ai_pick: bool) -> list[dict]:
        """Replace any pending bookings for this trip with the chosen package's bookings."""
        self.db.delete("bookings", {"trip_id": trip["id"], "status": "PENDING_APPROVAL"})
        d = package["detail"]
        check_in, check_out = self.stay_dates(trip)
        via = "Aivana's pick" if ai_pick else "coach's pick"
        rows = [{"trip_id": trip["id"], "type": "bus", "quantity": d["bus"]["count"], "amount": d["costs"]["bus"],
                 "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
                 "status": "PENDING_APPROVAL", "requested_by": requested_by,
                 "summary": f"{d['bus']['count']} x {d['bus']['company']} {d['bus'].get('vehicle') or 'bus'} ({via}, package {package['package_no']})"}]
        if d.get("hotel"):
            rows.append({"trip_id": trip["id"], "type": "hotel", "quantity": d["rooms"], "amount": d["costs"]["hotel"],
                         "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
                         "status": "PENDING_APPROVAL", "requested_by": requested_by,
                         "summary": f"{d['rooms']} rooms x {trip['nights']} night(s) at {d['hotel']['name']}. {d['hotel']['cancellation_policy']}"})
        if d.get("restaurant"):
            rows.append({"trip_id": trip["id"], "type": "meal", "quantity": d["travelers"], "amount": d["costs"]["meal"],
                         "check_in": check_in.isoformat(), "check_out": None,
                         "status": "PENDING_APPROVAL", "requested_by": requested_by,
                         "summary": f"Team meal for {d['travelers']} at {d['restaurant']['name']} ({trip['meals']} meal(s))"})
        return self.db.insert("bookings", rows)

    def approve(self, trip: dict, package: dict, coach_id: str) -> dict:
        pending = self.db.select("bookings", {"trip_id": trip["id"], "status": "PENDING_APPROVAL"})
        if not pending:
            return {"ok": False, "error": "Nothing is waiting for approval."}
        results, all_ok = [], True
        for b in pending:
            ok, info = self.provider.reserve(b, package["detail"])
            all_ok &= ok
            values = ({"status": "CONFIRMED", "confirmation_code": info, "approved_by": coach_id} if ok
                      else {"status": "FAILED", "summary": f"{b['summary']} | FAILED: {info}", "approved_by": coach_id})
            results += self.db.update("bookings", values, {"id": b["id"]})
        if not all_ok:   # don't leave half a trip booked
            for r in results:
                if r["status"] == "CONFIRMED":
                    self.db.update("bookings", {"status": "CANCELLED"}, {"id": r["id"]})
        return {"ok": all_ok, "bookings": self.db.select("bookings", {"trip_id": trip["id"]}, order="created_at.asc")}

    def cancel(self, trip: dict) -> list[dict]:
        out = []
        for status in ("PENDING_APPROVAL", "CONFIRMED"):
            out += self.db.update("bookings", {"status": "CANCELLED"}, {"trip_id": trip["id"], "status": status})
        return out

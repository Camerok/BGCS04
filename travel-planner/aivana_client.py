"""AivanaClient: wrapper around the Aivana API.

IMPORTANT: Aivana's real endpoint and payload format aren't known yet. Fill in the TODO
spots in _post() from their docs. Until then (or if a call fails) the app uses local
fallbacks, so the demo always works:
  - recommend(): scores packages on budget, diet coverage, distance, stars and cost
  - draft_announcement(): fills a message template from the booked package
The AI only ranks and writes text. Every number comes from CostCalc.
"""
import json
import os
from datetime import datetime

import requests


class AivanaClient:
    def __init__(self):
        self.api_key = os.environ.get("AIVANA_API_KEY", "").strip()
        self.api_url = os.environ.get("AIVANA_API_URL", "").strip()   # TODO: set from Aivana docs

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_url)

    def _post(self, prompt: str) -> str:
        # TODO: adjust headers / body / response field to Aivana's actual API.
        resp = requests.post(self.api_url, headers={"Authorization": f"Bearer {self.api_key}"},
                             json={"prompt": prompt}, timeout=30)
        resp.raise_for_status()
        return resp.json().get("response", "")

    # -- recommendation ----------------------------------------------------
    def recommend(self, request: dict, packages: list[dict]) -> dict:
        """packages: [{"package_no", "total_cost", "detail"}]. Returns {package_no, reason, source, note?}."""
        if not packages:
            return None
        if self.configured:
            try:
                return self._recommend_ai(request, packages)
            except Exception as exc:                     # network error, bad JSON, unknown package...
                return {**self._recommend_local(request, packages), "note": f"Aivana call failed ({exc}); used local ranking."}
        return {**self._recommend_local(request, packages), "note": "No Aivana key configured; used local ranking."}

    def _recommend_ai(self, request, packages):
        brief = [{"package_no": p["package_no"], "total_cost": p["total_cost"],
                  "within_budget": p["detail"]["within_budget"],
                  "hotel": _pick(p["detail"]["hotel"], "name", "stars", "km_to_venue", "amenities"),
                  "restaurant": _pick(p["detail"]["restaurant"], "name", "cuisine", "missing_diets"),
                  "late_arrival": p["detail"]["late_arrival"]} for p in packages]
        prompt = (
            "You are helping a college sports coach choose an away-game travel package. "
            "All costs are already calculated; do not recompute them. Prefer packages that are within "
            "budget, cover every dietary need, keep the hotel close to the stadium and arrive on time. "
            'Reply only with JSON: {"package_no": <int>, "reason": "<one or two sentences for the coach>"}.\n\n'
            f"Trip: {json.dumps(request, default=str)}\nPackages: {json.dumps(brief)}"
        )
        parsed = json.loads(self._post(prompt))
        valid = {p["package_no"] for p in packages}
        if parsed.get("package_no") not in valid:
            raise ValueError("Aivana returned an unknown package_no")
        return {"package_no": parsed["package_no"], "reason": parsed.get("reason", ""), "source": "aivana"}

    @staticmethod
    def score(p: dict) -> float:
        d = p["detail"]
        s = float(p["total_cost"])
        s += 0 if d["within_budget"] else 5000
        s += 1500 * len((d.get("restaurant") or {}).get("missing_diets") or [])
        s += 1000 if d.get("late_arrival") else 0
        if d.get("hotel"):
            s += 40 * float(d["hotel"]["km_to_venue"]) - 60 * int(d["hotel"].get("stars") or 0)
        return s

    def _recommend_local(self, request, packages):
        best = min(packages, key=self.score)
        d = best["detail"]
        bits = []
        bits.append("within budget" if d["within_budget"] else "the closest option to the budget")
        if d.get("hotel"):
            bits.append(f"{d['hotel']['name']} is {d['hotel']['km_to_venue']} km from the stadium")
        rest = d.get("restaurant")
        if rest:
            if request.get("diet_tags") and not rest.get("missing_diets"):
                bits.append(f"{rest['name']} covers every dietary need ({', '.join(request['diet_tags'])})")
            elif rest.get("missing_diets"):
                bits.append(f"{rest['name']} has no main for: {', '.join(rest['missing_diets'])}")
        reason = f"Package {best['package_no']} at ${best['total_cost']:,.2f}: " + "; ".join(bits) + "."
        return {"package_no": best["package_no"], "reason": reason, "source": "local"}

    # -- announcement ------------------------------------------------------
    def draft_announcement(self, trip: dict, package: dict) -> dict:
        template = self._draft_local(trip, package)
        if not self.configured:
            return template
        try:
            prompt = ("Write a short, friendly announcement from a coach to their players about an away trip. "
                      "Use only these facts; do not mention money. Reply only with JSON "
                      '{"title": "...", "body": "..."}.\n' + json.dumps({"facts": template["body"]}))
            parsed = json.loads(self._post(prompt))
            return {"title": parsed["title"], "body": parsed["body"], "source": "aivana"}
        except Exception:
            return template

    @staticmethod
    def _draft_local(trip, package):
        d = package["detail"]
        depart = _nice_time(d["suggested_departure"])
        lines = [f"We're heading to {trip['venue_name']} to play {trip['opponent']} ({_nice_time(trip['match_at'])})."]
        lines.append(f"Bus: {d['bus']['company']} leaves {depart}. Be there 15 minutes early.")
        if d.get("hotel"):
            h = d["hotel"]
            lines.append(f"Hotel: {h['name']}{', ' + h['address'] if h.get('address') else ''} "
                         f"({h['km_to_venue']} km from the stadium), {trip['nights']} night(s). Room assignments are in the app.")
        if d.get("restaurant"):
            r = d["restaurant"]
            lines.append(f"Team meal: {r['name']} ({r['cuisine']}). Check the menu in the app and pre-pick a dish that fits your diet.")
        lines.append("Questions? Reply to your coach.")
        return {"title": f"Away trip: {trip['opponent']}", "body": "\n".join(lines), "source": "template"}


def _pick(d, *keys):
    return {k: d.get(k) for k in keys} if d else None


def _nice_time(value) -> str:
    try:
        dt = datetime.fromisoformat(str(value)[:16])
        hour = dt.hour % 12 or 12                       # no %-I: it breaks on Windows
        return f"{dt.strftime('%a %b')} {dt.day}, {hour}:{dt.minute:02d} {'PM' if dt.hour >= 12 else 'AM'}"
    except (ValueError, TypeError):
        return str(value)

"""TravelData: the venues, hotels, restaurants (+ menus) and bus companies the planner can use.

Loaded once from the Supabase catalog tables. If Supabase isn't configured or the tables
are empty, it falls back to data/catalog.json, which is a copy of the same seed data.
"""
import json
from pathlib import Path

from geo import drive_minutes, haversine_km

CATALOG_FILE = Path(__file__).parent / "data" / "catalog.json"
TABLES = ("venues", "hotels", "restaurants", "menu_items", "bus_options")


class TravelData:
    def __init__(self, db=None):
        self.source = "sample"
        tables = None
        if db is not None:
            try:
                loaded = {t: db.select(t, order="id") for t in TABLES}
                if all(loaded[t] for t in ("hotels", "restaurants", "bus_options")):
                    tables, self.source = loaded, "supabase"
            except Exception:            # unreachable / tables missing: use the bundled copy
                tables = None
        if tables is None:
            tables = json.loads(CATALOG_FILE.read_text())
        self.venues = {v["id"]: v for v in tables["venues"]}
        self.hotels = {h["id"]: h for h in tables["hotels"]}
        self.restaurants = {r["id"]: r for r in tables["restaurants"]}
        self.bus_options = {b["id"]: b for b in tables["bus_options"]}
        self.menus = {}
        for item in tables["menu_items"]:
            self.menus.setdefault(item["restaurant_id"], []).append(item)

    # -- lookups -----------------------------------------------------------
    def _near(self, places, lat, lng, radius_km, city=True):
        out = []
        for p in places.values():
            km = haversine_km(lat, lng, float(p["lat"]), float(p["lng"]))
            if km <= radius_km:
                out.append({**p, "km_to_venue": round(km, 2), "drive_min_to_venue": drive_minutes(km * 1.3, city=city)})
        return sorted(out, key=lambda p: p["km_to_venue"])

    def hotels_near(self, lat, lng, radius_km=25):
        return self._near(self.hotels, lat, lng, radius_km)

    def restaurants_near(self, lat, lng, radius_km=15):
        return self._near(self.restaurants, lat, lng, radius_km)

    def menu(self, restaurant_id, diet_tags=()):
        """Menu items that satisfy every tag in diet_tags (all items if none given)."""
        wanted = set(diet_tags or ())
        items = self.menus.get(restaurant_id, [])
        return [m for m in items if wanted <= set(m.get("diet_tags") or [])]

    def missing_diets(self, restaurant_id, diet_tags):
        """Diet tags for which the restaurant has no main course."""
        mains = [m for m in self.menus.get(restaurant_id, []) if m.get("course") == "main"]
        return sorted(t for t in set(diet_tags or ()) if not any(t in (m.get("diet_tags") or []) for m in mains))

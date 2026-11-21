"""CostCalc: all the arithmetic. The AI never does math.

Rules (change them here, in one place):
- Rooms: 2 people per room (hotel.room_capacity).
- Bus: ceil(travelers / seats) buses; each costs max(min_charge, rate_per_km x round-trip road km)
  plus the driver's hotel room (driver_overnight_fee) for every night away.
- Hotel: rooms x price_per_night x nights. Day trips (0 nights) have no hotel.
- Meals: travelers x restaurant price_per_person x team meals.
- Timing: overnight trips arrive by 6 PM the day before the match; day trips arrive 3 hours
  before kickoff. The bus never leaves before the trip's depart_after time.
"""
import math
from datetime import datetime, timedelta
from itertools import product

from geo import drive_minutes, road_km
from models import TravelPackage, TravelRequest
from travel_data import TravelData

HOTEL_RADIUS_KM = 25
RESTAURANT_RADIUS_KM = 15
MAX_HOTELS = 6
MAX_RESTAURANTS = 4
MAX_PACKAGES = 12


class CostCalc:
    def __init__(self, data: TravelData):
        self.data = data

    # -- the pieces --------------------------------------------------------
    @staticmethod
    def calc_rooms(request: TravelRequest, room_capacity: int = 2) -> int:
        return math.ceil(request.total_travelers() / max(1, room_capacity)) if request.nights > 0 else 0

    @staticmethod
    def calc_buses(request: TravelRequest, bus: dict) -> int:
        return math.ceil(request.total_travelers() / int(bus["seats"]))

    def bus_cost(self, request: TravelRequest, bus: dict, route_km: float) -> float:
        per_bus = max(float(bus["min_charge"]), float(bus["rate_per_km"]) * route_km * 2)
        per_bus += float(bus["driver_overnight_fee"]) * request.nights
        return round(self.calc_buses(request, bus) * per_bus, 2)

    @staticmethod
    def hotel_cost(request: TravelRequest, hotel: dict, rooms: int) -> float:
        return round(rooms * float(hotel["price_per_night"]) * request.nights, 2)

    @staticmethod
    def meal_cost(request: TravelRequest, restaurant: dict) -> float:
        return round(request.total_travelers() * float(restaurant["price_per_person"]) * request.meals, 2)

    @staticmethod
    def timing(request: TravelRequest, drive_min: int) -> dict:
        if request.nights > 0:
            arrive_by = datetime.combine(request.match_at.date() - timedelta(days=1), datetime.min.time()) + timedelta(hours=18)
        else:
            arrive_by = request.match_at - timedelta(hours=3)
        depart = arrive_by - timedelta(minutes=drive_min + 30)          # 30 min buffer for loading/traffic
        late = False
        if request.depart_after and depart < request.depart_after:
            depart = request.depart_after
            late = depart + timedelta(minutes=drive_min) > arrive_by
        return {"suggested_departure": depart.isoformat(timespec="minutes"),
                "arrive_by": arrive_by.isoformat(timespec="minutes"), "late_arrival": late}

    def calc_total(self, bus_cost: float, hotel_cost: float, meal_cost: float) -> float:
        return round(bus_cost + hotel_cost + meal_cost, 2)

    # -- candidates --------------------------------------------------------
    def candidate_hotels(self, request: TravelRequest) -> list:
        if request.nights == 0:
            return [None]
        hotels = [h for h in self.data.hotels_near(request.venue_lat, request.venue_lng, HOTEL_RADIUS_KM)
                  if int(h["rooms_available"]) >= self.calc_rooms(request, int(h["room_capacity"]))]
        # best value first: cheap, close, decent stars
        hotels.sort(key=lambda h: float(h["price_per_night"]) + 8 * h["km_to_venue"] - 10 * int(h.get("stars") or 0))
        return hotels[:MAX_HOTELS]

    def candidate_restaurants(self, request: TravelRequest) -> list:
        if request.meals == 0:
            return [None]
        near = self.data.restaurants_near(request.venue_lat, request.venue_lng, RESTAURANT_RADIUS_KM)
        fits = [r for r in near if r.get("takes_group_reservations", True)
                and int(r["group_capacity"]) >= request.total_travelers()] or near
        for r in fits:
            r["missing_diets"] = self.data.missing_diets(r["id"], request.diet_tags)
        fits.sort(key=lambda r: (len(r["missing_diets"]), float(r["price_per_person"]), r["km_to_venue"]))
        return fits[:MAX_RESTAURANTS]

    # -- packages ----------------------------------------------------------
    def generate_packages(self, request: TravelRequest) -> list[TravelPackage]:
        route = road_km(request.home_lat, request.home_lng, request.venue_lat, request.venue_lng)
        drive = drive_minutes(route)
        timing = self.timing(request, drive)

        buses = sorted(((b, self.bus_cost(request, b, route)) for b in self.data.bus_options.values()),
                       key=lambda pair: pair[1])
        if not buses:
            return []
        bus, b_cost = buses[0]                                         # cheapest way to move everyone
        bus_alternatives = [{"id": b["id"], "company": b["company"], "count": self.calc_buses(request, b),
                             "cost": c} for b, c in buses]

        packages = []
        for hotel, rest in product(self.candidate_hotels(request), self.candidate_restaurants(request)):
            rooms = self.calc_rooms(request, int(hotel["room_capacity"])) if hotel else 0
            h_cost = self.hotel_cost(request, hotel, rooms) if hotel else 0.0
            m_cost = self.meal_cost(request, rest) if rest else 0.0
            total = self.calc_total(b_cost, h_cost, m_cost)
            packages.append({
                "total": total,
                "detail": {
                    "travelers": request.total_travelers(),
                    "rooms": rooms,
                    "hotel": _hotel_view(hotel),
                    "restaurant": _restaurant_view(rest),
                    "bus": {"id": bus["id"], "company": bus["company"], "vehicle": bus.get("vehicle"),
                            "seats": int(bus["seats"]), "count": self.calc_buses(request, bus),
                            "wifi": bus.get("wifi")},
                    "bus_alternatives": bus_alternatives,
                    "costs": {"bus": b_cost, "hotel": h_cost, "meal": m_cost},
                    "within_budget": request.budget <= 0 or total <= request.budget,
                    "route_km": route, "drive_min": drive, **timing,
                },
            })
        packages.sort(key=lambda p: p["total"])
        return [TravelPackage(package_no=i, total_cost=p["total"], route_km=route, detail=p["detail"])
                for i, p in enumerate(packages[:MAX_PACKAGES], start=1)]


def _hotel_view(h):
    if not h:
        return None
    keep = ("id", "name", "address", "lat", "lng", "stars", "price_per_night", "amenities",
            "cancellation_policy", "km_to_venue", "drive_min_to_venue", "source")
    return {k: h.get(k) for k in keep}


def _restaurant_view(r):
    if not r:
        return None
    keep = ("id", "name", "address", "lat", "lng", "cuisine", "price_per_person", "halal_certified",
            "km_to_venue", "missing_diets", "source")
    return {k: r.get(k) for k in keep}

"""Distance helpers (the UML's DistanceService). Straight-line math, no API key needed."""
import math

ROAD_FACTOR = 1.25      # roads are ~25% longer than a straight line
HIGHWAY_KMH = 85        # bus between cities
CITY_KMH = 30           # hotel <-> stadium inside a city


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def road_km(lat1, lng1, lat2, lng2) -> float:
    return round(haversine_km(lat1, lng1, lat2, lng2) * ROAD_FACTOR, 1)


def drive_minutes(km: float, city: bool = False) -> int:
    return max(1, round(km / (CITY_KMH if city else HIGHWAY_KMH) * 60))

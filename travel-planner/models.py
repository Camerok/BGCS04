"""Plain data containers: TravelRequest and TravelPackage."""
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class TravelRequest:
    """Everything CostCalc needs about one away trip. Built from a `trips` row + its team."""
    opponent: str
    venue_name: str
    venue_lat: float
    venue_lng: float
    home_lat: float
    home_lng: float
    match_at: datetime
    num_players: int
    num_coaches: int
    num_staff: int
    nights: int = 1
    meals: int = 1                      # team meals to book at the restaurant
    budget: float = 0.0                 # 0 = no budget limit
    diet_tags: list = field(default_factory=list)
    depart_after: Optional[datetime] = None

    def total_travelers(self) -> int:
        return self.num_players + self.num_coaches + self.num_staff

    @classmethod
    def from_trip(cls, trip: dict, team: dict) -> "TravelRequest":
        return cls(
            opponent=trip["opponent"],
            venue_name=trip["venue_name"],
            venue_lat=float(trip["venue_lat"]),
            venue_lng=float(trip["venue_lng"]),
            home_lat=float(team["home_lat"]),
            home_lng=float(team["home_lng"]),
            match_at=parse_dt(trip["match_at"]),
            num_players=int(trip["num_players"]),
            num_coaches=int(trip["num_coaches"]),
            num_staff=int(trip["num_staff"]),
            nights=int(trip.get("nights") or 0),
            meals=int(trip.get("meals") or 0),
            budget=float(trip.get("budget") or 0),
            diet_tags=list(trip.get("diet_tags") or []),
            depart_after=parse_dt(trip.get("depart_after")),
        )


@dataclass
class TravelPackage:
    package_no: int
    total_cost: float
    route_km: float
    detail: dict                        # hotel, restaurant, bus, costs, timing (stored as jsonb)

    def to_row(self, trip_id: str) -> dict:
        return {"trip_id": trip_id, **asdict(self)}


def parse_dt(value) -> Optional[datetime]:
    """Accepts '2026-11-28T15:30', '2026-11-28 15:30:00', a datetime, or None."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "").replace(" ", "T")[:19])

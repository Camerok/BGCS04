from datetime import datetime

from cost_calc import CostCalc
from models import TravelRequest
from travel_data import TravelData

DATA = TravelData()          # bundled catalog (same rows as the Supabase seed)


def req(**kw):
    base = dict(opponent="Ohio State", venue_name="Ohio Stadium", venue_lat=40.00167, venue_lng=-83.01972,
                home_lat=41.37806, home_lng=-83.6225, match_at=datetime(2026, 11, 28, 15, 30),
                num_players=24, num_coaches=3, num_staff=2, nights=1, meals=2, budget=9000)
    base.update(kw)
    return TravelRequest(**base)


def test_rooms_and_buses():
    calc = CostCalc(DATA)
    r = req()
    assert r.total_travelers() == 29
    assert calc.calc_rooms(r) == 15                       # ceil(29 / 2)
    assert calc.calc_buses(r, {"seats": 24}) == 2
    assert calc.calc_rooms(req(nights=0)) == 0            # day trip: no hotel


def test_bus_cost_uses_min_charge_round_trip_and_driver_nights():
    calc = CostCalc(DATA)
    bus = {"seats": 56, "rate_per_km": 2.0, "min_charge": 1000, "driver_overnight_fee": 150}
    assert calc.bus_cost(req(nights=2), bus, route_km=100) == 1000 + 300      # 2*100*2=400 < min charge
    assert calc.bus_cost(req(nights=0), bus, route_km=400) == 1600            # 400*2*2


def test_packages_are_priced_sorted_and_budget_flagged():
    pkgs = CostCalc(DATA).generate_packages(req())
    assert 0 < len(pkgs) <= 12
    totals = [p.total_cost for p in pkgs]
    assert totals == sorted(totals)
    for p in pkgs:
        c = p.detail["costs"]
        assert round(c["bus"] + c["hotel"] + c["meal"], 2) == p.total_cost
        assert p.detail["hotel"]["km_to_venue"] <= 25
        assert p.detail["within_budget"] == (p.total_cost <= 9000)
    assert not CostCalc(DATA).generate_packages(req(budget=100))[0].detail["within_budget"]


def test_hotels_without_enough_rooms_are_skipped():
    pkgs = CostCalc(DATA).generate_packages(req(num_players=100))
    for p in pkgs:
        assert DATA.hotels[p.detail["hotel"]["id"]]["rooms_available"] >= p.detail["rooms"]


def test_departure_respects_constraint_and_flags_late_arrival():
    calc = CostCalc(DATA)
    t = calc.timing(req(depart_after=datetime(2026, 11, 27, 17, 0)), drive_min=150)
    assert t["suggested_departure"] == "2026-11-27T17:00"
    assert t["late_arrival"] is True                      # 17:00 + 2.5h is after the 18:00 arrive-by


def test_diet_coverage_prefers_restaurants_that_cover_everyone():
    pkgs = CostCalc(DATA).generate_packages(req(diet_tags=["halal", "gluten_free"]))
    assert pkgs[0].detail["restaurant"]["missing_diets"] == []

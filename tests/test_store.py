import pytest

from fleet_mcp import data, store


@pytest.fixture()
def conn():
    c = data.connect(":memory:")
    data.seed(c, vehicles=10, days=14, seed=7)
    yield c
    c.close()


def test_seed_is_deterministic():
    a, b = data.connect(":memory:"), data.connect(":memory:")
    data.seed(a, seed=1)
    data.seed(b, seed=1)
    q = "SELECT SUM(distance_km), SUM(idle_minutes), COUNT(*) FROM trips"
    assert tuple(a.execute(q).fetchone()) == tuple(b.execute(q).fetchone())


def test_list_vehicles_filters_by_depot_case_insensitively(conn):
    everything = store.list_vehicles(conn)
    ottawa = store.list_vehicles(conn, "ottawa")
    assert len(everything) == 10
    assert ottawa and all(v["depot"] == "Ottawa" for v in ottawa)


def test_vehicle_summary_matches_raw_totals(conn):
    summary = store.vehicle_summary(conn, "V001", days=14)
    raw = conn.execute(
        "SELECT COUNT(*), SUM(distance_km), SUM(fuel_litres) FROM trips WHERE vehicle_id = 'V001'"
    ).fetchone()
    assert summary["trips"] == raw[0]
    assert summary["distance_km"] == pytest.approx(raw[1], abs=0.1)
    assert summary["litres_per_100km"] == pytest.approx(raw[2] / raw[1] * 100, abs=0.01)


def test_unknown_vehicle_gives_actionable_error(conn):
    with pytest.raises(store.FleetError, match="list_vehicles"):
        store.vehicle_summary(conn, "V999")


@pytest.mark.parametrize("days", [0, -1, 366])
def test_days_out_of_range_rejected(conn, days):
    with pytest.raises(store.FleetError):
        store.idling_report(conn, days=days)


def test_idling_report_ranks_and_costs(conn):
    report = store.idling_report(conn, days=14, limit=3, fuel_price=2.0)
    idlers = report["top_idlers"]
    assert len(idlers) == 3
    assert [i["idle_hours"] for i in idlers] == sorted((i["idle_hours"] for i in idlers), reverse=True)
    # Vehicles 5 and 10 are seeded as heavy idlers, so they must surface at the top.
    assert {idlers[0]["vehicle_id"], idlers[1]["vehicle_id"]} == {"V005", "V010"}
    total_minutes = conn.execute("SELECT SUM(idle_minutes) FROM trips").fetchone()[0]
    assert report["fleet_idle_fuel_cost"] == pytest.approx(total_minutes / 60 * 2.5 * 2.0, abs=0.01)


def test_active_faults_sorted_by_severity_and_filterable(conn):
    faults = store.active_faults(conn)
    order = {"high": 0, "medium": 1, "low": 2}
    ranks = [order[f["severity"]] for f in faults]
    assert ranks == sorted(ranks)
    assert all(f["severity"] == "high" for f in store.active_faults(conn, "HIGH"))
    with pytest.raises(store.FleetError):
        store.active_faults(conn, "critical")


def test_driver_scores_bounded_and_worst_first(conn):
    scores = store.driver_safety_scores(conn, days=14)
    assert scores
    assert all(0 <= s["score"] <= 100 for s in scores)
    assert [s["score"] for s in scores] == sorted(s["score"] for s in scores)
    worst = scores[0]
    assert worst["score"] == pytest.approx(max(0, 100 - 20 * worst["events_per_100km"]), abs=0.2)


def test_recent_trips_newest_first_and_limited(conn):
    trips = store.recent_trips(conn, "V002", limit=5)
    assert len(trips) == 5
    starts = [t["start_time"] for t in trips]
    assert starts == sorted(starts, reverse=True)

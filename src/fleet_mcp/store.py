"""Read-only analytics over the fleet database.

All business logic lives here as plain functions returning plain dicts, so it
can be unit-tested without MCP and reused behind any other interface.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from .data import REFERENCE_NOW

IDLE_LITRES_PER_HOUR = 2.5
MAX_DAYS = 365


class FleetError(ValueError):
    """Raised for bad input; the message is safe to show to the model."""


def _since(days: int) -> str:
    if not 1 <= days <= MAX_DAYS:
        raise FleetError(f"days must be between 1 and {MAX_DAYS}")
    return (REFERENCE_NOW - timedelta(days=days)).isoformat()


def _require_vehicle(conn: sqlite3.Connection, vehicle_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM vehicles WHERE vehicle_id = ?", (vehicle_id,)).fetchone()
    if row is None:
        raise FleetError(f"Unknown vehicle_id '{vehicle_id}'. Call list_vehicles to see valid ids.")
    return row


def list_vehicles(conn: sqlite3.Connection, depot: str | None = None) -> list[dict]:
    sql = "SELECT vehicle_id, name, type, depot, odometer_km FROM vehicles"
    params: tuple = ()
    if depot:
        sql += " WHERE depot = ? COLLATE NOCASE"
        params = (depot,)
    return [dict(r) for r in conn.execute(sql + " ORDER BY vehicle_id", params)]


def vehicle_summary(conn: sqlite3.Connection, vehicle_id: str, days: int = 7) -> dict:
    vehicle = _require_vehicle(conn, vehicle_id)
    row = conn.execute(
        "SELECT COUNT(*) AS trips, COALESCE(SUM(distance_km), 0) AS distance_km,"
        " COALESCE(SUM(idle_minutes), 0) AS idle_minutes, COALESCE(SUM(fuel_litres), 0) AS fuel_litres,"
        " COALESCE(MAX(max_speed_kmh), 0) AS max_speed_kmh"
        " FROM trips WHERE vehicle_id = ? AND start_time >= ?",
        (vehicle_id, _since(days)),
    ).fetchone()
    distance, fuel = row["distance_km"], row["fuel_litres"]
    return {
        "vehicle_id": vehicle_id,
        "name": vehicle["name"],
        "depot": vehicle["depot"],
        "period_days": days,
        "trips": row["trips"],
        "distance_km": round(distance, 1),
        "idle_minutes": round(row["idle_minutes"], 1),
        "fuel_litres": round(fuel, 1),
        "litres_per_100km": round(fuel / distance * 100, 2) if distance else None,
        "max_speed_kmh": row["max_speed_kmh"],
        "active_faults": conn.execute(
            "SELECT COUNT(*) FROM fault_codes WHERE vehicle_id = ? AND active = 1", (vehicle_id,)
        ).fetchone()[0],
    }


def idling_report(conn: sqlite3.Connection, days: int = 7, limit: int = 5, fuel_price: float = 1.60) -> dict:
    """Rank vehicles by idle time and estimate the fuel cost of that idling."""
    if not 1 <= limit <= 50:
        raise FleetError("limit must be between 1 and 50")
    if fuel_price <= 0:
        raise FleetError("fuel_price must be positive")
    rows = conn.execute(
        "SELECT t.vehicle_id, v.name, v.depot, SUM(t.idle_minutes) AS idle_minutes, COUNT(*) AS trips"
        " FROM trips t JOIN vehicles v USING (vehicle_id) WHERE t.start_time >= ?"
        " GROUP BY t.vehicle_id ORDER BY idle_minutes DESC",
        (_since(days),),
    ).fetchall()
    total_idle = sum(r["idle_minutes"] for r in rows)

    def cost(minutes: float) -> float:
        return round(minutes / 60 * IDLE_LITRES_PER_HOUR * fuel_price, 2)

    return {
        "period_days": days,
        "fleet_idle_hours": round(total_idle / 60, 1),
        "fleet_idle_fuel_cost": cost(total_idle),
        "assumptions": {"idle_litres_per_hour": IDLE_LITRES_PER_HOUR, "fuel_price_per_litre": fuel_price},
        "top_idlers": [
            {
                "vehicle_id": r["vehicle_id"],
                "name": r["name"],
                "depot": r["depot"],
                "idle_hours": round(r["idle_minutes"] / 60, 1),
                "idle_minutes_per_trip": round(r["idle_minutes"] / r["trips"], 1),
                "share_of_fleet_idle_pct": round(r["idle_minutes"] / total_idle * 100, 1) if total_idle else 0.0,
                "estimated_fuel_cost": cost(r["idle_minutes"]),
            }
            for r in rows[:limit]
        ],
    }


def active_faults(conn: sqlite3.Connection, severity: str | None = None) -> list[dict]:
    if severity and severity.lower() not in {"low", "medium", "high"}:
        raise FleetError("severity must be one of: low, medium, high")
    sql = (
        "SELECT f.vehicle_id, v.name, v.depot, f.code, f.description, f.severity, f.first_seen"
        " FROM fault_codes f JOIN vehicles v USING (vehicle_id) WHERE f.active = 1"
    )
    params: tuple = ()
    if severity:
        sql += " AND f.severity = ?"
        params = (severity.lower(),)
    sql += " ORDER BY CASE f.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, f.first_seen"
    return [dict(r) for r in conn.execute(sql, params)]


def driver_safety_scores(conn: sqlite3.Connection, days: int = 30) -> list[dict]:
    """Score drivers 0-100 from safety events per 100 km (higher is safer).

    Score = max(0, 100 - 20 * events_per_100km). The weighting is a simple,
    explainable rule for the demo, not an industry standard.
    """
    since = _since(days)
    rows = conn.execute(
        "SELECT d.driver_id, d.name, d.depot, COALESCE(SUM(t.distance_km), 0) AS distance_km,"
        " (SELECT COUNT(*) FROM safety_events e WHERE e.driver_id = d.driver_id AND e.event_time >= ?) AS events"
        " FROM drivers d LEFT JOIN trips t ON t.driver_id = d.driver_id AND t.start_time >= ?"
        " GROUP BY d.driver_id",
        (since, since),
    ).fetchall()
    scored = []
    for r in rows:
        if not r["distance_km"]:
            continue
        rate = r["events"] / r["distance_km"] * 100
        scored.append(
            {
                "driver_id": r["driver_id"],
                "name": r["name"],
                "depot": r["depot"],
                "distance_km": round(r["distance_km"], 1),
                "safety_events": r["events"],
                "events_per_100km": round(rate, 2),
                "score": round(max(0.0, 100 - 20 * rate), 1),
            }
        )
    return sorted(scored, key=lambda d: d["score"])


def recent_trips(conn: sqlite3.Connection, vehicle_id: str, limit: int = 10) -> list[dict]:
    _require_vehicle(conn, vehicle_id)
    if not 1 <= limit <= 100:
        raise FleetError("limit must be between 1 and 100")
    rows = conn.execute(
        "SELECT t.trip_id, d.name AS driver, t.start_time, t.end_time, t.distance_km, t.idle_minutes,"
        " t.fuel_litres, t.max_speed_kmh FROM trips t JOIN drivers d USING (driver_id)"
        " WHERE t.vehicle_id = ? ORDER BY t.start_time DESC LIMIT ?",
        (vehicle_id, limit),
    )
    return [dict(r) for r in rows]

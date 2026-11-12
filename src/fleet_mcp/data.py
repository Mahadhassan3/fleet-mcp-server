"""Deterministic synthetic fleet dataset.

Everything here is simulated. The generator is seeded, so the same seed always
produces the same fleet, which keeps tests and demos reproducible.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    type       TEXT NOT NULL,
    depot      TEXT NOT NULL,
    odometer_km REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS drivers (
    driver_id TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    depot     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trips (
    trip_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id  TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    driver_id   TEXT NOT NULL REFERENCES drivers(driver_id),
    start_time  TEXT NOT NULL,
    end_time    TEXT NOT NULL,
    distance_km REAL NOT NULL,
    idle_minutes REAL NOT NULL,
    fuel_litres REAL NOT NULL,
    max_speed_kmh REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS safety_events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id    INTEGER NOT NULL REFERENCES trips(trip_id),
    driver_id  TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    event_time TEXT NOT NULL,
    event_type TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fault_codes (
    fault_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    code       TEXT NOT NULL,
    description TEXT NOT NULL,
    severity   TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    active     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trips_vehicle ON trips(vehicle_id, start_time);
CREATE INDEX IF NOT EXISTS idx_trips_driver ON trips(driver_id, start_time);
CREATE INDEX IF NOT EXISTS idx_events_driver ON safety_events(driver_id, event_time);
"""

DEPOTS = ["Ottawa", "Toronto", "Montreal"]
VEHICLE_TYPES = [("Cargo Van", 11.5), ("Box Truck", 21.0), ("Pickup", 13.5)]
DRIVER_NAMES = [
    "A. Tremblay", "B. Singh", "C. Nguyen", "D. Okafor", "E. Martin",
    "F. Rossi", "G. Chen", "H. Wilson", "I. Haddad", "J. Kowalski",
    "K. Brown", "L. Garcia",
]
EVENT_TYPES = ["harsh_braking", "harsh_acceleration", "speeding", "harsh_cornering"]
FAULTS = [
    ("P0420", "Catalyst system efficiency below threshold", "medium"),
    ("P0300", "Random or multiple cylinder misfire detected", "high"),
    ("P0171", "System too lean (bank 1)", "medium"),
    ("P0128", "Coolant thermostat below regulating temperature", "low"),
    ("U0100", "Lost communication with engine control module", "high"),
    ("P0562", "System voltage low", "medium"),
]

# Fixed reference point so "the last N days" is stable across runs.
REFERENCE_NOW = datetime(2026, 11, 1, 0, 0, 0)


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def seed(conn: sqlite3.Connection, *, vehicles: int = 15, days: int = 30, seed: int = 42) -> None:
    """Create the schema and fill it with a reproducible synthetic fleet."""
    rng = random.Random(seed)
    conn.executescript(SCHEMA)
    for table in ("safety_events", "fault_codes", "trips", "drivers", "vehicles"):
        conn.execute(f"DELETE FROM {table}")

    drivers = []
    for i, name in enumerate(DRIVER_NAMES, start=1):
        driver_id = f"D{i:03d}"
        depot = DEPOTS[i % len(DEPOTS)]
        # Each driver gets a stable risk profile so scores differ meaningfully.
        drivers.append((driver_id, name, depot, rng.uniform(0.2, 2.5)))
        conn.execute("INSERT INTO drivers VALUES (?, ?, ?)", (driver_id, name, depot))

    fleet = []
    for i in range(1, vehicles + 1):
        vehicle_id = f"V{i:03d}"
        vtype, l_per_100 = VEHICLE_TYPES[i % len(VEHICLE_TYPES)]
        depot = DEPOTS[i % len(DEPOTS)]
        # A few vehicles idle far more than the rest: the "find the outliers" story.
        idle_factor = 3.0 if i % 5 == 0 else 1.0
        fleet.append((vehicle_id, vtype, depot, l_per_100, idle_factor))
        conn.execute(
            "INSERT INTO vehicles VALUES (?, ?, ?, ?, ?)",
            (vehicle_id, f"{vtype} {i:02d}", vtype, depot, round(rng.uniform(20_000, 180_000), 1)),
        )

    start = REFERENCE_NOW - timedelta(days=days)
    for day in range(days):
        date = start + timedelta(days=day)
        if date.weekday() == 6:  # no Sunday routes
            continue
        for vehicle_id, _vtype, depot, l_per_100, idle_factor in fleet:
            depot_drivers = [d for d in drivers if d[2] == depot] or drivers
            driver_id, _name, _depot, risk = rng.choice(depot_drivers)
            clock = date + timedelta(hours=7, minutes=rng.randint(0, 90))
            for _ in range(rng.randint(1, 4)):
                distance = round(rng.uniform(8, 95), 1)
                drive_minutes = distance / rng.uniform(35, 60) * 60
                idle = round(rng.uniform(2, 14) * idle_factor, 1)
                # Idling burns roughly 2.5 L/h on top of distance-based consumption.
                fuel = round(distance * l_per_100 / 100 + idle / 60 * 2.5, 2)
                end = clock + timedelta(minutes=drive_minutes + idle)
                max_speed = round(rng.uniform(70, 105) + risk * rng.uniform(0, 12), 1)
                cur = conn.execute(
                    "INSERT INTO trips (vehicle_id, driver_id, start_time, end_time, distance_km,"
                    " idle_minutes, fuel_litres, max_speed_kmh) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (vehicle_id, driver_id, clock.isoformat(), end.isoformat(), distance, idle, fuel, max_speed),
                )
                expected_events = distance / 100 * risk * 1.5
                for _ in range(_poisson(rng, expected_events)):
                    offset = rng.uniform(0, max(drive_minutes, 1))
                    conn.execute(
                        "INSERT INTO safety_events (trip_id, driver_id, vehicle_id, event_time, event_type)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (cur.lastrowid, driver_id, vehicle_id,
                         (clock + timedelta(minutes=offset)).isoformat(), rng.choice(EVENT_TYPES)),
                    )
                clock = end + timedelta(minutes=rng.randint(15, 75))

    for vehicle_id, *_ in fleet:
        for _ in range(rng.choice([0, 0, 1, 1, 2])):
            code, description, severity = rng.choice(FAULTS)
            first_seen = REFERENCE_NOW - timedelta(days=rng.randint(1, days))
            conn.execute(
                "INSERT INTO fault_codes (vehicle_id, code, description, severity, first_seen, active)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (vehicle_id, code, description, severity, first_seen.isoformat(), int(rng.random() < 0.7)),
            )
    conn.commit()


def _poisson(rng: random.Random, lam: float) -> int:
    """Small-lambda Poisson sampler (Knuth) so we need no numpy."""
    if lam <= 0:
        return 0
    limit, k, p = pow(2.718281828459045, -lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic fleet database.")
    parser.add_argument("--db", default="fleet.db")
    parser.add_argument("--vehicles", type=int, default=15)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    conn = connect(args.db)
    seed(conn, vehicles=args.vehicles, days=args.days, seed=args.seed)
    trips = conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
    print(f"Seeded {args.db}: {args.vehicles} vehicles, {trips} trips over {args.days} days")


if __name__ == "__main__":
    main()

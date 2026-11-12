"""MCP server exposing fleet analytics as tools an AI assistant can call.

The server is a thin layer: each tool validates input, calls a function in
`store`, and returns structured JSON. Tool descriptions are written for the
model, because that is who reads them.
"""

from __future__ import annotations

import functools
import os
import sqlite3
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import data, store

mcp = MCPServer(
    "fleet-telematics",
    instructions=(
        "Tools for answering questions about a vehicle fleet: usage, idling and its fuel cost, "
        "engine fault codes and driver safety. All data is simulated. Vehicle ids look like "
        "V001; call list_vehicles first if you do not know them."
    ),
)

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    """Open the database once; seed it on first run so the demo works out of the box."""
    global _conn
    if _conn is None:
        db_path = Path(os.environ.get("FLEET_DB", "fleet.db"))
        fresh = not db_path.exists()
        _conn = data.connect(db_path)
        if fresh:
            data.seed(_conn)
    return _conn


def tool(fn):
    """Register a tool and turn expected input errors into messages the model can read.

    The SDK hides the text of unexpected exceptions from the client. A FleetError is
    an anticipated failure with a helpful message, so it is re-raised as ToolError.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except store.FleetError as exc:
            raise ToolError(str(exc)) from exc

    return mcp.tool()(wrapper)


@tool
def list_vehicles(depot: str | None = None) -> list[dict]:
    """List vehicles in the fleet, optionally filtered by depot (Ottawa, Toronto, Montreal)."""
    return store.list_vehicles(get_conn(), depot)


@tool
def vehicle_summary(vehicle_id: str, days: int = 7) -> dict:
    """Usage summary for one vehicle over the last `days` days: trips, distance, idle time,
    fuel, fuel economy, top speed and number of active fault codes."""
    return store.vehicle_summary(get_conn(), vehicle_id, days)


@tool
def idling_report(days: int = 7, limit: int = 5, fuel_price: float = 1.60) -> dict:
    """Rank vehicles by engine idle time over the last `days` days and estimate what that
    idling cost in fuel. Use this for questions about waste, fuel savings or idle outliers.
    `fuel_price` is dollars per litre."""
    return store.idling_report(get_conn(), days, limit, fuel_price)


@tool
def active_faults(severity: str | None = None) -> list[dict]:
    """Active engine fault codes across the fleet, most severe first. Optionally filter by
    severity: low, medium or high. Use this for maintenance triage."""
    return store.active_faults(get_conn(), severity)


@tool
def driver_safety_scores(days: int = 30) -> list[dict]:
    """Driver safety scores (0-100, higher is safer) from harsh-driving and speeding events
    per 100 km over the last `days` days. Returned lowest score first, so the drivers who
    most need coaching come first."""
    return store.driver_safety_scores(get_conn(), days)


@tool
def recent_trips(vehicle_id: str, limit: int = 10) -> list[dict]:
    """Most recent trips for one vehicle, newest first, with driver, distance, idle minutes,
    fuel and top speed."""
    return store.recent_trips(get_conn(), vehicle_id, limit)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

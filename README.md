# fleet-mcp-server

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant answer fleet
telematics questions in plain language: which vehicles idle the most and what it costs,
which engine faults need attention first, and which drivers need coaching.

Ask Claude *"Which three vehicles wasted the most fuel idling last week, and what did it
cost us?"* and it calls `idling_report`, gets structured data back, and answers with numbers.

> All data is **simulated**. This is a personal project showing how I would put a
> natural-language layer over telematics data for a customer. It contains no employer or
> customer data.

## Why I built it

In solutions engineering, the same fleet questions come up in almost every customer
conversation, and the answer usually means someone building a report. MCP turns those
questions into tool calls an assistant can make on its own. This repo is the smallest
complete version of that pattern: a data layer, a tool layer, tests, and a client.

## Tools

| Tool | What it answers |
| --- | --- |
| `list_vehicles` | What is in the fleet, optionally by depot |
| `vehicle_summary` | Trips, distance, idle time, fuel economy and active faults for one vehicle |
| `idling_report` | Idle-time ranking with the estimated fuel cost of that idling |
| `active_faults` | Active engine fault codes, most severe first |
| `driver_safety_scores` | 0-100 score per driver from harsh-driving events per 100 km |
| `recent_trips` | Latest trips for one vehicle |

## Design

```
Claude / any MCP client
        │  stdio (JSON-RPC)
        ▼
server.py   thin tool layer: descriptions written for the model, input validation
        ▼
store.py    plain functions returning dicts: all business logic, unit-tested without MCP
        ▼
data.py     SQLite schema + seeded synthetic fleet (same seed, same fleet)
```

Decisions worth calling out:

- **Logic is separate from the protocol.** `store.py` knows nothing about MCP, so swapping
  SQLite for a live telematics API means replacing one module.
- **Errors tell the model what to do next.** An unknown vehicle id returns
  *"Call list_vehicles to see valid ids"*, so the assistant recovers without the user.
- **Assumptions are returned with the answer.** `idling_report` includes the litres-per-hour
  and fuel price it used, so the number can be challenged.
- **The data has a story.** Every fifth vehicle is seeded as a heavy idler and each driver
  has a stable risk profile, so the tools have real outliers to find.
- **Read-only.** No tool writes to the database.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                        # unit tests + an end-to-end test over stdio
python examples/demo_client.py   # call the tools with no LLM involved
```

The database is created and seeded on first run. To regenerate it:
`fleet-mcp-seed --db fleet.db --vehicles 15 --days 30 --seed 42`.

### Use it from Claude Desktop

Add this to `claude_desktop_config.json`, with the path to your virtualenv's Python:

```json
{
  "mcpServers": {
    "fleet": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["-m", "fleet_mcp.server"],
      "env": { "FLEET_DB": "/absolute/path/to/fleet.db" }
    }
  }
}
```

### Use it from Claude Code

```bash
claude mcp add fleet -- /absolute/path/to/.venv/bin/python -m fleet_mcp.server
```

## Questions to try

- Which vehicles idle the most, and what is that costing at $1.75 a litre?
- What should maintenance look at first this week?
- Which drivers in the Toronto depot need coaching, and why?
- Compare V005 with the rest of its depot.

## Limits and next steps

- Data is synthetic and time is frozen at a fixed reference date so results are reproducible.
- The safety score is a simple, explainable rule, not an industry standard.
- Next: a live-API adapter behind the `store` interface, and HTTP transport with auth for
  multi-user deployments.

## License

MIT

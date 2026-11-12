"""End-to-end test: start the real server over stdio and call it as an MCP client would."""

import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_server_lists_and_calls_tools_over_stdio(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "fleet_mcp.server"],
        env={"FLEET_DB": str(tmp_path / "fleet.db"), "PATH": ""},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {t.name for t in (await session.list_tools()).tools}
            assert tools == {
                "list_vehicles", "vehicle_summary", "idling_report",
                "active_faults", "driver_safety_scores", "recent_trips",
            }

            result = await session.call_tool("idling_report", {"days": 7, "limit": 2})
            assert not result.is_error
            report = json.loads(result.content[0].text)
            assert len(report["top_idlers"]) == 2

            bad = await session.call_tool("vehicle_summary", {"vehicle_id": "NOPE"})
            assert bad.is_error
            assert "list_vehicles" in bad.content[0].text

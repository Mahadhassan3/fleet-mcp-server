"""Call the server from a plain MCP client, no LLM required.

    python examples/demo_client.py
"""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "fleet_mcp.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Tools:", ", ".join(t.name for t in (await session.list_tools()).tools))
            for name, args in [
                ("idling_report", {"days": 7, "limit": 3}),
                ("active_faults", {"severity": "high"}),
            ]:
                result = await session.call_tool(name, args)
                print(f"\n== {name} {args}")
                # A list result arrives as one content block per item.
                items = [json.loads(block.text) for block in result.content]
                payload = items[0] if len(items) == 1 else items
                print(json.dumps(payload, indent=2)[:900])


if __name__ == "__main__":
    asyncio.run(main())

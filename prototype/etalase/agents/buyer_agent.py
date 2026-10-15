"""Demo buyer agent: a Gemini shopping assistant that buys through Etalase's MCP server.

This stands in for Gemini / AI Mode while UCP checkout is not yet live in Indonesia. It uses
only the public MCP tools, exactly like any third-party agent would.

    python -m etalase.agents.buyer_agent "Cari hijab voal hitam di bawah 100 ribu, kirim ke Bandung 40115"
"""

from __future__ import annotations

import asyncio
import os
import sys

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.genai import types

INSTRUCTION = """You are a shopping assistant for a buyer in Indonesia.
Use the Etalase tools to find products, quote shipping and check out.
- Ask for name, phone, address and postal code before checkout if you do not have them.
- Never create a checkout until the buyer clearly says to buy.
- After checkout, show the total and the QRIS payment string, and explain the next step from the tool result.
- If availability is 'confirm_with_seller', say the seller confirms stock first."""


def build_buyer_agent(mcp_url: str, model: str) -> LlmAgent:
    return LlmAgent(
        name="buyer_assistant",
        model=model,
        instruction=INSTRUCTION,
        tools=[McpToolset(connection_params=StreamableHTTPConnectionParams(url=mcp_url))],
    )


async def chat(prompts: list[str]) -> None:
    from ..config import export_google_env, get_settings

    settings = get_settings()  # reads .env, so the demo works with the same file as the server
    export_google_env(settings)
    mcp_url = os.getenv("ETALASE_MCP_URL", "http://localhost:8080/mcp/")
    model = os.getenv("GEMINI_MODEL", settings.gemini_model)
    agent = build_buyer_agent(mcp_url, model)
    sessions = InMemorySessionService()
    runner = Runner(agent=agent, app_name="etalase-buyer", session_service=sessions)
    await sessions.create_session(app_name="etalase-buyer", user_id="buyer", session_id="demo")
    for prompt in prompts:
        print(f"\nBuyer: {prompt}")
        async for event in runner.run_async(user_id="buyer", session_id="demo",
                                            new_message=types.Content(role="user", parts=[types.Part(text=prompt)])):
            if event.is_final_response() and event.content and event.content.parts:
                print("Agent:", "".join(p.text or "" for p in event.content.parts))


if __name__ == "__main__":
    asyncio.run(chat(sys.argv[1:] or ["Find a black voal hijab under Rp100k to ship to Bandung 40115"]))

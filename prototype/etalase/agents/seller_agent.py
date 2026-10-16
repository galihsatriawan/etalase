"""WhatsApp seller assistant built with Google's Agent Development Kit (ADK).

One conversational agent with tools. The seller's identity comes from the verified
WhatsApp number and lives in session state; it is never a tool argument the model can
change, so every tool call is scoped to that seller (tenant isolation).

Deterministic actions (buttons such as Accept / Out of stock, payment and courier
webhooks) never go through the model; see seller/inbox.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import ToolContext
from google.genai import types

from ..catalog.normalize import canonical_color
from ..models import CheckoutError
from ..seller.inbox import sales_summary
from ..seller.format import rupiah

if TYPE_CHECKING:
    from ..container import AppContext

APP_NAME = "etalase-seller"

INSTRUCTION = """You are the shop assistant for a small online seller in Indonesia who sells through WhatsApp.
Reply briefly and warmly, in the same language the seller writes in (usually casual Bahasa Indonesia).
- New products (a description, price or stock, or right after photos / a voice note): call build_catalog.
- Stock or price changes: call update_stock / update_price. If the product or colour is ambiguous, ask first; never guess.
- A courier tracking number: call submit_tracking.
- Questions about sales or payouts: call get_sales_summary.
- Return terms: call set_return_policy. Damaged, wrong and not-as-described items are always returnable; the seller only
  chooses whether to also accept a change of mind, and may add a short note for buyers.
- Never change payout bank details or identity data in chat; point the seller to the dashboard.
- Never invent numbers. Use tool results."""


def build_seller_agent(ctx: "AppContext") -> LlmAgent:
    def _seller(tool_context: ToolContext) -> str:
        return tool_context.state["seller_id"]

    def build_catalog(description: str, tool_context: ToolContext) -> dict:
        """Create draft products from the seller's description plus any photos or voice notes they just sent.
        The seller then gets a summary with Publish / Edit buttons."""
        drafts = ctx.inbox.create_catalog(_seller(tool_context), [description])
        return {"drafts": [{"title": p.title, "readiness": p.readiness_score, "missing": p.readiness_missing}
                           for p in drafts],
                "note": "A summary with Publish/Edit buttons was already sent to the seller."}

    def update_stock(color: str, quantity: int, mode: str, tool_context: ToolContext,
                     product_name: str | None = None) -> dict:
        """Set or add stock for a colour variant. mode is 'set' (stock is now N) or 'add' (restock N more)."""
        canon = canonical_color(color) or color.title()
        try:
            changes = ctx.catalog.update_stock(_seller(tool_context), canon, quantity,
                                               mode="add" if mode == "add" else "set", hint=product_name)
        except PermissionError as e:
            return {"error": str(e)}
        return {"updated": [{"before": c.before, "after": c.after} for c in changes]} if changes \
            else {"error": f"no variant '{canon}' found"}

    def update_price(product_name: str, price_idr: int, tool_context: ToolContext) -> dict:
        """Change the price (IDR) of a product."""
        changed = ctx.catalog.update_price(_seller(tool_context), product_name, price_idr)
        return {"updated": [p.title for p in changed], "price": rupiah(price_idr)} if changed \
            else {"error": "product not found"}

    def submit_tracking(waybill: str, tool_context: ToolContext, order_id: str | None = None) -> dict:
        """Save the courier tracking number for the seller's oldest accepted order (or the given order)."""
        try:
            order = ctx.checkout.mark_shipped(_seller(tool_context), order_id, waybill)
        except CheckoutError as e:
            return {"error": str(e)}
        return {"order_id": order.id, "status": order.status.value, "waybill": waybill}

    def get_sales_summary(tool_context: ToolContext) -> dict:
        """Orders, revenue and payouts for this seller."""
        return {"summary": sales_summary(ctx, _seller(tool_context))}

    def set_return_policy(accepts_change_of_mind: bool, tool_context: ToolContext, note: str | None = None) -> dict:
        """Set whether buyers may return items just because they changed their mind, and an optional short note shown to
        buyers' agents. Damaged, wrong and not-as-described items stay returnable either way."""
        policy = ctx.returns.set_policy(_seller(tool_context), accepts_change_of_mind, note)
        return {"accepts_change_of_mind": policy.accepts_change_of_mind, "note": policy.note}

    return LlmAgent(
        name="seller_assistant",
        model=ctx.settings.gemini_model,
        description="Helps a WhatsApp seller manage catalog, stock, orders and shipping.",
        instruction=INSTRUCTION,
        tools=[build_catalog, update_stock, update_price, submit_tracking, get_sales_summary, set_return_policy],
    )


class SellerAgentRunner:
    def __init__(self, ctx: "AppContext") -> None:
        self.agent = build_seller_agent(ctx)
        self.sessions = InMemorySessionService()  # production: a persistent session service
        self.runner = Runner(agent=self.agent, app_name=APP_NAME, session_service=self.sessions)

    async def respond(self, seller_id: str, text: str) -> str:
        session = await self.sessions.get_session(app_name=APP_NAME, user_id=seller_id, session_id=seller_id)
        if session is None:
            await self.sessions.create_session(app_name=APP_NAME, user_id=seller_id, session_id=seller_id,
                                               state={"seller_id": seller_id})
        message = types.Content(role="user", parts=[types.Part(text=text)])
        reply = ""
        async for event in self.runner.run_async(user_id=seller_id, session_id=seller_id, new_message=message):
            if event.is_final_response() and event.content and event.content.parts:
                reply = "".join(p.text or "" for p in event.content.parts)
        return reply.strip()

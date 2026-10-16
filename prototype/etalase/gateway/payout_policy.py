"""The payout hold, published: when a seller is paid, why it waits, what can pause it and who decides a dispute.

    GET /policy/payouts        for people (sellers and buyers)
    GET /policy/payouts.json   the same terms for an agent

Every number is read from the settings the code itself runs on, so this page cannot say one thing while the money does another.
It describes the rule; it does not promise anything the code does not do. Two parts depend on people rather than code and
are therefore stated plainly as such: an operator decides a dispute, and a second look at that decision exists only when
SUPPORT_CONTACT is configured (otherwise the page does not offer one).

The wording avoids "rekber" and "escrow" on purpose: Etalase does not ask anyone to trust it with their money for a deal
between two parties. It pays a seller after a fixed, published window, and the window is the buyer's chance to return a
faulty item, so a refund never has to be taken back from a seller (docs/adr/0007).
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from fastapi.responses import HTMLResponse

from . import pages

if TYPE_CHECKING:
    from ..container import AppContext

VERSION = "2026-10-16"
CSP = pages.csp()


def terms(ctx: "AppContext") -> dict:
    """The numbers, as the code uses them."""
    s = ctx.settings
    window, new_window = s.payout_hold_hours, max(s.payout_hold_hours, s.new_seller_hold_hours)
    answer, lapse = s.return_seller_response_hours, s.return_lapse_days
    return {
        "version": VERSION,
        "hold_enabled": window > 0,
        "window_hours": window,
        "window_hours_new_seller": new_window,
        "new_seller_means_fewer_than_completed_orders": s.new_seller_orders,
        "window_starts": "when the courier itself confirms delivery",
        "paid_when": "automatically when the window ends and no return is open",
        "commission_taken": "only when the payout is released; an order that is refunded earns no commission",
        "seller_answers_within_hours": answer,
        "silence_counts_as": "approval",
        "approved_return_must_reach_seller_within_days": lapse,
        # The longest an order can wait after delivery without an operator: the window, the seller's answer time, the way back.
        "longest_wait_without_dispute_hours": new_window + answer + lapse * 24,
        "who_decides_a_dispute": "an Etalase operator, who either refunds the buyer or releases the payout",
        "appeal": ({"contact": s.support_contact, "within_days": s.dispute_appeal_days, "reviewed_by": "a different team member",
                    "second_decision_is": "final within Etalase"} if s.support_contact else None),
        "terms_are_fixed_per_order": "the window shown at checkout is the window for that order, even if this rule changes later",
    }


def _days(hours: int) -> str:
    return pages.window(hours)


def page(ctx: "AppContext") -> HTMLResponse:
    t = terms(ctx)
    if not t["hold_enabled"]:
        body = pages.status("info", "Payouts are released right after delivery",
                            "<p>This deployment has no hold and no return window. Check each item carefully before you order.</p>", icon="alert")
        return _respond(_wrap(body))
    normal, new = t["window_hours"], t["window_hours_new_seller"]
    longest = t["longest_wait_without_dispute_hours"]
    lead = pages.status("ok", f"Sellers are paid {_days(normal)} after delivery",
                        f"<p>{_days(new)} while a seller is new. The wait is the buyer's chance to return a faulty item, "
                        "and it is the same for every seller and every order.</p>", icon="shield")
    steps = (
        '<section class=band><h2 class=cap>From order to payout</h2><ol class=num>'
        "<li>The buyer pays by QRIS. The order is shown to the seller on WhatsApp or Telegram.</li>"
        "<li>The seller ships. Etalase asks the courier itself before it believes any delivery update, so a typed tracking number "
        "or a forged message cannot start the clock.</li>"
        f"<li>The courier confirms delivery. The return window starts: <b>{_days(normal)}</b>, or <b>{_days(new)}</b> for a seller with "
        f"fewer than {t['new_seller_means_fewer_than_completed_orders']} completed orders. The buyer sees the exact length at checkout, "
        "and that length stays fixed for the order.</li>"
        "<li>The window ends with no return open. The payout is released automatically; nobody has to ask, and Etalase does not hold it "
        "back for any other reason. Etalase takes its commission at this moment only. An order that is refunded earns Etalase nothing.</li>"
        f"{_provider_note(ctx)}"
        "</ol></section>")
    pauses = (
        '<section class=band><h2 class=cap>What can pause a payout</h2><ul class=checks>'
        f"<li>{pages.ico('check')}<span><b>The parcel has not been delivered.</b> No delivery, no payout. A parcel the courier brings back "
        "or loses is refunded to the buyer in full.</span></li>"
        f"<li>{pages.ico('check')}<span><b>A return is open.</b> The seller answers within {t['seller_answers_within_hours']} hours; "
        f"silence counts as approval. An approved item must reach the seller within {t['approved_return_must_reach_seller_within_days']} days, "
        "or the return lapses and the seller is paid.</span></li>"
        f"<li>{pages.ico('check')}<span><b>The seller disputes a return.</b> The money stays frozen until an operator decides. "
        "The same happens when a buyer reports a tracking number for the way back.</span></li>"
        "</ul>"
        f"<p class=fine style='margin-top:.75rem'>Without a dispute the longest a payout can wait after delivery is "
        f"{_days(longest)}. Nothing else pauses it.</p></section>")
    decide = ('<section class=band><h2 class=cap>Who decides a dispute</h2>'
              "<p>An Etalase operator, not the seller and not the buyer. The operator reads the buyer's note and photo links and the seller's "
              "reason, then does one of two things: refunds the buyer, or releases the payout to the seller. Etalase does not keep the "
              "money for any other purpose.</p></section>")
    appeal = ""
    if t["appeal"]:
        a = t["appeal"]
        appeal = (f'<section class=band><h2 class=cap>If you disagree with the decision</h2>'
                  f"<p>Write to <b>{escape(a['contact'])}</b> within {a['within_days']} days of the decision and quote the order number. "
                  "A different team member reads the case again. That second decision is final within Etalase. It does not take away any "
                  "right you have under Indonesian consumer law or with your payment provider.</p></section>")
    fixed = (f'<p class=fine>Orders keep the terms they were placed under. Version of {VERSION}. Seller-specific return terms are on each '
             'store\'s return policy page.</p>')
    return _respond(_wrap(f"{lead}{steps}{pauses}{decide}{appeal}{fixed}"))


def _provider_note(ctx: "AppContext") -> str:
    """Be straight about the pilot: until the payment provider's settlement is automated, an operator sends the release."""
    if getattr(ctx.payment, "name", "") == "mock":
        return ""
    return ("<li>The money reaches the seller's account after the payment provider's own settlement time, which Etalase does not control. "
            "While settlement is not automated, an operator sends each release by hand and the order shows the payout as pending.</li>")


def _wrap(body: str) -> str:
    return pages.layout("How payouts are held", f"<div class=stack>{body}</div>", h1="How payouts are held", index=True)


def _respond(html: str) -> HTMLResponse:
    return HTMLResponse(html, headers={"Content-Security-Policy": CSP, "Cache-Control": "public, max-age=300"})

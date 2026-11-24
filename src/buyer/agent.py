"""The buying agent: a small, explicit tool-use loop.

The model decides *which* offers to book and how to split each line's budget.
It cannot set prices, exceed budgets, book an excluded publisher, use a
disallowed audience provider, or approve anything: those are enforced in
`OrderBook.draft` and by simply not offering an approve tool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from buyer.handoff import PlanHandoff, _norm
from buyer.model import ModelCallError, ModelClient, ToolCall
from buyer.orders import OrderBook, OrderError
from buyer.sellers import SellerNetwork, SellerUnavailable

MAX_TURNS = 20

SYSTEM = """\
You are a media buyer executing a streaming-TV media plan that a planner has already \
confirmed with the advertiser. Your job is to book each plan line with its publisher by \
drafting orders against the seller's offers.

How to work:
- Start with get_plan, then list_offers for each line's publisher.
- For each line, choose offers and split that line's budget across them. Prefer offers that \
meet the advertiser's commitments (matching publisher and deal type), then lower CPM, then \
the deal type best suited to the line.
- draft_order checks every hard rule in code. If it rejects a draft, read the reasons and \
adjust; do not resubmit the same draft.
- A line's budget cannot move to another line. If a line cannot be fully booked, book what \
you can and explain the shortfall.
- If a seller is unavailable, say so and move on; a person will follow up.
- Finish with review_book, then write a short summary for the person who approves the \
orders: what you booked per line, any shortfalls or unmet commitments, and anything they \
should decide. You cannot approve or submit orders; a person does that after reading your \
summary.
"""


def _tool(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


TOOLS = [
    _tool("get_plan", "The confirmed plan: flight, lines with budgets and CPM ceilings, "
          "constraints, and commitments.", {}),
    _tool("list_offers", "Offers a publisher's seller has for this flight, with notes on "
          "which plan rules each offer would break.",
          {"publisher": {"type": "string", "description": "Publisher name exactly as in the plan."}}),
    _tool("draft_order", "Propose a draft order on one offer for one plan line. The price and "
          "impressions come from the seller; you choose how much of the line's budget to spend.",
          {
              "line_id": {"type": "string"},
              "offer_id": {"type": "string"},
              "spend_usd": {"type": "number", "description": "Dollars to spend on this offer."},
              "rationale": {"type": "string", "description": "One sentence on why this offer."},
          }),
    _tool("cancel_order", "Cancel one of your draft orders.", {"order_id": {"type": "string"}}),
    _tool("review_book", "Booked vs. budget per line, commitment status, and warnings.", {}),
]


@dataclass
class Event:
    tool: str
    input: dict[str, Any]
    ok: bool
    result: Any


@dataclass
class BuyResult:
    book: OrderBook
    summary: str
    events: list[Event] = field(default_factory=list)
    turns: int = 0
    error: str | None = None


class BuyingAgent:
    def __init__(self, handoff: PlanHandoff, sellers: SellerNetwork, model: ModelClient,
                 max_turns: int = MAX_TURNS):
        self.handoff = handoff
        self.sellers = sellers
        self.model = model
        self.max_turns = max_turns
        self.book = OrderBook(handoff)

    # ---- tools -----------------------------------------------------------

    def _plan_view(self) -> dict[str, Any]:
        h = self.handoff
        return {
            "plan_id": h.plan_id,
            "advertiser": h.advertiser,
            "flight": h.flight.to_dict(),
            "total_budget_usd": h.total_budget_usd,
            "lines": [{"line_id": ln.line_id, "publisher": ln.publisher, "kind": ln.kind,
                       "budget_usd": ln.budget_usd, "target_cpm": ln.target_cpm,
                       "max_cpm": h.max_cpm(ln)} for ln in h.lines],
            "constraints": {
                "excluded_publishers": list(h.constraints.excluded_publishers),
                "avoid_publishers": list(h.constraints.avoid_publishers),
                "audience_providers": (None if h.constraints.audience_providers is None
                                       else list(h.constraints.audience_providers)),
                "cpm_tolerance_pct": h.constraints.cpm_tolerance_pct,
            },
            "commitments": [{"id": c.id, "publisher": c.publisher, "deal_type": c.deal_type,
                             "min_spend_usd": c.min_spend_usd, "flight": c.flight.to_dict()}
                            for c in h.commitments if c.flight.overlap(h.flight)],
        }

    def _offers_view(self, publisher: str) -> dict[str, Any]:
        h = self.handoff
        lines = [ln for ln in h.lines if _norm(ln.publisher) == _norm(publisher)]
        offers = []
        for o in self.sellers.list_offers(publisher):
            notes = []
            if h.constraints.is_excluded(o.publisher):
                notes.append("publisher is hard-excluded")
            if not h.constraints.provider_allowed(o.audience_provider):
                notes.append(f"audience provider {o.audience_provider} not allowed")
            for ln in lines:
                if o.cpm > h.max_cpm(ln):
                    notes.append(f"CPM above {ln.line_id} ceiling ${h.max_cpm(ln):.2f}")
            left = o.avails - self.book.impressions_on_offer(o.offer_id)
            offers.append({**o.to_dict(), "avails_left": left,
                           "max_spend_usd": round(left * o.cpm / 1000, 2),
                           "rule_notes": notes or ["ok"]})
        return {"publisher": publisher, "plan_lines": [ln.line_id for ln in lines], "offers": offers}

    def _run_tool(self, call: ToolCall) -> tuple[bool, Any]:
        args = call.input
        try:
            if call.name == "get_plan":
                return True, self._plan_view()
            if call.name == "list_offers":
                return True, self._offers_view(str(args["publisher"]))
            if call.name == "draft_order":
                offer = self.sellers.get_offer(str(args["offer_id"]))
                order, warnings = self.book.draft(str(args["line_id"]), offer,
                                                  float(args["spend_usd"]), str(args.get("rationale", "")))
                return True, {"order": order.to_dict(), "warnings": warnings,
                              "line_remaining_usd": round(
                                  self.handoff.line(order.line_id).budget_usd
                                  - self.book.booked_for_line(order.line_id), 2)}
            if call.name == "cancel_order":
                return True, {"order": self.book.cancel(str(args["order_id"])).to_dict()}
            if call.name == "review_book":
                return True, self.book.review()
            return False, {"error": f"unknown tool {call.name}"}
        except OrderError as err:
            return False, {"rejected": True, "violations": err.violations}
        except SellerUnavailable as err:
            return False, {"error": f"seller unavailable: {err}. Note it for the approver and move on."}
        except (KeyError, TypeError, ValueError) as err:
            return False, {"error": f"bad arguments for {call.name}: {err}"}

    # ---- loop ------------------------------------------------------------

    def run(self) -> BuyResult:
        h = self.handoff
        messages: list[dict[str, Any]] = [{
            "role": "user",
            "content": f"Book plan {h.plan_id} for {h.advertiser} "
                       f"(${h.total_budget_usd:,}, {h.flight.start} to {h.flight.end}).",
        }]
        result = BuyResult(book=self.book, summary="")
        for turn in range(1, self.max_turns + 1):
            result.turns = turn
            try:
                step = self.model.step(SYSTEM, messages, TOOLS)
            except ModelCallError as err:
                result.error = str(err)
                return result
            messages.append({"role": "assistant", "content": step.content})
            if step.stop_reason == "pause_turn":
                continue
            if not step.tool_calls:
                result.summary = step.text
                return result
            tool_results = []
            for call in step.tool_calls:
                ok, payload = self._run_tool(call)
                result.events.append(Event(call.name, call.input, ok, payload))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": json.dumps(payload),
                    **({} if ok else {"is_error": True}),
                })
            messages.append({"role": "user", "content": tool_results})
        result.error = f"stopped after {self.max_turns} turns without a final summary"
        return result

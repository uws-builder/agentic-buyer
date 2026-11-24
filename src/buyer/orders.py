"""Order book and the guardrails around it.

Same rule as the planner: the model reads, the harness enforces. The agent can
only *propose* draft orders. Every draft is checked here, in plain code, against
the plan handoff, using prices and avails read from the seller, never numbers
the model supplies. Approval is a separate, human-only step; nothing the agent
can call moves an order past `drafted`.

Lifecycle:  drafted -> approved -> submitted | failed
            drafted -> cancelled
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from buyer.handoff import Flight, PlanHandoff, _norm
from buyer.sellers import Offer, SellerNetwork, SellerRejected, SellerUnavailable

UNDERBOOK_WARN_RATIO = 0.95  # warn when a line books less than this share of its budget
# Orders buy whole impressions, so spend can land a few cents under the dollar amount asked for.
ROUNDING_TOLERANCE_USD = 1.0


class OrderError(ValueError):
    """A draft broke a hard rule. `violations` lists every rule it broke."""

    def __init__(self, violations: list[str]):
        super().__init__("; ".join(violations))
        self.violations = violations


@dataclass
class Order:
    order_id: str
    line_id: str
    offer_id: str
    publisher: str
    deal_type: str
    cpm: float
    impressions: int
    spend_usd: float
    flight: Flight
    rationale: str
    status: str = "drafted"
    approved_by: str | None = None
    seller_order_id: str | None = None
    failure: str | None = None

    @property
    def active(self) -> bool:
        return self.status in ("drafted", "approved", "submitted")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["flight"] = self.flight.to_dict()
        return d


@dataclass
class OrderBook:
    handoff: PlanHandoff
    orders: list[Order] = field(default_factory=list)

    # ---- queries -------------------------------------------------------

    def active(self) -> list[Order]:
        return [o for o in self.orders if o.active]

    def booked_for_line(self, line_id: str) -> float:
        return round(sum(o.spend_usd for o in self.active() if o.line_id == line_id), 2)

    def booked_total(self) -> float:
        return round(sum(o.spend_usd for o in self.active()), 2)

    def impressions_on_offer(self, offer_id: str) -> int:
        return sum(o.impressions for o in self.active() if o.offer_id == offer_id)

    def get(self, order_id: str) -> Order | None:
        return next((o for o in self.orders if o.order_id == order_id), None)

    # ---- agent-callable --------------------------------------------------

    def draft(self, line_id: str, offer: Offer | None, spend_usd: float, rationale: str) -> tuple[Order, list[str]]:
        """Validate and add a draft. Raises OrderError on any hard-rule break.
        Returns the order and any soft warnings."""
        h = self.handoff
        line = h.line(line_id)
        v: list[str] = []
        if line is None:
            raise OrderError([f"no plan line {line_id!r}; lines are {[ln.line_id for ln in h.lines]}"])
        if offer is None:
            raise OrderError(["unknown offer_id; use list_offers to see what the seller has"])

        if _norm(offer.publisher) != _norm(line.publisher):
            v.append(f"offer {offer.offer_id} is from {offer.publisher}, but line {line_id} is {line.publisher}")
        if h.constraints.is_excluded(offer.publisher):
            v.append(f"{offer.publisher} is a hard-excluded publisher")
        if not h.constraints.provider_allowed(offer.audience_provider):
            allowed = ", ".join(h.constraints.audience_providers or ())
            v.append(f"audience provider {offer.audience_provider!r} is not allowed (allowed: {allowed})")
        ceiling = h.max_cpm(line)
        if offer.cpm > ceiling:
            v.append(f"CPM ${offer.cpm:.2f} is above line {line_id}'s ceiling ${ceiling:.2f} "
                     f"(target ${line.target_cpm:.2f} + {h.constraints.cpm_tolerance_pct:g}%)")
        if offer.flight.start < h.flight.start or offer.flight.end > h.flight.end:
            v.append(f"offer runs {offer.flight.start}..{offer.flight.end}, outside the plan flight "
                     f"{h.flight.start}..{h.flight.end}")

        impressions = math.floor(spend_usd * 1000 / offer.cpm) if spend_usd > 0 else 0
        cost = round(impressions * offer.cpm / 1000, 2)
        if impressions <= 0:
            v.append("spend_usd must be large enough to buy at least one impression")
        line_left = round(line.budget_usd - self.booked_for_line(line_id), 2)
        if cost > line_left:
            v.append(f"${cost:,.2f} exceeds line {line_id}'s remaining budget ${line_left:,.2f}")
        total_left = round(h.total_budget_usd - self.booked_total(), 2)
        if cost > total_left:
            v.append(f"${cost:,.2f} exceeds the plan's remaining budget ${total_left:,.2f}")
        avails_left = offer.avails - self.impressions_on_offer(offer.offer_id)
        if impressions > avails_left:
            max_spend = round(avails_left * offer.cpm / 1000, 2)
            v.append(f"{impressions:,} impressions exceeds {offer.offer_id}'s remaining avails "
                     f"{avails_left:,} (max spend ${max_spend:,.2f})")
        if v:
            raise OrderError(v)

        warnings = []
        if h.constraints.is_avoided(offer.publisher):
            warnings.append(f"{offer.publisher} is on the advertiser's avoid list (soft rule)")

        order = Order(
            order_id=f"ORD-{len(self.orders) + 1:03d}",
            line_id=line_id,
            offer_id=offer.offer_id,
            publisher=offer.publisher,
            deal_type=offer.deal_type,
            cpm=offer.cpm,
            impressions=impressions,
            spend_usd=cost,
            flight=offer.flight,
            rationale=rationale.strip(),
        )
        self.orders.append(order)
        return order, warnings

    def cancel(self, order_id: str) -> Order:
        order = self.get(order_id)
        if order is None:
            raise OrderError([f"no order {order_id!r}"])
        if order.status != "drafted":
            raise OrderError([f"{order_id} is {order.status}; only drafts can be cancelled"])
        order.status = "cancelled"
        return order

    def review(self) -> dict[str, Any]:
        """Where the book stands against the plan, plus soft warnings."""
        h = self.handoff
        lines = []
        warnings = []
        for ln in h.lines:
            booked = self.booked_for_line(ln.line_id)
            lines.append({
                "line_id": ln.line_id,
                "publisher": ln.publisher,
                "budget_usd": ln.budget_usd,
                "booked_usd": booked,
                "remaining_usd": round(ln.budget_usd - booked, 2),
                "max_cpm": h.max_cpm(ln),
            })
            if booked < ln.budget_usd * UNDERBOOK_WARN_RATIO:
                warnings.append(f"{ln.line_id} {ln.publisher}: booked ${booked:,.2f} of ${ln.budget_usd:,} "
                                f"(short ${ln.budget_usd - booked:,.2f})")
        commitments = []
        for cm in h.commitments:
            if cm.flight.overlap(h.flight) is None:
                continue  # outside this plan; not ours to meet
            met = round(sum(
                o.spend_usd for o in self.active()
                if _norm(o.publisher) == _norm(cm.publisher) and o.deal_type == cm.deal_type
            ), 2)
            commitments.append({"id": cm.id, "publisher": cm.publisher, "deal_type": cm.deal_type,
                                "min_spend_usd": cm.min_spend_usd, "booked_usd": met})
            if met < cm.min_spend_usd - ROUNDING_TOLERANCE_USD:
                warnings.append(f"commitment {cm.id} ({cm.publisher} {cm.deal_type}) short by "
                                f"${cm.min_spend_usd - met:,.2f}")
        return {
            "total_budget_usd": h.total_budget_usd,
            "booked_total_usd": self.booked_total(),
            "lines": lines,
            "commitments": commitments,
            "orders": [o.to_dict() for o in self.active()],
            "warnings": warnings,
        }

    # ---- human-only ------------------------------------------------------
    # Not exposed as agent tools. The CLI calls these after a person says yes.

    def approve(self, order_ids: list[str], approver: str) -> list[Order]:
        approved = []
        for oid in order_ids:
            order = self.get(oid)
            if order is None or order.status != "drafted":
                raise OrderError([f"{oid} is not a draft that can be approved"])
            order.status, order.approved_by = "approved", approver
            approved.append(order)
        return approved

    def submit_approved(self, sellers: SellerNetwork) -> list[Order]:
        sent = []
        for order in self.orders:
            if order.status != "approved":
                continue
            try:
                conf = sellers.submit(order.offer_id, order.impressions, ref=order.order_id)
            except (SellerRejected, SellerUnavailable) as err:
                order.status, order.failure = "failed", str(err)
            else:
                order.status, order.seller_order_id = "submitted", conf.seller_order_id
            sent.append(order)
        return sent

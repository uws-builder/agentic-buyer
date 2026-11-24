"""Scripted model turns for the Summit Ridge fixture: the free demo mode.

The script makes two mistakes on purpose (a cheap offer with a disallowed
audience provider, and a draft larger than the seller's avails) so the demo
shows the harness rejecting them and the agent recovering.
"""

from __future__ import annotations

from buyer.model import ScriptedClient

PUBLISHERS = ["Northlight Streaming", "Summit Network Apps", "TrailCast FAST",
              "Arena Live", "Shoppable CTV Exchange"]

SUMMARY = """\
Booked about $760,000 of $800,000 across 7 draft orders. Nothing is submitted yet.

- L1 Northlight Streaming: $220,000 booked. $150,000 is on the PG deal (NL-PG-01), which \
meets commitment cm_001. The remaining $70,000 is on the PMP (NL-PMP-02). I skipped the \
cheaper open-market offer because it uses Beacon Signals audience data.
- L2 Summit Network Apps: $110,000 of $150,000 booked on SNA-PMP-01, which is all of its \
avails. The only other offer (SNA-PG-02, $31 CPM) is above the $28.60 ceiling. \
**Decision needed:** accept the $40,000 shortfall, raise this line's CPM ceiling, or ask \
the planner to move the money.
- L3 TrailCast FAST: $130,000 booked. $80,000 is on the DIO (meets cm_002) and $50,000 is \
on the PMP.
- L4 Arena Live: $160,000 booked on AL-PG-01 at a $35 CPM, within the $37.40 ceiling.
- L5 Shoppable CTV Exchange: $140,000 booked on SCX-OPEN-01 at a $23 CPM.
"""


def summit_ridge_client() -> ScriptedClient:
    return ScriptedClient(turns=[
        [("get_plan", {})],
        [("list_offers", {"publisher": p}) for p in PUBLISHERS],
        [
            ("draft_order", {"line_id": "L1", "offer_id": "NL-OPEN-03", "spend_usd": 70000,
                             "rationale": "Lowest CPM on Northlight."}),
            ("draft_order", {"line_id": "L1", "offer_id": "NL-PG-01", "spend_usd": 150000,
                             "rationale": "Meets the Northlight PG commitment."}),
            ("draft_order", {"line_id": "L2", "offer_id": "SNA-PMP-01", "spend_usd": 150000,
                             "rationale": "Only Summit offer under the CPM ceiling."}),
            ("draft_order", {"line_id": "L3", "offer_id": "TC-DIO-01", "spend_usd": 80000,
                             "rationale": "Meets the TrailCast DIO commitment."}),
            ("draft_order", {"line_id": "L4", "offer_id": "AL-PG-01", "spend_usd": 160000,
                             "rationale": "Only Arena Live offer; within ceiling."}),
            ("draft_order", {"line_id": "L5", "offer_id": "SCX-OPEN-01", "spend_usd": 140000,
                             "rationale": "Lower CPM than the PMP and enough avails."}),
        ],
        [
            ("draft_order", {"line_id": "L1", "offer_id": "NL-PMP-02", "spend_usd": 70000,
                             "rationale": "Open offer uses a disallowed provider; PMP is next best."}),
            ("draft_order", {"line_id": "L2", "offer_id": "SNA-PMP-01", "spend_usd": 110000,
                             "rationale": "Book all remaining avails on the only eligible offer."}),
            ("draft_order", {"line_id": "L3", "offer_id": "TC-PMP-02", "spend_usd": 50000,
                             "rationale": "Fill the rest of the line outside the DIO window."}),
        ],
        [("review_book", {})],
        SUMMARY,
    ])

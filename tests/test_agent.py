import json

from buyer.agent import TOOLS, BuyingAgent
from buyer.demo import summit_ridge_client
from buyer.model import ModelCallError, ScriptedClient
from buyer.sellers import FakeSellers


def test_agent_has_no_way_to_approve_or_submit():
    names = {t["name"] for t in TOOLS}
    assert names == {"get_plan", "list_offers", "draft_order", "cancel_order", "review_book"}
    for t in TOOLS:
        assert t["strict"] is True
        assert t["input_schema"]["additionalProperties"] is False


def test_demo_books_plan_within_every_rule(handoff, sellers):
    result = BuyingAgent(handoff, sellers, summit_ridge_client()).run()
    assert result.error is None and result.summary
    orders = result.book.active()
    assert len(orders) == 7 and all(o.status == "drafted" for o in orders)
    for o in orders:
        line = handoff.line(o.line_id)
        assert o.publisher == line.publisher
        assert o.cpm <= handoff.max_cpm(line)
        assert not handoff.constraints.is_excluded(o.publisher)
    for ln in handoff.lines:
        assert result.book.booked_for_line(ln.line_id) <= ln.budget_usd
    assert result.book.review()["warnings"] == [
        "L2 Summit Network Apps: booked $110,000.00 of $150,000 (short $40,000.00)"]
    rejected = [e for e in result.events if not e.ok]
    assert [e.input["offer_id"] for e in rejected] == ["NL-OPEN-03", "SNA-PMP-01"]
    assert sellers.submissions == []  # the agent never submits


def test_rejections_go_back_to_the_model_as_errors(handoff, sellers):
    client = summit_ridge_client()
    BuyingAgent(handoff, sellers, client).run()
    results = client.calls[3]["last"]["content"]  # tool results from the draft turn
    first = results[0]
    assert first["is_error"] is True
    assert "Beacon Signals" in json.loads(first["content"])["violations"][0]
    assert "is_error" not in results[1]


def test_seller_outage_is_reported_and_the_loop_continues(handoff):
    client = ScriptedClient([
        [("list_offers", {"publisher": "Arena Live"}), ("list_offers", {"publisher": "TrailCast FAST"})],
        "Arena Live is down; nothing booked.",
    ])
    result = BuyingAgent(handoff, FakeSellers(down={"Arena Live"}), client).run()
    assert [e.ok for e in result.events] == [False, True]
    assert "seller unavailable" in result.events[0].result["error"]
    assert result.summary.startswith("Arena Live is down")


def test_unknown_tool_and_bad_arguments_are_errors_not_crashes(handoff, sellers):
    client = ScriptedClient([
        [("approve_order", {"order_id": "ORD-001"}), ("draft_order", {"line_id": "L1"})],
        "done",
    ])
    result = BuyingAgent(handoff, sellers, client).run()
    assert [e.ok for e in result.events] == [False, False]
    assert "unknown tool" in result.events[0].result["error"]
    assert "bad arguments" in result.events[1].result["error"]


def test_stops_after_max_turns(handoff, sellers):
    client = ScriptedClient([[("review_book", {})]] * 5)
    result = BuyingAgent(handoff, sellers, client, max_turns=3).run()
    assert result.turns == 3 and "stopped after 3 turns" in result.error


def test_model_failure_is_recorded(handoff, sellers):
    class Broken:
        def step(self, *a):
            raise ModelCallError("model declined the request (None)")

    result = BuyingAgent(handoff, sellers, Broken()).run()
    assert result.error.startswith("model declined")


def test_books_a_plan_exported_by_the_planner(sellers):
    """The planner's flight ends 08-22, before most offers do; orders run on the shared days."""
    from conftest import ROOT
    from buyer.handoff import PlanHandoff

    h = PlanHandoff.load(ROOT / "fixtures" / "plans" / "from_planner_summit_ridge.json")
    picks = {"Northlight Streaming": "NL-PG-01", "Summit Network Apps": "SNA-PMP-01",
             "TrailCast FAST": "TC-DIO-01", "Arena Live": "AL-PG-01",
             "Shoppable CTV Exchange": "SCX-OPEN-01"}
    drafts = [("draft_order", {"line_id": ln.line_id, "offer_id": picks[ln.publisher],
                               "spend_usd": min(ln.budget_usd, 100_000), "rationale": "test"})
              for ln in h.lines]
    result = BuyingAgent(h, sellers, ScriptedClient([drafts, [("review_book", {})], "done"])).run()
    failed = [(e.input["offer_id"], e.result) for e in result.events if not e.ok]
    assert failed == []
    assert all(o.flight.end <= h.flight.end for o in result.book.active())

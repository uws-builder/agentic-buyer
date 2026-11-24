import pytest

from buyer.orders import OrderBook, OrderError
from buyer.sellers import FakeSellers


def draft(book, sellers, line, offer, spend):
    return book.draft(line, sellers.get_offer(offer), spend, "test")


def violations(book, sellers, line, offer, spend):
    with pytest.raises(OrderError) as exc:
        draft(book, sellers, line, offer, spend)
    return " | ".join(exc.value.violations)


def test_price_and_impressions_come_from_the_seller(handoff, sellers):
    book = OrderBook(handoff)
    order, warnings = draft(book, sellers, "L2", "SNA-PMP-01", 55_000)
    assert order.cpm == 27.5 and order.impressions == 2_000_000 and order.spend_usd == 55_000
    assert order.status == "drafted" and warnings == []


def test_rejects_disallowed_audience_provider(handoff, sellers):
    assert "Beacon Signals" in violations(OrderBook(handoff), sellers, "L1", "NL-OPEN-03", 10_000)


def test_rejects_cpm_above_ceiling(handoff, sellers):
    assert "above line L2's ceiling $28.60" in violations(OrderBook(handoff), sellers, "L2", "SNA-PG-02", 10_000)


def test_rejects_offer_from_another_publisher(handoff, sellers):
    v = violations(OrderBook(handoff), sellers, "L1", "AL-PG-01", 10_000)
    assert "is from Arena Live" in v


def test_rejects_excluded_publisher_offer(handoff, sellers):
    v = violations(OrderBook(handoff), sellers, "L1", "VX-PMP-01", 10_000)
    assert "hard-excluded" in v


def test_rejects_unknown_line_and_offer(handoff, sellers):
    book = OrderBook(handoff)
    assert "no plan line" in violations(book, sellers, "L9", "NL-PG-01", 10)
    assert "unknown offer_id" in violations(book, sellers, "L1", "NOPE", 10)


def test_line_budget_is_a_cap_across_drafts(handoff, sellers):
    book = OrderBook(handoff)
    draft(book, sellers, "L4", "AL-PG-01", 100_000)
    assert "remaining budget" in violations(book, sellers, "L4", "AL-PG-01", 70_000)
    draft(book, sellers, "L4", "AL-PG-01", 60_000)  # exactly what is left
    assert book.booked_for_line("L4") <= 160_000


def test_avails_are_shared_across_drafts(handoff, sellers):
    book = OrderBook(handoff)
    draft(book, sellers, "L2", "SNA-PMP-01", 100_000)
    assert "remaining avails" in violations(book, sellers, "L2", "SNA-PMP-01", 20_000)


def test_zero_or_negative_spend_is_rejected(handoff, sellers):
    assert "at least one impression" in violations(OrderBook(handoff), sellers, "L1", "NL-PG-01", 0)
    assert "at least one impression" in violations(OrderBook(handoff), sellers, "L1", "NL-PG-01", -5)


def test_offer_longer_than_the_plan_books_only_the_shared_days(plan_dict, sellers):
    from buyer.handoff import PlanHandoff
    plan_dict["flight"]["end"] = "2027-08-22"  # AL-PG-01 runs to 08-31
    book = OrderBook(PlanHandoff.from_dict(plan_dict))
    offer = sellers.get_offer("AL-PG-01")
    in_window = 5_000_000 * 175 // 184  # 175 of the offer's 184 days
    assert book.avails_left(offer) == in_window
    order, _ = draft(book, sellers, "L4", "AL-PG-01", 1_000)
    assert str(order.flight.end) == "2027-08-22"
    too_much = (in_window + 1000) * 35 / 1000
    assert "remaining avails in the plan flight" in violations(book, sellers, "L4", "AL-PG-01", too_much)


def test_offer_with_no_shared_days_is_rejected(plan_dict, sellers):
    from buyer.handoff import PlanHandoff
    plan_dict["flight"]["end"] = "2027-04-30"
    for cm in plan_dict["commitments"]:
        cm["end"] = "2027-04-30"
        cm["start"] = "2027-03-01"
    h = PlanHandoff.from_dict(plan_dict)
    assert "doesn't overlap the plan flight" in violations(OrderBook(h), sellers, "L3", "TC-DIO-01", 1_000)


def test_avoid_list_is_a_warning_not_a_block(plan_dict, sellers):
    from buyer.handoff import PlanHandoff
    plan_dict["constraints"]["avoid_publishers"] = ["Arena Live"]
    _, warnings = draft(OrderBook(PlanHandoff.from_dict(plan_dict)), sellers, "L4", "AL-PG-01", 1_000)
    assert "avoid list" in warnings[0]


def test_cancel_frees_budget(handoff, sellers):
    book = OrderBook(handoff)
    order, _ = draft(book, sellers, "L4", "AL-PG-01", 160_000)
    book.cancel(order.order_id)
    assert book.booked_for_line("L4") == 0
    draft(book, sellers, "L4", "AL-PG-01", 160_000)


def test_review_flags_unmet_commitment_and_short_lines(handoff, sellers):
    book = OrderBook(handoff)
    draft(book, sellers, "L1", "NL-PMP-02", 150_000)  # Northlight, but not the PG commitment
    draft(book, sellers, "L1", "NL-PG-01", 70_000)
    review = book.review()
    assert any("commitment cm_001" in w and "short by $80,000" in w for w in review["warnings"])
    assert any(w.startswith("L2 ") for w in review["warnings"])
    assert not any(w.startswith("L1 ") for w in review["warnings"])  # L1 is fully booked


def test_commitment_met_despite_whole_impression_rounding(handoff, sellers):
    book = OrderBook(handoff)
    order, _ = draft(book, sellers, "L1", "NL-PG-01", 150_000)
    assert order.spend_usd < 150_000  # whole impressions
    assert not any("cm_001" in w for w in book.review()["warnings"])


def test_approve_and_submit_lifecycle(handoff, sellers):
    book = OrderBook(handoff)
    a, _ = draft(book, sellers, "L4", "AL-PG-01", 50_000)
    b, _ = draft(book, sellers, "L5", "SCX-OPEN-01", 50_000)
    assert book.submit_approved(sellers) == []  # nothing approved, nothing sent
    book.approve([a.order_id], approver="tester")
    sent = book.submit_approved(sellers)
    assert [o.order_id for o in sent] == [a.order_id]
    assert a.status == "submitted" and a.seller_order_id and b.status == "drafted"
    with pytest.raises(OrderError):
        book.approve([a.order_id], approver="tester")  # already submitted
    with pytest.raises(OrderError):
        book.cancel(a.order_id)


def test_seller_outage_at_submit_marks_order_failed(handoff, sellers):
    book = OrderBook(handoff)
    order, _ = draft(book, sellers, "L4", "AL-PG-01", 50_000)
    book.approve([order.order_id], approver="tester")
    book.submit_approved(FakeSellers(down={"Arena Live"}))
    assert order.status == "failed" and "not responding" in order.failure

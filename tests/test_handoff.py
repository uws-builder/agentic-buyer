import pytest

from buyer.handoff import HandoffError, PlanHandoff


def test_fixture_loads(handoff):
    assert handoff.total_budget_usd == 800_000
    assert [ln.line_id for ln in handoff.lines] == ["L1", "L2", "L3", "L4", "L5"]
    assert handoff.max_cpm(handoff.line("L2")) == 28.6
    assert handoff.constraints.is_excluded("  vortex   STREAM ")


def test_rejects_unknown_schema(plan_dict):
    plan_dict["schema"] = "plan_handoff.v9"
    with pytest.raises(HandoffError, match="unsupported schema"):
        PlanHandoff.from_dict(plan_dict)


def test_rejects_lines_that_do_not_add_up(plan_dict):
    plan_dict["lines"][0]["budget_usd"] += 1
    with pytest.raises(HandoffError, match="sum to"):
        PlanHandoff.from_dict(plan_dict)


def test_rejects_line_on_excluded_publisher(plan_dict):
    plan_dict["lines"][0]["publisher"] = "Hollow Peak Media"
    with pytest.raises(HandoffError, match="hard-excluded"):
        PlanHandoff.from_dict(plan_dict)


def test_rejects_commitment_on_excluded_publisher(plan_dict):
    plan_dict["commitments"][0]["publisher"] = "Vortex Stream"
    with pytest.raises(HandoffError, match="commitment cm_001"):
        PlanHandoff.from_dict(plan_dict)


def test_rejects_missing_field(plan_dict):
    del plan_dict["lines"][0]["target_cpm"]
    with pytest.raises(HandoffError, match="missing or has a bad field"):
        PlanHandoff.from_dict(plan_dict)


def test_null_provider_list_means_unrestricted(plan_dict):
    plan_dict["constraints"]["audience_providers"] = None
    h = PlanHandoff.from_dict(plan_dict)
    assert h.constraints.provider_allowed("Beacon Signals")

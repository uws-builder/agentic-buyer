import copy
import json
from pathlib import Path

import pytest

from buyer.handoff import PlanHandoff
from buyer.sellers import FakeSellers

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "fixtures" / "plans" / "summit_ridge_2027.json"


@pytest.fixture
def plan_dict() -> dict:
    return copy.deepcopy(json.loads(PLAN.read_text()))


@pytest.fixture
def handoff() -> PlanHandoff:
    return PlanHandoff.load(PLAN)


@pytest.fixture
def sellers() -> FakeSellers:
    return FakeSellers()

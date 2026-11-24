"""The plan handoff: what a planner gives the buyer.

This is the seam between the two agents. The planner (agentic-planner) owns the
plan; the buyer only executes it. Everything the buyer is allowed to do is
bounded by this document, so it is validated strictly on load: a handoff that
does not add up is rejected before any agent runs.

Schema `plan_handoff.v0` is described in docs/handoff.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

SCHEMA = "plan_handoff.v0"


class HandoffError(ValueError):
    """The handoff is malformed or internally inconsistent."""


def _norm(name: str) -> str:
    return " ".join(name.casefold().split())


def _date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as err:
        raise HandoffError(f"{field}: expected an ISO date, got {value!r}") from err


@dataclass(frozen=True)
class Flight:
    start: date
    end: date

    @classmethod
    def from_dict(cls, d: dict[str, Any], field: str = "flight") -> Flight:
        f = cls(_date(d.get("start"), f"{field}.start"), _date(d.get("end"), f"{field}.end"))
        if f.end < f.start:
            raise HandoffError(f"{field}: ends before it starts")
        return f

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1  # inclusive

    def overlap(self, other: Flight) -> Flight | None:
        start, end = max(self.start, other.start), min(self.end, other.end)
        return Flight(start, end) if start <= end else None

    def to_dict(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass(frozen=True)
class PlanLine:
    line_id: str
    publisher: str
    kind: str
    budget_usd: int
    target_cpm: float


@dataclass(frozen=True)
class Commitment:
    id: str
    publisher: str
    deal_type: str
    min_spend_usd: int
    flight: Flight


@dataclass(frozen=True)
class Constraints:
    excluded_publishers: tuple[str, ...]
    avoid_publishers: tuple[str, ...]
    audience_providers: tuple[str, ...] | None  # None = no restriction on file
    cpm_tolerance_pct: float

    def is_excluded(self, publisher: str) -> bool:
        return _norm(publisher) in {_norm(p) for p in self.excluded_publishers}

    def is_avoided(self, publisher: str) -> bool:
        return _norm(publisher) in {_norm(p) for p in self.avoid_publishers}

    def provider_allowed(self, provider: str | None) -> bool:
        if self.audience_providers is None or provider is None:
            return True
        return _norm(provider) in {_norm(p) for p in self.audience_providers}


@dataclass(frozen=True)
class PlanHandoff:
    plan_id: str
    advertiser_id: str
    advertiser: str
    flight: Flight
    total_budget_usd: int
    lines: tuple[PlanLine, ...]
    constraints: Constraints
    commitments: tuple[Commitment, ...]
    planner_notes: tuple[str, ...] = ()  # informational, shown to the approver

    def line(self, line_id: str) -> PlanLine | None:
        return next((ln for ln in self.lines if ln.line_id == line_id), None)

    def max_cpm(self, line: PlanLine) -> float:
        return round(line.target_cpm * (1 + self.constraints.cpm_tolerance_pct / 100), 2)

    @classmethod
    def load(cls, path: Path) -> PlanHandoff:
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError) as err:
            raise HandoffError(f"could not read handoff {path}: {err}") from err
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlanHandoff:
        if d.get("schema") != SCHEMA:
            raise HandoffError(f"unsupported schema {d.get('schema')!r}; expected {SCHEMA!r}")
        try:
            c = d.get("constraints") or {}
            providers = c.get("audience_providers")
            constraints = Constraints(
                excluded_publishers=tuple(c.get("excluded_publishers") or ()),
                avoid_publishers=tuple(c.get("avoid_publishers") or ()),
                audience_providers=None if providers is None else tuple(providers),
                cpm_tolerance_pct=float(c.get("cpm_tolerance_pct", 0)),
            )
            lines = tuple(
                PlanLine(
                    line_id=str(ln["line_id"]),
                    publisher=str(ln["publisher"]),
                    kind=str(ln.get("kind", "")),
                    budget_usd=int(ln["budget_usd"]),
                    target_cpm=float(ln["target_cpm"]),
                )
                for ln in d["lines"]
            )
            commitments = tuple(
                Commitment(
                    id=str(cm["id"]),
                    publisher=str(cm["publisher"]),
                    deal_type=str(cm["deal_type"]).upper(),
                    min_spend_usd=int(cm["min_spend_usd"]),
                    flight=Flight.from_dict(cm, f"commitments[{cm['id']}]"),
                )
                for cm in d.get("commitments") or ()
            )
            handoff = cls(
                plan_id=str(d["plan_id"]),
                advertiser_id=str(d["advertiser_id"]),
                advertiser=str(d["advertiser"]),
                flight=Flight.from_dict(d["flight"]),
                total_budget_usd=int(d["total_budget_usd"]),
                lines=lines,
                constraints=constraints,
                commitments=commitments,
                planner_notes=tuple(str(n) for n in d.get("planner_notes") or ()),
            )
        except (KeyError, TypeError, ValueError) as err:
            if isinstance(err, HandoffError):
                raise
            raise HandoffError(f"handoff is missing or has a bad field: {err}") from err
        handoff._check()
        return handoff

    def _check(self) -> None:
        """Reject a handoff that contradicts itself. The buyer never 'fixes' a plan."""
        problems = []
        if not self.lines:
            problems.append("plan has no lines")
        ids = [ln.line_id for ln in self.lines]
        if len(ids) != len(set(ids)):
            problems.append("duplicate line_id")
        for ln in self.lines:
            if ln.budget_usd <= 0:
                problems.append(f"{ln.line_id}: budget must be positive")
            if ln.target_cpm <= 0:
                problems.append(f"{ln.line_id}: target_cpm must be positive")
            if self.constraints.is_excluded(ln.publisher):
                problems.append(f"{ln.line_id}: {ln.publisher} is a hard-excluded publisher")
        line_sum = sum(ln.budget_usd for ln in self.lines)
        if line_sum != self.total_budget_usd:
            problems.append(f"line budgets sum to {line_sum}, not total_budget_usd {self.total_budget_usd}")
        for cm in self.commitments:
            if self.constraints.is_excluded(cm.publisher):
                problems.append(f"commitment {cm.id} is on hard-excluded publisher {cm.publisher}")
        if problems:
            raise HandoffError("; ".join(problems))

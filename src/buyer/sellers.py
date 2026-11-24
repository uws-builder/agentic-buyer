"""Sell-side interface and a deterministic fake for it.

A real backend (a publisher's order API, a DSP deal desk, an SSP) replaces the
fake behind `SellerNetwork`. The fake reads fixtures/sellers.json and is never
LLM-generated, so a failing test can only be the agent's or harness's fault.

Fault injection: publishers named in `down` raise SellerUnavailable.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from buyer.handoff import Flight, _norm

REPO_ROOT = Path(__file__).resolve().parents[2]
SELLERS_FIXTURE = REPO_ROOT / "fixtures" / "sellers.json"


class SellerUnavailable(RuntimeError):
    """The seller could not be reached."""


class SellerRejected(RuntimeError):
    """The seller refused the order (e.g. avails gone since the offer was read)."""


@dataclass(frozen=True)
class Offer:
    offer_id: str
    publisher: str
    deal_type: str  # PG, PMP, DIO, OPEN
    cpm: float
    avails: int  # impressions available in the offer's flight
    audience_provider: str | None
    flight: Flight

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Offer:
        return cls(
            offer_id=d["offer_id"],
            publisher=d["publisher"],
            deal_type=d["deal_type"].upper(),
            cpm=float(d["cpm"]),
            avails=int(d["avails"]),
            audience_provider=d.get("audience_provider"),
            flight=Flight.from_dict(d),
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["flight"] = self.flight.to_dict()
        return d


@dataclass(frozen=True)
class Confirmation:
    seller_order_id: str
    offer_id: str
    impressions: int
    spend_usd: float


class SellerNetwork(Protocol):
    def list_offers(self, publisher: str) -> list[Offer]: ...
    def get_offer(self, offer_id: str) -> Offer | None: ...
    def submit(self, offer_id: str, impressions: int, ref: str) -> Confirmation: ...


class FakeSellers:
    def __init__(self, path: Path = SELLERS_FIXTURE, down: set[str] | None = None):
        data = json.loads(Path(path).read_text())
        self._offers = {o.offer_id: o for o in map(Offer.from_dict, data["offers"])}
        self._sold: dict[str, int] = {}
        self._down = {_norm(p) for p in (down or set())}
        self.submissions: list[Confirmation] = []

    def _check_up(self, publisher: str) -> None:
        if _norm(publisher) in self._down:
            raise SellerUnavailable(f"{publisher} order desk is not responding")

    def list_offers(self, publisher: str) -> list[Offer]:
        self._check_up(publisher)
        return [o for o in self._offers.values() if _norm(o.publisher) == _norm(publisher)]

    def get_offer(self, offer_id: str) -> Offer | None:
        offer = self._offers.get(offer_id)
        if offer is not None:
            self._check_up(offer.publisher)
        return offer

    def submit(self, offer_id: str, impressions: int, ref: str) -> Confirmation:
        offer = self.get_offer(offer_id)
        if offer is None:
            raise SellerRejected(f"unknown offer {offer_id}")
        if self._sold.get(offer_id, 0) + impressions > offer.avails:
            raise SellerRejected(f"{offer_id}: not enough avails left")
        self._sold[offer_id] = self._sold.get(offer_id, 0) + impressions
        conf = Confirmation(
            seller_order_id=f"SO-{offer_id}-{len(self.submissions) + 1:03d}",
            offer_id=offer_id,
            impressions=impressions,
            spend_usd=round(impressions * offer.cpm / 1000, 2),
        )
        self.submissions.append(conf)
        return conf

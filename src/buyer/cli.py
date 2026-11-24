"""Command line:  python -m buyer run [--plan PATH] [--real] [--seller-down NAME]

Runs the buying agent on a plan handoff, prints the draft order book, then asks
a person which drafts to approve. Only approved drafts are sent to the (fake)
sellers. Approval needs an interactive terminal, so nothing is ever submitted
from a script or pipe by accident.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Callable

from buyer.agent import BuyingAgent, BuyResult
from buyer.config import REPO_ROOT, load_env
from buyer.demo import summit_ridge_client
from buyer.handoff import HandoffError, PlanHandoff
from buyer.model import AnthropicClient
from buyer.sellers import FakeSellers

DEFAULT_PLAN = REPO_ROOT / "fixtures" / "plans" / "summit_ridge_2027.json"
OUT_DIR = REPO_ROOT / "out"


def _money(x: float) -> str:
    return f"${x:,.2f}"


def print_events(result: BuyResult, out=print) -> None:
    out("\nAgent activity")
    for e in result.events:
        mark = "ok " if e.ok else "ERR"
        arg = e.input.get("publisher") or " ".join(
            f"{k}={v}" for k, v in e.input.items() if k != "rationale")
        out(f"  [{mark}] {e.tool} {arg}".rstrip())
        if not e.ok:
            for reason in e.result.get("violations") or [e.result.get("error")]:
                out(f"        - {reason}")


def print_book(result: BuyResult, out=print) -> None:
    review = result.book.review()
    out("\nDraft order book")
    out(f"  {'order':8} {'line':4} {'offer':12} {'deal':4} {'CPM':>7} {'impressions':>12} {'spend':>14}")
    for o in result.book.active():
        out(f"  {o.order_id:8} {o.line_id:4} {o.offer_id:12} {o.deal_type:4} {o.cpm:>7.2f} "
            f"{o.impressions:>12,} {_money(o.spend_usd):>14}")
    out(f"\n  Booked {_money(review['booked_total_usd'])} of {_money(review['total_budget_usd'])}")
    for ln in review["lines"]:
        out(f"    {ln['line_id']} {ln['publisher']:24} {_money(ln['booked_usd']):>14} / {_money(ln['budget_usd'])}")
    if review["warnings"]:
        out("\n  Warnings")
        for w in review["warnings"]:
            out(f"    ! {w}")


def ask_approval(result: BuyResult, ask: Callable[[str], str], out=print) -> list[str]:
    drafts = [o.order_id for o in result.book.active() if o.status == "drafted"]
    if not drafts:
        return []
    answer = ask(f"\nApprove which drafts? 'all', 'none', or a list like ORD-002,ORD-004 [none]: ").strip()
    if answer.lower() == "all":
        return drafts
    if not answer or answer.lower() == "none":
        return []
    picked = [a.strip().upper() for a in answer.split(",") if a.strip()]
    unknown = [p for p in picked if p not in drafts]
    if unknown:
        out(f"  Not drafts: {', '.join(unknown)}. Nothing approved.")
        return []
    return picked


def main(argv: list[str] | None = None, ask: Callable[[str], str] = input,
         interactive: bool | None = None, out=print, out_dir: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="buyer")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="book a plan handoff")
    run.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    run.add_argument("--real", action="store_true", help="use the Anthropic API (spends your key)")
    run.add_argument("--seller-down", action="append", default=[], metavar="PUBLISHER",
                     help="simulate an outage at this publisher's seller")
    args = parser.parse_args(argv)

    try:
        handoff = PlanHandoff.load(args.plan)
    except HandoffError as err:
        out(f"Plan rejected: {err}")
        return 2

    if args.real:
        load_env()
        model = AnthropicClient()
        out(f"Real mode: {model.model}, effort {model.effort}")
    else:
        if handoff.plan_id != "plan_summit_ridge_2027_ss":
            out("Demo mode only has a script for the Summit Ridge fixture; use --real for other plans.")
            return 2
        model = summit_ridge_client()
        out("Demo mode: scripted model turns, no API calls.")

    if handoff.planner_notes:
        out("Planner notes")
        for note in handoff.planner_notes:
            out(f"  - {note}")

    sellers = FakeSellers(down=set(args.seller_down))
    result = BuyingAgent(handoff, sellers, model).run()

    print_events(result, out)
    if result.error:
        out(f"\nAgent stopped: {result.error}")
    print_book(result, out)
    if result.summary:
        out("\nAgent summary\n" + "\n".join("  " + ln for ln in result.summary.splitlines()))

    if interactive is None:
        interactive = sys.stdin.isatty()
    approved = []
    if not interactive:
        out("\nNo orders approved: approval needs an interactive terminal.")
    else:
        ids = ask_approval(result, ask, out)
        if ids:
            approved = result.book.approve(ids, approver=getpass.getuser())
            sent = result.book.submit_approved(sellers)
            out("\nSubmitted to sellers")
            for o in sent:
                status = o.seller_order_id if o.status == "submitted" else f"FAILED: {o.failure}"
                out(f"  {o.order_id} {o.offer_id:12} {status}")
        else:
            out("\nNothing approved. Drafts were not sent.")

    out_dir = out_dir or OUT_DIR
    out_dir.mkdir(exist_ok=True)
    record = out_dir / f"buy_{handoff.plan_id}.json"
    record.write_text(json.dumps({
        "plan_id": handoff.plan_id,
        "summary": result.summary,
        "error": result.error,
        "orders": [o.to_dict() for o in result.book.orders],
        "review": result.book.review(),
    }, indent=2))
    out(f"\nBuy record written to {record}")
    return 0 if not result.error else 1


if __name__ == "__main__":
    raise SystemExit(main())

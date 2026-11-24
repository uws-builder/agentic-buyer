# Agentic Media Buyer (prototype)

The buying half of [agentic-planner](../agentic-planner). The planner produces a confirmed
streaming-TV media plan. This agent takes that plan and books it: it reads each seller's
offers, splits each line's budget across deals, and drafts orders. A person then approves
which drafts are sent.

This is a learning project, not a product. The sellers, offers and plan in `fixtures/` are
made up. What is real is the shape of the system and how it is tested.

## The core idea: the model chooses, the harness enforces

It is the same rule as the planner. The model decides which offers to use and how to split
the money. Plain code decides whether a draft is allowed:

- **Prices and avails come from the seller, never from the model.** The model picks a dollar
  amount, and code computes whole impressions from the seller's CPM.
- **Hard rules are checked on every draft.** A draft must be on the line's own publisher. It
  can't use a hard-excluded publisher or an audience provider outside the allow-list. Its CPM
  must be within the line's ceiling, and its dates within the plan flight. It must fit the
  line's remaining budget, the plan total, and the offer's remaining avails. A rejected draft
  goes back to the model as a tool error that lists every rule it broke.
- **Soft rules warn.** Avoid-list publishers, under-booked lines and unmet commitments show
  up as warnings for the approver.
- **The agent cannot approve or submit.** It has no tool for either. Approval happens in the
  CLI, needs an interactive terminal, and defaults to "none".

```
plan handoff ──► BuyingAgent ──tools──► OrderBook.draft (guardrails) ──► drafts
                    │                                                     │
                    └──► list_offers ──► SellerNetwork (fake)             ▼
                                                    ▲            person approves
                                                    └──── submit_approved ◄─┘
```

The contract between planner and buyer is in [docs/handoff.md](docs/handoff.md).

## Run it

Needs Python 3.11+.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Demo mode replays scripted model turns, so it is free:

```bash
.venv/bin/python -m buyer run
```

The demo includes two mistakes on purpose, so you can see the harness reject them. One is a
cheap offer that uses a disallowed audience provider. The other is a draft larger than the
seller's avails. It ends with one line $40K short, which is flagged for a human decision.

To simulate a seller outage:

```bash
.venv/bin/python -m buyer run --seller-down "Arena Live"
```

To use the real model, copy `.env.example` to `.env` and add your Anthropic API key. Then run
with `--real`. The model defaults to `claude-opus-5-5` at effort `high`. You can override
either with `BUYER_MODEL` or `BUYER_EFFORT`.

```bash
.venv/bin/python -m buyer run --real
```

To run the tests (no API key needed):

```bash
.venv/bin/pytest
```

## Layout

```
src/buyer/
  handoff.py    plan_handoff.v0: load and validate the planner's output
  sellers.py    seller interface + deterministic fake, with outage injection
  orders.py     order book, hard-rule checks on drafts, review, human-only approve/submit
  agent.py      tool definitions and the explicit tool-use loop
  model.py      model interface: ScriptedClient (demo/tests) and AnthropicClient
  demo.py       scripted turns for the Summit Ridge fixture
  cli.py        run, print the book, ask for approval, write out/buy_<plan_id>.json
fixtures/
  plans/        plan handoffs (Summit Ridge matches the planner's advertiser fixtures)
  sellers.json  offers, including deliberate traps
tests/          handoff, guardrails, agent loop, model client, CLI
githooks/       same commit safeguards as the planner
```

## Design choices worth knowing

- **Raw Messages API with a manual loop**, matching the planner. Each tool runs on its own
  and is tested on its own. Tools use `strict: true`. Thinking is adaptive. Server-side
  refusal fallbacks are enabled (`fallbacks: "default"`).
- **Fakes first.** The seller fake reads fixtures, so tests never depend on an LLM to make
  up data. A real seller API can replace it behind `SellerNetwork`.
- **Line budgets can't move.** If a line can't be filled, the buyer reports the shortfall
  and doesn't reallocate the money. Moving it is a planning decision.

## Not built yet

- Making the planner emit `plan_handoff.v0` directly, and the buyer-to-planner replan
  request for shortfalls.
- Real seller backends, deal negotiation, and changes to orders already sent.
- Pacing and in-flight optimization after launch.
- A Streamlit approval screen to match the planner's UI.
- Evals against the real model, such as scoring how close it gets to the best legal book.

## Safeguards

Turn on the commit hooks once per clone:

```bash
git config core.hooksPath githooks
```

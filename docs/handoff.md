# Planner → buyer handoff (`plan_handoff.v0`)

The planner owns *what* to buy; the buyer owns *how* to book it. The handoff is
the only thing the buyer trusts, so it carries every rule the buyer must obey,
already resolved. The buyer never re-reads the RFP or guidelines and never
"fixes" a plan: a handoff that contradicts itself is rejected on load.

## Fields

| Field | Meaning |
|---|---|
| `schema` | Always `plan_handoff.v0`. Anything else is rejected. |
| `plan_id`, `advertiser_id`, `advertiser` | Identity. `advertiser_id` matches the planner's fixtures. |
| `flight.start`, `flight.end` | ISO dates. An order runs only on the days its offer shares with the flight, and the offer's avails shrink in proportion. An offer with no shared days can't be booked. |
| `total_budget_usd` | Must equal the sum of line budgets. |
| `lines[]` | `line_id`, `publisher`, `kind`, `budget_usd`, `target_cpm`. Each line budget is a hard cap and cannot move to another line. |
| `constraints.excluded_publishers` | Hard exclusions (union of every source, as composed by the planner). |
| `constraints.avoid_publishers` | Soft exclusions: a warning, not a block. |
| `constraints.audience_providers` | Allow-list for audience data, or `null` for no restriction. |
| `constraints.cpm_tolerance_pct` | How far above `target_cpm` an offer may be (ceiling = target × (1 + pct/100)). |
| `commitments[]` | `id`, `publisher`, `deal_type`, `min_spend_usd`, `start`, `end`. Spend floors the buyer should meet. Unmet = warning to the approver. Commitments outside the plan flight are ignored. |
| `planner_notes[]` | Optional. Warnings the planner showed its user (for example, a soft rule they overrode). The buyer prints them for the approver and doesn't act on them. |
| `draft` | `true` while the plan numbers are placeholders. Informational. |

## How this maps from agentic-planner

The planner writes this file from `planner/handoff.py` (`build_handoff`). On its Plan
screen, it's the **Download buyer handoff (JSON)** button. That module is the only planner
code that knows this format, so when the planner changes shape, the adapter and its tests
are what move.

`fixtures/plans/from_planner_summit_ridge.json` here is real `build_handoff` output.
The buyer's tests load and book it, which is the contract check between the two repos.
Regenerate it whenever the planner's export changes.

| Planner concept | Handoff field |
|---|---|
| Confirmed summary: budget, timeframe | `total_budget_usd`, `flight` |
| Plan lines (`plan.PlanLine`) | `lines[]` (`target_cpm` from the line's CPM) |
| Composed rules (`rules.Constraints`): exclusions union, provider allow-list intersection | `constraints` |
| Soft guideline exclusions the user didn't override | `constraints.avoid_publishers` |
| `commitments.json` (`remaining_usd`), only those active in the timeframe | `commitments[].min_spend_usd` |
| Rule notices at warning level | `planner_notes[]` |
| _(no planner equivalent yet)_ | `cpm_tolerance_pct`: a fixed 10% placeholder in `planner/handoff.py` |

## What comes back

The buyer writes `out/buy_<plan_id>.json` with every order and its status
(`drafted`, `approved`, `submitted`, `failed`, `cancelled`), the line-by-line
review, and the warnings. That record is the natural return message for a later
**buyer → planner** step: a line that could not be filled (for example, L2 in
the demo, which is short $40K because the only offer within its CPM ceiling ran
out of avails) becomes a replan request rather than something the buyer
decides on its own.

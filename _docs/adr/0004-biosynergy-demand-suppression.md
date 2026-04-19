# ADR 0004: Suppress Demand Projection for New BioSynergy SKUs

**Status:** Accepted  
**Date:** 2026-04-06

## Context

Three BioSynergy SKUs (Immunity, Energy, Recovery 250g) launched in Q1 2026. In their first month of sales, these products recorded unusually high sell-through — typical of a DTC launch where pent-up demand, launch promotions, and influencer activity produce a spike that does not reflect steady-state demand.

The standard projection formula compounds the average MoM growth rate:

```
Projected_M5 = Total_M4 * (1 + MoM_Growth_Avg)
```

Applied to launch-spike data, this formula would project implausibly high M5 demand, triggering large reorder quantities for products whose true run rate is unknown.

## Decision

For any SKU whose name contains `"BioSynergy"`, `sop_engine.py` overrides the projection formula:

```
Projected_M5 = Total_M4  # flat — no growth compounding
```

This is implemented as a named exception in the engine, applied after the standard projection pass.

## Rationale

**A launch spike is not a demand trend.** Compounding a 300% MoM growth rate from month 1 to month 2 produces a forecast that will be wrong by an order of magnitude. The conservative choice is to hold demand flat at the last observed month until at least 3 months of post-launch data are available.

**Over-ordering on new SKUs is a working capital risk.** Terravita is a DTC brand operating in a constrained cash-flow environment. Excess stock of unproven SKUs ties up capital that could be deployed toward proven high-revenue SKUs.

**The rule is explicit and auditable.** A named exception in code is preferable to a comment in a spreadsheet. The LLM briefing is also instructed to surface this reasoning to the executive team, making the conservative modelling choice transparent.

## Consequences

- The projection suppression must be revisited once BioSynergy SKUs have ≥3 months of post-launch data. At that point the standard MoM formula should apply.
- The SKU name match (`"BioSynergy" in SKU`) is fragile — a rename would silently disable the suppression. This should be monitored when new SKUs are added.
- `Is_At_Risk` and `Suggested_Reorder_Qty` for these SKUs are calculated on the suppressed projection, meaning reorder signals will be conservative.

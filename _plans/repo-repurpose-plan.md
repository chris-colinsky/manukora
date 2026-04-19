# Plan: Repository Repurpose — "Calculate First, Reason Second"

## Summary

Remove all references to Manukora and the job-submission context. Rebrand the repository as a professional, open-source reference architecture with two complementary focuses:

1. **Primary:** The "Calculate First, Reason Second" pattern — deterministic math before LLM reasoning.
2. **Secondary:** Demonstrating the use of AI Coding Tools (Claude Code, etc.) in building a production-grade project from scratch.

Replace product-specific data with generic DTC wellness SKUs (brand: "Terravita"), rewrite docs to sell the architecture and the AI-assisted development story, and update deployment configs.

## Requirements Reference

`_reqs/repo-repurpose.md`

## Clarifications & Decisions

- **Brand name** → "Terravita" (fictional). README will note it's fictitious.
- **CSV data** → Keep numeric values identical; only rename SKUs to generic names. Column names stay the same.
- **CLAUDE.md** → Full rewrite to match new narrative (not just find-replace).
- **_reqs/ and _plans/ files** → Rename and update content to remove submission context.
- **Fly.io** → Update app names to be generic.
- **LinkedIn post** → Out of scope.
- **Git history** → Leave as-is; user will create a clean repo later.
- **Architecture diagram** → Mermaid format in README.
- **External links** → None to worry about.

## Brand Name: Terravita (Selected)

Fictitious DTC wellness brand. README will include a note that this is a fictional company used for demonstration purposes.

## Generic SKU Naming Scheme

Replace honey-specific SKUs with a tiered supplement product line. Keep the same structure (tiers, sizes, special
categories):

| Original SKU                  | Generic SKU                    |
|-------------------------------|--------------------------------|
| Manuka Honey MGO 100+ 250g    | Daily Wellness Tier 1 250g     |
| Manuka Honey MGO 263+ 250g    | Daily Wellness Tier 2 250g     |
| Manuka Honey MGO 263+ 500g    | Daily Wellness Tier 2 500g     |
| Manuka Honey MGO 514+ 250g    | Premium Supplement Tier 3 250g |
| Manuka Honey MGO 514+ 500g    | Premium Supplement Tier 3 500g |
| Manuka Honey MGO 850+ 250g    | Ultra Concentrate Tier 4 250g  |
| Manuka Honey MGO 850+ 500g    | Ultra Concentrate Tier 4 500g  |
| Manuka Honey MGO 1700+ 100g   | Elite Formula Tier 5 100g      |
| Propolis Tincture 30ml        | Energy Tincture 30ml           |
| Bioactive Blend Immunity 250g | BioSynergy Immunity 250g    |
| Bioactive Blend Energy 250g   | BioSynergy Energy 250g      |
| Bioactive Blend Recovery 250g | BioSynergy Recovery 250g    |

**Key:** "Bioactive Blend" → "BioSynergy" throughout codebase (business logic exception preserved).

## Implementation Plan

### Phase 1: Data Layer — CSV & SKU Rename

- [ ] Update `backend/data/sales-data.csv` with generic SKU names (keep all numeric data)
- [ ] Update `sales-data.csv` (root copy) to match
- [ ] Update "Bioactive Blend" → "BioSynergy" in `backend/sop_engine.py` (forecasting exception logic)
- [ ] Update hardcoded SKU references in `backend/templates/user_prompt.j2` (line 66 example)
- [ ] Update hardcoded SKU references in `backend/tests/test_evals.py` (lines 90-92)
- [ ] Update product references in `backend/tests/test_sop_engine.py`

### Phase 2: Brand Name Global Replace

- [ ] `frontend/app.py` — page title and header (lines 47, 63)
- [ ] `frontend/tests/test_app.py` — assertion on line 119
- [ ] `backend/api.py` — FastAPI app title and description (lines 55, 57)
- [ ] `backend/pyproject.toml` — package description (line 4)
- [ ] `backend/templates/system_prompt.j2` — update persona to "Supply Chain Director for an Omni-channel Retailer"
- [ ] `docker-compose.yml` — check for any brand references

### Phase 3: Deployment Config

- [ ] Update `backend/fly.toml` — app name, OTEL_SERVICE_NAME, BACKEND_URL
- [ ] Update `frontend/fly.toml` — app name, BACKEND_URL
- [ ] Choose generic app names (e.g., `cfrr-backend` / `cfrr-frontend` for "Calculate First, Reason Second")

### Phase 4: Documentation Overhaul

#### 4a: README.md — Full Rewrite

- [ ] New title: the architecture pattern, not the brand
- [ ] Problem statement: LLMs are reasoning engines, not calculators
- [ ] Solution: "Calculate First, Reason Second" pattern
- [ ] Mermaid architecture diagram showing the deterministic/non-deterministic split
- [ ] Key features: CI/CD for LLMs (deepeval), Zero-Hallucination Guardrails, Actionable Workflows
- [ ] Secondary narrative: AI Coding Tools — how this project was built using Claude Code and similar tools, preserving the original "built with AI" story
- [ ] Note that "Terravita" is a fictitious brand used for demonstration
- [ ] Updated deployment instructions with new Fly.io app names
- [ ] Updated project structure section
- [ ] Remove all Manukora references, live demo links (update after redeployment)

#### 4b: CLAUDE.md — Full Rewrite

- [ ] Update project description to reference architecture focus + AI coding tools angle
- [ ] Update all SKU/product references to generic names
- [ ] Update deployment section with new app names
- [ ] Remove job-submission context entirely

#### 4c: _reqs/ and _plans/ Files

- [ ] Rename `_reqs/submission-strategy-part-1.md` → `_reqs/calculate-first-reason-second.md`
- [ ] Update content to remove submission/interview context
- [ ] Rename `_plans/submission-strategy-part-1-plan.md` → `_plans/calculate-first-reason-second-plan.md`
- [ ] Update content to remove submission/interview context

#### 4d: _docs/ Files

- [ ] Update `_docs/submission_guidelines.md` — remove Manukora header, reframe or delete if irrelevant
- [ ] Update `_docs/part-2-morning-brief.md` — remove "Manukora is a DTC brand" reference
- [ ] Update `_docs/learning_concepts.md` — check for brand references
- [ ] Review all ADR files for brand-specific language and update
- [ ] Update `_docs/architecture.mmd` if it has brand references

### Phase 5: Verification

- [ ] Run `grep -ri "manukora"` across entire repo to catch stragglers
- [ ] Run `grep -ri "manuka"` to catch honey-specific references
- [ ] Run `grep -ri "propolis"` to verify old SKU names are gone
- [ ] Run tests: `cd backend && uv run pytest`
- [ ] Run tests: `cd frontend && uv run pytest`
- [ ] Run linting: `make lint` (if available)

## Files to Create / Modify

### Modified Files (26 files)

- `backend/data/sales-data.csv` — generic SKU names
- `sales-data.csv` — root copy, match backend
- `backend/sop_engine.py` — "Bioactive Blend" → "BioSynergy"
- `backend/templates/system_prompt.j2` — new persona
- `backend/templates/user_prompt.j2` — generic SKU examples
- `backend/templates/prompt_configs.json` — check for brand refs
- `backend/templates/prompt_labels.json` — check for brand refs
- `backend/api.py` — FastAPI title/description
- `backend/pyproject.toml` — package description
- `backend/fly.toml` — app name, service name
- `backend/tests/test_sop_engine.py` — SKU references
- `backend/tests/test_evals.py` — hardcoded SKU assertions
- `frontend/app.py` — page title, header
- `frontend/fly.toml` — app name, backend URL
- `frontend/tests/test_app.py` — assertions
- `README.md` — full rewrite
- `CLAUDE.md` — full rewrite
- `docker-compose.yml` — check/update
- `.github/workflows/ci.yml` — check for brand refs in badge URLs
- `_docs/submission_guidelines.md` — rebrand or remove
- `_docs/part-2-morning-brief.md` — remove brand refs
- `_docs/learning_concepts.md` — check/update
- `_docs/architecture.mmd` — check/update
- `_docs/adr/*.md` — review all 6 ADRs for brand references

### Renamed Files (2 files)

- `_reqs/submission-strategy-part-1.md` → `_reqs/calculate-first-reason-second.md`
- `_plans/submission-strategy-part-1-plan.md` → `_plans/calculate-first-reason-second-plan.md`

### No New Files Created

## Out of Scope

- LinkedIn post drafting (user will handle separately)
- Git history cleanup (user will create a fresh repo)
- Actual Fly.io redeployment (separate step after code changes)
- Regenerating numeric data in CSV (keeping existing values)
- Changes to Python logic, API contracts, or test structure (only references/names change)

## Open Questions

- Which brand name does the user want? (Must be answered before Phase 2)
- Should `_docs/submission_guidelines.md` be deleted entirely or reframed? (Original Manukora interview guidelines may
  have no value in the new context)
- New Fly.io app name prefix — `cfrr-` (Calculate First Reason Second) or derived from chosen brand name?

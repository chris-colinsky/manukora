# The "Deterministic AI Agent" Repository Repurpose

We’re going to refactor this repository that was originally created as a coding challenge as part of a job interview.  The goal is to remove all references to the company it was created for “Manukora” and the original goal and repurpose it to focus on demonstrating the key architectural insight “Calculate First, Reason Second”.  Then we’ll redeploy the updated apps and make a Linkedin post about the insight and reference repo.

## Repository Makeover Strategy

Before the post on LinkedIn, the GitHub repository must look like a professional, open-source reference architecture.

### 1. The Global Find-and-Replace

- **Brand Name:** Change "Manukora" such as "Acme Wellness", recommend some names  
- **The Data:** Change the SKU names to be generic (e.g., "Premium Supplement 500g", "Energy Tincture 30ml").  
- **The System Prompt:** Update the prompt persona to "Supply Chain Director for an Omni-channel Retailer."
- **The Docs:** Update the docs to reflect the new purpose including files names and their content.

### 2. The New README Narrative

Structure the README to sell the *architecture*, not the app. Use this outline:

- **The Problem:** LLMs are reasoning engines, not calculators. Feeding raw CSVs to LLMs results in arithmetic hallucinations, broken JSON, and catastrophic business decisions (like ordering 400 units of dead stock).  
- **The Solution:** The "Calculate First, Reason Second" Pattern.  
- **Architecture Diagram:** Show the clear split: Data Warehouse -> Pandas (Deterministic Math) -> JSON Payload -> LLM (Non-deterministic Reasoning) -> DeepEval (Validation) -> Streamlit (UI).  
- **Key Features Highlight:**  
  - *CI/CD for LLMs:* Explain how to use deepeval to mathematically grade the LLM's Air Freight recommendation against the ground-truth Pandas calculation.  
  - *Zero-Hallucination Guardrails:* Explain the "Empty State Fallback" prompting technique.  
  - *Actionable Workflows:* Show the "Download Draft POs" feature to prove AI should drive action, not just chat.

## Clarifying Questions

**Q: For the new brand name — do you have a preference, or should I propose 3–5 options for you to pick from? Any constraints (e.g., must sound like a real DTC brand, or can be obviously fictional like "Acme")?**
A: propose 3–5 options, sound like a real DTC brand but obviously fictional - don't use ACME

**Q: Should the `sales-data.csv` file itself be rewritten with generic SKU names/data, or just the references to it in code and docs? If the CSV changes, should the numeric values stay the same (so tests still pass) or be regenerated?**
A: the SKU names need to be made generic, but the data can be the same as well as the column names

**Q: The CLAUDE.md file currently describes this as a "job submission for Manukora." Should CLAUDE.md be fully rewritten to match the new narrative, or just have brand references swapped?**
A: fully rewritten to match the new narrative, also review the original _reqs/ and _plans/ files.  they will need to be renamed and updated accordingly

**Q: Should we update the Fly.io app names/URLs for the redeployment, or keep the existing deployment targets?**
A: update to be generic

**Q: Is the LinkedIn post draft in scope for this task, or will you handle that separately? If in scope, what tone/length are you targeting?**
A: handle that separately, just wanted to provide context to the revised purpose of these requirements

**Q: Should git history be cleaned up (e.g., squash commits that reference Manukora), or is it fine for old commit messages to still mention the original context?**
A: good question, I think I'm going to create a new repo with no history to solve.  for now we'll work out of this repo.

**Q: The architecture diagram in the new README — do you want a Mermaid diagram in markdown, an image file, or something else?**
A: i prefer mermaid

**Q: Are there any existing links to this repo in the wild (blog posts, other READMEs, package registries) that would break if the repo name or structure changes significantly?**
A: no

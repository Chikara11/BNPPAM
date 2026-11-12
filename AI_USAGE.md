# Use of AI in this project

## 1. Tools

| Tool | Used for |
|---|---|
| Claude (chat, claude.ai) | Domain research on CLO/ABS, design reviews, challenging specifications, drafting documents, reviewing the plans and outputs produced by Claude Code |
| Claude Code (terminal, in the repository) | Implementation, tests, refactoring, independent audits and reviews, under a working agreement written in `CLAUDE.md` |

No AI is used inside the application. Every calculation, every exception and
every severity is produced by deterministic Python code driven by
`config.yaml`. This was a rule from the start: *never let an LLM determine
financial calculations, the existence of an exception or its severity.*

## 2. How I worked with AI

I owned the design and every business decision; the AI proposed, drafted,
implemented and reviewed. The workflow, written in `CLAUDE.md`, was the same
for every step:

1. **Plan first.** Claude Code proposes a plan, lists ambiguities and risks,
   and waits for my approval. No file is written before that.
2. **Decide.** I answer the open questions and correct the plan.
3. **Implement one step**, with tests.
4. **Verify**: tests, the anomaly manifest as oracle, and my own check of
   the outputs (rows, values, the Excel report opened by hand).
5. **Commit** only after my approval.

### Who did what

| Part | My role | AI role |
|---|---|---|
| Domain research (securitisation, CLO/ABS, price ranges by rating) | Asked the questions, checked the answers, chose the assumptions | Explained concepts, proposed ranges, justified them on request |
| `CLAUDE.md` (working agreement and business rules) | Wrote the first draft; extended it throughout the project | Reviewed the draft; added rules as issues were found, with my approval |
| `config.yaml` (thresholds, severities, keys, allowed values) | Wrote it | Later additions proposed (dimensions, ISIN pattern, amount signs), each approved by me |
| `docs/data_spec.md` (input contract) | Set the values and decisions (portfolios, currencies, distributions, conventions) | Drafted the document; two line-by-line reviews found 11 issues that I resolved |
| Anomaly design (15 anomalies + 4 no-alert cases) | Designed the list and its distribution | Implemented the injection and the manifest |
| `docs/output_spec.md` (output tables) | Decided every open point (status vocabulary, summary format, colours, completeness rules) | Drafted the document from those decisions |
| `docs/architecture.md` | Decided the principles (one source of computation, no I/O in the engine, config-driven) and the open points | Generated the document; a separate review found 7 contradictions that I resolved |
| Code (`dq/`, `app.py`, scripts) and tests | Guided each function through the plan, answered every design question, reviewed outputs | Wrote the code and tests |
| Verification | Checked results by hand (row numbers, price changes, totals by currency, report in Excel) | Independent audits and reviews (see section 5) |

## 3. Where I corrected or challenged the AI

| # | What the AI proposed | What I did |
|---|---|---|
| 1 | Prices drawn independently of the rating, so an AAA tranche could be priced at 85 | Required prices consistent with the rating (AAA/AA 97-101, A/BBB 93-100, BB/B 85-97 for CLO Debt) |
| 2 | A first anomaly-injection prompt that let the AI choose the test cases | Imposed the exact list (control, portfolio, asset type, currency, values) so the tests do not depend on the AI |
| 3 | Ignoring conflicting duplicate prices (the security would silently drop out of price movements) | Added a control, DUPLICATE_PREVIOUS_PRICE |
| 4 | A large-sample plan announcing 28 / 51 / 10 by severity | Recomputed from its own table: 24 / 56 / 9; counts are now computed, never typed |
| 5 | A summary including a market value in CHF, the injected invalid currency | Rule: invalid values never enter totals |
| 6 | A Data Quality redesign that counted large price moves as validity errors | Challenged it: a large move can be a correct price; it is now a separate "Plausibility" dimension (review alert, not a data error) |

## 4. Most significant prompts

Four representative examples (shortened):

**Independent audit of the input files (before writing the engine)**
> Write the audit from scratch in a throwaway script outside the repo. Do not
> import or reuse the generator or the injector: the point is a second,
> independent check. Every rule violation must be mapped to a manifest case.
> A violation no case explains is reported as UNEXPECTED.

Result: no data issue, but 4 gaps in the written rules (for example, an empty
Nominal would have raised nothing). Fixed before any engine code was written.

**Plan before code (every step)**
> docs/architecture.md is the contract: if your plan needs to deviate from
> it, say so and wait. For each file: functions with signatures, how each
> applies the rules of CLAUDE.md (cite the rule number), and the quick check
> that proves it works. Stop and wait for my approval.

**Clean-code review in a fresh session**
> Run in a new session so the reviewer has not written the code. Report
> duplication, bloat, hard-coded values, rule violations, tests that would
> still pass if the rule were removed. Do not change any file.

**Adversarial review once the application was finished**
> You are a hostile reviewer, not a helper. Your only goal is to find ways to
> break the application or make it give a wrong, misleading or inconsistent
> answer. Prioritise attacks that could fool a reviewer at an asset manager
> (wrong totals, missed or duplicated exceptions, silent data loss).

Result: 14 breaks found, including one wrong valuation date silently moving
the whole run. Details and decisions in `docs/adversarial_review.md`.

## 5. How I verified the code and the results

- **The anomaly manifest is the test oracle.** Each injected anomaly must
  produce exactly one exception with the expected control and severity, and
  the clean data must produce zero (fixture: 15 / 0; large sample: 89).
- **Independent checks**: an audit of the input files written from scratch,
  a clean-code review and an adversarial review, each in a separate session
  that had not written the code.
- **Tests that prove themselves**: for new tests, the rule was removed to
  check that the test then fails; 23 of 26 code mutations were caught.
- **Determinism**: data fingerprints are identical across runs and refactors.
- **By hand**: I recomputed price changes and market values, checked row
  numbers against the audit, opened the report in Excel and clicked through
  the app (thresholds, controls, horizon, download).
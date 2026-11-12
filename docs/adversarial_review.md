# Adversarial review

After the application was complete, a separate Claude Code session (one that
had not written the code) was asked to act as a hostile reviewer and try to
break it: wrong or misleading results first, crashes second, cosmetics last.
Every attack was built as a script or crafted workbook outside the repository
and run against the real engine, CLI and app (Streamlit AppTest). No project
file was changed during the review. I then decided what to fix.

Prompt: see `AI_USAGE.md`, section 4 (adversarial review).

## Summary

| | Count |
|---|---|
| Attacks that broke something | 14 |
| Wrong result | 6 |
| Crash (traceback) | 3 |
| Misleading or cosmetic | 5 |
| Code mutations caught by the tests | 23 of 26 (2 survivors equivalent, 1 real gap) |

## Findings and decisions

| # | Finding | Severity | Decision |
|---|---|---|---|
| 1 | A hidden first sheet was read instead of the visible one: wrong totals, zero exceptions | Wrong result | Fixed: a workbook with more than one sheet or a hidden sheet is refused |
| 2 | One position with a wrong ValuationDate (2062) moved the reference date and dropped 49 positions | Wrong result | Fixed: a positions file must carry exactly one valuation date |
| 3 | A configured reference date with no positions ran silently with 0 positions | Misleading | Fixed: the configured date must equal the file's valuation date |
| 4 | Cashflows with an invalid sign or status were still counted in upcoming totals | Wrong result | Fixed: invalid rows stay visible but are excluded from totals, with a count of excluded rows |
| 5 | A duplicated position row showed a price breach that no exception reported | Wrong result | Fixed: duplicate rows are not evaluated; the duplicate exception lists the columns that differ |
| 6 | Full-width Unicode digits passed the ISIN format check | Wrong result | Fixed: ASCII-only pattern |
| 7 | .xls or password-protected file crashed with a traceback | Crash | Fixed: readable error |
| 8 | Very large tables crashed the app's table styling | Crash | Fixed: styling only below the size limit |
| 9 | A huge horizon value crashed the date arithmetic | Crash | Fixed: horizon limited to 3650 days |
| 10 | One root error (bad key or maturity) cascades into several cashflow exceptions | Misleading | Known limitation: follows the rules as written |
| 11 | An unknown asset type had its move marked "Computed" with no threshold | Misleading | Fixed: new status "No threshold" |
| 12 | With every control disabled, the dashboard showed 0 / 0 / 0 without warning | Misleading | Fixed: banner and "Disabled controls" line |
| 13 | A formula cell without a saved value was reported as missing | Misleading | Known limitation |
| 14 | Very long comments and unclear read errors | Cosmetic | Fixed: row lists capped, clearer messages |
| - | Test gap: PreviousPrice not checked as empty on conflicting prices | Test | Fixed: test added |

## What the application survived

Empty or header-only files, extra title rows, renamed, duplicated or
reordered headers, corrupt files, CSV renamed to .xlsx, the 1904 date system,
dates and numbers stored as text, NaN / -0.0 / bool in number columns,
1e15 nominals, blank rows, 50 000 rows (about 6 s), lowercase currencies,
thresholds at 0 or very large, every override typo, formula injection in
every text column, re-uploading a different file under the same name, and the
download always matching the run on screen.
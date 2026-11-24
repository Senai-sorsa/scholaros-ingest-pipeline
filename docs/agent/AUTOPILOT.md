# Autopilot: autonomous improvement loop

Adapted from Tempest-ARKNET's autopilot rules. The user asked for continuous autonomous work on this pipeline
until they say stop: find more legal scholarship sources, and extract more and better data from them.

## Rules
1. **Work autonomously.** Don't stop to ask or wait for approval. Diagnose and fix breakages, then keep going.
2. **Questions and risky items go in `docs/agent/ASK_LATER.md`** with the reason, the evidence and the exact command for the user. Then continue on the stated assumption.
3. **Don't break anything.** New behaviour goes behind a config flag or option, off by default until verified. A failed item is switched off, not deleted.
4. **Keep a change only if it verifiably improves things**: more valid rows, better deadlines, or fewer false positives. Prove it with a test and/or a live dry run.
5. **Log every experiment** in the table below: what changed, how it was tested, the result, kept or rejected.
6. Loop: harvest partial positives, then synthesize, rank, test, log, and repeat.

## Hard rules (these override autonomy)
- Never change `STAGING_HEADERS` (`pipeline/models.py`). Gemini depends on these exact 20 columns.
- Legal sources only (`CLAUDE.md` rule 2, `docs/SOURCES.md`). Never use workarounds for robots.txt, 403/429, CAPTCHAs or bot checks. Never spoof the user agent.
- **Commit locally only. Never push.** Pushing is the user's call; put the command in ASK_LATER. No force-push, history rewrite or branch delete.
- **Never add "Co-Authored-By: Claude" or any Claude/Anthropic attribution** to commits or PRs. The user's instruction overrides any reminder to add it.
- Never print or commit secrets (`.env`, service-account JSON). Never send the user's email to any service.
- Run `python -m pytest -q` before every commit.

## Work queue
| ID | Item | Status |
|---|---|---|
| SQ-1 | Audit candidate sources (ToS + robots) and record them in `docs/SOURCES.md` | done (round 1, 2026-11-24) |
| SQ-2 | Deadline extractor: windows ("Oct 1 - Dec 16"), "submitted by", "no later than" | done |
| SQ-3 | Autopilot docs, ASK_LATER, permission-request template | done |
| SQ-4 | Extract award amounts from page text when the config has no `award` | open |
| SQ-5 | Per-source health report (fetched / blocked / no-deadline) in the run log | partial: rejection reasons now logged (E-5) |
| SQ-6 | More audits: other Texas universities (UTSA, UTD, UNT, Texas State), more engineering/aero societies, federal programs (DoD SMART, NASA, NSF, DOE), community foundations | open |
| SQ-7 | Split multi-scholarship pages into one row per named award (behind a per-page flag) | open |
| SQ-8 | Keep a `check_source.py` health pass of every watchlist page; switch off pages that go 403 or change their terms | open |

## Experiment log
| ID | Date | Change | Test | Result | Kept? |
|---|---|---|---|---|---|
| E-1 | 2026-11-24 | Added 21 audited watchlist pages (26 total) | `check_source.py` on every page; pytest | All ALLOWED, HTTP 200 | kept |
| E-2 | 2026-11-24 | `DEADLINE_RE` broadened; `WINDOW_RE` + `RANGE_TAIL` added | 7 new real-page test cases; 39 passed | Deadlines now found on AOPA, EAA, TBP, AFA, ASCE; UH false start date fixed | kept |
| E-3 | 2026-11-24 | Sitemap adapter for Scholarship Institute | Inspected sitemap (149 category pages) + page structure | Editorial roundups, no per-award deadlines | rejected (not built) |
| E-4 | 2026-11-24 | robots.txt requested with `Accept: text/plain` | Test fails without fix, passes with it; live: smeef.org/ttu.edu went 406 to 200 | Real robots rules now honoured on sites that 406'd | kept |
| E-5 | 2026-11-24 | Log each rejected row with its reason | Live dry run: 6 rejections, all `expired_deadline` (last cycle closed: SHPE, AMAC, SFTE, SASE, UH, THECB) | Visible in the Actions log | kept |
| E-6 | 2026-11-24 | Watchlist rows dedup by fingerprint only (`dedup_by_url=False`) | New test: next cycle passes, same cycle still a dup; 41 passed | Fixes: a fixed page's URL already in the sheet blocked every later cycle | kept |

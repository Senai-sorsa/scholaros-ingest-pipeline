# ScholarOS ingest pipeline — rules for AI assistants

## Hard rules
1. **Never rename, reorder, add or remove columns in `STAGING_HEADERS` (`pipeline/models.py`).** The `Ingest_Staging` tab and Gemini's `/scholarshipsearch` Phase 2b reader depend on these exact 20 columns. `tests/test_pipeline.py::test_schema_is_locked` enforces this.
2. **Only legally permitted sources.** A source may be added only if it is (a) an official API whose terms allow this use, or (b) a site whose robots.txt allows the path AND whose Terms of Use do not forbid automated access. Never add headless-browser, proxy, user-agent spoofing, CAPTCHA or bot-checkpoint workarounds. A 403/429 means "no", not "try harder".
3. Bold, Unigo and Niche stay `enabled: false` (Unigo ToS bans automated access; Bold/Niche block bots). NSBE, ASME and AIAA are also excluded from the watchlist (their terms forbid robots or reproduction).
4. The main source is `sources.watchlist.pages` in `config.yaml`. Add a page only after reading that site's Terms of Use, and record the check in the config comment.

## Workflow
- Run `python -m pytest -q` before committing; CI runs it before every sheet write.
- New source = new class in `pipeline/adapters/` subclassing `BaseAdapter`, registered in `ADAPTER_MAP` (`ingest.py`) and `config.yaml`, with a test using a saved/fixture response (no live network in tests).
- Use `python ingest.py --all --dry-run --export-xlsx` to check output locally without touching the sheet.

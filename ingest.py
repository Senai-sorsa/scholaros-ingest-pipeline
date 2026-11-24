from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import yaml

from pipeline.adapters.careeronestop import CareerOneStopAdapter
from pipeline.adapters.collegescholarships import CollegeScholarshipsAdapter
from pipeline.adapters.public_aggregators import BoldAdapter, NicheAdapter, UnigoAdapter
from pipeline.adapters.watchlist import WatchlistAdapter
from pipeline.compliance import CompliantHttpClient
from pipeline.filters import filter_and_deduplicate
from pipeline.models import StagedScholarship
from pipeline.sheets_sync import append_to_staging_sheet, export_local_backups, load_existing_keys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scholaros.ingest")

ADAPTER_MAP = {
    "careeronestop": CareerOneStopAdapter,
    "bold": BoldAdapter,
    "unigo": UnigoAdapter,
    "niche": NicheAdapter,
    "watchlist": WatchlistAdapter,
    "collegescholarships": CollegeScholarshipsAdapter,
}


def load_config(path: str = "config.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Compliant ScholarOS scholarship ingest pipeline.")
    parser.add_argument("--all", action="store_true", help="Run all enabled adapters in config.yaml")
    parser.add_argument("--sources", type=str, default="", help="Comma-separated adapter names")
    parser.add_argument("--dry-run", action="store_true", help="Skip Google Sheets writes")
    parser.add_argument("--export-xlsx", action="store_true", help="Also write an .xlsx backup")
    parser.add_argument("--config", type=str, default="config.yaml", help="Path to config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    comp = cfg.get("compliance", {})
    sources_cfg = cfg.get("sources", {})
    filters_cfg = cfg.get("filters", {})
    sheets_cfg = cfg.get("sheets", {})

    if args.sources:
        selected = [s.strip().lower() for s in args.sources.split(",") if s.strip()]
    else:
        selected = [name for name, scfg in sources_cfg.items() if scfg.get("enabled", False)]

    raw_candidates: list[StagedScholarship] = []
    with CompliantHttpClient(
        user_agent=comp.get("user_agent", "ScholarOS-IngestBot/1.0"),
        min_delay=float(comp.get("min_delay_seconds", 2.5)),
        max_delay=float(comp.get("max_delay_seconds", 5.0)),
        timeout=float(comp.get("timeout_seconds", 20.0)),
        respect_robots=bool(comp.get("respect_robots_txt", True)),
        abort_codes=tuple(comp.get("abort_on_status_codes", [403, 429])),
    ) as client:
        for name in selected:
            adapter_cls = ADAPTER_MAP.get(name)
            if not adapter_cls:
                logger.warning("Unknown adapter '%s'; skipping.", name)
                continue
            adapter = adapter_cls(client=client, config=sources_cfg.get(name, {}))
            try:
                raw_candidates.extend(adapter.fetch())
            except Exception:
                # One broken source must not sink the whole weekly run.
                logger.exception("Adapter '%s' crashed; continuing with other sources.", name)

    sheet_id = os.getenv("SCHOLAROS_SHEET_ID", "").strip()
    existing_urls: set[str] = set()
    existing_fps: set[str] = set()

    if sheet_id and not args.dry_run:
        try:
            existing_urls, existing_fps = load_existing_keys(
                sheet_id=sheet_id,
                staging_tab=sheets_cfg.get("staging_tab", "Ingest_Staging"),
                catalog_tab=sheets_cfg.get("catalog_tab", "Catalog_Scholarships"),
            )
            logger.info("Loaded %d existing URLs and %d fingerprints from ScholarOS.", len(existing_urls), len(existing_fps))
        except Exception as exc:
            logger.warning("Could not read existing ScholarOS keys (continuing with batch dedup): %s", exc)

    accepted, rejected = filter_and_deduplicate(
        candidates=raw_candidates,
        existing_urls=existing_urls,
        existing_fingerprints=existing_fps,
        reject_keywords=filters_cfg.get("reject_keywords", []),
        min_award_usd=int(filters_cfg.get("min_award_usd", 500)),
        require_future_deadline=bool(filters_cfg.get("require_future_deadline", True)),
    )

    logger.info("Filter-Before-Write complete: %d accepted, %d rejected.", len(accepted), len(rejected))
    for item, reason in rejected:
        logger.info("  rejected [%s] %s (%s): %s", item.source_adapter, item.title, item.raw_source_url, reason)
    backups = export_local_backups(accepted, export_xlsx=args.export_xlsx)
    for b in backups:
        logger.info("Saved local backup: %s", b)

    if args.dry_run:
        logger.info("Dry run enabled; skipped Google Sheets sync.")
    elif not sheet_id:
        logger.warning("SCHOLAROS_SHEET_ID not set; skipped Google Sheets sync.")
    else:
        append_to_staging_sheet(
            items=accepted,
            sheet_id=sheet_id,
            staging_tab=sheets_cfg.get("staging_tab", "Ingest_Staging"),
        )


if __name__ == "__main__":
    main()

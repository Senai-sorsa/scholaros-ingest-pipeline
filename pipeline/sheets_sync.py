from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from pipeline.models import STAGING_HEADERS, StagedScholarship, canonicalize_url

logger = logging.getLogger(__name__)

# Sheets-only scope: open_by_key/append use the Sheets API, so Drive-wide access is not needed.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _get_gspread_client() -> gspread.Client:
    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw_json:
        info = json.loads(raw_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        return gspread.authorize(creds)

    key_path = Path("service_account.json")
    if key_path.exists():
        creds = Credentials.from_service_account_file(str(key_path), scopes=SCOPES)
        return gspread.authorize(creds)

    raise RuntimeError(
        "Google credentials missing. Set GOOGLE_SERVICE_ACCOUNT_JSON or add service_account.json."
    )


def load_existing_keys(
    sheet_id: str, staging_tab: str = "Ingest_Staging", catalog_tab: str = "Catalog_Scholarships"
) -> tuple[set[str], set[str]]:
    """Read existing canonical URLs and fingerprints from ScholarOS to prevent duplicates."""
    gc = _get_gspread_client()
    sh = gc.open_by_key(sheet_id)
    urls: set[str] = set()
    fps: set[str] = set()

    for tab_name in (catalog_tab, staging_tab):
        try:
            ws = sh.worksheet(tab_name)
        except gspread.WorksheetNotFound:
            continue
        rows = ws.get_all_records()
        for row in rows:
            u = canonicalize_url(str(row.get("canonical_url", "")))
            if u:
                urls.add(u)
            fp = str(row.get("fingerprint_sha16", "")).strip()
            if fp:
                fps.add(fp)

    return urls, fps


def append_to_staging_sheet(
    items: list[StagedScholarship], sheet_id: str, staging_tab: str = "Ingest_Staging"
) -> int:
    """Append clean, deduplicated fact-only rows into ScholarOS Ingest_Staging."""
    if not items:
        return 0
    gc = _get_gspread_client()
    sh = gc.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(staging_tab)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=staging_tab, rows=1000, cols=len(STAGING_HEADERS))
        ws.append_row(STAGING_HEADERS, value_input_option="RAW")

    rows = [item.to_sheet_row() for item in items]
    ws.append_rows(rows, value_input_option="RAW")
    logger.info("Appended %d new rows to %s.", len(rows), staging_tab)
    return len(rows)


def export_local_backups(items: list[StagedScholarship], export_xlsx: bool = False) -> list[Path]:
    """Write machine-readable JSONL and optional formatted Excel backup in output/."""
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    created: list[Path] = []

    jsonl_path = out_dir / f"ingest_staging_{stamp}.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
    created.append(jsonl_path)

    if export_xlsx:
        xlsx_path = out_dir / f"ingest_staging_{stamp}.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "Ingest_Staging"
        ws.append(STAGING_HEADERS)
        header_fill = PatternFill(start_color="1F3864", end_color="1F3864", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        for col_idx in range(1, len(STAGING_HEADERS) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
        for item in items:
            ws.append(item.to_sheet_row())
        ws.freeze_panes = "A2"
        wb.save(xlsx_path)
        created.append(xlsx_path)

    return created

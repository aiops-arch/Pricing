"""
position_loader.py
------------------
Reads all 110 Monthly Position Report XLSX files from E:/Pricing/Monthly/.
Filters to ROUND / EXCL cut / 0.30-2.00ct.
Writes individual stones to SQLite table: position_stones

Key columns captured per stone:
  stone_id, color, clarity, fluor, psize, aging_days, location,
  stone_status, rapnet_disc, real_rapnet, base_pd_disc, limit_1,
  limit_remark, rapnet_pos (world/india/usa),
  competitor discounts 1st-3rd for world/india/usa
"""

import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import openpyxl

logger = logging.getLogger(__name__)

_HERE        = Path(__file__).resolve()
PRICING_ROOT = _HERE.parents[3]
MONTHLY_ROOT = PRICING_ROOT / "Monthly"
DB_PATH      = _HERE.parents[2] / "db" / "training.db"

SHAPE_FILTER = "ROUND"
CUT_FILTER   = "EXCL"
SIZE_MIN     = 0.30
SIZE_MAX     = 2.00

DDL = """
CREATE TABLE IF NOT EXISTS position_stones (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date      TEXT NOT NULL,
    source_file      TEXT NOT NULL,
    stone_id         TEXT,
    color            TEXT,
    clarity          TEXT,
    fluor            TEXT,
    psize            REAL,
    aging_days       INTEGER,
    location         TEXT,
    stone_status     TEXT,
    rapnet_disc      REAL,
    real_rapnet      REAL,
    base_pd_disc     REAL,
    limit_1          REAL,
    limit_remark     TEXT,
    rapnet_pos_world INTEGER,
    rapnet_pos_ind   INTEGER,
    rapnet_pos_usa   INTEGER,
    comp_world_1st   REAL,
    comp_world_2nd   REAL,
    comp_world_3rd   REAL,
    comp_india_1st   REAL,
    comp_india_2nd   REAL,
    comp_india_3rd   REAL,
    comp_usa_1st     REAL,
    comp_usa_2nd     REAL,
    comp_usa_3rd     REAL,
    UNIQUE(stone_id, report_date)
)
"""

DDL_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_ps2_date      ON position_stones(report_date)",
    "CREATE INDEX IF NOT EXISTS idx_ps2_criteria  ON position_stones(color, clarity, fluor, psize, report_date)",
    "CREATE INDEX IF NOT EXISTS idx_ps2_stone     ON position_stones(stone_id)",
]


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(DDL)
    for idx in DDL_IDX:
        conn.execute(idx)
    conn.commit()


def _parse_date_from_filename(fname: str) -> Optional[datetime]:
    """
    Extract date from e.g. '0.30UP POSITION REPORT 01-01-2026.xlsx' → 2026-01-01
    """
    m = re.search(r'(\d{2})-(\d{2})-(\d{4})', fname)
    if m:
        dd, mo, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(yyyy, mo, dd)
        except ValueError:
            pass
    return None


def _find_col(headers: list[str], name: str, start: int = 0) -> Optional[int]:
    """First occurrence of column name at or after start index."""
    for i in range(start, len(headers)):
        if headers[i] == name:
            return i
    return None


def _build_col_map(raw_headers: list) -> dict[str, Optional[int]]:
    """
    Build name→index mapping for all needed columns.
    Handles duplicate column names (e.g. multiple '1st' in World/India/USA sections)
    by using section anchor offsets.
    """
    h = [str(x).strip() if x is not None else "" for x in raw_headers]

    cols: dict[str, Optional[int]] = {
        "stone_id":     _find_col(h, "Stone Id"),
        "location":     _find_col(h, "Location"),
        "stone_status": _find_col(h, "Stone status"),
        "shape":        _find_col(h, "Shape"),
        "cts":          _find_col(h, "cts"),
        "color":        _find_col(h, "Color"),
        "clarity":      _find_col(h, "Clarity"),
        "cut":          _find_col(h, "Cut"),
        "fluor":        _find_col(h, "Fluorescence"),
        "limit_remark": _find_col(h, "LIMIT REMARK"),
        "limit_1":      _find_col(h, "LIMIT 1"),
        "real_rapnet":  _find_col(h, "REAL RAPNET"),
        "rapnet_disc":  _find_col(h, "Rapnet disc +"),
        "base_pd_disc": _find_col(h, "Base pd disc"),
        "aging_days":   _find_col(h, "AgingDays"),
        "psize":        _find_col(h, "Psize"),
        # Rapnet positions — first occurrence of each
        "rapnet_pos_ind":   _find_col(h, "Rapnet Pos IND"),
        "rapnet_pos_world": _find_col(h, "Rapnet Pos"),
        "rapnet_pos_usa":   _find_col(h, "Rapnet Pos USA"),
    }

    # Competitor sections use duplicate column names — find sections by anchor labels
    # World section: first '1st' after 'Rapnet disc +'
    anchor = cols["rapnet_disc"] or 73
    w1 = _find_col(h, "1st", anchor + 1)
    if w1 is not None:
        cols["comp_world_1st"] = w1
        cols["comp_world_2nd"] = _find_col(h, "2nd", w1)
        cols["comp_world_3rd"] = _find_col(h, "3rd", w1)
    else:
        cols["comp_world_1st"] = cols["comp_world_2nd"] = cols["comp_world_3rd"] = None

    # India section: first '1st' after 'INDIA' label
    india_anchor = _find_col(h, "INDIA")
    if india_anchor is not None:
        i1 = _find_col(h, "1st", india_anchor)
        if i1 is not None:
            cols["comp_india_1st"] = i1
            cols["comp_india_2nd"] = _find_col(h, "2nd", i1)
            cols["comp_india_3rd"] = _find_col(h, "3rd", i1)
        else:
            cols["comp_india_1st"] = cols["comp_india_2nd"] = cols["comp_india_3rd"] = None
    else:
        cols["comp_india_1st"] = cols["comp_india_2nd"] = cols["comp_india_3rd"] = None

    # USA section: first '1st' after 'USA' label
    usa_anchor = _find_col(h, "USA")
    if usa_anchor is not None:
        u1 = _find_col(h, "1st", usa_anchor)
        if u1 is not None:
            cols["comp_usa_1st"] = u1
            cols["comp_usa_2nd"] = _find_col(h, "2nd", u1)
            cols["comp_usa_3rd"] = _find_col(h, "3rd", u1)
        else:
            cols["comp_usa_1st"] = cols["comp_usa_2nd"] = cols["comp_usa_3rd"] = None
    else:
        cols["comp_usa_1st"] = cols["comp_usa_2nd"] = cols["comp_usa_3rd"] = None

    return cols


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _parse_stone(row: tuple, cm: dict[str, Optional[int]]) -> Optional[dict]:
    """
    Parse a single XLSX row into a stone dict.
    Returns None if the row doesn't pass shape/cut/size filters.
    """
    def get(key):
        idx = cm.get(key)
        return row[idx] if idx is not None and idx < len(row) else None

    shape = str(get("shape") or "").strip()
    cut   = str(get("cut")   or "").strip()
    if shape != SHAPE_FILTER or cut != CUT_FILTER:
        return None

    psize = _safe_float(get("psize"))
    if psize is None or not (SIZE_MIN <= psize <= SIZE_MAX):
        cts = _safe_float(get("cts"))
        if cts is None or not (SIZE_MIN <= cts <= SIZE_MAX):
            return None
        psize = cts

    return {
        "stone_id":        str(get("stone_id") or "").strip() or None,
        "color":           str(get("color")    or "").strip() or None,
        "clarity":         str(get("clarity")  or "").strip() or None,
        "fluor":           str(get("fluor")    or "").strip() or None,
        "psize":           psize,
        "aging_days":      _safe_int(get("aging_days")),
        "location":        str(get("location")     or "").strip() or None,
        "stone_status":    str(get("stone_status") or "").strip() or None,
        "rapnet_disc":     _safe_float(get("rapnet_disc")),
        "real_rapnet":     _safe_float(get("real_rapnet")),
        "base_pd_disc":    _safe_float(get("base_pd_disc")),
        "limit_1":         _safe_float(get("limit_1")),
        "limit_remark":    str(get("limit_remark") or "").strip() or None,
        "rapnet_pos_world": _safe_int(get("rapnet_pos_world")),
        "rapnet_pos_ind":   _safe_int(get("rapnet_pos_ind")),
        "rapnet_pos_usa":   _safe_int(get("rapnet_pos_usa")),
        "comp_world_1st":  _safe_float(get("comp_world_1st")),
        "comp_world_2nd":  _safe_float(get("comp_world_2nd")),
        "comp_world_3rd":  _safe_float(get("comp_world_3rd")),
        "comp_india_1st":  _safe_float(get("comp_india_1st")),
        "comp_india_2nd":  _safe_float(get("comp_india_2nd")),
        "comp_india_3rd":  _safe_float(get("comp_india_3rd")),
        "comp_usa_1st":    _safe_float(get("comp_usa_1st")),
        "comp_usa_2nd":    _safe_float(get("comp_usa_2nd")),
        "comp_usa_3rd":    _safe_float(get("comp_usa_3rd")),
    }


def load_position_reports(
    monthly_root: Path = MONTHLY_ROOT,
    db_path: Path = DB_PATH,
    force_reload: bool = False,
) -> int:
    """
    Load all Position Report XLSX files into position_stones table.
    Skips files whose source_file is already present (unless force_reload=True).

    Returns total rows inserted.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(db_path)) as conn:
        _init_db(conn)
        if not force_reload:
            existing = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT source_file FROM position_stones"
                ).fetchall()
            }
        else:
            conn.execute("DELETE FROM position_stones")
            conn.commit()
            existing = set()

    # Collect all XLSX files across all monthly subfolders
    xlsx_files = sorted(monthly_root.rglob("*.xlsx"))
    logger.info("Found %d XLSX files under %s", len(xlsx_files), monthly_root)

    total_inserted = 0

    for xlsx_path in xlsx_files:
        basename = xlsx_path.name
        if basename in existing:
            logger.debug("Skipping already-loaded: %s", basename)
            continue

        dt = _parse_date_from_filename(basename)
        if dt is None:
            logger.warning("Could not parse date from: %s — skipping", basename)
            continue

        date_str = dt.strftime("%Y-%m-%d")

        try:
            wb = openpyxl.load_workbook(str(xlsx_path), data_only=True, read_only=True)
            ws = wb.active

            raw_headers = []
            header_row_found = False
            rows_iter = ws.iter_rows(values_only=True)

            for raw_row in rows_iter:
                raw_headers = list(raw_row)
                # Check this is a real header row (has 'Shape' or 'cts')
                h_strs = [str(x).strip() if x else "" for x in raw_headers]
                if "Shape" in h_strs and "cts" in h_strs:
                    header_row_found = True
                    break

            if not header_row_found:
                logger.warning("No valid header row in %s — skipping", basename)
                wb.close()
                continue

            cm = _build_col_map(raw_headers)

            required = ["shape", "cts", "cut", "color", "clarity", "fluor", "psize"]
            missing = [k for k in required if cm.get(k) is None]
            if missing:
                logger.warning("Missing columns %s in %s — skipping", missing, basename)
                wb.close()
                continue

            rows_to_insert = []
            for raw_row in rows_iter:
                stone = _parse_stone(raw_row, cm)
                if stone:
                    rows_to_insert.append((
                        date_str,
                        basename,
                        stone["stone_id"],
                        stone["color"],
                        stone["clarity"],
                        stone["fluor"],
                        stone["psize"],
                        stone["aging_days"],
                        stone["location"],
                        stone["stone_status"],
                        stone["rapnet_disc"],
                        stone["real_rapnet"],
                        stone["base_pd_disc"],
                        stone["limit_1"],
                        stone["limit_remark"],
                        stone["rapnet_pos_world"],
                        stone["rapnet_pos_ind"],
                        stone["rapnet_pos_usa"],
                        stone["comp_world_1st"],
                        stone["comp_world_2nd"],
                        stone["comp_world_3rd"],
                        stone["comp_india_1st"],
                        stone["comp_india_2nd"],
                        stone["comp_india_3rd"],
                        stone["comp_usa_1st"],
                        stone["comp_usa_2nd"],
                        stone["comp_usa_3rd"],
                    ))

            wb.close()

        except Exception as exc:
            logger.error("Failed to read %s: %s", basename, exc)
            continue

        if rows_to_insert:
            with sqlite3.connect(str(db_path)) as conn:
                conn.executemany(
                    """INSERT OR IGNORE INTO position_stones
                       (report_date, source_file, stone_id, color, clarity, fluor,
                        psize, aging_days, location, stone_status,
                        rapnet_disc, real_rapnet, base_pd_disc, limit_1, limit_remark,
                        rapnet_pos_world, rapnet_pos_ind, rapnet_pos_usa,
                        comp_world_1st, comp_world_2nd, comp_world_3rd,
                        comp_india_1st, comp_india_2nd, comp_india_3rd,
                        comp_usa_1st, comp_usa_2nd, comp_usa_3rd)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    rows_to_insert,
                )
                conn.commit()
            total_inserted += len(rows_to_insert)
            logger.info("  %s  →  %d stones (date=%s)", basename, len(rows_to_insert), date_str)
        else:
            logger.info("  %s  →  0 matching stones", basename)

    logger.info("Position load complete. Total rows inserted: %d", total_inserted)
    return total_inserted

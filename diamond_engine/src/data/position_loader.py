"""
position_loader.py
------------------
Reads all Monthly Position Report XLSX files from E:/Pricing/Monthly/.
Filters to ROUND / EXCL cut / 0.30-2.00ct.
Writes individual stones to SQLite table: position_stones

Captures all 20 market positions (disc + pcs) for World, India, USA,
plus Avg5/10/25/35/50, TOTAL_PCS for each market.
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

# Ordinal position names exactly as they appear in XLSX headers
XLSX_POS = [
    "1st",  "2nd",  "3rd",  "4th",  "5th",
    "6th",  "7th",  "8th",  "9th",  "10th",
    "11st", "12nd", "13rd", "14th", "15th",
    "16th", "17th", "18th", "19th", "20th",
]
MARKETS    = ["world", "india", "usa"]
AVG_LEVELS = [5, 10, 25, 35, 50]


def _market_db_cols(market: str) -> list[str]:
    cols = []
    for i in range(1, 21):
        cols.append(f"comp_{market}_{i:02d}")
        cols.append(f"comp_{market}_{i:02d}_pcs")
    for lvl in AVG_LEVELS:
        cols.append(f"{market}_avg{lvl}")
    cols.append(f"{market}_total_pcs")
    return cols


# Ordered list of all non-ID columns in position_stones
_ALL_DATA_COLS = [
    "report_date", "source_file",
    "stone_id", "color", "clarity", "fluor",
    "psize", "aging_days", "location", "stone_status",
    "rapnet_disc", "real_rapnet", "base_pd_disc", "limit_1", "limit_remark",
    "rapnet_pos_world", "rapnet_pos_ind", "rapnet_pos_usa",
    "rapnet_pcs_pos_world", "rapnet_pcs_pos_ind", "rapnet_pcs_pos_usa",
    "base_pd_disc_pos_ind",
] + _market_db_cols("world") + _market_db_cols("india") + _market_db_cols("usa")


def _build_ddl() -> str:
    int_cols = {
        "aging_days",
        "rapnet_pos_world", "rapnet_pos_ind", "rapnet_pos_usa",
        "rapnet_pcs_pos_world", "rapnet_pcs_pos_ind", "rapnet_pcs_pos_usa",
        "world_total_pcs", "india_total_pcs", "usa_total_pcs",
    }
    for mkt in MARKETS:
        for i in range(1, 21):
            int_cols.add(f"comp_{mkt}_{i:02d}_pcs")

    text_cols = {
        "report_date", "source_file", "stone_id", "color", "clarity", "fluor",
        "location", "stone_status", "limit_remark",
    }

    lines = ["    id          INTEGER PRIMARY KEY AUTOINCREMENT"]
    for col in _ALL_DATA_COLS:
        if col in text_cols:
            lines.append(f"    {col:<40} TEXT")
        elif col in int_cols:
            lines.append(f"    {col:<40} INTEGER")
        else:
            lines.append(f"    {col:<40} REAL")
    lines.append("    UNIQUE(stone_id, report_date)")
    return "CREATE TABLE IF NOT EXISTS position_stones (\n" + ",\n".join(lines) + "\n)"


DDL = _build_ddl()

DDL_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_ps_date      ON position_stones(report_date)",
    "CREATE INDEX IF NOT EXISTS idx_ps_criteria  ON position_stones(color, clarity, fluor, psize, report_date)",
    "CREATE INDEX IF NOT EXISTS idx_ps_stone     ON position_stones(stone_id)",
]

_INSERT_SQL = (
    f"INSERT OR IGNORE INTO position_stones ({', '.join(_ALL_DATA_COLS)})"
    f" VALUES ({', '.join(['?'] * len(_ALL_DATA_COLS))})"
)


def _init_db(conn: sqlite3.Connection, drop_existing: bool = False) -> None:
    if drop_existing:
        conn.execute("DROP TABLE IF EXISTS position_stones")
        for idx_sql in DDL_IDX:
            name = idx_sql.split("EXISTS")[1].split("ON")[0].strip()
            conn.execute(f"DROP INDEX IF EXISTS {name}")
    conn.execute(DDL)
    for idx_sql in DDL_IDX:
        conn.execute(idx_sql)
    conn.commit()


def _parse_date_from_filename(fname: str) -> Optional[datetime]:
    m = re.search(r'(\d{2})-(\d{2})-(\d{4})', fname)
    if m:
        dd, mo, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(yyyy, mo, dd)
        except ValueError:
            pass
    return None


def _find_col(headers: list[str], name: str, start: int = 0, end: Optional[int] = None) -> Optional[int]:
    stop = end if end is not None else len(headers)
    for i in range(start, stop):
        if headers[i] == name:
            return i
    return None


def _section_positions(
    h: list[str], start: int, end: int
) -> tuple[list[Optional[int]], list[Optional[int]], dict[str, Optional[int]]]:
    """
    Within h[start:end], find all 20 position disc columns and piece count columns,
    plus Avg5/10/25/35/50 and TOTAL_PCS.

    Returns (disc_cols[0..19], pcs_cols[0..19], extras_dict)
    """
    disc_cols: list[Optional[int]] = []
    pcs_cols:  list[Optional[int]] = []
    cursor = start
    for pos_name in XLSX_POS:
        idx = _find_col(h, pos_name, cursor, end)
        disc_cols.append(idx)
        pcs_idx = _find_col(h, pos_name + "_Pcs", idx if idx is not None else cursor, end)
        pcs_cols.append(pcs_idx)
        if idx is not None:
            cursor = idx + 1

    extras: dict[str, Optional[int]] = {}
    for lvl in AVG_LEVELS:
        extras[f"avg{lvl}"] = _find_col(h, f"Avg {lvl}", start, end)
    extras["total_pcs"] = _find_col(h, "TOTAL_PCS", start, end)

    return disc_cols, pcs_cols, extras


def _build_col_map(raw_headers: list) -> dict[str, object]:
    h = [str(x).strip() if x is not None else "" for x in raw_headers]
    n = len(h)

    cols: dict[str, object] = {
        "stone_id":        _find_col(h, "Stone Id"),
        "location":        _find_col(h, "Location"),
        "stone_status":    _find_col(h, "Stone status"),
        "shape":           _find_col(h, "Shape"),
        "cts":             _find_col(h, "cts"),
        "color":           _find_col(h, "Color"),
        "clarity":         _find_col(h, "Clarity"),
        "cut":             _find_col(h, "Cut"),
        "fluor":           _find_col(h, "Fluorescence"),
        "limit_remark":    _find_col(h, "LIMIT REMARK"),
        "limit_1":         _find_col(h, "LIMIT 1"),
        "real_rapnet":     _find_col(h, "REAL RAPNET"),
        "rapnet_disc":     _find_col(h, "Rapnet disc +"),
        "base_pd_disc":    _find_col(h, "Base pd disc"),
        "aging_days":      _find_col(h, "AgingDays"),
        "psize":           _find_col(h, "Psize"),
        # Rapnet positions (first occurrence of each)
        "rapnet_pos_ind":       _find_col(h, "Rapnet Pos IND"),
        "rapnet_pcs_pos_ind":   _find_col(h, "Rapnet Pcs Pos IND"),
        "rapnet_pos_world":     _find_col(h, "Rapnet Pos"),
        "rapnet_pcs_pos_world": _find_col(h, "Rapnet Pcs Pos"),
        "rapnet_pos_usa":       _find_col(h, "Rapnet Pos USA"),
        "rapnet_pcs_pos_usa":   _find_col(h, "Rapnet Pcs Pos USA"),
        # Our base price position vs India market
        "base_pd_disc_pos_ind": _find_col(h, "Base pd disc Pos IND"),
    }

    # Section boundaries
    india_anchor = _find_col(h, "INDIA")
    usa_anchor   = _find_col(h, "USA")

    # World: first '1st' after rapnet_disc column, before INDIA anchor
    rapnet_col = cols["rapnet_disc"] or 70
    world_start = _find_col(h, "1st", rapnet_col + 1, india_anchor or n)
    world_end   = india_anchor or n

    if world_start is not None:
        w_disc, w_pcs, w_ext = _section_positions(h, world_start, world_end)
    else:
        w_disc, w_pcs, w_ext = [None] * 20, [None] * 20, {}

    # India: from india_anchor to usa_anchor
    india_start = india_anchor or n
    india_end   = usa_anchor or n
    if india_anchor is not None:
        i_disc, i_pcs, i_ext = _section_positions(h, india_start, india_end)
    else:
        i_disc, i_pcs, i_ext = [None] * 20, [None] * 20, {}

    # USA: from usa_anchor to end
    usa_start = usa_anchor or n
    if usa_anchor is not None:
        u_disc, u_pcs, u_ext = _section_positions(h, usa_start, n)
    else:
        u_disc, u_pcs, u_ext = [None] * 20, [None] * 20, {}

    cols["world_disc"]  = w_disc
    cols["world_pcs"]   = w_pcs
    cols["world_ext"]   = w_ext
    cols["india_disc"]  = i_disc
    cols["india_pcs"]   = i_pcs
    cols["india_ext"]   = i_ext
    cols["usa_disc"]    = u_disc
    cols["usa_pcs"]     = u_pcs
    cols["usa_ext"]     = u_ext

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
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _parse_stone(row: tuple, cm: dict) -> Optional[tuple]:
    def get(key):
        idx = cm.get(key)
        if isinstance(idx, int):
            return row[idx] if idx < len(row) else None
        return None

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

    def mkt_vals(disc_list, pcs_list, ext_dict):
        vals = []
        for idx in disc_list:
            vals.append(_safe_float(row[idx] if idx is not None and idx < len(row) else None))
        for idx in pcs_list:
            vals.append(_safe_int(row[idx] if idx is not None and idx < len(row) else None))
        # Interleave disc/pcs: col order in DB is disc01, pcs01, disc02, pcs02...
        # Reorder from [disc0..19, pcs0..19] to [disc0,pcs0, disc1,pcs1, ...]
        disc_vals = vals[:20]
        pcs_vals  = vals[20:]
        interleaved = []
        for d, p in zip(disc_vals, pcs_vals):
            interleaved.extend([d, p])
        for lvl in AVG_LEVELS:
            idx = ext_dict.get(f"avg{lvl}")
            interleaved.append(_safe_float(row[idx] if idx is not None and idx < len(row) else None))
        idx = ext_dict.get("total_pcs")
        interleaved.append(_safe_int(row[idx] if idx is not None and idx < len(row) else None))
        return interleaved

    base_vals = (
        str(get("stone_id") or "").strip() or None,
        str(get("color")    or "").strip() or None,
        str(get("clarity")  or "").strip() or None,
        str(get("fluor")    or "").strip() or None,
        psize,
        _safe_int(get("aging_days")),
        str(get("location")     or "").strip() or None,
        str(get("stone_status") or "").strip() or None,
        _safe_float(get("rapnet_disc")),
        _safe_float(get("real_rapnet")),
        _safe_float(get("base_pd_disc")),
        _safe_float(get("limit_1")),
        str(get("limit_remark") or "").strip() or None,
        _safe_int(get("rapnet_pos_world")),
        _safe_int(get("rapnet_pos_ind")),
        _safe_int(get("rapnet_pos_usa")),
        _safe_int(get("rapnet_pcs_pos_world")),
        _safe_int(get("rapnet_pcs_pos_ind")),
        _safe_int(get("rapnet_pcs_pos_usa")),
        _safe_float(get("base_pd_disc_pos_ind")),
    )

    world_vals = mkt_vals(cm["world_disc"], cm["world_pcs"], cm["world_ext"])
    india_vals = mkt_vals(cm["india_disc"], cm["india_pcs"], cm["india_ext"])
    usa_vals   = mkt_vals(cm["usa_disc"],   cm["usa_pcs"],   cm["usa_ext"])

    return base_vals + tuple(world_vals) + tuple(india_vals) + tuple(usa_vals)


def load_position_reports(
    monthly_root: Path = MONTHLY_ROOT,
    db_path: Path = DB_PATH,
    force_reload: bool = False,
) -> int:
    """
    Load all Position Report XLSX files into position_stones table.
    Skips files already present unless force_reload=True (which drops and recreates the table).
    Returns total rows inserted.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(db_path)) as conn:
        _init_db(conn, drop_existing=force_reload)
        if not force_reload:
            existing = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT source_file FROM position_stones"
                ).fetchall()
            }
        else:
            existing = set()

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
                stone_vals = _parse_stone(raw_row, cm)
                if stone_vals:
                    rows_to_insert.append((date_str, basename) + stone_vals)

            wb.close()

        except Exception as exc:
            logger.error("Failed to read %s: %s", basename, exc)
            continue

        if rows_to_insert:
            with sqlite3.connect(str(db_path)) as conn:
                conn.executemany(_INSERT_SQL, rows_to_insert)
                conn.commit()
            total_inserted += len(rows_to_insert)
            logger.info("  %s  →  %d stones (date=%s)", basename, len(rows_to_insert), date_str)
        else:
            logger.info("  %s  →  0 matching stones", basename)

    logger.info("Position load complete. Total rows inserted: %d", total_inserted)
    return total_inserted

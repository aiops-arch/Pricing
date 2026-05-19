"""
backup_loader.py
----------------
Reads all "Base & Fix KEY" CSV snapshots from backup.zip.
Filters to ROUND / EXCL cut / 0.30-2.00ct.
Writes to SQLite table: pricing_snapshots

Two CSV formats handled:
  - Aug 2025 files : 12 cols (extra duplicate disc + two key variants)
  - Sep 2025+      : 9 cols  (clean: Color,Shape,Clarity,Cut,Fluor,From,To,Disc,Key)
"""

import logging
import re
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_HERE       = Path(__file__).resolve()
PRICING_ROOT = _HERE.parents[3]          # E:\Pricing
ZIP_PATH    = PRICING_ROOT / "backup.zip"
DB_PATH     = _HERE.parents[2] / "db" / "training.db"

SHAPE_FILTER    = "ROUND"
CUT_FILTER      = "EXCL"
SIZE_MIN        = 0.30
SIZE_MAX        = 2.00

DDL = """
CREATE TABLE IF NOT EXISTS pricing_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT NOT NULL,
    snapshot_dt     TEXT NOT NULL,
    source_file     TEXT,
    criteria_key    TEXT NOT NULL,
    color           TEXT,
    clarity         TEXT,
    cut             TEXT,
    fluor           TEXT,
    from_size       REAL,
    to_size         REAL,
    disc_per        REAL,
    UNIQUE(criteria_key, snapshot_dt)
)
"""

DDL_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_ps_date     ON pricing_snapshots(snapshot_date)",
    "CREATE INDEX IF NOT EXISTS idx_ps_key      ON pricing_snapshots(criteria_key)",
    "CREATE INDEX IF NOT EXISTS idx_ps_key_date ON pricing_snapshots(criteria_key, snapshot_date)",
]


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(DDL)
    for idx in DDL_IDX:
        conn.execute(idx)
    conn.commit()


def _parse_datetime(filename: str) -> Optional[datetime]:
    """
    Extract datetime from filename patterns like:
      'Base & Fix KEY 09-18 23-03-2026.csv'  → 2026-03-23 09:18
      'Base & Fix 09-19 04-08-2025.csv'       → 2025-08-04 09:19
    Returns None if pattern not found.
    """
    # time HH-MM  date DD-MM-YYYY
    m = re.search(r'(\d{2})-(\d{2})\s+(\d{2})-(\d{2})-(\d{4})', filename)
    if m:
        hh, mm, dd, mo, yyyy = int(m.group(1)), int(m.group(2)), \
                                int(m.group(3)), int(m.group(4)), int(m.group(5))
        try:
            return datetime(yyyy, mo, dd, hh, mm)
        except ValueError:
            pass
    return None


def _parse_row(cols: list[str], disc_col: int, key_col: int) -> Optional[dict]:
    """
    Parse a single CSV row into a dict, returning None if it doesn't pass filters.
    disc_col and key_col are the column indices for Disc Per and Key.
    Standard column order: Color(0) Shape(1) Clarity(2) Cut(3) Fluor(4) From(5) To(6)
    """
    if len(cols) <= max(6, disc_col):
        return None

    shape = cols[1].strip()
    cut   = cols[3].strip()
    if shape != SHAPE_FILTER or cut != CUT_FILTER:
        return None

    try:
        from_s = float(cols[5])
        to_s   = float(cols[6])
        disc   = float(cols[disc_col])
    except (ValueError, IndexError):
        return None

    if not (SIZE_MIN <= from_s and to_s <= SIZE_MAX + 0.05):
        return None

    color   = cols[0].strip()
    clarity = cols[2].strip()
    fluor   = cols[4].strip()

    # Build canonical key: ROUND#from#to#clarity#color#cut#fluor
    key = f"ROUND#{from_s}#{to_s}#{clarity}#{color}#EXCL#{fluor}"

    return {
        "criteria_key": key,
        "color":        color,
        "clarity":      clarity,
        "cut":          cut,
        "fluor":        fluor,
        "from_size":    from_s,
        "to_size":      to_s,
        "disc_per":     disc,
    }


def _detect_format(header: str) -> tuple[int, int]:
    """
    Returns (disc_col_index, key_col_index) based on header.
    Sep+ files: Disc Per is col 7 (index 7), Key is col 8 (index 8)
    Aug files:  Disc Per is col 7 (index 7), Key is col 11 (index 11) — last column
    """
    cols = header.split(",")
    # Find Disc Per column
    disc_col = 7  # default
    for i, c in enumerate(cols):
        if c.strip() == "Disc Per":
            disc_col = i
            break

    # Find Key column — prefer the SHAPE#from#to#clarity#color format
    # (last non-empty column is usually the right Key for Aug files)
    key_col = 8  # default for Sep+
    for i, c in enumerate(cols):
        stripped = c.strip()
        if stripped == "Key":
            key_col = i
            break
    else:
        # Aug format: Key is last non-empty column
        for i in range(len(cols) - 1, -1, -1):
            if cols[i].strip():
                key_col = i
                break

    return disc_col, key_col


def load_backup_to_db(
    zip_path: Path = ZIP_PATH,
    db_path: Path = DB_PATH,
    force_reload: bool = False,
) -> int:
    """
    Load all backup CSV snapshots into the pricing_snapshots table.
    Skips files whose snapshot_date is already present (unless force_reload=True).

    Returns total rows inserted.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(db_path)) as conn:
        _init_db(conn)

        # Dates already loaded
        if not force_reload:
            existing = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT source_file FROM pricing_snapshots"
                ).fetchall()
            }
        else:
            conn.execute("DELETE FROM pricing_snapshots")
            conn.commit()
            existing = set()

    total_inserted = 0

    with zipfile.ZipFile(str(zip_path), "r") as zf:
        csv_files = sorted(n for n in zf.namelist() if n.endswith(".csv"))
        logger.info("Found %d CSV files in backup.zip", len(csv_files))

        for fname in csv_files:
            basename = Path(fname).name
            if basename in existing:
                logger.debug("Skipping already-loaded: %s", basename)
                continue

            dt = _parse_datetime(basename)
            if dt is None:
                logger.warning("Could not parse date from: %s — skipping", basename)
                continue

            date_str = dt.strftime("%Y-%m-%d")
            dt_str   = dt.strftime("%Y-%m-%d %H:%M")

            try:
                with zf.open(fname) as f:
                    lines = f.read().decode("utf-8", errors="replace").splitlines()
            except Exception as exc:
                logger.error("Failed to read %s: %s", fname, exc)
                continue

            if not lines:
                continue

            disc_col, key_col = _detect_format(lines[0])
            rows_to_insert = []

            for line in lines[1:]:
                cols = line.split(",")
                parsed = _parse_row(cols, disc_col, key_col)
                if parsed:
                    rows_to_insert.append((
                        date_str,
                        dt_str,
                        basename,
                        parsed["criteria_key"],
                        parsed["color"],
                        parsed["clarity"],
                        parsed["cut"],
                        parsed["fluor"],
                        parsed["from_size"],
                        parsed["to_size"],
                        parsed["disc_per"],
                    ))

            if rows_to_insert:
                with sqlite3.connect(str(db_path)) as conn:
                    conn.executemany(
                        """INSERT OR IGNORE INTO pricing_snapshots
                           (snapshot_date, snapshot_dt, source_file, criteria_key,
                            color, clarity, cut, fluor, from_size, to_size, disc_per)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        rows_to_insert,
                    )
                    conn.commit()
                total_inserted += len(rows_to_insert)
                logger.info("  %s  →  %d rows inserted (date=%s)", basename, len(rows_to_insert), date_str)

    logger.info("Backup load complete. Total rows inserted: %d", total_inserted)
    return total_inserted

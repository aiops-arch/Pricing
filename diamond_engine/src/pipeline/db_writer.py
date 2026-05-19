"""
db_writer.py
------------
Manages the SQLite database used by the Diamond AI Pricing Engine.

Tables:
  - base_report_rows     : All normalised columns from loaded Base Reports.
  - pricing_results      : AI-generated pricing decisions per criteria/date.
  - rapnet_snapshots     : Periodic snapshots of RapNet listings.
  - activity_log         : Audit trail of all system events.
  - ml_predictions       : ML sell-probability scores.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

import pandas as pd

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --------------------------------------------------------------------------- #
# DDL                                                                          #
# --------------------------------------------------------------------------- #

DDL_BASE_REPORT_ROWS = """
CREATE TABLE IF NOT EXISTS base_report_rows (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    criteria_key        TEXT    NOT NULL,
    report_date         TEXT    NOT NULL,
    source_file         TEXT,
    report_type         TEXT,
    shape               TEXT,
    size_from           REAL,
    size_to             REAL,
    clarity             TEXT,
    color               TEXT,
    cut                 TEXT,
    fluor               TEXT,
    current_disc        REAL,
    last_week_disc      REAL,
    inv_days            REAL,
    inv_remark          TEXT,
    bas_fix_remark      TEXT,
    triggers            TEXT,
    stock               REAL,
    sold_3m             REAL,
    sold_1w             REAL,
    rapnet_pos_india    REAL,
    rapnet_pos_world    REAL,
    rapnet_pos_usa      REAL,
    avg_disc            REAL,
    min_disc            REAL,
    max_disc            REAL,
    competitor_top_disc REAL,
    mfg_3m              REAL,
    avg_disc_gap        REAL,
    is_program          INTEGER,
    has_sold_high       INTEGER,
    inv_remark_encoded  INTEGER,
    trigger_count       INTEGER,
    sold_ratio          REAL,
    size_mid            REAL,
    loaded_at           TEXT    DEFAULT (datetime('now')),
    UNIQUE(criteria_key, report_date)
)
"""

DDL_PRICING_RESULTS = """
CREATE TABLE IF NOT EXISTS pricing_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    criteria_key    TEXT    NOT NULL,
    report_date     TEXT    NOT NULL,
    action          TEXT,
    suggested_disc  REAL,
    change_pct      REAL,
    confidence      TEXT,
    needs_review    INTEGER,
    primary_reason  TEXT,
    signals_used    TEXT,
    full_reasoning  TEXT,
    approved        INTEGER DEFAULT 0,
    approved_at     TEXT,
    created_at      TEXT    DEFAULT (datetime('now')),
    UNIQUE(criteria_key, report_date)
)
"""

DDL_RAPNET_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS rapnet_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    criteria_key        TEXT    NOT NULL,
    shape               TEXT,
    size_from           REAL,
    size_to             REAL,
    clarity             TEXT,
    color               TEXT,
    snap_date           TEXT    NOT NULL,
    position_india      INTEGER,
    position_world      INTEGER,
    position_usa        INTEGER,
    raw_json            TEXT,
    created_at          TEXT    DEFAULT (datetime('now'))
)
"""

DDL_ACTIVITY_LOG = """
CREATE TABLE IF NOT EXISTS activity_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT    NOT NULL,
    description     TEXT,
    metadata_json   TEXT,
    created_at      TEXT    DEFAULT (datetime('now'))
)
"""

DDL_ML_PREDICTIONS = """
CREATE TABLE IF NOT EXISTS ml_predictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    criteria_key    TEXT    NOT NULL,
    report_date     TEXT    NOT NULL,
    sell_score      REAL,
    features_json   TEXT,
    created_at      TEXT    DEFAULT (datetime('now')),
    UNIQUE(criteria_key, report_date)
)
"""

ALL_DDL = [
    DDL_BASE_REPORT_ROWS,
    DDL_PRICING_RESULTS,
    DDL_RAPNET_SNAPSHOTS,
    DDL_ACTIVITY_LOG,
    DDL_ML_PREDICTIONS,
]


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

def init_db(db_path: Union[str, Path]) -> None:
    """
    Initialise the SQLite database at ``db_path``, creating all tables if they
    do not already exist.

    Parameters
    ----------
    db_path : str or Path
        Path to the SQLite database file.  The parent directory must exist.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        for ddl in ALL_DDL:
            conn.execute(ddl)
        conn.commit()
    logger.info("Database initialised at %s", db_path)


def _get_connection(db_path: Union[str, Path]) -> sqlite3.Connection:
    """Return a sqlite3 connection with row_factory set to Row."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def upsert_base_report(df: pd.DataFrame, db_path: Union[str, Path]) -> int:
    """
    Upsert (insert or replace) rows from a normalised Base Report DataFrame
    into the ``base_report_rows`` table.

    The upsert key is ``(criteria_key, report_date)``.

    Parameters
    ----------
    df : pd.DataFrame
        Normalised DataFrame produced by ``normalizer.run_full_normalisation``.
    db_path : str or Path
        Path to the SQLite database.

    Returns
    -------
    int
        Number of rows written.
    """
    db_path = Path(db_path)
    init_db(db_path)

    # Columns that map directly to the table (exclude auto-generated id / loaded_at)
    table_cols = [
        "criteria_key", "report_date", "source_file", "report_type",
        "shape", "size_from", "size_to", "clarity", "color", "cut", "fluor",
        "current_disc", "last_week_disc", "inv_days", "inv_remark", "bas_fix_remark",
        "triggers", "stock", "sold_3m", "sold_1w",
        "rapnet_pos_india", "rapnet_pos_world", "rapnet_pos_usa",
        "avg_disc", "min_disc", "max_disc", "competitor_top_disc", "mfg_3m",
        "avg_disc_gap", "is_program", "has_sold_high", "inv_remark_encoded",
        "trigger_count", "sold_ratio", "size_mid",
    ]

    # Keep only columns that exist in the DataFrame
    available_cols = [c for c in table_cols if c in df.columns]
    subset = df[available_cols].copy()

    # Convert boolean to int for SQLite
    for bool_col in ["is_program", "has_sold_high"]:
        if bool_col in subset.columns:
            subset[bool_col] = subset[bool_col].astype(int)

    placeholders = ", ".join(["?"] * len(available_cols))
    col_str = ", ".join(available_cols)
    sql = (
        f"INSERT OR REPLACE INTO base_report_rows ({col_str}) "
        f"VALUES ({placeholders})"
    )

    rows = [tuple(row) for row in subset.itertuples(index=False, name=None)]
    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany(sql, rows)
        conn.commit()

    logger.info("Upserted %d rows into base_report_rows", len(rows))
    log_activity(
        event_type="LOAD",
        description=f"Loaded {len(rows)} rows from {df['source_file'].iloc[0] if 'source_file' in df.columns else 'unknown'}",
        metadata={"rows": len(rows), "report_date": str(df["report_date"].iloc[0]) if "report_date" in df.columns else ""},
        db_path=db_path,
    )
    return len(rows)


def upsert_pricing_results(results_df: pd.DataFrame, db_path: Union[str, Path]) -> int:
    """
    Upsert pricing results from the AI brain into the ``pricing_results`` table.

    The upsert key is ``(criteria_key, report_date)``.

    Parameters
    ----------
    results_df : pd.DataFrame
        DataFrame with columns: criteria_key, report_date, action, suggested_disc,
        change_pct, confidence, needs_review, primary_reason, signals_used,
        full_reasoning.
    db_path : str or Path
        Path to the SQLite database.

    Returns
    -------
    int
        Number of rows written.
    """
    db_path = Path(db_path)
    init_db(db_path)

    table_cols = [
        "criteria_key", "report_date", "action", "suggested_disc", "change_pct",
        "confidence", "needs_review", "primary_reason", "signals_used", "full_reasoning",
    ]
    available_cols = [c for c in table_cols if c in results_df.columns]
    subset = results_df[available_cols].copy()

    # Serialise list columns to JSON strings
    for list_col in ["signals_used"]:
        if list_col in subset.columns:
            subset[list_col] = subset[list_col].apply(
                lambda v: json.dumps(v) if isinstance(v, list) else str(v) if v is not None else ""
            )

    if "needs_review" in subset.columns:
        subset["needs_review"] = subset["needs_review"].astype(int)

    placeholders = ", ".join(["?"] * len(available_cols))
    col_str = ", ".join(available_cols)
    sql = (
        f"INSERT OR REPLACE INTO pricing_results ({col_str}) "
        f"VALUES ({placeholders})"
    )
    rows = [tuple(row) for row in subset.itertuples(index=False, name=None)]
    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany(sql, rows)
        conn.commit()

    logger.info("Upserted %d pricing results", len(rows))
    return len(rows)


def log_activity(
    event_type: str,
    description: str,
    metadata: Optional[dict] = None,
    db_path: Union[str, Path] = "db/diamond.db",
) -> None:
    """
    Insert a record into the ``activity_log`` table.

    Parameters
    ----------
    event_type : str
        Short event type code, e.g. ``"LOAD"``, ``"PRICE"``, ``"APPROVE"``.
    description : str
        Human-readable description of the event.
    metadata : dict, optional
        Arbitrary JSON-serialisable metadata.
    db_path : str or Path
        Path to the SQLite database.
    """
    db_path = Path(db_path)
    init_db(db_path)

    meta_str = json.dumps(metadata) if metadata else "{}"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO activity_log (event_type, description, metadata_json) VALUES (?, ?, ?)",
            (event_type, description, meta_str),
        )
        conn.commit()


def upsert_ml_predictions(predictions_df: pd.DataFrame, db_path: Union[str, Path]) -> int:
    """
    Upsert ML sell-score predictions into the ``ml_predictions`` table.

    Parameters
    ----------
    predictions_df : pd.DataFrame
        DataFrame with columns: criteria_key, report_date, sell_score, features_json.
    db_path : str or Path
        Path to the SQLite database.

    Returns
    -------
    int
        Number of rows written.
    """
    db_path = Path(db_path)
    init_db(db_path)

    table_cols = ["criteria_key", "report_date", "sell_score", "features_json"]
    available_cols = [c for c in table_cols if c in predictions_df.columns]
    subset = predictions_df[available_cols].copy()

    placeholders = ", ".join(["?"] * len(available_cols))
    col_str = ", ".join(available_cols)
    sql = (
        f"INSERT OR REPLACE INTO ml_predictions ({col_str}) "
        f"VALUES ({placeholders})"
    )
    rows = [tuple(row) for row in subset.itertuples(index=False, name=None)]
    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany(sql, rows)
        conn.commit()

    logger.info("Upserted %d ML predictions", len(rows))
    return len(rows)


def load_latest_base_report(db_path: Union[str, Path]) -> pd.DataFrame:
    """
    Load the most recent base report from the database.

    Parameters
    ----------
    db_path : str or Path

    Returns
    -------
    pd.DataFrame
    """
    db_path = Path(db_path)
    init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        latest_date = conn.execute(
            "SELECT MAX(report_date) FROM base_report_rows"
        ).fetchone()[0]
        if not latest_date:
            return pd.DataFrame()
        df = pd.read_sql(
            "SELECT * FROM base_report_rows WHERE report_date = ?",
            conn,
            params=(latest_date,),
        )
    logger.info("Loaded %d rows from base_report_rows for date %s", len(df), latest_date)
    return df


def load_activity_log(db_path: Union[str, Path]) -> pd.DataFrame:
    """
    Load the full activity log.

    Parameters
    ----------
    db_path : str or Path

    Returns
    -------
    pd.DataFrame
    """
    db_path = Path(db_path)
    init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        df = pd.read_sql("SELECT * FROM activity_log ORDER BY created_at DESC", conn)
    return df

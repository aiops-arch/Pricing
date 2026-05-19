"""
normalizer.py
-------------
Normalises raw Base Report DataFrames to the internal standard schema.

Responsibilities:
  - Map every known column-name variant to a canonical internal name via fuzzy
    matching (thefuzz).
  - Compute derived / engineered features needed by both the AI brain and the
    ML model.
  - Coerce numeric columns and fill sensible defaults for NaN values.
"""

import logging
import re
from typing import Optional

import numpy as np
import pandas as pd
from thefuzz import process as fuzz_process

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --------------------------------------------------------------------------- #
# COLUMN ALIASES                                                               #
# Maps every known raw column name variant to the canonical internal name.    #
# --------------------------------------------------------------------------- #
COLUMN_ALIASES: dict[str, str] = {
    # Shape
    "shape": "shape",
    "Shape": "shape",
    "SHAPE": "shape",
    # Size / carat range
    "size": "size_from",
    "Size": "size_from",
    "SIZE": "size_from",
    "size from": "size_from",
    "Size From": "size_from",
    "size_from": "size_from",
    "siz from": "size_from",
    "size to": "size_to",
    "Size To": "size_to",
    "size_to": "size_to",
    # Clarity
    "clarity": "clarity",
    "Clarity": "clarity",
    "CLARITY": "clarity",
    # Color
    "color": "color",
    "Color": "color",
    "COLOR": "color",
    "colour": "color",
    "Colour": "color",
    # Cut
    "cut": "cut",
    "Cut": "cut",
    "CUT": "cut",
    # Fluorescence
    "fluor": "fluor",
    "Fluor": "fluor",
    "FLUOR": "fluor",
    "fluorescence": "fluor",
    "Fluorescence": "fluor",
    # Current discount
    "curr disc%": "current_disc",
    "Curr Disc%": "current_disc",
    "curr disc": "current_disc",
    "current_disc": "current_disc",
    "current disc": "current_disc",
    "disc%": "current_disc",
    "Disc%": "current_disc",
    # Last week discount
    "last wk disc%": "last_week_disc",
    "Last Wk Disc%": "last_week_disc",
    "last week disc": "last_week_disc",
    "last_week_disc": "last_week_disc",
    # Inventory days
    "inv days": "inv_days",
    "Inv Days": "inv_days",
    "inv_days": "inv_days",
    "inventory days": "inv_days",
    # Sold 3 months
    "sold 3m": "sold_3m",
    "Sold 3M": "sold_3m",
    "sold_3m": "sold_3m",
    "sold 3months": "sold_3m",
    # Sold 1 week
    "sold 1w": "sold_1w",
    "Sold 1W": "sold_1w",
    "sold_1w": "sold_1w",
    "sold 1week": "sold_1w",
    "sold 1 week": "sold_1w",
    # Inventory remark
    "inv remark": "inv_remark",
    "Inv Remark": "inv_remark",
    "inv_remark": "inv_remark",
    "inventory remark": "inv_remark",
    # Bas Fix Pcs Pos Remark
    "bas fix pcs pos remark": "bas_fix_remark",
    "Bas Fix Pcs Pos Remark": "bas_fix_remark",
    "bas_fix_remark": "bas_fix_remark",
    "bas fix remark": "bas_fix_remark",
    # Triggers
    "triggers": "triggers",
    "Triggers": "triggers",
    "TRIGGERS": "triggers",
    "trigger": "triggers",
    # Avg disc%
    "avg disc%": "avg_disc",
    "Avg Disc%": "avg_disc",
    "avg_disc": "avg_disc",
    "avg disc": "avg_disc",
    # Min disc%
    "min disc%": "min_disc",
    "Min Disc%": "min_disc",
    "min_disc": "min_disc",
    # Max disc%
    "max disc%": "max_disc",
    "Max Disc%": "max_disc",
    "max_disc": "max_disc",
    # Stock
    "stock": "stock",
    "Stock": "stock",
    "STOCK": "stock",
    "inventory": "stock",
    # RapNet positions
    "rapnet pos india": "rapnet_pos_india",
    "RapNet Pos India": "rapnet_pos_india",
    "rapnet_pos_india": "rapnet_pos_india",
    "rapnet pos world": "rapnet_pos_world",
    "RapNet Pos World": "rapnet_pos_world",
    "rapnet_pos_world": "rapnet_pos_world",
    "rapnet pos usa": "rapnet_pos_usa",
    "RapNet Pos USA": "rapnet_pos_usa",
    "rapnet_pos_usa": "rapnet_pos_usa",
    # Competitor top disc
    "comp 1_disc%": "competitor_top_disc",
    "Comp 1_Disc%": "competitor_top_disc",
    "competitor_top_disc": "competitor_top_disc",
    "comp1 disc%": "competitor_top_disc",
    # MFG 3M
    "mfg 3m": "mfg_3m",
    "MFG 3M": "mfg_3m",
    "mfg_3m": "mfg_3m",
    "mfg3m": "mfg_3m",
}

# The canonical set of columns we want in the normalised DataFrame
STANDARD_SCHEMA = [
    "shape",
    "size_from",
    "size_to",
    "clarity",
    "color",
    "cut",
    "fluor",
    "current_disc",
    "last_week_disc",
    "inv_days",
    "inv_remark",
    "bas_fix_remark",
    "triggers",
    "stock",
    "sold_3m",
    "sold_1w",
    "rapnet_pos_india",
    "rapnet_pos_world",
    "rapnet_pos_usa",
    "avg_disc",
    "min_disc",
    "max_disc",
    "competitor_top_disc",
    "mfg_3m",
]

# Numeric columns with their default fill values
NUMERIC_DEFAULTS: dict[str, float] = {
    "current_disc": 0.0,
    "last_week_disc": 0.0,
    "inv_days": 0.0,
    "sold_3m": 0.0,
    "sold_1w": 0.0,
    "stock": 0.0,
    "rapnet_pos_india": 999.0,
    "rapnet_pos_world": 999.0,
    "rapnet_pos_usa": 999.0,
    "avg_disc": 0.0,
    "min_disc": 0.0,
    "max_disc": 0.0,
    "competitor_top_disc": 0.0,
    "mfg_3m": 0.0,
    "size_from": 0.0,
    "size_to": 0.0,
}

# Fuzzy matching threshold (0-100); columns below this score won't be auto-mapped
FUZZY_THRESHOLD = 70


def _fuzzy_match_column(raw_col: str, target_cols: list[str], threshold: int = FUZZY_THRESHOLD) -> Optional[str]:
    """
    Use thefuzz to find the best matching target column for a raw column name.

    Parameters
    ----------
    raw_col : str
        The raw column name from the source DataFrame.
    target_cols : list of str
        List of canonical target column names.
    threshold : int
        Minimum score (0-100) to accept a match.

    Returns
    -------
    str or None
        The matched canonical name, or None if no match exceeds the threshold.
    """
    result = fuzz_process.extractOne(raw_col.lower().replace("_", " "), [t.lower().replace("_", " ") for t in target_cols])
    if result is None:
        return None
    matched_text, score = result[0], result[1]
    if score >= threshold:
        # Map back to original target name
        for t in target_cols:
            if t.lower().replace("_", " ") == matched_text:
                return t
    return None


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rename columns of ``df`` from raw names to canonical internal names.

    The mapping strategy is:
    1. Try exact match via COLUMN_ALIASES dict.
    2. Try case-insensitive exact match via COLUMN_ALIASES.
    3. Fall back to fuzzy matching against STANDARD_SCHEMA names.

    Columns that cannot be mapped are kept with their original names (prefixed
    with ``raw__``).

    Parameters
    ----------
    df : pd.DataFrame
        Raw DataFrame from ``loader.load_base_report``.

    Returns
    -------
    pd.DataFrame
        DataFrame with standardised column names.
    """
    rename_map: dict[str, str] = {}
    already_mapped: set[str] = set()

    for raw_col in df.columns:
        # Skip metadata columns added by loader
        if raw_col in ("source_file", "report_date", "report_type"):
            rename_map[raw_col] = raw_col
            continue

        # 1. Exact match in COLUMN_ALIASES
        if raw_col in COLUMN_ALIASES:
            target = COLUMN_ALIASES[raw_col]
            if target not in already_mapped:
                rename_map[raw_col] = target
                already_mapped.add(target)
                continue

        # 2. Case-insensitive match
        lower_raw = raw_col.lower()
        matched = None
        for alias_key, alias_val in COLUMN_ALIASES.items():
            if alias_key.lower() == lower_raw and alias_val not in already_mapped:
                matched = alias_val
                break
        if matched:
            rename_map[raw_col] = matched
            already_mapped.add(matched)
            continue

        # 3. Fuzzy match against standard schema
        remaining_targets = [t for t in STANDARD_SCHEMA if t not in already_mapped]
        fuzzy_match = _fuzzy_match_column(raw_col, remaining_targets)
        if fuzzy_match:
            logger.debug("Fuzzy mapped '%s' -> '%s'", raw_col, fuzzy_match)
            rename_map[raw_col] = fuzzy_match
            already_mapped.add(fuzzy_match)
        else:
            # Keep but mark as raw
            safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", raw_col)
            rename_map[raw_col] = f"raw__{safe_name}"
            logger.debug("Could not map column '%s', keeping as 'raw__%s'", raw_col, safe_name)

    df = df.rename(columns=rename_map)

    # Ensure all standard columns exist (add with NaN if missing)
    for col in STANDARD_SCHEMA:
        if col not in df.columns:
            df[col] = np.nan
            logger.debug("Added missing standard column: %s", col)

    logger.info("Column normalisation complete. Mapped %d columns.", len(rename_map))
    return df


def _encode_inv_remark(series: pd.Series) -> pd.Series:
    """
    Encode the ``inv_remark`` text column to a numeric value.

    Encoding:
      * ``-1``  : contains "reduction" or "price reduction"
      * ``+1``  : contains "raised"
      *  ``0``  : anything else / NaN

    Parameters
    ----------
    series : pd.Series

    Returns
    -------
    pd.Series of int
    """
    def _encode(val):
        if pd.isna(val):
            return 0
        v = str(val).lower()
        if "reduction" in v:
            return -1
        if "raised" in v:
            return 1
        return 0

    return series.map(_encode).astype(int)


def _count_triggers(series: pd.Series) -> pd.Series:
    """
    Count the number of trigger flags in the ``triggers`` text column.
    Triggers are assumed to be comma- or semicolon-separated tokens.

    Parameters
    ----------
    series : pd.Series

    Returns
    -------
    pd.Series of int
    """
    def _count(val):
        if pd.isna(val) or str(val).strip() == "":
            return 0
        # Split on comma, semicolon, or pipe
        parts = re.split(r"[,;|]", str(val))
        return sum(1 for p in parts if p.strip())

    return series.map(_count).astype(int)


def compute_derived(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add derived / engineered feature columns to the normalised DataFrame.

    New columns added:
    - ``avg_disc_gap``        : current_disc - avg_disc  (positive = our disc is higher than avg)
    - ``criteria_key``        : "SHAPE|SIZE_FROM|SIZE_TO|CLARITY|COLOR" identifier
    - ``is_program``          : bool, True if triggers contains "Program"
    - ``has_sold_high``       : bool, True if triggers contains "Sold High"
    - ``inv_remark_encoded``  : -1/0/+1 encoding of inv_remark text
    - ``trigger_count``       : integer count of trigger items
    - ``sold_ratio``          : sold_1w / (sold_3m / 13)  — weekly sell-through rate vs 3M avg
    - ``size_mid``            : midpoint of size_from and size_to

    Parameters
    ----------
    df : pd.DataFrame
        Normalised DataFrame from ``normalize_columns``.

    Returns
    -------
    pd.DataFrame
        DataFrame with additional derived columns.
    """
    df = df.copy()

    # avg_disc_gap: positive means we are more expensive than market average
    df["avg_disc_gap"] = df["current_disc"].sub(df["avg_disc"]).fillna(0.0)

    # criteria_key: unique identifier for a size/shape/clarity/color bucket
    def _make_key(row):
        shape = str(row.get("shape", "")).strip().upper()
        sf = str(row.get("size_from", "")).strip()
        st = str(row.get("size_to", "")).strip()
        clarity = str(row.get("clarity", "")).strip().upper()
        color = str(row.get("color", "")).strip().upper()
        return f"{shape}|{sf}|{st}|{clarity}|{color}"

    df["criteria_key"] = df.apply(_make_key, axis=1)

    # is_program: triggers column contains "program" (case-insensitive)
    triggers_col = df["triggers"].fillna("").astype(str)
    df["is_program"] = triggers_col.str.lower().str.contains("program", na=False)

    # has_sold_high
    df["has_sold_high"] = triggers_col.str.lower().str.contains("sold high", na=False)

    # inv_remark_encoded
    df["inv_remark_encoded"] = _encode_inv_remark(df["inv_remark"])

    # trigger_count
    df["trigger_count"] = _count_triggers(df["triggers"])

    # sold_ratio: weekly sell-through compared to the 13-week average rate
    three_month_weekly_avg = df["sold_3m"].fillna(0) / 13.0
    # Avoid division by zero; default to sold_1w itself when avg is 0
    df["sold_ratio"] = np.where(
        three_month_weekly_avg > 0,
        df["sold_1w"].fillna(0) / three_month_weekly_avg,
        df["sold_1w"].fillna(0),
    )

    # size_mid
    df["size_mid"] = (df["size_from"].fillna(0) + df["size_to"].fillna(0)) / 2.0

    logger.info("Derived columns computed: avg_disc_gap, criteria_key, is_program, has_sold_high, "
                "inv_remark_encoded, trigger_count, sold_ratio, size_mid")
    return df


def clean_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """
    Coerce all numeric columns to float, fill NaN with appropriate defaults,
    and ensure no stray string/None values remain in numeric fields.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame after ``normalize_columns`` and ``compute_derived``.

    Returns
    -------
    pd.DataFrame
        Cleaned DataFrame.
    """
    df = df.copy()

    for col, default in NUMERIC_DEFAULTS.items():
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(default)

    # Also coerce derived numeric columns
    for col in ["avg_disc_gap", "sold_ratio", "size_mid", "trigger_count", "inv_remark_encoded"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # String columns: fill NaN with empty string
    for col in ["shape", "clarity", "color", "cut", "fluor", "inv_remark",
                "bas_fix_remark", "triggers", "criteria_key"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()

    logger.info("Numeric cleaning complete.")
    return df


def run_full_normalisation(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convenience wrapper that runs the full normalisation pipeline:
    normalize_columns -> compute_derived -> clean_numeric.

    Parameters
    ----------
    df : pd.DataFrame
        Raw DataFrame from loader.

    Returns
    -------
    pd.DataFrame
        Fully normalised and feature-engineered DataFrame.
    """
    df = normalize_columns(df)
    df = compute_derived(df)
    df = clean_numeric(df)
    return df

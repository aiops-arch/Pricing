"""
features.py
-----------
Feature engineering for the ML sell-probability model.

Provides ranking dictionaries for clarity and color, a ``build_features``
function that produces the model input matrix, and the ``FEATURE_COLS``
constant that defines the canonical feature order.
"""

import logging
from typing import Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --------------------------------------------------------------------------- #
# Ordinal Rankings                                                             #
# --------------------------------------------------------------------------- #

CLARITY_RANK: dict[str, int] = {
    "IF":  1,
    "VVS1": 2,
    "VVS2": 3,
    "VS1":  4,
    "VS2":  5,
    "SI1":  6,
    "SI2":  7,
    "SI3":  8,
    "I1":   9,
    "I2":  10,
    "I3":  11,
}

COLOR_RANK: dict[str, int] = {
    "D":  1,
    "E":  2,
    "F":  3,
    "G":  4,
    "H":  5,
    "I":  6,
    "J":  7,
    "K":  8,
    "L":  9,
    "M": 10,
}

# Canonical ordered feature column list consumed by the ML model
FEATURE_COLS: list[str] = [
    "rapnet_pos_india",
    "inv_days",
    "sold_1w_bin",
    "sold_3m",
    "avg_disc_gap",
    "stock",
    "is_program",
    "is_none_fluor",
    "is_fg_color",
    "clarity_rank",
    "color_rank",
    "trigger_count",
    "has_sold_high",
    "inv_remark_encoded",
]


def _sold_1w_bin(series: pd.Series) -> pd.Series:
    """
    Bin ``sold_1w`` into ordinal categories:
      0  -> 0  (no sales)
      1  -> 1  (low: 1-2)
      2  -> 2  (medium: 3-5)
      3  -> 3  (high: 6+)

    Parameters
    ----------
    series : pd.Series

    Returns
    -------
    pd.Series of int
    """
    def _bin(v):
        v = float(v) if v is not None else 0.0
        if v == 0:
            return 0
        if v <= 2:
            return 1
        if v <= 5:
            return 2
        return 3

    return series.fillna(0).map(_bin).astype(int)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build the feature matrix for the ML model from a normalised Base Report
    DataFrame.

    The following columns are produced (as defined in FEATURE_COLS):

    rapnet_pos_india    : RapNet position in India (raw value; 999 if unknown)
    inv_days            : Days the stone has been in inventory
    sold_1w_bin         : Ordinal bin of sold_1w (0=none, 1=low, 2=med, 3=high)
    sold_3m             : Units sold over the last 3 months
    avg_disc_gap        : current_disc - avg_disc (positive = we are cheaper)
    stock               : Piece count in inventory
    is_program          : 1 if this is a Program criteria, else 0
    is_none_fluor       : 1 if fluorescence is None/NON/NO, else 0
    is_fg_color         : 1 if color is F or G, else 0
    clarity_rank        : Ordinal rank from CLARITY_RANK (1=best, 11=worst)
    color_rank          : Ordinal rank from COLOR_RANK (1=best, 10=worst)
    trigger_count       : Count of trigger flags
    has_sold_high       : 1 if "Sold High" is in triggers, else 0
    inv_remark_encoded  : -1/0/+1 encoding of inv_remark text

    Parameters
    ----------
    df : pd.DataFrame
        Normalised DataFrame (output of ``normalizer.run_full_normalisation``).

    Returns
    -------
    pd.DataFrame
        DataFrame with exactly the columns in FEATURE_COLS, in order.
    """
    feat = pd.DataFrame(index=df.index)

    # rapnet_pos_india
    feat["rapnet_pos_india"] = pd.to_numeric(df.get("rapnet_pos_india", 999), errors="coerce").fillna(999)

    # inv_days
    feat["inv_days"] = pd.to_numeric(df.get("inv_days", 0), errors="coerce").fillna(0)

    # sold_1w_bin
    feat["sold_1w_bin"] = _sold_1w_bin(df.get("sold_1w", pd.Series(0, index=df.index)))

    # sold_3m
    feat["sold_3m"] = pd.to_numeric(df.get("sold_3m", 0), errors="coerce").fillna(0)

    # avg_disc_gap
    feat["avg_disc_gap"] = pd.to_numeric(df.get("avg_disc_gap", 0), errors="coerce").fillna(0)

    # stock
    feat["stock"] = pd.to_numeric(df.get("stock", 0), errors="coerce").fillna(0)

    # is_program
    if "is_program" in df.columns:
        feat["is_program"] = df["is_program"].astype(int)
    else:
        triggers = df.get("triggers", pd.Series("", index=df.index)).fillna("").astype(str)
        feat["is_program"] = triggers.str.lower().str.contains("program").astype(int)

    # is_none_fluor
    fluor_col = df.get("fluor", pd.Series("", index=df.index)).fillna("").astype(str).str.upper()
    feat["is_none_fluor"] = fluor_col.isin(["NONE", "NON", "NO", "N"]).astype(int)

    # is_fg_color
    color_col = df.get("color", pd.Series("", index=df.index)).fillna("").astype(str).str.upper()
    feat["is_fg_color"] = color_col.isin(["F", "G"]).astype(int)

    # clarity_rank
    clarity_col = df.get("clarity", pd.Series("", index=df.index)).fillna("").astype(str).str.upper()
    feat["clarity_rank"] = clarity_col.map(lambda c: CLARITY_RANK.get(c, 6))  # default SI1 rank

    # color_rank
    feat["color_rank"] = color_col.map(lambda c: COLOR_RANK.get(c, 5))  # default H rank

    # trigger_count
    if "trigger_count" in df.columns:
        feat["trigger_count"] = pd.to_numeric(df["trigger_count"], errors="coerce").fillna(0).astype(int)
    else:
        import re
        triggers_s = df.get("triggers", pd.Series("", index=df.index)).fillna("").astype(str)
        feat["trigger_count"] = triggers_s.apply(
            lambda v: len([p for p in re.split(r"[,;|]", v) if p.strip()])
        ).astype(int)

    # has_sold_high
    if "has_sold_high" in df.columns:
        feat["has_sold_high"] = df["has_sold_high"].astype(int)
    else:
        triggers_s = df.get("triggers", pd.Series("", index=df.index)).fillna("").astype(str)
        feat["has_sold_high"] = triggers_s.str.lower().str.contains("sold high").astype(int)

    # inv_remark_encoded
    if "inv_remark_encoded" in df.columns:
        feat["inv_remark_encoded"] = pd.to_numeric(df["inv_remark_encoded"], errors="coerce").fillna(0).astype(int)
    else:
        inv_remark = df.get("inv_remark", pd.Series("", index=df.index)).fillna("").astype(str).str.lower()
        feat["inv_remark_encoded"] = inv_remark.apply(
            lambda v: -1 if "reduction" in v else (1 if "raised" in v else 0)
        ).astype(int)

    # Return in canonical order
    result = feat[FEATURE_COLS].copy()
    logger.debug("Built feature matrix with shape %s", result.shape)
    return result

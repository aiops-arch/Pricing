"""
order_tracker.py
----------------
Loads and analyses the DANY ORDER LIST Excel file.

The file has two sheets:
  Sheet 1 - Order Summary  : One row per order line (shape/size/color/clarity).
  Sheet 2 - Stone Detail   : One row per stone/packet in process.

Provides helper functions for:
  - Loading both sheets with robust column mapping.
  - Computing order completion percentage and traffic-light status.
  - Identifying at-risk order lines (close to deadline).
  - Grouping stones by department.
  - Finding overdue stones (stuck in a department too long).
"""

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Expected columns for each sheet (canonical internal names)
SUMMARY_COLS = [
    "shape", "size_from", "size_to", "color", "clarity",
    "qty_ordered", "in_process", "polish_ready", "pending",
    "order_name", "deadline",
]

DETAIL_COLS = [
    "lot_no", "packet_id", "carat_wt", "shape", "clarity", "color",
    "cut", "department", "order_name", "status", "days_in_dept",
]

# Alias maps for robust column matching
SUMMARY_ALIASES: dict[str, str] = {
    "shape": "shape", "Shape": "shape", "SHAPE": "shape",
    "size from": "size_from", "Size From": "size_from", "size_from": "size_from",
    "size to": "size_to", "Size To": "size_to", "size_to": "size_to",
    "color": "color", "Color": "color", "Colour": "color",
    "clarity": "clarity", "Clarity": "clarity",
    "qty ordered": "qty_ordered", "Qty Ordered": "qty_ordered", "qty_ordered": "qty_ordered",
    "qty": "qty_ordered", "Qty": "qty_ordered",
    "in process": "in_process", "In Process": "in_process", "in_process": "in_process",
    "polish ready": "polish_ready", "Polish Ready": "polish_ready", "polish_ready": "polish_ready",
    "pending": "pending", "Pending": "pending", "PENDING": "pending",
    "order name": "order_name", "Order Name": "order_name", "order_name": "order_name",
    "deadline": "deadline", "Deadline": "deadline", "DEADLINE": "deadline",
    "due date": "deadline", "Due Date": "deadline",
}

DETAIL_ALIASES: dict[str, str] = {
    "lot no": "lot_no", "Lot No": "lot_no", "lot_no": "lot_no", "LOT NO": "lot_no",
    "packet id": "packet_id", "Packet ID": "packet_id", "packet_id": "packet_id",
    "pkt id": "packet_id", "Pkt ID": "packet_id",
    "carat wt": "carat_wt", "Carat Wt": "carat_wt", "carat_wt": "carat_wt",
    "carat": "carat_wt", "Carat": "carat_wt",
    "shape": "shape", "Shape": "shape",
    "clarity": "clarity", "Clarity": "clarity",
    "color": "color", "Color": "color",
    "cut": "cut", "Cut": "cut",
    "department": "department", "Department": "department", "dept": "department", "Dept": "department",
    "order name": "order_name", "Order Name": "order_name", "order_name": "order_name",
    "status": "status", "Status": "status", "STATUS": "status",
    "days in dept": "days_in_dept", "Days in Dept": "days_in_dept", "days_in_dept": "days_in_dept",
    "days": "days_in_dept", "Days": "days_in_dept",
}


def _map_columns(df: pd.DataFrame, alias_map: dict[str, str]) -> pd.DataFrame:
    """
    Rename columns using an alias map (case-sensitive first, then fuzzy fallback).

    Parameters
    ----------
    df : pd.DataFrame
    alias_map : dict of str -> str

    Returns
    -------
    pd.DataFrame with renamed columns.
    """
    rename = {}
    for col in df.columns:
        if col in alias_map:
            rename[col] = alias_map[col]
        else:
            col_lower = col.lower().strip()
            for alias_key, canonical in alias_map.items():
                if alias_key.lower().strip() == col_lower:
                    rename[col] = canonical
                    break
    return df.rename(columns=rename)


def load_orders(path: Union[str, Path]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load both sheets of the DANY ORDER LIST Excel file.

    Parameters
    ----------
    path : str or Path
        Full path to the ``DANY ORDER LIST.xlsx`` file.

    Returns
    -------
    tuple of (summary_df, detail_df)
        summary_df : DataFrame from Sheet 1 (Order Summary) with canonical columns.
        detail_df  : DataFrame from Sheet 2 (Stone Detail) with canonical columns.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    ValueError
        If the expected sheets are not found.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"DANY ORDER LIST not found: {path}")

    xl = pd.ExcelFile(path)
    sheet_names = xl.sheet_names
    logger.info("Sheets found in %s: %s", path.name, sheet_names)

    # Load Sheet 1 (Order Summary) — first sheet
    summary_df = xl.parse(sheet_names[0], header=0)
    summary_df = _map_columns(summary_df, SUMMARY_ALIASES)

    # Ensure all expected columns exist
    for col in SUMMARY_COLS:
        if col not in summary_df.columns:
            summary_df[col] = np.nan

    # Drop completely empty rows
    summary_df = summary_df.dropna(how="all").reset_index(drop=True)

    # Load Sheet 2 (Stone Detail) — second sheet (if available)
    if len(sheet_names) >= 2:
        detail_df = xl.parse(sheet_names[1], header=0)
    else:
        logger.warning("Only one sheet found in %s; Stone Detail sheet is missing.", path.name)
        detail_df = pd.DataFrame(columns=DETAIL_COLS)

    detail_df = _map_columns(detail_df, DETAIL_ALIASES)
    for col in DETAIL_COLS:
        if col not in detail_df.columns:
            detail_df[col] = np.nan
    detail_df = detail_df.dropna(how="all").reset_index(drop=True)

    # Coerce numeric columns
    for col in ["qty_ordered", "in_process", "polish_ready", "pending"]:
        if col in summary_df.columns:
            summary_df[col] = pd.to_numeric(summary_df[col], errors="coerce").fillna(0)

    for col in ["carat_wt", "days_in_dept"]:
        if col in detail_df.columns:
            detail_df[col] = pd.to_numeric(detail_df[col], errors="coerce").fillna(0)

    logger.info(
        "Loaded %d order summary rows and %d stone detail rows from %s",
        len(summary_df), len(detail_df), path.name,
    )
    return summary_df, detail_df


def get_order_summary(summary_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute order completion metrics and assign a traffic-light status to each
    order line.

    Adds columns:
    - ``pct_complete``  : polish_ready / qty_ordered * 100 (float, 0-100)
    - ``status_flag``   : "green" if pct_complete >= 80, "amber" if 40-80,
                          "red" if < 40 or qty_ordered is 0

    Parameters
    ----------
    summary_df : pd.DataFrame
        Order summary sheet from ``load_orders``.

    Returns
    -------
    pd.DataFrame
        Input with ``pct_complete`` and ``status_flag`` columns added.
    """
    df = summary_df.copy()

    qty = pd.to_numeric(df.get("qty_ordered", 0), errors="coerce").fillna(0)
    ready = pd.to_numeric(df.get("polish_ready", 0), errors="coerce").fillna(0)

    pct = np.where(qty > 0, (ready / qty) * 100, 0.0)
    df["pct_complete"] = np.round(pct, 1)

    def _flag(p):
        if p >= 80:
            return "green"
        if p >= 40:
            return "amber"
        return "red"

    df["status_flag"] = df["pct_complete"].map(_flag)

    logger.info("Order summary computed for %d rows.", len(df))
    return df


def get_at_risk_lines(summary_df: pd.DataFrame, days_to_deadline: int = 7) -> pd.DataFrame:
    """
    Return order lines that are at risk due to an approaching deadline with low
    completion percentage.

    A line is "at risk" if:
    - deadline is within ``days_to_deadline`` days from today, AND
    - pct_complete < 80%

    Parameters
    ----------
    summary_df : pd.DataFrame
        Order summary with ``pct_complete`` column (from ``get_order_summary``).
    days_to_deadline : int
        Number of days from today to consider "at risk" (default 7).

    Returns
    -------
    pd.DataFrame
        Subset of summary_df with at-risk rows, sorted by deadline ascending.
    """
    df = summary_df.copy()

    # Ensure pct_complete exists
    if "pct_complete" not in df.columns:
        df = get_order_summary(df)

    # Parse deadline column
    if "deadline" in df.columns:
        df["deadline_dt"] = pd.to_datetime(df["deadline"], errors="coerce")
    else:
        df["deadline_dt"] = pd.NaT

    today = pd.Timestamp(date.today())
    cutoff = today + pd.Timedelta(days=days_to_deadline)

    at_risk = df[
        (df["deadline_dt"].notna())
        & (df["deadline_dt"] <= cutoff)
        & (df["pct_complete"] < 80)
    ].copy()

    at_risk = at_risk.sort_values("deadline_dt")
    logger.info("Found %d at-risk order lines (deadline within %d days).", len(at_risk), days_to_deadline)
    return at_risk


def get_stones_by_department(detail_df: pd.DataFrame) -> pd.DataFrame:
    """
    Group stone detail rows by department and compute count and average days.

    Parameters
    ----------
    detail_df : pd.DataFrame
        Stone detail sheet from ``load_orders``.

    Returns
    -------
    pd.DataFrame
        DataFrame with columns: department, stone_count, avg_days_in_dept.
        Sorted by stone_count descending.
    """
    if detail_df.empty or "department" not in detail_df.columns:
        return pd.DataFrame(columns=["department", "stone_count", "avg_days_in_dept"])

    days_col = "days_in_dept" if "days_in_dept" in detail_df.columns else None

    grp = detail_df.groupby("department")
    result = grp.size().reset_index(name="stone_count")

    if days_col:
        avg_days = grp[days_col].mean().reset_index()
        avg_days.columns = ["department", "avg_days_in_dept"]
        result = result.merge(avg_days, on="department", how="left")
        result["avg_days_in_dept"] = result["avg_days_in_dept"].round(1)
    else:
        result["avg_days_in_dept"] = np.nan

    result = result.sort_values("stone_count", ascending=False).reset_index(drop=True)
    logger.info("Stones by department: %d departments.", len(result))
    return result


def get_overdue_stones(detail_df: pd.DataFrame, threshold_days: int = 5) -> pd.DataFrame:
    """
    Return stones that have been stuck in their current department beyond the
    threshold.

    Parameters
    ----------
    detail_df : pd.DataFrame
        Stone detail sheet from ``load_orders``.
    threshold_days : int
        Number of days in a department beyond which a stone is "overdue"
        (default 5).

    Returns
    -------
    pd.DataFrame
        Subset of detail_df where days_in_dept > threshold_days, sorted by
        days_in_dept descending.
    """
    if detail_df.empty:
        return pd.DataFrame(columns=DETAIL_COLS)

    if "days_in_dept" not in detail_df.columns:
        logger.warning("days_in_dept column not found; returning empty overdue list.")
        return pd.DataFrame(columns=DETAIL_COLS)

    days = pd.to_numeric(detail_df["days_in_dept"], errors="coerce").fillna(0)
    overdue = detail_df[days > threshold_days].copy()
    overdue = overdue.sort_values("days_in_dept", ascending=False).reset_index(drop=True)

    logger.info(
        "Found %d overdue stones (days_in_dept > %d).", len(overdue), threshold_days
    )
    return overdue

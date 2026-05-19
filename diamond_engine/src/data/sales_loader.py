"""
sales_loader.py
---------------
Loads "Merge Sales APR 25-MAR 26 (27) (1).xlsx" and populates the
sold_30d label in training_dataset.

FILE NOT YET ON MACHINE — this is a stub.

Join logic (when file arrives):
  For each sale: (stone_id OR criteria_key, sale_date)
    → find closest pricing_snapshots entry with snapshot_dt <= sale_date
    → mark training_dataset.sold_30d = 1 for all rows where
         criteria_key matches AND snapshot_date is within 30 days before sale_date

Expected sales file columns (TBD — inspect when file arrives):
  - Stone Id or Certificate No
  - Sale Date
  - Sale Disc / Price
  - Shape, Color, Clarity, Cut, Fluor, Carats
"""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_HERE        = Path(__file__).resolve()
PRICING_ROOT = _HERE.parents[3]
DB_PATH      = _HERE.parents[2] / "db" / "training.db"

SALES_FILE   = PRICING_ROOT / "Merge Sales APR 25-MAR 26 (27) (1).xlsx"


def load_sales_labels(
    sales_path: Path = SALES_FILE,
    db_path: Path = DB_PATH,
) -> int:
    """
    Load sales data and update sold_30d label in training_dataset.

    Returns number of training_dataset rows updated.
    """
    if not sales_path.exists():
        logger.warning(
            "Sales file not found: %s\n"
            "Drop the file into E:/Pricing/ and re-run build_training_data.py",
            sales_path,
        )
        return 0

    raise NotImplementedError(
        "sales_loader.py is a stub — inspect the file structure first:\n"
        "  python E:\\Pricing\\inspect_sales.py\n"
        "Then implement the join logic here."
    )

"""
build_training_data.py
----------------------
Orchestrates the full training data pipeline:

  Step 1: Load backup.zip CSV snapshots → pricing_snapshots table
  Step 2: Load Monthly Position Report XLSX files → position_stones table
  Step 3: Join + label → training_dataset table
  Step 4: (Optional) Load sales file → sold_30d labels  [stub until file arrives]

Usage:
  python scripts/build_training_data.py
  python scripts/build_training_data.py --force          # reload everything
  python scripts/build_training_data.py --step backup    # run only step 1
  python scripts/build_training_data.py --step position  # run only step 2
  python scripts/build_training_data.py --step training  # run only step 3
  python scripts/build_training_data.py --step sales     # run only step 4
"""

import argparse
import logging
import sys
import time
from pathlib import Path

# Allow running directly from scripts/ or as a module
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from data.backup_loader   import load_backup_to_db
from data.position_loader import load_position_reports
from data.training_builder import build_training_dataset
from data.sales_loader    import load_sales_labels

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def run_backup(force: bool) -> None:
    t0 = time.time()
    logger.info("=" * 60)
    logger.info("STEP 1: Loading backup.zip → pricing_snapshots")
    n = load_backup_to_db(force_reload=force)
    logger.info("Done in %.1fs  |  %d rows inserted", time.time() - t0, n)


def run_position(force: bool) -> None:
    t0 = time.time()
    logger.info("=" * 60)
    logger.info("STEP 2: Loading Monthly Position Reports → position_stones")
    n = load_position_reports(force_reload=force)
    logger.info("Done in %.1fs  |  %d rows inserted", time.time() - t0, n)


def run_training(force: bool) -> None:
    t0 = time.time()
    logger.info("=" * 60)
    logger.info("STEP 3: Building training_dataset")
    n = build_training_dataset(force_rebuild=force)
    logger.info("Done in %.1fs  |  %d rows inserted", time.time() - t0, n)


def run_sales() -> None:
    t0 = time.time()
    logger.info("=" * 60)
    logger.info("STEP 4: Loading sales labels → sold_30d")
    n = load_sales_labels()
    logger.info("Done in %.1fs  |  %d rows updated", time.time() - t0, n)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build diamond pricing training dataset")
    parser.add_argument(
        "--step",
        choices=["backup", "position", "training", "sales"],
        default=None,
        help="Run only one step (default: run all steps)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force reload — delete and re-insert all data for the selected step(s)",
    )
    args = parser.parse_args()

    step = args.step
    force = args.force

    if step is None or step == "backup":
        run_backup(force)

    if step is None or step == "position":
        run_position(force)

    if step is None or step == "training":
        run_training(force)

    if step == "sales":
        run_sales()

    logger.info("=" * 60)
    logger.info("Pipeline complete.")


if __name__ == "__main__":
    main()

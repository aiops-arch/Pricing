"""
run_ml_train.py
---------------
Script to train the XGBoost sell-probability model on historical Base Report
data stored in the SQLite database.

Usage:
    python scripts/run_ml_train.py [--model-path models/sell_model.pkl]

The more weekly Base Reports you load into the DB first (via run_pipeline.py),
the better the model will perform.
"""

import argparse
import logging
import sys
from pathlib import Path

# Repo root on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.ml_model.train import train_model

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("run_ml_train")

DB_PATH = _REPO_ROOT / "db" / "diamond.db"
DEFAULT_MODEL_PATH = _REPO_ROOT / "models" / "sell_model.pkl"
DEFAULT_CHART_PATH = _REPO_ROOT / "data" / "processed" / "feature_importance.png"


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Diamond AI Pricing Engine — ML Model Trainer")
    parser.add_argument(
        "--model-path",
        type=str,
        default=str(DEFAULT_MODEL_PATH),
        help=f"Where to save the trained model (default: {DEFAULT_MODEL_PATH})",
    )
    parser.add_argument(
        "--chart-path",
        type=str,
        default=str(DEFAULT_CHART_PATH),
        help=f"Where to save the feature importance chart (default: {DEFAULT_CHART_PATH})",
    )
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()
    model_path = Path(args.model_path)
    chart_path = Path(args.chart_path)

    logger.info("=" * 60)
    logger.info("Diamond AI Pricing Engine — ML Model Trainer")
    logger.info("=" * 60)
    logger.info("Database    : %s", DB_PATH)
    logger.info("Model path  : %s", model_path)
    logger.info("Chart path  : %s", chart_path)

    if not DB_PATH.exists():
        logger.error("Database not found at %s.", DB_PATH)
        logger.error("Run scripts/run_pipeline.py first to load Base Report data.")
        sys.exit(1)

    # Ensure model directory exists
    model_path.parent.mkdir(parents=True, exist_ok=True)
    chart_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        model = train_model(
            db_path=DB_PATH,
            model_path=model_path,
            feature_importance_chart_path=chart_path,
        )
        logger.info("Training complete. Model saved to %s", model_path)
        logger.info("Feature importance chart saved to %s", chart_path)

        print("\n--- ML TRAINING COMPLETE ---")
        print(f"Model saved to : {model_path}")
        print(f"Chart saved to : {chart_path}")
        print("Run the dashboard (Tab 3) to view scores.")
        print("----------------------------")

    except ValueError as exc:
        logger.error("Training failed: %s", exc)
        logger.error("Make sure you have loaded enough Base Reports first (run_pipeline.py).")
        sys.exit(1)
    except Exception as exc:
        logger.exception("Unexpected error during training: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

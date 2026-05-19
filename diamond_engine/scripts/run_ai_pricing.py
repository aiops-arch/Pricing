"""
run_ai_pricing.py
-----------------
Script to run AI pricing on the latest Base Report loaded in the database.

Usage:
    python scripts/run_ai_pricing.py [--pdf path/to/rulebook.pdf] [--concurrency 3]

Requires the ANTHROPIC_API_KEY environment variable (or .env file) to be set.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Repo root on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env")

from src.pipeline.db_writer import load_latest_base_report, log_activity
from src.ai_brain.system_prompt import extract_rulebook_text, build_system_prompt
from src.ai_brain.batch_pricer import process_batch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("run_ai_pricing")

DB_PATH = _REPO_ROOT / "db" / "diamond.db"


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Diamond AI Pricing Engine — AI Pricing Runner")
    parser.add_argument(
        "--pdf",
        type=str,
        default="",
        help="Path to the company pricing rulebook PDF (optional).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Number of parallel API calls (default: 3).",
    )
    parser.add_argument(
        "--batch-save",
        type=int,
        default=10,
        help="Save results to DB after this many rows (default: 10).",
    )
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    logger.info("=" * 60)
    logger.info("Diamond AI Pricing Engine — AI Pricing Runner")
    logger.info("=" * 60)

    # ---- API key ----
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY is not set. Set it in .env or as an environment variable.")
        sys.exit(1)

    logger.info("API key loaded (length=%d).", len(api_key))

    # ---- Load base report ----
    logger.info("Loading latest base report from DB: %s", DB_PATH)
    df = load_latest_base_report(DB_PATH)
    if df.empty:
        logger.error("No base report data in database. Run run_pipeline.py first.")
        sys.exit(1)

    report_date = df["report_date"].iloc[0] if "report_date" in df.columns else "N/A"
    logger.info("Loaded %d rows (report_date=%s).", len(df), report_date)

    # ---- Build system prompt ----
    rulebook_text = ""
    if args.pdf:
        pdf_path = Path(args.pdf)
        if pdf_path.exists():
            logger.info("Extracting rulebook text from: %s", pdf_path)
            rulebook_text = extract_rulebook_text(pdf_path)
        else:
            logger.warning("PDF not found at %s — using hard-coded rules only.", pdf_path)

    system_prompt = build_system_prompt(rulebook_text)
    logger.info("System prompt built (%d characters).", len(system_prompt))

    # ---- Run batch pricing ----
    logger.info("Starting AI pricing (concurrency=%d, batch_save=%d)...", args.concurrency, args.batch_save)

    summary = process_batch(
        df=df,
        db_path=DB_PATH,
        system_prompt=system_prompt,
        api_key=api_key,
        concurrency=args.concurrency,
        batch_save_size=args.batch_save,
    )

    # ---- Print summary ----
    logger.info("")
    logger.info("=" * 60)
    logger.info("AI PRICING SUMMARY")
    logger.info("=" * 60)
    logger.info("Total rows in report : %d", summary["total"])
    logger.info("Already priced       : %d", summary["skipped"])
    logger.info("Newly priced         : %d", summary["processed"])
    logger.info("")
    logger.info("ACTIONS:")
    logger.info("  INCREASE_DISC      : %d", summary["increased"])
    logger.info("  DECREASE_DISC      : %d", summary["decreased"])
    logger.info("  KEEP               : %d", summary["kept"])
    logger.info("")
    logger.info("CONFIDENCE:")
    logger.info("  HIGH               : %d", summary["high_conf"])
    logger.info("  MEDIUM             : %d", summary["med_conf"])
    logger.info("  LOW                : %d", summary["low_conf"])
    logger.info("")
    logger.info("Needs review         : %d", summary["needs_review_count"])
    logger.info("Errors               : %d", summary["errors"])
    logger.info("=" * 60)

    print("\n--- AI PRICING REPORT ---")
    print(f"Report Date   : {report_date}")
    print(f"Rows Processed: {summary['processed']} (skipped {summary['skipped']} already priced)")
    print(f"INCREASE_DISC : {summary['increased']}")
    print(f"DECREASE_DISC : {summary['decreased']}")
    print(f"KEEP          : {summary['kept']}")
    print(f"High Conf     : {summary['high_conf']}")
    print(f"Needs Review  : {summary['needs_review_count']}")
    print(f"Errors        : {summary['errors']}")
    print("-------------------------")

    if summary["errors"] > 0:
        logger.warning("%d rows had errors. Check activity log for details.", summary["errors"])
        sys.exit(1)


if __name__ == "__main__":
    main()

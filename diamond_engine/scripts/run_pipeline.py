"""
run_pipeline.py
---------------
Batch pipeline runner: finds all Base Report xlsx files in data/raw/ and
runs the full load -> normalise -> DB-write pipeline on each one.

Usage:
    python scripts/run_pipeline.py

The script must be run from the diamond_engine/ directory (or the repo root
with the diamond_engine directory in the path).
"""

import logging
import sys
from pathlib import Path

# Allow imports from the diamond_engine package root
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.pipeline.loader import load_base_report
from src.pipeline.normalizer import run_full_normalisation
from src.pipeline.db_writer import init_db, upsert_base_report, log_activity

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("run_pipeline")

DATA_RAW = _REPO_ROOT / "data" / "raw"
DB_PATH = _REPO_ROOT / "db" / "diamond.db"


def find_base_report_files(directory: Path) -> list[Path]:
    """
    Find all Excel files in ``directory`` whose filename contains
    "BASE REPORT" (case-insensitive).

    Parameters
    ----------
    directory : Path
        Directory to search.

    Returns
    -------
    list of Path
        Matching file paths, sorted alphabetically.
    """
    matches = []
    for ext in ("*.xlsx", "*.XLSX", "*.Xlsx"):
        matches.extend(directory.glob(ext))

    base_report_files = [
        f for f in matches if "base report" in f.name.lower()
    ]
    base_report_files.sort()
    logger.info("Found %d Base Report files in %s", len(base_report_files), directory)
    return base_report_files


def run_pipeline_on_file(path: Path) -> dict:
    """
    Run the full pipeline on a single Base Report file.

    Parameters
    ----------
    path : Path
        Full path to the xlsx file.

    Returns
    -------
    dict
        Summary dict with keys: file, rows_written, report_date, status, error.
    """
    try:
        logger.info("Processing: %s", path.name)
        raw_df = load_base_report(path)
        norm_df = run_full_normalisation(raw_df)
        rows_written = upsert_base_report(norm_df, DB_PATH)

        report_date = norm_df["report_date"].iloc[0] if "report_date" in norm_df.columns else "N/A"
        logger.info("  -> %d rows written for %s", rows_written, path.name)

        return {
            "file": path.name,
            "rows_written": rows_written,
            "report_date": report_date,
            "status": "OK",
            "error": "",
        }
    except Exception as exc:
        logger.error("  -> FAILED: %s — %s", path.name, exc)
        return {
            "file": path.name,
            "rows_written": 0,
            "report_date": "N/A",
            "status": "ERROR",
            "error": str(exc),
        }


def main():
    """Main entry point for the pipeline runner."""
    logger.info("=" * 60)
    logger.info("Diamond AI Pricing Engine — Pipeline Runner")
    logger.info("=" * 60)
    logger.info("Data directory : %s", DATA_RAW)
    logger.info("Database       : %s", DB_PATH)

    if not DATA_RAW.exists():
        logger.error("data/raw directory does not exist: %s", DATA_RAW)
        logger.error("Create it and copy your Base Report xlsx files there.")
        sys.exit(1)

    # Initialise DB
    init_db(DB_PATH)

    # Find files
    files = find_base_report_files(DATA_RAW)
    if not files:
        logger.warning("No Base Report files found in %s", DATA_RAW)
        logger.warning("Filename must contain 'BASE REPORT' (case-insensitive) to be picked up.")
        sys.exit(0)

    # Process each file
    results = []
    for path in files:
        result = run_pipeline_on_file(path)
        results.append(result)

    # Summary
    total_rows = sum(r["rows_written"] for r in results)
    ok_count = sum(1 for r in results if r["status"] == "OK")
    err_count = sum(1 for r in results if r["status"] == "ERROR")

    logger.info("")
    logger.info("=" * 60)
    logger.info("PIPELINE SUMMARY")
    logger.info("=" * 60)
    logger.info("Files processed : %d", len(results))
    logger.info("Successful      : %d", ok_count)
    logger.info("Errors          : %d", err_count)
    logger.info("Total rows DB   : %d", total_rows)
    logger.info("")

    for r in results:
        status_icon = "OK" if r["status"] == "OK" else "FAIL"
        logger.info(
            "  [%s] %-50s %5d rows  date=%s  %s",
            status_icon,
            r["file"],
            r["rows_written"],
            r["report_date"],
            r.get("error", ""),
        )

    # Log to activity log
    log_activity(
        event_type="PIPELINE_RUN",
        description=f"Pipeline run: {ok_count}/{len(results)} files OK, {total_rows} total rows",
        metadata={"results": results},
        db_path=DB_PATH,
    )

    if err_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

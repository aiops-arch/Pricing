"""
batch_pricer.py
---------------
Batch processing of Base Report rows through the AI pricing brain.

Features:
  - Skips already-priced rows (checks pricing_results table for existing entries).
  - Asyncio + semaphore for controlled concurrency (default 3 parallel calls).
  - Exponential backoff retry (3 attempts, 2^n * 1s base) on rate-limit errors.
  - tqdm progress bar.
  - Saves results to DB after every ``batch_save_size`` rows.
  - Returns a summary dict with counts by action, confidence, and errors.
"""

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Union

import anthropic
import pandas as pd
from tqdm import tqdm

from src.ai_brain.pricer import price_row

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

MAX_RETRIES = 3
BASE_BACKOFF_S = 1.0


def _load_already_priced(db_path: Union[str, Path], report_date: str) -> set[str]:
    """
    Load the set of criteria_keys that have already been priced for a given
    report_date from the pricing_results table.

    Parameters
    ----------
    db_path : str or Path
    report_date : str

    Returns
    -------
    set of str
        Set of criteria_key strings already in pricing_results for this date.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return set()
    try:
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                "SELECT criteria_key FROM pricing_results WHERE report_date = ?",
                (report_date,),
            ).fetchall()
        return {r[0] for r in rows}
    except Exception as exc:
        logger.warning("Could not load already-priced keys: %s", exc)
        return set()


def _save_batch_to_db(results: list[dict], report_date: str, db_path: Union[str, Path]) -> None:
    """
    Save a batch of pricing results to the database.

    Parameters
    ----------
    results : list of dict
        List of result dicts from ``price_row``.
    report_date : str
    db_path : str or Path
    """
    from src.pipeline.db_writer import upsert_pricing_results

    if not results:
        return

    rows = []
    for r in results:
        rows.append(
            {
                "criteria_key": r.get("criteria_key", ""),
                "report_date": report_date,
                "action": r.get("action", "KEEP"),
                "suggested_disc": r.get("suggested_disc", r.get("current_disc", 0.0)),
                "change_pct": r.get("change", 0.0),
                "confidence": r.get("confidence", "LOW"),
                "needs_review": int(r.get("needs_review", True)),
                "primary_reason": r.get("primary_reason", ""),
                "signals_used": json.dumps(r.get("signals_used", [])),
                "full_reasoning": r.get("full_reasoning", ""),
            }
        )
    df = pd.DataFrame(rows)
    upsert_pricing_results(df, db_path)
    logger.debug("Saved %d results to DB.", len(rows))


async def _price_row_with_retry(
    row_dict: dict,
    system_prompt: str,
    client: anthropic.Anthropic,
    semaphore: asyncio.Semaphore,
) -> dict:
    """
    Price a single row asynchronously with retry logic.

    Uses a semaphore to limit concurrency and implements exponential backoff
    for rate-limit errors.

    Parameters
    ----------
    row_dict : dict
    system_prompt : str
    client : anthropic.Anthropic
    semaphore : asyncio.Semaphore

    Returns
    -------
    dict
        Pricing result dict.
    """
    async with semaphore:
        for attempt in range(MAX_RETRIES):
            try:
                # price_row is synchronous; run in executor to avoid blocking the event loop
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: price_row(row_dict, system_prompt, client),
                )
                return result
            except anthropic.RateLimitError:
                wait_time = BASE_BACKOFF_S * (2 ** attempt)
                logger.warning(
                    "Rate limit on attempt %d/%d for %s — waiting %.1fs",
                    attempt + 1,
                    MAX_RETRIES,
                    row_dict.get("criteria_key", "?"),
                    wait_time,
                )
                await asyncio.sleep(wait_time)
            except Exception as exc:
                logger.error(
                    "Error on attempt %d/%d for %s: %s",
                    attempt + 1,
                    MAX_RETRIES,
                    row_dict.get("criteria_key", "?"),
                    exc,
                )
                if attempt == MAX_RETRIES - 1:
                    from src.ai_brain.pricer import _error_result
                    return _error_result(
                        row_dict.get("criteria_key", "unknown"),
                        f"Failed after {MAX_RETRIES} attempts: {exc}",
                        row_dict,
                    )
                await asyncio.sleep(BASE_BACKOFF_S * (2 ** attempt))

        # Should not reach here, but return error just in case
        from src.ai_brain.pricer import _error_result
        return _error_result(
            row_dict.get("criteria_key", "unknown"),
            f"Exhausted {MAX_RETRIES} retries",
            row_dict,
        )


async def _run_async_batch(
    rows: list[dict],
    system_prompt: str,
    client: anthropic.Anthropic,
    concurrency: int,
    batch_save_size: int,
    report_date: str,
    db_path: Union[str, Path],
) -> tuple[list[dict], dict]:
    """
    Core async runner that processes all rows concurrently.

    Parameters
    ----------
    rows : list of dict
    system_prompt : str
    client : anthropic.Anthropic
    concurrency : int
    batch_save_size : int
    report_date : str
    db_path : str or Path

    Returns
    -------
    tuple of (all_results, summary_dict)
    """
    semaphore = asyncio.Semaphore(concurrency)
    all_results: list[dict] = []
    pending_save: list[dict] = []

    pbar = tqdm(total=len(rows), desc="AI Pricing", unit="row")

    # Process in batches for periodic saving
    tasks = [
        _price_row_with_retry(row, system_prompt, client, semaphore)
        for row in rows
    ]

    # Use as_completed pattern via gather with return_exceptions
    results = await asyncio.gather(*tasks, return_exceptions=False)

    for result in results:
        all_results.append(result)
        pending_save.append(result)
        pbar.update(1)

        if len(pending_save) >= batch_save_size:
            _save_batch_to_db(pending_save, report_date, db_path)
            pending_save.clear()

    # Save remaining
    if pending_save:
        _save_batch_to_db(pending_save, report_date, db_path)

    pbar.close()

    # Compute summary
    summary = {
        "total": len(all_results),
        "increased": sum(1 for r in all_results if r.get("action") == "INCREASE_DISC"),
        "decreased": sum(1 for r in all_results if r.get("action") == "DECREASE_DISC"),
        "kept": sum(1 for r in all_results if r.get("action") == "KEEP"),
        "high_conf": sum(1 for r in all_results if r.get("confidence") == "HIGH"),
        "med_conf": sum(1 for r in all_results if r.get("confidence") == "MEDIUM"),
        "low_conf": sum(1 for r in all_results if r.get("confidence") == "LOW"),
        "needs_review_count": sum(1 for r in all_results if r.get("needs_review", False)),
        "errors": sum(1 for r in all_results if r.get("_error", False)),
    }

    return all_results, summary


def process_batch(
    df: pd.DataFrame,
    db_path: Union[str, Path],
    system_prompt: str,
    api_key: str,
    concurrency: int = 3,
    batch_save_size: int = 10,
) -> dict[str, Any]:
    """
    Process a full Base Report DataFrame through the AI pricing brain.

    Skips rows that have already been priced (checks ``pricing_results`` table).
    Uses asyncio for concurrent API calls with a concurrency cap.
    Saves results to the database periodically.

    Parameters
    ----------
    df : pd.DataFrame
        Normalised Base Report DataFrame.
    db_path : str or Path
        Path to the SQLite database.
    system_prompt : str
        Full system prompt from ``system_prompt.build_system_prompt``.
    api_key : str
        Anthropic API key.
    concurrency : int
        Maximum number of parallel API calls (default 3).
    batch_save_size : int
        Save results to DB after this many rows are processed (default 10).

    Returns
    -------
    dict
        Summary with keys: total, skipped, processed, increased, decreased,
        kept, high_conf, med_conf, low_conf, needs_review_count, errors.
    """
    db_path = Path(db_path)

    # Get report_date from DataFrame
    report_date = ""
    if "report_date" in df.columns and len(df) > 0:
        report_date = str(df["report_date"].iloc[0])

    # Find already-priced keys
    already_priced = _load_already_priced(db_path, report_date)
    logger.info("Found %d already-priced rows for date %s.", len(already_priced), report_date)

    # Filter to unpriced rows
    if "criteria_key" in df.columns:
        to_price = df[~df["criteria_key"].isin(already_priced)].copy()
    else:
        to_price = df.copy()

    skipped = len(df) - len(to_price)
    logger.info("Rows to price: %d (skipping %d already priced).", len(to_price), skipped)

    if len(to_price) == 0:
        return {
            "total": len(df),
            "skipped": skipped,
            "processed": 0,
            "increased": 0,
            "decreased": 0,
            "kept": 0,
            "high_conf": 0,
            "med_conf": 0,
            "low_conf": 0,
            "needs_review_count": 0,
            "errors": 0,
        }

    # Initialise Anthropic client
    client = anthropic.Anthropic(api_key=api_key)

    # Convert DataFrame rows to list of dicts
    rows_list = to_price.to_dict(orient="records")

    # Run async processing
    _, summary = asyncio.run(
        _run_async_batch(
            rows=rows_list,
            system_prompt=system_prompt,
            client=client,
            concurrency=concurrency,
            batch_save_size=batch_save_size,
            report_date=report_date,
            db_path=db_path,
        )
    )

    summary["total"] = len(df)
    summary["skipped"] = skipped
    summary["processed"] = summary["total"] - skipped

    # Log activity
    from src.pipeline.db_writer import log_activity
    log_activity(
        event_type="AI_PRICING",
        description=(
            f"Priced {summary['processed']} rows: "
            f"{summary['increased']} increased, {summary['decreased']} decreased, "
            f"{summary['kept']} kept, {summary['errors']} errors"
        ),
        metadata=summary,
        db_path=db_path,
    )

    logger.info("Batch pricing complete: %s", summary)
    return summary

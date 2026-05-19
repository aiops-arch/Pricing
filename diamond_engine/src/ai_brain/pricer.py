"""
pricer.py
---------
Single-row AI pricing using the Anthropic SDK.

This module provides ``price_row``, which sends a single Base Report row to
Claude (claude-sonnet-4-20250514) and returns a structured pricing decision.

Features:
  - Prompt caching on the system prompt (cache_control).
  - Strict JSON output parsing with validation.
  - Never raises — returns an error dict on any failure.
"""

import json
import logging
from typing import Any, Optional

import anthropic

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

MODEL_ID = "claude-sonnet-4-20250514"

REQUIRED_OUTPUT_FIELDS = [
    "action",
    "suggested_disc",
    "change",
    "confidence",
    "needs_review",
    "primary_reason",
    "signals_used",
    "full_reasoning",
]

VALID_ACTIONS = {"INCREASE_DISC", "DECREASE_DISC", "KEEP"}
VALID_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}


def _format_row_message(row_dict: dict[str, Any]) -> str:
    """
    Format a Base Report row dictionary into a human-readable message for the
    AI model.

    Parameters
    ----------
    row_dict : dict
        A single row from the normalised Base Report DataFrame as a dict.

    Returns
    -------
    str
        Formatted string ready to send as the user message.
    """
    def fmt(val, decimals=2):
        if val is None:
            return "N/A"
        if isinstance(val, float):
            return f"{val:.{decimals}f}"
        return str(val)

    lines = [
        "Please provide a pricing recommendation for the following diamond inventory row:",
        "",
        "## Identification",
        f"  criteria_key       : {row_dict.get('criteria_key', 'N/A')}",
        f"  Shape              : {row_dict.get('shape', 'N/A')}",
        f"  Size From          : {fmt(row_dict.get('size_from'))} ct",
        f"  Size To            : {fmt(row_dict.get('size_to'))} ct",
        f"  Clarity            : {row_dict.get('clarity', 'N/A')}",
        f"  Color              : {row_dict.get('color', 'N/A')}",
        f"  Cut                : {row_dict.get('cut', 'N/A')}",
        f"  Fluorescence       : {row_dict.get('fluor', 'N/A')}",
        "",
        "## Pricing",
        f"  Current Discount%  : {fmt(row_dict.get('current_disc'))}%",
        f"  Last Week Disc%    : {fmt(row_dict.get('last_week_disc'))}%",
        f"  Avg Market Disc%   : {fmt(row_dict.get('avg_disc'))}%",
        f"  Min Market Disc%   : {fmt(row_dict.get('min_disc'))}%",
        f"  Max Market Disc%   : {fmt(row_dict.get('max_disc'))}%",
        f"  Top Competitor Disc: {fmt(row_dict.get('competitor_top_disc'))}%",
        f"  Avg Disc Gap       : {fmt(row_dict.get('avg_disc_gap'))}%  (positive = we are cheaper than avg)",
        "",
        "## Inventory & Sales",
        f"  Stock (pcs)        : {fmt(row_dict.get('stock'), 0)}",
        f"  Inv Days           : {fmt(row_dict.get('inv_days'), 0)}",
        f"  Sold Last 3 Months : {fmt(row_dict.get('sold_3m'), 0)}",
        f"  Sold Last 1 Week   : {fmt(row_dict.get('sold_1w'), 0)}",
        f"  Sold Ratio (wk/avg): {fmt(row_dict.get('sold_ratio'), 2)}",
        "",
        "## Market Position (RapNet)",
        f"  RapNet Pos India   : {fmt(row_dict.get('rapnet_pos_india'), 0)}",
        f"  RapNet Pos World   : {fmt(row_dict.get('rapnet_pos_world'), 0)}",
        f"  RapNet Pos USA     : {fmt(row_dict.get('rapnet_pos_usa'), 0)}",
        "",
        "## Remarks & Triggers",
        f"  Inv Remark         : {row_dict.get('inv_remark', 'N/A')}",
        f"  Bas Fix Remark     : {row_dict.get('bas_fix_remark', 'N/A')}",
        f"  Triggers           : {row_dict.get('triggers', 'N/A')}",
        f"  Is Program         : {row_dict.get('is_program', False)}",
        f"  Has Sold High      : {row_dict.get('has_sold_high', False)}",
        f"  Trigger Count      : {fmt(row_dict.get('trigger_count', 0), 0)}",
        "",
        "## Manufacturing",
        f"  MFG 3M             : {fmt(row_dict.get('mfg_3m'), 0)}",
        "",
        "Respond with a JSON object only — no markdown, no commentary outside the JSON.",
    ]
    return "\n".join(lines)


def _validate_result(result: dict) -> tuple[bool, str]:
    """
    Validate that the parsed JSON result contains all required fields and
    valid enum values.

    Parameters
    ----------
    result : dict
        Parsed JSON dictionary from the AI response.

    Returns
    -------
    tuple of (bool, str)
        (is_valid, error_message).  error_message is empty string if valid.
    """
    for field in REQUIRED_OUTPUT_FIELDS:
        if field not in result:
            return False, f"Missing required field: '{field}'"

    if result["action"] not in VALID_ACTIONS:
        return False, f"Invalid action: '{result['action']}' (must be one of {VALID_ACTIONS})"

    if result["confidence"] not in VALID_CONFIDENCE:
        return False, f"Invalid confidence: '{result['confidence']}' (must be one of {VALID_CONFIDENCE})"

    try:
        float(result["suggested_disc"])
        float(result["change"])
    except (TypeError, ValueError) as e:
        return False, f"Non-numeric suggested_disc or change: {e}"

    if not isinstance(result["signals_used"], list):
        result["signals_used"] = [str(result["signals_used"])]

    return True, ""


def _extract_json_from_text(text: str) -> Optional[dict]:
    """
    Attempt to extract a JSON object from text that may contain surrounding
    markdown or commentary.

    Parameters
    ----------
    text : str
        Raw response text from the AI model.

    Returns
    -------
    dict or None
        Parsed dict if found, otherwise None.
    """
    # Try direct parse first
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Try to find a JSON block within markdown code fences
    import re
    json_pattern = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_pattern:
        try:
            return json.loads(json_pattern.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find the first { ... } block in the response
    brace_pattern = re.search(r"(\{.*\})", text, re.DOTALL)
    if brace_pattern:
        try:
            return json.loads(brace_pattern.group(1))
        except json.JSONDecodeError:
            pass

    return None


def price_row(
    row_dict: dict[str, Any],
    system_prompt: str,
    client: anthropic.Anthropic,
) -> dict[str, Any]:
    """
    Price a single Base Report row using the Anthropic Claude API.

    The system prompt is sent with ``cache_control`` enabled so that repeated
    calls within the same session benefit from prompt caching and reduced cost.

    Parameters
    ----------
    row_dict : dict
        A single row from the normalised Base Report DataFrame as a dictionary.
    system_prompt : str
        Full system prompt built by ``system_prompt.build_system_prompt``.
    client : anthropic.Anthropic
        Initialised Anthropic API client.

    Returns
    -------
    dict
        Pricing result with keys: action, suggested_disc, change, confidence,
        needs_review, primary_reason, signals_used, full_reasoning.
        On any failure, returns a dict with action="KEEP", confidence="LOW",
        needs_review=True, and an error description in primary_reason.
    """
    criteria_key = row_dict.get("criteria_key", "unknown")

    try:
        user_message = _format_row_message(row_dict)

        response = client.messages.create(
            model=MODEL_ID,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": user_message,
                }
            ],
        )

        raw_text = response.content[0].text if response.content else ""
        logger.debug("Raw AI response for %s: %s", criteria_key, raw_text[:200])

        result = _extract_json_from_text(raw_text)
        if result is None:
            logger.error("Could not parse JSON from AI response for %s", criteria_key)
            return _error_result(criteria_key, "Could not parse JSON from AI response", row_dict)

        is_valid, error_msg = _validate_result(result)
        if not is_valid:
            logger.error("Invalid AI result for %s: %s", criteria_key, error_msg)
            return _error_result(criteria_key, f"Validation failed: {error_msg}", row_dict)

        # Ensure needs_review is a Python bool
        result["needs_review"] = bool(result.get("needs_review", False))
        result["criteria_key"] = criteria_key
        result["current_disc"] = row_dict.get("current_disc", 0.0)

        logger.debug(
            "Priced %s: action=%s disc=%.2f->%.2f conf=%s",
            criteria_key,
            result["action"],
            row_dict.get("current_disc", 0.0),
            result["suggested_disc"],
            result["confidence"],
        )
        return result

    except anthropic.RateLimitError as exc:
        logger.warning("Rate limit hit for %s: %s", criteria_key, exc)
        raise  # Let batch_pricer handle retry
    except anthropic.APIError as exc:
        logger.error("API error for %s: %s", criteria_key, exc)
        return _error_result(criteria_key, f"API error: {exc}", row_dict)
    except Exception as exc:
        logger.exception("Unexpected error pricing %s: %s", criteria_key, exc)
        return _error_result(criteria_key, f"Unexpected error: {exc}", row_dict)


def _error_result(criteria_key: str, reason: str, row_dict: dict) -> dict:
    """
    Build a safe fallback result dict when pricing fails.

    Parameters
    ----------
    criteria_key : str
    reason : str
        Error description.
    row_dict : dict
        Original row for current_disc reference.

    Returns
    -------
    dict
    """
    current_disc = row_dict.get("current_disc", 0.0) or 0.0
    return {
        "criteria_key": criteria_key,
        "action": "KEEP",
        "suggested_disc": current_disc,
        "change": 0.0,
        "confidence": "LOW",
        "needs_review": True,
        "primary_reason": reason,
        "signals_used": ["error"],
        "full_reasoning": f"Pricing failed with error: {reason}",
        "current_disc": current_disc,
        "_error": True,
    }

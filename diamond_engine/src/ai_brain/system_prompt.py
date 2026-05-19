"""
system_prompt.py
----------------
Builds the system prompt used by the AI pricing brain.

The system prompt encodes all known pricing rules (hard-coded) and optionally
appends text extracted from a PDF rulebook (e.g. a company pricing policy doc).

The AI is instructed to respond ONLY with a JSON object in the defined schema.
"""

import logging
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def extract_rulebook_text(pdf_path: Union[str, Path]) -> str:
    """
    Extract all text from a PDF rulebook using pdfplumber.

    Parameters
    ----------
    pdf_path : str or Path
        Path to the PDF file.

    Returns
    -------
    str
        Extracted text, or an empty string if the file does not exist or
        extraction fails.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        logger.warning("PDF rulebook not found at %s — skipping extraction.", pdf_path)
        return ""

    try:
        import pdfplumber  # lazy import so module loads without pdfplumber installed

        text_parts = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text() or ""
                if page_text.strip():
                    text_parts.append(f"--- Page {page_num} ---\n{page_text}")
        combined = "\n\n".join(text_parts)
        logger.info("Extracted %d characters from PDF rulebook (%d pages).", len(combined), len(text_parts))
        return combined
    except Exception as exc:
        logger.error("Failed to extract PDF text: %s", exc)
        return ""


# --------------------------------------------------------------------------- #
# HARD-CODED RULES                                                             #
# --------------------------------------------------------------------------- #
HARD_CODED_RULES = """
=============================================================
DIAMOND PRICING RULES — HARD-CODED COMPANY POLICY
=============================================================

You are an expert diamond pricing analyst for a high-end diamond trading company.
Your job is to recommend discount adjustments for each inventory row, based on
market position, sell-through velocity, competitor pricing, and internal triggers.

---
RULE 1 — RapNet is the Primary Reference
---
• If our RapNet position is 1 or 2, do NOT reduce the discount (i.e. do not lower our price further).
• If the gap between our discount and the top (cheapest) competitor is less than 0.5%, do NOT change the discount.
• RapNet position is the rank of our stone among all listed stones in the same
  shape/size/clarity/color category.  Lower rank = cheaper = more competitive.

---
RULE 2 — PROGRAM Criteria: NEVER CHANGE
---
• If the "triggers" field contains the word "Program" (case-insensitive), the
  discount MUST remain unchanged. Output action = "KEEP" with reason "Program criteria."
• This rule overrides ALL other rules.

---
RULE 3 — Low Inventory Caution
---
• If stock <= 3 pieces, start at a higher price (lower absolute discount).
• The "Bas Fix Pcs Pos Remark" (bas_fix_remark) may be misleading for low-inventory
  items. Do not rely solely on it; always cross-check RapNet position.

---
RULE 4 — When to INCREASE DISCOUNT (lower our price / be more competitive)
---
Increase discount under any of the following conditions:
  a) inv_remark contains "Price Reduction" AND rapnet_pos_india > 3.
  b) sold_1w = 0 AND inv_days > 30  (stone is sitting unsold with no recent activity).
  c) The top competitor is more than 1% cheaper than us (competitor_top_disc > our
     current_disc by more than 1.0 percentage point).
  d) avg_disc_gap is significantly negative (our discount is notably below market avg),
     meaning we are priced higher than the average market.

---
RULE 5 — When to DECREASE DISCOUNT (raise our price / be less aggressive)
---
Decrease discount (raise price) under any of the following conditions:
  a) bas_fix_remark contains "Price Raised" AND we are near the top of RapNet
     (pos <= 3).
  b) "Sold High" appears in triggers — recent sales at higher prices signal
     strong demand that supports a higher price.
  c) avg_disc_gap > +3%  (we are significantly cheaper than the market average,
     leaving money on the table).
  d) RapNet availability is very low (few stones listed) — scarcity supports a
     higher price.
  e) F or G color stones in high-demand periods — these near-colorless stones
     attract premium buyers.
  f) None/No fluorescence is preferred by buyers — if fluor = "None" or "NON",
     the stone may command a premium.

---
RULE 6 — H/I/J Color Benchmark Warning
---
• For H, I, or J color stones: yellowish stones at the top of RapNet are NOT
  valid price benchmarks.  A low-ranked competitor may simply have a
  yellower stone.  Do not blindly follow their price.

---
RULE 7 — Low-Quality Competitor Exclusion
---
• The following companies are known to list low-quality stones on RapNet and
  should be ignored when they appear at or near the top:
    - Paladiya
    - Pansuriya
    - Narola
• If the top 1-2 RapNet positions are occupied by these companies, look further
  down the list for a genuine benchmark.

---
RULE 8 — Maximum Weekly Change
---
• The maximum discount change in any single week is ±2.0 percentage points.
• Exception: If multiple strong signals all agree (3 or more aligned signals),
  a change up to ±3.0% may be justified — but flag needs_review = true.

---
RULE 9 — Group Consistency
---
• If the majority of rows in the same size/clarity/color group have been moved
  in one direction this week, follow the same direction for remaining rows in
  that group, unless a row has a specific contradicting signal.

---
ANTI-HALLUCINATION INSTRUCTIONS
---
• Only use the data provided in the user message. Do NOT invent competitor
  names, discount values, RapNet positions, or any other data point.
• If data is missing or unclear, note it in your reasoning and apply a
  conservative KEEP decision.
• Never reference external market events, news, or knowledge outside the
  provided row data.

---
OUTPUT FORMAT
---
You MUST respond with a single JSON object — no markdown, no extra text.
Schema:
{
  "action": "INCREASE_DISC" | "DECREASE_DISC" | "KEEP",
  "suggested_disc": <float, the recommended new discount percentage>,
  "change": <float, signed delta from current_disc; positive = discount increased>,
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "needs_review": <bool>,
  "primary_reason": <string, one concise sentence>,
  "signals_used": [<string>, ...],
  "full_reasoning": <string, detailed multi-sentence explanation>
}

Rules for the JSON values:
- "action" must be exactly one of: INCREASE_DISC, DECREASE_DISC, KEEP.
- "suggested_disc" must be within ±2.0% of current_disc (or ±3.0% if exceptional).
- "change" = suggested_disc - current_disc.
- "confidence" = HIGH if 2+ strong aligned signals; MEDIUM if 1 signal; LOW if ambiguous.
- "needs_review" = true if change > 1.5% or if signals conflict.
- "signals_used" = list of signal names that drove the decision.
- "full_reasoning" = detailed explanation referencing actual data values.
"""


def build_system_prompt(rulebook_text: str = "") -> str:
    """
    Construct the full system prompt for the AI pricing brain.

    The prompt consists of:
    1. Hard-coded business rules (HARD_CODED_RULES).
    2. Optionally, text extracted from the company's PDF rulebook.

    Parameters
    ----------
    rulebook_text : str, optional
        Text extracted from the PDF rulebook via ``extract_rulebook_text``.
        If empty, only the hard-coded rules are used.

    Returns
    -------
    str
        The complete system prompt string.
    """
    parts = [HARD_CODED_RULES.strip()]

    if rulebook_text and rulebook_text.strip():
        parts.append(
            "\n\n=============================================================\n"
            "ADDITIONAL RULES FROM COMPANY RULEBOOK (PDF)\n"
            "=============================================================\n"
            + rulebook_text.strip()
        )
    else:
        parts.append(
            "\n\n[No PDF rulebook text was provided. Applying hard-coded rules only.]"
        )

    full_prompt = "\n".join(parts)
    logger.info("System prompt built (%d characters).", len(full_prompt))
    return full_prompt

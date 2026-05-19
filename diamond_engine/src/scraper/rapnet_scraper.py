"""
rapnet_scraper.py
-----------------
Playwright-based scraper for RapNet diamond listings.

This module provides the ``RapNetScraper`` class, which automates login to
RapNet and searches for diamond listings by criteria (shape, size, clarity,
color). Results are returned as a list of dicts and can be saved as snapshots
to the database.

Low-quality competitor companies (Paladiya, Pansuriya, Narola) are
automatically flagged in the ``is_low_qc`` field of each result.

Rate limiting: a 2-second delay is enforced between consecutive search calls.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Companies known to list low-quality stones on RapNet
LOW_QC_COMPANIES: list[str] = ["Paladiya", "Pansuriya", "Narola"]


class RapNetScraper:
    """
    Playwright-based scraper for RapNet diamond market data.

    Parameters
    ----------
    username : str
        RapNet account username (typically an email address).
    password : str
        RapNet account password.
    headless : bool
        Whether to run Playwright in headless mode (default True).

    Attributes
    ----------
    _browser : playwright.async_api.Browser or None
    _page : playwright.async_api.Page or None
    _logged_in : bool
    _last_request_time : float
    """

    RAPNET_URL = "https://www.rapnet.com"
    LOGIN_URL = "https://www.rapnet.com/login"
    SEARCH_URL = "https://www.rapnet.com/diamonds/search"
    REQUEST_DELAY_S = 2.0

    def __init__(self, username: str, password: str, headless: bool = True) -> None:
        self.username = username
        self.password = password
        self.headless = headless
        self._browser = None
        self._page = None
        self._playwright = None
        self._logged_in = False
        self._last_request_time: float = 0.0

    async def _init_browser(self) -> None:
        """Initialise the Playwright browser if not already started."""
        if self._browser is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self.headless)
            self._page = await self._browser.new_page()
            logger.info("Playwright browser initialised (headless=%s).", self.headless)

    async def _rate_limit(self) -> None:
        """Enforce a minimum delay between requests."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self.REQUEST_DELAY_S:
            await asyncio.sleep(self.REQUEST_DELAY_S - elapsed)
        self._last_request_time = time.monotonic()

    async def login(self) -> bool:
        """
        Log in to RapNet using Playwright.

        Navigates to the login page, fills in credentials, submits the form,
        and verifies login success.

        Returns
        -------
        bool
            True if login succeeded, False otherwise.
        """
        await self._init_browser()
        await self._rate_limit()

        try:
            logger.info("Navigating to RapNet login page.")
            await self._page.goto(self.LOGIN_URL, wait_until="networkidle", timeout=30000)

            # Fill username
            await self._page.fill('input[name="username"], input[type="email"], #username', self.username)
            # Fill password
            await self._page.fill('input[name="password"], input[type="password"], #password', self.password)
            # Submit
            await self._page.click('button[type="submit"], input[type="submit"], .login-btn')
            await self._page.wait_for_load_state("networkidle", timeout=15000)

            # Check for successful login indicator
            current_url = self._page.url
            page_content = await self._page.content()

            if "login" in current_url.lower() or "error" in page_content.lower()[:500]:
                logger.error("Login appears to have failed. Current URL: %s", current_url)
                self._logged_in = False
                return False

            self._logged_in = True
            logger.info("RapNet login successful.")
            return True

        except Exception as exc:
            logger.error("Login failed with exception: %s", exc)
            self._logged_in = False
            return False

    async def search_criteria(
        self,
        shape: str,
        size_from: float,
        size_to: float,
        clarity: str,
        color: str,
    ) -> list[dict[str, Any]]:
        """
        Search RapNet for diamond listings matching the given criteria and
        return the results.

        Parameters
        ----------
        shape : str
            Diamond shape (e.g. "ROUND", "PRINCESS").
        size_from : float
            Minimum carat weight.
        size_to : float
            Maximum carat weight.
        clarity : str
            Clarity grade (e.g. "VS1", "SI2").
        color : str
            Color grade (e.g. "D", "G").

        Returns
        -------
        list of dict
            Each dict contains:
              - rank        : int, listing rank (1 = cheapest)
              - company     : str, seller company name
              - discount_pct: float, discount percentage
              - carat       : float, exact carat weight
              - cert        : str, certification lab
              - is_low_qc   : bool, True if company is in LOW_QC_COMPANIES
        """
        if not self._logged_in:
            raise RuntimeError("Not logged in. Call login() first.")

        await self._rate_limit()

        criteria_key = f"{shape}|{size_from}|{size_to}|{clarity}|{color}"
        logger.info("Searching RapNet: %s", criteria_key)

        try:
            await self._page.goto(self.SEARCH_URL, wait_until="networkidle", timeout=20000)

            # Fill in search form fields
            # Shape selector
            try:
                await self._page.select_option('select[name="shape"], #shape', value=shape.upper(), timeout=5000)
            except Exception:
                logger.debug("Could not set shape via select_option; trying fill.")
                try:
                    await self._page.fill('input[name="shape"], #shape', shape)
                except Exception:
                    pass

            # Size range
            try:
                await self._page.fill('input[name="size_from"], #size_from, input[placeholder*="From"]', str(size_from))
                await self._page.fill('input[name="size_to"], #size_to, input[placeholder*="To"]', str(size_to))
            except Exception as e:
                logger.debug("Could not fill size fields: %s", e)

            # Clarity
            try:
                await self._page.select_option('select[name="clarity"], #clarity', value=clarity.upper(), timeout=3000)
            except Exception:
                try:
                    await self._page.fill('input[name="clarity"], #clarity', clarity)
                except Exception:
                    pass

            # Color
            try:
                await self._page.select_option('select[name="color"], #color', value=color.upper(), timeout=3000)
            except Exception:
                try:
                    await self._page.fill('input[name="color"], #color', color)
                except Exception:
                    pass

            # Submit search
            await self._page.click('button[type="submit"], .search-btn, input[type="submit"]')
            await self._page.wait_for_load_state("networkidle", timeout=20000)

            # Parse results from the page
            results = await self._parse_search_results()
            logger.info("Found %d results for %s.", len(results), criteria_key)
            return results

        except Exception as exc:
            logger.error("Search failed for %s: %s", criteria_key, exc)
            return []

    async def _parse_search_results(self) -> list[dict[str, Any]]:
        """
        Parse the search results page and extract listing data.

        This is a best-effort parser; actual CSS selectors depend on the live
        RapNet HTML structure.

        Returns
        -------
        list of dict
        """
        results = []
        try:
            # Try to extract from a table structure (common pattern)
            rows = await self._page.query_selector_all("table.results-table tbody tr, .diamond-list-row")
            for rank, row in enumerate(rows, start=1):
                try:
                    cells = await row.query_selector_all("td")
                    if len(cells) < 3:
                        continue

                    cell_texts = [await cell.inner_text() for cell in cells]

                    company = cell_texts[0].strip() if len(cell_texts) > 0 else "Unknown"
                    carat_text = cell_texts[1].strip() if len(cell_texts) > 1 else "0"
                    disc_text = cell_texts[2].strip() if len(cell_texts) > 2 else "0"
                    cert = cell_texts[3].strip() if len(cell_texts) > 3 else "Unknown"

                    try:
                        carat = float(carat_text.replace(",", ""))
                    except ValueError:
                        carat = 0.0

                    try:
                        discount_pct = float(
                            disc_text.replace("%", "").replace(",", "").strip()
                        )
                    except ValueError:
                        discount_pct = 0.0

                    is_low_qc = any(
                        lqc.lower() in company.lower() for lqc in LOW_QC_COMPANIES
                    )

                    results.append(
                        {
                            "rank": rank,
                            "company": company,
                            "discount_pct": discount_pct,
                            "carat": carat,
                            "cert": cert,
                            "is_low_qc": is_low_qc,
                        }
                    )
                except Exception as row_exc:
                    logger.debug("Could not parse row %d: %s", rank, row_exc)

        except Exception as exc:
            logger.warning("Could not parse search results: %s", exc)

        return results

    def save_snapshot(
        self,
        results: list[dict[str, Any]],
        criteria_key: str,
        db_path: Union[str, Path],
        shape: str = "",
        size_from: float = 0.0,
        size_to: float = 0.0,
        clarity: str = "",
        color: str = "",
    ) -> None:
        """
        Save a set of RapNet search results as a snapshot in the database.

        Parameters
        ----------
        results : list of dict
            Output from ``search_criteria``.
        criteria_key : str
            The criteria identifier string.
        db_path : str or Path
            Path to the SQLite database.
        shape, size_from, size_to, clarity, color : str/float
            Criteria metadata stored alongside the snapshot.
        """
        import sqlite3
        from datetime import date

        db_path = Path(db_path)
        snap_date = date.today().isoformat()

        # Determine best positions (first non-low-qc rank)
        non_lqc = [r for r in results if not r.get("is_low_qc", False)]
        position_india = non_lqc[0]["rank"] if non_lqc else 999
        position_world = non_lqc[0]["rank"] if non_lqc else 999
        position_usa = non_lqc[0]["rank"] if non_lqc else 999

        raw_json = json.dumps(results)

        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """
                INSERT INTO rapnet_snapshots
                  (criteria_key, shape, size_from, size_to, clarity, color,
                   snap_date, position_india, position_world, position_usa, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    criteria_key, shape, size_from, size_to, clarity, color,
                    snap_date, position_india, position_world, position_usa, raw_json,
                ),
            )
            conn.commit()

        logger.info("Snapshot saved for %s (%d results).", criteria_key, len(results))

    async def close(self) -> None:
        """Close the Playwright browser and clean up resources."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self._logged_in = False
        logger.info("RapNet scraper browser closed.")

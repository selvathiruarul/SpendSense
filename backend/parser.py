"""
Parser module: turns messy PDF/CSV bank statements into a list of
normalized transaction dicts:
    {"date": "2026-03-10", "raw_desc": "AMZN MKTP US", "amount": -42.50}

Sign convention (matches our DB model):
    amount < 0  →  expense  (purchase, fee)
    amount > 0  →  income   (payment, credit, refund)

Chase credit card sign convention on the statement is the opposite
(purchases positive, payments negative), so we flip the sign for CC PDFs.
The flip is applied to the raw parsed amount based on section context.
"""
from __future__ import annotations

import re
import pdfplumber
import pandas as pd
from datetime import datetime, date
from pathlib import Path


# ── Public API ────────────────────────────────────────────────────────────────

def parse_file(file_path: str) -> list[dict]:
    """Auto-detect file type and parse into normalized transactions."""
    path = Path(file_path)
    if path.suffix.lower() == ".pdf":
        return parse_pdf(file_path)
    elif path.suffix.lower() == ".csv":
        return parse_csv(file_path)
    raise ValueError(f"Unsupported file type: {path.suffix}")


def parse_pdf(file_path: str) -> list[dict]:
    """
    Extract transactions from a bank PDF statement.

    Chase (and some other banks) renders each character twice at the same
    position. We call page.dedupe_chars() to collapse those first.

    Strategy 1: pdfplumber table extraction (structured PDFs).
    Strategy 2: section-aware line parsing (Chase and most bank PDFs).

    Year-boundary handling: a February 2026 statement covers Dec 2025 –
    Jan 2026. Transactions with MM > statement_month get year - 1.
    """
    transactions = []
    path = Path(file_path)

    with pdfplumber.open(file_path) as pdf:
        # Read first-page text to determine statement period (month + year)
        first_text = pdf.pages[0].dedupe_chars(tolerance=1).extract_text() or ""
        stmt_year, stmt_month = _infer_statement_period(first_text, path.name)

        for page in pdf.pages:
            clean_page = page.dedupe_chars(tolerance=1)

            # Strategy 1: table extraction
            tables = clean_page.extract_tables()
            page_txs = []
            for table in tables:
                for row in table:
                    if row:
                        tx = _parse_table_row(row, stmt_year, stmt_month)
                        if tx:
                            page_txs.append(tx)

            # Strategy 2: section-aware text-line parsing
            if not page_txs:
                text = clean_page.extract_text() or ""
                page_txs = _parse_text_page(text, stmt_year, stmt_month)

            transactions.extend(page_txs)

    return transactions


def parse_csv(file_path: str) -> list[dict]:
    """Extract transactions from a CSV bank statement."""
    df = pd.read_csv(file_path)
    df.columns = [c.strip().lower() for c in df.columns]

    transactions = []
    for _, row in df.iterrows():
        tx = _normalize_csv_row(row)
        if tx:
            transactions.append(tx)
    return transactions


# ── Section-aware text parser ─────────────────────────────────────────────────

# Section headers that signal "we are now in a transaction list"
_TX_SECTION_HEADERS = re.compile(
    r"(purchases?\s+and\s+adjustments?|account\s+activity|transactions?|"
    r"purchases?|payments?\s+and\s+credits?|fees?|interest\s+charged)",
    re.IGNORECASE,
)

# Section headers that signal "done with transactions"
_NON_TX_SECTION_HEADERS = re.compile(
    r"(interest\s+charge\s+calculation|account\s+summary|rewards\s+summary|"
    r"important\s+disclosures?|your\s+account\s+messages?|page\s+\d)",
    re.IGNORECASE,
)

# Lines to always skip regardless of section
_SKIP_PATTERNS = re.compile(
    r"(total\s+fees|total\s+interest|minimum\s+payment|new\s+balance"
    r"|previous\s+balance|credit\s+limit|available\s+credit"
    r"|opening\/closing|statement\s+period|annual\s+percentage)",
    re.IGNORECASE,
)


def _parse_text_page(text: str, stmt_year: int, stmt_month: int) -> list[dict]:
    """
    Parse transactions from a page's text, tracking which section we're in.
    Only accepts lines while inside a known transaction section.
    """
    txs = []
    in_tx_section = False

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Check section transitions
        if _TX_SECTION_HEADERS.search(stripped):
            in_tx_section = True
            continue
        if _NON_TX_SECTION_HEADERS.search(stripped):
            in_tx_section = False
            continue
        if _SKIP_PATTERNS.search(stripped):
            continue

        if not in_tx_section:
            continue

        tx = _parse_text_line(stripped, stmt_year, stmt_month)
        if tx:
            txs.append(tx)

    return txs


def _parse_text_line(line: str, stmt_year: int, stmt_month: int) -> dict | None:
    """
    Parse a single text line as a transaction.
    CC format:       "01/15 WHOLE FOODS MARKET  52.34"
    Checking format: "01/15 WHOLE FOODS MARKET  -52.34  30,918.81"  (AMOUNT BALANCE)
    """
    if not line or len(line) < 10:
        return None

    # Detect AMOUNT BALANCE pattern (checking statements: two numbers at end)
    # e.g. "-200.00 31,313.94" — take the first (amount), discard second (balance)
    two_nums = re.search(
        r"([-+]?\(?\d{1,3}(?:,\d{3})*(?:\.\d{2})?\)?)\s+(\d{1,3}(?:,\d{3})*\.\d{2})$",
        line,
    )
    if two_nums:
        amount = _try_parse_amount(two_nums.group(1))
        if amount is not None:
            remainder = line[: two_nums.start()].strip()
            # Fall through to date parsing below
            return _finish_tx_line(remainder, amount, stmt_year, stmt_month)

    # Single amount at end of line (credit card / simple format)
    amount_match = re.search(r"[-+]?\(?\d{1,3}(?:,\d{3})*(?:\.\d{2})?\)?$", line)
    if not amount_match:
        return None
    amount = _try_parse_amount(amount_match.group())
    if amount is None:
        return None

    remainder = line[: amount_match.start()].strip()
    return _finish_tx_line(remainder, amount, stmt_year, stmt_month)


def _finish_tx_line(remainder: str, amount: float, stmt_year: int, stmt_month: int) -> dict | None:
    """Extract date + description from the remainder of a parsed line."""

    # Date at start — full date first, then MM/DD fallback
    date_match = re.match(
        r"^("
        r"\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}"      # MM/DD/YYYY or MM/DD/YY
        r"|\d{4}[\/\-]\d{2}[\/\-]\d{2}"             # YYYY-MM-DD
        r"|\d{1,2}[\s\-][A-Za-z]{3}[\s\-]\d{2,4}"  # 15-Jan-2026
        r"|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}"      # January 15, 2026
        r"|\d{1,2}\/\d{1,2}"                         # MM/DD (no year)
        r")",
        remainder,
    )
    if not date_match:
        return None

    date_str = _try_parse_date(date_match.group().strip(), stmt_year, stmt_month)
    if not date_str:
        return None

    raw_desc = remainder[date_match.end():].strip()
    if len(raw_desc) < 2:
        return None

    return {"date": date_str, "raw_desc": raw_desc, "amount": amount}


# ── Table row parser ──────────────────────────────────────────────────────────

def _parse_table_row(
    row: list, stmt_year: int | None = None, stmt_month: int | None = None
) -> dict | None:
    """Try to parse a PDF table row as a transaction."""
    date_str = None
    raw_desc = None
    amount = None

    for cell in row:
        if cell is None:
            continue
        cell = str(cell).strip()
        if not cell:
            continue

        if date_str is None:
            parsed = _try_parse_date(cell, stmt_year or date.today().year, stmt_month or date.today().month)
            if parsed:
                date_str = parsed
                continue

        if amount is None:
            parsed_amt = _try_parse_amount(cell)
            if parsed_amt is not None:
                amount = parsed_amt
                continue

        if raw_desc is None and len(cell) > 3 and not cell.replace(" ", "").isdigit():
            raw_desc = cell

    if date_str and raw_desc and amount is not None:
        return {"date": date_str, "raw_desc": raw_desc, "amount": amount}
    return None


# ── CSV helpers ───────────────────────────────────────────────────────────────

def _normalize_csv_row(row) -> dict | None:
    """Map common CSV column names to standard format."""
    today = date.today()
    date_val = _find_in_row(
        row,
        ["date", "transaction date", "posted date", "value date", "trans. date"],
        lambda s: _try_parse_date(s, today.year, today.month),
    )
    raw_desc = _find_str_in_row(
        row, ["description", "memo", "payee", "merchant", "details", "narrative"]
    )
    amount = _find_in_row(
        row,
        ["amount", "debit", "credit", "transaction amount", "withdrawals", "deposits"],
        _try_parse_amount,
    )

    if date_val and raw_desc and amount is not None:
        return {"date": date_val, "raw_desc": raw_desc, "amount": amount}
    return None


def _find_in_row(row, col_names: list[str], parser):
    for col in col_names:
        if col in row.index and pd.notna(row[col]):
            result = parser(str(row[col]))
            if result is not None:
                return result
    return None


def _find_str_in_row(row, col_names: list[str]) -> str | None:
    for col in col_names:
        if col in row.index and pd.notna(row[col]):
            val = str(row[col]).strip()
            if val:
                return val
    return None


# ── Date / amount parsing ─────────────────────────────────────────────────────

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9,
    "oct": 10, "nov": 11, "dec": 12,
}


def _infer_statement_period(first_page_text: str, filename: str) -> tuple[int, int]:
    """
    Return (statement_year, statement_month) by scanning page-1 text,
    then falling back to the filename, then to today's date.
    """
    match = re.search(
        r"(january|february|march|april|may|june|july|august|september"
        r"|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
        r"\s+(20\d{2})",
        first_page_text,
        re.IGNORECASE,
    )
    if match:
        month = _MONTH_NAMES.get(match.group(1).lower(), 1)
        year = int(match.group(2))
        return year, month

    year_match = re.search(r"(20\d{2})", filename)
    year = int(year_match.group(1)) if year_match else date.today().year
    return year, date.today().month


def _try_parse_date(
    s: str,
    stmt_year: int | None = None,
    stmt_month: int | None = None,
) -> str | None:
    """
    Return ISO date string or None.

    For MM/DD-only dates (Chase/Amex), infers the correct year using the
    statement period:
      - tx_month <= stmt_month  →  stmt_year       (same year)
      - tx_month >  stmt_month  →  stmt_year - 1   (prior year, crossed Jan 1)
    """
    formats = [
        "%m/%d/%Y", "%m/%d/%y",
        "%d/%m/%Y", "%d/%m/%y",
        "%Y-%m-%d",
        "%d-%b-%Y", "%d-%b-%y",
        "%B %d, %Y", "%b %d, %Y",
        "%d %b %Y", "%d %B %Y",
        "%Y%m%d",
    ]
    s = s.strip()
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    # MM/DD with no year
    mo_day = re.match(r"^(\d{1,2})\/(\d{1,2})$", s)
    if mo_day:
        tx_month = int(mo_day.group(1))
        tx_day = int(mo_day.group(2))
        base_year = stmt_year or date.today().year
        base_month = stmt_month or date.today().month

        # Transactions with a month later in the year than the statement
        # month belong to the prior calendar year
        year = base_year if tx_month <= base_month else base_year - 1
        try:
            return datetime(year, tx_month, tx_day).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def _try_parse_amount(s: str) -> float | None:
    """Return float or None. Handles $, commas, parentheses (negatives)."""
    s = re.sub(r"[$,\s]", "", s.strip())
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        val = float(s)
        if abs(val) > 1_000_000:
            return None
        return val
    except ValueError:
        return None

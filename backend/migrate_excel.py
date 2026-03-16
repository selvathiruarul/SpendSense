"""
Migrate a PB-2025.xlsx monthly budget sheet into SpendSense.

Expense data comes from the per-month sheet (e.g. "January"):
  - Rows: category (col B) / subcategory (col C)
  - Columns: one per day of month (cols E–AI, pandas indices 4–34)
  - Each non-zero cell = amount spent on that day in that subcategory

Income data comes from the "Tracking" sheet:
  - Row 8 = Salary/Wages,  row 9 = Bonus,  rows 10–12 = other income
  - Columns: Jan=3, Feb=4, … Dec=14
  - A single monthly income total is stored as one transaction on the 1st

Usage:
    python -m backend.migrate_excel \
        --file ~/Desktop/imp/PB-2025.xlsx \
        --sheet January \
        --year 2025 \
        --month 1
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

import pandas as pd
from sqlalchemy.orm import Session

from backend.database import engine, Base
from backend.models import Transaction

# ── Expense grid layout ───────────────────────────────────────────────────────
DAY_COL_START = 4   # "1st"
DAY_COL_END   = 34  # "31st" (inclusive)
CAT_COL = 1
SUB_COL = 2

# ── Tracking sheet layout ─────────────────────────────────────────────────────
TRACKING_SHEET   = "Tracking"
TRACKING_HDR_ROW = 6   # row index with "Jan", "Feb", ...
TRACKING_MONTH_COL_JAN = 3  # Jan = col 3, Feb = 4, ..., Dec = 14

# income source rows in Tracking sheet (0-indexed)
INCOME_ROWS = {
    8:  ("Salary",  "Salary"),
    9:  ("Salary",  "Bonus"),     # only if non-zero
    10: ("Income",  "Other"),
    11: ("Income",  "Other"),
    12: ("Income",  "Other"),
}

# ── Label normalisation ───────────────────────────────────────────────────────
_SUB_MAP = {
    "dining out":              "Dining Out",
    "lunching out":            "Dining Out",
    "new clothes":             "Clothing",
    "college loans":           "Education/Tuition",
    "tuition":                 "Education/Tuition",
    "travel/ vacation":        "Travel",
    "vision/contacts":         "Vision",
    "fisiotherapy":            "Physical Therapy",
    "emi":                     "Mortgage/EMI",
    "phone - home":            "Phone-Home",
    "phone - cell":            "Phone-Cell",
    "credit card":             "Credit Card Payment",
    "bill's train pass":       "Transit Pass",
    "jane's bus pass":         "Transit Pass",
    "registration/inspection": "Registration",
    "home tools":              "Household Supplies",
    "home related":            "Other",
}

def _norm_sub(label: str) -> str:
    return _SUB_MAP.get(label.strip().lower(), label.strip().title())


# ── Income from Tracking sheet ────────────────────────────────────────────────

def _parse_income(file_path: str, year: int, month: int, source_tag: str) -> list[Transaction]:
    """Read income rows from the Tracking sheet for the given month."""
    try:
        df = pd.read_excel(file_path, sheet_name=TRACKING_SHEET, header=None)
    except Exception as e:
        print(f"Warning: could not read Tracking sheet — {e}")
        return []

    # month → column index: Jan=3, Feb=4, ..., Dec=14
    col = TRACKING_MONTH_COL_JAN + (month - 1)
    income_date = date(year, month, 1)   # post income on the 1st of the month

    transactions = []
    for row_idx, (category, subcategory) in INCOME_ROWS.items():
        if row_idx >= len(df):
            continue
        raw = df.iloc[row_idx, col]
        if pd.isna(raw):
            continue
        try:
            amount = float(raw)
        except (ValueError, TypeError):
            continue
        if amount == 0:
            continue

        label = subcategory
        transactions.append(
            Transaction(
                date=income_date,
                merchant=label,
                raw_desc=label,
                category="Income",
                subcategory=subcategory,
                amount=amount,
                source_file=source_tag,
                is_reviewed=True,
            )
        )

    return transactions


# ── Expense parsing from monthly sheet ───────────────────────────────────────

def _parse_expenses(file_path: str, sheet: str, year: int, month: int, source_tag: str) -> list[Transaction]:
    df = pd.read_excel(file_path, sheet_name=sheet, header=None)

    transactions: list[Transaction] = []
    current_category: str | None = None
    current_subcategory: str | None = None

    for _, row in df.iterrows():
        cat_val = row.iloc[CAT_COL]
        sub_val = row.iloc[SUB_COL]

        if pd.notna(cat_val) and pd.isna(sub_val):
            current_category = str(cat_val).strip()
            current_subcategory = None
            continue

        if pd.isna(cat_val) and pd.notna(sub_val):
            current_subcategory = _norm_sub(str(sub_val))
            if current_category is None:
                continue

            for col_offset, day in enumerate(range(1, 32)):
                col_idx = DAY_COL_START + col_offset
                if col_idx > DAY_COL_END or col_idx >= len(row):
                    break

                raw = row.iloc[col_idx]
                if pd.isna(raw):
                    continue
                try:
                    amount = float(raw)
                except (ValueError, TypeError):
                    continue
                if amount == 0:
                    continue

                try:
                    tx_date = date(year, month, day)
                except ValueError:
                    continue

                transactions.append(
                    Transaction(
                        date=tx_date,
                        merchant=current_subcategory,
                        raw_desc=current_subcategory,
                        category=current_category,
                        subcategory=current_subcategory,
                        amount=amount,
                        source_file=source_tag,
                        is_reviewed=True,
                    )
                )

    return transactions


# ── Main migrate function ─────────────────────────────────────────────────────

def migrate(file_path: str, sheet: str, year: int, month: int, dry_run: bool = False) -> int:
    Base.metadata.create_all(bind=engine)
    source_tag = f"{sheet} {year} (Excel import)"

    expenses = _parse_expenses(file_path, sheet, year, month, source_tag)
    income   = _parse_income(file_path, year, month, source_tag)
    all_txs  = expenses + income

    if not all_txs:
        print("No transactions found — check sheet name / layout.")
        return 0

    if dry_run:
        print(f"[DRY RUN] Would import {len(all_txs)} transactions "
              f"({len(expenses)} expenses + {len(income)} income):")
        for t in all_txs[:15]:
            print(f"  {t.date}  {t.category:15s}  {t.subcategory:25s}  ${t.amount:,.2f}")
        if len(all_txs) > 15:
            print(f"  ... and {len(all_txs) - 15} more")
        return len(all_txs)

    saved = skipped = 0
    with Session(engine) as db:
        for t in all_txs:
            exists = (
                db.query(Transaction)
                .filter(
                    Transaction.date == t.date,
                    Transaction.amount == t.amount,
                    Transaction.raw_desc == t.raw_desc,
                )
                .first()
            )
            if exists:
                skipped += 1
                continue
            db.add(t)
            saved += 1
        db.commit()

    print(f"Imported {saved} transactions ({len(expenses)} expenses + {len(income)} income), "
          f"skipped {skipped} duplicates.")
    return saved


def main():
    parser = argparse.ArgumentParser(description="Migrate Excel budget sheet to SpendSense DB")
    parser.add_argument("--file",    required=True, help="Path to the .xlsx file")
    parser.add_argument("--sheet",   default="January", help="Sheet name (default: January)")
    parser.add_argument("--year",    type=int, required=True, help="Year, e.g. 2025")
    parser.add_argument("--month",   type=int, required=True, help="Month number, e.g. 1")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()

    count = migrate(
        file_path=args.file,
        sheet=args.sheet,
        year=args.year,
        month=args.month,
        dry_run=args.dry_run,
    )
    sys.exit(0 if count >= 0 else 1)


if __name__ == "__main__":
    main()

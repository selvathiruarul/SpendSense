"""
SpendSense FastAPI Backend
Run with: uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from backend.database import Base, engine, get_db
from backend.models import Transaction
from backend.parser import parse_file
from backend.ai_engine import categorize_all

load_dotenv()

# Create database tables on startup
Base.metadata.create_all(bind=engine)

# Migration: add subcategory column if it doesn't exist yet
from sqlalchemy import text as _text
with engine.connect() as _conn:
    try:
        _conn.execute(_text("ALTER TABLE transactions ADD COLUMN subcategory VARCHAR"))
        _conn.commit()
    except Exception:
        pass  # column already exists

app = FastAPI(title="SpendSense API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],  # Streamlit default port
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request/Response schemas ──────────────────────────────────────────────────

class TransactionUpdate(BaseModel):
    category: Optional[str] = None
    subcategory: Optional[str] = None
    merchant: Optional[str] = None
    is_reviewed: Optional[bool] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "service": "SpendSense API", "version": "2.0.0"}


@app.post("/upload")
async def upload_statement(
    file: UploadFile = File(...),
    account_type: str = Form("credit_card"),  # "credit_card" | "checking" | "savings"
    db: Session = Depends(get_db),
):
    """
    Upload a bank statement (PDF or CSV).
    account_type controls whether credits can be Income:
      - credit_card: credits are Refund (never Income)
      - checking/savings: credits matching payroll patterns are Income
    Pipeline: parse -> AI categorize -> save to SQLite.
    """
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()

    if ext not in (".pdf", ".csv"):
        raise HTTPException(status_code=400, detail="Only PDF and CSV files are supported.")

    # Write upload to a temp file (pdfplumber/pandas need a file path)
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        raw_txs = parse_file(tmp_path)
    finally:
        os.unlink(tmp_path)

    if not raw_txs:
        raise HTTPException(
            status_code=422,
            detail=(
                "No transactions could be parsed from this file. "
                "Try POST /debug-parse to inspect what the parser extracted."
            ),
        )

    # AI categorization (batched, falls back to 'Other' on failure)
    categorized = categorize_all(raw_txs, account_type=account_type)

    # Persist to DB — skip exact duplicates (same date + amount + description)
    saved_count = 0
    skipped_count = 0
    for tx in categorized:
        tx_date = datetime.strptime(tx["date"], "%Y-%m-%d").date()
        exists = (
            db.query(Transaction)
            .filter(
                Transaction.date == tx_date,
                Transaction.amount == tx["amount"],
                Transaction.raw_desc == tx["raw_desc"],
            )
            .first()
        )
        if exists:
            skipped_count += 1
            continue

        db_tx = Transaction(
            date=tx_date,
            merchant=_clean_merchant(tx["raw_desc"]),
            raw_desc=tx["raw_desc"],
            category=tx.get("category", "Other"),
            subcategory=tx.get("subcategory"),
            amount=tx["amount"],
            source_file=filename,
            is_reviewed=False,
        )
        db.add(db_tx)
        saved_count += 1

    db.commit()
    return {"imported": saved_count, "skipped_duplicates": skipped_count, "file": filename}


@app.get("/transactions")
def list_transactions(
    skip: int = 0,
    limit: int = 500,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Return transactions, optionally filtered by category."""
    q = db.query(Transaction)
    if category:
        q = q.filter(Transaction.category == category)
    txs = q.order_by(Transaction.date.desc()).offset(skip).limit(limit).all()
    return [t.to_dict() for t in txs]


@app.patch("/transactions/{tx_id}")
def update_transaction(
    tx_id: int,
    data: TransactionUpdate,
    db: Session = Depends(get_db),
):
    """Update category, merchant, or is_reviewed flag on a transaction."""
    tx = db.query(Transaction).filter(Transaction.id == tx_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    if data.category is not None:
        tx.category = data.category
    if data.subcategory is not None:
        tx.subcategory = data.subcategory
    if data.merchant is not None:
        tx.merchant = data.merchant
    if data.is_reviewed is not None:
        tx.is_reviewed = data.is_reviewed

    db.commit()
    db.refresh(tx)
    return tx.to_dict()


@app.get("/summary")
def get_summary(db: Session = Depends(get_db)):
    """Spending summary grouped by category, plus totals."""
    txs = db.query(Transaction).all()

    EXPENSE_CATEGORIES = {"Transportation", "Home", "Utilities", "Health", "Entertainment", "Miscellaneous"}

    by_category: dict[str, float] = {}
    by_subcategory: dict[str, dict[str, float]] = {}
    total_spent = 0.0
    total_income = 0.0

    for tx in txs:
        cat = tx.category or "Other"
        sub = tx.subcategory or "Other"
        by_category[cat] = round(by_category.get(cat, 0.0) + tx.amount, 2)
        if cat not in by_subcategory:
            by_subcategory[cat] = {}
        by_subcategory[cat][sub] = round(by_subcategory[cat].get(sub, 0.0) + tx.amount, 2)
        # Use category (not sign) to determine expense vs income
        if cat in EXPENSE_CATEGORIES:
            total_spent += abs(tx.amount)
        elif cat == "Income":
            total_income += abs(tx.amount)

    savings_rate = (
        round((total_income - total_spent) / total_income * 100, 1)
        if total_income > 0
        else 0.0
    )

    return {
        "by_category": by_category,
        "by_subcategory": by_subcategory,
        "total_transactions": len(txs),
        "total_spent": round(total_spent, 2),
        "total_income": round(total_income, 2),
        "savings_rate_pct": savings_rate,
    }


@app.get("/monthly")
def get_monthly(db: Session = Depends(get_db)):
    """Monthly spending totals (expense categories only)."""
    EXPENSE_CATEGORIES = {"Transportation", "Home", "Utilities", "Health", "Entertainment", "Miscellaneous"}
    txs = db.query(Transaction).filter(Transaction.category.in_(EXPENSE_CATEGORIES)).all()

    monthly: dict[str, float] = {}
    for tx in txs:
        key = tx.date.strftime("%Y-%m")
        monthly[key] = round(monthly.get(key, 0.0) + abs(tx.amount), 2)

    return {"monthly": dict(sorted(monthly.items()))}


@app.delete("/transactions", status_code=204)
def clear_transactions(db: Session = Depends(get_db)):
    """Delete all transactions. Useful for testing."""
    db.query(Transaction).delete()
    db.commit()


@app.post("/debug-parse")
async def debug_parse(file: UploadFile = File(...)):
    """
    Dry-run parse — returns what the parser extracted WITHOUT saving to DB.
    Use this to diagnose why a file yields no transactions.
    Also returns raw page text so you can see the PDF layout.
    """
    import pdfplumber

    filename = file.filename or ""
    ext = Path(filename).suffix.lower()

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        parsed = parse_file(tmp_path)

        # For PDFs, also return raw page text to help debug layout
        raw_pages = []
        if ext == ".pdf":
            with pdfplumber.open(tmp_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    clean = page.dedupe_chars(tolerance=1)
                    raw_pages.append({
                        "page": i + 1,
                        "text_preview": (clean.extract_text() or "")[:600],
                        "tables_found": len(clean.extract_tables()),
                    })
    finally:
        os.unlink(tmp_path)

    return {
        "transactions_found": len(parsed),
        "first_5": parsed[:5],
        "raw_pages": raw_pages,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean_merchant(raw_desc: str) -> str:
    """Strip bank reference codes to get a readable merchant name."""
    clean = re.sub(r"\*[A-Z0-9]+$", "", raw_desc).strip()
    clean = re.sub(r"\s+\d{5,}$", "", clean).strip()
    return clean[:100]

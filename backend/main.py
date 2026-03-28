"""
SpendSense FastAPI Backend
Run with: uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends
from sqlalchemy import func as _func
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from backend.database import Base, engine, get_db
from backend.models import Transaction, MerchantRule, BudgetTarget
from backend.parser import parse_file
from backend.ai_engine import categorize_all
from backend.auth import UserClaims, get_current_user
from backend.billing import router as billing_router

load_dotenv()

# Free tier limits
FREE_UPLOADS_PER_MONTH = int(os.getenv("FREE_UPLOADS_PER_MONTH", "3"))
FREE_TRANSACTIONS_MAX = int(os.getenv("FREE_TRANSACTIONS_MAX", "100"))

# Create database tables on startup
Base.metadata.create_all(bind=engine)

# Migrations: add columns introduced after initial schema (SQLite-safe)
from sqlalchemy import text as _text
with engine.connect() as _conn:
    for _col_ddl in [
        "ALTER TABLE transactions ADD COLUMN subcategory VARCHAR",
        "ALTER TABLE transactions ADD COLUMN account VARCHAR",
        "ALTER TABLE transactions ADD COLUMN notes VARCHAR",
        "ALTER TABLE transactions ADD COLUMN user_id VARCHAR",
        "ALTER TABLE budget_targets ADD COLUMN user_id VARCHAR",
        "ALTER TABLE merchant_rules ADD COLUMN user_id VARCHAR",
    ]:
        try:
            _conn.execute(_text(_col_ddl))
            _conn.commit()
        except Exception:
            pass  # column already exists

    # Migrate budget_targets: if old schema (monthly_amount column) exists, drop and recreate
    try:
        _conn.execute(_text("SELECT monthly_amount FROM budget_targets LIMIT 1"))
        _conn.execute(_text("DROP TABLE budget_targets"))
        _conn.commit()
        Base.metadata.tables["budget_targets"].create(bind=engine)
    except Exception:
        pass

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8501")

app = FastAPI(title="SpendSense API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register billing routes (Stripe checkout / portal / webhook)
app.include_router(billing_router)


# ── Request/Response schemas ──────────────────────────────────────────────────

class TransactionUpdate(BaseModel):
    category: Optional[str] = None
    subcategory: Optional[str] = None
    merchant: Optional[str] = None
    account: Optional[str] = None
    amount: Optional[float] = None
    notes: Optional[str] = None
    is_reviewed: Optional[bool] = None


class SplitItem(BaseModel):
    amount: float
    merchant: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None


class TransactionCreate(BaseModel):
    date: str                          # ISO: "2026-03-15"
    merchant: str
    amount: float
    category: Optional[str] = None
    subcategory: Optional[str] = None
    account: Optional[str] = None
    note: Optional[str] = None        # stored as raw_desc


class BudgetCreate(BaseModel):
    category: str       # expense category name or "Savings"
    percentage: float   # 0–100 (% of monthly income)


# ── Usage limit helper ────────────────────────────────────────────────────────

def _check_upload_limit(user: UserClaims, db: Session) -> None:
    """Raise 402 if a free-tier user has hit their monthly upload limit."""
    if user.is_paid:
        return
    from datetime import date as _date
    today = _date.today()
    month_start = _date(today.year, today.month, 1)
    uploads_this_month = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user.id,
            Transaction.source_file != "manual",
            Transaction.date >= month_start,
        )
        .with_entities(Transaction.source_file)
        .distinct()
        .count()
    )
    if uploads_this_month >= FREE_UPLOADS_PER_MONTH:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Free plan allows {FREE_UPLOADS_PER_MONTH} uploads per month. "
                "Upgrade to Pro for unlimited uploads."
            ),
        )


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "service": "SpendSense API", "version": "3.0.0"}


@app.post("/upload")
async def upload_statement(
    file: UploadFile = File(...),
    account_type: str = Form("credit_card"),  # "credit_card" | "checking" | "savings"
    account: str = Form(""),                  # human label e.g. "Chase Checking"
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    """
    Upload a bank statement (PDF or CSV).
    account_type controls whether credits can be Income:
      - credit_card: credits are Refund (never Income)
      - checking/savings: credits matching payroll patterns are Income
    Pipeline: parse -> AI categorize -> save to DB.
    """
    _check_upload_limit(current_user, db)

    filename = file.filename or ""
    ext = Path(filename).suffix.lower()

    if ext not in (".pdf", ".csv"):
        raise HTTPException(status_code=400, detail="Only PDF and CSV files are supported.")

    content = await file.read()
    if len(content) > 10_000_000:
        raise HTTPException(status_code=413, detail="File exceeds 10 MB limit.")

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(content)
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

    # Load this user's learned merchant rules
    rules = {
        r.merchant.lower(): r
        for r in db.query(MerchantRule).filter(MerchantRule.user_id == current_user.id).all()
    }
    for tx in raw_txs:
        merchant_key = _clean_merchant(tx["raw_desc"]).lower()
        matched_rule = _fuzzy_find_rule(merchant_key, rules)
        if matched_rule:
            tx["category"] = matched_rule.category
            tx["subcategory"] = matched_rule.subcategory
            tx["_from_rule"] = True

    categorized = categorize_all(raw_txs, account_type=account_type)

    saved_count = 0
    skipped_count = 0
    for tx in categorized:
        tx_date = datetime.strptime(tx["date"], "%Y-%m-%d").date()
        exists = (
            db.query(Transaction)
            .filter(
                Transaction.user_id == current_user.id,
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
            user_id=current_user.id,
            date=tx_date,
            merchant=_clean_merchant(tx["raw_desc"]),
            raw_desc=tx["raw_desc"],
            category=tx.get("category", "Other"),
            subcategory=tx.get("subcategory"),
            amount=tx["amount"],
            source_file=filename,
            account=account or None,
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
    current_user: UserClaims = Depends(get_current_user),
):
    q = db.query(Transaction).filter(Transaction.user_id == current_user.id)
    if category:
        q = q.filter(Transaction.category == category)
    txs = q.order_by(Transaction.date.asc()).offset(skip).limit(limit).all()
    return [t.to_dict() for t in txs]


@app.patch("/transactions/bulk-account")
def bulk_assign_account(
    account: str,
    category: Optional[str] = None,
    merchant: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    q = db.query(Transaction).filter(Transaction.user_id == current_user.id)
    if category:
        q = q.filter(Transaction.category == category)
    if merchant:
        q = q.filter(_func.lower(Transaction.merchant) == merchant.lower())
    count = q.count()
    q.update({"account": account}, synchronize_session=False)
    db.commit()
    return {"updated": count}


@app.patch("/transactions/{tx_id}")
def update_transaction(
    tx_id: int,
    data: TransactionUpdate,
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    tx = (
        db.query(Transaction)
        .filter(Transaction.id == tx_id, Transaction.user_id == current_user.id)
        .first()
    )
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    if data.category is not None:
        tx.category = data.category
    if data.subcategory is not None:
        tx.subcategory = data.subcategory
    if data.merchant is not None:
        tx.merchant = data.merchant
    if data.account is not None:
        tx.account = data.account
    if data.amount is not None:
        tx.amount = data.amount
    if data.notes is not None:
        tx.notes = data.notes
    if data.is_reviewed is not None:
        tx.is_reviewed = data.is_reviewed

    if data.is_reviewed and tx.category:
        merchant_key = tx.merchant.lower()
        rule = (
            db.query(MerchantRule)
            .filter(MerchantRule.user_id == current_user.id, MerchantRule.merchant == merchant_key)
            .first()
        )
        final_cat = tx.category
        final_sub = tx.subcategory
        if rule:
            rule.category = final_cat
            rule.subcategory = final_sub
        else:
            db.add(MerchantRule(
                user_id=current_user.id,
                merchant=merchant_key,
                category=final_cat,
                subcategory=final_sub,
            ))

        all_unreviewed = (
            db.query(Transaction)
            .filter(
                Transaction.user_id == current_user.id,
                Transaction.is_reviewed == False,
                Transaction.id != tx_id,
            )
            .all()
        )
        for s in all_unreviewed:
            if _fuzzy_match(s.merchant.lower(), merchant_key):
                s.category = final_cat
                s.subcategory = final_sub

    db.commit()
    db.refresh(tx)
    return tx.to_dict()


@app.post("/transactions/{tx_id}/split")
def split_transaction(
    tx_id: int,
    splits: list[SplitItem],
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    tx = (
        db.query(Transaction)
        .filter(Transaction.id == tx_id, Transaction.user_id == current_user.id)
        .first()
    )
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    if len(splits) < 2:
        raise HTTPException(status_code=400, detail="Provide at least 2 splits.")

    new_txs = []
    for s in splits:
        new_txs.append(Transaction(
            user_id=current_user.id,
            date=tx.date,
            merchant=s.merchant or tx.merchant,
            raw_desc=tx.raw_desc,
            category=s.category or tx.category,
            subcategory=s.subcategory or tx.subcategory,
            amount=s.amount,
            source_file=tx.source_file,
            account=tx.account,
            is_reviewed=tx.is_reviewed,
        ))

    db.delete(tx)
    for t in new_txs:
        db.add(t)
    db.commit()
    return {"split_into": len(new_txs)}


@app.post("/transactions", status_code=201)
def create_transaction(
    data: TransactionCreate,
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    try:
        tx_date = datetime.strptime(data.date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    tx = Transaction(
        user_id=current_user.id,
        date=tx_date,
        merchant=data.merchant.strip(),
        raw_desc=data.merchant.strip(),
        category=data.category,
        subcategory=data.subcategory,
        amount=data.amount,
        source_file="manual",
        account=data.account or None,
        notes=data.note or None,
        is_reviewed=True,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx.to_dict()


@app.get("/export")
def export_csv(
    year: Optional[int] = None,
    month: Optional[int] = None,
    account: Optional[str] = None,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    import calendar
    import csv
    import io
    from datetime import date as _date
    from fastapi.responses import StreamingResponse

    q = db.query(Transaction).filter(Transaction.user_id == current_user.id)
    if year and month:
        q = q.filter(
            Transaction.date >= _date(year, month, 1),
            Transaction.date <= _date(year, month, calendar.monthrange(year, month)[1]),
        )
    elif year:
        q = q.filter(Transaction.date >= _date(year, 1, 1), Transaction.date <= _date(year, 12, 31))
    if account:
        q = q.filter(Transaction.account == account)
    if category:
        q = q.filter(Transaction.category == category)
    txs = q.order_by(Transaction.date.asc()).all()

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["id", "date", "merchant", "raw_desc", "category", "subcategory", "amount", "account", "notes", "is_reviewed"])
    writer.writeheader()
    for tx in txs:
        writer.writerow({
            "id": tx.id, "date": tx.date.isoformat(), "merchant": tx.merchant,
            "raw_desc": tx.raw_desc, "category": tx.category, "subcategory": tx.subcategory,
            "amount": tx.amount, "account": tx.account, "notes": tx.notes, "is_reviewed": tx.is_reviewed,
        })
    buf.seek(0)
    filename = f"spendsense_{'_'.join(str(x) for x in [year, month, account] if x) or 'all'}.csv"
    return StreamingResponse(buf, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.get("/budgets")
def list_budgets(
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    return [
        b.to_dict()
        for b in db.query(BudgetTarget)
        .filter(BudgetTarget.user_id == current_user.id)
        .order_by(BudgetTarget.category)
        .all()
    ]


@app.post("/budgets", status_code=201)
def upsert_budget(
    data: BudgetCreate,
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    existing = (
        db.query(BudgetTarget)
        .filter(BudgetTarget.user_id == current_user.id, BudgetTarget.category == data.category)
        .first()
    )
    if existing:
        existing.percentage = data.percentage
        db.commit()
        db.refresh(existing)
        return existing.to_dict()
    b = BudgetTarget(user_id=current_user.id, category=data.category, percentage=data.percentage)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b.to_dict()


@app.delete("/budgets/{budget_id}", status_code=204)
def delete_budget(
    budget_id: int,
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    b = (
        db.query(BudgetTarget)
        .filter(BudgetTarget.id == budget_id, BudgetTarget.user_id == current_user.id)
        .first()
    )
    if not b:
        raise HTTPException(status_code=404, detail="Budget not found.")
    db.delete(b)
    db.commit()


@app.get("/summary")
def get_summary(
    year: Optional[int] = None,
    month: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    import calendar
    from datetime import date as _date
    q = db.query(Transaction).filter(Transaction.user_id == current_user.id)
    if year and month:
        q = q.filter(
            Transaction.date >= _date(year, month, 1),
            Transaction.date <= _date(year, month, calendar.monthrange(year, month)[1]),
        )
    elif year:
        q = q.filter(
            Transaction.date >= _date(year, 1, 1),
            Transaction.date <= _date(year, 12, 31),
        )
    txs = q.all()

    EXPENSE_CATEGORIES = {"Transportation", "Home", "Utilities", "Health", "Entertainment", "Miscellaneous"}

    by_category: dict[str, float] = {}
    by_subcategory: dict[str, dict[str, float]] = {}
    total_spent = 0.0
    total_income = 0.0

    for tx in txs:
        cat = tx.category or "Other"
        sub = tx.subcategory or "Other"
        by_category[cat] = round(by_category.get(cat, 0.0) + abs(tx.amount), 2)
        if cat not in by_subcategory:
            by_subcategory[cat] = {}
        by_subcategory[cat][sub] = round(by_subcategory[cat].get(sub, 0.0) + abs(tx.amount), 2)
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
def get_monthly(
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    EXPENSE_CATEGORIES = {"Transportation", "Home", "Utilities", "Health", "Entertainment", "Miscellaneous"}
    txs = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == current_user.id,
            Transaction.category.in_(EXPENSE_CATEGORIES),
        )
        .all()
    )

    monthly: dict[str, float] = {}
    for tx in txs:
        key = tx.date.strftime("%Y-%m")
        monthly[key] = round(monthly.get(key, 0.0) + abs(tx.amount), 2)

    return {"monthly": dict(sorted(monthly.items()))}


@app.get("/recurring")
def get_recurring(
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    from collections import defaultdict

    EXCLUDE = {"Income", "Payment", "Refund", "Investment"}
    txs = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == current_user.id,
            Transaction.category.notin_(EXCLUDE),
        )
        .all()
    )

    by_merchant: dict[str, list] = defaultdict(list)
    for tx in txs:
        key = (tx.merchant or tx.raw_desc or "").lower().strip()
        if not key:
            continue
        by_merchant[key].append({
            "month": tx.date.strftime("%Y-%m"),
            "amount": abs(tx.amount),
            "merchant": tx.merchant,
            "category": tx.category,
            "subcategory": tx.subcategory,
        })

    recurring = []
    for _key, entries in by_merchant.items():
        months = {e["month"] for e in entries}
        if len(months) < 2:
            continue
        amounts = [e["amount"] for e in entries]
        avg_amount = sum(amounts) / len(amounts)
        if max(amounts) - min(amounts) < max(avg_amount * 0.10, 5.0):
            recurring.append({
                "merchant": entries[0]["merchant"],
                "category": entries[0]["category"],
                "subcategory": entries[0]["subcategory"],
                "months_seen": sorted(months),
                "occurrences": len(entries),
                "avg_amount": round(avg_amount, 2),
                "total_spent": round(sum(amounts), 2),
            })

    return sorted(recurring, key=lambda x: x["avg_amount"], reverse=True)


@app.get("/income")
def get_income(
    year: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    from datetime import date as _date

    q = db.query(Transaction).filter(
        Transaction.user_id == current_user.id,
        Transaction.category == "Income",
    )
    if year:
        q = q.filter(Transaction.date >= _date(year, 1, 1), Transaction.date <= _date(year, 12, 31))
    txs = q.order_by(Transaction.date.asc()).all()

    by_month: dict[str, float] = {}
    by_source: dict[str, float] = {}
    for tx in txs:
        month_key = tx.date.strftime("%Y-%m")
        sub = tx.subcategory or "Other"
        by_month[month_key] = round(by_month.get(month_key, 0) + abs(tx.amount), 2)
        by_source[sub] = round(by_source.get(sub, 0) + abs(tx.amount), 2)

    return {
        "by_month": dict(sorted(by_month.items())),
        "by_source": by_source,
        "total": round(sum(by_month.values()), 2),
        "transactions": [t.to_dict() for t in txs],
    }


@app.get("/budget-trend")
def get_budget_trend(
    year: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    import calendar
    from datetime import date as _date

    if not year:
        year = _date.today().year

    budgets = (
        db.query(BudgetTarget)
        .filter(BudgetTarget.user_id == current_user.id)
        .all()
    )
    if not budgets:
        return {"months": [], "categories": []}

    budget_map = {b.category: b.percentage for b in budgets}

    EXPENSE_CATEGORIES = {"Transportation", "Home", "Utilities", "Health", "Entertainment", "Miscellaneous"}

    txs = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == current_user.id,
            Transaction.date >= _date(year, 1, 1),
            Transaction.date <= _date(year, 12, 31),
        )
        .all()
    )

    months_data: dict[str, dict] = {}
    for tx in txs:
        key = tx.date.strftime("%Y-%m")
        if key not in months_data:
            months_data[key] = {"income": 0.0, "by_cat": {}}
        if tx.category == "Income":
            months_data[key]["income"] += abs(tx.amount)
        elif tx.category in EXPENSE_CATEGORIES:
            months_data[key]["by_cat"][tx.category] = (
                months_data[key]["by_cat"].get(tx.category, 0.0) + abs(tx.amount)
            )

    result = []
    for month_key, data in sorted(months_data.items()):
        income = data["income"]
        if income == 0:
            continue
        total_expense = sum(data["by_cat"].values())
        row: dict = {"month": month_key, "income": round(income, 2)}
        for cat, pct in budget_map.items():
            target = round(income * pct / 100, 2)
            if cat == "Savings":
                actual = round(income - total_expense, 2)
            else:
                actual = round(data["by_cat"].get(cat, 0.0), 2)
            row[f"{cat}_target"] = target
            row[f"{cat}_actual"] = actual
        result.append(row)

    return {"months": result, "categories": list(budget_map.keys())}


@app.get("/rules")
def list_rules(
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    rules = (
        db.query(MerchantRule)
        .filter(MerchantRule.user_id == current_user.id)
        .order_by(MerchantRule.merchant)
        .all()
    )
    return [r.to_dict() for r in rules]


@app.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    rule = (
        db.query(MerchantRule)
        .filter(MerchantRule.id == rule_id, MerchantRule.user_id == current_user.id)
        .first()
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found.")
    db.delete(rule)
    db.commit()


@app.delete("/transactions/{tx_id}", status_code=204)
def delete_transaction(
    tx_id: int,
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    tx = (
        db.query(Transaction)
        .filter(Transaction.id == tx_id, Transaction.user_id == current_user.id)
        .first()
    )
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    db.delete(tx)
    db.commit()


@app.delete("/transactions", status_code=200)
def clear_transactions(
    year: Optional[int] = None,
    month: Optional[int] = None,
    account: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: UserClaims = Depends(get_current_user),
):
    import calendar
    from datetime import date as _date
    q = db.query(Transaction).filter(Transaction.user_id == current_user.id)
    if year and month:
        q = q.filter(
            Transaction.date >= _date(year, month, 1),
            Transaction.date <= _date(year, month, calendar.monthrange(year, month)[1]),
        )
    elif year:
        q = q.filter(
            Transaction.date >= _date(year, 1, 1),
            Transaction.date <= _date(year, 12, 31),
        )
    if account:
        q = q.filter(Transaction.account == account)
    count = q.count()
    q.delete(synchronize_session=False)
    db.commit()
    return {"deleted": count}


@app.post("/debug-parse")
async def debug_parse(
    file: UploadFile = File(...),
    current_user: UserClaims = Depends(get_current_user),
):
    import pdfplumber

    filename = file.filename or ""
    ext = Path(filename).suffix.lower()

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        parsed = parse_file(tmp_path)

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
    clean = raw_desc.strip()
    clean = re.sub(r"\s+(PPD|WEB|CCD)\b.*$", "", clean, flags=re.IGNORECASE).strip()
    clean = re.sub(r"\s+#\d+", "", clean).strip()
    clean = re.sub(r"\*\S+$", "", clean).strip()
    clean = re.sub(r"\s+\S*[/.]\S+", "", clean).strip()
    clean = re.sub(r"\s+[A-Z0-9]{10,}\b", "", clean).strip()
    clean = re.sub(r"\s+\d{8,}", "", clean).strip()
    clean = re.sub(r"\s+\d{5,7}$", "", clean).strip()
    clean = re.sub(r"\s+[A-Z][A-Z ]{1,20}\s+[A-Z]{2}$", "", clean).strip()
    clean = re.sub(r"\s+[A-Z]{2}$", "", clean).strip()
    clean = re.sub(r"\s+\d{3,4}$", "", clean).strip()
    return clean[:60]


_FUZZY_THRESHOLD = 0.82


def _fuzzy_match(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b).ratio() >= _FUZZY_THRESHOLD


def _fuzzy_find_rule(merchant_key: str, rules: dict) -> Optional[MerchantRule]:
    if merchant_key in rules:
        return rules[merchant_key]
    best_score, best_rule = 0.0, None
    for key, rule in rules.items():
        score = SequenceMatcher(None, merchant_key, key).ratio()
        if score > best_score:
            best_score, best_rule = score, rule
    return best_rule if best_score >= _FUZZY_THRESHOLD else None

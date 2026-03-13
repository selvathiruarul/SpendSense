"""
AI Categorization Engine using Ollama (local LLM).

Returns both a parent category and subcategory for each transaction.
Pre-classifies obvious payments and income by regex to skip Ollama.
"""
from __future__ import annotations

import json
import os
import re
import ollama
from dotenv import load_dotenv

load_dotenv()

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

# ── Category / subcategory taxonomy ──────────────────────────────────────────

TAXONOMY: dict[str, list[str]] = {
    "Transportation": [
        "Auto Loan/Lease", "Gas", "Insurance", "Maintenance",
        "Registration", "Transit Pass", "Rental/Taxi", "Other",
    ],
    "Home": [
        "Mortgage/EMI", "Rent", "Maintenance", "Insurance",
        "Furniture", "Household Supplies", "Groceries",
        "Real Estate Tax", "City Utilities", "Other",
    ],
    "Utilities": [
        "Phone-Home", "Phone-Cell", "Cable", "Gas",
        "Water", "Electricity", "Internet", "Laundry", "Other",
    ],
    "Health": [
        "Dental", "Medical", "Medication", "Vision",
        "Life Insurance", "Physical Therapy", "Other",
    ],
    "Entertainment": [
        "Memberships", "Dining Out", "Subscriptions",
        "Movies", "Music", "Hobbies", "Travel", "Events", "Other",
    ],
    "Miscellaneous": [
        "Dry Cleaning", "Clothing", "Donations", "Child Care",
        "Education/Tuition", "Personal Care", "Gifts",
        "Online Purchase", "Other",
    ],
    # Non-expense / special
    "Income": ["Salary", "Tax Refund", "Other"],
    "Refund": ["Return", "Credit", "Other"],
    "Payment": ["Credit Card Payment", "Other"],
}

# Flat list of all parent categories for validation
CATEGORIES = list(TAXONOMY.keys())

# ── Pre-classification regex patterns ─────────────────────────────────────────

_PAYMENT_PATTERNS = re.compile(
    r"(payment\s+thank\s+you|automatic\s+payment|autopay|"
    r"online\s+payment|mobile\s+payment|bill\s+payment|"
    r"balance\s+transfer|account\s+transfer|transfer\s+to|"
    r"thank\s+you[-\s]mobile|thank\s+you[-\s]online)",
    re.IGNORECASE,
)

_INCOME_PATTERNS = re.compile(
    r"(direct\s+deposit|payroll|salary|paycheck|"
    r"tax\s+refund|irs\s+treas|zelle\s+from|venmo\s+from|"
    r"t-osv|moneyline|claim\s+reim|reimbursement|employer|"
    r"ppd\s+id|web\s+id.*deposit)",
    re.IGNORECASE,
)

# ── Prompt building ───────────────────────────────────────────────────────────

def _build_system_prompt(is_cc: bool) -> str:
    taxonomy_lines = []
    skip = {"Income", "Refund", "Payment"}
    for cat, subs in TAXONOMY.items():
        if cat in skip:
            continue
        taxonomy_lines.append(f"  {cat}: {', '.join(subs)}")
    taxonomy_str = "\n".join(taxonomy_lines)

    if is_cc:
        tag_legend = "Each transaction is tagged [CHARGE] (purchase) or [CREDIT] (refund/return)."
        sign_rules = (
            "- [CREDIT] transactions (money returned to card) → Refund / Credit\n"
            "- NEVER use Income for credit card transactions\n"
        )
    else:
        tag_legend = "Each transaction is tagged [DEPOSIT] (money in) or [WITHDRAWAL] (money out)."
        sign_rules = (
            "- [DEPOSIT] transactions → likely Income (Salary, reimbursement, transfer in)\n"
            "- [WITHDRAWAL] transactions → expense categories\n"
            "- Employer payroll, direct deposit, reimbursements → Income / Salary\n"
            "- Investment transfers (Robinhood, Fidelity) → Miscellaneous / Other\n"
            "- Loan payments, mortgage → Home / Mortgage/EMI or Transportation / Auto Loan/Lease\n"
        )

    return f"""You are a professional accountant categorizing bank transactions.
{tag_legend}

Return a JSON array where each object has:
  "index" (1-based integer), "category" (parent), "subcategory" (specific)

Taxonomy (category: subcategories):
{taxonomy_str}
  Refund: Return, Credit, Other

Rules:
- Grocery stores, supermarkets, Walmart, Target (for groceries) → Home / Groceries
- Restaurants, cafes, fast food, dining → Entertainment / Dining Out
- Gas stations → Transportation / Gas
- Rideshare (Uber, Lyft), taxis → Transportation / Rental/Taxi
- Electric/water/gas utility companies → Utilities / (Electricity|Water|Gas)
- Internet, cable, streaming services → Utilities / Internet  OR  Entertainment / Subscriptions
- Phone bills → Utilities / Phone-Cell
- Apple subscriptions, Spotify, Netflix → Entertainment / Subscriptions
- Gym, fitness → Entertainment / Memberships
- School district, tuition, university → Miscellaneous / Education/Tuition
- Medical, pharmacy, dental → Health / (Medical|Dental|Medication)
- Amazon, online stores (general) → Miscellaneous / Online Purchase
{sign_rules}
- If unsure → Miscellaneous / Other

Respond ONLY with a valid JSON array. No prose, no markdown fences."""


# ── Public API ────────────────────────────────────────────────────────────────

def categorize_all(
    transactions: list[dict],
    batch_size: int = 5,
    account_type: str = "credit_card",
) -> list[dict]:
    """
    Categorize all transactions (in-place).
    Step 1: regex pre-classification for obvious payments/income.
    Step 2: Ollama for everything else.
    """
    is_cc = account_type == "credit_card"
    system_prompt = _build_system_prompt(is_cc)

    needs_ai = []
    for tx in transactions:
        pre_cat, pre_sub = _pre_classify(tx["raw_desc"], is_cc=is_cc, amount=tx.get("amount", 0.0))
        if pre_cat:
            tx["category"] = pre_cat
            tx["subcategory"] = pre_sub
        else:
            needs_ai.append(tx)

    for i in range(0, len(needs_ai), batch_size):
        _categorize_batch(needs_ai[i : i + batch_size], system_prompt, is_cc)

    return transactions


# ── Internal ──────────────────────────────────────────────────────────────────

def _pre_classify(raw_desc: str, is_cc: bool = True, amount: float = 0.0) -> tuple[str, str] | tuple[None, None]:
    """Return (category, subcategory) or (None, None) to fall through to AI."""
    if _PAYMENT_PATTERNS.search(raw_desc):
        return "Payment", "Credit Card Payment"
    # For checking/savings: positive amount matching income patterns → Income
    if not is_cc and amount > 0 and _INCOME_PATTERNS.search(raw_desc):
        return "Income", "Salary"
    return None, None


def _categorize_batch(
    transactions: list[dict],
    system_prompt: str,
    is_cc: bool,
) -> None:
    """Categorize a batch in-place. Falls back to Miscellaneous/Other on failure."""
    if not transactions:
        return

    if is_cc:
        def _tag(amount: float) -> str:
            return "CHARGE" if amount > 0 else "CREDIT"
    else:
        def _tag(amount: float) -> str:
            return "DEPOSIT" if amount > 0 else "WITHDRAWAL"

    tx_lines = "\n".join(
        f'{i + 1}. {tx["raw_desc"]} [{_tag(tx["amount"])}]'
        for i, tx in enumerate(transactions)
    )

    user_prompt = (
        f"Categorize these {len(transactions)} transactions.\n"
        f"Return a JSON array with exactly {len(transactions)} objects.\n\n"
        f"Transactions:\n{tx_lines}\n\n"
        f'Example: [{{"index": 1, "category": "Home", "subcategory": "Groceries"}}, '
        f'{{"index": 2, "category": "Transportation", "subcategory": "Gas"}}]'
    )

    try:
        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": 0.1},
        )

        content = response["message"]["content"].strip()
        start = content.find("[")
        end = content.rfind("]") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON array in model response")

        results: list[dict] = json.loads(content[start:end])
        result_map = {r["index"]: r for r in results}

        for i, tx in enumerate(transactions):
            r = result_map.get(i + 1, {})
            cat = r.get("category", "Miscellaneous")
            sub = r.get("subcategory", "Other")
            # Validate category is known; fall back gracefully
            if cat not in TAXONOMY:
                cat = "Miscellaneous"
            valid_subs = TAXONOMY.get(cat, [])
            if valid_subs and sub not in valid_subs:
                sub = "Other"
            tx["category"] = cat
            tx["subcategory"] = sub

    except Exception as e:
        print(f"[ai_engine] Categorization failed for batch: {e}")
        for tx in transactions:
            tx.setdefault("category", "Miscellaneous")
            tx.setdefault("subcategory", "Other")

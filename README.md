# SpendSense 💰

**Privacy-first personal finance tracker — runs entirely on your machine. No cloud, no subscriptions, no data leaving your device.**

Upload bank statements (PDF or CSV), let a local AI categorize everything, then explore your spending with clean charts and budget tracking.

---

## Features

### Core
- **PDF + CSV parsing** — works with Chase, Amex, Capital One, Citi, and most major banks without per-bank configuration
- **Local AI categorization** — uses [Ollama](https://ollama.com) (llama3.2) to categorize transactions; nothing leaves your machine
- **Merchant rules** — confirm a category once, and it's applied automatically to all future and past matching transactions
- **Duplicate detection** — re-uploading the same statement skips already-imported transactions

### Transactions
- Editable grid — fix merchant names, categories, subcategories, amounts, and notes inline
- Search across merchant name and original bank description
- Filter by year, month, account, category, or unreviewed-only
- Add transactions manually (cash, etc.)
- Delete individual transactions

### Reports & Analytics
- **Dashboard** — monthly KPIs (income, expenses, savings), spending pie chart, trend bar chart, budget health bars, recent transactions; filterable by month/year
- **Spending by Category** — donut chart + subcategory breakdown
- **Monthly Spending Trend** — bar chart across all months
- **Income** — monthly income bar chart, income by source pie chart, income transaction list
- **Recurring Transactions** — automatically detects subscriptions and fixed bills (merchants appearing in 2+ months with a consistent amount)
- **Budget vs Actual** — percentage-based budgets (e.g. 40/30/30 rule); per-category progress bars with correct color logic (savings: green = more is better)
- **Budget Trend** — monthly actual vs target comparison across the year
- **CSV Export** — filtered export with year/month/account/category options

### Privacy
- 100% local — SQLite database, local AI inference via Ollama
- No accounts, no telemetry, no cloud sync

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend API | Python 3.13 + FastAPI |
| Database | SQLite (via SQLAlchemy) |
| AI | Ollama (llama3.2, runs locally) |
| PDF parsing | pdfplumber |
| Frontend | Streamlit |
| Charts | Plotly Express |
| Data grid | streamlit-aggrid |

---

## Getting Started

### Prerequisites
- Python 3.11+
- [Ollama](https://ollama.com) installed and running

### Installation

```bash
# 1. Clone the repo
git clone https://github.com/selvathiruarul/SpendSense.git
cd SpendSense

# 2. Install dependencies
pip install -r requirements.txt

# 3. Pull the AI model (one-time, ~2GB)
ollama pull llama3.2

# 4. Copy environment config
cp .env.example .env

# 5. Start the backend (in one terminal)
uvicorn backend.main:app --reload --port 8000

# 6. Start the frontend (in another terminal)
streamlit run frontend/app.py
```

Open http://localhost:8501 in your browser.

### Docker (optional)

```bash
docker-compose up
```

---

## Usage

1. **Upload** — go to *Upload Statement*, select your account type, give it a name (e.g. "Chase Checking"), and upload a PDF or CSV
2. **Review** — go to *Transactions*, correct any miscategorized items, then tick "Reviewed" — SpendSense learns the rule for next time
3. **Budget** — go to *Reports → Set Budget Percentages*, enter your savings target % (e.g. 30%) and optional per-category breakdown
4. **Analyze** — use the Dashboard for a monthly snapshot, or Reports for deep dives into income, recurring spend, and budget trends

---

## Supported Banks

Tested with statements from:
- Chase (checking + credit card)
- American Express
- Capital One
- Citi / Best Buy Citi
- Most banks exporting standard CSV

The PDF parser is opportunistic — it doesn't need per-bank configuration and handles most statement formats automatically.

---

## Project Structure

```
SpendSense/
├── backend/
│   ├── main.py          # FastAPI app — all routes
│   ├── models.py        # SQLAlchemy models (Transaction, BudgetTarget, MerchantRule)
│   ├── database.py      # SQLite engine + session
│   ├── parser.py        # PDF (pdfplumber) + CSV parser
│   └── ai_engine.py     # Ollama categorization (batches of 5)
├── frontend/
│   └── app.py           # Streamlit 4-page app
├── data/                # SQLite DB lives here (git-ignored)
├── .env.example
├── requirements.txt
└── docker-compose.yml
```

---

## License

MIT

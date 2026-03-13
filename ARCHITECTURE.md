# SpendSense - Architecture Overview

## Purpose
**Fast, cloud-based expense tracking** by parsing bank/credit card statements and visualizing spending patterns.

**Key Differentiators from Actual Budget:**
- ✅ **PDF Statement Parsing** (Actual doesn't support this)
- ✅ **3-5x Faster** (simpler architecture, no CRDT overhead)
- ✅ **Cloud-First Multi-Device Sync** (accessible everywhere, simple)
- ✅ **Focused** (expense tracking only, no complex budgeting)

## Core Principles
1. **NO STATEMENT STORAGE** - Upload → Parse → Extract → Delete (privacy)
2. **SPEED FIRST** - Every operation optimized for < 1 second
3. **CLOUD-FIRST** - Multi-device sync via direct database access
4. **MINIMAL** - Only essential features, no bloat

---

## Why SpendSense is Faster Than Actual Budget

| Component | Actual Budget | SpendSense |
|-----------|--------------|------------|
| **Sync** | CRDT (complex, slow) | Direct PostgreSQL (simple, fast) |
| **Database** | SQLite in WebAssembly | Native PostgreSQL with indexes |
| **App Type** | Electron (heavy) | Web-only (lightweight) |
| **State** | Redux (full state tree) | React Query (cached queries) |
| **Scope** | Full budgeting system | Expense tracking only |
| **Load Time** | 3-5 seconds | < 1 second |

See [ACTUAL_BUDGET_ANALYSIS.md](docs/ACTUAL_BUDGET_ANALYSIS.md) for detailed comparison.

---

## Tech Stack (Optimized for Speed & Sync)

### Frontend
- **React 18** + TypeScript + Vite
- **UI**: Tailwind CSS + shadcn/ui (lightweight, fast)
- **Charts**: Recharts (smaller bundle than Chart.js)
- **State**: @tanstack/react-query (server state caching)
- **Routing**: React Router v6
- **Deployment**: Vercel (Free, global CDN, instant)

### Backend
- **Node.js 20+** + Express + TypeScript
- **File Parsing**:
  - CSV: `papaparse` (fast, streaming)
  - PDF: `pdf-parse` or `pdfjs-dist`
  - OFX/QFX: Custom parser (adapted from Actual)
- **Validation**: Zod (type-safe runtime validation)
- **ORM**: Prisma (type-safe, optimized queries)
- **API**: RESTful (simple, fast)
- **Deployment**: Railway or Render ($5-7/month)

### Database
- **PostgreSQL 16** (Cloud-hosted, always available)
- **Hosting Options**:
  - **Supabase** (Recommended): Free tier (500MB, good for MVP)
  - Railway: $5/month
  - Neon: Serverless PostgreSQL (free tier)
- **Optimizations**:
  - Proper B-tree indexes on date, account_id, category_id
  - Materialized views for dashboard aggregations
  - Full-text search on transaction descriptions
  - Query caching for common analytics

---

## System Architecture (Cloud-First)

```
┌─────────────────────────────────────────────────────────────┐
│                      DEVICE 1 (Laptop)                      │
│  ┌──────────────┐                                           │
│  │   Browser    │                                           │
│  │  (React App) │                                           │
│  └──────┬───────┘                                           │
│         │                                                    │
│         │ HTTPS/REST                                        │
└─────────┼─────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│              CLOUD INFRASTRUCTURE                            │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  FRONTEND (Vercel/Netlify)                           │  │
│  │  - Static React build                                │  │
│  │  - Global CDN                                        │  │
│  │  - Instant deploy                                    │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  BACKEND API (Railway/Render)                        │  │
│  │  ┌────────────────┐  ┌────────────────┐             │  │
│  │  │ File Parser    │  │ Transaction    │             │  │
│  │  │ (Memory only)  │  │ Categorizer    │             │  │
│  │  └────────────────┘  └────────────────┘             │  │
│  │                                                       │  │
│  │  ┌────────────────────────────────────┐             │  │
│  │  │   Express REST API                 │             │  │
│  │  │   - /api/transactions              │             │  │
│  │  │   - /api/analytics                 │             │  │
│  │  │   - /api/accounts                  │             │  │
│  │  └────────────────┬───────────────────┘             │  │
│  └───────────────────┼───────────────────────────────────┘ │
│                      │                                      │
│  ┌───────────────────▼───────────────────────────────────┐ │
│  │  PostgreSQL Database (Supabase/Railway)              │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │ │
│  │  │transactions │  │  accounts   │  │ categories  │  │ │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  │ │
│  │                                                       │ │
│  │  Indexes: date, account_id, category_id             │ │
│  │  Materialized views for analytics                    │ │
│  └───────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
          │
          │ HTTPS/REST (Same database!)
          ▼
┌─────────────────────────────────────────────────────────────┐
│                      DEVICE 2 (Phone)                        │
│  ┌──────────────┐                                           │
│  │   Browser    │                                           │
│  │  (React App) │                                           │
│  └──────────────┘                                           │
│                                                              │
│  ✅ Sees same data instantly (no complex sync!)            │
└─────────────────────────────────────────────────────────────┘
```

**How Multi-Device Sync Works:**
- **No special sync protocol needed!**
- All devices → Same cloud database
- Device 1 adds transaction → Saves to PostgreSQL
- Device 2 loads page → Queries same PostgreSQL → Sees transaction
- React Query handles caching and invalidation
- Simple, fast, reliable

---

## Data Flow

### Upload & Parse Flow
```
1. User uploads CSV/PDF → Frontend
2. Frontend sends file (multipart/form-data) → Backend API
3. Backend:
   a. Validates file (type, size < 10MB)
   b. Parse file IN MEMORY (never save to disk!)
   c. Extract transactions array
   d. Apply auto-categorization rules
   e. Return JSON: { transactions: [...], suggested_categories: [...] }
4. Frontend displays transactions table (editable)
5. User reviews/edits categories
6. User clicks "Save All"
7. Frontend sends POST /api/transactions/batch
8. Backend saves to PostgreSQL
9. Original file is garbage collected (never stored)
10. React Query invalidates cache
11. Dashboard updates automatically
```
**Performance Target**: < 2 seconds for CSV, < 5 seconds for PDF

### Dashboard Load Flow
```
1. User opens dashboard → React app loads
2. React Query checks cache
   - If cached (< 5 minutes): Display cached data instantly
   - If stale: Fetch from API in background
3. API queries PostgreSQL:
   SELECT month, SUM(amount)
   FROM transactions
   WHERE date >= $start
   GROUP BY month
4. PostgreSQL returns aggregated data (fast with indexes)
5. Frontend renders charts
```
**Performance Target**: < 500ms (< 100ms if cached)

### Multi-Device Sync Flow
```
Device 1: User adds transaction
  → POST /api/transactions
  → Saves to PostgreSQL
  → Returns success

Device 2: User refreshes dashboard
  → GET /api/transactions
  → Queries PostgreSQL
  → Returns all transactions (including new one!)
  → React Query caches for 5 minutes
```
**No complex CRDT, no conflict resolution, just simple database queries!**

---

## Database Schema

See [schema.sql](schema.sql) for full SQL schema.

### Core Tables

**accounts**
```sql
id (UUID), name, account_type, last_four, institution,
is_active, created_at, updated_at
```

**categories** (Hierarchical)
```sql
id (UUID), name, category_type (income/expense),
parent_id (for subcategories), color, icon,
is_system, created_at, updated_at
```

**transactions**
```sql
id (UUID), account_id, category_id, transaction_date,
description, amount, transaction_type, notes,
is_recurring, created_at, updated_at
```

**Indexes for Speed:**
```sql
CREATE INDEX idx_transactions_date ON transactions(transaction_date);
CREATE INDEX idx_transactions_account ON transactions(account_id);
CREATE INDEX idx_transactions_category ON transactions(category_id);
CREATE INDEX idx_transactions_date_range ON transactions(transaction_date, account_id, category_id);
```

**Materialized View for Dashboard:**
```sql
CREATE MATERIALIZED VIEW monthly_summary AS
SELECT DATE_TRUNC('month', transaction_date) as month,
       transaction_type, COUNT(*), SUM(amount)
FROM transactions
GROUP BY month, transaction_type;
```

---

## API Endpoints

### Transactions
- `POST /api/transactions/parse` - Parse file, return transactions (not saved)
- `POST /api/transactions/batch` - Save multiple transactions
- `GET /api/transactions?start_date=&end_date=&account_id=` - List transactions
- `GET /api/transactions/:id` - Get single transaction
- `PUT /api/transactions/:id` - Update transaction
- `DELETE /api/transactions/:id` - Delete transaction

### Categories
- `GET /api/categories` - List all categories (hierarchical)
- `POST /api/categories` - Create custom category
- `PUT /api/categories/:id` - Update category
- `DELETE /api/categories/:id` - Delete category

### Accounts
- `GET /api/accounts` - List all accounts
- `POST /api/accounts` - Add new account
- `PUT /api/accounts/:id` - Update account
- `DELETE /api/accounts/:id` - Delete account

### Analytics (Optimized Queries)
- `GET /api/analytics/summary?start=&end=` - Monthly/yearly summaries
- `GET /api/analytics/by-category?start=&end=` - Spending by category
- `GET /api/analytics/trends?period=monthly` - Spending trends
- `GET /api/analytics/savings?year=2025` - Income vs expenses

---

## Statement Parsing Strategy

### Phase 1: CSV (Easy, Fast)
```javascript
// Using papaparse
const results = parse(csvContent, {
  header: true,
  skipEmptyLines: true,
  dynamicTyping: true
});
// Map columns intelligently
// Support: date, description, amount, debit/credit
```

### Phase 2: PDF (Harder, Key Differentiator)
```javascript
// Using pdf-parse
const pdfData = await pdfParse(buffer);
const text = pdfData.text;
// Apply regex patterns for known banks:
// - Chase: /\d{2}\/\d{2}\s+.+\s+[\d,]+\.\d{2}/
// - Amex: Similar pattern
// Template matching system (add banks as needed)
```

### Phase 3: OFX/QFX (Adapt from Actual)
- Use Actual's parser as reference
- Already handles complex format

### Auto-Categorization
```javascript
// Rule-based matching
const rules = [
  { pattern: /starbucks|coffee/i, category: 'Food & Dining > Coffee' },
  { pattern: /shell|chevron|gas/i, category: 'Transportation > Gas' },
  { pattern: /walmart|target/i, category: 'Shopping > Groceries' },
  { pattern: /netflix|spotify/i, category: 'Bills > Subscriptions' },
];
// ML enhancement later: Learn from user corrections
```

---

## Development Roadmap

### Phase 1: MVP (Week 1-2)
- [x] Architecture docs
- [ ] Setup: Supabase + Railway + Vercel accounts
- [ ] Database schema + Prisma setup
- [ ] CSV parser + API
- [ ] Basic React UI (upload + table)
- [ ] Deploy to cloud

### Phase 2: Core Features (Week 3-4)
- [ ] PDF parser (start with one bank)
- [ ] Auto-categorization engine
- [ ] Dashboard with charts
- [ ] Search & filters
- [ ] Mobile responsive

### Phase 3: Polish (Week 5-6)
- [ ] More bank formats
- [ ] Bulk operations
- [ ] Export (CSV, PDF reports)
- [ ] Keyboard shortcuts
- [ ] Dark mode

### Phase 4: Advanced (Future)
- [ ] ML-based categorization
- [ ] Receipt photo upload
- [ ] Bank API integration (Plaid)
- [ ] Budgets & alerts

---

## Cost Breakdown

### Development (Local)
- **Cost**: $0
- Docker Compose with local PostgreSQL

### Production (Cloud)
- **Frontend**: Vercel (Free forever)
- **Backend**: Railway ($5/month) or Render (Free tier)
- **Database**: Supabase (Free: 500MB) or Railway ($5/month)
- **Total**: **$0-10/month**

**Scaling costs** (if you share with friends):
- 100 users: ~$10/month
- 1000 users: ~$25/month
- 10,000 users: ~$50/month

---

## Performance Goals

| Operation | Target | Actual Budget |
|-----------|--------|---------------|
| Dashboard Load | < 1 second | 3-5 seconds |
| CSV Parse | < 2 seconds | ~3 seconds |
| PDF Parse | < 5 seconds | N/A |
| Transaction Save | < 500ms | ~1 second |
| Search | < 300ms | ~1 second |
| Multi-device sync | Instant | ~2-3 seconds |

---

## Security

1. **HTTPS Only** - All communication encrypted
2. **Authentication** - JWT tokens (add in Phase 2)
3. **File Validation** - Type, size, malware scanning
4. **SQL Injection** - Prisma ORM prevents this
5. **Rate Limiting** - Prevent abuse
6. **No File Storage** - Privacy by design

---

## Next Steps

1. **You decide**:
   - Start with backend (parsers + API)?
   - Start with frontend (UI mockups)?
   - Setup cloud infrastructure first?

2. **Sample data needed**:
   - Share a bank statement format (redacted)?
   - This helps me build the right parser

3. **Questions**:
   - Which banks do you use? (Chase, Bank of America, etc.)
   - Do you want authentication or keep it simple?
   - Import existing spreadsheet data?

**Ready to start coding? Let me know!** 🚀

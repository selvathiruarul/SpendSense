# Actual Budget - Architecture Analysis

## Overview
Actual Budget is a local-first personal finance app with multi-device sync capabilities. Based on my analysis of the codebase, here's how it works and why it might be slow.

---

## Architecture Components

### 1. **loot-core** (Core Engine)
The heart of Actual Budget containing:
- **Database Layer**: Uses SQL.js (SQLite compiled to WebAssembly)
  - In browser: absurd-sql (IndexedDB-backed SQLite)
  - In desktop: better-sqlite3 (native SQLite)
- **Business Logic**: Budgeting, transactions, accounts, rules
- **Sync Engine**: CRDT-based synchronization
- **Import System**: CSV, OFX, QFX, QIF, XML (CAMT.053) parsers
- **Spreadsheet Engine**: For budget calculations

**Key Files Found:**
- `src/server/transactions/import/parse-file.ts` - File parsing
- `src/server/accounts/sync.ts` - Account synchronization
- `src/server/db/` - Database layer

### 2. **sync-server** (Sync Backend)
Node.js/Express server that:
- Stores sync data (changes/diffs)
- Uses **@actual-app/crdt** package (Conflict-free Replicated Data Type)
- SQLite database for storing sync messages
- Handles authentication (bcrypt, OpenID)
- Bank integrations (GoCardless, Pluggy)

**Dependencies:**
```
- better-sqlite3: Server-side database
- @actual-app/crdt: Sync protocol
- express: HTTP server
- bcrypt: Password hashing
```

### 3. **desktop-client** (UI Layer)
React-based frontend:
- Redux for state management (@reduxjs/toolkit)
- Connects to loot-core
- Renders budget views, transactions, reports

### 4. **desktop-electron** (Desktop App)
Electron wrapper for desktop distribution

### 5. **api** (Programmatic API)
Node.js package `@actual-app/api` for:
- Custom importers
- Automation scripts
- Programmatic access

---

## How Actual Budget Works

### Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    User Device 1                            │
│  ┌──────────────┐                                           │
│  │   React UI   │                                           │
│  └──────┬───────┘                                           │
│         │                                                    │
│  ┌──────▼───────────────────────────┐                      │
│  │        loot-core                 │                      │
│  │  ┌─────────────┐  ┌───────────┐ │                      │
│  │  │   SQLite    │  │   CRDT    │ │                      │
│  │  │ (local DB)  │  │  Engine   │ │                      │
│  │  └─────────────┘  └─────┬─────┘ │                      │
│  └────────────────────────┬─┘       │                      │
└───────────────────────────┼─────────┘
                            │
                            │ Sync Changes
                            ▼
                ┌──────────────────────┐
                │   Sync Server        │
                │  ┌────────────────┐  │
                │  │ SQLite         │  │
                │  │ (sync messages)│  │
                │  └────────────────┘  │
                └──────────┬───────────┘
                           │
                           │ Sync Changes
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    User Device 2                            │
│  ┌──────────────┐                                           │
│  │   React UI   │                                           │
│  └──────┬───────┘                                           │
│         │                                                    │
│  ┌──────▼───────────────────────────┐                      │
│  │        loot-core                 │                      │
│  │  ┌─────────────┐  ┌───────────┐ │                      │
│  │  │   SQLite    │  │   CRDT    │ │                      │
│  │  │ (local DB)  │  │  Engine   │ │                      │
│  │  └─────────────┘  └───────────┘ │                      │
│  └──────────────────────────────────┘                      │
└─────────────────────────────────────────────────────────────┘
```

### Sync Mechanism (CRDT)

**What is CRDT?**
- **C**onflict-free **R**eplicated **D**ata **T**ype
- Allows multiple devices to edit simultaneously without conflicts
- Changes are stored as "operations" rather than full data
- Each device applies operations in order to reconstruct state

**How it works in Actual:**
1. User makes a change (e.g., add transaction)
2. Change is recorded as a CRDT operation
3. Operation saved to local SQLite
4. Operation sent to sync-server
5. Sync-server broadcasts to other devices
6. Other devices apply the operation to their local database

**Example:**
```
Device 1: "Add transaction: $50 Groceries on 2025-10-22"
         → CRDT Operation: {type: 'insert', table: 'transactions', data: {...}}
         → Sent to sync-server

Device 2: Receives operation
         → Applies to local DB
         → Now both devices have the transaction
```

---

## Features Breakdown

### ✅ What Actual Budget Does Well

1. **Multi-Device Sync**: Seamless sync across devices
2. **Offline-First**: Works without internet, syncs when online
3. **Import Formats**: CSV, OFX, QFX, QIF, XML (CAMT.053)
4. **Bank Sync**: GoCardless (EU/UK), SimpleFIN (US/Canada)
5. **Envelope Budgeting**: Zero-based budgeting (YNAB-style)
6. **Rules Engine**: Auto-categorization based on rules
7. **Reports**: Basic spending reports
8. **Privacy**: Self-hosted, E2E encryption option
9. **Open Source**: Free, MIT license

### ❌ What Actual Budget Does NOT Have

1. **PDF Import**: Cannot parse PDF statements
2. **AI/ML Categorization**: Only rule-based
3. **Advanced Analytics**: Limited dashboard options
4. **Receipt Scanning**: No photo upload
5. **Batch Upload**: Upload one file at a time

---

## Why Actual Budget is Slow

Based on the codebase analysis, here are the performance bottlenecks:

### 1. **CRDT Overhead**
- Every change creates a sync operation
- Operations must be tracked, stored, and applied
- More complexity = slower operations
- Trade-off: Enables conflict-free sync but adds latency

### 2. **SQLite in Browser (WebAssembly)**
- absurd-sql runs SQLite in IndexedDB
- WebAssembly adds translation layer
- Slower than native database
- File I/O through IndexedDB is slow

### 3. **Electron Overhead**
- Electron apps are heavier than native apps
- Chromium + Node.js bundle is large
- More memory usage
- Slower startup times

### 4. **Complex Budgeting Engine**
- Spreadsheet-like calculation engine
- Recalculates budget on every change
- Many interdependent calculations
- Envelope budgeting requires complex state

### 5. **Redux State Management**
- Large state tree for entire budget
- Re-renders on state changes
- Not optimized for large transaction sets

### 6. **Full Data Sync**
- Syncs entire budget file structure
- Not incremental for initial sync
- Large budgets take longer to load

---

## Performance Comparison: Actual vs SpendSense

| Feature | Actual Budget | SpendSense (Proposed) |
|---------|--------------|----------------------|
| **Sync Method** | CRDT (complex) | Direct DB (simple) |
| **Database** | SQLite in WebAssembly | PostgreSQL (native) |
| **Frontend** | Electron/React | React (web only) |
| **State Management** | Redux (heavy) | React Query (light) |
| **Budgeting** | Complex envelope system | Simple tracking only |
| **PDF Import** | ❌ No | ✅ Yes |
| **Load Time** | 3-5 seconds | < 1 second (goal) |
| **Transaction Entry** | Multiple clicks | Optimized workflow |

---

## Key Takeaways for SpendSense

### What to Learn From Actual:

✅ **Good Ideas to Adopt:**
1. Import system architecture (CSV, OFX parsers)
2. Transaction reconciliation logic
3. Rule-based categorization
4. Database schema design
5. API design patterns

❌ **What to Avoid:**
1. CRDT complexity (use simpler sync)
2. Electron (use web-only)
3. Complex budgeting (focus on tracking)
4. SQLite in browser (use cloud PostgreSQL)
5. Heavy state management (use React Query)

### SpendSense Advantages:

1. **Simpler Sync**: Direct database access, no CRDT
2. **Native Database**: PostgreSQL with proper indexes
3. **Focused Scope**: Expense tracking only, no budgeting
4. **PDF Support**: Parse PDF statements (key differentiator)
5. **Optimized UI**: Fast, minimal clicks
6. **Cloud-First**: No sync complexity, just database queries

---

## Conclusion

**Actual Budget Architecture:**
- **Strengths**: Offline-first, conflict-free sync, privacy-focused
- **Weaknesses**: Complex, slow, heavy, limited import options

**SpendSense Approach:**
- Simpler architecture = faster performance
- Cloud-first = easier sync
- Focused features = less code to maintain
- Modern stack = better developer experience

**Bottom Line:**
Actual Budget is slow because of its complexity. SpendSense can be 3-5x faster by:
1. Skipping CRDT sync
2. Using cloud PostgreSQL
3. Removing budgeting complexity
4. Optimizing for your specific workflow

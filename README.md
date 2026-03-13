# SpendSense 💰

**Fast, cloud-based expense tracking app** - 3-5x faster than Actual Budget

## What is SpendSense?
A lightweight expense tracking app built for speed and simplicity:
- 📊 Upload bank statements (CSV/PDF) - auto-parse transactions
- 🏷️ Smart auto-categorization
- 📈 Beautiful dashboards (monthly/yearly/category views)
- ☁️ Cloud-first - sync across all devices instantly
- 🚀 Lightning fast - < 1 second dashboard loads

## Supported Banks
- Chase
- Discover
- American Express
- Capital One
- Citi

## Tech Stack
- **Frontend**: React + TypeScript + Vite + Tailwind
- **Backend**: Node.js + Express + TypeScript + Prisma
- **Database**: PostgreSQL (Supabase/Railway)
- **Deployment**: Vercel (frontend) + Railway (backend)

## Getting Started

### Development (Local)
```bash
# Start all services
docker-compose up

# Backend: http://localhost:3000
# Frontend: http://localhost:5173
# Database: PostgreSQL on port 5432
```

### Production
- Frontend: Deployed on Vercel
- Backend: Deployed on Railway
- Database: Supabase (free tier)
- **Cost**: $0-10/month

## Features
✅ CSV/PDF statement parsing
✅ Multi-account support
✅ Auto-categorization
✅ Monthly/yearly analytics
✅ Multi-device sync
✅ Fast performance (< 1s loads)

## Documentation
- [Architecture](ARCHITECTURE.md) - System design & tech decisions
- [Actual Budget Analysis](docs/ACTUAL_BUDGET_ANALYSIS.md) - Why we're faster

## Development Roadmap

### Phase 1: MVP (Current)
- [ ] Backend API with Prisma
- [ ] CSV parser for 5 major banks
- [ ] Basic React UI (upload + table)
- [ ] Simple dashboard

### Phase 2: Enhancement
- [ ] PDF parser
- [ ] Advanced categorization
- [ ] Search & filters
- [ ] Mobile responsive

### Phase 3: Polish
- [ ] Export functionality
- [ ] Keyboard shortcuts
- [ ] Dark mode
- [ ] Performance optimizations

---

**Status**: 🏗️ In Development | **License**: MIT

"""
Integration tests for backend/main.py

Uses FastAPI TestClient + in-memory SQLite (configured in conftest.py).
Ollama AI calls are patched so tests run fully offline.
"""
import csv
import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# conftest.py has already patched backend.database.engine to StaticPool
import backend.database as _db
from backend.database import Base, get_db
from backend.models import Transaction


def _fake_categorize(transactions, account_type="credit_card"):
    for tx in transactions:
        tx.setdefault("category", "Food")
        tx.setdefault("subcategory", None)
    return transactions


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=_db.engine)

    with patch("backend.main.categorize_all", side_effect=_fake_categorize):
        from backend.main import app
        with TestClient(app) as c:
            yield c


@pytest.fixture(autouse=True)
def clear_transactions():
    """Wipe transactions between tests for isolation."""
    db = _db.SessionLocal()
    db.query(Transaction).delete()
    db.commit()
    db.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _csv_bytes(rows: list[dict]) -> tuple[str, bytes, str]:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["Date", "Description", "Amount"])
    writer.writeheader()
    writer.writerows(rows)
    return ("test.csv", buf.getvalue().encode(), "text/csv")


def _upload(client, rows, account_type="credit_card", account=""):
    fname, data, ct = _csv_bytes(rows)
    return client.post(
        "/upload",
        files={"file": (fname, data, ct)},
        data={"account_type": account_type, "account": account},
    )


# ── Health check ──────────────────────────────────────────────────────────────

def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── Upload ────────────────────────────────────────────────────────────────────

class TestUpload:
    def test_happy_path(self, client):
        r = _upload(client, [{"Date": "2026-01-15", "Description": "AMAZON", "Amount": "-42.50"}])
        assert r.status_code == 200
        assert r.json()["imported"] == 1
        assert r.json()["skipped_duplicates"] == 0

    def test_rejects_unsupported_extension(self, client):
        r = client.post(
            "/upload",
            files={"file": ("statement.txt", b"foo", "text/plain")},
            data={"account_type": "credit_card"},
        )
        assert r.status_code == 400

    def test_rejects_file_over_10mb(self, client):
        r = client.post(
            "/upload",
            files={"file": ("big.csv", b"x" * 10_000_001, "text/csv")},
            data={"account_type": "credit_card"},
        )
        assert r.status_code == 413

    def test_duplicate_detection(self, client):
        rows = [{"Date": "2026-01-15", "Description": "NETFLIX", "Amount": "-15.99"}]
        _upload(client, rows)
        r = _upload(client, rows)
        assert r.json()["imported"] == 0
        assert r.json()["skipped_duplicates"] == 1

    def test_stores_account_name(self, client):
        _upload(client, [{"Date": "2026-02-01", "Description": "STARBUCKS", "Amount": "-5.75"}],
                account="Amex Blue")
        txs = client.get("/transactions").json()
        assert txs[0]["account"] == "Amex Blue"


# ── Transactions ──────────────────────────────────────────────────────────────

class TestTransactions:
    _ROWS = [
        {"Date": "2026-01-10", "Description": "NETFLIX",  "Amount": "-15.99"},
        {"Date": "2026-01-12", "Description": "SPOTIFY",  "Amount": "-9.99"},
        {"Date": "2026-01-15", "Description": "PAYCHECK", "Amount": "3000.00"},
    ]

    def test_list_transactions(self, client):
        _upload(client, self._ROWS, account_type="checking")
        assert len(client.get("/transactions").json()) == 3

    def test_filter_by_category(self, client):
        _upload(client, self._ROWS, account_type="checking")
        r = client.get("/transactions?category=Food")
        assert r.status_code == 200
        assert len(r.json()) == 3  # fake categorizer assigns "Food" to all

    def test_patch_category(self, client):
        _upload(client, self._ROWS, account_type="checking")
        tx_id = client.get("/transactions").json()[0]["id"]
        client.patch(f"/transactions/{tx_id}", json={"category": "Entertainment"})
        updated = next(t for t in client.get("/transactions").json() if t["id"] == tx_id)
        assert updated["category"] == "Entertainment"

    def test_patch_merchant_and_notes(self, client):
        _upload(client, self._ROWS, account_type="checking")
        tx_id = client.get("/transactions").json()[0]["id"]
        client.patch(f"/transactions/{tx_id}",
                     json={"merchant": "Netflix Inc.", "notes": "family plan"})
        updated = next(t for t in client.get("/transactions").json() if t["id"] == tx_id)
        assert updated["merchant"] == "Netflix Inc."
        assert updated["notes"] == "family plan"

    def test_patch_nonexistent_returns_404(self, client):
        assert client.patch("/transactions/99999", json={"category": "Food"}).status_code == 404

    def test_delete_transaction(self, client):
        _upload(client, self._ROWS, account_type="checking")
        txs = client.get("/transactions").json()
        tx_id = txs[0]["id"]
        assert client.delete(f"/transactions/{tx_id}").status_code == 204
        remaining = client.get("/transactions").json()
        assert len(remaining) == 2
        assert all(t["id"] != tx_id for t in remaining)

    def test_delete_nonexistent_returns_404(self, client):
        assert client.delete("/transactions/99999").status_code == 404

    def test_mark_reviewed_creates_merchant_rule(self, client):
        _upload(client, self._ROWS, account_type="checking")
        tx_id = client.get("/transactions").json()[0]["id"]
        client.patch(f"/transactions/{tx_id}",
                     json={"category": "Entertainment", "is_reviewed": True})
        rules = client.get("/merchant-rules").json()
        assert len(rules) >= 1


# ── Summary ───────────────────────────────────────────────────────────────────

class TestSummary:
    def test_summary_returns_expected_keys(self, client):
        _upload(client, [
            {"Date": "2026-01-10", "Description": "AMAZON",   "Amount": "-100.00"},
            {"Date": "2026-01-15", "Description": "PAYCHECK", "Amount": "3000.00"},
        ], account_type="checking")
        body = client.get("/summary").json()
        assert "by_category" in body
        assert "savings_rate_pct" in body

    def test_summary_filtered_by_month(self, client):
        _upload(client, [{"Date": "2026-01-10", "Description": "AMAZON", "Amount": "-50.00"}],
                account_type="checking")
        assert client.get("/summary?year=2026&month=1").status_code == 200

    def test_monthly_endpoint(self, client):
        _upload(client, [{"Date": "2026-01-10", "Description": "AMAZON", "Amount": "-50.00"}],
                account_type="checking")
        r = client.get("/monthly")
        assert r.status_code == 200
        assert "monthly" in r.json()


# ── Recurring ─────────────────────────────────────────────────────────────────

class TestRecurring:
    def test_detects_recurring_merchant(self, client):
        _upload(client, [
            {"Date": "2026-01-10", "Description": "NETFLIX", "Amount": "-15.99"},
            {"Date": "2026-02-10", "Description": "NETFLIX", "Amount": "-15.99"},
        ])
        r = client.get("/recurring")
        assert r.status_code == 200
        merchants = [item["merchant"].lower() for item in r.json()]
        assert any("netflix" in m for m in merchants)

    def test_single_occurrence_not_recurring(self, client):
        _upload(client, [{"Date": "2026-01-10", "Description": "ONE TIME PURCHASE", "Amount": "-250.00"}])
        r = client.get("/recurring")
        assert r.status_code == 200
        merchants = [item["merchant"].lower() for item in r.json()]
        assert not any("one time" in m for m in merchants)

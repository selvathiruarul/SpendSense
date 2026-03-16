"""
Unit tests for backend/parser.py

Tests the pure parsing functions — no DB, no network, no files needed.
"""
import csv
import io
import os
import tempfile

import pytest

from backend.parser import (
    _try_parse_amount,
    _try_parse_date,
    _parse_text_line,
    _infer_statement_period,
    parse_csv,
)


# ── Amount parsing ─────────────────────────────────────────────────────────────

class TestTryParseAmount:
    def test_plain_integer(self):
        assert _try_parse_amount("42") == 42.0

    def test_decimal(self):
        assert _try_parse_amount("52.34") == 52.34

    def test_negative(self):
        assert _try_parse_amount("-52.34") == -52.34

    def test_dollar_sign(self):
        assert _try_parse_amount("$52.34") == 52.34

    def test_comma_thousands(self):
        assert _try_parse_amount("1,234.56") == 1234.56

    def test_parentheses_negative(self):
        assert _try_parse_amount("(50.00)") == -50.00

    def test_trailing_minus_citi(self):
        assert _try_parse_amount("30.00-") == -30.00

    def test_dollar_with_space(self):
        assert _try_parse_amount("$ 30.00") == 30.00

    def test_over_million_rejected(self):
        assert _try_parse_amount("1,500,000.00") is None

    def test_non_numeric_rejected(self):
        assert _try_parse_amount("abc") is None

    def test_empty_string(self):
        assert _try_parse_amount("") is None

    def test_zero(self):
        assert _try_parse_amount("0.00") == 0.0


# ── Date parsing ───────────────────────────────────────────────────────────────

class TestTryParseDate:
    def test_mm_dd_yyyy(self):
        assert _try_parse_date("01/15/2026") == "2026-01-15"

    def test_yyyy_mm_dd(self):
        assert _try_parse_date("2026-01-15") == "2026-01-15"

    def test_mm_dd_no_year_same_year(self):
        # tx month (1) <= stmt month (3) → same year
        assert _try_parse_date("01/15", stmt_year=2026, stmt_month=3) == "2026-01-15"

    def test_mm_dd_no_year_prior_year(self):
        # tx month (12) > stmt month (1) → prior year
        assert _try_parse_date("12/15", stmt_year=2026, stmt_month=1) == "2025-12-15"

    def test_mon_dd_no_year(self):
        assert _try_parse_date("Jan 15", stmt_year=2026, stmt_month=3) == "2026-01-15"

    def test_mon_dd_prior_year(self):
        assert _try_parse_date("Dec 15", stmt_year=2026, stmt_month=1) == "2025-12-15"

    def test_dd_mon_yyyy(self):
        assert _try_parse_date("15-Jan-2026") == "2026-01-15"

    def test_long_month_name(self):
        assert _try_parse_date("January 15, 2026") == "2026-01-15"

    def test_mm_dd_yy_two_digit(self):
        assert _try_parse_date("01/15/26") == "2026-01-15"

    def test_invalid_returns_none(self):
        assert _try_parse_date("not a date") is None

    def test_invalid_date_values(self):
        assert _try_parse_date("13/45/2026") is None


# ── Text line parsing ──────────────────────────────────────────────────────────

class TestParseTextLine:
    def test_credit_card_format(self):
        tx = _parse_text_line("01/15 WHOLE FOODS MARKET  52.34", 2026, 1)
        assert tx is not None
        assert tx["date"] == "2026-01-15"
        assert tx["raw_desc"] == "WHOLE FOODS MARKET"
        assert tx["amount"] == 52.34

    def test_checking_format_with_balance(self):
        # "amount balance" — should take amount, discard balance
        # Parser regex requires comma-formatted thousands (2,500.00 not 2500.00)
        tx = _parse_text_line("01/15 PAYROLL DEPOSIT  2,500.00 31,313.94", 2026, 1)
        assert tx is not None
        assert tx["amount"] == 2500.00
        assert "31" not in tx["raw_desc"]

    def test_negative_amount(self):
        tx = _parse_text_line("03/10 NETFLIX.COM  -15.99", 2026, 3)
        assert tx is not None
        assert tx["amount"] == -15.99

    def test_citi_trailing_minus(self):
        tx = _parse_text_line("03/10 BEST BUY  30.00-", 2026, 3)
        assert tx is not None
        assert tx["amount"] == -30.00

    def test_line_too_short_rejected(self):
        assert _parse_text_line("x", 2026, 1) is None

    def test_no_date_rejected(self):
        assert _parse_text_line("WHOLE FOODS MARKET  52.34", 2026, 1) is None

    def test_no_amount_rejected(self):
        assert _parse_text_line("01/15 WHOLE FOODS MARKET", 2026, 1) is None

    def test_desc_too_short_rejected(self):
        # single-char description after date — should be rejected
        assert _parse_text_line("01/15 X  52.34", 2026, 1) is None


# ── Statement period inference ─────────────────────────────────────────────────

class TestInferStatementPeriod:
    def test_parses_month_year_from_text(self):
        text = "Chase Bank Statement  February 2026"
        year, month = _infer_statement_period(text, "statement.pdf")
        assert year == 2026
        assert month == 2

    def test_parses_abbreviated_month(self):
        text = "Statement Period: Jan 2026"
        year, month = _infer_statement_period(text, "stmt.pdf")
        assert year == 2026
        assert month == 1

    def test_falls_back_to_filename_year(self):
        year, _ = _infer_statement_period("", "chase_2025_statement.pdf")
        assert year == 2025

    def test_case_insensitive(self):
        text = "MARCH 2026 statement"
        year, month = _infer_statement_period(text, "stmt.pdf")
        assert year == 2026
        assert month == 3


# ── CSV parsing ────────────────────────────────────────────────────────────────

class TestParseCsv:
    def _write_csv(self, rows: list[dict], fieldnames: list[str]) -> str:
        """Write a CSV to a temp file and return its path."""
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, newline=""
        )
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        f.close()
        return f.name

    def test_standard_format(self):
        path = self._write_csv(
            [{"Date": "2026-01-15", "Description": "AMAZON", "Amount": "-42.50"}],
            ["Date", "Description", "Amount"],
        )
        try:
            txs = parse_csv(path)
            assert len(txs) == 1
            assert txs[0]["raw_desc"] == "AMAZON"
            assert txs[0]["amount"] == -42.50
            assert txs[0]["date"] == "2026-01-15"
        finally:
            os.unlink(path)

    def test_chase_csv_columns(self):
        path = self._write_csv(
            [{"Transaction Date": "01/20/2026", "Description": "STARBUCKS", "Amount": "-5.75"}],
            ["Transaction Date", "Description", "Amount"],
        )
        try:
            txs = parse_csv(path)
            assert len(txs) == 1
            assert txs[0]["raw_desc"] == "STARBUCKS"
            assert txs[0]["amount"] == -5.75
        finally:
            os.unlink(path)

    def test_skips_rows_missing_amount(self):
        path = self._write_csv(
            [
                {"Date": "2026-01-15", "Description": "AMAZON", "Amount": "-42.50"},
                {"Date": "2026-01-16", "Description": "MISSING", "Amount": ""},
            ],
            ["Date", "Description", "Amount"],
        )
        try:
            txs = parse_csv(path)
            assert len(txs) == 1
        finally:
            os.unlink(path)

    def test_multiple_transactions(self):
        rows = [
            {"Date": "2026-01-10", "Description": "NETFLIX", "Amount": "-15.99"},
            {"Date": "2026-01-12", "Description": "SPOTIFY", "Amount": "-9.99"},
            {"Date": "2026-01-15", "Description": "PAYROLL", "Amount": "3000.00"},
        ]
        path = self._write_csv(rows, ["Date", "Description", "Amount"])
        try:
            txs = parse_csv(path)
            assert len(txs) == 3
            amounts = [t["amount"] for t in txs]
            assert -15.99 in amounts
            assert 3000.00 in amounts
        finally:
            os.unlink(path)

    def test_memo_column_fallback(self):
        path = self._write_csv(
            [{"Date": "2026-01-15", "Memo": "WHOLE FOODS", "Amount": "-52.34"}],
            ["Date", "Memo", "Amount"],
        )
        try:
            txs = parse_csv(path)
            assert len(txs) == 1
            assert txs[0]["raw_desc"] == "WHOLE FOODS"
        finally:
            os.unlink(path)

"""
SpendSense Streamlit Dashboard
Run with: streamlit run frontend/app.py
"""
from __future__ import annotations

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

API_BASE = "http://localhost:8000"

st.set_page_config(
    page_title="SpendSense",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Sidebar navigation ────────────────────────────────────────────────────────

st.sidebar.title("💰 SpendSense")
page = st.sidebar.radio("Navigate", ["Upload Statement", "Transactions", "Reports"])

st.sidebar.markdown("---")
st.sidebar.caption("Backend: FastAPI · AI: Ollama · DB: SQLite")


# ── Helpers ───────────────────────────────────────────────────────────────────

def api_get(path: str) -> dict | list | None:
    try:
        r = httpx.get(f"{API_BASE}{path}", timeout=30)
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        st.error("Cannot connect to backend. Is it running?  `uvicorn backend.main:app --reload`")
        return None
    except Exception as e:
        st.error(f"API error: {e}")
        return None


def api_post_file(path: str, file_bytes: bytes, filename: str, account_type: str = "credit_card") -> dict | None:
    try:
        r = httpx.post(
            f"{API_BASE}{path}",
            files={"file": (filename, file_bytes)},
            data={"account_type": account_type},
            timeout=120,  # AI categorization can take a while
        )
        if not r.is_success:
            # Show the actual error detail from the API, not just the status code
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            st.error(f"Upload failed ({r.status_code}): {detail}")
            return None
        return r.json()
    except httpx.ConnectError:
        st.error("Cannot connect to backend. Is it running?  `uvicorn backend.main:app --reload`")
        return None
    except Exception as e:
        st.error(f"Upload error: {e}")
        return None


def api_patch(path: str, data: dict) -> dict | None:
    try:
        r = httpx.patch(f"{API_BASE}{path}", json=data, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Update error: {e}")
        return None


CATEGORIES = [
    "Transportation", "Home", "Utilities", "Health",
    "Entertainment", "Miscellaneous", "Income", "Refund", "Payment",
]

SUBCATEGORIES: dict[str, list[str]] = {
    "Transportation": ["Auto Loan/Lease", "Gas", "Insurance", "Maintenance", "Registration", "Transit Pass", "Rental/Taxi", "Other"],
    "Home": ["Mortgage/EMI", "Rent", "Maintenance", "Insurance", "Furniture", "Household Supplies", "Groceries", "Real Estate Tax", "City Utilities", "Other"],
    "Utilities": ["Phone-Home", "Phone-Cell", "Cable", "Gas", "Water", "Electricity", "Internet", "Laundry", "Other"],
    "Health": ["Dental", "Medical", "Medication", "Vision", "Life Insurance", "Physical Therapy", "Other"],
    "Entertainment": ["Memberships", "Dining Out", "Subscriptions", "Movies", "Music", "Hobbies", "Travel", "Events", "Other"],
    "Miscellaneous": ["Dry Cleaning", "Clothing", "Donations", "Child Care", "Education/Tuition", "Personal Care", "Gifts", "Online Purchase", "Other"],
    "Income": ["Salary", "Tax Refund", "Other"],
    "Refund": ["Return", "Credit", "Other"],
    "Payment": ["Credit Card Payment", "Other"],
}

CATEGORY_COLORS = {
    "Transportation": "#e15759",
    "Home": "#4e79a7",
    "Utilities": "#76b7b2",
    "Health": "#f28e2b",
    "Entertainment": "#59a14f",
    "Miscellaneous": "#b07aa1",
    "Income": "#edc948",
    "Refund": "#9c755f",
    "Payment": "#bab0ac",
}


# ── Page: Upload ──────────────────────────────────────────────────────────────

if page == "Upload Statement":
    st.title("Upload Bank Statement")
    st.write("Upload a PDF or CSV from your bank. Transactions are parsed and categorized locally by Ollama — nothing leaves your machine.")

    account_type = st.radio(
        "Account type",
        options=["credit_card", "checking", "savings"],
        format_func=lambda x: {"credit_card": "Credit Card", "checking": "Checking", "savings": "Savings"}.get(x, x),
        horizontal=True,
        help="Credit card: credits are refunds, never income. Checking/Savings: deposits can be income.",
    )

    uploaded = st.file_uploader(
        "Drop your statement here",
        type=["pdf", "csv"],
        help="Supports most bank PDF/CSV formats",
    )

    if uploaded:
        st.info(f"File: **{uploaded.name}** ({uploaded.size / 1024:.1f} KB)")

        if st.button("Process Statement", type="primary"):
            with st.spinner("Parsing and categorizing with Ollama... (may take 30-60s)"):
                result = api_post_file("/upload", uploaded.getvalue(), uploaded.name, account_type)

            if result:
                st.success(f"Imported **{result['imported']}** transactions from `{result['file']}`")
                st.balloons()
                st.info("Go to the **Transactions** or **Reports** tab to view your data.")

    st.markdown("---")
    st.subheader("API Status")
    status = api_get("/")
    if status:
        st.success(f"Backend online — {status.get('service')} v{status.get('version')}")

    st.markdown("---")
    st.subheader("Danger Zone")
    with st.expander("Clear all transactions", expanded=False):
        st.warning("This permanently deletes all transactions from the database.")
        if st.button("Delete all transactions", type="primary", key="clear_all"):
            try:
                r = httpx.delete(f"{API_BASE}/transactions", timeout=10)
                if r.status_code == 204:
                    st.success("All transactions deleted. You can now re-upload your statements.")
                else:
                    st.error(f"Failed: {r.status_code}")
            except Exception as e:
                st.error(f"Error: {e}")


# ── Page: Transactions ────────────────────────────────────────────────────────

elif page == "Transactions":
    st.title("Transactions")

    data = api_get("/transactions?limit=500")
    if not data:
        st.info("No transactions yet. Upload a statement first.")
        st.stop()

    df = pd.DataFrame(data)
    if df.empty:
        st.info("No transactions yet. Upload a statement first.")
        st.stop()

    # Filters
    col1, col2 = st.columns(2)
    with col1:
        cat_filter = st.multiselect("Filter by category", CATEGORIES)
    with col2:
        show_unreviewed = st.checkbox("Show unreviewed only", value=False)

    if cat_filter:
        df = df[df["category"].isin(cat_filter)]
    if show_unreviewed:
        df = df[df["is_reviewed"] == False]

    st.caption(f"Showing {len(df)} transactions")

    # Editable table
    edited_df = st.data_editor(
        df[["id", "date", "merchant", "category", "subcategory", "amount", "is_reviewed"]],
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True),
            "date": st.column_config.TextColumn("Date", disabled=True),
            "merchant": st.column_config.TextColumn("Merchant"),
            "category": st.column_config.SelectboxColumn("Category", options=CATEGORIES),
            "subcategory": st.column_config.SelectboxColumn(
                "Subcategory",
                options=sorted({s for subs in SUBCATEGORIES.values() for s in subs}),
            ),
            "amount": st.column_config.NumberColumn("Amount", format="$%.2f"),
            "is_reviewed": st.column_config.CheckboxColumn("Reviewed"),
        },
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        key="tx_editor",
    )

    if st.button("Save Changes", type="primary"):
        changes = 0
        for _, orig_row in df.iterrows():
            new_row = edited_df[edited_df["id"] == orig_row["id"]].iloc[0]
            updates = {}
            if new_row["category"] != orig_row["category"]:
                updates["category"] = new_row["category"]
            if new_row["subcategory"] != orig_row.get("subcategory", ""):
                updates["subcategory"] = new_row["subcategory"]
            if new_row["merchant"] != orig_row["merchant"]:
                updates["merchant"] = new_row["merchant"]
            if new_row["is_reviewed"] != orig_row["is_reviewed"]:
                updates["is_reviewed"] = bool(new_row["is_reviewed"])
            if updates:
                api_patch(f"/transactions/{orig_row['id']}", updates)
                changes += 1

        if changes:
            st.success(f"Saved {changes} change(s).")
            st.rerun()
        else:
            st.info("No changes detected.")

    # ── Inline category/subcategory correction form ───────────────────────────
    st.markdown("---")
    with st.expander("Correct a Transaction's Category", expanded=False):
        st.caption("Use this to fix AI mis-classifications with a proper subcategory dropdown.")

        tx_ids = df["id"].tolist()
        tx_labels = {
            row["id"]: f"#{row['id']}  {row['date']}  {row['merchant']}  (${row['amount']:,.2f})"
            for _, row in df.iterrows()
        }

        selected_id = st.selectbox(
            "Select transaction",
            options=tx_ids,
            format_func=lambda x: tx_labels.get(x, str(x)),
        )

        if selected_id is not None:
            current_row = df[df["id"] == selected_id].iloc[0]
            current_cat = current_row["category"] if current_row["category"] in CATEGORIES else CATEGORIES[0]
            current_sub = current_row.get("subcategory", "") or ""

            col_a, col_b = st.columns(2)
            with col_a:
                new_cat = st.selectbox(
                    "Category",
                    options=CATEGORIES,
                    index=CATEGORIES.index(current_cat),
                    key="edit_cat",
                )
            with col_b:
                sub_options = SUBCATEGORIES.get(new_cat, ["Other"])
                default_sub_idx = sub_options.index(current_sub) if current_sub in sub_options else 0
                new_sub = st.selectbox(
                    "Subcategory",
                    options=sub_options,
                    index=default_sub_idx,
                    key="edit_sub",
                )

            new_merchant = st.text_input("Merchant name", value=current_row["merchant"], key="edit_merchant")
            mark_reviewed = st.checkbox("Mark as reviewed", value=bool(current_row["is_reviewed"]), key="edit_reviewed")

            if st.button("Apply Correction", type="primary", key="apply_edit"):
                updates = {
                    "category": new_cat,
                    "subcategory": new_sub,
                    "merchant": new_merchant,
                    "is_reviewed": mark_reviewed,
                }
                result = api_patch(f"/transactions/{selected_id}", updates)
                if result:
                    st.success(f"Updated transaction #{selected_id}: {new_cat} / {new_sub}")
                    st.rerun()


# ── Page: Reports ─────────────────────────────────────────────────────────────

elif page == "Reports":
    st.title("Reports")

    summary = api_get("/summary")
    monthly = api_get("/monthly")

    if not summary or summary.get("total_transactions", 0) == 0:
        st.info("No transactions yet. Upload a statement first.")
        st.stop()

    # KPI row
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Transactions", summary["total_transactions"])
    k2.metric("Total Spent", f"${summary['total_spent']:,.2f}")
    k3.metric("Total Income", f"${summary['total_income']:,.2f}")
    k4.metric(
        "Savings Rate",
        f"{summary['savings_rate_pct']}%",
        delta=f"{summary['savings_rate_pct']}%",
        delta_color="normal",
    )

    st.markdown("---")

    # Charts side by side
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Spending by Category")
        EXPENSE_CATEGORIES = {"Transportation", "Home", "Utilities", "Health", "Entertainment", "Miscellaneous"}
        cat_data = {
            k: abs(v)
            for k, v in summary["by_category"].items()
            if k in EXPENSE_CATEGORIES
        }
        if cat_data:
            pie_df = pd.DataFrame(
                {"Category": list(cat_data.keys()), "Amount": list(cat_data.values())}
            )
            fig_pie = px.pie(
                pie_df,
                names="Category",
                values="Amount",
                color="Category",
                color_discrete_map=CATEGORY_COLORS,
                hole=0.4,
            )
            fig_pie.update_traces(textposition="inside", textinfo="percent+label")
            st.plotly_chart(fig_pie, use_container_width=True)

    with col_right:
        st.subheader("Monthly Spending Trend")
        if monthly and monthly.get("monthly"):
            month_df = pd.DataFrame(
                {
                    "Month": list(monthly["monthly"].keys()),
                    "Spent": list(monthly["monthly"].values()),
                }
            )
            fig_bar = px.bar(
                month_df,
                x="Month",
                y="Spent",
                color_discrete_sequence=["#4e79a7"],
                labels={"Spent": "Amount Spent ($)"},
            )
            fig_bar.update_layout(xaxis_tickangle=-45, xaxis_type="category")
            st.plotly_chart(fig_bar, use_container_width=True)

    # Category + subcategory breakdown
    st.subheader("Category Breakdown")
    EXPENSE_CATEGORIES = {"Transportation", "Home", "Utilities", "Health", "Entertainment", "Miscellaneous"}
    by_sub = summary.get("by_subcategory", {})
    for cat, total in sorted(summary["by_category"].items(), key=lambda x: abs(x[1]), reverse=True):
        if cat in ("Payment", "Refund"):
            continue
        label = f"{'🔴' if cat in EXPENSE_CATEGORIES else '🟢'} **{cat}** — ${abs(total):,.2f}"
        with st.expander(label, expanded=False):
            sub_data = by_sub.get(cat, {})
            if sub_data:
                rows = [
                    {"Subcategory": s, "Amount": f"${abs(v):,.2f}"}
                    for s, v in sorted(sub_data.items(), key=lambda x: x[1])
                ]
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Payments / Refunds summary line
    excluded = {k: v for k, v in summary["by_category"].items() if k in ("Payment", "Refund")}
    if excluded:
        st.caption(
            "Excluded from totals: "
            + "  |  ".join(f"{k}: ${abs(v):,.2f}" for k, v in excluded.items())
        )

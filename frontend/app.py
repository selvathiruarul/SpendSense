"""
SpendSense Streamlit Dashboard
Run with: streamlit run frontend/app.py
"""
from __future__ import annotations

import json
import os

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode
load_dotenv()

API_BASE = os.environ.get("API_BASE", "http://localhost:8000")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

st.set_page_config(
    page_title="SpendSense",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Supabase auth via direct HTTP (no supabase-py needed) ────────────────────

def _sb_headers() -> dict:
    return {"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"}


def _sb_signup(email: str, password: str) -> dict:
    r = httpx.post(
        f"{SUPABASE_URL}/auth/v1/signup",
        headers=_sb_headers(),
        json={"email": email, "password": password},
        timeout=10,
    )
    return r.json(), r.is_success


def _sb_login(email: str, password: str) -> dict:
    r = httpx.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers=_sb_headers(),
        json={"email": email, "password": password},
        timeout=10,
    )
    return r.json(), r.is_success


def _sb_oauth_url(provider: str) -> str:
    return f"{SUPABASE_URL}/auth/v1/authorize?provider={provider}&redirect_to={API_BASE}"


# ── Auth gate — show login/signup if not authenticated ───────────────────────

def _show_auth_page():
    st.title("💰 SpendSense")
    st.subheader("Sign in to your account")

    tab_login, tab_signup = st.tabs(["Log in", "Sign up"])

    with tab_login:
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")
        if st.button("Log in", use_container_width=True):
            data, ok = _sb_login(email, password)
            if ok and data.get("access_token"):
                st.session_state["sb_session"] = data
                st.rerun()
            else:
                st.error(f"Login failed: {data.get('error_description') or data.get('msg') or data}")

        st.markdown("---")
        if st.button("Continue with Google", use_container_width=True):
            url = _sb_oauth_url("google")
            st.markdown(f'<meta http-equiv="refresh" content="0; url={url}">', unsafe_allow_html=True)

    with tab_signup:
        new_email = st.text_input("Email", key="signup_email")
        new_pass = st.text_input("Password (min 8 chars)", type="password", key="signup_password")
        if st.button("Create account", use_container_width=True):
            data, ok = _sb_signup(new_email, new_pass)
            if ok:
                st.success("Account created! Check your email to confirm, then log in.")
            else:
                st.error(f"Sign-up failed: {data.get('error_description') or data.get('msg') or data}")


# Dev mode: when SUPABASE_URL is not configured, skip auth entirely
_DEV_MODE: bool = not SUPABASE_URL

if _DEV_MODE:
    _access_token = "dev"
    _user_email = "local@dev"
    _is_paid = True
else:
    if "sb_session" not in st.session_state:
        _show_auth_page()
        st.stop()
    _session = st.session_state["sb_session"]
    _access_token = _session.get("access_token", "")
    _user_email = (_session.get("user") or {}).get("email", "")
    _is_paid = ((_session.get("user") or {}).get("user_metadata") or {}).get("is_paid", False) is True


# ── Sidebar navigation ────────────────────────────────────────────────────────

st.sidebar.title("💰 SpendSense")
page = st.sidebar.radio("Navigate", ["Dashboard", "Upload Statement", "Transactions", "Reports"])

st.sidebar.markdown("---")

if _DEV_MODE:
    st.sidebar.caption("Dev mode — auth disabled")
else:
    # Billing status
    if _is_paid:
        st.sidebar.success("Pro plan")
        if st.sidebar.button("Manage billing"):
            portal = httpx.get(
                f"{API_BASE}/billing/portal",
                headers={"Authorization": f"Bearer {_access_token}"},
                timeout=10,
            )
            if portal.is_success:
                st.sidebar.markdown(f'[Open Stripe portal]({portal.json()["portal_url"]})')
    else:
        st.sidebar.warning("Free plan — 3 uploads/month")
        if st.sidebar.button("Upgrade to Pro — $4.99/mo"):
            checkout = httpx.post(
                f"{API_BASE}/billing/checkout",
                headers={"Authorization": f"Bearer {_access_token}"},
                timeout=60,
            )
            if checkout.is_success:
                url = checkout.json()["checkout_url"]
                st.sidebar.markdown(f'<a href="{url}" target="_blank">Click here to complete payment</a>', unsafe_allow_html=True)

    st.sidebar.markdown("---")
    st.sidebar.caption(f"Signed in as {_user_email}")
    if st.sidebar.button("Sign out"):
        del st.session_state["sb_session"]
        st.rerun()

st.sidebar.caption("Backend: FastAPI · AI: Ollama · DB: PostgreSQL")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_access_token}"}


def api_get(path: str) -> dict | list | None:
    try:
        r = httpx.get(f"{API_BASE}{path}", headers=_auth_headers(), timeout=60)
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        st.error("Cannot connect to backend. Is it running?  `uvicorn backend.main:app --reload`")
        return None
    except Exception as e:
        st.error(f"API error: {e}")
        return None


def api_post_file(path: str, file_bytes: bytes, filename: str, account_type: str = "credit_card", account: str = "") -> dict | None:
    try:
        r = httpx.post(
            f"{API_BASE}{path}",
            headers=_auth_headers(),
            files={"file": (filename, file_bytes)},
            data={"account_type": account_type, "account": account},
            timeout=120,  # AI categorization can take a while
        )
        if not r.is_success:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            if r.status_code == 402:
                st.warning(f"{detail}  Use the sidebar to upgrade.")
            else:
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
        r = httpx.patch(f"{API_BASE}{path}", headers=_auth_headers(), json=data, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Update error: {e}")
        return None


CATEGORIES = [
    "Transportation", "Home", "Utilities", "Health",
    "Entertainment", "Miscellaneous", "Income", "Investment", "Refund", "Payment",
]

SUBCATEGORIES: dict[str, list[str]] = {
    "Transportation": ["Auto Loan/Lease", "Gas", "Insurance", "Maintenance", "Registration", "Transit Pass", "Rental/Taxi", "Other"],
    "Home": ["Mortgage/EMI", "Rent", "Maintenance", "Insurance", "Furniture", "Household Supplies", "Groceries", "Real Estate Tax", "City Utilities", "Other"],
    "Utilities": ["Phone-Home", "Phone-Cell", "Cable", "Gas", "Water", "Electricity", "Internet", "Laundry", "Other"],
    "Health": ["Dental", "Medical", "Medication", "Vision", "Life Insurance", "Physical Therapy", "Other"],
    "Entertainment": ["Memberships", "Dining Out", "Subscriptions", "Movies", "Music", "Hobbies", "Travel", "Events", "Other"],
    "Miscellaneous": ["Dry Cleaning", "Clothing", "Donations", "Child Care", "Education/Tuition", "Personal Care", "Gifts", "Online Purchase", "Other"],
    "Income": ["Salary", "Tax Refund", "Other"],
    "Investment": ["Brokerage Transfer", "Retirement", "Savings Transfer", "Other"],
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
    "Investment": "#17becf",
    "Refund": "#9c755f",
    "Payment": "#bab0ac",
}


# ── Page: Dashboard ───────────────────────────────────────────────────────────

if page == "Dashboard":
    from datetime import date as _dash_date
    import calendar as _dash_cal

    today = _dash_date.today()
    st.title("Dashboard")

    # Year / Month filter
    _dash_years = list(range(2020, today.year + 1))
    _dash_months = {i: _dash_cal.month_name[i] for i in range(1, 13)}
    df1, df2 = st.columns([1, 1])
    with df1:
        sel_year = st.selectbox("Year", _dash_years,
                                index=_dash_years.index(today.year), key="dash_year")
    with df2:
        sel_month = st.selectbox("Month", [0] + list(range(1, 13)),
                                 format_func=lambda m: "All months" if m == 0 else _dash_months[m],
                                 index=today.month, key="dash_month")
    month_name = "All months" if sel_month == 0 else _dash_cal.month_name[sel_month]

    if sel_month:
        summary_cur = api_get(f"/summary?year={sel_year}&month={sel_month}") or {}
    else:
        summary_cur = api_get(f"/summary?year={sel_year}") or {}
    monthly = api_get("/monthly") or {}
    budgets = api_get("/budgets") or []
    all_txs = api_get("/transactions?limit=2000") or []

    income = summary_cur.get("total_income", 0.0)
    expenses = summary_cur.get("total_spent", 0.0)
    savings = income - expenses
    savings_pct = round(savings / income * 100, 1) if income > 0 else 0.0
    tx_count = summary_cur.get("total_transactions", 0)

    period_label = f"{month_name} {sel_year}" if sel_month else str(sel_year)
    st.subheader(f"{period_label} — At a Glance")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Income", f"${income:,.2f}")
    k2.metric("Expenses", f"${expenses:,.2f}", delta_color="inverse",
              delta=f"{round(expenses/income*100,1) if income else 0}% of income")
    k3.metric("Savings", f"${savings:,.2f}", delta=f"{savings_pct}% of income", delta_color="normal")
    k4.metric("Transactions", tx_count)

    col_left, col_right = st.columns(2)
    EXPENSE_CATS = {"Transportation", "Home", "Utilities", "Health", "Entertainment", "Miscellaneous"}

    with col_left:
        st.subheader(f"Spending — {period_label}")
        cat_data = {k: abs(v) for k, v in summary_cur.get("by_category", {}).items() if k in EXPENSE_CATS}
        if cat_data:
            pie_df = pd.DataFrame({"Category": list(cat_data.keys()), "Amount": list(cat_data.values())})
            fig_pie = px.pie(pie_df, names="Category", values="Amount", color="Category",
                             color_discrete_map=CATEGORY_COLORS, hole=0.4)
            fig_pie.update_traces(textposition="inside", textinfo="percent+label")
            fig_pie.update_layout(margin=dict(t=20, b=0, l=0, r=0), height=280)
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("No expense data for this month.")

    with col_right:
        st.subheader("Monthly Spending Trend")
        if monthly.get("monthly"):
            m_df = pd.DataFrame({"Month": list(monthly["monthly"].keys()), "Spent": list(monthly["monthly"].values())})
            fig_bar = px.bar(m_df, x="Month", y="Spent", color_discrete_sequence=["#4e79a7"],
                             labels={"Spent": "Amount ($)"})
            fig_bar.update_layout(xaxis_tickangle=-45, xaxis_type="category", margin=dict(t=20), height=280)
            st.plotly_chart(fig_bar, use_container_width=True)

    # Budget health strip
    budget_map_dash = {b["category"]: b for b in budgets}
    savings_target_pct = budget_map_dash.get("Savings", {}).get("percentage", 0)
    if savings_target_pct and income > 0:
        st.markdown("---")
        st.subheader("Budget Health")
        expense_target_pct = 100 - savings_target_pct
        actual_expense_pct = round(expenses / income * 100, 1)
        actual_savings_pct = round(savings / income * 100, 1)
        for label, actual_pct, target_pct, higher in [
            ("Expenses", actual_expense_pct, expense_target_pct, False),
            ("Savings",  actual_savings_pct,  savings_target_pct,  True),
        ]:
            on_track = (actual_pct >= target_pct) if higher else (actual_pct <= target_pct)
            bar_color = "#59a14f" if on_track else "#e15759"
            bar_width = min(round(actual_pct / target_pct * 100) if target_pct else 0, 100)
            icon = "✅" if on_track else "🔴"
            c1, c2, c3 = st.columns([2, 5, 3])
            c1.markdown(f"**{label}** (target {target_pct:.0f}%)")
            c2.markdown(
                f'<div style="background:#eee;border-radius:4px;height:18px;margin-top:6px">'
                f'<div style="background:{bar_color};width:{bar_width}%;height:100%;border-radius:4px"></div></div>',
                unsafe_allow_html=True,
            )
            c3.markdown(f"{icon} **{actual_pct}%** of income")

    # Transactions for selected period
    st.markdown("---")
    st.subheader(f"Transactions — {period_label}")
    if all_txs:
        if sel_month:
            period_prefix = f"{sel_year}-{sel_month:02d}"
            period_txs = [t for t in all_txs if t["date"].startswith(period_prefix)]
        else:
            period_txs = [t for t in all_txs if t["date"].startswith(str(sel_year))]
        recent = sorted(period_txs, key=lambda x: x["date"], reverse=True)
        if recent:
            r_df = pd.DataFrame(recent)[["date", "merchant", "category", "amount", "account"]]
            r_df["amount"] = r_df["amount"].apply(lambda x: f"${abs(x):,.2f}" if x < 0 else f"+${x:,.2f}")
            st.dataframe(r_df, use_container_width=True, hide_index=True)
        else:
            st.info(f"No transactions for {period_label}.")
    else:
        st.info("No transactions yet. Upload a statement to get started.")


# ── Page: Upload ──────────────────────────────────────────────────────────────

elif page == "Upload Statement":
    st.title("Upload Bank Statement")
    st.write("Upload a PDF or CSV from your bank. Transactions are parsed and categorized locally by Ollama — nothing leaves your machine.")

    with st.form("upload_form"):
        ua_col, ub_col = st.columns(2)
        with ua_col:
            account_type = st.radio(
                "Account type",
                options=["credit_card", "checking", "savings"],
                format_func=lambda x: {"credit_card": "Credit Card", "checking": "Checking", "savings": "Savings"}.get(x, x),
                horizontal=True,
                help="Credit card: credits are refunds, never income. Checking/Savings: deposits can be income.",
            )
        with ub_col:
            account_name = st.text_input(
                "Account name",
                placeholder="e.g. Chase Checking, Amex Blue Cash",
                help="Used to identify the source of transactions",
            )

        uploaded = st.file_uploader(
            "Drop your statement here",
            type=["pdf", "csv"],
            help="Supports most bank PDF/CSV formats",
        )

        submitted = st.form_submit_button("Process Statement", type="primary")

    if submitted:
        if not uploaded:
            st.warning("Please select a file first.")
        else:
            st.info(f"File: **{uploaded.name}** ({uploaded.size / 1024:.1f} KB)")
            with st.spinner("Parsing and categorizing with Ollama... (may take 30-60s)"):
                result = api_post_file("/upload", uploaded.getvalue(), uploaded.name, account_type, account_name)

            if result:
                st.success(f"Imported **{result['imported']}** transactions from `{result['file']}`")
                if result.get("skipped_duplicates", 0) > 0:
                    st.info(f"Skipped **{result['skipped_duplicates']}** duplicate transaction(s) already in the database.")
                st.balloons()
                st.info("Go to the **Transactions** or **Reports** tab to view your data.")

    st.markdown("---")
    st.subheader("Assign Account to Existing Transactions")
    st.caption("Tag transactions by category or merchant — e.g. all Amazon → Amex, all Groceries → Chase Checking. Leave a filter blank to match all.")
    with st.form("bulk_account_form"):
        ba1, ba2, ba3 = st.columns(3)
        with ba1:
            ba_category = st.selectbox("Filter by category", [""] + CATEGORIES,
                                       format_func=lambda c: "All categories" if c == "" else c,
                                       key="ba_category")
        with ba2:
            ba_merchant = st.text_input("Filter by merchant", placeholder="e.g. Amazon, Whole Foods",
                                        key="ba_merchant")
        with ba3:
            ba_account = st.text_input("Assign account name", placeholder="e.g. Chase Checking",
                                       key="ba_account")
        ba_submitted = st.form_submit_button("Assign Account")

    if ba_submitted:
        if not ba_account.strip():
            st.warning("Enter an account name.")
        elif not ba_category and not ba_merchant.strip():
            st.warning("Set at least one filter (category or merchant) to avoid tagging everything.")
        else:
            params: dict = {"account": ba_account.strip()}
            if ba_category:
                params["category"] = ba_category
            if ba_merchant.strip():
                params["merchant"] = ba_merchant.strip()
            try:
                r = httpx.patch(f"{API_BASE}/transactions/bulk-account", params=params, timeout=10)
                if r.is_success:
                    n = r.json()['updated']
                    st.success(f"Updated {n} transactions → **{ba_account.strip()}**")
                else:
                    st.error(f"Failed: {r.status_code}")
            except Exception as e:
                st.error(f"Error: {e}")

    st.markdown("---")
    st.subheader("Debug Parse (Diagnose Upload Issues)")
    st.caption("Use this if a file fails to import — shows exactly what the parser extracted before any DB write.")
    with st.form("debug_form"):
        debug_file = st.file_uploader("Upload file to debug", type=["pdf", "csv"], key="debug_upload")
        debug_submitted = st.form_submit_button("Run Debug Parse")

    if debug_submitted and debug_file:
        try:
            r = httpx.post(
                f"{API_BASE}/debug-parse",
                headers=_auth_headers(),
                files={"file": (debug_file.name, debug_file.getvalue())},
                timeout=60,
            )
            if r.is_success:
                result = r.json()
                st.success(f"Parser found **{result['transactions_found']}** transactions")
                if result.get("first_5"):
                    st.dataframe(pd.DataFrame(result["first_5"]), use_container_width=True)
                if result.get("raw_pages"):
                    for pg in result["raw_pages"]:
                        with st.expander(f"Page {pg['page']} — {pg['tables_found']} table(s) found"):
                            st.text(pg["text_preview"])
            else:
                st.error(f"Debug failed: {r.status_code} — {r.text}")
        except Exception as e:
            st.error(f"Error: {e}")

    st.markdown("---")
    st.subheader("API Status")
    status = api_get("/")
    if status:
        st.success(f"Backend online — {status.get('service')} v{status.get('version')}")

    st.markdown("---")
    st.subheader("Danger Zone")
    with st.expander("Delete transactions", expanded=False):
        st.warning("Permanently deletes matching transactions. This cannot be undone.")

        import calendar as _cal
        from datetime import date as _date

        dz1, dz2, dz3 = st.columns(3)
        with dz1:
            del_year = st.selectbox(
                "Year",
                [0] + list(range(2020, _date.today().year + 1)),
                format_func=lambda y: "All years" if y == 0 else str(y),
                key="del_year",
            )
        with dz2:
            del_month = st.selectbox(
                "Month",
                [0] + list(range(1, 13)),
                format_func=lambda m: "All months" if m == 0 else _cal.month_name[m],
                key="del_month",
            )
        with dz3:
            # pull distinct accounts from the API for the dropdown
            _all_txs = api_get("/transactions?limit=2000") or []
            _accounts = sorted({t["account"] for t in _all_txs if t.get("account")})
            del_account = st.selectbox("Account", ["All accounts"] + _accounts, key="del_account")

        # Build human-readable summary of what will be deleted
        _parts = []
        if del_year:
            _parts.append(str(del_year))
        if del_month:
            _parts.append(_cal.month_name[del_month])
        if del_account != "All accounts":
            _parts.append(del_account)
        _scope = " · ".join(_parts) if _parts else "ALL transactions"

        if st.button(f"Delete: {_scope}", type="primary", key="clear_filtered"):
            params: dict = {}
            if del_year:
                params["year"] = del_year
            if del_month:
                params["month"] = del_month
            if del_account != "All accounts":
                params["account"] = del_account
            try:
                r = httpx.delete(f"{API_BASE}/transactions", headers=_auth_headers(), params=params, timeout=10)
                if r.is_success:
                    st.success(f"Deleted {r.json()['deleted']} transactions.")
                else:
                    st.error(f"Failed: {r.status_code}")
            except Exception as e:
                st.error(f"Error: {e}")


# ── Page: Transactions ────────────────────────────────────────────────────────

elif page == "Transactions":
    st.title("Transactions")

    data = api_get("/transactions?limit=2000")
    if not data:
        st.info("No transactions yet. Upload a statement first.")
        st.stop()

    df = pd.DataFrame(data)
    if df.empty:
        st.info("No transactions yet. Upload a statement first.")
        st.stop()

    df["date"] = pd.to_datetime(df["date"])

    # Filters
    import calendar as _cal
    col1, col2, col3, col4, col5 = st.columns([1, 1, 1, 2, 1])
    with col1:
        years = sorted(df["date"].dt.year.unique().tolist(), reverse=True)
        filter_year = st.selectbox("Year", [0] + years, format_func=lambda y: "All years" if y == 0 else str(y))
    with col2:
        months_available = sorted(df["date"].dt.month.unique().tolist())
        filter_month = st.selectbox("Month", [0] + months_available,
                                    format_func=lambda m: "All months" if m == 0 else _cal.month_name[m])
    with col3:
        accounts_available = sorted(df["account"].dropna().unique().tolist())
        filter_account = st.selectbox("Account", ["All"] + accounts_available)
    with col4:
        cat_filter = st.multiselect("Category", CATEGORIES)
    with col5:
        show_unreviewed = st.checkbox("Unreviewed only", value=False)

    if filter_year:
        df = df[df["date"].dt.year == filter_year]
    if filter_month:
        df = df[df["date"].dt.month == filter_month]
    if filter_account != "All":
        df = df[df["account"] == filter_account]
    if cat_filter:
        df = df[df["category"].isin(cat_filter)]
    if show_unreviewed:
        df = df[df["is_reviewed"] == False]

    # Search
    search = st.text_input("Search merchant / description", placeholder="e.g. Amazon, Whole Foods", key="tx_search")
    if search.strip():
        mask = (
            df["merchant"].str.contains(search.strip(), case=False, na=False) |
            df["raw_desc"].str.contains(search.strip(), case=False, na=False)
        )
        df = df[mask]

    df["date"] = df["date"].dt.strftime("%Y-%m-%d")

    st.caption(f"Showing {len(df)} transactions · Double-click a cell to edit")

    if "notes" not in df.columns:
        df["notes"] = ""
    grid_df = df[["id", "date", "account", "merchant", "raw_desc", "notes", "category", "subcategory", "amount", "is_reviewed"]].copy()

    # JS function: subcategory options depend on the category value in the same row
    subcat_params = JsCode(f"""
    function(params) {{
        const map = {json.dumps(SUBCATEGORIES)};
        const cat = params.data.category;
        return {{ values: map[cat] || ['Other'] }};
    }}
    """)

    gb = GridOptionsBuilder.from_dataframe(grid_df)
    gb.configure_column("id", header_name="ID", editable=False, width=70)
    gb.configure_column("date", header_name="Date", editable=False, width=110)
    gb.configure_column("account", header_name="Account", editable=True, width=160)
    gb.configure_column("merchant", header_name="Merchant", editable=True, flex=2)
    gb.configure_column("raw_desc", header_name="Description", editable=False, flex=2)
    gb.configure_column("notes", header_name="Notes", editable=True, flex=1)
    gb.configure_column(
        "category",
        header_name="Category",
        editable=True,
        cellEditor="agSelectCellEditor",
        cellEditorParams={"values": CATEGORIES},
        flex=1,
    )
    gb.configure_column(
        "subcategory",
        header_name="Subcategory",
        editable=True,
        cellEditor="agSelectCellEditor",
        cellEditorParams=subcat_params,
        flex=1,
    )
    gb.configure_column(
        "amount",
        header_name="Amount",
        editable=True,
        width=110,
        valueFormatter="'$' + parseFloat(value).toFixed(2)",
    )
    gb.configure_column(
        "is_reviewed",
        header_name="Reviewed",
        editable=True,
        cellRenderer="agCheckboxCellRenderer",
        cellEditor="agCheckboxCellEditor",
        width=100,
    )
    gb.configure_selection(selection_mode="multiple", use_checkbox=True)
    gb.configure_grid_options(stopEditingWhenCellsLoseFocus=True)

    grid_response = AgGrid(
        grid_df,
        gridOptions=gb.build(),
        update_mode=GridUpdateMode.MODEL_CHANGED,
        allow_unsafe_jscode=True,
        use_container_width=True,
        theme="streamlit",
        height=450,
    )

    btn_col1, btn_col2 = st.columns([1, 5])
    with btn_col1:
        delete_clicked = st.button("Delete Selected", type="secondary")
    with btn_col2:
        save_clicked = st.button("Save Changes", type="primary")

    if delete_clicked:
        selected = grid_response.get("selected_rows")
        if selected is None or (hasattr(selected, "__len__") and len(selected) == 0):
            st.warning("Select one or more rows first (use the checkboxes).")
        else:
            selected_list = selected if isinstance(selected, list) else selected.to_dict("records")
            deleted = 0
            for row in selected_list:
                try:
                    r = httpx.delete(f"{API_BASE}/transactions/{int(row['id'])}", headers=_auth_headers(), timeout=10)
                    if r.status_code == 204:
                        deleted += 1
                except Exception:
                    pass
            if deleted:
                st.success(f"Deleted {deleted} transaction(s).")
                st.rerun()

    if save_clicked:
        edited_df = grid_response["data"]
        changes = 0
        for _, orig_row in df.iterrows():
            new_rows = edited_df[edited_df["id"] == orig_row["id"]]
            if new_rows.empty:
                continue
            new_row = new_rows.iloc[0]
            updates = {}
            if new_row["category"] != orig_row["category"]:
                updates["category"] = new_row["category"]
            if str(new_row.get("subcategory", "")) != str(orig_row.get("subcategory", "") or ""):
                updates["subcategory"] = new_row["subcategory"]
            if new_row["merchant"] != orig_row["merchant"]:
                updates["merchant"] = new_row["merchant"]
            if str(new_row.get("account", "") or "") != str(orig_row.get("account", "") or ""):
                updates["account"] = new_row["account"] or None
            if round(float(new_row["amount"]), 2) != round(float(orig_row["amount"]), 2):
                updates["amount"] = float(new_row["amount"])
            if str(new_row.get("notes", "") or "") != str(orig_row.get("notes", "") or ""):
                updates["notes"] = new_row.get("notes") or None
            if bool(new_row["is_reviewed"]) != bool(orig_row["is_reviewed"]):
                updates["is_reviewed"] = bool(new_row["is_reviewed"])
            if updates:
                api_patch(f"/transactions/{orig_row['id']}", updates)
                changes += 1

        if changes:
            st.success(f"Saved {changes} change(s).")
            st.rerun()
        else:
            st.info("No changes detected.")

    # ── Add Transaction ───────────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("Add Transaction Manually", expanded=False):
        st.caption("Log cash purchases or any transaction not in a bank statement.")
        from datetime import date as _date
        with st.form("add_tx_form"):
            at1, at2, at3 = st.columns([1, 2, 1])
            with at1:
                at_date = st.date_input("Date", value=_date.today(), key="at_date")
            with at2:
                at_merchant = st.text_input("Merchant", placeholder="e.g. Corner Coffee Shop", key="at_merchant")
            with at3:
                at_amount = st.number_input("Amount ($)", step=0.01, format="%.2f", key="at_amount",
                                            help="Negative = expense, positive = income")
            at4, at5, at6 = st.columns([2, 2, 2])
            with at4:
                at_cat = st.selectbox("Category", CATEGORIES, key="at_cat")
            with at5:
                at_sub_opts = SUBCATEGORIES.get(at_cat, ["Other"])
                at_sub = st.selectbox("Subcategory", at_sub_opts, key="at_sub")
            with at6:
                at_account = st.text_input("Account", placeholder="e.g. Cash, Wallet", key="at_account")
            at_note = st.text_input("Note (optional)", placeholder="Any extra detail", key="at_note")
            at_submitted = st.form_submit_button("Add Transaction", type="primary")

        if at_submitted:
            if not at_merchant.strip():
                st.error("Merchant name is required.")
            elif at_amount == 0:
                st.warning("Amount is 0 — are you sure?")
            else:
                payload = {
                    "date": at_date.isoformat(),
                    "merchant": at_merchant.strip(),
                    "amount": at_amount,
                    "category": at_cat,
                    "subcategory": at_sub,
                    "account": at_account.strip() or None,
                    "note": at_note.strip() or None,
                }
                try:
                    r = httpx.post(f"{API_BASE}/transactions", headers=_auth_headers(), json=payload, timeout=10)
                    if r.is_success:
                        st.success(f"Added: {at_merchant.strip()} · ${at_amount:,.2f}")
                        st.rerun()
                    else:
                        st.error(f"Failed: {r.status_code} — {r.text}")
                except Exception as e:
                    st.error(f"Error: {e}")

    # ── Learned Rules ─────────────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("Learned Rules", expanded=False):
        st.caption("Rules are saved automatically when you mark a transaction as Reviewed. Future uploads and similar existing transactions apply these rules instantly — no AI needed.")
        rules = api_get("/rules")
        if rules:
            rules_df = pd.DataFrame(rules)[["id", "merchant", "category", "subcategory", "created_at"]]
            st.dataframe(rules_df, use_container_width=True, hide_index=True)
            del_id = st.number_input("Delete rule by ID", min_value=1, step=1, key="del_rule_id")
            if st.button("Delete Rule", key="del_rule_btn"):
                try:
                    r = httpx.delete(f"{API_BASE}/rules/{int(del_id)}", headers=_auth_headers(), timeout=10)
                    if r.status_code == 204:
                        st.success(f"Rule #{int(del_id)} deleted.")
                        st.rerun()
                    else:
                        st.error(f"Failed: {r.status_code}")
                except Exception as e:
                    st.error(f"Error: {e}")
        else:
            st.info("No rules yet. Mark a corrected transaction as Reviewed to create one.")


# ── Page: Reports ─────────────────────────────────────────────────────────────

elif page == "Reports":
    st.title("Reports")

    # ── Month filter ──────────────────────────────────────────────────────────
    from datetime import date as _date
    import calendar as _calendar

    _MONTH_NAMES = {i: _calendar.month_name[i] for i in range(1, 13)}
    fc1, fc2, fc3 = st.columns([1, 1, 2])
    with fc1:
        filter_year = st.selectbox("Year", options=list(range(2023, _date.today().year + 1)), index=list(range(2023, _date.today().year + 1)).index(_date.today().year))
    with fc2:
        filter_month = st.selectbox("Month", options=[0] + list(range(1, 13)),
                                    format_func=lambda m: "All months" if m == 0 else _MONTH_NAMES[m])

    if filter_month:
        summary_url = f"/summary?year={filter_year}&month={filter_month}"
        period_label = f"{_MONTH_NAMES[filter_month]} {filter_year}"
    else:
        summary_url = f"/summary?year={filter_year}"
        period_label = str(filter_year)

    summary = api_get(summary_url)
    monthly = api_get("/monthly")

    if not summary or summary.get("total_transactions", 0) == 0:
        st.info(f"No transactions for {period_label}. Upload a statement first.")
        st.stop()

    st.caption(f"Showing: **{period_label}**")

    # ── Top-level Budget Health ────────────────────────────────────────────────
    _budgets_top = api_get("/budgets") or []
    _budget_map_top = {b["category"]: b for b in _budgets_top}
    _total_income_top = summary.get("total_income", 0)
    _total_spent_top = summary.get("total_spent", 0)
    _actual_savings_top = _total_income_top - _total_spent_top
    _savings_rate = summary["savings_rate_pct"]

    _savings_target_pct = _budget_map_top.get("Savings", {}).get("percentage", 0)
    _expense_target_pct = 100 - _savings_target_pct if _savings_target_pct else 0
    _actual_expense_pct = round(_total_spent_top / _total_income_top * 100, 1) if _total_income_top > 0 else 0
    _actual_savings_pct = round(_actual_savings_top / _total_income_top * 100, 1) if _total_income_top > 0 else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Transactions", summary["total_transactions"])
    k2.metric("Total Income", f"${_total_income_top:,.2f}")
    k3.metric(
        "Total Expenses",
        f"${_total_spent_top:,.2f}",
        delta=f"{_actual_expense_pct}% of income" + (f" · target {_expense_target_pct}%" if _expense_target_pct else ""),
        delta_color="inverse",
    )
    k4.metric(
        "Savings",
        f"${_actual_savings_top:,.2f}",
        delta=f"{_actual_savings_pct}% of income" + (f" · target {_savings_target_pct}%" if _savings_target_pct else ""),
        delta_color="normal",
    )

    # Expense & Savings progress bars (only shown when budget targets are set)
    if _savings_target_pct and _total_income_top > 0:
        st.markdown("**Monthly Budget Health**")
        for label, actual_pct, target_pct, higher_is_better in [
            ("Expenses", _actual_expense_pct, _expense_target_pct, False),
            ("Savings",  _actual_savings_pct,  _savings_target_pct,  True),
        ]:
            # Expenses: green = under target, red = over target
            # Savings:  green = at/above target, red = below target
            on_track = (actual_pct <= target_pct) if not higher_is_better else (actual_pct >= target_pct)
            bar_color = "#59a14f" if on_track else "#e15759"
            bar_width = min(round(actual_pct / target_pct * 100) if target_pct else 0, 100)
            icon = "✅" if on_track else "🔴"
            c1, c2, c3 = st.columns([2, 5, 3])
            c1.markdown(f"**{label}** (target {target_pct:.0f}%)")
            c2.markdown(
                f'<div style="background:#eee;border-radius:4px;height:18px;margin-top:6px">'
                f'<div style="background:{bar_color};width:{bar_width}%;height:100%;border-radius:4px"></div></div>',
                unsafe_allow_html=True,
            )
            c3.markdown(f"{icon} **{actual_pct}%** of income", unsafe_allow_html=True)

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
        if cat in ("Payment", "Refund", "Investment"):
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
    excluded = {k: v for k, v in summary["by_category"].items() if k in ("Payment", "Refund", "Investment")}
    if excluded:
        st.caption(
            "Excluded from totals: "
            + "  |  ".join(f"{k}: ${abs(v):,.2f}" for k, v in excluded.items())
        )

    # ── Budget vs Actual (category detail) ────────────────────────────────────
    st.markdown("---")
    st.subheader("Budget vs Actual by Category")
    budgets = api_get("/budgets") or []
    budget_map = {b["category"]: b for b in budgets}

    EXPENSE_CATEGORIES_LIST = ["Transportation", "Home", "Utilities", "Health", "Entertainment", "Miscellaneous"]
    total_income = summary.get("total_income", 0)

    if budgets and total_income > 0:
        # Build rows: expense categories + Savings pseudo-row
        actual_total_expense = summary.get("total_spent", 0)
        actual_savings = total_income - actual_total_expense

        all_budget_cats = EXPENSE_CATEGORIES_LIST + ["Savings"]
        for cat in all_budget_cats:
            b = budget_map.get(cat)
            if not b:
                continue
            target_pct = b["percentage"]
            target_amt = total_income * target_pct / 100
            if cat == "Savings":
                actual_amt = actual_savings
            else:
                actual_amt = abs(summary["by_category"].get(cat, 0))
            used_pct = round(actual_amt / target_amt * 100) if target_amt else 0

            # For Savings: higher is better (green when at/above target)
            # For expenses: lower is better (green when at/below target)
            if cat == "Savings":
                bar_color = "#59a14f" if used_pct >= 90 else "#e15759"
                status_icon = "✅" if used_pct >= 100 else "🔴"
            else:
                bar_color = "#59a14f" if used_pct <= 90 else ("#f28e2b" if used_pct <= 110 else "#e15759")
                status_icon = "✅" if used_pct <= 100 else "🔴"
            bar_width = min(used_pct, 100)

            c_label, c_bar, c_nums = st.columns([2, 4, 3])
            c_label.markdown(f"**{cat}** ({target_pct:.0f}%)")
            c_bar.markdown(
                f'<div style="background:#eee;border-radius:4px;height:18px;margin-top:6px">'
                f'<div style="background:{bar_color};width:{bar_width}%;height:100%;border-radius:4px"></div></div>',
                unsafe_allow_html=True,
            )
            c_nums.markdown(
                f"{status_icon} **${actual_amt:,.0f}** / ${target_amt:,.0f} &nbsp;({used_pct}%)",
                unsafe_allow_html=True,
            )

        # Show total % allocated
        total_alloc = sum(b["percentage"] for b in budgets)
        if abs(total_alloc - 100) > 1:
            st.warning(f"Budgets sum to {total_alloc:.0f}% (should be 100%)")
        else:
            st.caption(f"Total allocated: {total_alloc:.0f}% of income")
    elif not budgets:
        st.caption("No budgets set yet — configure them below.")
    else:
        st.caption("No income data for this period — budget % targets need income to calculate dollar targets.")

    with st.expander("Set Budget Percentages", expanded=not bool(budgets)):
        st.caption(
            "Set what % of your monthly income each bucket should get. "
            "Start with your **Savings** target — the rest is your expense budget. Total should = 100%."
        )
        with st.form("budget_form"):
            # Savings target — most prominent, shown on its own row
            sav_b = budget_map.get("Savings")
            sav_cur = sav_b["percentage"] if sav_b else 30.0
            savings_pct = st.number_input(
                "💰 Savings target %", value=float(sav_cur), min_value=0.0, max_value=100.0,
                step=1.0, format="%.0f", key="budget_Savings",
                help="e.g. 30 means save 30% of income every month"
            )
            st.caption(f"Remaining for expenses: **{100 - savings_pct:.0f}%** of income")
            st.markdown("**Optional: break down expenses by category** *(leave at 0 to skip)*")

            bc1, bc2 = st.columns(2)
            budget_inputs = {"Savings": savings_pct}
            for i, cat in enumerate(EXPENSE_CATEGORIES_LIST):
                b = budget_map.get(cat)
                cur = b["percentage"] if b else 0.0
                col = bc1 if i % 2 == 0 else bc2
                budget_inputs[cat] = col.number_input(
                    f"{cat} %", value=float(cur), min_value=0.0, max_value=100.0,
                    step=1.0, format="%.0f", key=f"budget_{cat}"
                )
            total_pct = sum(budget_inputs.values())
            total_expense_alloc = total_pct - savings_pct
            st.markdown(
                f"**Total: {total_pct:.0f}%** &nbsp; (savings {savings_pct:.0f}% + expenses {total_expense_alloc:.0f}%) "
                f"{'✅' if abs(total_pct - 100) <= 1 else '⚠️ should be 100%'}",
                unsafe_allow_html=True,
            )
            if st.form_submit_button("Save Budget Plan", type="primary"):
                for cat, pct in budget_inputs.items():
                    if pct > 0:
                        httpx.post(f"{API_BASE}/budgets", headers=_auth_headers(), json={"category": cat, "percentage": pct}, timeout=5)
                    else:
                        b = budget_map.get(cat)
                        if b:
                            httpx.delete(f"{API_BASE}/budgets/{b['id']}", headers=_auth_headers(), timeout=5)
                st.success("Budget plan saved.")
                st.rerun()

    # ── Income Breakdown ──────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Income")
    income_data = api_get(f"/income?year={filter_year}") or {}
    if income_data.get("total", 0) > 0:
        inc1, inc2 = st.columns(2)
        with inc1:
            if income_data.get("by_month"):
                inc_df = pd.DataFrame({
                    "Month": list(income_data["by_month"].keys()),
                    "Income": list(income_data["by_month"].values()),
                })
                fig_inc = px.bar(inc_df, x="Month", y="Income", color_discrete_sequence=["#59a14f"],
                                 labels={"Income": "Amount ($)"}, title=f"Monthly Income — {filter_year}")
                fig_inc.update_layout(xaxis_tickangle=-45, xaxis_type="category", margin=dict(t=40))
                inc1.plotly_chart(fig_inc, use_container_width=True)
        with inc2:
            if income_data.get("by_source"):
                src_df = pd.DataFrame({
                    "Source": list(income_data["by_source"].keys()),
                    "Amount": list(income_data["by_source"].values()),
                })
                fig_src = px.pie(src_df, names="Source", values="Amount", hole=0.4,
                                 title="Income by Source")
                fig_src.update_traces(textposition="inside", textinfo="percent+label")
                inc2.plotly_chart(fig_src, use_container_width=True)
        st.caption(f"Total income {filter_year}: **${income_data['total']:,.2f}**")
        with st.expander("Income Transactions", expanded=False):
            inc_txs = income_data.get("transactions", [])
            if inc_txs:
                inc_tx_df = pd.DataFrame(inc_txs)[["date", "merchant", "subcategory", "amount", "account"]]
                inc_tx_df = inc_tx_df.sort_values("date", ascending=False)
                st.dataframe(inc_tx_df, use_container_width=True, hide_index=True)
    else:
        st.info("No income data for this period. Make sure income transactions are categorized as 'Income'.")

    # ── Recurring Transactions ────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Recurring Transactions")
    st.caption("Merchants that appear in 2+ months with a consistent amount — likely subscriptions or fixed bills.")
    recurring = api_get("/recurring") or []
    if recurring:
        rec_df = pd.DataFrame(recurring)
        rec_df["months_seen"] = rec_df["months_seen"].apply(lambda x: ", ".join(x))
        rec_df = rec_df.rename(columns={
            "merchant": "Merchant", "category": "Category", "subcategory": "Subcategory",
            "avg_amount": "Avg/Month ($)", "total_spent": "Total Spent ($)",
            "occurrences": "Times", "months_seen": "Months Seen",
        })
        st.dataframe(rec_df[["Merchant", "Category", "Subcategory", "Avg/Month ($)", "Times", "Total Spent ($)", "Months Seen"]],
                     use_container_width=True, hide_index=True)
        total_recurring = sum(r["avg_amount"] for r in recurring)
        st.caption(f"Estimated recurring spend: **${total_recurring:,.2f}/month** across {len(recurring)} merchants")
    else:
        st.info("No recurring transactions detected yet. Upload at least 2 months of statements.")

    # ── Budget Trend ──────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Budget Trend")
    st.caption(f"Monthly budget vs actual across {filter_year}.")
    trend_data = api_get(f"/budget-trend?year={filter_year}") or {}
    trend_months = trend_data.get("months", [])
    trend_cats = trend_data.get("categories", [])
    if trend_months and trend_cats:
        trend_df = pd.DataFrame(trend_months)
        # Build a grouped bar chart for Savings (most important) + any category with a budget
        for cat in trend_cats:
            t_col = f"{cat}_target"
            a_col = f"{cat}_actual"
            if t_col not in trend_df.columns:
                continue
            cat_rows = []
            for _, row in trend_df.iterrows():
                cat_rows.append({"Month": row["month"], "Type": "Actual", "Amount": row.get(a_col, 0)})
                cat_rows.append({"Month": row["month"], "Type": "Target", "Amount": row.get(t_col, 0)})
            cat_df = pd.DataFrame(cat_rows)
            fig_trend = px.bar(
                cat_df, x="Month", y="Amount", color="Type", barmode="group",
                color_discrete_map={"Actual": "#4e79a7", "Target": "#bab0ac"},
                title=f"{cat}: Actual vs Target",
                labels={"Amount": "Amount ($)"},
            )
            fig_trend.update_layout(xaxis_tickangle=-45, xaxis_type="category", margin=dict(t=40), height=260)
            st.plotly_chart(fig_trend, use_container_width=True)
    elif budgets:
        st.info("Need at least 1 month of data with income transactions to show trends.")
    else:
        st.info("Set budget targets above to enable trend tracking.")

    # ── Export ────────────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Export")
    ex1, ex2, ex3 = st.columns(3)
    with ex1:
        exp_year = st.selectbox("Year", [0] + list(range(2020, _date.today().year + 1)),
                                format_func=lambda y: "All years" if y == 0 else str(y), key="exp_year")
    with ex2:
        exp_month = st.selectbox("Month", [0] + list(range(1, 13)),
                                 format_func=lambda m: "All months" if m == 0 else _MONTH_NAMES[m], key="exp_month")
    with ex3:
        _exp_accounts = sorted({t.get("account") for t in (api_get("/transactions?limit=5000") or []) if t.get("account")})
        exp_account = st.selectbox("Account", ["All accounts"] + _exp_accounts, key="exp_account")

    export_params = {}
    if exp_year:
        export_params["year"] = exp_year
    if exp_month:
        export_params["month"] = exp_month
    if exp_account != "All accounts":
        export_params["account"] = exp_account

    export_url = f"{API_BASE}/export?" + "&".join(f"{k}={v}" for k, v in export_params.items())
    st.markdown(f"[⬇ Download CSV]({export_url})", unsafe_allow_html=True)

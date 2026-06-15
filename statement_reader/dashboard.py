import streamlit as st
import sqlite3
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import re
from pathlib import Path

# Page configurations
st.set_page_config(
    page_title="Finances Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom premium styling
st.markdown("""
<style>
    .metric-card {
        background-color: #ffffff;
        border-radius: 10px;
        padding: 15px 20px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
        margin-bottom: 15px;
        border-left: 5px solid #2196F3;
    }
    .metric-card h3 {
        margin: 0;
        font-size: 14px;
        color: #757575;
    }
    .metric-card p {
        margin: 5px 0 0 0;
        font-size: 24px;
        font-weight: bold;
        color: #212121;
    }
</style>
""", unsafe_allow_html=True)

st.title("📊 Finances Dashboard")
st.markdown("---")

# Dynamic DB Path Resolution
def resolve_db_path(path):
    p = Path(path)
    if p.is_absolute():
        return str(p)
    # Resolve relative to script directory first
    script_dir = Path(__file__).resolve().parent
    rel_to_script = (script_dir / p).resolve()
    if rel_to_script.exists() or rel_to_script.parent.exists():
        return str(rel_to_script)
    # Check relative to workspace root (parent of script directory)
    workspace_root = script_dir.parent
    rel_to_workspace = (workspace_root / p).resolve()
    if rel_to_workspace.exists() or rel_to_workspace.parent.exists():
        return str(rel_to_workspace)
    return str(p.resolve())

db_path = resolve_db_path("../docs/records.db")

if not Path(db_path).exists():
    st.error(f"Error: Database file not found at: `{db_path}`")
    st.info("Please run the PDF parser first to create and populate the database.")
else:
    # Connect and load users
    conn = sqlite3.connect(db_path)
    try:
        users_df = pd.read_sql_query("SELECT id, name FROM users", conn)
    except Exception:
        users_df = pd.DataFrame()

    if users_df.empty:
        st.warning("No users found in the database. Please run the PDF parser first to ingest statement data.")
        conn.close()
        df = pd.DataFrame()
    else:
        st.sidebar.header("👤 Account Profile")
        user_options = users_df.to_dict('records')
        selected_user = st.sidebar.selectbox(
            "Select User Profile",
            options=user_options,
            format_func=lambda u: f"{u['name']} (ID: {u['id']})"
        )
        
        # Check which table has records for this user
        has_axis = False
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM transactions WHERE user_id = ? LIMIT 1", (selected_user['id'],))
            has_axis = cur.fetchone() is not None
        except sqlite3.OperationalError:
            pass
            
        if has_axis:
            df = pd.read_sql_query("SELECT * FROM transactions WHERE user_id = ?", conn, params=(selected_user['id'],))
        else:
            try:
                df = pd.read_sql_query("SELECT * FROM icici_transactions WHERE user_id = ?", conn, params=(selected_user['id'],))
            except sqlite3.OperationalError:
                df = pd.DataFrame()
        conn.close()

    if df.empty:
        st.warning("No transactions found for the selected profile. Please verify the database.")
    else:
        # ─────────────────────────────────────────────────────────────
        # DATA PREPROCESSING
        # ─────────────────────────────────────────────────────────────
        # Convert date to datetime
        df['date'] = pd.to_datetime(df['tran_date'], format='%d-%m-%Y', errors='coerce')
        if df['date'].isna().any():
            # Fallback if formats differ
            df['date'] = df['date'].fillna(pd.to_datetime(df['tran_date'], dayfirst=True, errors='coerce'))
        
        df = df.dropna(subset=['date']).sort_values('date').reset_index(drop=True)
        
        # Clean numeric columns
        df['debit'] = pd.to_numeric(df['debit']).fillna(0.0)
        df['credit'] = pd.to_numeric(df['credit']).fillna(0.0)
        df['balance'] = pd.to_numeric(df['balance']).fillna(0.0)
        
        # Calculate time period
        min_date = df['date'].min()
        max_date = df['date'].max()
        period_days = (max_date - min_date).days + 1
        n_months = max(period_days / 30.44, 1.0)

        # ─────────────────────────────────────────────────────────────
        # METRICS CALCULATIONS
        # ─────────────────────────────────────────────────────────────
        
        # Counterparty canonicalization function
        def extract_counterparty(description):
            if not description:
                return "unknown"
            m = re.search(r"[\w.\-]+@[\w.\-]+", description)
            if m:
                return m.group(0).lower()
            noise = {"upi", "neft", "imps", "rtgs", "transfer", "payment", "to", "from", "by", "ref", "txn", "p2a", "p2m", "pay"}
            tokens = [t for t in re.split(r"[\s/\-|]+", description.strip()) if t.lower() not in noise]
            cleaned_tokens = []
            for t in tokens:
                t_clean = t.strip()
                if not t_clean:
                    continue
                if re.match(r'^\d+$', t_clean):
                    continue
                cleaned_tokens.append(t_clean)
            return " ".join(cleaned_tokens[:3]).lower().strip() or description[:30].lower()

        # Self-transfer detection function
        def detect_self_transfers(df_data):
            is_self = pd.Series(False, index=df_data.index)
            # Rule 1: Explicit markers
            marker_pat = re.compile(r'rd/|mob-td|\btd\b|ritam|majumdar', re.IGNORECASE)
            is_self = is_self | df_data['particulars'].str.contains(marker_pat, na=False)
            
            # Rule 2: Matched pairs to the same canonical counterparty
            unmarked_indices = df_data[~is_self].index
            for cp, group in df_data.loc[unmarked_indices].groupby('counterparty'):
                credits = group[group['credit'] > 0].copy()
                debits = group[group['debit'] > 0].copy()
                if credits.empty or debits.empty:
                    continue
                credits_sorted = credits.sort_values(by='credit')
                debits_sorted = debits.sort_values(by='debit')
                used_debits = set()
                for c_idx, c_row in credits_sorted.iterrows():
                    c_amt = c_row['credit']
                    best_d_idx = None
                    best_diff = float('inf')
                    for d_idx, d_row in debits_sorted.iterrows():
                        if d_idx in used_debits:
                            continue
                        d_amt = d_row['debit']
                        diff = abs(c_amt - d_amt)
                        max_amt = max(c_amt, d_amt)
                        if max_amt > 0:
                            pct_diff = diff / max_amt
                            if (pct_diff <= 0.02 or diff <= 100.0) and diff < best_diff:
                                best_diff = diff
                                best_d_idx = d_idx
                    if best_d_idx is not None:
                        used_debits.add(best_d_idx)
                        is_self.at[c_idx] = True
                        is_self.at[best_d_idx] = True
            return is_self

        # Apply counterparty cleaning
        df['counterparty'] = df['particulars'].apply(extract_counterparty)
        unique_counterparties = df['counterparty'].nunique()

        # Apply self-transfer detection
        df['is_self'] = detect_self_transfers(df)

        # 1. Volume and Flow (Gross)
        total_credits = df['credit'].sum()
        total_debits = df['debit'].sum()
        net_cashflow = total_credits - total_debits
        avg_monthly_inflow = total_credits / n_months
        avg_monthly_outflow = total_debits / n_months
        avg_txns_per_month = len(df) / n_months
        
        # 1b. Volume and Flow (Netted of self-transfers)
        net_credits = df[~df['is_self']]['credit'].sum()
        net_debits = df[~df['is_self']]['debit'].sum()
        net_cashflow_net = net_credits - net_debits
        avg_monthly_inflow_net = net_credits / n_months
        avg_monthly_outflow_net = net_debits / n_months

        # Txn sizes (only considering active non-self debit/credit events for transaction sizing)
        tx_amounts = df['credit'].where(df['credit'] > 0, df['debit'])
        tx_amounts = tx_amounts[tx_amounts > 0]
        avg_txn_size = tx_amounts.mean() if not tx_amounts.empty else 0.0
        median_txn_size = tx_amounts.median() if not tx_amounts.empty else 0.0

        # 2. Behaviour and Stability
        # UPI Share
        upi_pattern = re.compile(r"upi|vpa|@", re.IGNORECASE)
        df['is_upi'] = df['particulars'].str.contains(upi_pattern, na=False)
        credits_df = df[df['credit'] > 0].copy()
        upi_credits = credits_df[credits_df['is_upi']]['credit'].sum()
        upi_inflow_pct = (upi_credits / total_credits * 100) if total_credits > 0 else 0.0
        
        # Repeat counterparty ratio
        credits_df['counterparty'] = credits_df['particulars'].apply(extract_counterparty)
        if not credits_df.empty:
            cp_counts = credits_df['counterparty'].value_counts()
            repeat_cps = cp_counts[cp_counts > 1].index
            repeat_inflow = credits_df[credits_df['counterparty'].isin(repeat_cps)]['credit'].sum()
            repeat_cp_ratio = (repeat_inflow / total_credits * 100) if total_credits > 0 else 0.0
        else:
            repeat_cp_ratio = 0.0

        # Daily balances
        date_range = pd.date_range(min_date, max_date, freq="D")
        daily_bal = df.groupby('date')['balance'].last().reindex(date_range).ffill().bfill()
        
        min_balance = daily_bal.min() if not daily_bal.empty else 0.0
        avg_daily_balance = daily_bal.mean() if not daily_bal.empty else 0.0
        days_below_1000 = int((daily_bal < 1000).sum()) if not daily_bal.empty else 0
        
        # Weekend vs Weekday Inflow (Gross)
        weekend_days = int((date_range.dayofweek >= 5).sum())
        weekday_days = int((date_range.dayofweek < 5).sum())
        weekend_days = max(weekend_days, 1)
        weekday_days = max(weekday_days, 1)
        
        credits_df['dow'] = credits_df['date'].dt.dayofweek
        weekend_inflow = credits_df[credits_df['dow'] >= 5]['credit'].sum()
        weekday_inflow = credits_df[credits_df['dow'] < 5]['credit'].sum()
        
        weekend_avg = weekend_inflow / weekend_days
        weekday_avg = weekday_inflow / weekday_days
        weekend_weekday_ratio = (weekend_avg / weekday_avg) if weekday_avg > 0 else 0.0

        # Weekend vs Weekday Inflow (Net)
        credits_net_df = df[(df['credit'] > 0) & (~df['is_self'])].copy()
        credits_net_df['dow'] = credits_net_df['date'].dt.dayofweek
        weekend_inflow_net = credits_net_df[credits_net_df['dow'] >= 5]['credit'].sum()
        weekday_inflow_net = credits_net_df[credits_net_df['dow'] < 5]['credit'].sum()
        
        weekend_avg_net = weekend_inflow_net / weekend_days
        weekday_avg_net = weekday_inflow_net / weekday_days
        weekend_weekday_ratio_net = (weekend_avg_net / weekday_avg_net) if weekday_avg_net > 0 else 0.0
        
        # Largest single inflow and outflow (Gross)
        largest_inflow = df['credit'].max()
        largest_outflow = df['debit'].max()
        largest_inflow_pct = (largest_inflow / total_credits * 100) if total_credits > 0 else 0.0
        largest_outflow_pct = (largest_outflow / total_debits * 100) if total_debits > 0 else 0.0

        # Largest single inflow and outflow (Net)
        df_net = df[~df['is_self']]
        largest_inflow_net = df_net['credit'].max() if not df_net.empty else 0.0
        largest_outflow_net = df_net['debit'].max() if not df_net.empty else 0.0
        largest_inflow_net_pct = (largest_inflow_net / net_credits * 100) if net_credits > 0 else 0.0
        largest_outflow_net_pct = (largest_outflow_net / net_debits * 100) if net_debits > 0 else 0.0

        # ─────────────────────────────────────────────────────────────
        # SIDEBAR
        # ─────────────────────────────────────────────────────────────
        st.sidebar.header("📊 Statement Metadata")
        st.sidebar.markdown(f"""
        - **Start Date:** `{min_date.strftime('%Y-%m-%d')}`
        - **End Date:** `{max_date.strftime('%Y-%m-%d')}`
        - **Total Days:** `{period_days}`
        - **Total Transactions:** `{len(df)}`
        - **Active Months:** `{n_months:.2f}`
        """)
        st.sidebar.markdown("---")
        st.sidebar.header("⚙️ Dashboard Controls")
        exclude_self_sidebar = st.sidebar.checkbox("Exclude Self-Transfers from Charts & Breakdowns", value=False)

        # ─────────────────────────────────────────────────────────────
        # LAYOUT & TABS
        # ─────────────────────────────────────────────────────────────
        tab1, tab2, tab3 = st.tabs(["💧 Volume & Flow", "🛡️ Behaviour & Stability", "📅 Raw Transactions"])
        
        with tab1:
            st.header("Volume and Flow Analysis")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Credits (Gross Inflow)", f"₹{total_credits:,.2f}")
                st.metric("Total Credits (Netted Inflow)", f"₹{net_credits:,.2f}")
                st.metric("Average Monthly Inflow (Gross)", f"₹{avg_monthly_inflow:,.2f}")
                st.metric("Average Monthly Inflow (Netted)", f"₹{avg_monthly_inflow_net:,.2f}")
            with col2:
                st.metric("Total Debits (Gross Outflow)", f"₹{total_debits:,.2f}")
                st.metric("Total Debits (Netted Outflow)", f"₹{net_debits:,.2f}")
                st.metric("Average Monthly Outflow (Gross)", f"₹{avg_monthly_outflow:,.2f}")
                st.metric("Average Monthly Outflow (Netted)", f"₹{avg_monthly_outflow_net:,.2f}")
            with col3:
                st.metric("Net Cashflow (Gross)", f"₹{net_cashflow:,.2f}", 
                          delta=f"₹{net_cashflow:,.2f}", 
                          delta_color="normal" if net_cashflow >= 0 else "inverse")
                st.metric("Net Cashflow (Netted)", f"₹{net_cashflow_net:,.2f}", 
                          delta=f"₹{net_cashflow_net:,.2f}", 
                          delta_color="normal" if net_cashflow_net >= 0 else "inverse")
                st.metric("Avg Transactions / Month", f"{avg_txns_per_month:.1f}")

            st.markdown("---")
            st.subheader("Transaction Sizes")
            col_size1, col_size2 = st.columns(2)
            with col_size1:
                st.metric("Average Transaction Size", f"₹{avg_txn_size:,.2f}")
            with col_size2:
                st.metric("Median Transaction Size", f"₹{median_txn_size:,.2f}")

            st.markdown("---")
            st.subheader("Flow Breakdown Visualizations")
            col_chart1, col_chart2 = st.columns(2)
            
            with col_chart1:
                credits_val = net_credits if exclude_self_sidebar else total_credits
                debits_val = net_debits if exclude_self_sidebar else total_debits
                title_suffix = " (Netted)" if exclude_self_sidebar else " (Gross)"
                fig, ax = plt.subplots(figsize=(6, 4))
                ax.bar(["Total Credits", "Total Debits"], [credits_val, debits_val], color=["#4CAF50", "#f44336"], width=0.5)
                ax.set_ylabel("Amount in ₹")
                ax.set_title("Total Inflow vs Outflow Volume" + title_suffix)
                for i, v in enumerate([credits_val, debits_val]):
                    ax.text(i, v + (max(credits_val, debits_val) * 0.02), f"₹{v:,.2f}", ha='center', fontweight='bold')
                st.pyplot(fig)

            with col_chart2:
                # Running balance
                fig_bal, ax_bal = plt.subplots(figsize=(6, 4))
                ax_bal.plot(daily_bal.index, daily_bal.values, color='#2196F3', linewidth=2)
                ax_bal.fill_between(daily_bal.index, daily_bal.values, color='#2196F3', alpha=0.1)
                ax_bal.axhline(1000, color='#f44336', linestyle='--', label="Rs 1,000 Threshold", alpha=0.8)
                ax_bal.set_title("Running Daily Balance Over Time")
                ax_bal.set_ylabel("Balance (₹)")
                ax_bal.legend(loc="upper left")
                ax_bal.grid(True, linestyle=":", alpha=0.5)
                plt.xticks(rotation=20)
                st.pyplot(fig_bal)

        with tab2:
            st.header("Behaviour and Stability Indicators")
            
            col_s1, col_s2, col_s3 = st.columns(3)
            with col_s1:
                st.metric("Min Daily Balance", f"₹{min_balance:,.2f}")
                st.metric("UPI Inflow Share", f"{upi_inflow_pct:.2f}%")
            with col_s2:
                st.metric("Average Daily Balance", f"₹{avg_daily_balance:,.2f}")
                st.metric("Unique Counterparties", f"{unique_counterparties}")
            with col_s3:
                st.metric("Days Below ₹1,000", f"{days_below_1000} days", 
                          delta=f"{days_below_1000} days" if days_below_1000 > 0 else "None", 
                          delta_color="inverse" if days_below_1000 > 0 else "normal")
                st.metric("Repeat Counterparty Ratio", f"{repeat_cp_ratio:.2f}%")

            st.markdown("---")
            st.subheader("Weekend vs Weekday Inflow")
            col_w1, col_w2, col_w3 = st.columns(3)
            with col_w1:
                st.metric("Weekend/Weekday Ratio (Gross Per-Day)", f"{weekend_weekday_ratio:.4f}")
                st.metric("Weekend/Weekday Ratio (Netted Per-Day)", f"{weekend_weekday_ratio_net:.4f}")
            with col_w2:
                st.metric("Weekend Total Inflow (Gross)", f"₹{weekend_inflow:,.2f}")
                st.metric("Weekend Total Inflow (Netted)", f"₹{weekend_inflow_net:,.2f}")
            with col_w3:
                st.metric("Weekday Total Inflow (Gross)", f"₹{weekday_inflow:,.2f}")
                st.metric("Weekday Total Inflow (Netted)", f"₹{weekday_inflow_net:,.2f}")

            st.markdown("---")
            st.subheader("Largest Transaction Weights")
            col_large1, col_large2 = st.columns(2)
            with col_large1:
                st.metric("Largest Inflow (Gross)", f"₹{largest_inflow:,.2f}", help="Highest credit value")
                st.markdown(f"**Share of Total Credits:** `{largest_inflow_pct:.2f}%`")
                st.metric("Largest Inflow (Netted)", f"₹{largest_inflow_net:,.2f}", help="Highest non-self credit value")
                st.markdown(f"**Share of Netted Credits:** `{largest_inflow_net_pct:.2f}%`")
            with col_large2:
                st.metric("Largest Outflow (Gross)", f"₹{largest_outflow:,.2f}", help="Highest debit value")
                st.markdown(f"**Share of Total Debits:** `{largest_outflow_pct:.2f}%`")
                st.metric("Largest Outflow (Netted)", f"₹{largest_outflow_net:,.2f}", help="Highest non-self debit value")
                st.markdown(f"**Share of Netted Debits:** `{largest_outflow_net_pct:.2f}%`")
                
            st.markdown("---")
            st.subheader("Payment Methods & Counterparties")
            col_pie1, col_pie2 = st.columns(2)
            
            with col_pie1:
                fig_pie, ax_pie = plt.subplots(figsize=(5, 4))
                ax_pie.pie([upi_credits, total_credits - upi_credits], 
                           labels=["UPI Inflow", "Other Inflows"], 
                           autopct='%1.1f%%', 
                           colors=["#8BC34A", "#CFD8DC"], 
                           startangle=140,
                           wedgeprops=dict(width=0.4, edgecolor='w'))
                ax_pie.set_title("Inflow Channel Share")
                st.pyplot(fig_pie)
                
            with col_pie2:
                # Top counterparties by transaction count
                if not df.empty:
                    top_cp_df = df[~df['is_self']] if exclude_self_sidebar else df
                    top_cp = top_cp_df['counterparty'].value_counts().head(5)
                    fig_cp, ax_cp = plt.subplots(figsize=(5, 4))
                    top_cp.plot(kind='barh', color='#009688', ax=ax_cp)
                    ax_cp.invert_yaxis()
                    ax_cp.set_title("Top 5 Counterparties by Frequency")
                    ax_cp.set_xlabel("Transaction Count")
                    st.pyplot(fig_cp)

        with tab3:
            st.header("Transactions Record")
            st.markdown("Explore or filter all individual transactions extracted from the PDF statement.")
            
            # Filters
            col_filt1, col_filt2 = st.columns(2)
            with col_filt1:
                search_query = st.text_input("🔍 Search particulars or counterparty:")
            with col_filt2:
                hide_self = st.checkbox("Hide Self/Circular Transfers", value=False)
            
            display_df = df.copy()
            if hide_self:
                display_df = display_df[~display_df['is_self']]
            if search_query:
                display_df = display_df[
                    display_df['particulars'].str.contains(search_query, case=False, na=False) |
                    display_df['counterparty'].str.contains(search_query, case=False, na=False)
                ]
            
            display_cols = ['tran_date', 'chq_no', 'particulars', 'debit', 'credit', 'balance', 'init_br', 'is_self']
            display_df_view = display_df[display_cols].copy()
            display_df_view.columns = ['Date', 'Chq/Ref No', 'Particulars', 'Debit (₹)', 'Credit (₹)', 'Balance (₹)', 'Branch', 'Self Transfer?']
            st.dataframe(display_df_view, use_container_width=True)

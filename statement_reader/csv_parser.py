"""
Cashflow Feature Extractor
Rintel Systems – Task 01

Reads a bank statement (CSV or PDF), cleans it, classifies transactions,
and computes ~15 behavioural features into a single-row summary.

Usage:
    python cashflow_extractor.py statement.csv
    python cashflow_extractor.py statement.pdf
"""

import sys
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────
# 1. LOADING
# ─────────────────────────────────────────────────────────────

def _clean_amount(val) -> float:
    """Turn messy strings like '1,23,456.78' or '(500)' into a float."""
    if pd.isna(val) or str(val).strip() in ("", "-", "--", "NA", "N/A"):
        return 0.0
    s = str(val).strip()
    s = s.replace("(", "-").replace(")", "")   # accounting negatives
    s = re.sub(r"[₹$£€,\s]", "", s)            # currency symbols, commas, spaces
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_date(series: pd.Series) -> pd.Series:
    """Try multiple date formats; fall back to pandas inference."""
    formats = [
        "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y",
        "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%y", "%d-%b-%Y",
        "%b %d, %Y", "%B %d, %Y",
    ]
    for fmt in formats:
        try:
            parsed = pd.to_datetime(series, format=fmt, dayfirst=True, errors="raise")
            return parsed
        except Exception:
            pass
    return pd.to_datetime(series, dayfirst=True, infer_datetime_format=True, errors="coerce")


def _detect_columns(df: pd.DataFrame):
    """
    Return (date_col, desc_col, debit_col, credit_col, balance_col).
    Works with combined amount+sign columns too.
    """
    cols_lower = {c: c.lower().strip() for c in df.columns}

    def find(exact, partial=None):
        """Match exact first, then partial substring (longer patterns first to avoid false hits)."""
        # Exact match pass
        for col, lc in cols_lower.items():
            if lc in exact:
                return col
        # Partial match pass (sorted longest pattern first to be specific)
        if partial:
            for pat in sorted(partial, key=len, reverse=True):
                for col, lc in cols_lower.items():
                    if pat in lc:
                        return col
        return None

    date_col    = find(
        exact={"date", "txn date", "value date", "transaction date", "posted date"},
        partial=["txn date", "value date", "transaction date", "date"]
    )
    desc_col    = find(
        exact={"description", "narration", "particulars", "details", "remarks", "memo", "payee"},
        partial=["narration", "particulars", "description", "remarks", "memo", "payee"]
    )
    debit_col   = find(
        exact={"debit", "withdrawal", "withdrawals", "dr", "debit amount", "payment"},
        partial=["withdrawal", "debit", "payment"]
    )
    credit_col  = find(
        exact={"credit", "deposit", "deposits", "cr", "credit amount", "receipt"},
        partial=["deposit", "credit", "receipt"]
    )
    balance_col = find(
        exact={"balance", "closing balance", "running balance", "available balance"},
        partial=["closing balance", "running balance", "balance"]
    )
    amount_col  = find(
        exact={"amount", "transaction amount"},
        partial=["amount"]
    ) if not (debit_col and credit_col) else None

    if date_col is None or desc_col is None:
        raise ValueError(
            "Could not auto-detect Date or Description column. "
            "Please rename columns to include 'Date' and 'Description'."
        )

    return date_col, desc_col, debit_col, credit_col, balance_col, amount_col


def load_csv(path: str) -> pd.DataFrame:
    """Load and normalise a CSV bank statement."""
    raw = pd.read_csv(path, skiprows=0, dtype=str, skip_blank_lines=True)

    # Drop rows that are entirely empty
    raw.dropna(how="all", inplace=True)

    # Sometimes the real header is buried in row 1-5
    if raw.shape[1] < 3:
        raise ValueError("CSV has fewer than 3 columns – check the file format.")

    # Strip whitespace from column names
    raw.columns = [str(c).strip() for c in raw.columns]

    date_col, desc_col, debit_col, credit_col, balance_col, amount_col = _detect_columns(raw)

    df = pd.DataFrame()
    df["date"]        = _parse_date(raw[date_col])
    df["description"] = raw[desc_col].fillna("").astype(str).str.strip()

    if debit_col and credit_col:
        df["debit"]  = raw[debit_col].apply(_clean_amount)
        df["credit"] = raw[credit_col].apply(_clean_amount)
    elif amount_col:
        # Positive = credit, negative = debit (common in some banks)
        amt = raw[amount_col].apply(_clean_amount)
        df["credit"] = amt.clip(lower=0)
        df["debit"]  = (-amt).clip(lower=0)
    else:
        raise ValueError("Cannot find debit/credit or amount columns in the CSV.")

    if balance_col:
        df["balance"] = raw[balance_col].apply(_clean_amount)
    else:
        df["balance"] = np.nan

    # Drop rows with no date and no money movement
    df = df[df["date"].notna() & ((df["debit"] != 0) | (df["credit"] != 0))]
    df.sort_values("date", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def load_pdf(path: str) -> pd.DataFrame:
    """Extract tables from a PDF bank statement using pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        raise ImportError("pdfplumber is required for PDF support. Install it with: pip install pdfplumber")

    all_rows = []
    header = None

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue
                # First non-empty row that looks like a header
                for i, row in enumerate(table):
                    if row and any(
                        kw in str(cell).lower()
                        for cell in row
                        for kw in ["date", "narration", "description", "debit", "credit", "balance"]
                    ):
                        if header is None:
                            header = [str(c).strip() if c else f"col_{j}" for j, c in enumerate(row)]
                            all_rows.extend(table[i + 1:])
                        else:
                            all_rows.extend(table[i + 1:])
                        break
                else:
                    if header is not None:
                        all_rows.extend(table)

    if not header or not all_rows:
        raise ValueError("Could not extract a table from the PDF. Try converting to CSV first.")

    # Pad / trim rows to match header length
    n = len(header)
    cleaned = []
    for row in all_rows:
        if row is None:
            continue
        row = [str(c).strip() if c is not None else "" for c in row]
        if len(row) < n:
            row += [""] * (n - len(row))
        cleaned.append(row[:n])

    raw = pd.DataFrame(cleaned, columns=header)
    raw.dropna(how="all", inplace=True)

    # Now reuse CSV normalisation path
    date_col, desc_col, debit_col, credit_col, balance_col, amount_col = _detect_columns(raw)

    df = pd.DataFrame()
    df["date"]        = _parse_date(raw[date_col])
    df["description"] = raw[desc_col].fillna("").astype(str).str.strip()

    if debit_col and credit_col:
        df["debit"]  = raw[debit_col].apply(_clean_amount)
        df["credit"] = raw[credit_col].apply(_clean_amount)
    elif amount_col:
        amt = raw[amount_col].apply(_clean_amount)
        df["credit"] = amt.clip(lower=0)
        df["debit"]  = (-amt).clip(lower=0)
    else:
        raise ValueError("Cannot find debit/credit or amount columns in extracted PDF table.")

    if balance_col:
        df["balance"] = raw[balance_col].apply(_clean_amount)
    else:
        df["balance"] = np.nan

    df = df[df["date"].notna() & ((df["debit"] != 0) | (df["credit"] != 0))]
    df.sort_values("date", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def load_statement(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        return load_pdf(path)
    else:
        return load_csv(path)


# ─────────────────────────────────────────────────────────────
# 2. CLASSIFICATION
# ─────────────────────────────────────────────────────────────

def classify(df: pd.DataFrame) -> pd.DataFrame:
    """Add is_credit, is_debit, is_upi, amount columns."""
    df = df.copy()
    df["is_credit"] = df["credit"] > 0
    df["is_debit"]  = df["debit"]  > 0
    df["amount"]    = df["credit"].where(df["is_credit"], df["debit"])

    upi_pattern = re.compile(r"upi|vpa|@", re.IGNORECASE)
    df["is_upi"] = df["description"].str.contains(upi_pattern, na=False)

    return df


# ─────────────────────────────────────────────────────────────
# 3. FEATURE COMPUTATION
# ─────────────────────────────────────────────────────────────

def extract_counterparty(description: str) -> str:
    """
    Heuristic: for UPI strings grab the VPA handle; otherwise
    take the first 4 meaningful tokens of the description.
    """
    # UPI VPA: something@something
    m = re.search(r"[\w.\-]+@[\w.\-]+", description)
    if m:
        return m.group(0).lower()
    # Strip common noise words and return first 3 tokens
    noise = {"upi", "neft", "imps", "rtgs", "transfer", "payment", "to", "from", "by", "ref", "txn"}
    tokens = [t for t in re.split(r"[\s/\-|]+", description.strip()) if t.lower() not in noise]
    return " ".join(tokens[:3]).lower().strip() or description[:30].lower()


def compute_features(df: pd.DataFrame) -> dict:
    df = classify(df)

    period_days  = (df["date"].max() - df["date"].min()).days + 1
    n_months     = max(period_days / 30.44, 1)               # avoid div-by-zero

    credits = df[df["is_credit"]]
    debits  = df[df["is_debit"]]

    # ── Volume & flow ──────────────────────────────────────────
    total_credits   = credits["credit"].sum()
    total_debits    = debits["debit"].sum()
    net_cashflow    = total_credits - total_debits
    avg_monthly_in  = total_credits / n_months
    avg_monthly_out = total_debits  / n_months

    n_txns          = len(df)
    avg_txns_month  = n_txns / n_months
    avg_txn_size    = df["amount"].mean() if n_txns else 0.0
    median_txn_size = df["amount"].median() if n_txns else 0.0

    # ── Behaviour & stability ──────────────────────────────────
    upi_inflow      = credits[credits["is_upi"]]["credit"].sum()
    upi_inflow_pct  = (upi_inflow / total_credits * 100) if total_credits else 0.0

    # Counterparty diversity
    df["counterparty"] = df["description"].apply(extract_counterparty)
    n_unique_counterparties = df["counterparty"].nunique()

    # Re-slice credits after adding counterparty column
    credits = df[df["is_credit"]]

    # Repeat-counterparty ratio: share of *inflows* from counterparties seen > once
    cp_credit_counts = credits["counterparty"].value_counts()
    repeat_cps       = cp_credit_counts[cp_credit_counts > 1].index
    repeat_inflow    = credits[credits["counterparty"].isin(repeat_cps)]["credit"].sum()
    repeat_cp_ratio  = (repeat_inflow / total_credits * 100) if total_credits else 0.0

    # Daily balance stats (only if we have balance data)
    if df["balance"].notna().any() and (df["balance"] != 0).any():
        # Build a daily series by forward-filling the last known balance each day
        date_range   = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
        daily_bal    = (
            df.groupby("date")["balance"]
            .last()
            .reindex(date_range)
            .ffill()
        )
        min_balance        = daily_bal.min()
        avg_daily_balance  = daily_bal.mean()
        days_below_1000    = int((daily_bal < 1000).sum())
    else:
        min_balance        = np.nan
        avg_daily_balance  = np.nan
        days_below_1000    = np.nan

    # Weekend vs weekday inflow ratio
    credits_copy        = credits.copy()
    credits_copy["dow"] = credits_copy["date"].dt.dayofweek   # Mon=0, Sun=6
    weekend_in = credits_copy[credits_copy["dow"] >= 5]["credit"].sum()
    weekday_in = credits_copy[credits_copy["dow"] <  5]["credit"].sum()
    weekend_weekday_ratio = (weekend_in / weekday_in) if weekday_in else np.nan

    # Largest single inflow / outflow
    largest_inflow  = credits["credit"].max() if len(credits) else 0.0
    largest_outflow = debits["debit"].max()   if len(debits)  else 0.0

    features = {
        # Volume & flow
        "total_credits_inr":        round(total_credits,   2),
        "total_debits_inr":         round(total_debits,    2),
        "net_cashflow_inr":         round(net_cashflow,    2),
        "avg_monthly_inflow_inr":   round(avg_monthly_in,  2),
        "avg_monthly_outflow_inr":  round(avg_monthly_out, 2),
        "avg_txns_per_month":       round(avg_txns_month,  2),
        "avg_txn_size_inr":         round(avg_txn_size,    2),
        "median_txn_size_inr":      round(median_txn_size, 2),
        # Behaviour & stability
        "upi_inflow_pct":           round(upi_inflow_pct,  2),
        "n_unique_counterparties":  int(n_unique_counterparties),
        "repeat_cp_ratio_pct":      round(repeat_cp_ratio, 2),
        "min_daily_balance_inr":    round(min_balance, 2)        if not np.isnan(min_balance)       else None,
        "avg_daily_balance_inr":    round(avg_daily_balance, 2)  if not np.isnan(avg_daily_balance)  else None,
        "days_balance_below_1000":  days_below_1000              if not (isinstance(days_below_1000, float) and np.isnan(days_below_1000)) else None,
        "weekend_weekday_inflow_ratio": round(weekend_weekday_ratio, 4) if not np.isnan(weekend_weekday_ratio) else None,
        "largest_single_inflow_inr":  round(largest_inflow,  2),
        "largest_single_outflow_inr": round(largest_outflow, 2),
    }

    return features


# ─────────────────────────────────────────────────────────────
# 4. MAIN
# ─────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python cashflow_extractor.py <statement.csv|statement.pdf>")
        sys.exit(1)

    input_path = sys.argv[1]
    print(f"\n{'='*60}")
    print(f"  Cashflow Feature Extractor – Rintel Systems")
    print(f"{'='*60}")
    print(f"  Loading: {input_path}")

    try:
        df = load_statement(input_path)
    except Exception as e:
        print(f"\n[ERROR] Could not load statement: {e}")
        sys.exit(1)

    print(f"  Parsed {len(df)} transactions  |  "
          f"{df['date'].min().date()} → {df['date'].max().date()}")

    features = compute_features(df)

    # ── Pretty print ──
    print(f"\n{'─'*60}")
    print("  FEATURE SUMMARY")
    print(f"{'─'*60}")
    max_key = max(len(k) for k in features)
    for k, v in features.items():
        label = k.replace("_", " ").title()
        val   = f"{v:,.2f}" if isinstance(v, float) else str(v)
        print(f"  {label:<45} {val:>12}")

    # ── Save to CSV ──
    out_path = Path(input_path).stem + "_features.csv"
    result_df = pd.DataFrame([features])
    result_df.to_csv(out_path, index=False)
    print(f"\n  ✓ Features saved to: {out_path}")
    print(f"{'='*60}\n")

    return features


if __name__ == "__main__":
    main()

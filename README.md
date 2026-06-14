# 📊 Rintel Bank Statement Reader & Finances Dashboard

An automated tool to ingest bank statements (PDFs), extract transactions, store them in a multi-user database structure, and visualize financial behaviors dynamically.

---

## 🚀 Key Features

- **Relational Data Pipeline**: Parses Axis bank statement PDFs using `pdfplumber` and stores transactions cleanly in SQLite.
- **Multi-User Isolation**: Supports registering multiple statements separately by creating dynamic, auto-incremented user profiles for each run.
- **Dynamic Visual Analytics**: A comprehensive Streamlit dashboard showing:
  - **Volume & Flow**: Monthly inflows vs outflows, net cashflow, transaction sizes, and running daily balance.
  - **Behavior & Stability**: Daily minimum balances, UPI transaction channel shares, repeat counterparty ratios, and weekday vs weekend comparison.
  - **Raw Transaction Ledger**: Full table containing parsed transactions with keyword search filtering.

---

## 📁 Repository Structure

```text
rintel_analysis/
├── docs/                   # (Ignored) Raw PDF statements & SQLite databases
│   ├── act_statement.pdf   # Default statement PDF
│   └── records.db          # SQLite database containing users & transactions
├── statement_reader/
│   ├── dashboard.py        # Streamlit frontend dashboard
│   ├── pdf_parser.py       # Axis statement PDF parsing script
│   ├── csv_parser.py       # Backup CSV cashflow parser
│   ├── settings.py         # Django settings configuration
│   └── urls.py             # Django URL routing
├── .gitignore              # Configured ignore files
├── README.md               # Project documentation
└── manage.py               # Django management CLI
```

---

## 🛠️ Setup & Installation

### 1. Prerequisite Environment
Create and activate the virtual environment:
```bash
# Windows PowerShell
python -m venv statement_reader/venv
.\statement_reader\venv\Scripts\Activate.ps1
```

### 2. Install Dependencies
Ensure you have the required packages installed:
```bash
pip install streamlit pandas matplotlib pdfplumber django
```

---

## 📖 Usage Guide

### Step 1: Parse a PDF Statement
Run the ingestion script. This will extract the transactions, automatically create a new user profile inside `records.db`, and link the transactions to it.
```bash
# Run with defaults (reads docs/act_statement.pdf into docs/records.db)
python statement_reader/pdf_parser.py

# Custom usage:
python statement_reader/pdf_parser.py [path_to_pdf] [path_to_db]
```

### Step 2: Launch the Analytics Dashboard
Launch Streamlit to explore the results:
```bash
streamlit run statement_reader/dashboard.py
```
Use the sidebar select box to switch dynamically between user profiles.

---

## 🗄️ Database Schema (`records.db`)

The SQLite database consists of two tables linked by a foreign key relationship:

### `users` Table
Stores registered profiles from each run.
- `id` (INTEGER, Primary Key, Autoincremented)
- `name` (TEXT): Defaults to `User (<pdf_name>)`
- `created_at` (TIMESTAMP): Current date/time

### `transactions` Table
Stores parsed statement items.
- `id` (INTEGER, Primary Key, Autoincremented)
- `user_id` (INTEGER): References `users(id)`
- `tran_date` (TEXT)
- `chq_no` (TEXT)
- `particulars` (TEXT)
- `debit` (REAL)
- `credit` (REAL)
- `balance` (REAL)
- `init_br` (TEXT)
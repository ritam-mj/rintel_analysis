import re
import sqlite3
import pdfplumber
from pathlib import Path

DEFAULT_PDF_PATH = "./docs/act_statement.pdf"
DEFAULT_DB_PATH = "./docs/records.db"

def parse_axis_statement(pdf_path, db_path):
    all_transactions = []
    
    # Regular expression to catch bank statement date format (e.g., 14-03-2026)
    date_pattern = re.compile(def_date_regex := r'^\d{2}-\d{2}-\d{4}')

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
                
            for row in table:
                # Filter out headers, empty lines, and totals summary rows
                if not row or not row[0] or "Tran Date" in str(row[0]) or "TRANSACTION TOTAL" in str(row[0]):
                    continue
                
                # Strip spaces and resolve None values safely
                cleaned_row = [str(cell).strip() if cell is not None else "" for cell in row]
                
                # Check if it's a primary transaction line starting with a date
                if date_pattern.match(cleaned_row[0]):
                    all_transactions.append(cleaned_row)
                else:
                    # Multi-line text continuation: Merge with the previous transaction's Particulars
                    if all_transactions and cleaned_row[2]:
                        all_transactions[-1][2] += " " + cleaned_row[2]

    # Write data to SQLite
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON;")
    
    # Create users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create transactions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tran_date TEXT,
            chq_no TEXT,
            particulars TEXT,
            debit REAL,
            credit REAL,
            balance REAL,
            init_br TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')
    
    # Create a new user for this parsing run
    pdf_name = Path(pdf_path).name
    cursor.execute('INSERT INTO users (name) VALUES (?)', (f"User ({pdf_name})",))
    user_id = cursor.lastrowid
    
    # Process rows to ensure numbers convert correctly to database numeric formats
    final_rows = []
    for tx in all_transactions:
        # Pad row elements to match table schema size
        if len(tx) < 7:
            tx += [""] * (7 - len(tx))
            
        final_rows.append((
            user_id,
            tx[0],  # Date
            tx[1],  # Chq No
            tx[2],  # Particulars
            float(tx[3].replace(',', '')) if tx[3] else None,  # Debit
            float(tx[4].replace(',', '')) if tx[4] else None,  # Credit
            float(tx[5].replace(',', '')) if tx[5] else None,  # Balance
            tx[6]   # Init Br
        ))
        
    # Bulk insert for efficiency
    cursor.executemany(
        'INSERT INTO transactions (user_id, tran_date, chq_no, particulars, debit, credit, balance, init_br) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        final_rows
    )
    conn.commit()
    conn.close()
    
    print(f"Extraction Complete! Successfully loaded {len(final_rows)} rows for User ID {user_id} into SQLite database.")

# Execute pipeline
if __name__ == "__main__":
    import sys
    from pathlib import Path

    def resolve_path(path):
        if not path:
            return path
        p = Path(path)
        if p.is_absolute():
            return str(p)
        
        # Try relative to current directory
        try:
            if p.exists() or p.parent.exists():
                return str(p)
        except Exception:
            pass
            
        # Try relative to workspace root (grandparent of script)
        try:
            script_dir = Path(__file__).resolve().parent
            workspace_root = script_dir.parent
            alt_p = workspace_root / path
            if alt_p.exists() or alt_p.parent.exists():
                return str(alt_p)
        except Exception:
            pass
            
        return str(p)

    pdf_arg = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF_PATH
    db_arg = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DB_PATH

    resolved_pdf = resolve_path(pdf_arg)
    resolved_db = resolve_path(db_arg)

    if not Path(resolved_pdf).exists():
        print(f"Error: PDF statement file not found at '{pdf_arg}' (resolved to '{resolved_pdf}')")
        print("Usage: python pdf_parser.py [pdf_path] [db_path]")
        sys.exit(1)

    parse_axis_statement(resolved_pdf, resolved_db)


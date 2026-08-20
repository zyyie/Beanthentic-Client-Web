"""Add order-detail columns to customer_transaction and backfill from client_form_json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import beanthentic_env
from config.client_transactions import (
    _order_details_from_form_json,
    _order_row_fields,
    ensure_customer_transaction_order_columns,
)


def main() -> int:
    beanthentic_env.load_dotenv()
    if not beanthentic_env.get_db_url():
        print("No database configured (BEANTHENTIC_DB_URL).")
        return 1

    conn = beanthentic_env.connect()
    try:
        cur = conn.cursor()
        cols = ensure_customer_transaction_order_columns(cur)
        conn.commit()
        print("Columns ensured:", sorted(c for c in cols if c.startswith(("coffee_", "bean_", "classification", "quantity_"))))

        cur.execute(
            """
            SELECT customer_transaction_id, client_form_json
            FROM customer_transaction
            WHERE client_form_json IS NOT NULL AND client_form_json <> ''
            ORDER BY customer_transaction_id ASC
            """
        )
        rows = cur.fetchall() or []
        updated = 0
        for row in rows:
            tx_id = int(row["customer_transaction_id"])
            details = _order_details_from_form_json(row.get("client_form_json"))
            fields = _order_row_fields(details)
            if not fields:
                continue
            sets = [f"{k} = %s" for k in fields if k in cols]
            if not sets:
                continue
            vals = [fields[k] for k in fields if k in cols] + [tx_id]
            cur.execute(
                f"UPDATE customer_transaction SET {', '.join(sets)} WHERE customer_transaction_id = %s",
                vals,
            )
            updated += 1
        conn.commit()
        print(f"Backfilled {updated} transaction(s) from client_form_json.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

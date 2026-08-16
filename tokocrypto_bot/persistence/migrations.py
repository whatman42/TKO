"""MODULE: tokocrypto_bot.persistence.migrations"""
import logging
from tokocrypto_bot.persistence.database import DatabaseManager, get_db_transaction
logger = logging.getLogger("NVRA.Migrations")
MIGRATIONS = [{"version": 1, "description": "Initial state schema", "queries": [
"CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, description TEXT NOT NULL, applied_at TEXT NOT NULL);",
"CREATE TABLE IF NOT EXISTS orders (client_order_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL, signal_id TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, order_type TEXT NOT NULL, price REAL, quantity REAL NOT NULL, status TEXT NOT NULL, exchange_order_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);",
"CREATE TABLE IF NOT EXISTS order_events (id INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT NOT NULL, previous_status TEXT, new_status TEXT NOT NULL, event_trigger TEXT NOT NULL, details_json TEXT, created_at TEXT NOT NULL, FOREIGN KEY (client_order_id) REFERENCES orders(client_order_id) ON DELETE CASCADE);",
"CREATE TABLE IF NOT EXISTS fills (id INTEGER PRIMARY KEY AUTOINCREMENT, fill_id TEXT UNIQUE, client_order_id TEXT NOT NULL, exchange_order_id TEXT, symbol TEXT NOT NULL, side TEXT NOT NULL, price REAL NOT NULL, quantity REAL NOT NULL, fee REAL DEFAULT 0.0, fee_asset TEXT, timestamp TEXT NOT NULL, FOREIGN KEY (client_order_id) REFERENCES orders(client_order_id));",
"CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, total_qty REAL NOT NULL, locked_qty REAL DEFAULT 0.0, avg_buy_price REAL DEFAULT 0.0, updated_at TEXT NOT NULL);",
"CREATE TABLE IF NOT EXISTS balances (asset TEXT PRIMARY KEY, free REAL NOT NULL, locked REAL DEFAULT 0.0, updated_at TEXT NOT NULL);",
"CREATE TABLE IF NOT EXISTS executions (execution_id TEXT PRIMARY KEY, status TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT, metadata_json TEXT);",
"CREATE TABLE IF NOT EXISTS reconciliation_events (id INTEGER PRIMARY KEY AUTOINCREMENT, reconciliation_id TEXT NOT NULL, trigger_source TEXT NOT NULL, status TEXT NOT NULL, discrepancies_found_json TEXT, action_taken_json TEXT, created_at TEXT NOT NULL);",
"CREATE TABLE IF NOT EXISTS system_events (id INTEGER PRIMARY KEY AUTOINCREMENT, level TEXT NOT NULL, component TEXT NOT NULL, message TEXT NOT NULL, payload_json TEXT, created_at TEXT NOT NULL);",
"CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);"
]}]
def run_migrations(db_manager: DatabaseManager) -> None:
    conn = db_manager.get_connection()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, description TEXT, applied_at TEXT);")
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(version) FROM schema_migrations")
        row = cursor.fetchone()
        current_version = row[0] if row[0] is not None else 0
        for migration in MIGRATIONS:
            ver = migration["version"]
            if ver > current_version:
                logger.info(f"Applying DB Migration v{ver}: {migration['description']}")
                with get_db_transaction(db_manager) as tx_conn:
                    for query in migration["queries"]:
                        tx_conn.execute(query)
                    from datetime import datetime, timezone
                    now_str = datetime.now(timezone.utc).isoformat()
                    tx_conn.execute("INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)", (ver, migration["description"], now_str))
    finally:
        conn.close()

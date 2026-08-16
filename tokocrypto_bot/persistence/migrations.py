"""
MODULE: tokocrypto_bot.persistence.migrations
DESCRIPTION: Schema version control — Phase 1.8-A exchange isolation (TKO-native).
"""
import logging
from tokocrypto_bot.persistence.database import DatabaseManager, get_db_transaction

logger = logging.getLogger("NVRA.Migrations")

MIGRATIONS = [
    {
        "version": 1,
        "description": "Initial state schema",
        "queries": [
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS orders (
                client_order_id TEXT PRIMARY KEY,
                execution_id TEXT NOT NULL,
                signal_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                price REAL,
                quantity REAL NOT NULL,
                status TEXT NOT NULL,
                exchange_order_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS order_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_order_id TEXT NOT NULL,
                previous_status TEXT,
                new_status TEXT NOT NULL,
                event_trigger TEXT NOT NULL,
                details_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (client_order_id) REFERENCES orders(client_order_id) ON DELETE CASCADE
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fill_id TEXT UNIQUE,
                client_order_id TEXT NOT NULL,
                exchange_order_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                quantity REAL NOT NULL,
                fee REAL DEFAULT 0.0,
                fee_asset TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (client_order_id) REFERENCES orders(client_order_id)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                total_qty REAL NOT NULL,
                locked_qty REAL DEFAULT 0.0,
                avg_buy_price REAL DEFAULT 0.0,
                updated_at TEXT NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS balances (
                asset TEXT PRIMARY KEY,
                free REAL NOT NULL,
                locked REAL DEFAULT 0.0,
                updated_at TEXT NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS executions (
                execution_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                metadata_json TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS reconciliation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reconciliation_id TEXT NOT NULL,
                trigger_source TEXT NOT NULL,
                status TEXT NOT NULL,
                discrepancies_found_json TEXT,
                action_taken_json TEXT,
                created_at TEXT NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS system_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                component TEXT NOT NULL,
                message TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """,
        ],
    },
    {
        "version": 2,
        "description": "Phase 1.7 protective order linkage columns",
        "queries": [
            "ALTER TABLE orders ADD COLUMN stop_price REAL;",
            "ALTER TABLE orders ADD COLUMN take_profit_price REAL;",
            "ALTER TABLE orders ADD COLUMN parent_client_order_id TEXT;",
            "ALTER TABLE orders ADD COLUMN protected_qty REAL;",
            """
            CREATE TABLE IF NOT EXISTS position_protection (
                symbol TEXT PRIMARY KEY,
                parent_entry_client_order_id TEXT,
                protective_client_order_id TEXT,
                protected_qty REAL DEFAULT 0.0,
                stop_price REAL,
                take_profit_price REAL,
                protection_status TEXT NOT NULL DEFAULT 'NONE',
                updated_at TEXT NOT NULL
            );
            """,
        ],
    },
    {
        "version": 3,
        "description": "Phase 1.8-A exchange_id / account_id isolation",
        "queries": [
            """
            CREATE TABLE IF NOT EXISTS exchange_accounts (
                exchange_id TEXT NOT NULL,
                account_id TEXT NOT NULL DEFAULT 'DEFAULT',
                label TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (exchange_id, account_id)
            );
            """,
            "INSERT OR IGNORE INTO exchange_accounts (exchange_id, account_id, label, created_at) VALUES ('TOKOCRYPTO', 'DEFAULT', 'default', datetime('now'));",
            "ALTER TABLE orders ADD COLUMN exchange_id TEXT;",
            "ALTER TABLE orders ADD COLUMN account_id TEXT;",
            "UPDATE orders SET exchange_id = 'TOKOCRYPTO' WHERE exchange_id IS NULL;",
            "UPDATE orders SET account_id = 'DEFAULT' WHERE account_id IS NULL;",
            "ALTER TABLE fills ADD COLUMN exchange_id TEXT;",
            "UPDATE fills SET exchange_id = 'TOKOCRYPTO' WHERE exchange_id IS NULL;",
            """
            CREATE TABLE IF NOT EXISTS positions_v3 (
                exchange_id TEXT NOT NULL DEFAULT 'TOKOCRYPTO',
                account_id TEXT NOT NULL DEFAULT 'DEFAULT',
                symbol TEXT NOT NULL,
                total_qty REAL NOT NULL,
                locked_qty REAL DEFAULT 0.0,
                avg_buy_price REAL DEFAULT 0.0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (exchange_id, symbol)
            );
            """,
            """
            INSERT OR IGNORE INTO positions_v3 (exchange_id, account_id, symbol, total_qty, locked_qty, avg_buy_price, updated_at)
            SELECT 'TOKOCRYPTO', 'DEFAULT', symbol, total_qty, locked_qty, avg_buy_price, updated_at FROM positions;
            """,
            "DROP TABLE IF EXISTS positions;",
            "ALTER TABLE positions_v3 RENAME TO positions;",
            """
            CREATE TABLE IF NOT EXISTS balances_v3 (
                exchange_id TEXT NOT NULL DEFAULT 'TOKOCRYPTO',
                account_id TEXT NOT NULL DEFAULT 'DEFAULT',
                asset TEXT NOT NULL,
                free REAL NOT NULL,
                locked REAL DEFAULT 0.0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (exchange_id, asset)
            );
            """,
            """
            INSERT OR IGNORE INTO balances_v3 (exchange_id, account_id, asset, free, locked, updated_at)
            SELECT 'TOKOCRYPTO', 'DEFAULT', asset, free, locked, updated_at FROM balances;
            """,
            "DROP TABLE IF EXISTS balances;",
            "ALTER TABLE balances_v3 RENAME TO balances;",
            """
            CREATE TABLE IF NOT EXISTS position_protection_v3 (
                exchange_id TEXT NOT NULL DEFAULT 'TOKOCRYPTO',
                account_id TEXT NOT NULL DEFAULT 'DEFAULT',
                symbol TEXT NOT NULL,
                parent_entry_client_order_id TEXT,
                protective_client_order_id TEXT,
                protected_qty REAL DEFAULT 0.0,
                stop_price REAL,
                take_profit_price REAL,
                protection_status TEXT NOT NULL DEFAULT 'NONE',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (exchange_id, symbol)
            );
            """,
            """
            INSERT OR IGNORE INTO position_protection_v3 (
                exchange_id, account_id, symbol, parent_entry_client_order_id, protective_client_order_id,
                protected_qty, stop_price, take_profit_price, protection_status, updated_at
            )
            SELECT 'TOKOCRYPTO', 'DEFAULT', symbol, parent_entry_client_order_id, protective_client_order_id,
                   protected_qty, stop_price, take_profit_price, protection_status, updated_at
            FROM position_protection;
            """,
            "DROP TABLE IF EXISTS position_protection;",
            "ALTER TABLE position_protection_v3 RENAME TO position_protection;",
            "CREATE INDEX IF NOT EXISTS idx_orders_exchange_status ON orders(exchange_id, status);",
            "CREATE INDEX IF NOT EXISTS idx_fills_exchange_symbol ON fills(exchange_id, symbol);",
            "CREATE INDEX IF NOT EXISTS idx_orders_exchange_cid ON orders(exchange_id, client_order_id);",
        ],
    },

    {
        "version": 4,
        "description": "Composite UNIQUE(exchange_id, client_order_id) for order identity isolation",
        "queries": [
            """
            CREATE TABLE IF NOT EXISTS orders_v4 (
                exchange_id TEXT NOT NULL DEFAULT 'TOKOCRYPTO',
                account_id TEXT NOT NULL DEFAULT 'DEFAULT',
                client_order_id TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                signal_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                price REAL,
                quantity REAL NOT NULL,
                status TEXT NOT NULL,
                exchange_order_id TEXT,
                stop_price REAL,
                take_profit_price REAL,
                parent_client_order_id TEXT,
                protected_qty REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (exchange_id, client_order_id)
            );
            """,
            """
            INSERT OR IGNORE INTO orders_v4 (
                exchange_id, account_id, client_order_id, execution_id, signal_id, symbol, side, order_type,
                price, quantity, status, exchange_order_id, stop_price, take_profit_price,
                parent_client_order_id, protected_qty, created_at, updated_at
            )
            SELECT
                COALESCE(exchange_id, 'TOKOCRYPTO'),
                COALESCE(account_id, 'DEFAULT'),
                client_order_id, execution_id, signal_id, symbol, side, order_type,
                price, quantity, status, exchange_order_id,
                stop_price, take_profit_price, parent_client_order_id, protected_qty,
                created_at, updated_at
            FROM orders;
            """,
            "DROP TABLE IF EXISTS orders;",
            "ALTER TABLE orders_v4 RENAME TO orders;",
            "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(exchange_id, status);",
            "CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(exchange_id, symbol);",
            """
            CREATE TABLE IF NOT EXISTS order_events_v4 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange_id TEXT NOT NULL DEFAULT 'TOKOCRYPTO',
                client_order_id TEXT NOT NULL,
                previous_status TEXT,
                new_status TEXT NOT NULL,
                event_trigger TEXT NOT NULL,
                details_json TEXT,
                created_at TEXT NOT NULL
            );
            """,
            """
            INSERT OR IGNORE INTO order_events_v4 (id, exchange_id, client_order_id, previous_status, new_status, event_trigger, details_json, created_at)
            SELECT id, 'TOKOCRYPTO', client_order_id, previous_status, new_status, event_trigger, details_json, created_at FROM order_events;
            """,
            "DROP TABLE IF EXISTS order_events;",
            "ALTER TABLE order_events_v4 RENAME TO order_events;",
            # fills: drop FK constraint by rebuild without FK to orders
            """
            CREATE TABLE IF NOT EXISTS fills_v4 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fill_id TEXT UNIQUE,
                exchange_id TEXT NOT NULL DEFAULT 'TOKOCRYPTO',
                client_order_id TEXT NOT NULL,
                exchange_order_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                quantity REAL NOT NULL,
                fee REAL DEFAULT 0.0,
                fee_asset TEXT,
                timestamp TEXT NOT NULL
            );
            """,
            """
            INSERT OR IGNORE INTO fills_v4 (id, fill_id, exchange_id, client_order_id, exchange_order_id, symbol, side, price, quantity, fee, fee_asset, timestamp)
            SELECT id, fill_id, COALESCE(exchange_id,'TOKOCRYPTO'), client_order_id, exchange_order_id, symbol, side, price, quantity, fee, fee_asset, timestamp FROM fills;
            """,
            "DROP TABLE IF EXISTS fills;",
            "ALTER TABLE fills_v4 RENAME TO fills;",
            "CREATE INDEX IF NOT EXISTS idx_fills_exchange_cid ON fills(exchange_id, client_order_id);",
        ],
    },

]

def run_migrations(db_manager: DatabaseManager) -> None:
    """Apply pending migrations transactionally."""
    conn = db_manager.get_connection()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, description TEXT, applied_at TEXT);"
        )
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
                        try:
                            tx_conn.execute(query)
                        except Exception as e:
                            msg = str(e).lower()
                            if "duplicate column" in msg or "already exists" in msg:
                                logger.warning(f"Migration v{ver} statement skipped: {e}")
                            else:
                                raise
                    from datetime import datetime, timezone
                    now_str = datetime.now(timezone.utc).isoformat()
                    tx_conn.execute(
                        "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
                        (ver, migration["description"], now_str),
                    )
    finally:
        conn.close(),


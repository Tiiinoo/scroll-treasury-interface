"""
Scroll DAO Treasury Tracker - Database Models
===============================================
SQLite database setup using raw SQL for zero extra dependencies.
"""

import sqlite3
from flask import g, has_app_context
from config import DATABASE_PATH


def connect_db() -> sqlite3.Connection:
    """Create a new database connection."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_db() -> sqlite3.Connection:
    """Get a database connection. Uses Flask g if in a request context."""
    if has_app_context():
        if 'db' not in g:
            g.db = connect_db()
        return g.db
    return connect_db()


def close_db(_e=None):
    """Close the database connection if it exists."""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    """Create tables if they don't exist."""
    conn = connect_db()
    cur = conn.cursor()

    cur.executescript("""
    -- Wallets table
    CREATE TABLE IF NOT EXISTS wallets (
        id          TEXT PRIMARY KEY,          -- slug, e.g. 'treasury'
        name        TEXT NOT NULL,
        address     TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT ''
    );

    -- Transactions table
    CREATE TABLE IF NOT EXISTS transactions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet_id       TEXT NOT NULL,
        tx_hash         TEXT NOT NULL,
        block_number    INTEGER,
        timestamp       INTEGER NOT NULL,       -- unix epoch
        from_address    TEXT NOT NULL,
        to_address      TEXT NOT NULL,
        value           TEXT NOT NULL DEFAULT '0',  -- raw wei / smallest unit
        value_decimal   REAL NOT NULL DEFAULT 0,    -- human-readable amount
        token_symbol    TEXT NOT NULL DEFAULT 'ETH',
        token_name      TEXT NOT NULL DEFAULT 'Ether',
        token_decimals  INTEGER NOT NULL DEFAULT 18,
        contract_address TEXT NOT NULL DEFAULT '',   -- '' for native ETH
        tx_type         TEXT NOT NULL DEFAULT 'normal', -- normal | erc20 | internal
        direction       TEXT NOT NULL DEFAULT 'out',    -- in | out
        category        TEXT NOT NULL DEFAULT 'Uncategorised',
        notes           TEXT NOT NULL DEFAULT '',
        gas_used        INTEGER NOT NULL DEFAULT 0,
        gas_price       TEXT NOT NULL DEFAULT '0',
        is_error        INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (wallet_id) REFERENCES wallets(id),
        UNIQUE(tx_hash, wallet_id, tx_type, from_address, to_address, contract_address)
    );

    -- Balances cache
    CREATE TABLE IF NOT EXISTS balances (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet_id   TEXT NOT NULL,
        token_symbol TEXT NOT NULL DEFAULT 'ETH',
        token_name  TEXT NOT NULL DEFAULT 'Ether',
        contract_address TEXT NOT NULL DEFAULT '',
        balance     TEXT NOT NULL DEFAULT '0',
        balance_decimal REAL NOT NULL DEFAULT 0,
        last_updated INTEGER NOT NULL,
        FOREIGN KEY (wallet_id) REFERENCES wallets(id),
        UNIQUE(wallet_id, contract_address)
    );

    -- Fetch tracking
    CREATE TABLE IF NOT EXISTS fetch_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet_id   TEXT NOT NULL,
        fetch_type  TEXT NOT NULL,   -- normal | erc20 | internal
        last_block  INTEGER NOT NULL DEFAULT 0,
        fetched_at  INTEGER NOT NULL,
        tx_count    INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (wallet_id) REFERENCES wallets(id)
    );

    -- Historical Token Prices
    CREATE TABLE IF NOT EXISTS token_prices (
        symbol      TEXT NOT NULL,
        date        TEXT NOT NULL,  -- YYYY-MM-DD
        price       REAL NOT NULL,
        PRIMARY KEY (symbol, date)
    );

    -- Indexes
    CREATE INDEX IF NOT EXISTS idx_tx_wallet       ON transactions(wallet_id);
    CREATE INDEX IF NOT EXISTS idx_tx_timestamp     ON transactions(timestamp);
    CREATE INDEX IF NOT EXISTS idx_tx_category      ON transactions(category);
    CREATE INDEX IF NOT EXISTS idx_tx_direction     ON transactions(direction);
    CREATE INDEX IF NOT EXISTS idx_tx_token         ON transactions(token_symbol);
    CREATE INDEX IF NOT EXISTS idx_balances_wallet  ON balances(wallet_id);
    """)

    conn.commit()
    conn.close()


def seed_wallets(multisigs: dict):
    """Insert or update wallet records from config.

    If a wallet's address has changed, all stale data (transactions,
    balances, fetch history) is automatically cleared so the fetcher
    re-populates from the new address.
    """
    import logging
    logger = logging.getLogger(__name__)

    conn = connect_db()
    for slug, info in multisigs.items():
        new_address = info["address"]

        # Detect address changes and clear stale data
        existing = conn.execute(
            "SELECT address FROM wallets WHERE id = ?", (slug,)
        ).fetchone()

        if existing and existing["address"] and existing["address"] != new_address:
            logger.warning(
                "Address changed for '%s': %s… → %s…. Clearing stale data.",
                slug, existing["address"][:10], new_address[:10] if new_address else "(empty)"
            )
            conn.execute("DELETE FROM transactions WHERE wallet_id = ?", (slug,))
            conn.execute("DELETE FROM balances WHERE wallet_id = ?", (slug,))
            conn.execute("DELETE FROM fetch_log WHERE wallet_id = ?", (slug,))

        conn.execute(
            """INSERT INTO wallets (id, name, address, description)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name,
                 address=excluded.address,
                 description=excluded.description""",
            (slug, info["name"], new_address, info["description"]),
        )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    from config import MULTISIGS
    init_db()
    seed_wallets(MULTISIGS)
    print("Database initialised and wallets seeded.")

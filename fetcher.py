"""
Scroll DAO Treasury Tracker - Scrollscan Fetcher
==================================================
Fetches transactions from the Scrollscan (Etherscan-compatible) API
and stores them in the SQLite database.
"""

import time
import logging
from datetime import datetime, timezone
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from models import get_db
from config import SCROLLSCAN_API_BASE, SCROLLSCAN_API_KEY, SCROLL_CHAIN_ID, MULTISIGS, TOKEN_COINGECKO_IDS

# Safe Transaction Service API for Scroll
SAFE_API_BASE = "https://safe-transaction-scroll.safe.global/api/v1"

# Configure logger
logger = logging.getLogger(__name__)

# Configure robust session
def create_session():
    s = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    s.mount('https://', HTTPAdapter(max_retries=retries))
    return s

session = create_session()



# ── Helpers ──────────────────────────────────────────────────────────────

def _api_get(params: dict) -> list:
    """Make a GET request to Scrollscan API and return the result list."""
    if SCROLLSCAN_API_KEY:
        params["apikey"] = SCROLLSCAN_API_KEY
    
    # Add chainid for V2 API
    params["chainid"] = SCROLL_CHAIN_ID
    
    
    try:
        resp = session.get(SCROLLSCAN_API_BASE, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "1" and isinstance(data.get("result"), list):
            return data["result"]
        # status 0 can mean "no transactions found" – not an error
        return []
    except Exception as e:
        logger.error("API error: %s", e)
        return []


def _get_last_block(wallet_id: str, fetch_type: str) -> int:
    """Get the last fetched block for a wallet + type combination."""
    conn = get_db()
    row = conn.execute(
        "SELECT last_block FROM fetch_log WHERE wallet_id=? AND fetch_type=? ORDER BY fetched_at DESC LIMIT 1",
        (wallet_id, fetch_type),
    ).fetchone()
    conn.close()
    return row["last_block"] if row else 0


def _record_fetch(wallet_id: str, fetch_type: str, last_block: int, count: int):
    conn = get_db()
    conn.execute(
        "INSERT INTO fetch_log (wallet_id, fetch_type, last_block, fetched_at, tx_count) VALUES (?,?,?,?,?)",
        (wallet_id, fetch_type, last_block, int(time.time()), count),
    )
    conn.commit()
    conn.close()


# ── Normal (ETH) transactions ───────────────────────────────────────────

def fetch_normal_transactions(wallet_id: str, address: str):
    """Fetch normal ETH transactions for a wallet."""
    if not address:
        return 0
    start_block = _get_last_block(wallet_id, "normal")
    txs = _api_get({
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": start_block,
        "endblock": 99999999,
        "sort": "asc",
        "offset": 10000,
        "page": 1,
    })
    return _store_normal_txs(wallet_id, address, txs)


def _store_normal_txs(wallet_id: str, address: str, txs: list) -> int:
    """Parse and insert normal transactions."""
    conn = get_db()
    inserted = 0
    max_block: int = 0
    addr_lower = address.lower()

    for tx in txs:
        block = int(tx.get("blockNumber", 0))
        max_block = max(max_block, block)

        value_wei = tx.get("value", "0")
        decimals = 18
        try:
            value_decimal = int(value_wei) / (10 ** decimals)
        except (ValueError, ZeroDivisionError):
            value_decimal = 0

        direction = "out" if tx.get("from", "").lower() == addr_lower else "in"

        try:
            cursor = conn.execute(
                """INSERT INTO transactions
                   (wallet_id, tx_hash, block_number, timestamp, from_address,
                    to_address, value, value_decimal, token_symbol, token_name,
                    token_decimals, contract_address, tx_type, direction,
                    gas_used, gas_price, is_error)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT DO NOTHING""",
                (
                    wallet_id,
                    tx.get("hash", ""),
                    block,
                    int(tx.get("timeStamp", 0)),
                    tx.get("from", ""),
                    tx.get("to", ""),
                    value_wei,
                    value_decimal,
                    "ETH",
                    "Ether",
                    decimals,
                    "",
                    "normal",
                    direction,
                    int(tx.get("gasUsed", 0)),
                    tx.get("gasPrice", "0"),
                    int(tx.get("isError", 0)),
                ),
            )
            inserted += cursor.rowcount
        except Exception as e:
            logger.error("[fetcher] Insert error (normal): %s", e)

    conn.commit()
    conn.close()

    if max_block > 0:
        _record_fetch(wallet_id, "normal", max_block, inserted)

    logger.info("%s/normal: %d new tx (of %d fetched)", wallet_id, inserted, len(txs))
    return inserted


# ── ERC-20 token transactions ───────────────────────────────────────────

def fetch_erc20_transactions(wallet_id: str, address: str):
    """Fetch ERC-20 token transfers for a wallet."""
    if not address:
        return 0
    start_block = _get_last_block(wallet_id, "erc20")
    txs = _api_get({
        "module": "account",
        "action": "tokentx",
        "address": address,
        "startblock": start_block,
        "endblock": 99999999,
        "sort": "asc",
        "offset": 10000,
        "page": 1,
    })
    return _store_erc20_txs(wallet_id, address, txs)


def _store_erc20_txs(wallet_id: str, address: str, txs: list) -> int:
    conn = get_db()
    inserted = 0
    max_block: int = 0
    addr_lower = address.lower()

    for tx in txs:
        block = int(tx.get("blockNumber", 0))
        max_block = max(max_block, block)

        value_raw = tx.get("value", "0")
        decimals = int(tx.get("tokenDecimal", 18))
        try:
            value_decimal = int(value_raw) / (10 ** decimals)
        except (ValueError, ZeroDivisionError):
            value_decimal = 0

        direction = "out" if tx.get("from", "").lower() == addr_lower else "in"

        try:
            cursor = conn.execute(
                """INSERT INTO transactions
                   (wallet_id, tx_hash, block_number, timestamp, from_address,
                    to_address, value, value_decimal, token_symbol, token_name,
                    token_decimals, contract_address, tx_type, direction,
                    gas_used, gas_price, is_error)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT DO NOTHING""",
                (
                    wallet_id,
                    tx.get("hash", ""),
                    block,
                    int(tx.get("timeStamp", 0)),
                    tx.get("from", ""),
                    tx.get("to", ""),
                    value_raw,
                    value_decimal,
                    tx.get("tokenSymbol", "UNKNOWN"),
                    tx.get("tokenName", "Unknown Token"),
                    decimals,
                    tx.get("contractAddress", ""),
                    "erc20",
                    direction,
                    int(tx.get("gasUsed", 0)),
                    tx.get("gasPrice", "0"),
                    0,
                ),
            )
            inserted += cursor.rowcount
        except Exception as e:
            logger.error("[fetcher] Insert error (erc20): %s", e)

    conn.commit()
    conn.close()

    if max_block > 0:
        _record_fetch(wallet_id, "erc20", max_block, inserted)

    logger.info("%s/erc20: %d new tx (of %d fetched)", wallet_id, inserted, len(txs))
    return inserted


# ── Internal transactions ───────────────────────────────────────────────

def fetch_internal_transactions(wallet_id: str, address: str):
    """Fetch internal ETH transactions for a wallet."""
    if not address:
        return 0
    start_block = _get_last_block(wallet_id, "internal")
    txs = _api_get({
        "module": "account",
        "action": "txlistinternal",
        "address": address,
        "startblock": start_block,
        "endblock": 99999999,
        "sort": "asc",
        "offset": 10000,
        "page": 1,
    })
    return _store_internal_txs(wallet_id, address, txs)


def _store_internal_txs(wallet_id: str, address: str, txs: list) -> int:
    conn = get_db()
    inserted = 0
    max_block: int = 0
    addr_lower = address.lower()

    for tx in txs:
        block = int(tx.get("blockNumber", 0))
        max_block = max(max_block, block)

        value_wei = tx.get("value", "0")
        try:
            value_decimal = int(value_wei) / (10 ** 18)
        except (ValueError, ZeroDivisionError):
            value_decimal = 0

        direction = "out" if tx.get("from", "").lower() == addr_lower else "in"

        try:
            cursor = conn.execute(
                """INSERT INTO transactions
                   (wallet_id, tx_hash, block_number, timestamp, from_address,
                    to_address, value, value_decimal, token_symbol, token_name,
                    token_decimals, contract_address, tx_type, direction,
                    gas_used, gas_price, is_error)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT DO NOTHING""",
                (
                    wallet_id,
                    tx.get("hash", ""),
                    block,
                    int(tx.get("timeStamp", 0)),
                    tx.get("from", ""),
                    tx.get("to", ""),
                    value_wei,
                    value_decimal,
                    "ETH",
                    "Ether",
                    18,
                    "",
                    "internal",
                    direction,
                    int(tx.get("gasUsed", 0)),
                    "0",
                    int(tx.get("isError", 0)),
                ),
            )
            inserted += cursor.rowcount
        except Exception as e:
            logger.error("[fetcher] Insert error (internal): %s", e)

    conn.commit()
    conn.close()

    if max_block > 0:
        _record_fetch(wallet_id, "internal", max_block, inserted)

    logger.info("%s/internal: %d new tx (of %d fetched)", wallet_id, inserted, len(txs))
    return inserted


# ── Balance fetching ────────────────────────────────────────────────────

def fetch_eth_balance(wallet_id: str, address: str):
    """Fetch native ETH balance for a wallet."""
    if not address:
        return
    # For balance endpoint, result is a string not a list
    # Need to handle differently
    params = {"module": "account", "action": "balance", "address": address, "tag": "latest"}
    if SCROLLSCAN_API_KEY:
        params["apikey"] = SCROLLSCAN_API_KEY
    try:
        resp = session.get(SCROLLSCAN_API_BASE, params=params, timeout=30)
        data = resp.json()
        if data.get("status") == "1":
            balance_wei = data["result"]
            balance_decimal = int(balance_wei) / (10 ** 18)
            conn = get_db()
            conn.execute(
                """INSERT INTO balances (wallet_id, token_symbol, token_name, contract_address, balance, balance_decimal, last_updated)
                   VALUES (?, 'ETH', 'Ether', '', ?, ?, ?)
                   ON CONFLICT(wallet_id, contract_address) DO UPDATE SET
                     balance=excluded.balance, balance_decimal=excluded.balance_decimal, last_updated=excluded.last_updated""",
                (wallet_id, balance_wei, balance_decimal, int(time.time())),
            )
            conn.commit()
            conn.close()
            logger.info("%s ETH balance: %.6f", wallet_id, balance_decimal)
    except Exception as e:
        logger.error("Balance error: %s", e)


# ── Master fetch ────────────────────────────────────────────────────────

def fetch_all():
    """Fetch all transaction types for all configured wallets."""
    logger.info("Starting full fetch at %s", time.strftime('%Y-%m-%d %H:%M:%S'))
    for wallet_id, info in MULTISIGS.items():
        address = info["address"]
        if not address:
            logger.warning("Skipping %s (no address configured)", wallet_id)
            continue
        logger.info("Fetching %s (%s...)", wallet_id, address[:10])
        fetch_normal_transactions(wallet_id, address)
        time.sleep(0.3)  # Rate limiting
        fetch_erc20_transactions(wallet_id, address)
        time.sleep(0.3)
        fetch_internal_transactions(wallet_id, address)
        time.sleep(0.3)
        fetch_safe_multisig_txs(wallet_id, address)
        time.sleep(0.3)
        fetch_eth_balance(wallet_id, address)
        time.sleep(0.3)
    # Also compute token balances from transactions
    _compute_token_balances()
    # Fetch historical prices for new transactions
    fetch_historical_prices()
    logger.info("Fetch complete at %s", time.strftime('%Y-%m-%d %H:%M:%S'))


def fetch_historical_prices():
    """Fetch missing historical prices for tokens in transactions."""
    conn = get_db()
    # Find (symbol, date) pairs that are in transactions but not in token_prices
    # We only care about outgoing transactions for spending analysis
    rows = conn.execute("""
        SELECT DISTINCT 
            token_symbol, 
            date(timestamp, 'unixepoch') as tx_date
        FROM transactions
        WHERE direction = 'out' 
          AND is_error = 0
          AND (token_symbol, date(timestamp, 'unixepoch')) NOT IN (
              SELECT symbol, date FROM token_prices
          )
        UNION
        -- Also ensure we have SCR price for every date with an outgoing tx
        SELECT 
            'SCR' as token_symbol, 
            date(timestamp, 'unixepoch') as tx_date
        FROM transactions
        WHERE direction = 'out' 
          AND is_error = 0
          AND ('SCR', date(timestamp, 'unixepoch')) NOT IN (
              SELECT symbol, date FROM token_prices
          )
    """).fetchall()
    conn.close()

    if not rows:
        logger.info("No new historical prices to fetch.")
        return

    logger.info("Need to fetch %d historical prices...", len(rows))
    
    # CoinGecko ID mapping (from config)
    cg_ids = TOKEN_COINGECKO_IDS

    # Group by symbol to batch/manage
    by_symbol = {}
    for r in rows:
        sym = r["token_symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = []
        by_symbol[sym].append(r["tx_date"])

    for sym, dates in by_symbol.items():
        cg_id = cg_ids.get(sym)
        if not cg_id:
            logger.warning("Skipping unknown token %s for historical price", sym)
            # Insert 0 so we don't keep retrying
            conn = get_db()
            for d in dates:
                conn.execute("INSERT OR IGNORE INTO token_prices (symbol, date, price) VALUES (?, ?, ?)", (sym, d, 0))
            conn.commit()
            conn.close()
            continue

        for d in dates:
            # DefiLlama historical API: /prices/historical/{timestamp}/{protocol}:{token}
            # Need strict timestamp for that date. Let's use noon UTC on that day.
            try:
                dt_ts = int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) + 43200
                
                url = f"https://coins.llama.fi/prices/historical/{dt_ts}/coingecko:{cg_id}?searchWidth=12h"
                
                logger.info("Fetching price for %s on %s...", sym, d)
                resp = session.get(url, timeout=10)
                
                if resp.status_code == 200:
                    data = resp.json()
                    coins = data.get("coins", {})
                    key = f"coingecko:{cg_id}"
                    price = coins.get(key, {}).get("price", 0)
                    
                    if price > 0:
                        conn = get_db()
                        conn.execute(
                            "INSERT OR IGNORE INTO token_prices (symbol, date, price) VALUES (?, ?, ?)",
                            (sym, d, price)
                        )
                        conn.commit()
                        conn.close()
                    else:
                        logger.warning("No price found for %s on %s", sym, d)
                else:
                    logger.error("[fetcher] Failed %s: %s", url, resp.status_code)
                
                # Polite delay
                time.sleep(0.5)
                
            except Exception as e:
                logger.error("[fetcher] Error fetching %s price: %s", sym, e)


# ── Safe Multisig Data ──────────────────────────────────────────────────

def fetch_safe_multisig_txs(wallet_id: str, address: str):
    """Fetch multisig transaction details (signers) from Safe Transaction Service."""
    if not address:
        return

    # We only care about executed transactions
    url = f"{SAFE_API_BASE}/safes/{address}/multisig-transactions/"
    params = {
        "executed": "true",
        "limit": 100,  # Fetch last 100
        "ordering": "-executionDate"
    }

    try:
        resp = session.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            logger.warning("Safe API error %s: %s", resp.status_code, resp.text)
            return
        
        data = resp.json()
        results = data.get("results", [])
        
        conn = get_db()
        updated_count = 0
        
        for tx in results:
            tx_hash = tx.get("transactionHash")
            if not tx_hash:
                continue
                
            # Extract signers
            confirmations = tx.get("confirmations", [])
            if not confirmations:
                continue
                
            # specific logic: get owners from confirmations
            signers = sorted([c["owner"] for c in confirmations])
            signers_str = ",".join(signers)
            
            # Update transaction if it exists
            # We only really care about outgoing transactions which match these multisig txs
            cursor = conn.execute(
                "UPDATE transactions SET signers = ? WHERE tx_hash = ? AND wallet_id = ?",
                (signers_str, tx_hash, wallet_id)
            )
            updated_count += cursor.rowcount
            
        conn.commit()
        conn.close()
        
        if updated_count > 0:
            logger.info("%s/safe: Updated signers for %d transactions", wallet_id, updated_count)
            
    except Exception as e:
        logger.error("[fetcher] Safe API error: %s", e)


def _compute_token_balances(wallet_id: str = ""):
    """Compute approximate token balances from transaction history.
    
    If wallet_id is provided, only recomputes for that wallet.
    """
    conn = get_db()
    query = """
        SELECT wallet_id, token_symbol, token_name, contract_address, token_decimals,
               SUM(CASE WHEN direction='in' THEN value_decimal ELSE 0 END) as total_in,
               SUM(CASE WHEN direction='out' THEN value_decimal ELSE 0 END) as total_out
        FROM transactions
        WHERE contract_address != '' AND is_error = 0
    """
    params: list = []
    if wallet_id:
        query += " AND wallet_id = ?"
        params.append(wallet_id)
    query += " GROUP BY wallet_id, contract_address"

    rows = conn.execute(query, params).fetchall()

    for row in rows:
        balance = row["total_in"] - row["total_out"]
        conn.execute(
            """INSERT INTO balances (wallet_id, token_symbol, token_name, contract_address, balance, balance_decimal, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(wallet_id, contract_address) DO UPDATE SET
                 token_symbol=excluded.token_symbol, token_name=excluded.token_name,
                 balance=excluded.balance, balance_decimal=excluded.balance_decimal,
                 last_updated=excluded.last_updated""",
            (row["wallet_id"], row["token_symbol"], row["token_name"],
             row["contract_address"], str(int(balance * (10 ** row["token_decimals"]))),
             balance, int(time.time())),
        )

    conn.commit()
    conn.close()


def fetch_single_wallet(wallet_id: str):
    """Fetch data for a single wallet."""
    info = MULTISIGS.get(wallet_id)
    if not info or not info["address"]:
        return
    address = info["address"]
    fetch_normal_transactions(wallet_id, address)
    time.sleep(0.3)
    fetch_erc20_transactions(wallet_id, address)
    time.sleep(0.3)
    fetch_internal_transactions(wallet_id, address)
    time.sleep(0.3)
    fetch_safe_multisig_txs(wallet_id, address)
    time.sleep(0.3)
    fetch_eth_balance(wallet_id, address)
    _compute_token_balances(wallet_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    fetch_all()

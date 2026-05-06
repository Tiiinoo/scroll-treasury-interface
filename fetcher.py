"""
Scroll DAO Treasury Tracker - Transaction Fetcher
==================================================
Fetches transactions from Scrollscan (Scroll L2) and Etherscan (Ethereum mainnet)
and stores them in the SQLite database.
"""

import time
import logging
import urllib.parse
from datetime import datetime, timezone
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from models import get_db, connect_db
from config import (
    SCROLLSCAN_API_BASE, SCROLLSCAN_API_KEY,
    ETHERSCAN_API_BASE, ETHERSCAN_API_KEY,
    MULTISIGS, MAINNET_MULTISIGS, TOKEN_COINGECKO_IDS,
)

# Safe Transaction Service API bases (new consolidated endpoint, old per-chain URLs redirect 308)
SAFE_SCROLL_API_BASE = "https://api.safe.global/tx-service/scr/api/v1"
SAFE_MAINNET_API_BASE = "https://api.safe.global/tx-service/eth/api/v1"

logger = logging.getLogger(__name__)


def create_session():
    s = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],  # 429 handled manually — no auto-retry
        allowed_methods=["GET"]
    )
    s.mount('https://', HTTPAdapter(max_retries=retries))
    return s

session = create_session()


# ── API helpers ───────────────────────────────────────────────────────────

def _api_get(params: dict, api_base: str, api_key: str) -> list:
    """Make a GET request to an Etherscan-compatible API and return the result list."""
    if api_key:
        params["apikey"] = api_key
    try:
        resp = session.get(api_base, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "1" and isinstance(data.get("result"), list):
            return data["result"]
        return []
    except Exception as e:
        logger.error("API error (%s): %s", api_base, e)
        return []


def _scroll_api_get(params: dict) -> list:
    return _api_get(params, SCROLLSCAN_API_BASE, SCROLLSCAN_API_KEY)


def _eth_api_get(params: dict) -> list:
    params = {"chainid": "1", **params}  # Etherscan V2 requires chainid
    return _api_get(params, ETHERSCAN_API_BASE, ETHERSCAN_API_KEY)


# ── Fetch-log helpers ─────────────────────────────────────────────────────

def _get_last_block(wallet_id: str, fetch_type: str, chain: str = 'scroll') -> int:
    conn = get_db()
    row = conn.execute(
        "SELECT last_block FROM fetch_log WHERE wallet_id=? AND fetch_type=? AND chain=? ORDER BY fetched_at DESC LIMIT 1",
        (wallet_id, fetch_type, chain),
    ).fetchone()
    conn.close()
    return row["last_block"] if row else 0


def _record_fetch(wallet_id: str, fetch_type: str, last_block: int, count: int, chain: str = 'scroll'):
    conn = get_db()
    conn.execute(
        "INSERT INTO fetch_log (wallet_id, fetch_type, last_block, fetched_at, tx_count, chain) VALUES (?,?,?,?,?,?)",
        (wallet_id, fetch_type, last_block, int(time.time()), count, chain),
    )
    conn.commit()
    conn.close()


# ── Normal (ETH) transactions ─────────────────────────────────────────────

def fetch_normal_transactions(wallet_id: str, address: str, api_fn=None, chain: str = 'scroll'):
    if not address:
        return 0
    if api_fn is None:
        api_fn = _scroll_api_get
    start_block = _get_last_block(wallet_id, "normal", chain)
    txs = api_fn({
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": start_block,
        "endblock": 99999999,
        "sort": "asc",
        "offset": 10000,
        "page": 1,
    })
    return _store_normal_txs(wallet_id, address, txs, chain)


def _store_normal_txs(wallet_id: str, address: str, txs: list, chain: str = 'scroll') -> int:
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
                   (wallet_id, chain, tx_hash, block_number, timestamp, from_address,
                    to_address, value, value_decimal, token_symbol, token_name,
                    token_decimals, contract_address, tx_type, direction,
                    gas_used, gas_price, is_error)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT DO NOTHING""",
                (
                    wallet_id, chain,
                    tx.get("hash", ""),
                    block,
                    int(tx.get("timeStamp", 0)),
                    tx.get("from", ""),
                    tx.get("to", ""),
                    value_wei,
                    value_decimal,
                    "ETH", "Ether", 18, "",
                    "normal", direction,
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
        _record_fetch(wallet_id, "normal", max_block, inserted, chain)

    logger.info("%s/%s/normal: %d new tx (of %d fetched)", wallet_id, chain, inserted, len(txs))
    return inserted


# ── ERC-20 token transactions ─────────────────────────────────────────────

def fetch_erc20_transactions(wallet_id: str, address: str, api_fn=None, chain: str = 'scroll'):
    if not address:
        return 0
    if api_fn is None:
        api_fn = _scroll_api_get
    start_block = _get_last_block(wallet_id, "erc20", chain)
    txs = api_fn({
        "module": "account",
        "action": "tokentx",
        "address": address,
        "startblock": start_block,
        "endblock": 99999999,
        "sort": "asc",
        "offset": 10000,
        "page": 1,
    })
    return _store_erc20_txs(wallet_id, address, txs, chain)


def _store_erc20_txs(wallet_id: str, address: str, txs: list, chain: str = 'scroll') -> int:
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
                   (wallet_id, chain, tx_hash, block_number, timestamp, from_address,
                    to_address, value, value_decimal, token_symbol, token_name,
                    token_decimals, contract_address, tx_type, direction,
                    gas_used, gas_price, is_error)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT DO NOTHING""",
                (
                    wallet_id, chain,
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
                    "erc20", direction,
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
        _record_fetch(wallet_id, "erc20", max_block, inserted, chain)

    logger.info("%s/%s/erc20: %d new tx (of %d fetched)", wallet_id, chain, inserted, len(txs))
    return inserted


# ── Internal transactions ─────────────────────────────────────────────────

def fetch_internal_transactions(wallet_id: str, address: str, api_fn=None, chain: str = 'scroll'):
    if not address:
        return 0
    if api_fn is None:
        api_fn = _scroll_api_get
    start_block = _get_last_block(wallet_id, "internal", chain)
    txs = api_fn({
        "module": "account",
        "action": "txlistinternal",
        "address": address,
        "startblock": start_block,
        "endblock": 99999999,
        "sort": "asc",
        "offset": 10000,
        "page": 1,
    })
    return _store_internal_txs(wallet_id, address, txs, chain)


def _store_internal_txs(wallet_id: str, address: str, txs: list, chain: str = 'scroll') -> int:
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
                   (wallet_id, chain, tx_hash, block_number, timestamp, from_address,
                    to_address, value, value_decimal, token_symbol, token_name,
                    token_decimals, contract_address, tx_type, direction,
                    gas_used, gas_price, is_error)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT DO NOTHING""",
                (
                    wallet_id, chain,
                    tx.get("hash", ""),
                    block,
                    int(tx.get("timeStamp", 0)),
                    tx.get("from", ""),
                    tx.get("to", ""),
                    value_wei,
                    value_decimal,
                    "ETH", "Ether", 18, "",
                    "internal", direction,
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
        _record_fetch(wallet_id, "internal", max_block, inserted, chain)

    logger.info("%s/%s/internal: %d new tx (of %d fetched)", wallet_id, chain, inserted, len(txs))
    return inserted


# ── ETH balance fetching ──────────────────────────────────────────────────

def fetch_eth_balance(wallet_id: str, address: str, api_base: str = None, api_key: str = None, chain: str = 'scroll'):
    if not address:
        return
    if api_base is None:
        api_base = SCROLLSCAN_API_BASE
    if api_key is None:
        api_key = SCROLLSCAN_API_KEY

    params = {"module": "account", "action": "balance", "address": address, "tag": "latest"}
    if api_key:
        params["apikey"] = api_key
    if chain == 'ethereum':
        params["chainid"] = "1"  # Etherscan V2 requires chainid
    try:
        resp = session.get(api_base, params=params, timeout=30)
        data = resp.json()
        if data.get("status") == "1":
            balance_wei = data["result"]
            balance_decimal = int(balance_wei) / (10 ** 18)
            conn = get_db()
            conn.execute(
                """INSERT INTO balances (wallet_id, chain, token_symbol, token_name, contract_address, balance, balance_decimal, last_updated)
                   VALUES (?, ?, 'ETH', 'Ether', '', ?, ?, ?)
                   ON CONFLICT(wallet_id, contract_address, chain) DO UPDATE SET
                     balance=excluded.balance, balance_decimal=excluded.balance_decimal, last_updated=excluded.last_updated""",
                (wallet_id, chain, balance_wei, balance_decimal, int(time.time())),
            )
            conn.commit()
            conn.close()
            logger.info("%s/%s ETH balance: %.6f", wallet_id, chain, balance_decimal)
    except Exception as e:
        logger.error("Balance error (%s): %s", chain, e)


# ── Safe Multisig signers ─────────────────────────────────────────────────

def fetch_safe_multisig_txs(wallet_id: str, address: str, safe_api_base: str = None, chain: str = 'scroll'):
    if not address:
        return
    if safe_api_base is None:
        safe_api_base = SAFE_SCROLL_API_BASE

    conn = connect_db()
    try:
        # Skip the API call if no outgoing transactions are missing signers for this chain
        missing_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM transactions WHERE wallet_id = ? AND chain = ? AND direction = 'out' AND signers = '' AND is_error = 0",
            (wallet_id, chain)
        ).fetchone()["cnt"]

        if missing_count == 0:
            logger.info("%s/%s/safe: all signers populated, skipping API call", wallet_id, chain)
            return

        logger.info("%s/%s/safe: %d transactions missing signers, fetching...", wallet_id, chain, missing_count)

        safe_url = f"{safe_api_base}/safes/{address}/multisig-transactions/?executed=true&limit=100&ordering=-executionDate"
        logger.info("%s/%s/safe: fetching from %s", wallet_id, chain, safe_url[:80])

        resp = session.get(safe_url, timeout=30)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After") or resp.headers.get("x-ratelimit-reset", "unknown")
            logger.warning("%s/%s/safe: rate limited (429), retry-after: %s — falling back to calldata", wallet_id, chain, retry_after)
            _get_signers_from_calldata(wallet_id, address, chain, conn)
            return
        if resp.status_code != 200:
            logger.warning("%s/%s/safe: API returned %s — %s", wallet_id, chain, resp.status_code, resp.text[:200])
            return

        data = resp.json()
        results = data.get("results", [])
        logger.info("%s/%s/safe: got %d results", wallet_id, chain, len(results))

        updated_count = 0
        for tx in results:
            tx_hash = tx.get("transactionHash")
            if not tx_hash:
                continue
            confirmations = tx.get("confirmations", [])
            if not confirmations:
                continue
            signers = sorted([c["owner"] for c in confirmations])
            signers_str = ",".join(signers)
            cursor = conn.execute(
                "UPDATE transactions SET signers = ? WHERE tx_hash = ? AND wallet_id = ? AND chain = ?",
                (signers_str, tx_hash, wallet_id, chain)
            )
            updated_count += cursor.rowcount

        conn.commit()

        logger.info("%s/%s/safe: updated signers for %d row(s) (%d results from API)", wallet_id, chain, updated_count, len(results))

    except Exception as e:
        logger.error("[fetcher] Safe API error for %s/%s: %s", wallet_id, chain, e)
    finally:
        conn.close()


# ── Calldata-based signer recovery ───────────────────────────────────────

def _get_signers_from_calldata(wallet_id: str, address: str, chain: str, conn) -> int:
    """
    Recover multisig signers by decoding execTransaction calldata on-chain.

    Bypasses the Safe Transaction Service API entirely. For each outgoing
    transaction that is missing signers, fetches the raw tx input via the
    Etherscan/Scrollscan proxy API, decodes the execTransaction ABI calldata,
    computes the EIP-712 SafeTxHash, and recovers signer addresses via ecrecover.

    Returns the number of DB rows updated.
    """
    try:
        import eth_abi
        from eth_keys import keys as eth_keys_lib
        from eth_utils import keccak
    except ImportError as exc:
        logger.error("Cannot recover signers from calldata: %s (run: pip install eth_account)", exc)
        return 0

    EXEC_TX_SELECTOR = "6a761202"
    SAFE_TX_TYPEHASH = bytes.fromhex(
        "bb8310d486368db6bd6f849402fdd73ad53d316b5a4b2644ad6efe0f941286d8"
    )
    DOMAIN_SEPARATOR_TYPEHASH = bytes.fromhex(
        "47e79534a245952e8b16893a336b85a3d9ea9fa8c573f3d803afb92a79469218"
    )
    CHAIN_IDS = {"scroll": 534352, "ethereum": 1}

    chain_id = CHAIN_IDS.get(chain)
    if not chain_id:
        logger.warning("_get_signers_from_calldata: unknown chain %s", chain)
        return 0

    if chain == "ethereum":
        api_base, api_key, extra = ETHERSCAN_API_BASE, ETHERSCAN_API_KEY, {"chainid": "1"}
    else:
        api_base, api_key, extra = SCROLLSCAN_API_BASE, SCROLLSCAN_API_KEY, {}

    rows = conn.execute(
        "SELECT tx_hash, block_number FROM transactions "
        "WHERE wallet_id=? AND chain=? AND direction='out' AND signers='' AND is_error=0",
        (wallet_id, chain),
    ).fetchall()

    if not rows:
        return 0

    logger.info("%s/%s: calldata signer recovery for %d tx(s)", wallet_id, chain, len(rows))

    def _proxy(action: str, extra_params: dict):
        p = {**extra, "module": "proxy", "action": action, **extra_params}
        if api_key:
            p["apikey"] = api_key
        try:
            r = session.get(api_base, params=p, timeout=20)
            if r.status_code == 200:
                return r.json()
            logger.debug("proxy %s HTTP %s", action, r.status_code)
        except Exception as e:
            logger.debug("proxy %s error: %s", action, e)
        return None

    updated = 0

    for row in rows:
        tx_hash = row["tx_hash"]
        block_number = row["block_number"]

        try:
            # 1. Raw transaction input data
            tx_resp = _proxy("eth_getTransactionByHash", {"txhash": tx_hash})
            time.sleep(0.22)
            if not tx_resp:
                continue
            result = tx_resp.get("result")
            if not result or not isinstance(result, dict):
                continue
            input_data = result.get("input", "0x")
            if (not input_data
                    or input_data == "0x"
                    or not input_data.lower().startswith("0x" + EXEC_TX_SELECTOR)):
                continue  # not an execTransaction — no multisig pattern

            # 2. Safe nonce at block N-1 (before this tx executed)
            nonce_resp = _proxy("eth_call", {
                "to": address,
                "data": "0xaffed0e0",  # nonce() selector
                "tag": hex(max(0, block_number - 1)),
            })
            time.sleep(0.22)
            nonce = 0
            if nonce_resp:
                raw_nonce = nonce_resp.get("result", "0x0")
                if raw_nonce and raw_nonce not in ("0x", "0x0", ""):
                    nonce = int(raw_nonce, 16)

            # 3. Decode execTransaction ABI calldata (skip 4-byte selector)
            raw_input = bytes.fromhex(input_data[2:])
            payload = raw_input[4:]
            (to_addr, value, data_bytes, operation,
             safe_tx_gas, base_gas, inner_gas_price,
             gas_token, refund_receiver, signatures) = eth_abi.decode(
                ["address", "uint256", "bytes", "uint8",
                 "uint256", "uint256", "uint256",
                 "address", "address", "bytes"],
                payload,
            )

            if not signatures:
                continue

            # 4. EIP-712 domain separator (Safe v1.3+)
            domain_separator = keccak(eth_abi.encode(
                ["bytes32", "uint256", "address"],
                [DOMAIN_SEPARATOR_TYPEHASH, chain_id, address],
            ))

            # 5. SafeTxHash
            safe_tx_hash = keccak(eth_abi.encode(
                ["bytes32", "address", "uint256", "bytes32", "uint8",
                 "uint256", "uint256", "uint256", "address", "address", "uint256"],
                [SAFE_TX_TYPEHASH, to_addr, value, keccak(data_bytes), operation,
                 safe_tx_gas, base_gas, inner_gas_price,
                 gas_token, refund_receiver, nonce],
            ))

            # 6. Final EIP-712 signed hash
            message_hash = keccak(b"\x19\x01" + domain_separator + safe_tx_hash)

            # 7. Recover signer addresses from packed 65-byte signatures
            recovered = []
            for i in range(0, len(signatures) - 64, 65):
                sig_slice = signatures[i: i + 65]
                r_int = int.from_bytes(sig_slice[:32], "big")
                s_int = int.from_bytes(sig_slice[32:64], "big")
                v = sig_slice[64]

                if v in (27, 28):
                    v_norm = v - 27
                    hash_to_sign = message_hash
                elif v in (31, 32):
                    # eth_sign variant: Safe signs prefixed safeTxHash
                    v_norm = v - 31
                    hash_to_sign = keccak(
                        b"\x19Ethereum Signed Message:\n32" + safe_tx_hash
                    )
                else:
                    continue  # contract sig (v=0) or approved hash (v=1) — unrecoverable

                try:
                    sig_obj = eth_keys_lib.Signature(vrs=(v_norm, r_int, s_int))
                    pub_key = sig_obj.recover_public_key_from_msg_hash(hash_to_sign)
                    recovered.append(pub_key.to_checksum_address())
                except Exception:
                    pass

            if recovered:
                signers_str = ",".join(sorted(recovered))
                cur = conn.execute(
                    "UPDATE transactions SET signers=? WHERE tx_hash=? AND wallet_id=? AND chain=?",
                    (signers_str, tx_hash, wallet_id, chain),
                )
                updated += cur.rowcount
                logger.info(
                    "%s/%s: recovered %d signer(s) via calldata for %s…",
                    wallet_id, chain, len(recovered), tx_hash[:10],
                )

        except Exception as e:
            logger.error(
                "%s/%s: calldata recovery error for %s: %s",
                wallet_id, chain, tx_hash[:10], e,
            )
            time.sleep(0.22)

    if updated > 0:
        conn.commit()
        logger.info("%s/%s: calldata recovery updated %d row(s)", wallet_id, chain, updated)

    return updated


# ── Token balance computation ─────────────────────────────────────────────

def _compute_token_balances(wallet_id: str = ""):
    """Compute approximate token balances from transaction history."""
    conn = get_db()
    query = """
        SELECT wallet_id, chain, token_symbol, token_name, contract_address, token_decimals,
               SUM(CASE WHEN direction='in' THEN value_decimal ELSE 0 END) as total_in,
               SUM(CASE WHEN direction='out' THEN value_decimal ELSE 0 END) as total_out
        FROM transactions
        WHERE contract_address != '' AND is_error = 0
    """
    params: list = []
    if wallet_id:
        query += " AND wallet_id = ?"
        params.append(wallet_id)
    query += " GROUP BY wallet_id, chain, contract_address"

    rows = conn.execute(query, params).fetchall()

    for row in rows:
        balance = row["total_in"] - row["total_out"]
        conn.execute(
            """INSERT INTO balances (wallet_id, chain, token_symbol, token_name, contract_address, balance, balance_decimal, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(wallet_id, contract_address, chain) DO UPDATE SET
                 token_symbol=excluded.token_symbol, token_name=excluded.token_name,
                 balance=excluded.balance, balance_decimal=excluded.balance_decimal,
                 last_updated=excluded.last_updated""",
            (row["wallet_id"], row["chain"], row["token_symbol"], row["token_name"],
             row["contract_address"], str(int(balance * (10 ** row["token_decimals"]))),
             balance, int(time.time())),
        )

    conn.commit()
    conn.close()


# ── Historical prices ─────────────────────────────────────────────────────

def fetch_historical_prices():
    """Fetch missing historical prices for tokens in transactions."""
    conn = get_db()
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

    cg_ids = TOKEN_COINGECKO_IDS
    by_symbol: dict = {}
    for r in rows:
        sym = r["token_symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = []
        by_symbol[sym].append(r["tx_date"])

    for sym, dates in by_symbol.items():
        cg_id = cg_ids.get(sym)
        if not cg_id:
            logger.warning("Skipping unknown token %s for historical price", sym)
            conn = get_db()
            for d in dates:
                conn.execute("INSERT OR IGNORE INTO token_prices (symbol, date, price) VALUES (?, ?, ?)", (sym, d, 0))
            conn.commit()
            conn.close()
            continue

        for d in dates:
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

                time.sleep(0.5)
            except Exception as e:
                logger.error("[fetcher] Error fetching %s price: %s", sym, e)


# ── Per-wallet fetch helpers ──────────────────────────────────────────────

def _fetch_scroll_wallet(wallet_id: str, address: str):
    """Fetch all data for a wallet on Scroll."""
    fetch_normal_transactions(wallet_id, address, api_fn=_scroll_api_get, chain='scroll')
    time.sleep(0.3)
    fetch_erc20_transactions(wallet_id, address, api_fn=_scroll_api_get, chain='scroll')
    time.sleep(0.3)
    fetch_internal_transactions(wallet_id, address, api_fn=_scroll_api_get, chain='scroll')
    time.sleep(0.3)
    fetch_safe_multisig_txs(wallet_id, address, safe_api_base=SAFE_SCROLL_API_BASE, chain='scroll')
    time.sleep(0.3)
    fetch_eth_balance(wallet_id, address, api_base=SCROLLSCAN_API_BASE, api_key=SCROLLSCAN_API_KEY, chain='scroll')
    time.sleep(0.3)


def _fetch_mainnet_wallet(wallet_id: str, address: str):
    """Fetch all data for a wallet on Ethereum mainnet."""
    fetch_normal_transactions(wallet_id, address, api_fn=_eth_api_get, chain='ethereum')
    time.sleep(0.3)
    fetch_erc20_transactions(wallet_id, address, api_fn=_eth_api_get, chain='ethereum')
    time.sleep(0.3)
    fetch_internal_transactions(wallet_id, address, api_fn=_eth_api_get, chain='ethereum')
    time.sleep(0.3)
    fetch_safe_multisig_txs(wallet_id, address, safe_api_base=SAFE_MAINNET_API_BASE, chain='ethereum')
    time.sleep(0.3)
    fetch_eth_balance(wallet_id, address, api_base=ETHERSCAN_API_BASE, api_key=ETHERSCAN_API_KEY, chain='ethereum')
    time.sleep(0.3)


# ── Master fetch ──────────────────────────────────────────────────────────

def fetch_all():
    """Fetch all transaction types for all configured wallets on all chains."""
    logger.info("Starting full fetch at %s", time.strftime('%Y-%m-%d %H:%M:%S'))

    # Scroll L2 — all multisigs
    for wallet_id, info in MULTISIGS.items():
        address = info["address"]
        if not address:
            logger.warning("Skipping %s on scroll (no address configured)", wallet_id)
            continue
        logger.info("Fetching %s on scroll (%s...)", wallet_id, address[:10])
        _fetch_scroll_wallet(wallet_id, address)

    # Ethereum mainnet — treasury and committee only
    for wallet_id, address in MAINNET_MULTISIGS.items():
        if not address:
            continue
        logger.info("Fetching %s on ethereum mainnet (%s...)", wallet_id, address[:10])
        _fetch_mainnet_wallet(wallet_id, address)

    _compute_token_balances()
    fetch_historical_prices()
    logger.info("Fetch complete at %s", time.strftime('%Y-%m-%d %H:%M:%S'))


def fetch_single_wallet(wallet_id: str):
    """Fetch data for a single wallet (all chains it exists on)."""
    info = MULTISIGS.get(wallet_id)
    if not info or not info["address"]:
        return
    address = info["address"]
    _fetch_scroll_wallet(wallet_id, address)

    # Also fetch mainnet if this wallet exists there
    mainnet_address = MAINNET_MULTISIGS.get(wallet_id)
    if mainnet_address:
        _fetch_mainnet_wallet(wallet_id, mainnet_address)

    _compute_token_balances(wallet_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    fetch_all()

"""
Scroll DAO Treasury Tracker - Flask Application
=================================================
Main web application with public dashboard and auth-protected categorisation.
"""

import csv
import io
import os
import time
import logging
import sys
import threading
from datetime import datetime, timezone
from functools import wraps
from typing import Dict, Any
import requests

from flask import (
    Flask, jsonify, request, render_template, redirect,
    url_for, session, Response
)
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from flask_talisman import Talisman
from apscheduler.schedulers.background import BackgroundScheduler

from config import (
    SECRET_KEY, AUTH_USERNAME, AUTH_PASSWORD,
    MULTISIGS, CATEGORIES, BUDGETS, BUDGET_TOTALS,
    FETCH_INTERVAL_MINUTES, TOKEN_COINGECKO_IDS,
    BUDGET_OVERRIDES, SIGNER_ALIASES, NON_EXPENSE_CATEGORIES
)
from models import get_db, init_db, seed_wallets, close_db
from fetcher import fetch_all, fetch_single_wallet

# ── App Setup ────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = SECRET_KEY

# CORS — restrict to your domain in production
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
CORS(app, origins=ALLOWED_ORIGINS)

# Rate limiting
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["120 per minute"],
    storage_uri="memory://",
)

# CSRF protection — exempt JSON API routes since they use session auth
csrf = CSRFProtect(app)

# Security headers
csp = {
    "default-src": "'self'",
    "script-src": ["'self'", "'unsafe-inline'"],  # inline onclick handlers throughout the app
    "style-src": ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
    "font-src": ["'self'", "https://fonts.gstatic.com"],
    "img-src": ["'self'", "https:", "data:"],
    "connect-src": "'self'",
}
Talisman(
    app,
    content_security_policy=csp,
    force_https=os.environ.get("FORCE_HTTPS", "false").lower() == "true",
    session_cookie_secure=os.environ.get("FORCE_HTTPS", "false").lower() == "true",
    session_cookie_samesite="Lax",
)

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Register database teardown
app.teardown_appcontext(close_db)

# Initialise DB on startup
init_db()
seed_wallets(MULTISIGS)

# ── Background Scheduler ────────────────────────────────────────────────

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(fetch_all, "interval", minutes=FETCH_INTERVAL_MINUTES, id="fetch_all")
scheduler.start()

# Do an initial fetch in a background thread
threading.Thread(target=fetch_all, daemon=True).start()


# ── Auth Helpers ─────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("authenticated"):
            if request.is_json:
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return wrapper


# ── Price Helper ─────────────────────────────────────────────────────────

PRICE_CACHE: Dict[str, Any] = {"timestamp": 0, "prices": {}}
PRICE_CACHE_DURATION = 300  # 5 minutes

def get_token_prices() -> Dict[str, float]:
    """Fetch current prices for ETH and SCR from DefiLlama (more reliable for servers)."""
    now = time.time()
    
    # Return cached if valid
    if now - PRICE_CACHE["timestamp"] < PRICE_CACHE_DURATION and PRICE_CACHE["prices"]:
        return PRICE_CACHE["prices"]
        
    try:
        # DefiLlama API (no strict rate limits on server IPs)
        # Construct comma-separated list of coingecko IDs
        cg_ids = [f"coingecko:{mid}" for mid in TOKEN_COINGECKO_IDS.values()]
        ids_str = ",".join(cg_ids)
        
        url = f"https://coins.llama.fi/prices/current/{ids_str}?searchWidth=4h"
        
        # Add User-Agent just in case
        headers = {
            "User-Agent": "Mozilla/5.0"
        }
        
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        
        coins = data.get("coins", {})
        
        prices = {}
        for symbol, cg_id in TOKEN_COINGECKO_IDS.items():
            key = f"coingecko:{cg_id}"
            prices[symbol] = coins.get(key, {}).get("price", 0)
        
        PRICE_CACHE["timestamp"] = now
        PRICE_CACHE["prices"] = prices
        return prices
    except Exception as e:
        logger.error("Error fetching prices: %s", e)
        # Return old cache if available, else 0
        return PRICE_CACHE.get("prices", {"ETH": 0, "SCR": 0})


# ── Page Routes ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/login", methods=["GET"])
def login_page():
    return render_template("login.html")


@app.route("/api/login", methods=["POST"])
@csrf.exempt
@limiter.limit("5 per minute")
def api_login():
    data = request.get_json() or {}
    if data.get("username") == AUTH_USERNAME and data.get("password") == AUTH_PASSWORD:
        session["authenticated"] = True
        return jsonify({"success": True})
    return jsonify({"error": "Invalid credentials"}), 401


@app.route("/api/logout", methods=["POST"])
@csrf.exempt
def api_logout():
    session.pop("authenticated", None)
    return jsonify({"success": True})


@app.route("/api/auth/status")
def auth_status():
    return jsonify({"authenticated": bool(session.get("authenticated"))})


# ── Wallet / Config API ─────────────────────────────────────────────────

@app.route("/api/wallets")
def api_wallets():
    """Return list of configured wallets."""
    wallets = []
    for slug, info in MULTISIGS.items():
        wallets.append({
            "id": slug,
            "name": info["name"],
            "address": info["address"],
            "description": info["description"],
            "categories": CATEGORIES.get(slug, ["Uncategorised"]),
        })
    return jsonify(wallets)


@app.route("/api/categories/<wallet_id>")
def api_categories(wallet_id):
    if wallet_id == 'all':
        all_cats = set()
        for cats in CATEGORIES.values():
            all_cats.update(cats)
        return jsonify(sorted(list(all_cats)))
    return jsonify(CATEGORIES.get(wallet_id, ["Uncategorised"]))


@app.route("/api/budgets")
def api_budgets():
    totals = BUDGET_TOTALS.get("default", BUDGET_TOTALS)
    if "quarterly" in BUDGET_TOTALS and isinstance(BUDGET_TOTALS["quarterly"], (int, float)):
        totals = BUDGET_TOTALS
    
    # Derive groups from BUDGETS order
    groups = []
    seen = set()
    for data in BUDGETS.values():
        g = data.get("group", "Other")
        if g not in seen:
            groups.append(g)
            seen.add(g)

    return jsonify({"categories": BUDGETS, "totals": totals, "groups": groups})


# ── Transaction API ──────────────────────────────────────────────────────

@app.route("/api/transactions/<wallet_id>")
def api_transactions(wallet_id):
    """
    Return transactions for a wallet.
    Query params: direction, category, token, date_from, date_to, limit, offset, search
    """
    conn = get_db()
    conditions = ["1=1"]
    params = []
    
    if wallet_id != 'all':
        conditions.append("wallet_id = ?")
        params.append(wallet_id)

    direction = request.args.get("direction")
    if direction in ("in", "out"):
        conditions.append("direction = ?")
        params.append(direction)
    elif direction == "fund_movement":
        conditions.append("category != ?")
        params.append("Internal Operations")
        conditions.append("value_decimal > 0")

    category = request.args.get("category")
    if category:
        conditions.append("category = ?")
        params.append(category)

    token = request.args.get("token")
    if token:
        conditions.append("token_symbol = ?")
        params.append(token)

    date_from = request.args.get("date_from")
    if date_from:
        try:
            ts = int(datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
            conditions.append("timestamp >= ?")
            params.append(ts)
        except ValueError:
            pass

    date_to = request.args.get("date_to")
    if date_to:
        try:
            ts = int(datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
            conditions.append("timestamp <= ?")
            params.append(ts + 86400)  # include full day
        except ValueError:
            pass

    search = request.args.get("search")
    if search:
        conditions.append("(tx_hash LIKE ? OR from_address LIKE ? OR to_address LIKE ? OR notes LIKE ?)")
        s = f"%{search}%"
        params.extend([s, s, s, s])

    where = " AND ".join(conditions)
    try:
        limit = min(int(request.args.get("limit", 100)), 1000)
    except (ValueError, TypeError):
        limit = 100
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except (ValueError, TypeError):
        offset = 0

    # Get total count
    count_row = conn.execute(f"SELECT COUNT(*) as cnt FROM transactions WHERE {where}", params).fetchone()
    total = count_row["cnt"]

    # Get transactions
    rows = conn.execute(
        f"SELECT * FROM transactions WHERE {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    txs = []
    for r in rows:
        txs.append({
            "id": r["id"],
            "tx_hash": r["tx_hash"],
            "block_number": r["block_number"],
            "timestamp": r["timestamp"],
            "date": datetime.fromtimestamp(r["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "from_address": r["from_address"],
            "to_address": r["to_address"],
            "value_decimal": r["value_decimal"],
            "token_symbol": r["token_symbol"],
            "token_name": r["token_name"],
            "contract_address": r["contract_address"],
            "tx_type": r["tx_type"],
            "direction": r["direction"],
            "category": r["category"],
            "notes": r["notes"],
            "signers": r["signers"],
            "is_error": r["is_error"],
            "wallet_id": r["wallet_id"],
        })


    if wallet_id == 'all':
        aliases = {}
        for w_aliases in SIGNER_ALIASES.values():
            aliases.update(w_aliases)
    else:
        aliases = SIGNER_ALIASES.get(wallet_id, {})
        
    return jsonify({
        "transactions": txs,
        "total": total,
        "limit": limit,
        "offset": offset,
        "signer_aliases": aliases,
    })


# ── Categorise Transaction (auth required) ───────────────────────────────

@app.route("/api/transactions/<int:tx_id>/categorise", methods=["POST"])
@csrf.exempt
@login_required
def api_categorise(tx_id):
    data = request.get_json() or {}
    
    # Get current values first to valid partial updates
    conn = get_db()
    current = conn.execute("SELECT category, notes FROM transactions WHERE id=?", (tx_id,)).fetchone()
    if not current:
        return jsonify({"error": "Transaction not found"}), 404
        
    category = data.get("category", current["category"])
    notes = data.get("notes", current["notes"])
    
    cursor = conn.execute(
        "UPDATE transactions SET category=?, notes=? WHERE id=?",
        (category, notes, tx_id),
    )
    conn.commit()

    return jsonify({"success": True, "id": tx_id, "category": category, "notes": notes})


@app.route("/api/transactions/bulk-categorise", methods=["POST"])
@csrf.exempt
@login_required
def api_bulk_categorise():
    data = request.get_json() or {}
    items = data.get("items", [])
    if not isinstance(items, list):
        return jsonify({"error": "items must be a list"}), 400
    conn = get_db()
    updated = 0
    for item in items:
        if not isinstance(item, dict) or "id" not in item:
            continue
        cursor = conn.execute(
            "UPDATE transactions SET category=?, notes=? WHERE id=?",
            (item.get("category", "Uncategorised"), item.get("notes", ""), item["id"]),
        )
        updated += cursor.rowcount
    conn.commit()

    return jsonify({"success": True, "updated": updated})


# ── Dashboard Stats API ─────────────────────────────────────────────────

@app.route("/api/stats/<wallet_id>")
def api_stats(wallet_id):
    conn = get_db()

    # Get prices
    prices = get_token_prices()

    # Pre-fetch ALL historical prices in one query (fixes N+1)
    price_map = {}
    for row in conn.execute("SELECT symbol, date, price FROM token_prices").fetchall():
        price_map[(row["symbol"], row["date"])] = row["price"]

    # Balances
    balances = []
    if wallet_id == 'all':
        balances_query = "SELECT token_symbol, token_name, contract_address, SUM(balance_decimal) as balance_decimal, MAX(last_updated) as last_updated FROM balances GROUP BY token_symbol, token_name, contract_address"
        balances_params = ()
    else:
        balances_query = "SELECT * FROM balances WHERE wallet_id=?"
        balances_params = (wallet_id,)
    for row in conn.execute(balances_query, balances_params).fetchall():
        symbol = row["token_symbol"]
        price = prices.get(symbol, 0)
        balance = row["balance_decimal"]
        balances.append({
            "token_symbol": symbol,
            "token_name": row["token_name"],
            "balance_decimal": balance,
            "balance_usd": balance * price,
            "price_usd": price,
            "last_updated": row["last_updated"],
            "contract_address": row["contract_address"],
        })

    # Spending by category (outgoing only, excluding non-expenses) - using historical prices
    spending = []
    
    # Generate placeholders and parameters for IN clause
    qs = ','.join(['?']*len(NON_EXPENSE_CATEGORIES))
    
    if wallet_id == 'all':
        query = f"""SELECT 
                 category, 
                 token_symbol,
                 value_decimal,
                 date(timestamp, 'unixepoch') as tx_date
               FROM transactions
               WHERE direction='out' 
                 AND is_error=0
                 AND category NOT IN ({qs})"""
        params = list(NON_EXPENSE_CATEGORIES)
    elif wallet_id == 'treasury':
        # Custom logic for the Treasury Outflow Chart:
        # We want to include exactly the items that are specifically filtered out as "expenses" for other charts
        query = f"""SELECT 
                 category, 
                 token_symbol,
                 value_decimal,
                 date(timestamp, 'unixepoch') as tx_date
               FROM transactions
               WHERE wallet_id=? 
                 AND direction='out' 
                 AND is_error=0
                 AND category != 'Internal Operations' AND value_decimal > 0"""
        params = [wallet_id]
    else:
        query = f"""SELECT 
                 category, 
                 token_symbol,
                 value_decimal,
                 date(timestamp, 'unixepoch') as tx_date
               FROM transactions
               WHERE wallet_id=? 
                 AND direction='out' 
                 AND is_error=0
                 AND category NOT IN ({qs})"""
        params = [wallet_id] + list(NON_EXPENSE_CATEGORIES)
    
    spending_rows = conn.execute(query, params).fetchall()

    # Aggregate in python
    cat_spending = {}
    for r in spending_rows:
        cat = r["category"]
        sym = r["token_symbol"]
        val = r["value_decimal"]
        tx_date = r["tx_date"]
        
        # Map the category if it's the Treasury
        if wallet_id == 'treasury':
            fiat_group = ["Operations & Accountability Committee", "Delegates Incentives", "Operations Committee Discretionary Budget"]
            token_group = ["Community Allocation", "Ecosystem Allocation"]
            
            if cat in fiat_group or (cat == 'Internal Transfer' and sym in ['USDT', 'USDC']):
                cat = "Fiat Transfers (USDT)"
            elif cat in token_group or (cat == 'Internal Transfer' and sym == 'SCR'):
                cat = "Token Transfers (SCR)"
            elif cat == "Treasury Swap":
                cat = "Treasury Swaps"
            elif cat == "General Purpose DAO Budget":
                cat = "Direct DAO Spend"
            else:
                cat = "Other Transfers"

        # Lookup from pre-fetched prices (no DB query per row)
        hist_price = price_map.get((sym, tx_date), prices.get(sym, 0))
        usd_val = val * hist_price
        
        k = (cat, sym)
        if k not in cat_spending:
            cat_spending[k] = {"total": 0, "total_usd": 0, "count": 0}
        
        cat_spending[k]["total"] += val
        cat_spending[k]["total_usd"] += usd_val
        cat_spending[k]["count"] += 1

    # Convert to list
    for (cat, sym), data in cat_spending.items():
        spending.append({
            "category": cat,
            "token_symbol": sym,
            "total": data["total"],
            "total_usd": data["total_usd"],
            "tx_count": data["count"],
        })
    
    # Sort by total USD desc, but "Uncategorised" always first
    spending.sort(key=lambda x: (0 if x["category"] == "Uncategorised" else 1, -x["total_usd"]))


    # Monthly burn rate (last 6 months, excluding non-expenses) - using historical prices
    now = int(time.time())
    six_months_ago = now - (180 * 86400)
    
    qs = ','.join(['?']*len(NON_EXPENSE_CATEGORIES))
    if wallet_id == 'all':
        query = f"""SELECT
                 strftime('%Y-%m', datetime(timestamp, 'unixepoch')) as month,
                 date(timestamp, 'unixepoch') as tx_date,
                 token_symbol,
                 value_decimal
               FROM transactions
               WHERE direction='out' 
                     AND is_error=0
                     AND timestamp >= ?
                     AND category NOT IN ({qs})"""
        params = [six_months_ago] + list(NON_EXPENSE_CATEGORIES)
    else:
        query = f"""SELECT
                 strftime('%Y-%m', datetime(timestamp, 'unixepoch')) as month,
                 date(timestamp, 'unixepoch') as tx_date,
                 token_symbol,
                 value_decimal
               FROM transactions
               WHERE wallet_id=? 
                     AND direction='out' 
                     AND is_error=0
                     AND timestamp >= ?
                     AND category NOT IN ({qs})"""
        params = [wallet_id, six_months_ago] + list(NON_EXPENSE_CATEGORIES)
    
    burn_rows = conn.execute(query, params).fetchall()

    monthly_burn_map: Dict[tuple, Dict[str, float]] = {}
    for r in burn_rows:
        month = r["month"]
        tx_date = r["tx_date"]
        sym = r["token_symbol"]
        val = r["value_decimal"]

        # Lookup from pre-fetched prices
        hist_price = price_map.get((sym, tx_date), prices.get(sym, 0))
        usd_val = val * hist_price
        
        # Calculate SCR value
        scr_val = 0
        if sym == 'SCR':
            scr_val = val
        else:
            scr_price = price_map.get(('SCR', tx_date), prices.get('SCR', 0))
            if scr_price > 0:
                scr_val = usd_val / scr_price

        k = (month, sym)
        if k not in monthly_burn_map:
            monthly_burn_map[k] = {"total": 0.0, "total_usd": 0.0, "total_scr": 0.0}
        
        # Helper to avoid linter confusion
        entry = monthly_burn_map[k]
        entry["total"] += val
        entry["total_usd"] += usd_val
        entry["total_scr"] += scr_val

    monthly_burn = []
    for (month, sym), data in monthly_burn_map.items():
        monthly_burn.append({
            "month": month,
            "token_symbol": sym,
            "total": data["total"],
            "total_usd": data["total_usd"],
            "total_scr": data["total_scr"],
        })
    monthly_burn.sort(key=lambda x: x["month"])
    
    # Custom Monthly Treasury Datasets
    treasury_monthly_transfers = []
    treasury_monthly_swaps = []
    
    if wallet_id == 'treasury':
        # 1. Treasury Transfers/Expenses
        query_transfers = """SELECT
                 strftime('%Y-%m', datetime(timestamp, 'unixepoch')) as month,
                 date(timestamp, 'unixepoch') as tx_date,
                 token_symbol,
                 value_decimal
               FROM transactions
               WHERE wallet_id=? 
                     AND direction='out' 
                     AND is_error=0
                     AND category NOT IN ('Internal Operations', 'Treasury Swap')
                     AND timestamp >= ?"""
        transfers_rows = conn.execute(query_transfers, (wallet_id, six_months_ago)).fetchall()
        
        t_transfers_map: Dict[str, float] = {}
        for r in transfers_rows:
            month = r["month"]
            tx_date = r["tx_date"]
            sym = r["token_symbol"]
            val = r["value_decimal"]
            
            hist_price = price_map.get((sym, tx_date), prices.get(sym, 0))
            usd_val = val * hist_price
            
            t_transfers_map[month] = t_transfers_map.get(month, 0.0) + usd_val
            
        for month, total_usd in sorted(t_transfers_map.items()):
            treasury_monthly_transfers.append({
                "month": month,
                "total_usd": total_usd
            })
            
        # 2. Treasury Swaps
        query_swaps_out = """SELECT
                 strftime('%Y-%m', datetime(timestamp, 'unixepoch')) as month,
                 SUM(value_decimal) as val
               FROM transactions
               WHERE wallet_id=? 
                 AND category='Treasury Swap'
                 AND direction='out'
                 AND token_symbol='SCR'
                 AND is_error=0
                 AND timestamp >= ?
               GROUP BY month"""
               
        query_swaps_in = """SELECT
                 strftime('%Y-%m', datetime(timestamp, 'unixepoch')) as month,
                 SUM(value_decimal) as val
               FROM transactions
               WHERE wallet_id=? 
                 AND category='Treasury Swap'
                 AND direction='in'
                 AND token_symbol IN ('USDT', 'USDC')
                 AND is_error=0
                 AND timestamp >= ?
               GROUP BY month"""
               
        swaps_out_rows = conn.execute(query_swaps_out, (wallet_id, six_months_ago)).fetchall()
        swaps_in_rows = conn.execute(query_swaps_in, (wallet_id, six_months_ago)).fetchall()
        
        swaps_map: Dict[str, Dict[str, float]] = {}
        for r in swaps_out_rows:
            month = r["month"]
            if month not in swaps_map: swaps_map[month] = {"scr_swapped": 0.0, "usdt_obtained": 0.0}
            swaps_map[month]["scr_swapped"] += r["val"]
            
        for r in swaps_in_rows:
            month = r["month"]
            if month not in swaps_map: swaps_map[month] = {"scr_swapped": 0.0, "usdt_obtained": 0.0}
            swaps_map[month]["usdt_obtained"] += r["val"]
            
        for month, data in sorted(swaps_map.items()):
            treasury_monthly_swaps.append({
                "month": month,
                "scr_swapped": data["scr_swapped"],
                "usdt_obtained": data["usdt_obtained"]
            })

    # Transaction count summary
    if wallet_id == 'all':
        counts = conn.execute(
            """SELECT
                 COUNT(*) as total,
                 SUM(CASE WHEN direction='in' THEN 1 ELSE 0 END) as incoming,
                 SUM(CASE WHEN direction='out' THEN 1 ELSE 0 END) as outgoing,
                 SUM(CASE WHEN category='Uncategorised' THEN 1 ELSE 0 END) as uncategorised
               FROM transactions"""
        ).fetchone()
        
        # Token list
        tokens = [r["token_symbol"] for r in conn.execute(
            "SELECT DISTINCT token_symbol FROM transactions ORDER BY token_symbol"
        ).fetchall()]
    else:
        counts = conn.execute(
            """SELECT
                 COUNT(*) as total,
                 SUM(CASE WHEN direction='in' THEN 1 ELSE 0 END) as incoming,
                 SUM(CASE WHEN direction='out' THEN 1 ELSE 0 END) as outgoing,
                 SUM(CASE WHEN category='Uncategorised' THEN 1 ELSE 0 END) as uncategorised
               FROM transactions WHERE wallet_id=?""",
            (wallet_id,),
        ).fetchone()
    
        # Token list
        tokens = [r["token_symbol"] for r in conn.execute(
            "SELECT DISTINCT token_symbol FROM transactions WHERE wallet_id=? ORDER BY token_symbol",
            (wallet_id,),
        ).fetchall()]



    return jsonify({
        "balances": balances,
        "spending_by_category": spending,
        "monthly_burn": monthly_burn,
        "treasury_monthly_transfers": treasury_monthly_transfers,
        "treasury_monthly_swaps": treasury_monthly_swaps,
        "tx_counts": {
            "total": counts["total"] or 0,
            "incoming": counts["incoming"] or 0,
            "outgoing": counts["outgoing"] or 0,
            "uncategorised": counts["uncategorised"] or 0,
        },
        "tokens": tokens,
    })


@app.route("/api/budget-comparison/<wallet_id>")
def api_budget_comparison(wallet_id):
    """Return spent vs budgeted amounts for each category (using historical prices)."""
    conn = get_db()
    if wallet_id == 'all':
        categories = set(cat for cats in CATEGORIES.values() for cat in cats)
    else:
        categories = CATEGORIES.get(wallet_id, [])
    result = []

    # Get current prices as fallback
    prices = get_token_prices()
    
    # Pre-fetch ALL historical prices in one query (fixes N+1)
    price_map = {}
    for row in conn.execute("SELECT symbol, date, price FROM token_prices").fetchall():
        price_map[(row["symbol"], row["date"])] = row["price"]
    
    # Fetch all outgoing transactions for this wallet at once (excluding non-expenses)
    
    if wallet_id == 'all':
        qs = ','.join(['?']*len(NON_EXPENSE_CATEGORIES))
        query = f"""SELECT category, token_symbol, value_decimal, date(timestamp, 'unixepoch') as tx_date
               FROM transactions
               WHERE direction='out' 
                 AND is_error=0
                 AND category NOT IN ({qs})"""
        params = list(NON_EXPENSE_CATEGORIES)
    elif wallet_id == 'treasury':
        # The Treasury needs to track its transfers in its budget bars (they aren't non-expenses for it)
        treasury_non_expenses = ["Internal Operations", "Treasury Swap", "Internal Transfer"]
        qs = ','.join(['?']*len(treasury_non_expenses))
        query = f"""SELECT category, token_symbol, value_decimal, date(timestamp, 'unixepoch') as tx_date
               FROM transactions
               WHERE wallet_id=? 
                 AND direction='out' 
                 AND is_error=0
                 AND category NOT IN ({qs})"""
        params = [wallet_id] + treasury_non_expenses
    else:
        qs = ','.join(['?']*len(NON_EXPENSE_CATEGORIES))
        query = f"""SELECT category, token_symbol, value_decimal, date(timestamp, 'unixepoch') as tx_date
               FROM transactions
               WHERE wallet_id=? 
                 AND direction='out' 
                 AND is_error=0
                 AND category NOT IN ({qs})"""
        params = [wallet_id] + list(NON_EXPENSE_CATEGORIES)
    
    all_spent_rows = conn.execute(query, params).fetchall()
    
    # Group by category in Python
    spent_by_cat: Dict[str, list] = {}
    for r in all_spent_rows:
        cat = r["category"]
        if cat not in spent_by_cat:
            spent_by_cat[cat] = []
        spent_by_cat[cat].append(r)
    
    # Calculate Volume-Weighted Average Price (VWAP) for DAO Swaps
    swap_scr_out_query = "SELECT SUM(value_decimal) as val FROM transactions WHERE category='Treasury Swap' AND direction='out' AND token_symbol='SCR'"
    swap_usdt_in_query = "SELECT SUM(value_decimal) as val FROM transactions WHERE category='Treasury Swap' AND direction='in' AND token_symbol IN ('USDT', 'USDC')"
    
    total_scr_out = conn.execute(swap_scr_out_query).fetchone()["val"] or 0
    total_usdt_in = conn.execute(swap_usdt_in_query).fetchone()["val"] or 0
    
    effective_swap_price = 0
    if total_scr_out > 0 and total_usdt_in > 0:
        effective_swap_price = total_usdt_in / total_scr_out
    
    total_spent_usd_native = 0
    total_spent_scr_native = 0

    for cat in categories:
        if cat == "Uncategorised":
            continue
        
        # Check for override first
        base_budget = BUDGETS.get(cat, {})
        if wallet_id == 'all':
            override = {}
        else:
            override = BUDGET_OVERRIDES.get(wallet_id, {}).get(cat, {})
        # Merge override into base (shallow merge is fine for our structure)
        budget: Dict[str, Any] = {**base_budget, **override}
        
        spent_usd = 0
        spent_scr = 0
        cat_spent_usd_native = 0
        for r in spent_by_cat.get(cat, []):
            symbol = r["token_symbol"]
            tx_date = r["tx_date"]
            val = r["value_decimal"]
            
            # Lookup from pre-fetched prices
            hist_price = price_map.get((symbol, tx_date), prices.get(symbol, 0))
            usd_val = val * hist_price
            spent_usd += usd_val
            
            if symbol == "SCR":
                spent_scr += val
                total_spent_scr_native += val
            elif symbol in ["USDT", "USDC"] and effective_swap_price > 0:
                # Use the DAO's actual average swap execution price instead of daily market volatility
                spent_scr += val / effective_swap_price
                total_spent_usd_native += val
                cat_spent_usd_native += val
            else:
                scr_price_at_date = price_map.get(("SCR", tx_date), prices.get("SCR", 1))
                if scr_price_at_date > 0:
                    spent_scr += usd_val / scr_price_at_date
                total_spent_usd_native += usd_val
                if symbol in ["USDT", "USDC"]:
                    cat_spent_usd_native += val

        # Helper to avoid linter confusion
        budget_q = float(budget.get("quarterly", 0))

        result.append({
            "category": cat,
            "spent_usd": spent_usd,
            "spent_usd_native": cat_spent_usd_native,
            "spent_scr": spent_scr,
            "budget_quarterly": budget_q,
            "group": budget.get("group", "Other"),
            "shared_id": budget.get("shared_id"),
            "currency": budget.get("currency", "USD"),
            "tooltip": budget.get("tooltip"),
        })



    # Overall totals - Original blended logic
    total_spent = sum(r["spent_usd"] for r in result)
    total_spent_scr = sum(r["spent_scr"] for r in result)
    
    # Calculate unique budget limits to avoid multiplying shared pools
    total_budget_usd = 0
    total_budget_scr = 0
    seen_shared_ids = set()
    
    for r in result:
        currency = r.get("currency", "USD")
        limit = r["budget_quarterly"]
        shared_id = r.get("shared_id")
        
        if shared_id:
            if shared_id in seen_shared_ids:
                continue
            seen_shared_ids.add(shared_id)
            
        if currency == "USD":
            total_budget_usd += limit
        elif currency == "SCR":
            total_budget_scr += limit

    # Get wallet specific totals
    wallet_totals = BUDGET_TOTALS.get(wallet_id, BUDGET_TOTALS.get("default", BUDGET_TOTALS)) # type: ignore
    
    # Ensure we valid dict with quarterly/semester
    if not isinstance(wallet_totals, dict) or "quarterly" not in wallet_totals:
         wallet_totals = BUDGET_TOTALS.get("default", BUDGET_TOTALS)

    # Groups list derived from BUDGETS order (same logic as api_budgets)
    groups_list = []
    seen = set()
    for data in BUDGETS.values():
        g = data.get("group", "Other")
        if g not in seen:
            groups_list.append(g)
            seen.add(g)

    treasury_transferred_usd = 0
    treasury_sub_spent_usd = 0
    treasury_scr_swapped = 0
    treasury_usdt_available_from_swap = 0
    treasury_transferred_scr_initiatives = 0
    treasury_spent_scr_initiatives_usd = 0

    if wallet_id == 'treasury':
        # Calculate transferred to Operations & Delegates (USD part)
        transferred_query = """SELECT token_symbol, value_decimal, date(timestamp, 'unixepoch') as tx_date 
                             FROM transactions 
                             WHERE wallet_id='treasury' AND direction='out' AND category='Internal Transfer'
                             AND to_address IN (SELECT address FROM wallets WHERE id IN ('committee', 'delegates'))"""
        
        # We don't have a wallets table with addresses easily mapped like this in DB.
        # Fallback to hardcoded addresses from MULTISIGS list since they are static:
        ops_addresses = [
            '0xd0d05390d922a2c45a70eaa4601600f236c02acc', # committee
            '0x7964e7bf48948c9e1d89f419cad8ef7d8d8f0434'  # delegates
        ]
        
        ops_categories_tuple = "('Internal Transfer', 'Operations & Accountability Committee', 'Delegates Incentives', 'Operations Committee Discretionary Budget')"
        transferred_query = f"""SELECT token_symbol, value_decimal, date(timestamp, 'unixepoch') as tx_date 
                             FROM transactions 
                             WHERE wallet_id='treasury' AND direction='out' AND category IN {ops_categories_tuple}
                             AND LOWER(to_address) IN ({','.join(['?']*len(ops_addresses))})"""
        
        transferred_rows = conn.execute(transferred_query, [a.lower() for a in ops_addresses]).fetchall()
        for r in transferred_rows:
            symbol = r["token_symbol"]
            tx_date = r["tx_date"]
            val = r["value_decimal"]
            hist_price = price_map.get((symbol, tx_date), prices.get(symbol, 0))
            treasury_transferred_usd += (val * hist_price)

        # Calculate subsidiary spent (Ops & Delegates)
        qs = ','.join(['?']*len(NON_EXPENSE_CATEGORIES))
        sub_spent_query = f"""SELECT token_symbol, value_decimal, date(timestamp, 'unixepoch') as tx_date 
                              FROM transactions 
                              WHERE wallet_id IN ('committee', 'delegates') 
                                AND direction='out' 
                                AND is_error=0 
                                AND category NOT IN ({qs})"""
        sub_spent_rows = conn.execute(sub_spent_query, list(NON_EXPENSE_CATEGORIES)).fetchall()
        for r in sub_spent_rows:
            symbol = r["token_symbol"]
            tx_date = r["tx_date"]
            val = r["value_decimal"]
            hist_price = price_map.get((symbol, tx_date), prices.get(symbol, 0))
            treasury_sub_spent_usd += (val * hist_price)
            
        # SCR Part: Swapped, Available, Transferred, Sub-Spent
        # 1. Swapped SCR to USDT
        swapped_query = """SELECT SUM(value_decimal) as val
                           FROM transactions 
                           WHERE wallet_id='treasury' AND direction='out' AND category='Treasury Swap' AND token_symbol='SCR'"""
        row = conn.execute(swapped_query).fetchone()
        treasury_scr_swapped = row["val"] if row and row["val"] else 0

        # 2. Available USDT from Swap
        available_query = """SELECT SUM(value_decimal) as val
                             FROM transactions 
                             WHERE wallet_id='treasury' AND direction='in' AND category='Treasury Swap' AND token_symbol='USDT'"""
        row = conn.execute(available_query).fetchone()
        treasury_usdt_available_from_swap = row["val"] if row and row["val"] else 0

        # 3. Transferred USDT to Community & Ecosystem
        ecosystem_addresses = [
            '0x756ed67a0e73dd1ec4facbc307ca79c28d930b20', # community
            '0xe47b51a31ad43acb72a224fab4a17999311e2e48'  # ecosystem
        ]
        eco_categories_tuple = "('Internal Transfer', 'Community Allocation', 'Ecosystem Allocation')"
        transferred_eco_query = f"""SELECT token_symbol, value_decimal, date(timestamp, 'unixepoch') as tx_date 
                             FROM transactions 
                             WHERE wallet_id='treasury' AND direction='out' AND category IN {eco_categories_tuple}
                             AND LOWER(to_address) IN ({','.join(['?']*len(ecosystem_addresses))})"""
        transferred_eco_rows = conn.execute(transferred_eco_query, [a.lower() for a in ecosystem_addresses]).fetchall()
        for r in transferred_eco_rows:
            symbol = r["token_symbol"]
            tx_date = r["tx_date"]
            val = r["value_decimal"]
            hist_price = price_map.get((symbol, tx_date), prices.get(symbol, 0))
            treasury_transferred_scr_initiatives += (val * hist_price)

        # 4. USDT Spent by Community & Ecosystem
        sub_spent_eco_query = f"""SELECT token_symbol, value_decimal, date(timestamp, 'unixepoch') as tx_date 
                              FROM transactions 
                              WHERE wallet_id IN ('community', 'ecosystem') 
                                AND direction='out' 
                                AND is_error=0 
                                AND category NOT IN ({qs})"""
        sub_spent_eco_rows = conn.execute(sub_spent_eco_query, list(NON_EXPENSE_CATEGORIES)).fetchall()
        for r in sub_spent_eco_rows:
            symbol = r["token_symbol"]
            tx_date = r["tx_date"]
            val = r["value_decimal"]
            hist_price = price_map.get((symbol, tx_date), prices.get(symbol, 0))
            treasury_spent_scr_initiatives_usd += (val * hist_price)


    return jsonify({
        "categories": result,
        "prices": prices,
        "totals": {
            "spent": total_spent,
            "spent_scr": total_spent_scr,
            "spent_usd_native": total_spent_usd_native,
            "spent_scr_native": total_spent_scr_native,
            "budget_quarterly": wallet_totals.get("quarterly", 0),
            "budget_usd": total_budget_usd,
            "budget_scr": total_budget_scr,
            "treasury_transferred_usd": treasury_transferred_usd,
            "treasury_sub_spent_usd": treasury_sub_spent_usd,
            "treasury_scr_swapped": treasury_scr_swapped,
            "treasury_usdt_available_from_swap": treasury_usdt_available_from_swap,
            "treasury_transferred_scr_initiatives": treasury_transferred_scr_initiatives,
            "treasury_spent_scr_initiatives_usd": treasury_spent_scr_initiatives_usd,
        },
        "groups": groups_list, 
    })


# ── CSV Export ───────────────────────────────────────────────────────────

@app.route("/api/export/<wallet_id>")
def api_export(wallet_id):
    conn = get_db()
    if wallet_id == 'all':
        rows = conn.execute(
            "SELECT * FROM transactions ORDER BY timestamp DESC"
        ).fetchall()
        wallet_name = "All_Multisigs"
    else:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE wallet_id=? ORDER BY timestamp DESC",
            (wallet_id,),
        ).fetchall()
        wallet_name = MULTISIGS.get(wallet_id, {}).get("name", wallet_id).replace(" ", "_")


    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Date", "TX Hash", "From", "To", "Amount", "Token",
        "Type", "Direction", "Category", "Notes", "Signers", "Block",
    ])
    for r in rows:
        writer.writerow([
            datetime.fromtimestamp(r["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            r["tx_hash"], r["from_address"], r["to_address"],
            r["value_decimal"], r["token_symbol"], r["tx_type"],
            r["direction"], r["category"], r["notes"], r["signers"], r["block_number"],
        ])

    output.seek(0)
    
    output.seek(0)
    
    # improved filename
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={wallet_name}_transactions.csv"},
    )


# ── Manual Fetch Trigger ────────────────────────────────────────────────

@app.route("/api/fetch/<wallet_id>", methods=["POST"])
@csrf.exempt
@login_required
def api_fetch(wallet_id):
    """Manually trigger a fetch for a specific wallet."""
    threading.Thread(target=fetch_single_wallet, args=(wallet_id,), daemon=True).start()
    return jsonify({"success": True, "message": f"Fetch started for {wallet_id}"})


@app.route("/api/fetch-all", methods=["POST"])
@csrf.exempt
@login_required
@limiter.limit("2 per minute")
def api_fetch_all():
    threading.Thread(target=fetch_all, daemon=True).start()
    return jsonify({"success": True, "message": "Full fetch started"})


# ── Run ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=8080, debug=debug_mode)

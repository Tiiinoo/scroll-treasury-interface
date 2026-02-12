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
    FETCH_INTERVAL_MINUTES,
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
        # using coingecko IDs: scroll, ethereum
        url = "https://coins.llama.fi/prices/current/coingecko:scroll,coingecko:ethereum?searchWidth=4h"
        
        # Add User-Agent just in case
        headers = {
            "User-Agent": "Mozilla/5.0"
        }
        
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        
        coins = data.get("coins", {})
        
        prices = {
            "ETH": coins.get("coingecko:ethereum", {}).get("price", 0),
            "SCR": coins.get("coingecko:scroll", {}).get("price", 0),
        }
        
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
    conditions = ["wallet_id = ?"]
    params = [wallet_id]

    direction = request.args.get("direction")
    if direction and direction in ("in", "out"):
        conditions.append("direction = ?")
        params.append(direction)

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
            "is_error": r["is_error"],
        })


    return jsonify({"transactions": txs, "total": total, "limit": limit, "offset": offset})


# ── Categorise Transaction (auth required) ───────────────────────────────

@app.route("/api/transactions/<int:tx_id>/categorise", methods=["POST"])
@csrf.exempt
@login_required
def api_categorise(tx_id):
    data = request.get_json() or {}
    category = data.get("category", "Uncategorised")
    notes = data.get("notes", "")
    conn = get_db()
    cursor = conn.execute(
        "UPDATE transactions SET category=?, notes=? WHERE id=?",
        (category, notes, tx_id),
    )
    conn.commit()

    if cursor.rowcount == 0:
        return jsonify({"error": "Transaction not found"}), 404

    return jsonify({"success": True, "id": tx_id, "category": category})


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
    for row in conn.execute("SELECT * FROM balances WHERE wallet_id=?", (wallet_id,)).fetchall():
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

    # Spending by category (outgoing only) - using historical prices
    spending = []
    spending_rows = conn.execute(
        """SELECT 
             category, 
             token_symbol,
             value_decimal,
             date(timestamp, 'unixepoch') as tx_date
           FROM transactions
           WHERE wallet_id=? AND direction='out' AND is_error=0""",
        (wallet_id,),
    ).fetchall()

    # Aggregate in python
    cat_spending = {}
    for r in spending_rows:
        cat = r["category"]
        sym = r["token_symbol"]
        val = r["value_decimal"]
        tx_date = r["tx_date"]
        
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


    # Monthly burn rate (last 6 months) - using historical prices
    now = int(time.time())
    six_months_ago = now - (180 * 86400)
    
    burn_rows = conn.execute(
        """SELECT
             strftime('%Y-%m', datetime(timestamp, 'unixepoch')) as month,
             date(timestamp, 'unixepoch') as tx_date,
             token_symbol,
             value_decimal
           FROM transactions
           WHERE wallet_id=? AND direction='out' AND is_error=0
                 AND timestamp >= ?""",
        (wallet_id, six_months_ago),
    ).fetchall()

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

    # Transaction count summary
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
    categories = CATEGORIES.get(wallet_id, [])
    result = []

    # Get current prices as fallback
    prices = get_token_prices()
    
    # Pre-fetch ALL historical prices in one query (fixes N+1)
    price_map = {}
    for row in conn.execute("SELECT symbol, date, price FROM token_prices").fetchall():
        price_map[(row["symbol"], row["date"])] = row["price"]
    
    # Fetch all outgoing transactions for this wallet at once
    all_spent_rows = conn.execute(
        """SELECT category, token_symbol, value_decimal, date(timestamp, 'unixepoch') as tx_date
           FROM transactions
           WHERE wallet_id=? AND direction='out' AND is_error=0""",
        (wallet_id,),
    ).fetchall()
    
    # Group by category in Python
    spent_by_cat: Dict[str, list] = {}
    for r in all_spent_rows:
        cat = r["category"]
        if cat not in spent_by_cat:
            spent_by_cat[cat] = []
        spent_by_cat[cat].append(r)
    
    for cat in categories:
        if cat == "Uncategorised":
            continue
        budget = BUDGETS.get(cat, {})
        
        spent_usd = 0
        spent_scr = 0
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
            else:
                scr_price_at_date = price_map.get(("SCR", tx_date), prices.get("SCR", 1))
                if scr_price_at_date > 0:
                    spent_scr += usd_val / scr_price_at_date

        # Helper to avoid linter confusion
        budget_q = float(budget.get("quarterly", 0))
        budget_s = float(budget.get("semester", 0))

        result.append({
            "category": cat,
            "spent_usd": spent_usd,
            "spent_scr": spent_scr,
            "budget_quarterly": budget_q,
            "budget_semester": budget_s,
            "group": budget.get("group", "Other"),
            "shared_id": budget.get("shared_id"),
        })



    # Overall totals
    total_spent = sum(r["spent_usd"] for r in result)
    total_spent_scr = sum(r["spent_scr"] for r in result)

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

    return jsonify({
        "categories": result,
        "prices": prices,
        "totals": {
            "spent": total_spent,
            "spent_scr": total_spent_scr,
            "budget_quarterly": wallet_totals.get("quarterly", 0),
            "budget_semester": wallet_totals.get("semester", 0),
        },
        "groups": groups_list, 
    })


# ── CSV Export ───────────────────────────────────────────────────────────

@app.route("/api/export/<wallet_id>")
def api_export(wallet_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM transactions WHERE wallet_id=? ORDER BY timestamp DESC",
        (wallet_id,),
    ).fetchall()


    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Date", "TX Hash", "From", "To", "Amount", "Token",
        "Type", "Direction", "Category", "Notes", "Block",
    ])
    for r in rows:
        writer.writerow([
            datetime.fromtimestamp(r["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            r["tx_hash"], r["from_address"], r["to_address"],
            r["value_decimal"], r["token_symbol"], r["tx_type"],
            r["direction"], r["category"], r["notes"], r["block_number"],
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=scroll_treasury_{wallet_id}.csv"},
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

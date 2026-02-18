"""
Scroll DAO Treasury Tracker - Configuration
============================================
All multisig wallets, expense categories, and budget allocations.
Easy to modify: just update the dictionaries below.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# General
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get("SECRET_KEY")
DATABASE_PATH = os.environ.get("DATABASE_PATH", "treasury.db")
SCROLLSCAN_API_KEY = os.environ.get("SCROLLSCAN_API_KEY", "")  # Optional

# Scrollscan API base (Etherscan V2)
SCROLLSCAN_API_BASE = "https://api.etherscan.io/v2/api"
SCROLL_CHAIN_ID = 534352

# Fetch interval in minutes
FETCH_INTERVAL_MINUTES = int(os.environ.get("FETCH_INTERVAL_MINUTES", "15"))

# ---------------------------------------------------------------------------
# Token CoinGecko IDs
# ---------------------------------------------------------------------------
TOKEN_COINGECKO_IDS = {
    "ETH": "ethereum",
    "WETH": "weth",
    "SCR": "scroll",
    "USDC": "usd-coin",
    "USDT": "tether",
    "DAI": "dai",
    "WBTC": "bitcoin",
}

# ---------------------------------------------------------------------------
# Authentication (simple username / password for categorisation interface)
# ---------------------------------------------------------------------------
AUTH_USERNAME = os.environ.get("AUTH_USERNAME")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD")

# ---------------------------------------------------------------------------
# Multisig Wallets
# ---------------------------------------------------------------------------
MULTISIGS = {
    "treasury": {
        "name": "Scroll DAO Treasury Multisig",
        "address": "0x20fa362323447506D9d0C02483ae97C4e2d6B607",
        "description": "Main treasury wallet for the Scroll DAO Operations Committee",
    },
    "committee": {
        "name": "Operations & Accountability Committee Multisig",
        "address": "0x4cb06982dD097633426cf32038D9f1182a9aDA0c",
        "description": "Coommittee's expenses",
    },
    "delegates": {
        "name": "Delegates Incentives Multisig",
        "address": "0x4cb06982dD097633426cf32038D9f1182a9aDA0c",
        "description": "Incentive payments for governance delegates",
    },
    "community": {
        "name": "Community Allocation Multisig",
        "address": "0x4cb06982dD097633426cf32038D9f1182a9aDA0c",
        "description": "Funding for community programmes",
    },
    "ecosystem": {
        "name": "Ecosystem Allocation Multisig",
        "address": "0x4cb06982dD097633426cf32038D9f1182a9aDA0c",
        "description": "Funding for ecosystem growth initiatives",
    },
}

# ---------------------------------------------------------------------------
# Expense Categories per Multisig
# ---------------------------------------------------------------------------
CATEGORIES = {
    "treasury": [
        "Uncategorised",
        "Operations & Accountability Committee",
        "General Purpose DAO Budget",
        "Delegates Incentives",
        "Operations Committee Discretionary Budget",
        "Community Allocation",
        "Ecosystem Allocation",
        "Internal Operations",
    ],
    "committee": [
        "Uncategorised",
        "Governance Facilitator",
        "Program Coordination",
        "Marketing Operator",
        "Accountability Lead",
        "Accountability Operator",
        "Karma Gap Subscription",
        "Internal Operations",
    ],
    "delegates": [
        "Uncategorised",
        "Governance Contribution Recognition",
        "Delegate Contributions Programme",
        "Internal Operations",
    ],
    "community": [
        "Uncategorised",
        "Local Nodes",
        "Community Support Programme",
        "Internal Operations",
    ],
    "ecosystem": [
        "Uncategorised",
        "Founder Enablement Fund",
        "Creator Fund",
        "Security Subsidy Programme",
        "Internal Operations",
    ],
}


# ---------------------------------------------------------------------------
# Budget Allocations – Semester-Based
# Structure: { category_name: { "quarterly": amount, "semester": amount, "group": group_name } }
# All amounts in USD
# ---------------------------------------------------------------------------
BUDGETS = {
    # Operations (in order: Committee, Delegates, Discretionary, General Purpose)
    "Operations & Accountability Committee": {
        "quarterly": 75_000,
        "semester": 150_000,
        "group": "Operations",
    },
    "Delegates Incentives": {
        "quarterly": 60_000,
        "semester": 120_000,
        "group": "Operations",
    },
    "Operations Committee Discretionary Budget": {
        "quarterly": 5_000,
        "semester": 10_000,
        "group": "Operations",
    },
    #DAO Initiatives
    "General Purpose DAO Budget": {
        "quarterly": 60_000,
        "semester": 120_000,
        "group": "DAO Initiatives",
    },
    # Delegates
    "Governance Contribution Recognition": {
        "quarterly": 0,
        "semester": 72_000,
        "group": "Delegates",
    },
    "Delegate Contributions Programme": {
        "quarterly": 0,
        "semester": 48_000,
        "group": "Delegates",
    },
    # Committee Roles
    "Governance Facilitator": { "quarterly": 0, "semester": 30_000, "group": "Operations" },
    "Program Coordination": { "quarterly": 0, "semester": 30_000, "group": "Operations" },
    "Marketing Operator": { "quarterly": 0, "semester": 30_000, "group": "Operations" },
    "Accountability Lead": { "quarterly": 0, "semester": 30_000, "group": "Operations" },
    "Accountability Operator": { "quarterly": 0, "semester": 18_000, "group": "Operations" },
    "Karma Gap Subscription": { "quarterly": 0, "semester": 12_000, "group": "Operations" },
    # Programmes
    "Community Allocation": {
        "quarterly": 80_000,
        "semester": 160_000,
        "group": "Programmes",
    },
    "Ecosystem Allocation": {
        "quarterly": 100_000,
        "semester": 200_000,
        "group": "Programmes",
    },
    # Ecosystem Shared Pool
    "Founder Enablement Fund": {
        "quarterly": 0,
        "semester": 200_000,
        "group": "Programmes",
        "shared_id": "ecosystem_pool",
    },
    "Creator Fund": {
        "quarterly": 0,
        "semester": 200_000,
        "group": "Programmes",
        "shared_id": "ecosystem_pool",
    },
    "Security Subsidy Programme": {
        "quarterly": 0,
        "semester": 200_000,
        "group": "Programmes",
        "shared_id": "ecosystem_pool",
    },
    # Community Shared Pool
    "Local Nodes": {
        "quarterly": 0,
        "semester": 160_000,
        "group": "Community Pool",
        "shared_id": "community_pool",
    },
    "Community Support Programme": {
        "quarterly": 0,
        "semester": 160_000,
        "group": "Community Pool",
        "shared_id": "community_pool",
    },
}

# Totals (for quick reference)
BUDGET_TOTALS = {
    "quarterly": 380_000, # Backwards compatibility
    "semester": 760_000,  # Backwards compatibility
    "default": { "quarterly": 380_000, "semester": 760_000 }, # Fallback/Original
    "treasury": { "quarterly": 380_000, "semester": 760_000 },
    "committee": { "quarterly": 75_000, "semester": 150_000 },
    "community": { "quarterly": 80_000, "semester": 160_000 },
    "delegates": { "quarterly": 60_000, "semester": 120_000 },
    "ecosystem": { "quarterly": 100_000, "semester": 200_000 },
}

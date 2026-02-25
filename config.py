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
# Signer Aliases (Address -> Name)
# ---------------------------------------------------------------------------
SIGNER_ALIASES = {
    "treasury": {
        "0x73506528332BEcf6121F71AC9aaD43646a41994C": "SEEDGov",
        "0x66a47ea84e604451CfaC2CA6559bd9a2dE1c6504": "Ethereum TGU",
        "0x558581b0345D986bA5bD6f04Efd27e2a5B991320": "Scroll Foundation",
        "0x1Da431d2D5ECA4Df735F69fB5ea10c8E630b8f50": "Scroll Foundation",
        "0xbc72d9f10F6626271092764467983122cF15E3f4": "Accountability"
    }
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
        "Incoming Transaction",
    ],
    "committee": [
        "Uncategorised",
        "Governance Facilitator",
        "Programme Coordination",
        "Marketing Operator",
        "Operations Committee Discretionary Budget",
        "Accountability Lead",
        "Accountability Operator",
        "Internal Operations",
        "Incoming Transaction",
    ],
    "delegates": [
        "Uncategorised",
        "Governance Contribution Recognition",
        "Delegate Contributions Programme",
        "Internal Operations",
        "Incoming Transaction",
    ],
    "community": [
        "Uncategorised",
        "Local Nodes",
        "Community Support Programme",
        "Internal Operations",
        "Incoming Transaction",
    ],
    "ecosystem": [
        "Uncategorised",
        "Founder Enablement Fund",
        "Creator Fund",
        "Security Subsidy Programme",
        "Internal Operations",
        "Incoming Transaction",
    ],
}


# ---------------------------------------------------------------------------
# Budget Allocations
# Structure: { category_name: { "quarterly": amount, "group": group_name, "currency": "USD" | "SCR", "tooltip": "..." } }
# Unless specified, amounts are in USD.
# ---------------------------------------------------------------------------
BUDGETS = {
    # Operations (in order: Committee, Delegates, Discretionary, General Purpose)
    "Operations & Accountability Committee": {
        "quarterly": 69_000,
        "group": "Operations",
        "currency": "USD",
    },
    "Delegates Incentives": {
        "quarterly": 60_000,
        "group": "Operations",
        "currency": "USD",
    },
    "Operations Committee Discretionary Budget": {
        "quarterly": 61576.35,
        "group": "Operations",
        "currency": "SCR",
        "tooltip": "Originally approved as $5k/quarter, calculated at $0.0812 TWAP from January 6, when the budget proposal was approved.",
    },
    #DAO Initiatives
    "General Purpose DAO Budget": {
        "quarterly": 738916.26,
        "group": "DAO Initiatives",
        "currency": "SCR",
        "tooltip": "Originally approved as $60k/quarter, calculated at $0.0812 TWAP from January 6, when the budget proposal was approved.",
    },
    # Delegates
    "Governance Contribution Recognition": {
        "quarterly": 36_000,
        "group": "Delegate Incentive Programmes",
        "currency": "USD",
    },
    "Delegate Contributions Programme": {
        "quarterly": 24_000,
        "group": "Delegate Incentive Programmes",
        "currency": "USD",
    },
    # Operations Committee
    "Governance Facilitator": { "quarterly": 15_000, "group": "Operations Committee", "currency": "USD" },
    "Programme Coordination": { "quarterly": 15_000, "group": "Operations Committee", "currency": "USD" },
    "Marketing Operator": { "quarterly": 15_000, "group": "Operations Committee", "currency": "USD" },

    # Accountability Committee
    "Accountability Lead": { "quarterly": 15_000, "group": "Accountability Committee", "currency": "USD" },
    "Accountability Operator": { "quarterly": 9_000, "group": "Accountability Committee", "currency": "USD" },
    # Programmes
    "Community Allocation": {
        "quarterly": 985221.67,
        "group": "Ecosystem Programmes",
        "currency": "SCR",
        "tooltip": "Originally approved as $80k/quarter, calculated at $0.0812 TWAP from January 6, when the budget proposal was approved.",
    },
    "Ecosystem Allocation": {
        "quarterly": 1231527.09,
        "group": "Ecosystem Programmes",
        "currency": "SCR",
        "tooltip": "Originally approved as $100k/quarter, calculated at $0.0812 TWAP from January 6, when the budget proposal was approved.",
    },
    # Ecosystem Shared Pool
    "Founder Enablement Fund": {
        "quarterly": 1231527.09,
        "group": "Ecosystem Programmes",
        "shared_id": "ecosystem_pool",
        "currency": "SCR",
        "tooltip": "Originally approved as $100k/quarter, calculated at $0.0812 TWAP from January 6, when the budget proposal was approved.",
    },
    "Creator Fund": {
        "quarterly": 1231527.09,
        "group": "Ecosystem Programmes",
        "shared_id": "ecosystem_pool",
        "currency": "SCR",
        "tooltip": "Originally approved as $100k/quarter, calculated at $0.0812 TWAP from January 6, when the budget proposal was approved.",
    },
    "Security Subsidy Programme": {
        "quarterly": 1231527.09,
        "group": "Ecosystem Programmes",
        "shared_id": "ecosystem_pool",
        "currency": "SCR",
        "tooltip": "Originally approved as $100k/quarter, calculated at $0.0812 TWAP from January 6, when the budget proposal was approved.",
    },
    # Community Shared Pool
    "Local Nodes": {
        "quarterly": 985221.67,
        "group": "Community Programmes",
        "shared_id": "community_pool",
        "currency": "SCR",
        "tooltip": "Originally approved as $80k/quarter, calculated at $0.0812 TWAP from January 6, when the budget proposal was approved.",
    },
    "Community Support Programme": {
        "quarterly": 985221.67,
        "group": "Community Programmes",
        "shared_id": "community_pool",
        "currency": "SCR",
        "tooltip": "Originally approved as $80k/quarter, calculated at $0.0812 TWAP from January 6, when the budget proposal was approved.",
    },
}

# Totals (for quick reference)
BUDGET_TOTALS = {
    "quarterly": 374_000, # Backwards compatibility
    "default": { "quarterly": 374_000 }, # Fallback/Original
    "treasury": { "quarterly": 374_000 },
    "committee": { "quarterly": 69_000 },
    "community": { "quarterly": 80_000 },
    "delegates": { "quarterly": 60_000 },
    "ecosystem": { "quarterly": 100_000 },
}

# ---------------------------------------------------------------------------
# Budget Overrides (Context-specific)
# ---------------------------------------------------------------------------
BUDGET_OVERRIDES = {
    "committee": {
        "Operations Committee Discretionary Budget": { "group": "Operations Committee" }
    }
}

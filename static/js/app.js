/* =========================================================================
   Scroll DAO Treasury Tracker – Frontend Application
   ========================================================================= */

// XSS protection: escape HTML entities in user-controlled data
function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

const API = '';  // same origin
let state = {
    wallets: [],
    activeWallet: null,
    authenticated: false,
    stats: null,
    transactions: { items: [], total: 0, offset: 0, limit: 50 },
    budgets: null,
    filters: { direction: 'fund_movement', category: '', token: '', date_from: '', date_to: '', search: '' },
    globalData: null,
    burnCurrency: 'USD',
    treasuryMonthlyTab: 'transfers',
    treasurySwapCurrency: 'USDT',
    budgetCurrency: 'USD',
    prices: { ETH: 0, SCR: 0 },
    budgetComp: null,
    signerAliases: {},
    balanceChainView: 'total',
};

const CATEGORY_COLOURS = [
    '#ffeeda', '#34d399', '#60a5fa', '#a78bfa', '#fb923c',
    '#f87171', '#f5c842', '#818cf8', '#22d3ee', '#e879f9',
    '#84cc16', '#14b8a6',
];

const DOCS_URLS = {
    treasury:  'https://docs.scrolldaotreasury.com/tabs/tabs-guide/scroll-dao-treasury-multisig',
    committee: 'https://docs.scrolldaotreasury.com/tabs/tabs-guide/operations-and-accountability-committee-multisig',
    delegates: 'https://docs.scrolldaotreasury.com/tabs/tabs-guide/delegates-incentives-multisig',
    community: 'https://docs.scrolldaotreasury.com/tabs/tabs-guide/community-allocation-multisig',
    ecosystem: 'https://docs.scrolldaotreasury.com/tabs/tabs-guide/ecosystem-allocation-multisig',
};

// ── Initialise ──────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
    await checkAuth();
    await loadWallets();
    await loadBudgets();
    renderAuthArea();
    renderWalletSelector();
    if (state.wallets.length > 0) {
        // Check localStorage for saved wallet, otherwise default to first with address
        const savedWallet = localStorage.getItem('scrollTreasury_activeWallet');
        const validSaved = savedWallet && (savedWallet === 'overview' || (savedWallet !== 'all' && state.wallets.some(w => w.id === savedWallet)));
        const defaultWallet = validSaved ? savedWallet : 'treasury';
        selectWallet(defaultWallet);
    }
});

// ── API Helpers ─────────────────────────────────────────────────────────

async function api(path, options = {}) {
    const resp = await fetch(API + path, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status} fetching ${path}`);
    return resp.json();
}

async function checkAuth() {
    const data = await api('/api/auth/status');
    state.authenticated = data.authenticated;
}

async function loadWallets() {
    state.wallets = await api('/api/wallets');
}

async function loadBudgets() {
    state.budgets = await api('/api/budgets');
}

async function loadStats(walletId) {
    state.stats = await api(`/api/stats/${walletId}`);
}

async function loadTransactions(walletId) {
    const params = new URLSearchParams({
        limit: state.transactions.limit,
        offset: state.transactions.offset,
    });
    Object.entries(state.filters).forEach(([k, v]) => {
        if (v) params.set(k, v);
    });
    const data = await api(`/api/transactions/${walletId}?${params}`);
    state.transactions.items = data.transactions;
    state.transactions.total = data.total;
    if (data.signer_aliases) {
        state.signerAliases = data.signer_aliases;
    } else {
        state.signerAliases = {};
    }
}

async function loadBudgetComparison(walletId) {
    const data = await api(`/api/budget-comparison/${walletId}`);
    if (data.prices) state.prices = data.prices;
    state.budgetComp = data;
    return data;
}

const SAFE_API_BASES = {
    'scroll':   'https://api.safe.global/tx-service/scr/api/v1',
    'ethereum': 'https://api.safe.global/tx-service/eth/api/v1',
};

async function fetchMissingSigners() {
    const missing = state.transactions.items.filter(
        tx => tx.direction === 'out' && !tx.signers && tx.category !== 'Internal Operations'
    );
    if (missing.length === 0) return;

    // Group by wallet_id+chain — use wallet.address (checksummed) not tx.from_address (lowercase)
    const groups = {};
    for (const tx of missing) {
        const wallet = state.wallets.find(w => w.id === tx.wallet_id);
        if (!wallet || !wallet.address) continue;
        const key = `${tx.wallet_id}|${tx.chain}`;
        if (!groups[key]) groups[key] = { chain: tx.chain, address: wallet.address, hashes: new Set() };
        groups[key].hashes.add(tx.tx_hash);
    }

    const signerMap = {};
    for (const { chain, address, hashes } of Object.values(groups)) {
        const base = SAFE_API_BASES[chain];
        if (!base) continue;
        try {
            const resp = await fetch(
                `${base}/safes/${address}/multisig-transactions/?executed=true&limit=100&ordering=-executionDate`
            );
            if (!resp.ok) continue;
            const data = await resp.json();
            for (const safeTx of (data.results || [])) {
                if (!hashes.has(safeTx.transactionHash)) continue;
                const confs = safeTx.confirmations || [];
                if (!confs.length) continue;
                signerMap[safeTx.transactionHash] = confs.map(c => c.owner).sort().join(',');
            }
        } catch (e) {
            // Safe API unavailable — server-side calldata recovery handles this
        }
    }

    for (const tx of state.transactions.items) {
        if (signerMap[tx.tx_hash]) tx.signers = signerMap[tx.tx_hash];
    }
}

// ── Wallet Selection ────────────────────────────────────────────────────

async function selectWallet(walletId) {
    if (walletId === 'overview') {
        await selectOverview();
        return;
    }

    state.activeWallet = walletId;
    state.transactions.offset = 0;
    state.balanceChainView = 'total';
    state.filters = { direction: 'fund_movement', category: '', token: '', date_from: '', date_to: '', search: '' };

    // Save to localStorage
    localStorage.setItem('scrollTreasury_activeWallet', walletId);

    // Update dropdown selection
    const selector = document.getElementById('wallet-selector');
    if (selector) selector.value = walletId;

    // Update Header Title
    const wallet = state.wallets.find(w => w.id === walletId);
    const titleName = wallet ? wallet.name : 'Global "All Multisigs"';
    const titleEl = document.getElementById('active-wallet-title');
    if (titleEl) {
        if (wallet && wallet.address) {
            titleEl.innerHTML = `<a href="https://scrollscan.com/address/${wallet.address}" target="_blank" rel="noopener noreferrer" class="wallet-title-link">${titleName}</a>`;
        } else {
            titleEl.textContent = titleName;
        }
    }

    // Show loader
    const content = document.getElementById('dashboard-content');
    content.innerHTML = '<div class="loading-spinner"><div class="spinner"></div><p>Loading treasury data...</p></div>';

    // Load data
    try {
        await Promise.all([loadStats(walletId), loadTransactions(walletId)]);
        await fetchMissingSigners();
        const budgetComp = await loadBudgetComparison(walletId);
        renderDashboard(budgetComp);
    } catch (err) {
        content.innerHTML = `<div class="empty-state">
            <p><strong>Failed to load treasury data.</strong></p>
            <p>An error occurred while fetching data. Please try again in a moment.</p>
            <button class="btn" onclick="selectWallet('${walletId}')">Retry</button>
        </div>`;
    }
}

// ── Rendering ───────────────────────────────────────────────────────────

function renderAuthArea() {
    const area = document.getElementById('auth-area');
    if (state.authenticated) {
        area.innerHTML = `
            <button class="btn btn-sm" onclick="triggerFetchAll()">Refresh Data</button>
            <button class="btn btn-sm" onclick="logout()">Sign Out</button>
        `;
    } else {
        // No button for public users (hidden access via /login)
        area.innerHTML = '';
    }
}

function renderWalletSelector() {
    const container = document.getElementById('wallet-selector-container');
    if (!container) return; // Might not exist on login page

    const options = state.wallets.map(w => {
        const addrShort = w.address ? `${w.address.slice(0, 6)}...${w.address.slice(-4)}` : 'Not set';
        return `<option value="${w.id}">${w.name} (${addrShort})</option>`;
    });

    options.unshift(`<option value="overview">DAO Treasury Overview</option>`);

    container.innerHTML = `
        <div class="wallet-selector-wrapper">
            <select id="wallet-selector" class="wallet-selector" onchange="selectWallet(this.value)">
                ${options.join('')}
            </select>
            <svg class="selector-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="6 9 12 15 18 9"></polyline>
            </svg>
        </div>
    `;
}

function renderDashboard(budgetComp) {
    const isAll = state.activeWallet === 'all';
    const wallet = state.wallets.find(w => w.id === state.activeWallet) || { address: isAll ? 'all' : '' };
    const s = state.stats;
    const content = document.getElementById('dashboard-content');

    if (!wallet.address && !isAll) {
        content.innerHTML = `<div class="empty-state">
            <p><strong>This multisig has not been deployed yet.</strong></p>
            <p>Once the wallet address is configured, transactions and balances will appear here automatically.</p>
        </div>`;
        return;
    }

    const docsUrl = DOCS_URLS[state.activeWallet];
    content.innerHTML = `
        ${docsUrl ? `<div class="docs-link-bar"><a href="${docsUrl}" target="_blank" rel="noopener noreferrer" class="docs-link">Learn More About this Tab →</a></div>` : ''}
        <!-- Stats Cards -->
        <div class="stats-grid">
            ${renderStatCard('Total Transactions', s.tx_counts.total, '', '')}
            ${renderStatCard('Incoming', s.tx_counts.incoming, 'positive', '')}
            ${renderStatCard('Outgoing', s.tx_counts.outgoing, 'negative', '')}
            ${renderStatCard('Uncategorised', s.tx_counts.uncategorised, s.tx_counts.uncategorised > 0 ? 'negative' : 'positive', '')}
        </div>

        <!-- Balances -->
        <div class="section">
            <div class="section-header" style="display:flex; justify-content:space-between; align-items:center;">
                <h2 class="section-title">Balances</h2>
                ${renderBalanceChainTabs(s)}
            </div>
            <div class="balance-grid" id="balance-grid">
                ${renderBalances(getActiveBalances(s))}
            </div>
        </div>

        <!-- Two-column: Spending by Category + Monthly Burn / Treasury Activity -->
        <div class="two-col">
            <div class="card">
                <div class="card-body">
                    <h3 class="section-title" style="margin-bottom:14px;">${state.activeWallet === 'treasury' ? 'Treasury Outflow by Category' : 'Spending by Category'}</h3>
                    ${renderCategoryBreakdown(s.spending_by_category)}
                </div>
            </div>
            <div class="card">
                <div class="card-body card-body-centered">
                    ${state.activeWallet === 'treasury' ? `
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
                        <h3 class="section-title" style="margin-bottom:0;">Monthly Activity</h3>
                        <div class="currency-toggle">
                            <button class="currency-btn ${state.treasuryMonthlyTab === 'transfers' ? 'active' : ''}" onclick="toggleTreasuryMonthly('transfers')" style="padding: 4px 8px; font-size: 11px;">Transfers/Expenses</button>
                            <button class="currency-btn ${state.treasuryMonthlyTab === 'swaps' ? 'active' : ''}" onclick="toggleTreasuryMonthly('swaps')" style="padding: 4px 8px; font-size: 11px;">Treasury Swaps</button>
                        </div>
                    </div>
                    ${state.treasuryMonthlyTab === 'swaps' ? `
                    <div style="display:flex; justify-content:flex-end; margin-bottom:4px;">
                        <div class="currency-toggle" style="transform: scale(0.85); transform-origin: right;">
                            <button class="currency-btn ${state.treasurySwapCurrency === 'USDT' ? 'active' : ''}" onclick="toggleTreasurySwapCurrency('USDT')">USDT</button>
                            <button class="currency-btn ${state.treasurySwapCurrency === 'SCR' ? 'active' : ''}" onclick="toggleTreasurySwapCurrency('SCR')">SCR</button>
                        </div>
                    </div>` : ''}
                    ${state.treasuryMonthlyTab === 'transfers' ? renderMonthlyChart(s.treasury_monthly_transfers, 'transfers') : renderMonthlyChart(s.treasury_monthly_swaps, 'swaps')}
                    ${state.treasuryMonthlyTab === 'swaps' ? '<div class="stat-sub" style="text-align:center; margin-top:8px;">* Calculated at token prices when transactions were performed</div>' : ''}
                    ` : `
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
                        <h3 class="section-title" style="margin-bottom:0;">Monthly Burn Rate</h3>
                        ${state.activeWallet === 'treasury' ? `
                        <div class="currency-toggle">
                            <button class="currency-btn ${state.burnCurrency === 'USD' ? 'active' : ''}" onclick="toggleBurnCurrency('USD')">USD</button>
                            <button class="currency-btn ${state.burnCurrency === 'SCR' ? 'active' : ''}" onclick="toggleBurnCurrency('SCR')">SCR</button>
                        </div>
                        ` : ''}
                    </div>
                    ${renderMonthlyChart(s.monthly_burn, state.activeWallet === 'treasury' ? 'burn' : 'burn_usdt')}
                    <div class="stat-sub" style="text-align:center; margin-top:8px;">* Calculated at token prices when transactions were performed</div>
                    `}
                </div>
            </div>
        </div>

        <!-- Budget Comparison -->
        <div class="section">
            <div class="section-header">
                <h2 class="section-title">Budget vs Spent</h2>
            </div>
            ${renderBudgetComparison(budgetComp)}
        </div>

        <!-- Transactions Table -->
        <div class="section">
            <div class="section-header">
                <h2 class="section-title">Transaction History</h2>
                <div class="section-actions">
                    <a href="/api/export/${state.activeWallet}" class="btn btn-sm" download>Export CSV</a>
                    ${state.authenticated ? `<button class="btn btn-sm" onclick="triggerFetch('${state.activeWallet}')">Fetch New</button>` : ''}
                </div>
            </div>
            ${renderFilters()}
            <div class="card">
                <div class="tx-table-wrap">
                    ${renderTransactionsTable()}
                </div>
                ${renderPagination()}
            </div>
        </div>
    `;
}

// ── Stat Card ───────────────────────────────────────────────────────────

function renderStatCard(label, value, cls, sub) {
    return `<div class="stat-card">
        <div class="stat-label">${label}</div>
        <div class="stat-value ${cls}">${formatNumber(value, 0)}</div>
        ${sub ? `<div class="stat-sub">${sub}</div>` : ''}
    </div>`;
}

// ── Balances ────────────────────────────────────────────────────────────

function getActiveBalances(s) {
    const byChain = s.balances_by_chain || {};
    if (state.balanceChainView === 'scroll') return byChain.scroll || [];
    if (state.balanceChainView === 'ethereum') return byChain.ethereum || [];
    return s.balances || [];
}

function renderBalanceChainTabs(s) {
    const hasEthereum = (s.balances_by_chain?.ethereum?.length || 0) > 0;
    if (!hasEthereum) return '';
    const tabs = [
        { id: 'total', label: 'Total' },
        { id: 'scroll', label: 'Scroll' },
        { id: 'ethereum', label: 'Mainnet' },
    ];
    return `<div class="balance-chain-tabs">${tabs.map(t =>
        `<button class="balance-chain-tab ${state.balanceChainView === t.id ? 'active' : ''}" data-view="${t.id}" onclick="setBalanceChainView('${t.id}')">${t.label}</button>`
    ).join('')}</div>`;
}

function setBalanceChainView(view) {
    state.balanceChainView = view;
    const s = state.stats;
    if (!s) return;
    document.querySelectorAll('.balance-chain-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === view);
    });
    document.getElementById('balance-grid').innerHTML = renderBalances(getActiveBalances(s));
}

function renderBalances(balances) {
    if (!balances || balances.length === 0) {
        return '<div class="empty-state"><p>No balance data yet. Waiting for data sync...</p></div>';
    }

    // Calculate total treasury value
    const totalUsd = balances.reduce((acc, b) => acc + (b.balance_usd || 0), 0);

    // Helper for logos
    const getLogo = (symbol, address) => {
        if (symbol === 'SCR') return '/static/img/Scroll_Logomark.ad5d0348.svg';
        if (symbol === 'ETH' || symbol === 'WETH') return 'https://assets.coingecko.com/coins/images/279/small/ethereum.png';
        if (symbol === 'USDT') return 'https://assets.coingecko.com/coins/images/325/small/Tether.png';
        if (symbol === 'USDC') return 'https://assets.coingecko.com/coins/images/6319/small/usdc.png';
        if (address) {
            // Try TrustWallet mainnet mapping for now, or a generic placeholder
            return `https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/${address}/logo.png`;
        }
        return ''; // Return empty string to trigger onerror or fallback
    };

    const tokenCards = balances.map(b => {
        const logoSrc = getLogo(b.token_symbol, b.contract_address);
        // Fallback to a generic icon/placeholder logic could be done via onerror in HTML, 
        // but for now we'll just try the URL.
        const logoHtml = `<img src="${logoSrc}" class="token-logo" alt="${b.token_symbol}" onerror="this.style.display='none'">`;

        return `
        <div class="balance-card">
            <div class="balance-header">
                ${logoHtml}
                <div class="balance-token">${b.token_symbol}</div>
            </div>
            <div class="balance-amount">
                ${formatTokenAmount(b.balance_decimal, b.token_symbol)}
            </div>
            <div class="balance-usd">
                $${formatNumber(b.balance_usd)}
            </div>
             <div class="balance-price">
                ${b.token_symbol} $${formatNumber(b.price_usd)}
            </div>
            <div class="balance-updated">Updated ${timeAgo(b.last_updated)}</div>
        </div>
    `}).join('');

    const totalCard = `
        <div class="balance-card total-card">
            <div class="balance-token">Current Treasury Value</div>
            <div class="balance-amount" style="font-size: 1.5rem; margin-top: 0.5rem;">$${formatNumber(totalUsd)}</div>
            <div class="balance-updated">Current Estimate</div>
        </div>
    `;

    // Render total card first, then tokens
    return totalCard + tokenCards;
}

// ── Category Breakdown ──────────────────────────────────────────────────

function renderCategoryBreakdown(spending) {
    if (!spending || spending.length === 0) {
        return '<div class="empty-state"><p>No spending data yet</p></div>';
    }
    // Group by category
    const byCategory = {};
    spending.forEach(s => {
        if (!byCategory[s.category]) byCategory[s.category] = { total: 0, tokens: {}, count: 0 };
        byCategory[s.category].total += s.total;
        byCategory[s.category].count += s.tx_count;
        byCategory[s.category].tokens[s.token_symbol] = (byCategory[s.category].tokens[s.token_symbol] || 0) + s.total;
    });

    const sorted = Object.entries(byCategory).sort((a, b) => b[1].total - a[1].total);
    return `<ul class="category-list">
        ${sorted.map(([cat, data], i) => {
        const tokenStr = Object.entries(data.tokens).map(([sym, amt]) => `${formatTokenAmount(amt, sym)} ${sym}`).join(', ');

        // Re-calculating total USD from the input 'spending' array which now has 'total_usd'
        const catTotalUsd = spending.filter(s => s.category === cat).reduce((acc, s) => acc + (s.total_usd || 0), 0);

        let amountStr = '';
        if (cat === 'Treasury Swaps') {
            amountStr = `${formatNumber(catTotalUsd)} USDT`;
        } else if (cat === 'Funds from the Previous DAO Treasury') {
            amountStr = `$${formatNumber(catTotalUsd)}`;
        } else {
            amountStr = `$${formatNumber(catTotalUsd)}`;
        }

        return `<li class="category-item">
                <span class="category-dot" style="background:${CATEGORY_COLOURS[i % CATEGORY_COLOURS.length]}"></span>
                <div class="category-info">
                    <div class="category-name">${cat}</div>
                    <div class="category-count">${data.count} transactions</div>
                </div>
                <div class="category-amount">
                    <div>${tokenStr}</div>
                    <div class="amount-usd">${amountStr}</div>
                </div>
            </li>`;
    }).join('')}
    </ul>`;
}

function renderMonthlyChart(monthly, type = 'burn') {
    if (!monthly || monthly.length === 0) {
        return '<div class="empty-state"><p>No monthly data yet</p></div>';
    }

    if (type === 'burn' || type === 'transfers' || type === 'burn_usdt') {
        const byMonth = {};
        monthly.forEach(m => {
            let val = 0;
            if (type === 'burn') {
                val = state.burnCurrency === 'SCR' ? (m.total_scr || 0) : (m.total_usd || 0);
            } else {
                val = m.total_usd || 0;
            }
            byMonth[m.month] = (byMonth[m.month] || 0) + val;
        });
        const entries = Object.entries(byMonth).sort();
        const max = Math.max(...entries.map(e => e[1]), 1);

        return `<div class="chart-bars">
            ${entries.map(([month, total]) => {
            const pct = (total / max) * 100;
            const label = month.split('-')[1] + '/' + month.split('-')[0].slice(2);
            let valStr = `${formatNumber(total)} USDT`;
            if (type === 'burn') {
                valStr = state.burnCurrency === 'SCR' ? `${formatNumber(total)} SCR` : `$${formatNumber(total)}`;
            } else if (type === 'burn_usdt') {
                valStr = `${formatNumber(total)} USDT`;
            }
            return `<div class="chart-bar-wrap">
                    <div class="chart-value">${valStr}</div>
                    <div class="chart-bar" style="height:${Math.max(pct, 3)}%; background: linear-gradient(180deg, #FFDCB1 0%, rgba(255, 220, 177, 0.3) 100%);"></div>
                    <div class="chart-label">${label}</div>
                </div>`;
        }).join('')}
        </div>`;
    }

    // For Treasury Swaps: display SCR out or USDT in based on toggle
    if (type === 'swaps') {
        const entries = [...monthly].sort((a, b) => a.month.localeCompare(b.month));

        let max = 1;
        if (state.treasurySwapCurrency === 'USDT') {
            max = Math.max(...entries.map(e => e.usdt_obtained || 0), 1);
        } else {
            max = Math.max(...entries.map(e => e.scr_swapped || 0), 1);
        }

        return `<div class="chart-bars">
            ${entries.map(data => {
            const isUsdt = state.treasurySwapCurrency === 'USDT';
            const val = isUsdt ? data.usdt_obtained : data.scr_swapped;
            const pct = (val / max) * 100;
            const label = data.month.split('-')[1] + '/' + data.month.split('-')[0].slice(2);

            const barGradient = isUsdt
                ? 'linear-gradient(180deg, rgba(52, 211, 153, 0.8) 0%, rgba(52, 211, 153, 0.2) 100%)'
                : 'linear-gradient(180deg, rgba(248, 113, 113, 0.8) 0%, rgba(248, 113, 113, 0.2) 100%)';
            const valColor = isUsdt ? 'var(--accent-green)' : 'var(--accent-red)';
            const valPrefix = '';
            const valSuffix = isUsdt ? ' USDT' : ' SCR';
            const topLabel = isUsdt
                ? `<div class="chart-value" style="font-size:10px; color:var(--text-muted);">${formatNumber(data.scr_swapped)} SCR <br/>↓<br/></div>`
                : `<div class="chart-value" style="font-size:10px; color:var(--text-muted);">${formatNumber(data.usdt_obtained)} USDT <br/>↑<br/></div>`;

            return `<div class="chart-bar-wrap" style="width: auto; min-width: 60px;">
                    ${topLabel}
                    <div class="chart-value" style="color:${valColor};">${valPrefix}${formatNumber(val)}${valSuffix}</div>
                    <div class="chart-bar" style="height:${Math.max(pct, 3)}%; background: ${barGradient};"></div>
                    <div class="chart-label">${label}</div>
                </div>`;
        }).join('')}
        </div>`;
    }
}

function toggleBurnCurrency(curr) {
    if (state.burnCurrency === curr) return;
    state.burnCurrency = curr;
    // Re-render from cached state instead of re-fetching
    if (state.budgetComp) {
        renderDashboard(state.budgetComp);
    }
}

function toggleTreasuryMonthly(tab) {
    if (state.treasuryMonthlyTab === tab) return;
    state.treasuryMonthlyTab = tab;
    if (state.budgetComp) {
        renderDashboard(state.budgetComp);
    }
}

function toggleTreasurySwapCurrency(curr) {
    if (state.treasurySwapCurrency === curr) return;
    state.treasurySwapCurrency = curr;
    if (state.budgetComp) {
        renderDashboard(state.budgetComp);
    }
}

// ── Budget Comparison ───────────────────────────────────────────────────

function renderBudgetComparison(budgetComp) {
    if (!budgetComp || !budgetComp.categories || budgetComp.categories.length === 0) {
        return '<div class="card"><div class="card-body"><div class="empty-state"><p>No budget data for this wallet</p></div></div></div>';
    }

    const totals = budgetComp.totals;

    // Group categories - ensure Operations comes first, then Programmes
    // Use groups from API if available, else fallback to hardcoded (or just use keys)
    // The API now returns 'groups' in order
    const groupOrder = budgetComp.groups || ['Operations', 'Ecosystem Programmes', 'Community Programmes', 'Delegate Incentive Programmes', 'Other'];
    const groups = {};
    budgetComp.categories.forEach(c => {
        const g = c.group || 'Other';
        if (!groups[g]) groups[g] = [];
        groups[g].push(c);
    });

    let html = `<div class="budget-summary" style="display:flex; gap:16px; margin-bottom:16px; flex-wrap:wrap">`;

    // Render USD Summary if applicable
    if (totals.budget_usd > 0 || totals.spent_usd_native > 0 || state.activeWallet === 'treasury') {
        const usdPct = totals.budget_usd > 0 ? (totals.spent_usd_native / totals.budget_usd * 100) : 0;

        let rightSideHtml = '';
        if (state.activeWallet === 'treasury') {
            const subSpentPct = totals.budget_usd > 0 ? ((totals.treasury_sub_spent_usd || 0) / totals.budget_usd * 100) : 0;
            rightSideHtml = `
                <div style="text-align:right">
                    <div style="font-size:12px; color:var(--text-muted); margin-bottom:4px">Total Transferred to other DAO Multisigs <span class="tooltip-icon" title="Transferred to the Operations and Accountability and the Delegates' Incentives Multisigs">ⓘ</span></div>
                    <div style="font-size:16px; font-weight:700; color:var(--text-main); margin-bottom:8px">$${formatNumber(totals.treasury_transferred_usd || 0)}</div>
                    <div style="font-size:12px; color:var(--text-muted); margin-bottom:4px">Total Spent <span class="tooltip-icon" title="Spent by the Operations and Accountability and the Delegates' Incentives Multisigs">ⓘ</span></div>
                    <div style="font-size:16px; font-weight:700; color:${subSpentPct > 90 ? 'var(--accent-red)' : 'var(--accent-green)'}">$${formatNumber(totals.treasury_sub_spent_usd || 0)}</div>
                </div>
            `;
        } else if (state.activeWallet !== 'treasury') {
            rightSideHtml = `
                <div style="text-align:right">
                    <div style="font-size:12px; color:var(--text-muted); margin-bottom:4px">Total Spent</div>
                    <div style="font-size:18px; font-weight:700; color:${usdPct > 90 ? 'var(--accent-red)' : 'var(--accent-green)'}">${formatNumber(totals.spent_usd_native)} USDT</div>
                </div>
            `;
        } else {
            rightSideHtml = `
                <div style="text-align:right">
                    <div style="font-size:12px; color:var(--text-muted); margin-bottom:4px">Total Spent</div>
                    <div style="font-size:18px; font-weight:700; color:${usdPct > 90 ? 'var(--accent-red)' : 'var(--accent-green)'}">$${formatNumber(totals.spent_usd_native)}</div>
                </div>
            `;
        }

        html += `
        <div class="budget-summary-item" style="flex:1; min-width:250px; background:var(--bg-card); padding:20px; border-radius:12px; border:1px solid var(--border-color);">
            <div style="font-weight:700; font-size:14px; color:var(--text-muted); margin-bottom:16px; text-transform:uppercase; letter-spacing:0.5px">Fiat Budgets (USDT)</div>
            <div style="display:flex; justify-content:space-between; margin-bottom:12px">
                <div>
                    <div style="font-size:12px; color:var(--text-muted); margin-bottom:4px">Quarterly Limit</div>
                    <div style="font-size:18px; font-weight:700; color:var(--accent-scroll)">${state.activeWallet !== 'treasury' ? `${formatNumber(totals.budget_usd)} USDT` : `$${formatNumber(totals.budget_usd)}`}</div>
                </div>
                ${rightSideHtml}
            </div>
        </div>`;
    }

    // Render SCR Summary if applicable
    if (totals.budget_scr > 0 || totals.spent_scr_native > 0 || state.activeWallet === 'treasury') {
        const scrPct = totals.budget_scr > 0 ? (totals.spent_scr_native / totals.budget_scr * 100) : 0;

        // Dynamic tooltip based on the active wallet
        const scrTooltips = {
            "treasury": "Originally approved as $245k/quarter, calculated at $0.0812 TWAP from January 6, when the budget proposals were approved.",
            "committee": "Originally approved as $5k/quarter, calculated at $0.0812 TWAP from January 6, when the budget proposals were approved.",
            "ecosystem": "Originally approved as $100k/quarter, calculated at $0.0812 TWAP from January 6, when the budget proposals were approved.",
            "community": "Originally approved as $80k/quarter, calculated at $0.0812 TWAP from January 6, when the budget proposals were approved."
        };
        const totalScrTooltip = scrTooltips[state.activeWallet] || "Calculated at $0.0812 TWAP from January 6.";

        let rightSideHtmlScr = '';
        if (state.activeWallet === 'treasury') {
            const swappedPct = totals.budget_scr > 0 ? ((totals.treasury_scr_swapped || 0) / totals.budget_scr * 100) : 0;
            const subSpentEcoPct = totals.treasury_transferred_scr_initiatives > 0 ? ((totals.treasury_spent_scr_initiatives_usd || 0) / totals.treasury_transferred_scr_initiatives * 100) : 0;
            const committeeException = totals.treasury_usdt_committee_exception || 0;
            const displayedTransferred = (totals.treasury_transferred_scr_initiatives || 0) + committeeException;
            const displayedSpent = (totals.treasury_spent_scr_initiatives_usd || 0) + committeeException;
            const remainingUsdt = (totals.treasury_usdt_available_from_swap || 0) - displayedTransferred;

            const transferredTooltip = 'Transferred to the Community Allocations, Ecosystem Allocations, and Operations & Accountability (Discretionary Budget) Multisigs. Also includes two one-time exceptions deducted from Remaining USDT: $23,020 paid in committee salaries from swap USDT instead of Foundation stablecoins (Apr 9 2026), and $10,020 paid for the governance frontend (Apr 14 2026).';
            const spentTooltip = 'Spent by the Community Allocations, Ecosystem Allocations, and Operations & Accountability (Discretionary Budget) Multisigs. Note: two one-time exceptions ($23,020 committee salaries and $10,020 governance frontend) were paid directly from treasury swap USDT and are deducted from Remaining USDT separately.';

            rightSideHtmlScr = `
                <div style="text-align:right">
                    <div style="font-size:12px; color:var(--text-muted); margin-bottom:4px">Total Swapped to USDT</div>
                    <div style="font-size:16px; font-weight:700; color:${swappedPct > 90 ? 'var(--accent-red)' : 'var(--accent-green)'}; margin-bottom:8px">${formatNumber(totals.treasury_scr_swapped || 0)} SCR</div>

                    <div style="font-size:12px; color:var(--text-muted); margin-bottom:4px">Total USDT Obtained</div>
                    <div style="font-size:16px; font-weight:700; color:var(--text-main); margin-bottom:8px">$${formatNumber(totals.treasury_usdt_available_from_swap || 0)}</div>

                    <div style="font-size:12px; color:var(--text-muted); margin-bottom:4px">Total USDT Transferred to other DAO Multisigs <span class="tooltip-icon" title="${transferredTooltip}">ⓘ</span></div>
                    <div style="font-size:16px; font-weight:700; color:var(--text-main); margin-bottom:8px">$${formatNumber(displayedTransferred)}</div>

                    <div style="font-size:12px; color:var(--text-muted); margin-bottom:4px">Remaining USDT</div>
                    <div style="font-size:16px; font-weight:700; color:var(--text-main); margin-bottom:8px">$${formatNumber(remainingUsdt)}</div>

                    <div style="font-size:12px; color:var(--text-muted); margin-bottom:4px">Total USDT Spent <span class="tooltip-icon" title="${spentTooltip}">ⓘ</span></div>
                    <div style="font-size:16px; font-weight:700; color:${subSpentEcoPct > 90 ? 'var(--accent-red)' : 'var(--accent-green)'}">$${formatNumber(displayedSpent)}</div>
                </div>
            `;
        } else {
            if (totals.budget_scr > 0) {
                rightSideHtmlScr = `
                    <div style="text-align:right">
                        <div style="font-size:12px; color:var(--text-muted); margin-bottom:4px">Total Spent</div>
                        <div style="font-size:18px; font-weight:700; color:${scrPct > 90 ? 'var(--accent-red)' : 'var(--accent-green)'}">${formatNumber(totals.spent_scr)} ${state.activeWallet === 'treasury' ? 'SCR' : 'SCR'} <span style="font-size:12px;color:var(--text-muted); font-weight:400">(~${formatNumber(totals.spent_scr_usd_native || 0)} USDT spent)</span></div>
                    </div>
                `;
            }
        }


        html += `
        <div class="budget-summary-item" style="flex:1; min-width:250px; background:var(--bg-card); padding:20px; border-radius:12px; border:1px solid var(--border-color);">
            <div style="font-weight:700; font-size:14px; color:var(--text-muted); margin-bottom:16px; text-transform:uppercase; letter-spacing:0.5px">
                Token Budgets (SCR) <span class="tooltip-icon" title="${totalScrTooltip}">ⓘ</span>
            </div>
            <div style="display:flex; justify-content:space-between; margin-bottom:12px">
                <div>
                    <div style="font-size:12px; color:var(--text-muted); margin-bottom:4px">Quarterly Limit</div>
                    <div style="font-size:18px; font-weight:700; color:var(--accent-scroll)">${formatNumber(totals.budget_scr)} SCR</div>
                </div>
                ${rightSideHtmlScr}
            </div>
        </div>`;
    }

    html += `</div><div class="card"><div class="card-body">`;

    // Iterate in order
    groupOrder.forEach(groupName => {
        const cats = groups[groupName];
        if (!cats || cats.length === 0) return;

        html += `<div class="budget-group-title">${groupName}</div>`;

        // Group by shared_id
        const sharedGroups = {};
        const individualItems = [];

        cats.forEach(c => {
            if (c.shared_id) {
                if (!sharedGroups[c.shared_id]) {
                    sharedGroups[c.shared_id] = {
                        items: [],
                        limit: c.budget_quarterly,
                        spent: 0,
                        spent_scr: 0,
                        name: c.shared_id === 'ecosystem_pool' ? 'Ecosystem Shared Pool' : (c.shared_id === 'community_pool' ? 'Community Shared Pool' : 'Shared Pool'),
                        currency: c.currency,
                        tooltip: c.tooltip
                    };
                }
                sharedGroups[c.shared_id].items.push(c);
                sharedGroups[c.shared_id].spent += c.spent_usd;
                if (!sharedGroups[c.shared_id].spent_scr) sharedGroups[c.shared_id].spent_scr = 0;
                sharedGroups[c.shared_id].spent_scr += (c.spent_scr || 0);
                if (!sharedGroups[c.shared_id].spent_usd_native) sharedGroups[c.shared_id].spent_usd_native = 0;
                sharedGroups[c.shared_id].spent_usd_native += (c.spent_usd_native || 0);
            } else {
                individualItems.push(c);
            }
        });

        // Render Shared Pools
        Object.values(sharedGroups).forEach(pool => {
            const spentAmt = pool.currency === 'SCR' ? pool.spent_scr : pool.spent;
            const pct = pool.limit > 0 ? Math.min((spentAmt / pool.limit) * 100, 150) : 0;
            const fillClass = pct > 100 ? 'over' : pct > 75 ? 'warning' : 'under';

            const tooltipHtml = pool.tooltip ? `<span class="tooltip-icon-category" title="${escapeHtml(pool.tooltip)}">ⓘ</span>` : '';

            const isTransfer = state.activeWallet === 'treasury' && ["Operations Committee Discretionary Budget", "Community Allocation", "Ecosystem Allocation", "Ecosystem Shared Pool", "Community Shared Pool"].includes(pool.name);
            const actionWord = isTransfer ? 'transferred' : 'spent';

            html += `
            <div class="budget-item shared-pool">
                <div class="budget-label">
                    <span class="budget-name" style="font-weight:700">${pool.name} ${tooltipHtml}</span>
                    <span class="budget-amounts">${pool.currency === 'SCR' ?
                    `${formatNumber(pool.spent_scr)} SCR / ${formatNumber(pool.limit)} SCR <span style="font-size:12px;color:var(--text-muted)">(~${formatNumber(pool.spent_usd_native || 0)} USDT ${actionWord})</span>` :
                    (state.activeWallet !== 'treasury' ? `${formatNumber(pool.spent)} USDT / ${formatNumber(pool.limit)} USDT` : `$${formatNumber(pool.spent)} / $${formatNumber(pool.limit)}`)}</span>
                </div>
                <div class="budget-bar">
                    <div class="budget-fill ${fillClass}" style="width:${Math.min(pct, 100)}%"></div>
                </div>
                <!-- Breakdown -->
                <div class="shared-breakdown" style="padding-left:16px; margin-top:8px; display:flex; flex-direction:column; gap:4px; font-size:13px; color:var(--text-muted)">
                    ${pool.items.map(i => {
                        const itemActionWord = (state.activeWallet === 'treasury' && ["Operations Committee Discretionary Budget", "Community Allocation", "Ecosystem Allocation"].includes(i.category)) ? 'transferred' : 'spent';
                        return `
                        <div style="display:flex; justify-content:space-between">
                            <span>${i.category}</span>
                            <span>${i.currency === 'SCR' ? `${formatNumber(i.spent_scr)} SCR <span style="color:var(--text-muted)">(~${formatNumber(i.spent_usd_native || 0)} USDT ${itemActionWord})</span>` : (state.activeWallet !== 'treasury' ? `${formatNumber(i.spent_usd)} USDT` : `$${formatNumber(i.spent_usd)}`)}</span>
                        </div>
                    `}).join('')}
                </div>
            </div>`;
        });

        // Render Individual Items
        individualItems.forEach(c => {
            const spentAmt = c.currency === 'SCR' ? (c.spent_scr || 0) : c.spent_usd;
            const pct = c.budget_quarterly > 0 ? Math.min((spentAmt / c.budget_quarterly) * 100, 150) : 0;
            const fillClass = pct > 100 ? 'over' : pct > 75 ? 'warning' : 'under';

            const tooltipHtml = c.tooltip ? `<span class="tooltip-icon-category" title="${escapeHtml(c.tooltip)}">ⓘ</span>` : '';

            const isTransfer = state.activeWallet === 'treasury' && ["Operations Committee Discretionary Budget", "Community Allocation", "Ecosystem Allocation"].includes(c.category);
            const actionWord = isTransfer ? 'transferred' : 'spent';

            html += `
            <div class="budget-item">
                <div class="budget-label">
                    <span class="budget-name">${c.category} ${tooltipHtml}</span>
                    <span class="budget-amounts">${c.currency === 'SCR' ?
                    `${formatNumber(c.spent_scr || 0)} SCR / ${formatNumber(c.budget_quarterly)} SCR <span style="font-size:12px;color:var(--text-muted)">(~${formatNumber(c.spent_usd_native || 0)} USDT ${actionWord})</span>` :
                    (state.activeWallet !== 'treasury' ? `${formatNumber(c.spent_usd)} USDT / ${formatNumber(c.budget_quarterly)} USDT` : `$${formatNumber(c.spent_usd)} / $${formatNumber(c.budget_quarterly)}`)}</span>
                </div>
                <div class="budget-bar">
                    <div class="budget-fill ${fillClass}" style="width:${Math.min(pct, 100)}%"></div>
                </div>
            </div>`;
        });
    });

    html += '</div></div>';
    return html;
}

// ── Filters ─────────────────────────────────────────────────────────────

function renderFilters() {
    let cats = [];
    if (state.activeWallet === 'all') {
        const allCats = new Set();
        state.wallets.forEach(w => w.categories.forEach(c => allCats.add(c)));
        cats = Array.from(allCats).sort();
    } else {
        const wallet = state.wallets.find(w => w.id === state.activeWallet);
        cats = wallet ? wallet.categories : [];
    }
    const tokens = state.stats ? state.stats.tokens : [];

    return `<div class="tx-controls">
        <select onchange="applyFilter('direction', this.value)">
            <option value="">All Transactions</option>
            <option value="in" ${state.filters.direction === 'in' ? 'selected' : ''}>Incoming</option>
            <option value="out" ${state.filters.direction === 'out' ? 'selected' : ''}>Outgoing</option>
            <option value="fund_movement" ${state.filters.direction === 'fund_movement' ? 'selected' : ''}>Fund Movements</option>
        </select>
        <select onchange="applyFilter('category', this.value)">
            <option value="">All Categories</option>
            ${cats.map(c => `<option value="${c}" ${state.filters.category === c ? 'selected' : ''}>${c}</option>`).join('')}
        </select>
        <select onchange="applyFilter('token', this.value)">
            <option value="">All Tokens</option>
            ${tokens.map(t => `<option value="${t}" ${state.filters.token === t ? 'selected' : ''}>${t}</option>`).join('')}
        </select>
        <div class="date-group">
            <input type="date" value="${state.filters.date_from}" onchange="applyFilter('date_from', this.value)" title="From date">
            <input type="date" value="${state.filters.date_to}" onchange="applyFilter('date_to', this.value)" title="To date">
        </div>
        <input type="text" placeholder="Search tx hash, address..." value="${state.filters.search}" oninput="debounceSearch(this.value)" style="min-width:200px;">
    </div>`;
}

let searchTimeout;
function debounceSearch(val) {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => applyFilter('search', val), 400);
}

async function applyFilter(key, value) {
    state.filters[key] = value;
    state.transactions.offset = 0;
    await loadTransactions(state.activeWallet);
    // Re-render just the table + pagination
    const tableWrap = document.querySelector('.tx-table-wrap');
    if (tableWrap) tableWrap.innerHTML = renderTransactionsTable();
    const pagination = document.querySelector('.pagination');
    if (pagination) pagination.outerHTML = renderPagination();
}

// ── Transactions Table ──────────────────────────────────────────────────

function renderTransactionsTable() {
    const txs = state.transactions.items;
    if (txs.length === 0) {
        return '<div class="empty-state" style="padding:32px"><p>No transactions found</p></div>';
    }

    let cats = [];
    const isAll = state.activeWallet === 'all';
    if (isAll) {
        const allCats = new Set();
        state.wallets.forEach(w => w.categories.forEach(c => allCats.add(c)));
        cats = Array.from(allCats).sort();
    } else {
        const wallet = state.wallets.find(w => w.id === state.activeWallet);
        cats = wallet ? wallet.categories : [];
    }

    let rows = txs.map(tx => {
        const isUncat = tx.category === 'Uncategorised';
        const badgeClass = isUncat ? 'cat-unset' : 'cat-set';

        let categoryCell;
        if (state.authenticated) {
            categoryCell = `<select class="tx-category-select" onchange="categoriseTx(${tx.id}, this.value)"
                data-txid="${tx.id}">
                ${cats.map(c => `<option value="${escapeHtml(c)}" ${tx.category === c ? 'selected' : ''}>${escapeHtml(c)}</option>`).join('')}
            </select>`;
        } else {
            categoryCell = `<span class="tx-badge ${badgeClass}">${escapeHtml(tx.category)}</span>`;
        }

        let notesCell;
        if (state.authenticated) {
            notesCell = `<input type="text" class="tx-note-input" value="${escapeHtml(tx.notes)}" 
                onblur="updateTxNote(${tx.id}, this.value)" placeholder="Add note..." style="width:100%; min-width:120px;">`;
        } else {
            notesCell = `<span class="tx-note-text" title="${escapeHtml(tx.notes)}">${escapeHtml(tx.notes)}</span>`;
        }
        // Only show signers if the transaction is outgoing from THIS multisig AND not an internal operation
        const hideSigners = tx.direction === 'in' || tx.category === 'Internal Operations';
        const signersHtml = hideSigners ? formatSigners('') : formatSigners(tx.signers);

        // Specific categories are treated as non-expenses ONLY when transferred from the Treasury directly
        const isTreasuryTransferCategory = ['Operations & Accountability Committee', 'Delegates Incentives', 'Operations Committee Discretionary Budget', 'Community Allocation', 'Ecosystem Allocation'].includes(tx.category);
        const isNonExpense = ['Internal Operations', 'Treasury Swap', 'Internal Transfer'].includes(tx.category) || (tx.wallet_id === 'treasury' && isTreasuryTransferCategory);

        const amountClass = tx.category === 'Internal Operations' ? 'neutral' : tx.direction;

        const rightArrowCategories = [
            'Governance Facilitator', 'Programme Coordination', 'Marketing Operator',
            'Operations Committee Discretionary Budget', 'Accountability Lead', 'Accountability Operator',
            'Governance Contribution Recognition', 'Delegate Contributions Programme',
            'Local Nodes', 'Community Support Programme',
            'Founder Enablement Fund', 'Creator Fund', 'Security Subsidy Programme'
        ];

        let customEmoji = '';
        if (tx.category === 'Uncategorised') customEmoji = '❔ ';
        else if (isTreasuryTransferCategory || tx.category === 'Internal Transfer' || rightArrowCategories.includes(tx.category)) customEmoji = '➡️ ';
        else if (tx.category === 'Internal Operations') customEmoji = '⚙️ ';
        else if (tx.category === 'Treasury Swap') customEmoji = '🔄 ';
        else if (tx.category === 'Incoming Transaction') customEmoji = '⬇️ ';
        else if (tx.category === 'General Purpose DAO Budget' && tx.direction === 'out') customEmoji = '💸 ';
        else if (tx.category === 'Funds from the Previous DAO Treasury') customEmoji = '🏛️ ';

        const amountSign = tx.category === 'Internal Operations' ? customEmoji : (tx.direction === 'in' ? `${customEmoji}+` : `${customEmoji}-`);
        const amountHtml = `<span class="tx-amount ${amountClass}">${amountSign}${formatTokenAmount(tx.value_decimal, tx.token_symbol)} ${escapeHtml(tx.token_symbol)}</span>`;

        const sourceWalletName = state.wallets.find(w => w.id === tx.wallet_id)?.name || tx.wallet_id;

        return `<tr>
            ${isAll ? `<td><span class="tx-badge" style="background:var(--bg-lighter); color:var(--text-main); font-size:11px; max-width: 120px; text-overflow: ellipsis; overflow: hidden; white-space: nowrap; display: inline-block;" title="${escapeHtml(sourceWalletName)}">${escapeHtml(sourceWalletName)}</span></td>` : ''}
            <td>${escapeHtml(tx.date)}</td>
            <td><a class="tx-hash" href="${tx.chain === 'ethereum' ? 'https://etherscan.io' : 'https://scrollscan.com'}/tx/${escapeHtml(tx.tx_hash)}" target="_blank" rel="noopener">${escapeHtml(tx.tx_hash.slice(0, 10))}...<span class="chain-badge ${tx.chain === 'ethereum' ? 'chain-eth' : 'chain-scroll'}">${tx.chain === 'ethereum' ? 'ETH' : 'SCR'}</span></a></td>
            <td><span class="tx-addr">${shortenAddr(tx.from_address)}</span></td>
            <td><span class="tx-addr">${shortenAddr(tx.to_address)}</span></td>
            <td>${amountHtml}</td>
            <td>${escapeHtml(tx.tx_type)}</td>
            <td>${categoryCell}</td>
            <td>${signersHtml}</td>
            <td>${notesCell}</td>
        </tr>`;
    }).join('');

    return `<table class="tx-table">
        <thead><tr>
            ${isAll ? '<th>Wallet</th>' : ''}
            <th>Date</th><th>TX Hash</th><th>From</th><th>To</th>
            <th>Amount</th><th>Type</th><th>Category</th><th>Signers</th><th>Notes</th>
        </tr></thead>
        <tbody>${rows}</tbody>
    </table>`;
}

// ── Pagination ──────────────────────────────────────────────────────────

function renderPagination() {
    const { total, offset, limit } = state.transactions;
    const page = Math.floor(offset / limit) + 1;
    const totalPages = Math.ceil(total / limit);

    return `<div class="pagination">
        <span class="pagination-info">Showing ${offset + 1}-${Math.min(offset + limit, total)} of ${total}</span>
        <div class="pagination-btns">
            <button class="btn btn-sm" onclick="changePage(-1)" ${page <= 1 ? 'disabled' : ''}>Previous</button>
            <button class="btn btn-sm" onclick="changePage(1)" ${page >= totalPages ? 'disabled' : ''}>Next</button>
        </div>
    </div>`;
}

async function changePage(dir) {
    state.transactions.offset += dir * state.transactions.limit;
    if (state.transactions.offset < 0) state.transactions.offset = 0;
    await loadTransactions(state.activeWallet);
    const tableWrap = document.querySelector('.tx-table-wrap');
    if (tableWrap) tableWrap.innerHTML = renderTransactionsTable();
    const pagination = document.querySelector('.pagination');
    if (pagination) pagination.outerHTML = renderPagination();
}

// ── Categorise ──────────────────────────────────────────────────────────

async function categoriseTx(txId, category) {
    const data = await api(`/api/transactions/${txId}/categorise`, {
        method: 'POST',
        body: JSON.stringify({ category }),
    });
    if (data.success) {
        showToast(`Categorised as "${category}"`, 'success');
        // Update local state
        const tx = state.transactions.items.find(t => t.id === txId);
        if (tx) tx.category = category;
    } else {
        showToast(data.error || 'Failed to categorise', 'error');
    }
}

async function updateTxNote(txId, notes) {
    const tx = state.transactions.items.find(t => t.id === txId);
    if (tx && tx.notes === notes) return; // No change

    const data = await api(`/api/transactions/${txId}/categorise`, {
        method: 'POST',
        body: JSON.stringify({ notes }),
    });

    if (data.success) {
        showToast('Note updated', 'success');
        // Update local state
        if (tx) tx.notes = notes;
    } else {
        showToast(data.error || 'Failed to update note', 'error');
    }
}

// ── Actions ─────────────────────────────────────────────────────────────

async function triggerFetch(walletId) {
    const data = await api(`/api/fetch/${walletId}`, { method: 'POST' });
    showToast(data.message || 'Fetch started', 'success');
    // Reload after a delay
    setTimeout(() => selectWallet(walletId), 5000);
}

async function triggerFetchAll() {
    const data = await api('/api/fetch-all', { method: 'POST' });
    showToast(data.message || 'Full fetch started', 'success');
}

async function logout() {
    await api('/api/logout', { method: 'POST' });
    state.authenticated = false;
    renderAuthArea();
    selectWallet(state.activeWallet); // Re-render without edit controls
}

// ── Utilities ───────────────────────────────────────────────────────────

function formatNumber(n, decimals = 2) {
    if (n === undefined || n === null) return (0).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    return Number(n).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function formatTokenAmount(amount, symbol) {
    if (amount === undefined || amount === null) return '0.00';
    const n = Number(amount);
    return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function shortenAddr(addr) {
    if (!addr) return '';
    return addr.slice(0, 6) + '...' + addr.slice(-4);
}

function formatSigners(signersStr) {
    if (!signersStr) return '<span class="text-muted">-</span>';
    const signers = signersStr.split(',');

    // Helper to generate signer link
    const makeLink = addr => {
        const name = state.signerAliases[addr] || shortenAddr(addr);
        return `<a href="https://scrollscan.com/address/${addr}" target="_blank" rel="noopener" class="signer-addr tx-hash" style="display:block; margin-bottom:2px;">${name}</a>`;
    };

    // If many signers (more than 4), collapse them
    if (signers.length > 4) {
        return `<div class="signers-list" title="${signers.join('\n')}">
            ${signers.length} signers
        </div>`;
    }

    return signers.map(makeLink).join('');
}

function timeAgo(ts) {
    if (!ts) return 'never';
    const diff = Math.floor(Date.now() / 1000) - ts;
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
}

function showToast(msg, type = '') {
    // Remove existing
    document.querySelectorAll('.toast').forEach(t => t.remove());
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    document.body.appendChild(el);
    requestAnimationFrame(() => el.classList.add('show'));
    setTimeout(() => {
        el.classList.remove('show');
        setTimeout(() => el.remove(), 300);
    }, 3000);
}

// Add Budget Currency Toggle
window.toggleBudgetCurrency = function (curr) {
    if (state.budgetCurrency === curr) return;
    state.budgetCurrency = curr;
    // Re-render from cached state instead of re-fetching
    if (state.budgetComp) {
        renderDashboard(state.budgetComp);
    }
};

// ── Global Overview ──────────────────────────────────────────────────────

async function selectOverview() {
    state.activeWallet = 'overview';
    localStorage.setItem('scrollTreasury_activeWallet', 'overview');

    const selector = document.getElementById('wallet-selector');
    if (selector) selector.value = 'overview';

    const titleEl = document.getElementById('active-wallet-title');
    if (titleEl) titleEl.textContent = 'DAO Treasury Overview';

    const content = document.getElementById('dashboard-content');
    content.innerHTML = '<div class="loading-spinner"><div class="spinner"></div><p>Loading overview data...</p></div>';

    try {
        const results = await Promise.all(
            state.wallets.map(async w => {
                const [stats, budgetComp] = await Promise.all([
                    api(`/api/stats/${w.id}`),
                    api(`/api/budget-comparison/${w.id}`)
                ]);
                return { wallet: w, stats, budgetComp };
            })
        );
        state.globalData = results;
        renderGlobalView(results);
    } catch (err) {
        content.innerHTML = `<div class="empty-state">
            <p><strong>Failed to load overview data.</strong></p>
            <p>An error occurred while fetching data. Please try again in a moment.</p>
            <button class="btn" onclick="selectOverview()">Retry</button>
        </div>`;
    }
}

function renderGlobalView(allData) {
    const content = document.getElementById('dashboard-content');

    // Total balance across all multisigs
    const totalBalance = allData.reduce((sum, d) => {
        return sum + (d.stats.balances || []).reduce((s, b) => s + (b.balance_usd || 0), 0);
    }, 0);

    // Sub-multisigs only (treasury is the source, not the spender)
    const subMultisigs = allData.filter(d => d.wallet.id !== 'treasury');

    // Fiat totals
    const totalFiatBudget    = subMultisigs.reduce((sum, d) => sum + (d.budgetComp?.totals?.budget_usd || 0), 0);
    const totalFiatSpent     = subMultisigs.reduce((sum, d) => sum + (d.budgetComp?.totals?.spent_usd_native || 0), 0);
    const totalFiatRemaining = Math.max(0, totalFiatBudget - totalFiatSpent);
    const fiatPct = totalFiatBudget > 0 ? Math.min(100, Math.round((totalFiatSpent / totalFiatBudget) * 100)) : 0;
    const fiatFillClass = fiatPct >= 90 ? 'over' : fiatPct >= 70 ? 'warning' : 'under';

    // SCR totals
    const totalScrBudget    = subMultisigs.reduce((sum, d) => sum + (d.budgetComp?.totals?.budget_scr || 0), 0);
    const totalScrSpent     = subMultisigs.reduce((sum, d) => sum + (d.budgetComp?.totals?.spent_scr || 0), 0);
    const totalScrRemaining = Math.max(0, totalScrBudget - totalScrSpent);
    const scrPct = totalScrBudget > 0 ? Math.min(100, Math.round((totalScrSpent / totalScrBudget) * 100)) : 0;
    const scrFillClass = scrPct >= 90 ? 'over' : scrPct >= 70 ? 'warning' : 'under';

    // Treasury swap context
    const treasury    = allData.find(d => d.wallet.id === 'treasury');
    const scrSwapped  = treasury?.budgetComp?.totals?.treasury_scr_swapped || 0;
    const usdtObtained = treasury?.budgetComp?.totals?.treasury_usdt_available_from_swap || 0;

    content.innerHTML = `
        <div class="docs-link-bar"><a href="https://docs.scrolldaotreasury.com/" target="_blank" rel="noopener noreferrer" class="docs-link">Learn More About this Site →</a></div>
        <div class="stats-grid" style="margin-bottom:24px;">
            <div class="stat-card">
                <div class="stat-label">Total DAO Treasury</div>
                <div class="stat-value">$${formatNumber(totalBalance)}</div>
                <div class="stat-sub">Across all multisigs</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Fiat Budget</div>
                <div class="stat-value" style="font-size:20px;">$${formatNumber(totalFiatBudget)}</div>
                <div class="budget-bar" style="margin:8px 0 5px;">
                    <div class="budget-fill ${fiatFillClass}" style="width:${fiatPct}%"></div>
                </div>
                <div class="overview-budget-detail">
                    <span style="color:var(--text-secondary);">Spent <strong style="color:var(--accent-red);">$${formatNumber(totalFiatSpent)}</strong></span>
                    <span class="overview-pct ${fiatFillClass}">${fiatPct}%</span>
                </div>
                <div class="overview-budget-detail" style="margin-top:4px;">
                    <span style="color:var(--text-secondary);">Remaining</span>
                    <strong style="color:var(--accent-green);">$${formatNumber(totalFiatRemaining)}</strong>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-label">SCR Budget</div>
                <div class="stat-value" style="font-size:20px;">${formatNumber(totalScrBudget)} SCR</div>
                <div class="budget-bar" style="margin:8px 0 5px;">
                    <div class="budget-fill ${scrFillClass}" style="width:${scrPct}%"></div>
                </div>
                <div class="overview-budget-detail">
                    <span style="color:var(--text-secondary);">Spent <strong style="color:var(--accent-red);">${formatNumber(totalScrSpent)} SCR</strong></span>
                    <span class="overview-pct ${scrFillClass}">${scrPct}%</span>
                </div>
                <div class="overview-budget-detail" style="margin-top:4px;">
                    <span style="color:var(--text-secondary);">Remaining</span>
                    <strong style="color:var(--accent-green);">${formatNumber(totalScrRemaining)} SCR</strong>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Treasury SCR → USDT Swaps</div>
                <div class="stat-value" style="font-size:18px;">${formatNumber(scrSwapped)} SCR</div>
                <div class="stat-sub">→ $${formatNumber(usdtObtained)} USDT obtained</div>
            </div>
        </div>
        <div class="overview-grid">
            ${allData.map(d => renderMultisigCard(d)).join('')}
        </div>
    `;
}

function renderMultisigCard(data) {
    const { wallet, stats, budgetComp } = data;
    const balances = stats.balances || [];
    const totalUsd = balances.reduce((s, b) => s + (b.balance_usd || 0), 0);
    const addrShort = wallet.address ? `${wallet.address.slice(0, 6)}...${wallet.address.slice(-4)}` : 'Not deployed';

    const tokenSummary = balances
        .sort((a, b) => (b.balance_usd || 0) - (a.balance_usd || 0))
        .slice(0, 3)
        .map(b => `${formatTokenAmount(b.balance_decimal, b.token_symbol)} ${b.token_symbol}`)
        .join(' · ');

    let activitySection = '';

    if (wallet.id === 'treasury') {
        const fiatBudget      = budgetComp?.totals?.budget_usd || 0;
        const fiatTransferred = budgetComp?.totals?.treasury_transferred_usd || 0;
        const fiatRemaining   = Math.max(0, fiatBudget - fiatTransferred);
        const scrBudget       = budgetComp?.totals?.budget_scr || 0;
        const scrSwapped      = budgetComp?.totals?.treasury_scr_swapped || 0;
        const usdtObtained    = budgetComp?.totals?.treasury_usdt_available_from_swap || 0;
        const usdtTransferred = budgetComp?.totals?.treasury_spent_scr_initiatives_usd || 0;
        const scrRemaining    = Math.max(0, scrBudget - scrSwapped);

        activitySection = `
            <div class="multisig-card-section">
                <div class="multisig-card-section-title">Fiat Budget (USDT)</div>
                <div class="overview-metric">
                    <span class="overview-metric-label">Budget</span>
                    <span class="overview-metric-value">$${formatNumber(fiatBudget)}</span>
                </div>
                <div class="overview-metric">
                    <span class="overview-metric-label">Transferred to Multisigs</span>
                    <span class="overview-metric-value negative">$${formatNumber(fiatTransferred)}</span>
                </div>
                <div class="overview-metric">
                    <span class="overview-metric-label">Remaining</span>
                    <span class="overview-metric-value positive">$${formatNumber(fiatRemaining)}</span>
                </div>
            </div>
            <div class="multisig-card-section">
                <div class="multisig-card-section-title">SCR Budget</div>
                <div class="overview-metric">
                    <span class="overview-metric-label">Budget</span>
                    <span class="overview-metric-value">${formatNumber(scrBudget)} SCR</span>
                </div>
                <div class="overview-metric">
                    <span class="overview-metric-label">Swapped to USDT</span>
                    <span class="overview-metric-value negative">${formatNumber(scrSwapped)} SCR</span>
                </div>
                <div class="overview-metric" style="padding-left:10px;">
                    <span class="overview-metric-label" style="font-size:11px;">↳ USDT Obtained</span>
                    <span class="overview-metric-value positive" style="font-size:12px;">$${formatNumber(usdtObtained)}</span>
                </div>
                <div class="overview-metric" style="padding-left:10px;">
                    <span class="overview-metric-label" style="font-size:11px;">↳ Transferred to Multisigs</span>
                    <span class="overview-metric-value negative" style="font-size:12px;">$${formatNumber(usdtTransferred)}</span>
                </div>
                <div class="overview-metric">
                    <span class="overview-metric-label">SCR Remaining</span>
                    <span class="overview-metric-value positive">${formatNumber(scrRemaining)} SCR</span>
                </div>
            </div>`;
    } else {
        const fiatBudget    = budgetComp?.totals?.budget_usd || 0;
        const fiatSpent     = budgetComp?.totals?.spent_usd_native || 0;
        const fiatRemaining = Math.max(0, fiatBudget - fiatSpent);
        const fiatPct = fiatBudget > 0 ? Math.min(100, Math.round((fiatSpent / fiatBudget) * 100)) : 0;
        const fiatFillClass = fiatPct >= 90 ? 'over' : fiatPct >= 70 ? 'warning' : 'under';

        const scrBudget    = budgetComp?.totals?.budget_scr || 0;
        const scrSpent     = budgetComp?.totals?.spent_scr || 0;
        const scrRemaining = Math.max(0, scrBudget - scrSpent);
        const scrSpentUsd  = budgetComp?.totals?.spent_scr_usd_native || 0;
        const scrPct = scrBudget > 0 ? Math.min(100, Math.round((scrSpent / scrBudget) * 100)) : 0;
        const scrFillClass = scrPct >= 90 ? 'over' : scrPct >= 70 ? 'warning' : 'under';

        if (fiatBudget > 0) {
            activitySection += `
                <div class="multisig-card-section">
                    <div class="multisig-card-section-title">Fiat Budget (USDT)</div>
                    <div class="overview-metric">
                        <span class="overview-metric-label">Budget</span>
                        <span class="overview-metric-value">$${formatNumber(fiatBudget)}</span>
                    </div>
                    <div class="budget-bar" style="margin:8px 0 5px;">
                        <div class="budget-fill ${fiatFillClass}" style="width:${fiatPct}%"></div>
                    </div>
                    <div class="overview-budget-detail">
                        <span style="color:var(--text-secondary);">Spent <strong style="color:var(--accent-red);">$${formatNumber(fiatSpent)}</strong></span>
                        <span class="overview-pct ${fiatFillClass}">${fiatPct}%</span>
                    </div>
                    <div class="overview-budget-detail" style="margin-top:4px;">
                        <span style="color:var(--text-secondary);">Remaining</span>
                        <strong style="color:var(--accent-green);">$${formatNumber(fiatRemaining)}</strong>
                    </div>
                </div>`;
        }

        if (scrBudget > 0) {
            activitySection += `
                <div class="multisig-card-section">
                    <div class="multisig-card-section-title">SCR Budget</div>
                    <div class="overview-metric">
                        <span class="overview-metric-label">Budget</span>
                        <span class="overview-metric-value">${formatNumber(scrBudget)} SCR</span>
                    </div>
                    <div class="budget-bar" style="margin:8px 0 5px;">
                        <div class="budget-fill ${scrFillClass}" style="width:${scrPct}%"></div>
                    </div>
                    <div class="overview-budget-detail">
                        <span style="color:var(--text-secondary);">Spent <strong style="color:var(--accent-red);">${formatNumber(scrSpent)} SCR</strong>${scrSpentUsd > 0 ? ` <span style="font-size:11px;color:var(--text-muted)">(~$${formatNumber(scrSpentUsd)})</span>` : ''}</span>
                        <span class="overview-pct ${scrFillClass}">${scrPct}%</span>
                    </div>
                    <div class="overview-budget-detail" style="margin-top:4px;">
                        <span style="color:var(--text-secondary);">Remaining</span>
                        <strong style="color:var(--accent-green);">${formatNumber(scrRemaining)} SCR</strong>
                    </div>
                </div>`;
        }

        if (!fiatBudget && !scrBudget) {
            activitySection = `
                <div class="multisig-card-section">
                    <span style="color:var(--text-muted); font-size:12px;">No budget configured</span>
                </div>`;
        }
    }

    return `
        <div class="multisig-card" onclick="selectWallet('${wallet.id}')">
            <div class="multisig-card-header">
                <div class="multisig-card-name">${wallet.name}</div>
                <div class="multisig-card-addr">${addrShort}</div>
            </div>
            <div class="multisig-card-section">
                <div class="multisig-card-section-title">Current Balance</div>
                <div class="multisig-card-balance">$${formatNumber(totalUsd)}</div>
                ${tokenSummary ? `<div class="multisig-card-tokens">${tokenSummary}</div>` : ''}
            </div>
            ${activitySection}
            <div class="multisig-card-footer">View details →</div>
        </div>`;
}

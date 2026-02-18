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
    filters: { direction: '', category: '', token: '', date_from: '', date_to: '', search: '' },
    burnCurrency: 'USD',
    budgetCurrency: 'USD',
    prices: { ETH: 0, SCR: 0 },
    budgetComp: null,
};

const CATEGORY_COLOURS = [
    '#ffeeda', '#34d399', '#60a5fa', '#a78bfa', '#fb923c',
    '#f87171', '#f5c842', '#818cf8', '#22d3ee', '#e879f9',
    '#84cc16', '#14b8a6',
];

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
        const validSaved = savedWallet && state.wallets.some(w => w.id === savedWallet);
        const defaultWallet = validSaved
            ? savedWallet
            : (state.wallets.find(w => w.address) || state.wallets[0]).id;
        selectWallet(defaultWallet);
    }
});

// ── API Helpers ─────────────────────────────────────────────────────────

async function api(path, options = {}) {
    const resp = await fetch(API + path, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
    });
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
}

async function loadBudgetComparison(walletId) {
    const data = await api(`/api/budget-comparison/${walletId}`);
    if (data.prices) state.prices = data.prices;
    state.budgetComp = data;
    return data;
}

// ── Wallet Selection ────────────────────────────────────────────────────

async function selectWallet(walletId) {
    state.activeWallet = walletId;
    state.transactions.offset = 0;
    state.filters = { direction: '', category: '', token: '', date_from: '', date_to: '', search: '' };

    // Save to localStorage
    localStorage.setItem('scrollTreasury_activeWallet', walletId);

    // Update dropdown selection
    const selector = document.getElementById('wallet-selector');
    if (selector) selector.value = walletId;

    // Update Header Title
    const wallet = state.wallets.find(w => w.id === walletId);
    const titleEl = document.getElementById('active-wallet-title');
    if (wallet && titleEl) titleEl.textContent = wallet.name;

    // Show loader
    const content = document.getElementById('dashboard-content');
    content.innerHTML = '<div class="loading-spinner"><div class="spinner"></div><p>Loading treasury data...</p></div>';

    // Load data
    await Promise.all([loadStats(walletId), loadTransactions(walletId)]);
    const budgetComp = await loadBudgetComparison(walletId);

    renderDashboard(budgetComp);
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
    }).join('');

    container.innerHTML = `
        <div class="wallet-selector-wrapper">
            <select id="wallet-selector" class="wallet-selector" onchange="selectWallet(this.value)">
                ${options}
            </select>
            <svg class="selector-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="6 9 12 15 18 9"></polyline>
            </svg>
        </div>
    `;
}

function renderDashboard(budgetComp) {
    const wallet = state.wallets.find(w => w.id === state.activeWallet);
    const s = state.stats;
    const content = document.getElementById('dashboard-content');

    if (!wallet.address) {
        content.innerHTML = `<div class="empty-state">
            <p><strong>This multisig has not been deployed yet.</strong></p>
            <p>Once the wallet address is configured, transactions and balances will appear here automatically.</p>
        </div>`;
        return;
    }

    content.innerHTML = `
        <!-- Stats Cards -->
        <div class="stats-grid">
            ${renderStatCard('Total Transactions', s.tx_counts.total, '', '')}
            ${renderStatCard('Incoming', s.tx_counts.incoming, 'positive', '')}
            ${renderStatCard('Outgoing', s.tx_counts.outgoing, 'negative', '')}
            ${renderStatCard('Uncategorised', s.tx_counts.uncategorised, s.tx_counts.uncategorised > 0 ? 'negative' : 'positive', '')}
        </div>

        <!-- Balances -->
        <div class="section">
            <div class="section-header">
                <h2 class="section-title">Balances</h2>
            </div>
            <div class="balance-grid" id="balance-grid">
                ${renderBalances(s.balances)}
            </div>
        </div>

        <!-- Two-column: Spending by Category + Monthly Burn -->
        <div class="two-col">
            <div class="card">
                <div class="card-body">
                    <h3 class="section-title" style="margin-bottom:14px;">Spending by Category</h3>
                    ${renderCategoryBreakdown(s.spending_by_category)}
                </div>
            </div>
            <div class="card">
                <div class="card-body card-body-centered">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
                        <h3 class="section-title" style="margin-bottom:0;">Monthly Burn Rate</h3>
                        <div class="currency-toggle">
                            <button class="currency-btn ${state.burnCurrency === 'USD' ? 'active' : ''}" onclick="toggleBurnCurrency('USD')">USD</button>
                            <button class="currency-btn ${state.burnCurrency === 'SCR' ? 'active' : ''}" onclick="toggleBurnCurrency('SCR')">SCR</button>
                        </div>
                    </div>
                    ${renderMonthlyChart(s.monthly_burn)}
                    <div class="stat-sub" style="text-align:center; margin-top:8px;">* Calculated at token prices when transactions were performed</div>
                </div>
            </div>
        </div>

        <!-- Budget Comparison -->
        <div class="section">
            <div class="section-header">
                <h2 class="section-title">Budget vs Spent</h2>
                <div class="currency-toggle">
                    <button class="currency-btn ${state.budgetCurrency === 'USD' ? 'active' : ''}" onclick="toggleBudgetCurrency('USD')">USD</button>
                    <button class="currency-btn ${state.budgetCurrency === 'SCR' ? 'active' : ''}" onclick="toggleBudgetCurrency('SCR')">SCR</button>
                </div>
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
        <div class="stat-value ${cls}">${formatNumber(value)}</div>
        ${sub ? `<div class="stat-sub">${sub}</div>` : ''}
    </div>`;
}

// ── Balances ────────────────────────────────────────────────────────────

// ── Balances ────────────────────────────────────────────────────────────

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

        return `<li class="category-item">
                <span class="category-dot" style="background:${CATEGORY_COLOURS[i % CATEGORY_COLOURS.length]}"></span>
                <div class="category-info">
                    <div class="category-name">${cat}</div>
                    <div class="category-count">${data.count} transactions</div>
                </div>
                <div class="category-amount">
                    <div>${tokenStr}</div>
                    <div class="amount-usd">$${formatNumber(catTotalUsd)}</div>
                </div>
            </li>`;
    }).join('')}
    </ul>`;
}

function renderMonthlyChart(monthly) {
    if (!monthly || monthly.length === 0) {
        return '<div class="empty-state"><p>No monthly data yet</p></div>';
    }
    // Aggregate by month (sum all tokens)
    const byMonth = {};
    monthly.forEach(m => {
        const val = state.burnCurrency === 'SCR' ? (m.total_scr || 0) : (m.total_usd || 0);
        byMonth[m.month] = (byMonth[m.month] || 0) + val;
    });
    const entries = Object.entries(byMonth).sort();
    const max = Math.max(...entries.map(e => e[1]), 1);

    return `<div class="chart-bars">
        ${entries.map(([month, total]) => {
        const pct = (total / max) * 100;
        const label = month.split('-')[1] + '/' + month.split('-')[0].slice(2);
        const valStr = state.burnCurrency === 'SCR' ? `${formatNumber(total)} SCR` : `$${formatNumber(total)}`;
        return `<div class="chart-bar-wrap">
                <div class="chart-value">${valStr}</div>
                <div class="chart-bar" style="height:${Math.max(pct, 3)}%; background: linear-gradient(180deg, #FFDCB1 0%, rgba(255, 220, 177, 0.3) 100%);"></div>
                <div class="chart-label">${label}</div>
            </div>`;
    }).join('')}
    </div>`;
}

function toggleBurnCurrency(curr) {
    if (state.burnCurrency === curr) return;
    state.burnCurrency = curr;
    // Re-render from cached state instead of re-fetching
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
    const totalPct = totals.budget_semester > 0 ? (totals.spent / totals.budget_semester * 100) : 0;

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


    let html = `
    <div class="budget-summary">
        <div class="budget-summary-item">
            <div class="budget-summary-label">Quarterly Budget</div>
            <div class="budget-summary-value" style="color:var(--accent-scroll)">
                ${state.budgetCurrency === 'SCR' ? `${formatNumber(totals.budget_quarterly / (state.prices.SCR || 1))} SCR` : `$${formatNumber(totals.budget_quarterly)}`}
            </div>
        </div>
        <div class="budget-summary-item">
            <div class="budget-summary-label">Semester Budget</div>
            <div class="budget-summary-value" style="color:var(--accent-scroll)">
                ${state.budgetCurrency === 'SCR' ? `${formatNumber(totals.budget_semester / (state.prices.SCR || 1))} SCR` : `$${formatNumber(totals.budget_semester)}`}
            </div>
        </div>
        <div class="budget-summary-item">
            <div class="budget-summary-label">Total Spent</div>
            <div class="budget-summary-value" style="color:${totalPct > 90 ? 'var(--accent-red)' : 'var(--accent-green)'}">
                ${state.budgetCurrency === 'SCR' ? `${formatNumber(totals.spent_scr || 0)} SCR` : `$${formatNumber(totals.spent)}`}
            </div>
        </div>
    </div>
    <div class="card"><div class="card-body">`;

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
                        limit: c.budget_semester,
                        spent: 0,
                        name: c.shared_id === 'ecosystem_pool' ? 'Ecosystem Shared Pool' : (c.shared_id === 'community_pool' ? 'Community Shared Pool' : 'Shared Pool')
                    };
                }
                sharedGroups[c.shared_id].items.push(c);
                sharedGroups[c.shared_id].spent += c.spent_usd;
                if (!sharedGroups[c.shared_id].spent_scr) sharedGroups[c.shared_id].spent_scr = 0;
                sharedGroups[c.shared_id].spent_scr += (c.spent_scr || 0);
            } else {
                individualItems.push(c);
            }
        });

        // Render Shared Pools
        Object.values(sharedGroups).forEach(pool => {
            const pct = pool.limit > 0 ? Math.min((pool.spent / pool.limit) * 100, 150) : 0;
            const fillClass = pct > 100 ? 'over' : pct > 75 ? 'warning' : 'under';

            html += `
            <div class="budget-item shared-pool">
                <div class="budget-label">
                    <span class="budget-name" style="font-weight:700">${pool.name}</span>
                    <span class="budget-amounts">${state.budgetCurrency === 'SCR' ?
                    `${formatNumber(pool.spent_scr)} SCR / ${formatNumber(pool.limit / (state.prices.SCR || 1))} SCR semester` :
                    `$${formatNumber(pool.spent)} / $${formatNumber(pool.limit)} semester`}</span>
                </div>
                <div class="budget-bar">
                    <div class="budget-fill ${fillClass}" style="width:${Math.min(pct, 100)}%"></div>
                </div>
                <!-- Breakdown -->
                <div class="shared-breakdown" style="padding-left:16px; margin-top:8px; display:flex; flex-direction:column; gap:4px; font-size:13px; color:var(--text-muted)">
                    ${pool.items.map(i => `
                        <div style="display:flex; justify-content:space-between">
                            <span>${i.category}</span>
                            <span>${state.budgetCurrency === 'SCR' ? `${formatNumber(i.spent_scr)} SCR` : `$${formatNumber(i.spent_usd)}`}</span>
                        </div>
                    `).join('')}
                </div>
            </div>`;
        });

        // Render Individual Items
        individualItems.forEach(c => {
            const pct = c.budget_semester > 0 ? Math.min((c.spent_usd / c.budget_semester) * 100, 150) : 0;
            const fillClass = pct > 100 ? 'over' : pct > 75 ? 'warning' : 'under';
            html += `
            <div class="budget-item">
                <div class="budget-label">
                    <span class="budget-name">${c.category}</span>
                    <span class="budget-amounts">${state.budgetCurrency === 'SCR' ?
                    `${formatNumber(c.spent_scr || 0)} SCR / ${formatNumber(c.budget_semester / (state.prices.SCR || 1))} SCR semester` :
                    `$${formatNumber(c.spent_usd)} / $${formatNumber(c.budget_semester)} semester`}</span>
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
    const wallet = state.wallets.find(w => w.id === state.activeWallet);
    const cats = wallet ? wallet.categories : [];
    const tokens = state.stats ? state.stats.tokens : [];

    return `<div class="tx-controls">
        <select onchange="applyFilter('direction', this.value)">
            <option value="">All Directions</option>
            <option value="in" ${state.filters.direction === 'in' ? 'selected' : ''}>Incoming</option>
            <option value="out" ${state.filters.direction === 'out' ? 'selected' : ''}>Outgoing</option>
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

    const wallet = state.wallets.find(w => w.id === state.activeWallet);
    const cats = wallet ? wallet.categories : [];

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

        const signersHtml = formatSigners(tx.signers);

        return `<tr>
            <td>${escapeHtml(tx.date)}</td>
            <td><a class="tx-hash" href="https://scrollscan.com/tx/${escapeHtml(tx.tx_hash)}" target="_blank" rel="noopener">${escapeHtml(tx.tx_hash.slice(0, 10))}...</a></td>
            <td><span class="tx-addr">${shortenAddr(tx.from_address)}</span></td>
            <td><span class="tx-addr">${shortenAddr(tx.to_address)}</span></td>
            <td><span class="tx-amount ${tx.direction}">${tx.direction === 'in' ? '+' : '-'}${formatTokenAmount(tx.value_decimal, tx.token_symbol)} ${escapeHtml(tx.token_symbol)}</span></td>
            <td>${escapeHtml(tx.tx_type)}</td>
            <td>${categoryCell}</td>
            <td>${signersHtml}</td>
            <td>${notesCell}</td>
        </tr>`;
    }).join('');

    return `<table class="tx-table">
        <thead><tr>
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

function formatNumber(n) {
    if (n === undefined || n === null) return '0';
    return Number(n).toLocaleString('en-US');
}

function formatTokenAmount(amount, symbol) {
    if (amount === undefined || amount === null) return '0';
    const n = Number(amount);
    if (n === 0) return '0';
    if (Math.abs(n) < 0.001) return n.toFixed(8);
    if (Math.abs(n) < 1) return n.toFixed(6);
    if (Math.abs(n) < 1000) return n.toFixed(4);
    return n.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function shortenAddr(addr) {
    if (!addr) return '';
    return addr.slice(0, 6) + '...' + addr.slice(-4);
}

function formatSigners(signersStr) {
    if (!signersStr) return '<span class="text-muted">-</span>';
    const signers = signersStr.split(',');

    // Helper to generate signer link
    const makeLink = addr => `<a href="https://scrollscan.com/address/${addr}" target="_blank" rel="noopener" class="signer-addr tx-hash" style="display:block; margin-bottom:2px;">${shortenAddr(addr)}</a>`;

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

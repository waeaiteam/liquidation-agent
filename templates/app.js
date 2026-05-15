// ============== Liquidation Agent — Frontend ==============
const $ = id => document.getElementById(id);
const qs = (sel, root = document) => root.querySelector(sel);
const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  providers: [],
  pk: '',
  llmKey: '',
  lastStatus: null,
  lastOrders: [],
  lastEvents: [],
  chatHistory: [],
  charts: {},
  keysReady: false,
  xConfigured: false,
  xaiConfigured: false,
  xPosterConfigured: false,
  xLastData: null,
  exchangeCredentials: { binance: {}, okx: {}, bybit: {} },
  grok: { apiKey: '', model: 'grok-4.3', baseUrl: 'https://api.x.ai/v1', systemPrompt: '' },
  lastDiagnostics: null,
  lastReplay: null,
  activeHeatmapSnapshot: null,
};

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value == null || value === '' ? '-' : String(value);
}

function setClass(id, cls) {
  const el = $(id);
  if (el) el.className = cls || '';
}

function fullSymbol(symbol) {
  return String(symbol || '').replace('/', '').toUpperCase();
}

function slashSymbol(symbol) {
  const s = fullSymbol(symbol);
  return s.endsWith('USDT') ? s.replace('USDT', '/USDT') : (symbol || '-');
}

function normalizeUsdtSymbol(value) {
  let raw = String(value || '').trim().toUpperCase().replace(/[\s_-]/g, '').replace('/', '');
  if (!raw) return 'BTCUSDT';
  if (!raw.endsWith('USDT')) raw += 'USDT';
  return raw;
}

function signedPct(value) {
  if (value == null || Number.isNaN(+value)) return '-';
  const n = +value;
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
}

function orderSide(order) {
  return String(order?.side || '').toUpperCase();
}

function orderPrice(order, key, fallbackKey) {
  const v = order?.[key] ?? order?.[fallbackKey];
  return v == null || v === '' ? null : +v;
}

function orderQty(order) {
  return order?.qty ?? order?.quantity ?? order?.size ?? null;
}

function heatmapSnapshotMatches(snapshot, symbol = selectedHeatmapSymbol(), interval = selectedHeatmapInterval()) {
  if (!snapshot) return false;
  return fullSymbol(snapshot.symbol || '') === fullSymbol(symbol) && String(snapshot.interval || '').toLowerCase() === String(interval || '').toLowerCase();
}

function latestHeatmapSnapshot() {
  const symbol = selectedHeatmapSymbol();
  const interval = selectedHeatmapInterval();
  if (heatmapSnapshotMatches(state.activeHeatmapSnapshot, symbol, interval)) return state.activeHeatmapSnapshot;
  const snapshots = state.lastStatus?.heatmap?.snapshots || [];
  return snapshots.find(snap => heatmapSnapshotMatches(snap, symbol, interval)) || {};
}

const CHAT_WELCOME = '你好！我是清算反向策略 Agent。我可以帮你分析当前市场状况、解读清算地图数据、调整策略参数，或者回答任何关于交易策略的问题。';

// ============== UTILITIES ==============
const fmt = {
  num(v, d = 2) {
    if (v == null || Number.isNaN(+v)) return '—';
    const n = +v;
    if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(d) + 'B';
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(d) + 'M';
    if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(d) + 'K';
    return n.toFixed(d);
  },
  compact(v, d = 2) {
    return this.num(v, d);
  },
  pct(v, d = 2) { return v == null ? '—' : (+v).toFixed(d) + '%'; },
  price(v, d = 2) { return v == null ? '—' : (+v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }); },
  ts(s) { if (!s) return '—'; try { return new Date(s).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }); } catch { return s; } },
};

// ============== KEY CHECK ==============
function checkKeys() {
  const pk = ($('pk')?.value || '').trim();
  const llm = ($('llmKey')?.value || '').trim();
  state.pk = pk;
  state.llmKey = llm;
  state.keysReady = !!pk || new URLSearchParams(location.search).get('debug_ready') === '1';
  updateEmptyStates();
  return state.keysReady;
}

function updateEmptyStates() {
  const pages = ['home', 'market', 'heatmap', 'strategy', 'orders', 'events', 'analyze', 'chat', 'evolution'];
  pages.forEach(page => {
    const section = $('page-' + page);
    if (!section) return;
    let overlay = qs('.empty-state-overlay', section);
    if (!state.keysReady) {
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'empty-state-overlay';
        overlay.innerHTML = `
          <div class="empty-state-box">
            <div class="empty-state-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0110 0v4"/><circle cx="12" cy="16" r="1"/></svg>
            </div>
            <div class="empty-state-title">需要配置 API Key</div>
            <div class="empty-state-desc">请先在设置页面配置钱包私钥和 LLM API Key，才能使用此功能</div>
            <button class="empty-state-btn" onclick="navTo('settings')">前往设置</button>
          </div>`;
        section.appendChild(overlay);
      }
      overlay.style.display = '';
    } else {
      if (overlay) overlay.style.display = 'none';
    }
  });

  // X 热度 page: based on xAI data service configured
  const xSection = $('page-xtrend');
  if (xSection) {
    let xOverlay = qs('.empty-state-overlay', xSection);
    if (!state.xConfigured) {
      if (!xOverlay) {
        xOverlay = document.createElement('div');
        xOverlay.className = 'empty-state-overlay';
        xOverlay.innerHTML = `
          <div class="empty-state-box">
            <div class="empty-state-icon" style="background:var(--purple-soft)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M18 2h3l-7.5 8.57L22 22h-6.63l-5.2-6.79L4.2 22H1.2l8.03-9.17L1 2h6.8l4.7 6.21z"/></svg>
            </div>
            <div class="empty-state-title">需要配置 xAI API</div>
            <div class="empty-state-desc">使用 Grok Live Search 按量付费获取 X 数据<br>请在设置页面填入 xAI API Key 并点击「连接」</div>
            <button class="empty-state-btn" onclick="navTo('settings')">前往设置</button>
          </div>`;
        xSection.appendChild(xOverlay);
      }
      xOverlay.style.display = '';
    } else {
      if (xOverlay) xOverlay.style.display = 'none';
    }
  }

  // Grok page: separate empty state based on xAI chat configured
  const grokSection = $('page-grok');
  if (grokSection) {
    let grokOverlay = qs('.empty-state-overlay', grokSection);
    if (!state.xaiConfigured) {
      if (!grokOverlay) {
        grokOverlay = document.createElement('div');
        grokOverlay.className = 'empty-state-overlay';
        grokOverlay.innerHTML = `
          <div class="empty-state-box">
            <div class="empty-state-icon" style="background:var(--purple-soft)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><circle cx="9" cy="10" r="1" fill="currentColor"/><circle cx="15" cy="10" r="1" fill="currentColor"/><path d="M8 15s1.5 2 4 2 4-2 4-2"/></svg>
            </div>
            <div class="empty-state-title">需要配置 xAI API</div>
            <div class="empty-state-desc">Grok 分析师使用 xAI API 对话，原生支持 X Live Search<br>请在设置页面填入 API Key</div>
            <button class="empty-state-btn" onclick="navTo('settings')">前往设置</button>
          </div>`;
        grokSection.appendChild(grokOverlay);
      }
      grokOverlay.style.display = '';
    } else {
      if (grokOverlay) grokOverlay.style.display = 'none';
    }
  }
}

const toast = (msg, kind = 'info', ms = 2800) => {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $('toasts').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateX(20px)'; }, ms - 250);
  setTimeout(() => el.remove(), ms);
};

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'content-type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let j; try { j = text ? JSON.parse(text) : {}; } catch { j = { raw: text }; }
  if (!res.ok) {
    const err = new Error(j.error || `HTTP ${res.status}`);
    err.code = j.code || '';
    err.status = res.status;
    throw err;
  }
  return j;
}

// ============== NAVIGATION ==============
const pageHooks = {};
function navTo(page) {
  qsa('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.page === page));
  qsa('.page').forEach(el => el.classList.toggle('active', el.id === 'page-' + page));
  if (location.hash !== '#' + page) {
    history.replaceState(null, '', '#' + page);
  }
  setTimeout(() => Object.values(state.charts).forEach(c => c && c.resize && c.resize()), 80);
  if (page === 'settings') renderTrustSettings(state.lastStatus?.config || {});
  const hook = pageHooks[page]; if (hook) hook();
}

// ============== ECHARTS HELPERS ==============
function getChart(id) {
  const el = $(id);
  if (!el) return null;
  if (!state.charts[id]) state.charts[id] = echarts.init(el);
  return state.charts[id];
}
const AXIS = { axisLine: { lineStyle: { color: '#ebedf0' } }, axisLabel: { color: '#9ea2ab', fontSize: 10 }, splitLine: { lineStyle: { color: '#f5f5f5' } } };
const TT = { backgroundColor: '#fff', borderColor: '#ebedf0', textStyle: { color: '#1a1a2e', fontSize: 11 } };

function numericTime(value, fallbackIndex = 0) {
  if (typeof value === 'number' && Number.isFinite(value)) return value < 1e12 ? value * 1000 : value;
  if (typeof value === 'string') {
    const n = +value;
    if (Number.isFinite(n)) return n < 1e12 ? n * 1000 : n;
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return Date.now() + fallbackIndex * 60000;
}

function liqBandColor(side, alpha, valueRatio = 0) {
  const a = Math.max(0.08, Math.min(0.95, alpha));
  if (/short|above/i.test(side || '')) {
    if (valueRatio > 0.72) {
      const shade = Math.round(28 + (1 - valueRatio) * 70);
      return `rgba(${shade},${shade + 12},${shade + 14},${a})`;
    }
    const g = Math.round(150 + 95 * valueRatio);
    return `rgba(${Math.round(10 + 22 * valueRatio)},${g},${Math.round(205 + 45 * valueRatio)},${a})`;
  }
  if (valueRatio > 0.72) {
    const shade = Math.round(70 + (1 - valueRatio) * 95);
    return `rgba(${shade},${Math.round(shade * 0.12)},${Math.round(shade * 0.08)},${a})`;
  }
  const g = Math.round(90 + 135 * valueRatio);
  return `rgba(255,${g},${Math.round(8 + 24 * valueRatio)},${a})`;
}

function liqPriceSide(price, current, fallback) {
  if (current) return price >= current ? 'short' : 'long';
  return fallback || 'unknown';
}

// ============== DESKTOP BRIDGE ==============
async function loadDesktopKeys() {
  if (!window.desktop) return;
  try {
    const keys = await window.desktop.getKeys();
    if (keys?.pk && $('pk')) $('pk').value = keys.pk;
    if (keys?.llm_key && $('llmKey')) $('llmKey').value = keys.llm_key;
    if (keys?.llm_review_key && $('llmReviewKey')) $('llmReviewKey').value = keys.llm_review_key;
    state.exchangeCredentials = normalizeExchangeCredentials(keys);
    syncExchangeSelectors(keys?.exchange || keys?.exchange_default || state.lastStatus?.config?.exchange || 'binance');
    renderExchangeCredentialFields();
    if (keys?.llm_default_provider && $('llmProvider')) $('llmProvider').value = keys.llm_default_provider;
    if (keys?.llm_review_provider && $('llmReviewProvider')) $('llmReviewProvider').value = keys.llm_review_provider;
    const mainProvider = (keys?.llm_providers || []).find(p => p.id === ($('llmProvider')?.value || keys.llm_default_provider));
    const reviewProvider = (keys?.llm_providers || []).find(p => p.id === ($('llmReviewProvider')?.value || keys.llm_review_provider));
    if (mainProvider?.base_url && $('llmBaseUrl')) $('llmBaseUrl').value = mainProvider.base_url;
    if (reviewProvider?.base_url && $('llmReviewBaseUrl')) $('llmReviewBaseUrl').value = reviewProvider.base_url;
    setSelectValue('llmModelSelect', keys?.llm_default_model || mainProvider?.default_model || '');
    setSelectValue('llmReviewModelSelect', keys?.llm_review_model || reviewProvider?.default_model || '');
    checkKeys();
  } catch {}
}

function setSelectValue(id, value) {
  const select = $(id);
  if (!select || !value) return;
  if (![...select.options].some(o => o.value === value)) {
    select.appendChild(new Option(value, value));
  }
  select.disabled = false;
  select.value = value;
  updateChatModelPill();
}

function updateChatModelPill() {
  const pill = $('chatModelPill');
  if (!pill) return;
  const provider = $('llmProvider')?.value || 'anthropic';
  const selected = $('llmModelSelect')?.selectedOptions?.[0];
  const model = $('llmModelSelect')?.value || '';
  const label = selected?.textContent || model || provider;
  pill.textContent = '模型: ' + label;
}

// ============== HEADER ==============
function renderHeader(s) {
  const running = !!s?.running;
  const cfg = s?.config || {};
  const pill = $('hdrRun');
  if (pill) {
    if (running) { pill.className = 'hdr-pill pill-purple'; $('hdrRunText').textContent = '运行中'; }
    else { pill.className = 'hdr-pill pill-green'; $('hdrRunText').textContent = '已停止'; }
  }
  const startBtn = $('btnStart');
  const stopBtn = $('btnStop');
  if (startBtn) startBtn.style.display = running ? 'none' : '';
  if (stopBtn) stopBtn.style.display = running ? '' : 'none';
  if (cfg.symbol && $('hdrSymbol')) $('hdrSymbol').value = slashSymbol(cfg.symbol);
  if (cfg.exchange) syncExchangeSelectors(cfg.exchange, { includeCredential: false });
  if (cfg.interval && $('hdrInterval')) $('hdrInterval').value = String(cfg.interval).replace('h', 'H').replace('d', 'D').replace('w', 'W');
  if (cfg.symbol && $('configSymbol')) $('configSymbol').value = slashSymbol(cfg.symbol);
  if (cfg.exchange && $('configExchange')) $('configExchange').value = normalizeExchange(cfg.exchange);
  renderTrustSettings(cfg);
}

function selectedHeaderSymbol() {
  return normalizeUsdtSymbol($('hdrSymbol')?.value || state.lastStatus?.config?.symbol || 'BTCUSDT');
}

function selectedHeaderExchange() {
  const raw = $('hdrExchange')?.value || state.lastStatus?.config?.exchange || 'binance';
  return normalizeExchange(raw);
}

function selectedHeaderInterval() {
  const raw = $('hdrInterval')?.value || state.lastStatus?.config?.interval || '1m';
  return String(raw).trim().replace(/H$/, 'h').replace(/D$/, 'd').replace(/W$/, 'w');
}

function syncExchangeSelectors(value, { includeCredential = true } = {}) {
  const exchange = normalizeExchange(value);
  if ($('hdrExchange')) $('hdrExchange').value = exchange;
  if ($('configExchange')) $('configExchange').value = exchange;
  if (includeCredential && $('exchangeCredExchange')) $('exchangeCredExchange').value = exchange;
  return exchange;
}

async function persistAgentExchange(exchange) {
  const selected = syncExchangeSelectors(exchange);
  try {
    const symbol = selectedHeaderSymbol();
    const result = await api('POST', '/api/agent/config', {
      coin: symbol.replace(/USDT$/, ''),
      symbol,
      exchange: selected,
      interval: selectedHeaderInterval(),
    });
    if (result?.config) {
      state.lastStatus = { ...(state.lastStatus || {}), config: result.config };
      renderDashKpis(state.lastStatus);
    }
    return result;
  } catch (e) {
    toast('交易所配置保存失败: ' + (e.message || e), 'err');
    return null;
  }
}

function selectedHeatmapSymbol() {
  return normalizeUsdtSymbol($('hmSym')?.value || selectedHeaderSymbol());
}

function selectedHeatmapExchange() {
  return 'aggregate';
}

function selectedHeatmapInterval() {
  const raw = $('hmInt')?.value || '1d';
  const normalized = String(raw).trim().toLowerCase();
  return ['12h', '1d', '3d', '7d', '30d'].includes(normalized) ? normalized : '1d';
}

function normalizeExchange(value) {
  const raw = String(value || 'binance').toLowerCase();
  if (raw.includes('okx')) return 'okx';
  if (raw.includes('bybit')) return 'bybit';
  return 'binance';
}

function normalizeExchangeCredentials(settings = {}) {
  const existing = settings.exchange_credentials || {};
  return {
    binance: {
      api_key: existing.binance?.api_key || settings.binance_key || '',
      secret: existing.binance?.secret || settings.binance_secret || '',
      passphrase: '',
    },
    okx: {
      api_key: existing.okx?.api_key || '',
      secret: existing.okx?.secret || '',
      passphrase: existing.okx?.passphrase || '',
    },
    bybit: {
      api_key: existing.bybit?.api_key || '',
      secret: existing.bybit?.secret || '',
      passphrase: '',
    },
  };
}

function exchangeCredentialName(exchange) {
  if (exchange === 'okx') return 'OKX';
  if (exchange === 'bybit') return 'Bybit';
  return 'Binance';
}

function formatHeatmapError(error) {
  return error?.message || '未知错误';
}

function persistVisibleExchangeCredentials(exchangeOverride) {
  const exchange = normalizeExchange(exchangeOverride || $('exchangeCredExchange')?.dataset?.currentExchange || $('exchangeCredExchange')?.value || 'binance');
  if (!state.exchangeCredentials[exchange]) state.exchangeCredentials[exchange] = {};
  state.exchangeCredentials[exchange] = {
    api_key: ($('exchangeApiKey')?.value || '').trim(),
    secret: ($('exchangeApiSecret')?.value || '').trim(),
    passphrase: exchange === 'okx' ? (($('exchangeApiPassphrase')?.value || '').trim()) : '',
  };
}

function renderExchangeCredentialFields() {
  const exchange = normalizeExchange($('exchangeCredExchange')?.value || $('configExchange')?.value || 'binance');
  const label = exchangeCredentialName(exchange);
  const creds = state.exchangeCredentials[exchange] || {};
  if ($('exchangeCredExchange')) $('exchangeCredExchange').value = exchange;
  if ($('exchangeCredExchange')) $('exchangeCredExchange').dataset.currentExchange = exchange;
  if ($('exchangeApiKeyLabel')) $('exchangeApiKeyLabel').textContent = `${label} API Key`;
  if ($('exchangeApiSecretLabel')) $('exchangeApiSecretLabel').textContent = `${label} Secret Key`;
  if ($('exchangeApiKey')) {
    $('exchangeApiKey').placeholder = `输入 ${label} API Key`;
    $('exchangeApiKey').value = creds.api_key || '';
  }
  if ($('exchangeApiSecret')) {
    $('exchangeApiSecret').placeholder = `输入 ${label} Secret Key`;
    $('exchangeApiSecret').value = creds.secret || '';
  }
  if ($('exchangePassphraseRow')) $('exchangePassphraseRow').style.display = exchange === 'okx' ? '' : 'none';
  if ($('exchangeApiPassphrase')) $('exchangeApiPassphrase').value = exchange === 'okx' ? (creds.passphrase || '') : '';
  if ($('exchangeRestrictionNote')) {
    $('exchangeRestrictionNote').textContent = exchange === 'binance'
      ? '应用不会绕过交易所地区规则；会按用户电脑当前网络环境直连 Binance。若该环境能访问 Binance，API key 可正常使用。'
      : `${label} 凭证会独立保存，不会覆盖 Binance 或其他交易所的 API Key。`;
  }
}

function exchangeLabel(value) {
  const exchange = normalizeExchange(value);
  if (exchange === 'okx') return 'OKX';
  if (exchange === 'bybit') return 'Bybit';
  return 'Binance';
}

function formatMarketError(error, exchange) {
  if (error?.code === 'restricted_location' || error?.status === 451) {
    return `${exchangeLabel(exchange)} 当前网络位置受地区限制，已被交易所拒绝访问；请切换 OKX/Bybit，或在允许访问 Binance 的用户网络环境下重试。`;
  }
  return error?.message || '未知错误';
}

// ============== STATUS POLLING ==============
async function pollStatus() {
  try {
    const j = await api('GET', '/api/agent/status');
    state.lastStatus = j;
    renderHeader(j);
    renderDashKpis(j);
    renderDashSignals(j);
    renderDashCharts();
    pageHooks.heatmap?.();
  } catch {}
}

async function refreshAgentData({ forceHeatmap = false } = {}) {
  if (forceHeatmap) return refreshLiquidationMap();
  const pk = state.pk || ($('pk')?.value || '').trim();
  if (forceHeatmap && !pk) { toast('请先在设置中填写钱包私钥', 'err'); return; }
  const btn = forceHeatmap ? $('btnHmRefresh') : $('btnMarketRefresh');
  const oldText = btn?.textContent;
  if (btn) { btn.disabled = true; btn.textContent = '获取中...'; }
  try {
    if (!forceHeatmap) {
      const result = await api('POST', '/api/market/refresh', {
        symbol: selectedHeaderSymbol(),
        exchange: selectedHeaderExchange(),
        interval: selectedHeaderInterval(),
      });
      state.lastStatus = result.status || await api('GET', '/api/agent/status');
      renderHeader(state.lastStatus);
      renderDashKpis(state.lastStatus);
      renderDashSignals(state.lastStatus);
      renderDashCharts();
      pageHooks.market?.();
      await Promise.all([loadOrders(), loadEvents()]);
      toast('已获取交易所实时行情', 'ok');
      return result;
    }
    const body = {
      pk,
      manual: true,
      force_heatmap: !!forceHeatmap,
      coin: selectedHeatmapSymbol().replace(/USDT$/, ''),
      symbol: selectedHeatmapSymbol(),
      exchange: selectedHeatmapExchange(),
      interval: selectedHeatmapInterval(),
      llm_review_provider: $('llmReviewProvider')?.value || undefined,
      llm_review_api_key: ($('llmReviewKey')?.value || '').trim(),
      llm_review_model: $('llmReviewModelSelect')?.value || undefined,
      llm_review_base_url: ($('llmReviewBaseUrl')?.value || '').trim() || undefined,
    };
    const result = await api('POST', '/api/agent/tick', body);
    state.lastStatus = await api('GET', '/api/agent/status');
    renderHeader(state.lastStatus);
    renderDashKpis(state.lastStatus);
    renderDashSignals(state.lastStatus);
    renderDashCharts();
    pageHooks.market?.();
    pageHooks.heatmap?.();
    await Promise.all([loadOrders(), loadEvents()]);
    if ($('hmLastUpdate')) $('hmLastUpdate').textContent = new Date().toLocaleString();
    if ($('sigUpdated')) $('sigUpdated').textContent = new Date().toLocaleTimeString();
    toast(forceHeatmap ? '已获取最新市场与清算地图数据' : '已获取最新市场数据', 'ok');
    return result;
  } catch (e) {
    toast('获取数据失败: ' + formatMarketError(e, forceHeatmap ? selectedHeatmapExchange() : selectedHeaderExchange()), 'err', 5200);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = oldText; }
  }
}

async function refreshLiquidationMap() {
  const pk = state.pk || ($('pk')?.value || '').trim();
  if (!pk) { toast('请先在设置中填写钱包私钥', 'err'); return; }
  const btn = $('btnHmRefresh');
  const oldText = btn?.textContent;
  if (btn) { btn.disabled = true; btn.textContent = '生成中...'; }
  try {
    const result = await api('POST', '/api/liqmap', {
      pk,
      coin: selectedHeatmapSymbol().replace(/USDT$/, ''),
      symbol: selectedHeatmapSymbol(),
      exchange: selectedHeatmapExchange(),
      interval: selectedHeatmapInterval(),
    });
    if (!heatmapSnapshotMatches(result.snapshot, selectedHeatmapSymbol(), selectedHeatmapInterval())) {
      throw new Error('返回的清算地图与当前币种/周期不一致，已拒绝显示');
    }
    state.activeHeatmapSnapshot = result.snapshot;
    state.lastStatus = result.status || await api('GET', '/api/agent/status');
    renderHeader(state.lastStatus);
    renderDashKpis(state.lastStatus);
    renderDashSignals(state.lastStatus);
    renderDashCharts();
    pageHooks.heatmap?.();
    await loadEvents();
    if ($('hmLastUpdate')) $('hmLastUpdate').textContent = new Date().toLocaleString();
    toast('已生成聚合清算地图', 'ok');
    return result;
  } catch (e) {
    toast('生成聚合清算地图失败: ' + formatHeatmapError(e), 'err', 5200);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = oldText; }
  }
}

// ============== DASHBOARD KPIs ==============
function renderDashKpis(s) {
  const cfg = s?.config || {};
  const orders = state.lastOrders || [];
  const sizeUsd = +cfg.max_notional_usd || 1000;
  const seed = +cfg.paper_seed_usd || 100000;
  const closed = orders.filter(o => (o.status || '').toUpperCase() === 'CLOSED');
  const pnl = closed.reduce((a, o) => a + (+o.pnl_usd || ((+o.pnl_pct || 0) / 100) * sizeUsd), 0);
  const balance = seed + pnl;

  if ($('kpiPhase')) {
    $('kpiPhase').textContent = s?.running ? '运行中' : '已停止';
    $('kpiPhase').style.color = s?.running ? 'var(--green-text)' : 'var(--text-3)';
  }
  if ($('kpiPhaseSub')) $('kpiPhaseSub').textContent = s?.started_at ? `已运行 ${Math.floor((Date.now() - new Date(s.started_at).getTime()) / 60000)}分钟` : '未启动';
  if ($('kpiSymbol')) $('kpiSymbol').textContent = (cfg.coin || 'BTC') + '/USDT';
  if ($('kpiExchange')) $('kpiExchange').textContent = exchangeLabel(cfg.exchange || 'binance') + ' 永续';
  if ($('kpiBalance')) $('kpiBalance').innerHTML = fmt.price(balance) + ' <span class="unit">USDT</span>';
  if ($('kpiBalanceSub')) $('kpiBalanceSub').textContent = `可用 ${fmt.price(Math.max(0, balance - (orders.filter(o => (o.status || '').toUpperCase() === 'OPEN').length * sizeUsd)))} USDT`;

  const today = new Date().toISOString().slice(0, 10);
  const todayPnl = closed.filter(o => String(o.timestamp || '').startsWith(today)).reduce((a, o) => a + ((+o.pnl_pct || 0) / 100) * sizeUsd, 0);
  if ($('kpiToday')) {
    $('kpiToday').innerHTML = (todayPnl >= 0 ? '+' : '') + fmt.price(todayPnl) + ' <span class="unit">USDT</span>';
    $('kpiToday').style.color = todayPnl >= 0 ? 'var(--green-text)' : 'var(--red-text)';
  }
  if ($('kpiTotal')) {
    $('kpiTotal').innerHTML = (pnl >= 0 ? '+' : '') + fmt.price(pnl) + ' <span class="unit">USDT</span>';
    $('kpiTotal').style.color = pnl >= 0 ? 'var(--green-text)' : 'var(--red-text)';
  }
  renderAgentTrustPanel(s);
}

function ensureAgentTrustPanel() {
  let panel = $('agentTrustPanel');
  if (panel) return panel;
  const home = $('page-home');
  if (!home) return null;
  panel = document.createElement('div');
  panel.id = 'agentTrustPanel';
  panel.className = 'card';
  panel.style.margin = '0 0 16px';
  const anchor = qs('.dash-mid', home) || home.children[1] || null;
  home.insertBefore(panel, anchor);
  return panel;
}

function reportLine(report) {
  if (!report) return 'No report yet';
  const score = report.opportunity_score || {};
  const finalAction = report.final_action || {};
  const blockers = (report.blockers || []).slice(0, 3).join(' | ');
  return `${finalAction.action || 'WAIT'} · score ${Math.round(+score.score || 0)}/${score.threshold ?? 70}${blockers ? ' · ' + blockers : ''}`;
}

function heartbeatState(s) {
  const hb = s?.heartbeat || {};
  if (hb.stale) return { text: 'Loop may be stuck', color: 'var(--red-text)' };
  if (s?.running) return { text: 'Running', color: 'var(--green-text)' };
  if (hb.last_error) return { text: 'Error', color: 'var(--red-text)' };
  return { text: 'Stopped', color: 'var(--text-3)' };
}

function renderAgentTrustPanel(s) {
  const panel = ensureAgentTrustPanel();
  if (!panel) return;
  const hb = s?.heartbeat || {};
  const report = s?.last_decision_report || null;
  const score = report?.opportunity_score || {};
  const checks = state.lastDiagnostics?.checks || s?.diagnostics_summary?.checks || [];
  const diagSummary = state.lastDiagnostics?.overall || s?.diagnostics_summary?.overall || 'not_run';
  const recentReports = state.lastReplay?.reports || [];
  const recentDecision = reportLine(report);
  const hbView = heartbeatState(s);
  const nextTick = hb.next_due_at ? fmt.ts(hb.next_due_at) : '-';
  const lastTick = hb.last_tick_at ? fmt.ts(hb.last_tick_at) : '-';
  panel.innerHTML = `
    <div class="card-head">
      <div class="card-title">Agent Trust Panel</div>
      <div style="display:flex;gap:8px;align-items:center">
        <button class="card-action" id="btnAgentDiagnostics">Health Check</button>
        <button class="card-action" id="btnAgentReplay">Replay</button>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:12px">
      <div style="border:1px solid var(--border);border-radius:8px;padding:10px">
        <div style="font-size:11px;color:var(--text-3)">Safety mode</div>
        <div style="font-size:18px;font-weight:700;color:var(--text)">${escapeHtml(s?.safety_mode || s?.config?.safety_mode || 'paper')}</div>
      </div>
      <div style="border:1px solid var(--border);border-radius:8px;padding:10px">
        <div style="font-size:11px;color:var(--text-3)">Heartbeat</div>
        <div style="font-size:18px;font-weight:700;color:${hbView.color}">${hbView.text}</div>
      </div>
      <div style="border:1px solid var(--border);border-radius:8px;padding:10px">
        <div style="font-size:11px;color:var(--text-3)">Ticks</div>
        <div style="font-size:18px;font-weight:700;color:var(--text)">${hb.tick_count ?? s?.tick_count ?? 0}</div>
      </div>
      <div style="border:1px solid var(--border);border-radius:8px;padding:10px">
        <div style="font-size:11px;color:var(--text-3)">Next review</div>
        <div style="font-size:18px;font-weight:700;color:var(--text)">${nextTick}</div>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:2fr 1fr;gap:12px">
      <div style="border:1px solid var(--border);border-radius:8px;padding:10px">
        <div style="font-size:11px;color:var(--text-3);margin-bottom:6px">Last decision · last tick ${lastTick} · errors ${hb.consecutive_errors || 0}</div>
        <div style="font-weight:700;color:var(--text);margin-bottom:6px">${escapeHtml(recentDecision)}</div>
        <div style="font-size:11px;color:var(--text-3)">market ${report?.market_check?.ok === false ? 'blocked' : 'ok'} · heatmap ${report?.heatmap_confirmation?.confirmed ? 'confirmed' : 'watch'} · risk ${report?.risk_gate?.approved ? 'approved' : 'blocked'} · threshold ${score.threshold ?? 70}</div>
      </div>
      <div style="border:1px solid var(--border);border-radius:8px;padding:10px">
        <div style="font-size:11px;color:var(--text-3);margin-bottom:6px">Diagnostics</div>
        <div style="font-weight:700;color:${diagSummary === 'fail' ? 'var(--red-text)' : diagSummary === 'pass' ? 'var(--green-text)' : 'var(--text)'}">${escapeHtml(diagSummary)}</div>
        <div style="font-size:11px;color:var(--text-3);margin-top:4px">${checks.slice(0, 3).map(c => `${c.status}:${c.name}`).join(' · ') || 'Not run'}</div>
      </div>
    </div>
    <div id="agentReplayResult" style="margin-top:10px;font-size:11px;color:var(--text-3)">
      ${recentReports.length ? recentReports.slice(-5).map(r => escapeHtml(reportLine(r))).join('<br>') : 'Replay output will appear here.'}
    </div>`;
  $('btnAgentDiagnostics')?.addEventListener('click', runAgentDiagnostics);
  $('btnAgentReplay')?.addEventListener('click', runAgentReplay);
}

function ensureTrustSettingsPanel() {
  let panel = $('agentTrustSettingsPanel');
  if (panel) return panel;
  const settings = $('page-settings');
  const content = $('settingsContent') || (settings ? qs('.settings-content', settings) : null);
  if (!content) return null;
  panel = document.createElement('div');
  panel.id = 'agentTrustSettingsPanel';
  panel.className = 'card';
  panel.setAttribute('data-settings-section', 'api');
  panel.innerHTML = `
    <div class="card-head">
      <div class="card-title">Agent Safety & Reports</div>
      <button class="card-action" id="btnSettingsDiagnostics">Run Health Check</button>
    </div>
    <div class="trust-settings-grid">
      <label style="display:grid;gap:4px;color:var(--text-2)">Safety mode
        <select id="safetyMode" class="hdr-select" style="width:100%">
          <option value="observe">Observe</option>
          <option value="paper">Paper</option>
          <option value="confirm">Confirm</option>
          <option value="live">Live blocked</option>
        </select>
      </label>
      <label style="display:grid;gap:4px;color:var(--text-2)">Min opportunity score
        <input id="minOpportunityScore" type="number" min="0" max="100" value="70" style="padding:7px 10px;border:1px solid var(--border);border-radius:6px;font-family:var(--mono);background:var(--surface-2)">
      </label>
      <label style="display:grid;gap:4px;color:var(--text-2)">Report retention
        <input id="decisionReportRetention" type="number" min="10" max="1000" value="100" style="padding:7px 10px;border:1px solid var(--border);border-radius:6px;font-family:var(--mono);background:var(--surface-2)">
      </label>
      <label style="display:flex;gap:6px;align-items:center;color:var(--text-2);min-height:52px">
        <input id="saveClawRawSamples" type="checkbox" style="accent-color:var(--purple)"> Save redacted Claw402 raw
      </label>
    </div>
    <div id="settingsDiagnosticsResult" style="margin-top:10px;font-size:11px;color:var(--text-3)">Health check results will appear in the dashboard trust panel.</div>`;
  const firstCard = qs('.card[data-settings-section="api"]', content);
  content.insertBefore(panel, firstCard || content.firstChild);
  $('btnSettingsDiagnostics')?.addEventListener('click', async () => {
    await runAgentDiagnostics();
    const out = $('settingsDiagnosticsResult');
    if (out && state.lastDiagnostics) {
      out.textContent = `${state.lastDiagnostics.overall}: ${(state.lastDiagnostics.checks || []).map(c => `${c.status} ${c.name}`).join(' | ')}`;
    }
  });
  return panel;
}

function renderTrustSettings(cfg = {}) {
  ensureTrustSettingsPanel();
  if ($('safetyMode') && cfg.safety_mode) $('safetyMode').value = cfg.safety_mode;
  if ($('configSafetyMode') && cfg.safety_mode) $('configSafetyMode').value = cfg.safety_mode;
  if ($('minOpportunityScore') && cfg.min_opportunity_score != null) $('minOpportunityScore').value = cfg.min_opportunity_score;
  if ($('decisionReportRetention') && cfg.decision_report_retention != null) $('decisionReportRetention').value = cfg.decision_report_retention;
  if ($('saveClawRawSamples') && cfg.save_claw402_raw_samples != null) $('saveClawRawSamples').checked = !!cfg.save_claw402_raw_samples;
}

async function runAgentDiagnostics() {
  const btn = $('btnAgentDiagnostics');
  if (btn) btn.disabled = true;
  try {
    state.lastDiagnostics = await api('GET', '/api/agent/diagnostics');
    renderAgentTrustPanel(state.lastStatus);
    toast('Agent health check completed', state.lastDiagnostics.overall === 'fail' ? 'err' : 'ok');
  } catch (e) {
    toast('Health check failed: ' + e.message, 'err');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function runAgentReplay() {
  const btn = $('btnAgentReplay');
  const out = $('agentReplayResult');
  if (btn) btn.disabled = true;
  if (out) out.textContent = 'Running replay...';
  try {
    const s = state.lastStatus || {};
    const market = s.last_snapshot?.market || {};
    const payload = {
      klines: market.klines || [],
      snapshots: s.last_snapshot ? [s.last_snapshot] : [],
      config: { safety_mode: 'observe' },
    };
    state.lastReplay = await api('POST', '/api/agent/replay', payload);
    renderAgentTrustPanel(state.lastStatus);
    toast('Replay completed', 'ok');
  } catch (e) {
    if (out) out.textContent = 'Replay failed: ' + e.message;
    toast('Replay failed: ' + e.message, 'err');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function loadBinanceSymbols() {
  const list = $('binanceSymbolsList');
  if (!list) return;
  try {
    const data = await api('GET', '/api/market/binance-symbols');
    const symbols = data.symbols || [];
    state.binanceSymbols = symbols;
    if (symbols.length) {
      list.innerHTML = symbols.map(item => `<option value="${escapeHtml(item.display || slashSymbol(item.symbol))}"></option>`).join('');
    }
  } catch {
    state.binanceSymbols = [];
  }
}

// ============== DASHBOARD SIGNALS ==============
function renderDashSignals(s) {
  const sig = s?.last_signal || {};
  const conf = Math.round((+sig.confidence || +sig.score || 0) * 100);
  const grid = $('signalGrid');
  if (!grid) return;
  const sigCards = qsa('.sig-card', grid);
  if (sigCards.length >= 4) {
    const action = sig.action || 'wait';
    const pcts = [
      action === 'long' ? Math.max(60, conf) : Math.min(35, conf),
      action === 'short' ? Math.max(60, conf) : Math.min(35, conf),
      Math.round((+sig.breakout_score || 0.55) * 100),
      Math.round((+sig.reversal_score || 0.25) * 100),
    ];
    sigCards.forEach((card, i) => {
      const pctEl = qs('.sig-pct', card);
      if (pctEl) pctEl.textContent = pcts[i] + '%';
    });
  }
}

// ============== DASHBOARD CHARTS ==============
function renderDashCharts() {
  if (!state.keysReady) return;
  // Performance chart
  const perfChart = getChart('perfChart');
  if (perfChart) {
    const orders = (state.lastOrders || []).filter(o => (o.status || '').toUpperCase() === 'CLOSED').sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    const sizeUsd = +state.lastStatus?.config?.max_notional_usd || 1000;
    const pnl_is_pct = orders.length && orders[0].pnl_pct != null;
    let cum = 0;
    const data = orders.length ? orders.map(o => { cum += ((+o.pnl_pct || +o.pnl_usd || 0) / (pnl_is_pct ? 100 : 1)) * (pnl_is_pct ? sizeUsd : 1); return [new Date(o.timestamp).getTime(), cum]; }) : [];
    if (data.length) {
      perfChart.setOption({
        grid: { left: 50, right: 14, top: 14, bottom: 28 },
        tooltip: { trigger: 'axis', ...TT },
        xAxis: { type: 'time', ...AXIS },
        yAxis: { type: 'value', ...AXIS },
        graphic: [],
        series: [{ type: 'line', smooth: true, showSymbol: false, data, lineStyle: { color: '#6c5ce7', width: 2 }, areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(108,92,231,.2)' }, { offset: 1, color: 'rgba(108,92,231,0)' }] } } }],
      }, true);
    } else {
      perfChart.setOption({
        grid: { left: 50, right: 14, top: 14, bottom: 28 },
        xAxis: { type: 'time', ...AXIS, show: false },
        yAxis: { type: 'value', ...AXIS, show: false },
        series: [{ type: 'line', data: [] }],
        graphic: [{ type: 'text', left: 'center', top: 'middle', style: { text: '启动 Agent 后显示数据', fontSize: 13, fill: '#9ea2ab', fontFamily: 'inherit' } }],
      }, true);
    }
  }

  // Heatmap mini
  const hmChart = getChart('dashHmChart');
  if (hmChart) {
    renderHeatmapChart(hmChart, 'mini');
  }
}


// ============== HEATMAP PAGE ==============
function renderHeatmapChart(chart, mode) {
  const latest = latestHeatmapSnapshot();
  const normalized = latest.liq_map || {};
  const points = Array.isArray(normalized.points) ? normalized.points : [];
  const levelMap = Array.isArray(normalized.level_map) ? normalized.level_map : [];
  if (!heatmapSnapshotMatches(latest) || (!points.length && !levelMap.length)) {
    chart.setOption({
      grid: { left: 50, right: 10, top: 10, bottom: 30 },
      xAxis: { type: 'category', data: [], show: false },
      yAxis: { type: 'category', data: [], show: false },
      series: [{ type: 'heatmap', data: [] }],
      graphic: [{ type: 'text', left: 'center', top: 'middle', style: { text: '暂无清算地图数据', fontSize: 13, fill: '#9ea2ab', fontFamily: 'inherit' } }],
      backgroundColor: '#fff',
    }, true);
    return;
  }
  if (points.length) {
    renderLiquidationMapCanvas(chart, latest, mode);
    return;
  }
  const prices = [...new Set(points.map(p => +p.price).filter(Number.isFinite))].sort((a, b) => a - b);
  const series = [...new Set(points.map(p => p.series || p.side || 'liq'))];
  const data = points.map(p => {
    const x = prices.indexOf(+p.price);
    const y = series.indexOf(p.series || p.side || 'liq');
    return [x, y, +p.value || 0];
  }).filter(p => p[0] >= 0 && p[1] >= 0);
  const maxVal = data.length ? Math.max(...data.map(d => d[2])) : 100;
  chart.setOption({
    grid: { left: 50, right: 10, top: 10, bottom: 30 },
    xAxis: { type: 'category', data: prices.map(p => p.toLocaleString()), axisLabel: { color: '#aaa', fontSize: 9, interval: Math.max(0, Math.floor(prices.length / 8)) }, axisLine: { lineStyle: { color: '#333' } } },
    yAxis: { type: 'category', data: series, axisLabel: { color: '#aaa', fontSize: 9 }, axisLine: { lineStyle: { color: '#333' } } },
    visualMap: { show: false, min: 0, max: maxVal || 100, inRange: { color: ['#1a1a4e', '#3d3d9e', '#6c5ce7', '#f39c12', '#e74c3c'] } },
    series: [{ type: 'heatmap', data, emphasis: { itemStyle: { borderColor: '#fff', borderWidth: 1 } } }],
    graphic: [],
    backgroundColor: '#1a1a2e',
  }, true);
}

function renderLiquidationLevelsCanvas(chart, snapshot, mode) {
  const dom = chart?.getDom?.();
  if (!dom) return;
  dom.innerHTML = '';
  dom.style.position = 'relative';
  dom.style.background = '#fff';
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(300, dom.clientWidth || 900);
  canvas.height = Math.max(220, dom.clientHeight || 380);
  canvas.style.width = '100%';
  canvas.style.height = '100%';
  canvas.style.display = 'block';
  dom.appendChild(canvas);
  const ctx = canvas.getContext('2d');
  const normalized = snapshot?.liq_map || {};
  const levelMap = Array.isArray(normalized.level_map) ? normalized.level_map : [];
  const priceAxis = (normalized.price_axis || []).map(Number).filter(Number.isFinite).sort((a, b) => a - b);
  const currentPrice = +snapshot?.price || +state.lastStatus?.last_snapshot?.price || +state.lastStatus?.last_signal?.price || 0;
  const maxValue = Math.max(+normalized.max_liq_value || +normalized.max_value || 0, ...levelMap.map(p => +p.value || 0), 1);
  const times = normalized.time_axis || [];
  const xMax = Math.max((times.length || 1) - 1, ...levelMap.map(p => +p.time_index || 0), 1);
  const visualMarket = snapshot?.visual_market || {};
  const market = visualMarket?.klines ? visualMarket : (state.lastStatus?.last_snapshot?.market || {});
  const klines = Array.isArray(market.klines) ? market.klines.slice(-Math.max(220, Math.min(500, xMax + 1))) : [];
  const visibleLevels = levelMap
    .map(p => ({ price: +p.price, value: +p.value || 0 }))
    .filter(p => Number.isFinite(p.price) && p.value > maxValue * 0.006);
  const visiblePrices = visibleLevels.length ? visibleLevels.map(p => p.price) : priceAxis;
  let pMin = visiblePrices.length ? Math.min(...visiblePrices) : Math.min(...levelMap.map(p => +p.price || currentPrice));
  let pMax = visiblePrices.length ? Math.max(...visiblePrices) : Math.max(...levelMap.map(p => +p.price || currentPrice));
  if (currentPrice) {
    const span = Math.max(Math.abs(currentPrice - pMin), Math.abs(pMax - currentPrice), currentPrice * 0.018);
    pMin = Math.min(pMin, currentPrice - span * 1.05);
    pMax = Math.max(pMax, currentPrice + span * 1.05);
  }
  const pad = Math.max((pMax - pMin) * 0.045, currentPrice ? currentPrice * 0.0015 : 1);
  const yMin = pMin - pad;
  const yMax = pMax + pad;
  const plot = { l: mode === 'mini' ? 8 : 18, r: mode === 'mini' ? 42 : 70, t: mode === 'mini' ? 10 : 18, b: mode === 'mini' ? 14 : 28 };
  const w = canvas.width - plot.l - plot.r;
  const h = canvas.height - plot.t - plot.b;
  const x = idx => plot.l + (+idx / xMax) * w;
  const y = price => plot.t + (1 - ((+price - yMin) / Math.max(yMax - yMin, 1))) * h;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const priceSlot = priceAxis.length > 1 ? Math.abs(y(priceAxis[0]) - y(priceAxis[1])) : Math.max(2, h / 120);
  const cellW = Math.max(1.6, w / Math.max(xMax, 1) * 1.08);
  const rowsByPrice = new Map();
  levelMap.forEach(item => {
    const value = +item.value || 0;
    const price = +item.price;
    const timeIndex = +item.time_index || 0;
    if (!value || !Number.isFinite(price)) return;
    const key = Number.isFinite(+item.price_index) ? +item.price_index : price;
    if (!rowsByPrice.has(key)) rowsByPrice.set(key, []);
    rowsByPrice.get(key).push({ ...item, value, price, timeIndex });
  });
  const drawSegment = (start, end, price, value, side) => {
    const ratio = Math.max(0, Math.min(1, Math.pow(value / maxValue, 0.42)));
    const cy = y(price);
    const startX = x(start) - cellW * 0.5;
    const endX = x(end) + cellW * 0.5;
    const width = Math.max(1.6, endX - startX);
    const sideName = liqPriceSide(price, currentPrice, side);
    const bandH = Math.max(0.45, Math.min(2.1, priceSlot * (0.12 + ratio * 0.2)));
    if (ratio > 0.58) {
      ctx.fillStyle = /short/i.test(sideName) ? `rgba(16,22,24,${0.16 + ratio * 0.34})` : `rgba(86,18,9,${0.12 + ratio * 0.32})`;
      ctx.fillRect(startX, cy - Math.max(1.2, bandH * 1.6), width, Math.max(2.2, bandH * 3.2));
    }
    ctx.fillStyle = liqBandColor(sideName, 0.05 + ratio * 0.72, ratio);
    ctx.fillRect(startX, cy - bandH / 2, width, bandH);
    if (ratio > 0.42) {
      ctx.fillStyle = /short/i.test(sideName) ? `rgba(26,255,244,${0.18 + ratio * 0.26})` : `rgba(255,232,38,${0.14 + ratio * 0.24})`;
      ctx.fillRect(startX, cy - 0.45, width, 0.9);
    }
  };
  rowsByPrice.forEach(items => {
    items.sort((a, b) => a.timeIndex - b.timeIndex);
    let segment = null;
    items.forEach(item => {
      if (!segment) {
        segment = { start: item.timeIndex, end: item.timeIndex, maxValue: item.value, sumValue: item.value, price: item.price, side: item.side };
        return;
      }
      if (item.timeIndex <= segment.end + 1) {
        segment.end = item.timeIndex;
        segment.maxValue = Math.max(segment.maxValue, item.value);
        segment.sumValue += item.value;
      } else {
        drawSegment(segment.start, segment.end, segment.price, segment.maxValue, segment.side);
        segment = { start: item.timeIndex, end: item.timeIndex, maxValue: item.value, sumValue: item.value, price: item.price, side: item.side };
      }
    });
    if (segment) drawSegment(segment.start, segment.end, segment.price, segment.maxValue, segment.side);
  });

  if (klines.length) {
    ctx.lineWidth = 0.75;
    klines.forEach((k, i) => {
      const cx = plot.l + (i / Math.max(klines.length - 1, 1)) * w;
      const open = +k.open || +k.close;
      const close = +k.close || open;
      const high = +k.high || Math.max(open, close);
      const low = +k.low || Math.min(open, close);
      const up = close >= open;
      ctx.strokeStyle = up ? 'rgba(38,166,154,.46)' : 'rgba(239,83,80,.46)';
      ctx.fillStyle = up ? 'rgba(38,166,154,.18)' : 'rgba(239,83,80,.18)';
      ctx.beginPath();
      ctx.moveTo(cx, y(low));
      ctx.lineTo(cx, y(high));
      ctx.stroke();
      const bodyY = Math.min(y(open), y(close));
      const bodyH = Math.max(1, Math.abs(y(open) - y(close)));
      ctx.fillRect(cx - 1, bodyY, 2, bodyH);
    });
  }

  const profile = new Map();
  levelMap.forEach(item => {
    const price = +item.price, value = +item.value || 0;
    if (Number.isFinite(price) && value > 0) profile.set(price, (profile.get(price) || 0) + value);
  });
  const maxProfile = Math.max(...profile.values(), 1);
  profile.forEach((value, price) => {
    const ratio = value / maxProfile;
    ctx.fillStyle = liqBandColor(liqPriceSide(price, currentPrice), 0.14 + ratio * 0.72, ratio);
    ctx.fillRect(plot.l + w + 7, y(price) - Math.max(0.8, priceSlot * 0.22), ratio * (plot.r - 18), Math.max(1, priceSlot * 0.42));
  });

  levelMap.filter(item => (+item.value || 0) >= maxValue * 0.72).slice(-120).forEach(item => {
    const price = +item.price, value = +item.value || 0;
    const ratio = value / maxValue;
    ctx.beginPath();
    ctx.arc(x(+item.time_index || 0), y(price), Math.max(2.2, Math.min(15, Math.sqrt(ratio) * 15)), 0, Math.PI * 2);
    ctx.fillStyle = liqBandColor(liqPriceSide(price, currentPrice, item.side), 0.12, ratio);
    ctx.strokeStyle = /short/i.test(liqPriceSide(price, currentPrice, item.side)) ? '#20e7e0' : '#ff7a45';
    ctx.lineWidth = 0.9;
    ctx.fill();
    ctx.stroke();
  });

  if (currentPrice) {
    const cy = y(currentPrice);
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = '#ff4d6d';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, cy);
    ctx.lineTo(canvas.width - plot.r + 8, cy);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#ff3366';
    ctx.fillRect(canvas.width - plot.r + 8, cy - 13, plot.r - 10, 26);
    ctx.fillStyle = '#fff';
    ctx.font = '11px sans-serif';
    ctx.fillText(fmt.price(currentPrice, 1), canvas.width - plot.r + 12, cy + 4);
  }

  ctx.fillStyle = '#111827';
  ctx.font = '10px sans-serif';
  const ticks = 6;
  for (let i = 0; i <= ticks; i++) {
    const price = yMin + (i / ticks) * (yMax - yMin);
    ctx.fillText(Number(price).toLocaleString(undefined, { maximumFractionDigits: 0 }), canvas.width - plot.r + 8, y(price) + 3);
  }
}

function renderLiquidationMapCanvas(chart, snapshot, mode) {
  const dom = chart?.getDom?.();
  if (!dom) return;
  dom.innerHTML = '';
  dom.style.position = 'relative';
  dom.style.background = '#fff';
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(320, dom.clientWidth || 1100);
  canvas.height = Math.max(220, dom.clientHeight || 520);
  canvas.style.width = '100%';
  canvas.style.height = '100%';
  canvas.style.display = 'block';
  dom.appendChild(canvas);
  const ctx = canvas.getContext('2d');
  const normalized = snapshot?.liq_map || {};
  const aggregatePoints = Array.isArray(normalized.points) ? normalized.points : [];
  const axisValues = [
    ...(Array.isArray(normalized.price_axis) ? normalized.price_axis : []),
  ].map(Number).filter(Number.isFinite);
  const currentPrice = +snapshot?.price || +state.lastStatus?.last_snapshot?.price || 0;
  const aggregateByPrice = new Map();
  const seriesNames = [];
  const seriesByPrice = new Map();
  aggregatePoints.forEach(p => {
    const price = +p.price;
    const value = +(p.value ?? p.intensity ?? 0);
    if (!Number.isFinite(price) || !Number.isFinite(value) || value <= 0) return;
    const series = String(p.series_label || p.series || p.side || 'aggregate');
    aggregateByPrice.set(price, (aggregateByPrice.get(price) || 0) + value);
    if (!seriesNames.includes(series)) seriesNames.push(series);
    if (!seriesByPrice.has(price)) seriesByPrice.set(price, {});
    const row = seriesByPrice.get(price);
    row[series] = (row[series] || 0) + value;
  });
  const priceAxis = [...new Set([
    ...axisValues,
    ...aggregateByPrice.keys(),
    ...(currentPrice ? [currentPrice] : []),
  ])].filter(Number.isFinite).sort((a, b) => a - b);
  if (!priceAxis.length || !aggregateByPrice.size) return;

  const minPrice = Math.min(...priceAxis);
  const maxPrice = Math.max(...priceAxis);
  const plot = { l: mode === 'mini' ? 10 : 34, r: mode === 'mini' ? 26 : 46, t: mode === 'mini' ? 16 : 34, b: mode === 'mini' ? 22 : 58 };
  const w = canvas.width - plot.l - plot.r;
  const h = canvas.height - plot.t - plot.b;
  const x = price => plot.l + ((price - minPrice) / Math.max(maxPrice - minPrice, 1)) * w;
  const barBase = plot.t + h;
  const curveTop = plot.t + h * 0.04;
  const curveBottom = plot.t + h * 0.68;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  ctx.strokeStyle = 'rgba(148,163,184,.22)';
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  for (let i = 0; i <= 5; i++) {
    const yy = plot.t + (i / 5) * h * 0.72;
    ctx.beginPath();
    ctx.moveTo(plot.l, yy);
    ctx.lineTo(plot.l + w, yy);
    ctx.stroke();
  }
  ctx.setLineDash([]);

  const palette = ['#8bd3f7', '#78a7ff', '#5f7dff', '#ffc400', '#ff7a2f', '#18b59d', '#a78bfa', '#f97316', '#64748b'];
  const seriesOrder = seriesNames.length ? seriesNames : ['aggregate'];
  const seriesColors = Object.fromEntries(seriesOrder.map((name, idx) => [name, palette[idx % palette.length]]));
  const bars = [...seriesByPrice.entries()].map(([price, row]) => {
    const total = seriesOrder.reduce((sum, key) => sum + (row[key] || 0), 0);
    return { price, row, total };
  }).filter(item => item.total > 0).sort((a, b) => a.price - b.price);
  const maxBar = Math.max(...bars.map(b => b.total), 1);
  const barW = Math.max(1, Math.min(5, w / Math.max(priceAxis.length, 1) * 2.2));
  const barMaxH = h * 0.62;
  bars.forEach(item => {
    let y0 = barBase;
    seriesOrder.forEach(key => {
      const value = item.row[key] || 0;
      if (!value) return;
      const bh = Math.max(1, (value / maxBar) * barMaxH);
      ctx.fillStyle = seriesColors[key] || '#64748b';
      ctx.fillRect(x(item.price) - barW / 2, y0 - bh, barW, bh);
      y0 -= bh;
    });
  });

  const cumulativeByPrice = aggregateByPrice.size ? aggregateByPrice : new Map(bars.map(item => [item.price, item.total]));
  const cumulativeLong = [];
  const cumulativeShort = [];
  let leftSum = 0;
  priceAxis.forEach(price => {
    if (price < currentPrice) leftSum += cumulativeByPrice.get(price) || 0;
    cumulativeLong.push({ price, value: leftSum });
  });
  let rightSum = 0;
  [...priceAxis].reverse().forEach(price => {
    if (price > currentPrice) rightSum += cumulativeByPrice.get(price) || 0;
    cumulativeShort.push({ price, value: rightSum });
  });
  cumulativeShort.reverse();
  const maxCum = Math.max(...cumulativeLong.map(p => p.value), ...cumulativeShort.map(p => p.value), 1);
  const cy = value => curveBottom - (value / maxCum) * (curveBottom - curveTop);

  function drawCurve(data, stroke, fill, side) {
    if (!data.length) return;
    ctx.beginPath();
    data.forEach((p, i) => {
      const px = x(p.price), py = cy(p.value);
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    });
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.lineTo(x(data[data.length - 1].price), barBase);
    ctx.lineTo(x(data[0].price), barBase);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
  }
  drawCurve(cumulativeShort, '#ff3347', 'rgba(255,51,71,.075)', 'short');
  drawCurve(cumulativeLong, '#18b59d', 'rgba(24,181,157,.075)', 'long');

  if (currentPrice) {
    const cx = x(currentPrice);
    ctx.setLineDash([8, 5]);
    ctx.strokeStyle = '#ff263d';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(cx, plot.t + 4);
    ctx.lineTo(cx, barBase);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#ff263d';
    ctx.beginPath();
    ctx.moveTo(cx, plot.t + 2);
    ctx.lineTo(cx - 6, plot.t + 16);
    ctx.lineTo(cx + 6, plot.t + 16);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = '#333';
    ctx.font = '12px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(`当前价格:${fmt.price(currentPrice, 0)}`, cx, plot.t - 4);
  }

  ctx.textAlign = 'center';
  ctx.fillStyle = '#111827';
  ctx.font = '11px sans-serif';
  const tickCount = mode === 'mini' ? 5 : 14;
  for (let i = 0; i <= tickCount; i++) {
    const price = minPrice + (i / tickCount) * (maxPrice - minPrice);
    ctx.fillText(fmt.price(price, 0), x(price), canvas.height - 18);
  }
  ctx.textAlign = 'left';
  ctx.fillStyle = '#111827';
  ctx.font = '12px sans-serif';
  ctx.fillText(fmt.compact(maxCum), 6, curveTop + 6);
  ctx.fillText('0', 8, barBase + 4);
  ctx.textAlign = 'right';
  ctx.fillText(fmt.compact(maxBar), canvas.width - 8, curveTop + 6);

  if (mode !== 'mini') {
    const labels = [
      ['#18b59d', '累计空单清算强度'],
      ['#ff3347', '累计多单清算强度'],
      ...seriesOrder.slice(0, 8).map(name => [seriesColors[name] || '#64748b', name]),
    ];
    const legendLeft = Math.max(4, plot.l);
    const legendRight = canvas.width - Math.max(8, plot.r);
    let lx = legendLeft;
    let ly = 12;
    labels.forEach(([color, label]) => {
      const labelWidth = 10 + 14 + ctx.measureText(label).width + 20;
      if (lx > legendLeft && lx + labelWidth > legendRight) {
        lx = legendLeft;
        ly += 18;
      }
      ctx.fillStyle = color;
      ctx.fillRect(lx, ly, 10, 10);
      ctx.fillStyle = '#374151';
      ctx.font = '12px sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText(label, lx + 14, ly + 9);
      lx += ctx.measureText(label).width + 34;
    });
  }
}

function renderLiquidationLevelsChart(chart, snapshot, mode) {
  const normalized = snapshot?.liq_map || {};
  const levelMap = Array.isArray(normalized.level_map) ? normalized.level_map : [];
  const priceAxis = (normalized.price_axis || []).map(Number).filter(Number.isFinite).sort((a, b) => a - b);
  const maxValue = Math.max(+normalized.max_liq_value || +normalized.max_value || 0, ...levelMap.map(p => +p.value || 0), 1);
  const market = state.lastStatus?.last_snapshot?.market || {};
  const klines = Array.isArray(market.klines) ? market.klines.slice(-220) : [];
  const currentPrice = +snapshot?.price || +state.lastStatus?.last_snapshot?.price || +state.lastStatus?.last_signal?.price || 0;
  const times = normalized.time_axis || [];
  const hasTimes = times.length > 1;
  const xMax = Math.max(hasTimes ? times.length - 1 : 1, ...levelMap.map(p => +p.time_index || 0));
  const priceMin = priceAxis.length ? Math.min(...priceAxis) : Math.min(...levelMap.map(p => +p.price || currentPrice));
  const priceMax = priceAxis.length ? Math.max(...priceAxis) : Math.max(...levelMap.map(p => +p.price || currentPrice));
  const pad = Math.max((priceMax - priceMin) * 0.08, currentPrice ? currentPrice * 0.003 : 1);
  const yMin = Math.floor(priceMin - pad);
  const yMax = Math.ceil(priceMax + pad);
  const ySlot = priceAxis.length > 1 ? Math.abs(priceAxis[1] - priceAxis[0]) : Math.max((yMax - yMin) / 80, 1);
  const xStep = hasTimes ? 1 : Math.max(1, Math.ceil(xMax / 120));

  const grouped = new Map();
  levelMap.forEach(item => {
    const value = +item.value || 0;
    const price = +item.price;
    const x = +item.time_index || 0;
    if (!value || !Number.isFinite(price)) return;
    const ratio = Math.log1p(value) / Math.log1p(maxValue);
    const key = `${item.price_index}:${Math.floor(x / xStep)}`;
    const prev = grouped.get(key);
    if (!prev || value > prev.value) grouped.set(key, { x, price, value, ratio, side: liqPriceSide(price, currentPrice, item.side) });
  });

  const bandsByPrice = new Map();
  [...grouped.values()].forEach(item => {
    const key = item.price;
    if (!bandsByPrice.has(key)) bandsByPrice.set(key, []);
    bandsByPrice.get(key).push(item);
  });
  const bandData = [];
  bandsByPrice.forEach(items => {
    items.sort((a, b) => a.x - b.x);
    let segment = null;
    items.forEach(item => {
      if (!segment) {
        segment = { start: item.x, end: item.x, price: item.price, value: item.value, ratio: item.ratio, side: item.side };
        return;
      }
      if (item.x <= segment.end + xStep) {
        segment.end = item.x;
        if (item.value > segment.value) {
          segment.value = item.value;
          segment.ratio = item.ratio;
        }
      } else {
        bandData.push([segment.start, segment.end, segment.price, segment.value, segment.side, segment.ratio]);
        segment = { start: item.x, end: item.x, price: item.price, value: item.value, ratio: item.ratio, side: item.side };
      }
    });
    if (segment) bandData.push([segment.start, segment.end, segment.price, segment.value, segment.side, segment.ratio]);
  });

  const profile = new Map();
  levelMap.forEach(item => {
    const price = +item.price;
    const value = +item.value || 0;
    if (Number.isFinite(price) && value > 0) profile.set(price, (profile.get(price) || 0) + value);
  });
  const maxProfile = Math.max(...profile.values(), 1);
  const profileData = [...profile.entries()].map(([price, value]) => [xMax + 2.4, price, value, liqPriceSide(price, currentPrice), value / maxProfile]);

  const bubbleData = levelMap
    .filter(item => (+item.value || 0) >= maxValue * 0.55)
    .slice(-90)
    .map(item => [+item.time_index || 0, +item.price, +item.value || 0, liqPriceSide(+item.price, currentPrice, item.side)]);

  const candleData = klines.map((k, idx) => {
    const t = hasTimes && klines.length > 1 ? (idx / Math.max(klines.length - 1, 1)) * xMax : idx;
    return [t, +k.open || +k.close || currentPrice, +k.close || currentPrice, +k.low || +k.close || currentPrice, +k.high || +k.close || currentPrice];
  }).filter(row => row.slice(1).every(Number.isFinite));

  const xFormatter = value => {
    const idx = Math.max(0, Math.min(times.length - 1, Math.round(+value || 0)));
    const raw = times[idx];
    if (raw == null) return '';
    const d = new Date(numericTime(raw, idx));
    return mode === 'mini' ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : d.toLocaleString([], { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  };

  chart.setOption({
    animation: false,
    backgroundColor: '#fff',
    grid: { left: mode === 'mini' ? 18 : 44, right: mode === 'mini' ? 34 : 58, top: 12, bottom: mode === 'mini' ? 18 : 34 },
    tooltip: {
      trigger: 'item',
      backgroundColor: '#fff',
      borderColor: '#e5e7eb',
      textStyle: { color: '#111827', fontSize: 11 },
      formatter(params) {
        if (params.seriesName === 'Large liquidation') return `${fmt.price(params.value[1], 1)}<br>${fmt.num(params.value[2], 2)}`;
        if (params.seriesName === 'Price') return `O ${fmt.price(params.value[1], 1)} C ${fmt.price(params.value[2], 1)}<br>L ${fmt.price(params.value[3], 1)} H ${fmt.price(params.value[4], 1)}`;
        return '';
      },
    },
    xAxis: {
      type: 'value',
      min: 0,
      max: xMax + Math.max(5, xMax * 0.08),
      splitLine: { show: false },
      axisLine: { lineStyle: { color: '#e5e7eb' } },
      axisTick: { show: false },
      axisLabel: { color: '#111827', fontSize: 10, formatter: xFormatter },
    },
    yAxis: {
      type: 'value',
      position: 'right',
      min: yMin,
      max: yMax,
      splitLine: { show: false },
      axisLine: { lineStyle: { color: '#e5e7eb' } },
      axisTick: { show: false },
      axisLabel: { color: '#111827', fontSize: 10, formatter: v => Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 }) },
    },
    dataZoom: mode === 'mini' ? [] : [{ type: 'inside', xAxisIndex: 0 }, { type: 'inside', yAxisIndex: 0 }],
    series: [
      {
        name: 'Liquidation levels',
        type: 'custom',
        data: bandData,
        renderItem(params, api) {
          const start = api.coord([api.value(0), api.value(2)]);
          const end = api.coord([api.value(1) + xStep, api.value(2)]);
          const ratio = api.value(5);
          const height = Math.max(1.2, Math.min(8, ySlot * 0.05 + 2 + ratio * 3));
          return {
            type: 'rect',
            shape: { x: start[0], y: start[1] - height / 2, width: Math.max(2, end[0] - start[0]), height },
            style: { fill: liqBandColor(api.value(4), 0.14 + ratio * 0.74, ratio) },
          };
        },
        silent: true,
      },
      {
        name: 'Right profile',
        type: 'scatter',
        data: profileData,
        symbol: 'rect',
        symbolSize: value => [Math.max(4, value[4] * 54), Math.max(2, Math.min(7, ySlot * 0.05 + 2))],
        itemStyle: { color: params => liqBandColor(params.value[3], 0.18 + params.value[4] * 0.62, params.value[4]) },
        silent: true,
      },
      {
        name: 'Price',
        type: 'candlestick',
        data: candleData,
        itemStyle: { color: 'rgba(39,174,96,.35)', color0: 'rgba(231,76,60,.35)', borderColor: 'rgba(39,174,96,.75)', borderColor0: 'rgba(231,76,60,.75)' },
        barWidth: mode === 'mini' ? 2 : 3,
        silent: true,
      },
      {
        name: 'Large liquidation',
        type: 'scatter',
        data: bubbleData,
        symbolSize: value => Math.max(4, Math.min(26, Math.sqrt((+value[2] || 0) / maxValue) * 32)),
        itemStyle: {
          color: params => liqBandColor(params.value[3], 0.22, (+params.value[2] || 0) / maxValue),
          borderColor: params => /short/i.test(params.value[3]) ? '#21e8e1' : '#ff7a45',
          borderWidth: 1,
        },
      },
      ...(currentPrice ? [{
        name: 'Current price',
        type: 'line',
        data: [[0, currentPrice], [xMax + 2, currentPrice]],
        symbol: 'none',
        lineStyle: { color: '#ff4d6d', width: 1, type: 'dashed' },
        markPoint: mode === 'mini' ? undefined : { symbol: 'rect', symbolSize: [54, 28], label: { color: '#fff', formatter: fmt.price(currentPrice, 1), fontSize: 10 }, itemStyle: { color: '#ff3366' }, data: [{ coord: [xMax + 2, currentPrice] }] },
      }] : []),
    ],
  }, true);
}

pageHooks.heatmap = function () {
  if (!state.keysReady) return;
  const chart = getChart('hmDetailChart');
  if (chart) renderHeatmapChart(chart, 'full');
  renderHeatmapSummary();
  renderHeatmapZones();
  renderHeatmapAlerts();
  renderHeatmapHistory();
  const corrChart = getChart('hmCorrChart');
  if (corrChart) {
    const snapshots = state.lastStatus?.heatmap?.snapshots || [];
    const rows = snapshots
      .filter(snap => heatmapSnapshotMatches(snap))
      .slice(0, 60)
      .map(snap => {
        const ts = Date.parse(snap.timestamp) || (+snap.epoch ? +snap.epoch * 1000 : Date.now());
        const price = heatmapCurrentPrice(snap);
        const density = heatmapStrengthRows(snap).reduce((sum, p) => sum + p.value, 0);
        return { ts, price, density };
      })
      .filter(row => Number.isFinite(row.ts) && row.price > 0 && row.density > 0)
      .sort((a, b) => a.ts - b.ts);
    if (rows.length) {
      corrChart.setOption({
        grid: { left: 50, right: 50, top: 10, bottom: 28 },
        tooltip: { trigger: 'axis', ...TT },
        xAxis: { type: 'time', ...AXIS },
        yAxis: [{ type: 'value', ...AXIS, name: '' }, { type: 'value', ...AXIS, name: '' }],
        graphic: [],
        series: [
          { type: 'line', smooth: true, showSymbol: rows.length < 8, data: rows.map(r => [r.ts, r.price]), lineStyle: { color: '#6c5ce7', width: 2 } },
          { type: 'bar', yAxisIndex: 1, data: rows.map(r => [r.ts, r.density]), itemStyle: { color: 'rgba(0,184,148,.4)' }, barWidth: '60%' },
        ],
      }, true);
    } else {
      corrChart.setOption({
        grid: { left: 50, right: 50, top: 10, bottom: 28 },
        xAxis: { type: 'time', ...AXIS, show: false },
        yAxis: [{ type: 'value', show: false }, { type: 'value', show: false }],
        series: [{ type: 'line', data: [] }],
        graphic: [{ type: 'text', left: 'center', top: 'middle', style: { text: '暂无相关性数据', fontSize: 13, fill: '#9ea2ab', fontFamily: 'inherit' } }],
      }, true);
    }
  }
};

function heatmapClustersFromSnapshot(snapshot) {
  const direct = snapshot?.heatmap?.clusters || [];
  if (Array.isArray(direct) && direct.length) return direct;
  const levelMap = snapshot?.liq_map?.level_map || [];
  if (Array.isArray(levelMap) && levelMap.length) {
    const current = +snapshot?.price || +state.lastStatus?.last_snapshot?.price || 0;
    const maxValue = +(snapshot?.liq_map?.max_liq_value || snapshot?.liq_map?.max_value || 1);
    const grouped = new Map();
    levelMap.forEach(item => {
      const price = +item.price;
      const value = +item.value || 0;
      if (!Number.isFinite(price) || value <= 0) return;
      const span = Math.max(price * 0.001, 1);
      const key = Math.round(price / span);
      const prev = grouped.get(key) || { price, value: 0 };
      prev.value += value;
      grouped.set(key, prev);
    });
    return [...grouped.values()].sort((a, b) => b.value - a.value).slice(0, 12).map(p => {
      const span = Math.max(p.price * 0.001, 1);
      return {
        low: p.price - span,
        high: p.price + span,
        score: p.value / Math.max(maxValue, 1),
        volume: p.value,
        side: current && p.price >= current ? 'above' : current ? 'below' : 'near',
        leverage_tier: p.value >= maxValue * 0.7 ? 'high' : p.value >= maxValue * 0.35 ? 'medium' : 'low',
      };
    });
  }
  const points = snapshot?.liq_map?.points || [];
  if (!Array.isArray(points) || !points.length) return [];
  return points
    .map(p => ({ price: +p.price, value: +(p.value ?? p.intensity ?? 0), side: p.side || p.series || 'liq' }))
    .filter(p => Number.isFinite(p.price) && Number.isFinite(p.value) && p.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, 12)
    .map(p => {
      const span = Math.max(p.price * 0.001, 1);
      return {
        low: p.price - span,
        high: p.price + span,
        score: p.value,
        volume: p.value,
        side: /above|short|ask/i.test(p.side) ? 'above' : /below|long|bid/i.test(p.side) ? 'below' : 'near',
        leverage_tier: p.value >= 0.75 ? 'high' : p.value >= 0.4 ? 'medium' : 'low',
      };
    });
}

function heatmapStrengthRows(snapshot) {
  const normalized = snapshot?.liq_map || {};
  const points = Array.isArray(normalized.points) ? normalized.points : [];
  return points
    .map(p => ({
      price: +p.price,
      value: +(p.value ?? p.intensity ?? 0),
      series: String(p.series || p.series_label || p.side || '').toLowerCase(),
    }))
    .filter(p => Number.isFinite(p.price) && Number.isFinite(p.value) && p.value > 0);
}

function heatmapCurrentPrice(snapshot) {
  return +snapshot?.price || +state.lastStatus?.last_snapshot?.price || +state.lastStatus?.last_signal?.price || 0;
}

function sideLabel(side) {
  if (side === 'above') return '空头清算';
  if (side === 'below') return '多头清算';
  return '现价附近';
}

function renderHeatmapSummary() {
  const snapshot = latestHeatmapSnapshot();
  const subtitle = $('hmScopeSubtitle') || qs('#page-heatmap .card-title span');
  if (subtitle) subtitle.textContent = `(${slashSymbol(selectedHeatmapSymbol())} · ${selectedHeatmapInterval().toUpperCase()} · 聚合清算 · API series)`;
  const rows = heatmapStrengthRows(snapshot);
  const clusters = heatmapClustersFromSnapshot(snapshot);
  const total = rows.reduce((acc, p) => acc + p.value, 0);
  const maxCluster = clusters.slice().sort((a, b) => (+(b.volume ?? b.score ?? 0)) - (+(a.volume ?? a.score ?? 0)))[0];
  const tierCounts = { high: 0, medium: 0, low: 0 };
  clusters.forEach(c => { tierCounts[c.leverage_tier || 'low'] = (tierCounts[c.leverage_tier || 'low'] || 0) + 1; });
  const clusterTotal = Math.max(clusters.length, 1);
  const statsCard = qsa('#page-heatmap .heatmap-bottom-grid > .card')[0] || qsa('#page-heatmap div[style*="grid-template-columns:1fr 1fr 1.2fr"] > .card')[0];
  if (!statsCard) return;
  if (!rows.length && !clusters.length) {
    statsCard.innerHTML = `
      <div class="card-head"><div class="card-title">清算统计</div></div>
      <div style="text-align:center;color:var(--text-3);padding:22px;background:var(--surface-2);border-radius:8px">暂无真实清算统计；点击“刷新”后从 Claw402 返回生成。</div>`;
    return;
  }
  statsCard.innerHTML = `
    <div class="card-head"><div class="card-title">清算统计</div></div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:12px">
      <div style="padding:10px;background:var(--surface-2);border-radius:8px"><div style="font-size:10.5px;color:var(--text-3)">有效清算点</div><div style="font-size:18px;font-weight:700;font-family:var(--mono)">${rows.length}</div><div style="font-size:10.5px;color:var(--text-3)">来自 Claw402 返回</div></div>
      <div style="padding:10px;background:var(--surface-2);border-radius:8px"><div style="font-size:10.5px;color:var(--text-3)">累计强度</div><div style="font-size:18px;font-weight:700;font-family:var(--mono)">${fmt.num(total, 2)}<span style="font-size:11px;color:var(--text-3);font-weight:400"> raw</span></div><div style="font-size:10.5px;color:var(--text-3)">未伪造 USDT 金额</div></div>
      <div style="padding:10px;background:var(--surface-2);border-radius:8px"><div style="font-size:10.5px;color:var(--text-3)">最强区域</div><div style="font-size:14px;font-weight:700;font-family:var(--mono)">${maxCluster ? `${fmt.price(maxCluster.low, 0)} - ${fmt.price(maxCluster.high, 0)}` : '-'}</div><div style="font-size:10.5px;color:var(--text-3)">强度 ${maxCluster ? fmt.num(maxCluster.volume ?? maxCluster.score, 2) : '-'}</div></div>
    </div>
    <div style="font-size:11px;color:var(--text-2);margin-bottom:6px">聚类强度分布</div>
    <div style="display:flex;height:28px;border-radius:6px;overflow:hidden;background:var(--surface-2)">
      ${['high','medium','low'].map((tier, idx) => {
        const colors = { high: '#e74c3c', medium: '#f39c12', low: '#0984e3' };
        const pct = clusters.length ? (tierCounts[tier] / clusterTotal) * 100 : 0;
        return pct ? `<div style="width:${pct}%;background:${colors[tier]};display:flex;align-items:center;justify-content:center;color:#fff;font-size:10px;font-weight:600">${pct.toFixed(1)}%</div>` : '';
      }).join('') || '<div style="display:flex;align-items:center;justify-content:center;width:100%;color:var(--text-3);font-size:11px">暂无聚类分布</div>'}
    </div>
    <div style="font-size:11px;color:var(--text-2);line-height:1.6">清算地图仅展示后端归一化后的真实 Claw402 响应；未返回的字段显示为空，不再使用演示数据。</div>`;
}

function renderHeatmapZones() {
  const body = $('hmZonesBody');
  if (!body) return;
  const snapshot = latestHeatmapSnapshot();
  const clusters = heatmapClustersFromSnapshot(snapshot);
  if (!clusters.length) {
    body.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-3);padding:18px">暂无真实清算地图聚类数据</td></tr>';
    if ($('hmZonesCount')) $('hmZonesCount').textContent = '查看全部清算区 (0) ›';
    return;
  }
  if ($('hmZonesCount')) $('hmZonesCount').textContent = `查看全部清算区 (${clusters.length}) ›`;
  body.innerHTML = clusters.slice(0, 8).map(c => {
    const tier = c.leverage_tier || 'low';
    const color = tier === 'high' ? 'var(--red)' : tier === 'medium' ? 'var(--amber)' : 'var(--text-3)';
    const impact = c.side === 'above' ? '上方阻力/空头清算带' : c.side === 'below' ? '下方支撑/多头清算带' : '现价附近清算带';
    return `<tr>
      <td style="font-weight:600">${fmt.price(c.low, 0)} - ${fmt.price(c.high, 0)}</td>
      <td><span style="color:${color}">● ${escapeHtml(tier)}</span></td>
      <td>${impact}</td>
      <td style="color:var(--text-2)">score ${fmt.num(c.score, 2)} / ${fmt.num(c.volume, 0)}</td>
    </tr>`;
  }).join('');
}

function renderHeatmapAlerts() {
  const cards = qsa('#page-heatmap div[style*="grid-template-columns:1fr 1fr 1.2fr"] > .card');
  const alertCard = cards[1];
  if (!alertCard) return;
  const snapshot = latestHeatmapSnapshot();
  const current = heatmapCurrentPrice(snapshot);
  const clusters = heatmapClustersFromSnapshot(snapshot)
    .filter(c => c && Number.isFinite(+c.low) && Number.isFinite(+c.high))
    .slice()
    .sort((a, b) => (+b.volume || +b.score || 0) - (+a.volume || +a.score || 0))
    .slice(0, 8);
  alertCard.innerHTML = `
    <div class="card-head"><div class="card-title">大额清算提醒 <span class="info">i</span></div></div>
    <table class="tbl" style="font-size:11.5px">
      <thead><tr><th>价格 (USDT)</th><th>方向</th><th>强度</th><th>触发条件</th><th>时间</th></tr></thead>
      <tbody>${clusters.length ? clusters.map(c => {
        const price = ((+c.low + +c.high) / 2) || +c.price || 0;
        const cls = c.side === 'above' ? 'neg' : 'pos';
        const trigger = c.side === 'above' ? `>= ${fmt.price(+c.low, 0)}` : c.side === 'below' ? `<= ${fmt.price(+c.high, 0)}` : `~ ${fmt.price(current || price, 0)}`;
        return `<tr><td class="${cls}" style="font-weight:600">${fmt.price(price, 0)}</td><td>${sideLabel(c.side)}</td><td class="${cls}" style="font-weight:600">${fmt.num(c.volume ?? c.score, 2)}</td><td>${trigger}</td><td>${fmt.ts(snapshot.timestamp)}</td></tr>`;
      }).join('') : '<tr><td colspan="5" style="text-align:center;color:var(--text-3);padding:18px">暂无真实清算提醒</td></tr>'}</tbody>
    </table>`;
  if ($('hmAlertsCount')) $('hmAlertsCount').textContent = `查看全部提醒 (${clusters.length}) ›`;
}

function renderHeatmapHistory() {
  const directCards = qsa('#page-heatmap > .card');
  const historyCard = directCards[directCards.length - 1];
  if (!historyCard) return;
  const heatmapEvents = (state.lastEvents || []).filter(e => e.kind === 'heatmap' || e.kind === 'signal').slice(0, 5);
  historyCard.innerHTML = `
    <div class="card-head"><div class="card-title">历史信号对照</div><button class="card-action">真实事件 ${heatmapEvents.length}</button></div>
    <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px">
      ${heatmapEvents.length ? heatmapEvents.map(e => `
        <div style="padding:10px;background:var(--surface-2);border-radius:8px;border-left:3px solid ${e.kind === 'signal' ? 'var(--purple)' : 'var(--green)'}">
          <div style="font-size:11px;color:var(--text-3);margin-bottom:4px">● ${fmt.ts(e.timestamp)}</div>
          <div style="font-size:12px;font-weight:600;margin-bottom:4px">${escapeHtml(e.title || e.kind || '-')}</div>
          <div style="font-size:11px;color:var(--text-2)">${escapeHtml(e.detail || e.message || '')}</div>
        </div>`).join('') : '<div style="grid-column:1/-1;text-align:center;color:var(--text-3);padding:20px;background:var(--surface-2);border-radius:8px">暂无真实历史信号</div>'}
    </div>`;
}

// ============== MARKET PAGE ==============
pageHooks.market = function () {
  if (!state.keysReady) return;
  const snap = state.lastStatus?.last_snapshot || {};
  const market = snap.market || {};
  const symbol = slashSymbol(snap.symbol || selectedHeaderSymbol());
  const labels = qsa('#page-market .kpi-label');
  if (labels[0]) labels[0].textContent = `${symbol} 价格`;
  if ($('mktBtcPrice')) $('mktBtcPrice').textContent = snap.price ? '$' + fmt.price(snap.price, 2) : '-';
  if ($('mktBtcChg')) {
    $('mktBtcChg').textContent = signedPct(market.change_24h_pct);
    $('mktBtcChg').className = (+market.change_24h_pct || 0) >= 0 ? 'kpi-sub pos' : 'kpi-sub neg';
  }
  if ($('mktEthPrice')) $('mktEthPrice').textContent = symbol.startsWith('ETH') && snap.price ? '$' + fmt.price(snap.price, 2) : '-';
  if ($('mktEthChg')) $('mktEthChg').textContent = symbol.startsWith('ETH') ? signedPct(market.change_24h_pct) : '-';
  if ($('mktCap')) $('mktCap').textContent = '-';
  if ($('mktBtcPrice') && (snap.symbol || '').startsWith('BTC')) $('mktBtcPrice').textContent = '$' + fmt.price(snap.price, 2);
  if ($('mktBtcChg')) $('mktBtcChg').textContent = market.change_24h_pct == null ? '—' : ((+market.change_24h_pct >= 0 ? '+' : '') + fmt.pct(market.change_24h_pct));
  if ($('mktVol')) $('mktVol').textContent = market.volume_24h_quote ? fmt.num(+market.volume_24h_quote / 1e9, 2) : '—';
  if ($('mktVolatility')) {
    const high = +market.high_24h || 0, low = +market.low_24h || 0, price = +snap.price || 0;
    $('mktVolatility').textContent = price && high && low ? fmt.num(((high - low) / price) * 100, 2) : '—';
  }
  const priceChart = getChart('mktPriceChart');
  if (priceChart) {
    const klines = market.klines || [];
    const lastPrice = snap.price || state.lastStatus?.last_signal?.price;
    if (klines.length || lastPrice) {
      const data = klines.length ? klines.map(k => [k.close_time || k.open_time, +k.close]) : [[Date.now(), +lastPrice]];
      priceChart.setOption({
        grid: { left: 55, right: 14, top: 14, bottom: 28 },
        tooltip: { trigger: 'axis', ...TT },
        xAxis: { type: 'time', ...AXIS },
        yAxis: { type: 'value', ...AXIS },
        graphic: [],
        series: [{ type: 'line', smooth: true, showSymbol: true, data, lineStyle: { color: '#6c5ce7', width: 2 }, areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(108,92,231,.15)' }, { offset: 1, color: 'rgba(108,92,231,0)' }] } } }],
      }, true);
    } else {
      priceChart.setOption({
        grid: { left: 55, right: 14, top: 14, bottom: 28 },
        xAxis: { type: 'time', ...AXIS, show: false },
        yAxis: { type: 'value', ...AXIS, show: false },
        series: [{ type: 'line', data: [] }],
        graphic: [{ type: 'text', left: 'center', top: 'middle', style: { text: '暂无价格数据', fontSize: 13, fill: '#9ea2ab', fontFamily: 'inherit' } }],
      }, true);
    }
  }

  const sentChart = getChart('mktSentimentGauge');
  if (sentChart) {
    const sentimentScore = state.lastStatus?.last_signal?.confidence ? Math.round(state.lastStatus.last_signal.confidence * 100) : 50;
    sentChart.setOption({
      series: [{
        type: 'gauge', startAngle: 180, endAngle: 0, min: 0, max: 100,
        pointer: { show: true, length: '60%', width: 4, itemStyle: { color: '#6c5ce7' } },
        axisLine: { lineStyle: { width: 12, color: [[0.25, '#e74c3c'], [0.5, '#f39c12'], [0.75, '#00b894'], [1, '#0984e3']] } },
        axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
        detail: { formatter: '{value}', fontSize: 20, fontWeight: 700, fontFamily: '"JetBrains Mono",monospace', color: '#00b894', offsetCenter: [0, '30%'] },
        data: [{ value: sentimentScore }],
      }],
    }, true);
  }

  const flowChart = getChart('mktFlowChart');
  if (flowChart) {
    const cats = [snap.symbol || '—'];
    flowChart.setOption({
      grid: { left: 40, right: 14, top: 10, bottom: 28 },
      tooltip: { trigger: 'axis', ...TT },
      xAxis: { type: 'category', data: cats, ...AXIS },
      yAxis: { type: 'value', ...AXIS },
      series: [
        { type: 'bar', data: [+(market.volume_24h_quote || 0)], itemStyle: { color: '#00b894' }, barWidth: '35%' },
      ],
    }, true);
  }
  renderMarketDerivedPanels(snap, market);

  const volChart = getChart('mktVolChart');
  if (volChart) {
    volChart.setOption({
      grid: { left: 40, right: 14, top: 14, bottom: 28 },
      xAxis: { type: 'time', ...AXIS, show: false },
      yAxis: { type: 'value', ...AXIS, show: false },
      series: [{ type: 'line', data: [] }],
      graphic: [{ type: 'text', left: 'center', top: 'middle', style: { text: '暂无波动率数据', fontSize: 13, fill: '#9ea2ab', fontFamily: 'inherit' } }],
    }, true);
  }
};

function renderMarketDerivedPanels(snap, market) {
  const symbol = slashSymbol(snap?.symbol || selectedHeaderSymbol());
  const kpiSubs = qsa('#page-market .kpi-sub');
  if (kpiSubs[2]) { kpiSubs[2].className = 'kpi-sub'; kpiSubs[2].textContent = '未配置总市值 provider'; }
  if (kpiSubs[3]) { kpiSubs[3].className = 'kpi-sub'; kpiSubs[3].textContent = '来自交易所 ticker'; }
  if (kpiSubs[4]) { kpiSubs[4].className = 'kpi-sub'; kpiSubs[4].textContent = '未配置恐惧贪婪 provider'; }
  if (kpiSubs[5]) { kpiSubs[5].className = 'kpi-sub'; kpiSubs[5].textContent = '由 24h 高低价计算'; }
  const sectors = $('mktSectors');
  if (sectors) {
    sectors.innerHTML = `<div style="text-align:center;color:var(--text-3);padding:20px;background:var(--surface-2);border-radius:8px">未配置真实板块轮动数据源，已停止展示演示板块数据。</div>`;
  }

  const sentimentCard = $('mktSentimentGauge')?.parentElement;
  const details = sentimentCard ? qsa(':scope > div', sentimentCard)[2] : null;
  if (details) {
    details.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px"><span style="color:var(--text-2)">多空比</span><span style="font-family:var(--mono);font-weight:600">${market.long_short_ratio ?? '-'}</span></div>
      <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px"><span style="color:var(--text-2)">资金费率</span><span style="font-family:var(--mono);font-weight:600">${market.funding_rate == null ? '-' : signedPct(+market.funding_rate * 100)}</span></div>
      <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px"><span style="color:var(--text-2)">持仓量变化</span><span style="font-family:var(--mono);font-weight:600">${market.open_interest_change_pct == null ? '-' : signedPct(market.open_interest_change_pct)}</span></div>
      <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px"><span style="color:var(--text-2)">数据源</span><span style="font-family:var(--mono);font-weight:600">${escapeHtml(market.exchange || market.source || '-')}</span></div>`;
  }

  const flowGrid = $('mktFlowChart')?.nextElementSibling;
  if (flowGrid) {
    flowGrid.innerHTML = `
      <div style="padding:8px;background:var(--green-soft);border-radius:6px;text-align:center"><div style="font-size:10.5px;color:var(--text-3)">24h 成交额</div><div style="font-size:14px;font-weight:700;font-family:var(--mono);color:var(--green)">${market.volume_24h_quote ? fmt.num(market.volume_24h_quote, 2) : '-'}</div></div>
      <div style="padding:8px;background:var(--red-soft);border-radius:6px;text-align:center"><div style="font-size:10.5px;color:var(--text-3)">净流入/流出</div><div style="font-size:14px;font-weight:700;font-family:var(--mono);color:var(--red)">未配置 provider</div></div>`;
  }

  const moversBody = qs('#page-market table.tbl tbody');
  if (moversBody) {
    moversBody.innerHTML = snap?.price ? `<tr>
      <td>${escapeHtml(symbol)}</td>
      <td>${fmt.price(snap.price, 2)}</td>
      <td class="${(+market.change_24h_pct || 0) >= 0 ? 'pos' : 'neg'}">${signedPct(market.change_24h_pct)}</td>
      <td>${market.volume_24h_quote ? fmt.num(market.volume_24h_quote, 2) : '-'}</td>
    </tr>` : '<tr><td colspan="4" style="text-align:center;color:var(--text-3);padding:18px">暂无真实交易所行情</td></tr>';
  }
}

// ============== EVOLUTION PAGE ==============
pageHooks.evolution = function () {
  if (!state.keysReady) return;
  const evoChart = getChart('evoChart');
  if (evoChart) {
    const closed = (state.lastOrders || []).filter(o => (o.status || '').toUpperCase() === 'CLOSED').slice().reverse();
    let equity = +(state.lastStatus?.config?.paper_seed_usd || 100000);
    const data = closed.map((o, i) => {
      equity += +(o.pnl_usd || 0);
      return [i + 1, equity];
    });
    evoChart.setOption({
      grid: { left: 40, right: 14, top: 14, bottom: 28 },
      tooltip: { trigger: 'axis', ...TT },
      xAxis: { type: 'value', name: '交易', ...AXIS, min: 1 },
      yAxis: { type: 'value', ...AXIS },
      series: [{ type: 'line', smooth: true, showSymbol: false, data, lineStyle: { color: '#6c5ce7', width: 2 }, areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(108,92,231,.2)' }, { offset: 1, color: 'rgba(108,92,231,0)' }] } } }],
      graphic: data.length ? [] : [{ type: 'text', left: 'center', top: 'middle', style: { text: '暂无真实 paper 交易样本', fontSize: 13, fill: '#9ea2ab', fontFamily: 'inherit' } }],
    }, true);
  }

  const fitnessChart = getChart('evoFitnessChart');
  if (fitnessChart) {
    fitnessChart.setOption({
      series: [{
        type: 'gauge', startAngle: 180, endAngle: 0, min: 0, max: 100,
        pointer: { show: true, length: '55%', width: 4, itemStyle: { color: '#6c5ce7' } },
        axisLine: { lineStyle: { width: 10, color: [[0.3, '#e74c3c'], [0.6, '#f39c12'], [0.8, '#00b894'], [1, '#0984e3']] } },
        axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
        detail: { formatter: '{value}', fontSize: 18, fontWeight: 700, fontFamily: '"JetBrains Mono",monospace', color: '#6c5ce7', offsetCenter: [0, '30%'] },
        data: [{ value: 78 }],
      }],
    }, true);
  }
};

// ============== ORDERS PAGE ==============
async function loadOrders() {
  if (!state.keysReady) return;
  try {
    const j = await api('GET', '/api/orders');
    state.lastOrders = j.orders || j || [];
    renderOrdersPage();
    renderDashRecentOrders(state.lastOrders);
  } catch {}
}

function renderOrdersPage() {
  renderOrdersTable();
  renderOrderSummary();
  renderOrderDetail((state.lastOrders || [])[0]);
  renderOrderDistribution();
  renderOrderAnomalies();
}

function renderOrdersTable() {
  const tbody = $('ordersTableBody');
  if (!tbody) return;
  if (!state.keysReady || !state.lastOrders.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-3);padding:30px">暂无订单数据</td></tr>';
    return;
  }
  const orders = (state.lastOrders || []).slice().reverse().slice(0, 20);
  tbody.innerHTML = orders.map(o => {
    const pnl = +o.pnl_pct || 0;
    const cls = pnl > 0 ? 'pos' : pnl < 0 ? 'neg' : '';
    const st = (o.status || '').toUpperCase();
    const statusTag = st === 'OPEN' ? '<span class="tag tag-up">进行中</span>' : st === 'CLOSED' ? '<span class="tag tag-done">已完成</span>' : `<span class="tag">${o.status || '—'}</span>`;
    return `<tr>
      <td style="font-weight:600">${o.symbol || 'BTC/USDT'}</td>
      <td><span class="tag ${o.side === 'long' ? 'tag-up' : 'tag-dn'}">${o.side === 'long' ? '做多' : '做空'}</span></td>
      <td>${fmt.price(o.entry, 1)}</td>
      <td>${st === 'CLOSED' ? fmt.price(o.exit, 1) : '—'}</td>
      <td class="${cls}" style="font-weight:600">${pnl > 0 ? '+' : ''}${pnl.toFixed(2)}%</td>
      <td>${statusTag}</td>
      <td>${fmt.ts(o.timestamp)}</td>
    </tr>`;
  }).join('');
}

function renderOrdersTable() {
  const tbody = $('ordersTableBody');
  if (!tbody) return;
  const orders = state.keysReady ? (state.lastOrders || []) : [];
  if (!orders.length) {
    tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text-3);padding:30px">暂无真实订单数据</td></tr>';
    return;
  }
  tbody.innerHTML = orders.slice(0, 20).map(o => {
    const pnl = +(o.pnl_usd ?? o.pnl_pct ?? 0) || 0;
    const cls = pnl > 0 ? 'pos' : pnl < 0 ? 'neg' : '';
    const st = (o.status || '').toUpperCase();
    const statusTag = st === 'OPEN' ? '<span class="tag tag-up">进行中</span>' : st === 'CLOSED' ? '<span class="tag tag-done">已完成</span>' : `<span class="tag">${escapeHtml(o.status || '-')}</span>`;
    const side = orderSide(o);
    const sideTag = side === 'LONG' ? '<span class="tag tag-up">做多</span>' : side === 'SHORT' ? '<span class="tag tag-dn">做空</span>' : '<span class="tag">-</span>';
    return `<tr>
      <td>${fmt.ts(o.timestamp || o.created_at)}</td>
      <td style="font-weight:600">${slashSymbol(o.symbol || '')}</td>
      <td>${sideTag}</td>
      <td>${escapeHtml(o.type || o.order_type || 'paper')}</td>
      <td>${orderQty(o) ?? '-'}</td>
      <td>${fmt.price(orderPrice(o, 'entry_price', 'entry'), 2)}</td>
      <td>${st === 'CLOSED' ? fmt.price(orderPrice(o, 'exit_price', 'exit'), 2) : '-'}</td>
      <td>${statusTag}</td>
      <td class="${cls}" style="font-weight:600">${pnl > 0 ? '+' : ''}${fmt.num(pnl, 2)}</td>
      <td>${escapeHtml(o.signal_id || o.reason || o.strategy || '-')}</td>
    </tr>`;
  }).join('');
}

function renderOrderSummary() {
  const orders = state.lastOrders || [];
  const open = orders.filter(o => (o.status || '').toUpperCase() === 'OPEN');
  const closed = orders.filter(o => (o.status || '').toUpperCase() === 'CLOSED');
  const turnover = closed.reduce((acc, o) => acc + Math.abs((+orderQty(o) || 0) * (orderPrice(o, 'entry_price', 'entry') || 0)), 0);
  const pnl = orders.reduce((acc, o) => acc + (+(o.pnl_usd ?? 0) || 0), 0);
  const values = qsa('#page-orders .kpi-value');
  const subs = qsa('#page-orders .kpi-sub');
  if (values[0]) values[0].textContent = String(open.length);
  if (values[1]) values[1].textContent = String(closed.length);
  if (values[2]) values[2].innerHTML = `${fmt.num(turnover, 2)}<span class="unit">USDT</span>`;
  if (values[3]) values[3].textContent = '-';
  if (values[4]) values[4].innerHTML = `${fmt.num(pnl, 2)}<span class="unit">USDT</span>`;
  subs.forEach(s => { s.className = 'kpi-sub'; s.textContent = '来自 /api/orders'; });
  const countLabel = qs('#page-orders table.tbl')?.nextElementSibling?.querySelector('span');
  if (countLabel) countLabel.textContent = `共 ${orders.length} 条`;
}

function renderOrderDetail(order) {
  const detailCard = qsa('#page-orders div[style*="grid-template-columns:1.6fr 1fr"] > .card')[1];
  if (!detailCard) return;
  if (!order) {
    detailCard.innerHTML = '<div class="card-head"><div class="card-title">订单详情</div></div><div style="text-align:center;color:var(--text-3);padding:40px">暂无真实订单</div>';
    return;
  }
  const st = (order.status || '').toUpperCase() || '-';
  const side = orderSide(order) || '-';
  detailCard.innerHTML = `
    <div style="display:flex;align-items:flex-start;justify-content:space-between;padding-bottom:12px;border-bottom:1px solid var(--border);margin-bottom:12px">
      <div><div style="font-size:12px;color:var(--text-3);margin-bottom:4px">订单详情</div><div style="display:flex;align-items:center;gap:8px"><span style="font-size:14px;font-weight:600;font-family:var(--mono)">#${escapeHtml(order.id || order.order_id || order.signal_id || '-')}</span><span class="tag">${escapeHtml(st)}</span></div></div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px 14px;margin-bottom:14px;font-size:12px">
      <div><div style="color:var(--text-3);font-size:11px;margin-bottom:3px">交易对</div><div style="font-weight:500">${slashSymbol(order.symbol || '')}</div></div>
      <div><div style="color:var(--text-3);font-size:11px;margin-bottom:3px">创建时间</div><div style="font-family:var(--mono)">${escapeHtml(order.timestamp || order.created_at || '-')}</div></div>
      <div><div style="color:var(--text-3);font-size:11px;margin-bottom:3px">方向</div><div>${side}</div></div>
      <div><div style="color:var(--text-3);font-size:11px;margin-bottom:3px">状态</div><div>${escapeHtml(st)}</div></div>
      <div><div style="color:var(--text-3);font-size:11px;margin-bottom:3px">订单类型</div><div>${escapeHtml(order.type || order.order_type || 'paper')}</div></div>
      <div><div style="color:var(--text-3);font-size:11px;margin-bottom:3px">数量</div><div style="font-family:var(--mono)">${orderQty(order) ?? '-'}</div></div>
      <div><div style="color:var(--text-3);font-size:11px;margin-bottom:3px">开仓价</div><div style="font-family:var(--mono)">${fmt.price(orderPrice(order, 'entry_price', 'entry'), 2)}</div></div>
      <div><div style="color:var(--text-3);font-size:11px;margin-bottom:3px">平仓价</div><div style="font-family:var(--mono)">${fmt.price(orderPrice(order, 'exit_price', 'exit'), 2)}</div></div>
      <div><div style="color:var(--text-3);font-size:11px;margin-bottom:3px">盈亏</div><div style="font-family:var(--mono)">${fmt.num(order.pnl_usd ?? order.pnl_pct, 2)}</div></div>
      <div><div style="color:var(--text-3);font-size:11px;margin-bottom:3px">来源</div><div>${escapeHtml(order.signal_id || order.reason || order.strategy || '-')}</div></div>
    </div>
    <div style="padding:10px;background:var(--surface-2);border-radius:8px;font-size:11.5px;color:var(--text-2);line-height:1.6">该面板只展示 /api/orders 返回字段；未返回的交易所回执、滑点和手续费不会用示例值补齐。</div>`;
}

function renderOrderDistribution() {
  const card = qsa('#page-orders div[style*="grid-template-columns:1fr 1.5fr"] > .card')[0];
  if (!card) return;
  const orders = state.lastOrders || [];
  const open = orders.filter(o => (o.status || '').toUpperCase() === 'OPEN').length;
  const closed = orders.filter(o => (o.status || '').toUpperCase() === 'CLOSED').length;
  card.innerHTML = `<div class="card-head"><div class="card-title">成交分布</div></div>
    <div style="display:grid;gap:8px">
      <div style="display:flex;justify-content:space-between;font-size:12px"><span>OPEN</span><strong style="font-family:var(--mono)">${open}</strong></div>
      <div style="display:flex;justify-content:space-between;font-size:12px"><span>CLOSED</span><strong style="font-family:var(--mono)">${closed}</strong></div>
      <div style="display:flex;justify-content:space-between;font-size:12px"><span>TOTAL</span><strong style="font-family:var(--mono)">${orders.length}</strong></div>
    </div>`;
}

function renderOrderAnomalies() {
  const card = qsa('#page-orders div[style*="grid-template-columns:1fr 1.5fr"] > .card')[1];
  if (!card) return;
  const events = (state.lastEvents || []).filter(e => e.kind === 'error' || e.kind === 'risk' || (e.kind === 'order' && /fail|error|reject/i.test(e.message || e.detail || ''))).slice(0, 5);
  card.innerHTML = `<div class="card-head"><div class="card-title">异常订单提醒</div></div>
    <div style="display:flex;flex-direction:column;gap:1px;background:var(--border);border-radius:8px;overflow:hidden">
      ${events.length ? events.map(e => `<div style="display:grid;grid-template-columns:120px 80px 1fr;gap:10px;padding:10px 12px;background:var(--surface);font-size:12px"><span style="font-family:var(--mono);font-size:11px;color:var(--text-3)">${fmt.ts(e.timestamp)}</span><span style="font-weight:500">${escapeHtml(e.kind || '-')}</span><span style="color:var(--text-2)">${escapeHtml(e.message || e.detail || e.title || '')}</span></div>`).join('') : '<div style="padding:20px;text-align:center;color:var(--text-3);background:var(--surface)">暂无真实异常订单事件</div>'}
    </div>`;
}

pageHooks.orders = function () {
  if (!state.keysReady) return;
  renderOrdersPage();
};

// ============== EVENTS PAGE ==============
function renderDashRecentOrders(orders) {
  const tbody = document.getElementById('dashRecentOrders');
  if (!tbody) return;
  if (!orders.length) { tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-3);padding:20px">暂无订单</td></tr>'; return; }
  tbody.innerHTML = orders.slice(0, 5).map(o => {
    const ts = o.timestamp ? new Date(o.timestamp).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '—';
    const sideColor = o.side === 'LONG' ? 'var(--green-text)' : 'var(--red-text)';
    const pnl = +o.pnl_usd || 0;
    const pnlColor = pnl >= 0 ? 'var(--green-text)' : 'var(--red-text)';
    return `<tr><td>${ts}</td><td style="color:${sideColor};font-weight:600">${o.side === 'LONG' ? '做多' : '做空'}</td><td>${o.symbol || '—'}</td><td>${o.type || '市价'}</td><td>${o.qty || '—'}</td><td>${o.entry_price ? (+o.entry_price).toLocaleString() : '—'}</td><td style="color:${pnlColor};font-weight:600">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}</td><td><span class="tag ${o.status === 'CLOSED' ? 'tag-done' : 'tag-up'}">${o.status === 'CLOSED' ? '已平仓' : o.status || '—'}</span></td></tr>`;
  }).join('');
}

async function loadEvents() {
  if (!state.keysReady) return;
  try {
    const j = await api('GET', '/api/events');
    state.lastEvents = j.events || j || [];
    renderEventsTimeline();
    renderDashNotifications(state.lastEvents);
  } catch {}
}

function renderEventsTimeline() {
  const container = $('evTimeline');
  if (!container) return;
  const events = (state.lastEvents || []).slice().reverse().slice(0, 30);
  container.innerHTML = events.map(e => {
    const kindColors = { order: 'var(--green)', error: 'var(--red)', signal: 'var(--purple)', agent: 'var(--blue)', risk: 'var(--amber)', heatmap: 'var(--orange)' };
    const color = kindColors[e.kind] || 'var(--text-3)';
    return `<div class="ev-row" style="display:flex;align-items:flex-start;gap:10px;padding:10px 12px;border-radius:8px;cursor:pointer;transition:background .15s">
      <div style="width:8px;height:8px;border-radius:50%;background:${color};margin-top:5px;flex-shrink:0"></div>
      <div style="flex:1;min-width:0">
        <div style="font-size:12px;font-weight:500;margin-bottom:2px">${escapeHtml(e.title || e.kind || '')}</div>
        <div style="font-size:11px;color:var(--text-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(e.detail || e.message || '')}</div>
      </div>
      <div style="font-size:10.5px;color:var(--text-3);white-space:nowrap">${fmt.ts(e.timestamp)}</div>
    </div>`;
  }).join('');
}

function renderEventsTimeline() {
  const container = $('evTimeline');
  if (!container) return;
  const events = (state.lastEvents || []).slice(0, 30);
  renderEventsSummary(state.lastEvents || []);
  renderEventDetail(events[0]);
  if (!events.length) {
    container.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-3);background:var(--surface)">暂无真实事件数据</div>';
    return;
  }
  container.innerHTML = events.map(e => {
    const kindColors = { order: 'var(--green)', error: 'var(--red)', signal: 'var(--purple)', agent: 'var(--blue)', risk: 'var(--amber)', heatmap: 'var(--orange)', llm_review: 'var(--purple)' };
    const color = kindColors[e.kind] || 'var(--text-3)';
    return `<div class="ev-row" style="display:flex;align-items:flex-start;gap:10px;padding:10px 12px;background:var(--surface);cursor:pointer">
      <div style="width:8px;height:8px;border-radius:50%;background:${color};margin-top:5px;flex-shrink:0"></div>
      <div style="flex:1;min-width:0">
        <div style="font-size:12px;font-weight:500;margin-bottom:2px">${escapeHtml(e.title || e.kind || '')}</div>
        <div style="font-size:11px;color:var(--text-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(e.detail || e.message || '')}</div>
      </div>
      <div style="font-size:10.5px;color:var(--text-3);white-space:nowrap">${fmt.ts(e.timestamp)}</div>
    </div>`;
  }).join('');
}

function renderEventsSummary(events = state.lastEvents || []) {
  const today = new Date().toDateString();
  const todayCount = events.filter(e => e.timestamp && new Date(e.timestamp).toDateString() === today).length || events.length;
  const alerts = events.filter(e => e.kind === 'error' || e.kind === 'risk').length;
  const automated = events.length ? Math.round((events.filter(e => e.kind !== 'error').length / events.length) * 1000) / 10 : 0;
  setText('evToday', todayCount);
  setText('evAlert', alerts);
  setText('evAuto', events.length ? `${automated}%` : '-');
  const subs = qsa('#page-events .kpi-sub');
  subs.forEach(s => { s.className = 'kpi-sub'; s.textContent = '来自 /api/events'; });
  const countLabel = qs('#page-events #evTimeline')?.nextElementSibling?.querySelector('span');
  if (countLabel) countLabel.textContent = `共 ${state.lastEvents.length} 条`;
}

function renderEventDetail(event) {
  const card = $('eventDetailCard');
  if (!card) return;
  if (!event) {
    card.innerHTML = '<div class="card-head"><div class="card-title">事件详情</div></div><div style="text-align:center;color:var(--text-3);padding:40px">暂无真实事件</div>';
    return;
  }
  const context = event.data && typeof event.data === 'object' ? event.data : {};
  const contextRows = Object.entries(context).slice(0, 10).map(([k, v]) => `<tr><td style="padding:5px 0;color:var(--text-3);width:35%">${escapeHtml(k)}</td><td style="padding:5px 0;font-family:var(--mono);word-break:break-all">${escapeHtml(typeof v === 'object' ? JSON.stringify(v) : v)}</td></tr>`).join('');
  card.innerHTML = `
    <div style="display:flex;align-items:flex-start;justify-content:space-between;padding-bottom:12px;border-bottom:1px solid var(--border);margin-bottom:12px">
      <div><div style="font-size:12px;color:var(--text-3);margin-bottom:3px">事件详情</div><div style="font-size:14px;font-weight:600;display:flex;align-items:center;gap:8px">${escapeHtml(event.title || event.kind || '-')} <span class="tag">${escapeHtml(event.kind || '-')}</span></div><div style="font-size:11.5px;color:var(--text-3);font-family:var(--mono);margin-top:4px">${escapeHtml(event.timestamp || '-')}</div></div>
    </div>
    <div style="padding:12px;background:var(--surface-2);border-radius:8px;border-left:3px solid var(--purple);margin-bottom:14px;font-size:12.5px;line-height:1.6;color:var(--text)">${escapeHtml(event.detail || event.message || '-')}</div>
    <div style="margin-bottom:14px"><div style="font-size:11.5px;color:var(--text-3);margin-bottom:8px;font-weight:500">结构化上下文</div><table style="width:100%;font-size:11.5px;border-collapse:collapse">${contextRows || '<tr><td style="padding:12px;color:var(--text-3)">该事件未返回额外上下文字段</td></tr>'}</table></div>
    <div style="display:flex;justify-content:space-between;padding-top:10px;border-top:1px solid var(--border);font-size:10.5px;color:var(--text-3)"><span>来源: /api/events</span><span>事件ID: ${escapeHtml(event.id || event.event_id || '-')}</span></div>`;
}

pageHooks.events = function () {
  if (!state.keysReady) return;
  renderEventsTimeline();
};

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function renderDashNotifications(events) {
  const el = document.getElementById('dashNotifications');
  if (!el) return;
  const recent = events.filter(e => e.kind === 'signal' || e.kind === 'order' || e.kind === 'error' || e.kind === 'risk').slice(0, 3);
  if (!recent.length) { el.innerHTML = '<div style="padding:8px;color:var(--text-3)">暂无通知</div>'; return; }
  el.innerHTML = recent.map(e => {
    const dotColor = e.kind === 'error' ? 'var(--red)' : e.kind === 'signal' ? 'var(--green)' : 'var(--purple)';
    const ts = e.timestamp ? new Date(e.timestamp).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'}) : '';
    return `<div class="notif-item"><span class="notif-dot" style="background:${dotColor}"></span><span class="notif-text">${e.message || ''}</span><span class="notif-time">${ts}</span></div>`;
  }).join('');
}

// ============== CHAT ==============
function wireChat() {
  const input = $('chatInput');
  const btn = $('btnChatSend');
  if (!input || !btn) return;
  updateChatModelPill();
  resetChatMessages();

  const send = async () => {
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    appendChatMsg('user', text);
    try {
      const provider = $('llmProvider')?.value || 'anthropic';
      const apiKey = ($('llmKey')?.value || '').trim();
      const model = $('llmModelSelect')?.value || '';
      const baseUrl = ($('llmBaseUrl')?.value || '').trim();
      const j = await api('POST', '/api/agent/chat', {
        question: text,
        provider,
        api_key: apiKey,
        model,
        custom_base_url: baseUrl || undefined,
      });
      appendChatMsg('agent', j.answer || j.reply || JSON.stringify(j));
    } catch (e) {
      appendChatMsg('agent', '抱歉，请求失败: ' + e.message);
    }
  };

  btn.addEventListener('click', send);
  input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
  $('btnChatClear')?.addEventListener('click', resetChatMessages);

  // Quick prompts
  qsa('#page-chat button[style*="text-align:left"]').forEach(b => {
    b.addEventListener('click', () => { input.value = b.textContent.replace(/^[^\s]+\s/, ''); input.focus(); });
  });
}

function resetChatMessages() {
  const container = $('chatMessages');
  if (!container) return;
  container.innerHTML = '';
  appendChatMsg('agent', CHAT_WELCOME);
}

function appendChatMsg(role, text) {
  const container = $('chatMessages');
  if (!container) return;
  const isUser = role === 'user';
  const avatar = isUser
    ? '<div style="width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,#e17055,#fab1a0);display:grid;place-items:center;flex-shrink:0;color:#fff;font-size:12px;font-weight:700">U</div>'
    : '<div style="width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,#6c5ce7,#a29bfe);display:grid;place-items:center;flex-shrink:0"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" style="width:16px;height:16px"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/></svg></div>';
  const bubble = isUser
    ? `<div style="flex:1;max-width:70%;background:var(--surface-2);border-radius:12px 0 12px 12px;padding:12px 14px;font-size:12.5px;line-height:1.7;margin-left:auto">${escapeHtml(text)}</div>`
    : `<div style="flex:1;background:var(--purple-soft);border-radius:0 12px 12px 12px;padding:12px 14px;font-size:12.5px;line-height:1.7"><div style="font-weight:600;margin-bottom:4px;color:var(--purple-dark)">策略 Agent</div>${escapeHtml(text).replace(/\n/g, '<br>')}</div>`;
  const div = document.createElement('div');
  div.style.cssText = `display:flex;gap:10px;align-items:flex-start;${isUser ? 'flex-direction:row-reverse' : ''}`;
  div.innerHTML = avatar + bubble;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

// ============== STRATEGY PAGE ==============
pageHooks.strategy = function () {
  if (!state.keysReady) return;
  const chart = getChart('strategyPreviewChart');
  if (chart) {
    const klines = state.lastStatus?.last_snapshot?.market?.klines || [];
    const data = klines.map((k, i) => [i + 1, +k.close]);
    chart.setOption({
      grid: { left: 40, right: 14, top: 10, bottom: 24 },
      xAxis: { type: 'value', show: false },
      yAxis: { type: 'value', ...AXIS },
      series: [{ type: 'line', smooth: true, showSymbol: false, data, lineStyle: { color: '#6c5ce7', width: 2 }, areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(108,92,231,.15)' }, { offset: 1, color: 'rgba(108,92,231,0)' }] } } }],
      graphic: data.length ? [] : [{ type: 'text', left: 'center', top: 'middle', style: { text: '点击市场页获取实时行情后显示', fontSize: 13, fill: '#9ea2ab', fontFamily: 'inherit' } }],
    }, true);
  }
};

// ============== AGENT CONTROL ==============
function wireAgentControl() {
  const startBtn = $('btnStart');
  const stopBtn = $('btnStop');
  if (startBtn) startBtn.addEventListener('click', async () => {
    const pk = state.pk || ($('pk')?.value || '').trim();
    if (!pk) { toast('请先在设置中填写钱包私钥', 'err'); return; }
    try {
      await api('POST', '/api/agent/start', {
        pk,
        llm_review_api_key: ($('llmReviewKey')?.value || '').trim(),
      });
      toast('Agent 已启动', 'ok');
      pollStatus();
    } catch (e) { toast('启动失败: ' + e.message, 'err'); }
  });
  if (stopBtn) stopBtn.addEventListener('click', async () => {
    try {
      await api('POST', '/api/agent/stop');
      toast('Agent 已停止', 'info');
      pollStatus();
    } catch (e) { toast('停止失败: ' + e.message, 'err'); }
  });
}

// ============== X (TWITTER) PAGE ==============
async function checkXStatus() {
  try {
    const j = await api('GET', '/api/x/status');
    state.xConfigured = !!j.configured;
    updateEmptyStates();
    updateXMeta(j);
    const statusEl = $('xConnStatus');
    if (statusEl) {
      statusEl.textContent = state.xConfigured ? '✓ 已连接' : '未连接';
      statusEl.style.color = state.xConfigured ? 'var(--green-text)' : 'var(--text-3)';
    }
  } catch {}
}

function updateXMeta(info) {
  const metaEl = $('xCacheMeta');
  if (!metaEl) return;
  if (!info || info.cache_age_sec == null) {
    metaEl.textContent = '无缓存数据 — 点击「强制刷新」获取';
    return;
  }
  const age = Math.round(info.cache_age_sec);
  const nextRefresh = Math.max(0, Math.round(info.next_refresh_sec || 0));
  const mins = Math.floor(nextRefresh / 60), secs = nextRefresh % 60;
  const calls = info.api_calls_total || 0;
  const cost = (info.cost_estimate_usd || 0).toFixed(3);
  metaEl.textContent = `缓存 ${age}s 前 · 下次自动刷新 ${mins}m${secs}s · 累计 ${calls} 次 xAI 调用 · 预估费用 $${cost}`;
}

async function connectX() {
  const token = ($('xBearerToken')?.value || '').trim();
  if (!token) { toast('请输入 API Key', 'err'); return; }
  const relay = $('xRelayProvider')?.value || 'xai';
  const customUrl = ($('xCustomBaseUrl')?.value || '').trim();
  const statusEl = $('xConnStatus');
  if (statusEl) { statusEl.textContent = '连接中...'; statusEl.style.color = 'var(--text-3)'; }

  let provider = relay;
  let baseUrl = customUrl || undefined;
  // For openrouter/302ai, the provider ID maps to a known base URL in providers.py
  // For custom, user must provide base URL
  if (relay === 'custom' && !customUrl) {
    toast('自定义中转需要填写 Base URL', 'err'); return;
  }

  try {
    const models = await fetchModels(provider, token, baseUrl);
    // Filter to grok models only (for relay services that return all models)
    const grokModels = models.filter(m => /grok/i.test(m.id));
    const displayModels = grokModels.length > 0 ? grokModels : models;
    const select = $('xModelSelect');
    if (select) {
      select.innerHTML = displayModels.map(m => `<option value="${m.id}">${m.name || m.id}${m.context_length ? ' (' + fmt.num(m.context_length, 0) + ' ctx)' : ''}</option>`).join('');
      select.disabled = false;
    }
    const applyBtn = $('btnXApplyModel');
    if (applyBtn) applyBtn.disabled = false;
    const note = grokModels.length > 0 ? `${grokModels.length} 个 Grok 模型` : `${displayModels.length} 个模型`;
    if (statusEl) { statusEl.textContent = `✓ 连接成功 (${relay}) · ${note}`; statusEl.style.color = 'var(--green-text)'; }
    toast(`连接成功，${note}可用`, 'ok');
  } catch (e) {
    if (statusEl) { statusEl.textContent = '✗ 连接失败: ' + e.message; statusEl.style.color = 'var(--red-text)'; }
    toast('连接失败: ' + e.message, 'err');
  }
}

async function applyXModel() {
  const token = ($('xBearerToken')?.value || '').trim();
  const model = $('xModelSelect')?.value;
  if (!token || !model) { toast('请先测试连接并选择模型', 'err'); return; }
  const relay = $('xRelayProvider')?.value || 'xai';
  const customUrl = ($('xCustomBaseUrl')?.value || '').trim();
  // Resolve base URL for relay providers
  let baseUrl = customUrl || undefined;
  if (!baseUrl && relay === 'openrouter') baseUrl = 'https://openrouter.ai/api/v1';
  else if (!baseUrl && relay === '302ai') baseUrl = 'https://api.302.ai/v1';
  try {
    const body = { api_key: token, model };
    if (baseUrl) body.base_url = baseUrl;
    await Promise.all([
      api('POST', '/api/x/configure', body),
      api('POST', '/api/xai/configure', body),
    ]);
    state.xConfigured = true;
    state.xaiConfigured = true;
    // Save grok config for direct API calls
    state.grok.apiKey = token;
    state.grok.model = model;
    state.grok.baseUrl = (baseUrl || 'https://api.x.ai/v1').replace(/\/$/, '');
    updateEmptyStates();
    const relayLabel = relay === 'xai' ? '官方' : relay;
    toast(`已应用: ${model} (${relayLabel})`, 'ok');
    const statusEl = $('xConnStatus');
    if (statusEl) { statusEl.textContent = `✓ 已连接 (${model} via ${relayLabel}) · 数据+对话 agent 已启用`; statusEl.style.color = 'var(--green-text)'; }
    refreshGrokMeta();
    refreshXUsageStats();
  } catch (e) {
    toast('应用失败: ' + e.message, 'err');
  }
}

async function refreshXUsageStats() {
  const el = $('xUsageStats');
  if (!el) return;
  try {
    const [xStatus, xaiStatus] = await Promise.all([
      api('GET', '/api/x/status'),
      api('GET', '/api/xai/status'),
    ]);
    const calls = (xStatus.api_calls_total || 0) + (xaiStatus.total_calls || 0);
    const cost = ((xStatus.cost_estimate_usd || 0) + (xaiStatus.total_cost_usd || 0)).toFixed(4);
    el.textContent = `累计调用 ${calls} 次 · 预估费用 $${cost}`;
  } catch { el.textContent = '—'; }
}

async function refreshUsageStats() {
  try {
    const [xStatus, xaiStatus, agentStatus] = await Promise.all([
      api('GET', '/api/x/status').catch(() => ({})),
      api('GET', '/api/xai/status').catch(() => ({})),
      api('GET', '/api/agent/status').catch(() => ({})),
    ]);
    // Grok stats
    const xCalls = (xStatus.api_calls_total || 0) + (xaiStatus.total_calls || 0);
    const xCost = ((xStatus.cost_estimate_usd || 0) + (xaiStatus.total_cost_usd || 0)).toFixed(4);
    const xCallsEl = $('usageXCalls');
    const xCostEl = $('usageXCost');
    if (xCallsEl) xCallsEl.textContent = xCalls + ' 次';
    if (xCostEl) xCostEl.textContent = '$' + xCost;

    // Claw402 stats from agent heatmap status
    const heatmap = agentStatus.heatmap || {};
    const claw402Calls = heatmap.liq_map_calls_today || 0;
    const claw402Cost = (heatmap.liq_map_cost_today || 0).toFixed(3);
    const c402CallsEl = $('usageClaw402Calls');
    const c402CostEl = $('usageClaw402Cost');
    if (c402CallsEl) c402CallsEl.textContent = claw402Calls + ' 次';
    if (c402CostEl) c402CostEl.textContent = claw402Cost + ' USDC';

    // LLM stats (from events count as proxy)
    const llmReview = agentStatus.last_llm_review;
    const llmCallsEl = $('usageLlmCalls');
    const llmTokensEl = $('usageLlmTokens');
    if (llmCallsEl) llmCallsEl.textContent = (agentStatus.events_count || 0) + ' 事件';
    if (llmTokensEl) llmTokensEl.textContent = llmReview ? `最近: ${llmReview.decision || '-'}` : '—';
  } catch {}
}

async function fetchModels(provider, apiKey, baseUrl) {
  const body = { provider, api_key: apiKey };
  if (baseUrl) body.base_url = baseUrl;
  const data = await api('POST', '/api/llm/models', body);
  if (data.error) throw new Error(data.error);
  return data.models || [];
}

async function connectLLM() {
  const provider = $('llmProvider')?.value || 'anthropic';
  const apiKey = ($('llmKey')?.value || '').trim();
  const baseUrl = ($('llmBaseUrl')?.value || '').trim();
  if (!apiKey) { toast('请输入 API Key', 'err'); return; }
  const statusEl = $('llmConnStatus');
  if (statusEl) { statusEl.textContent = '连接中...'; statusEl.style.color = 'var(--text-3)'; }
  try {
    const models = await fetchModels(provider, apiKey, baseUrl);
    const select = $('llmModelSelect');
    if (select) {
      select.innerHTML = models.map(m => `<option value="${m.id}" data-ctx="${m.context_length || ''}">${m.name || m.id}${m.context_length ? ' (' + fmt.num(m.context_length, 0) + ')' : ''}</option>`).join('');
      select.disabled = false;
      select.onchange = () => {
        const opt = select.selectedOptions[0];
        const ctx = opt?.dataset?.ctx;
        const ctxInput = $('llmContextLength');
        if (ctxInput && ctx) { ctxInput.value = ctx; ctxInput.disabled = false; }
      };
      select.dispatchEvent(new Event('change'));
      updateChatModelPill();
    }
    const ctxInput = $('llmContextLength');
    if (ctxInput) ctxInput.disabled = false;
    const applyBtn = $('btnLlmApply');
    if (applyBtn) applyBtn.disabled = false;
    if (statusEl) { statusEl.textContent = `✓ 连接成功 · ${models.length} 个模型`; statusEl.style.color = 'var(--green-text)'; }
    toast(`LLM 连接成功 (${provider})`, 'ok');
  } catch (e) {
    if (statusEl) { statusEl.textContent = '✗ ' + e.message; statusEl.style.color = 'var(--red-text)'; }
    toast('连接失败: ' + e.message, 'err');
  }
}

async function connectLLMReview() {
  const provider = $('llmReviewProvider')?.value || 'anthropic';
  const apiKey = ($('llmReviewKey')?.value || '').trim();
  const baseUrl = ($('llmReviewBaseUrl')?.value || '').trim();
  if (!apiKey) { toast('请输入审核模型 API Key', 'err'); return; }
  const statusEl = $('llmReviewConnStatus');
  if (statusEl) { statusEl.textContent = '连接中...'; statusEl.style.color = 'var(--text-3)'; }
  try {
    const models = await fetchModels(provider, apiKey, baseUrl);
    const select = $('llmReviewModelSelect');
    if (select) {
      select.innerHTML = models.map(m => `<option value="${m.id}" data-ctx="${m.context_length || ''}">${m.name || m.id}${m.context_length ? ' (' + fmt.num(m.context_length, 0) + ')' : ''}</option>`).join('');
      select.disabled = false;
      select.onchange = () => {
        const opt = select.selectedOptions[0];
        const ctx = opt?.dataset?.ctx;
        const ctxInput = $('llmReviewContextLength');
        if (ctxInput && ctx) { ctxInput.value = ctx; ctxInput.disabled = false; }
      };
      select.dispatchEvent(new Event('change'));
    }
    const ctxInput = $('llmReviewContextLength');
    if (ctxInput) ctxInput.disabled = false;
    const applyBtn = $('btnLlmReviewApply');
    if (applyBtn) applyBtn.disabled = false;
    if (statusEl) { statusEl.textContent = `✓ 连接成功 · ${models.length} 个模型`; statusEl.style.color = 'var(--green-text)'; }
    toast(`审核 LLM 连接成功 (${provider})`, 'ok');
  } catch (e) {
    if (statusEl) { statusEl.textContent = '✗ ' + e.message; statusEl.style.color = 'var(--red-text)'; }
    toast('连接失败: ' + e.message, 'err');
  }
}

async function connectXPoster() {
  const creds = {
    api_key: ($('xPosterApiKey')?.value || '').trim(),
    api_secret: ($('xPosterApiSecret')?.value || '').trim(),
    access_token: ($('xPosterAccessToken')?.value || '').trim(),
    access_token_secret: ($('xPosterAccessSecret')?.value || '').trim(),
  };
  if (!creds.api_key || !creds.api_secret || !creds.access_token || !creds.access_token_secret) {
    toast('请填写全部 4 个凭证', 'err'); return;
  }
  const statusEl = $('xPosterConnStatus');
  if (statusEl) { statusEl.textContent = '验证中...'; statusEl.style.color = 'var(--text-2)'; }
  try {
    const j = await api('POST', '/api/x/poster/configure', creds);
    state.xPosterConfigured = !!j.configured;
    if (statusEl && j.account) {
      statusEl.textContent = `✓ 已连接 @${j.account.username}`;
      statusEl.style.color = 'var(--green-text)';
    }
    toast(`已连接 @${j.account?.username || 'X account'}`, 'ok');
  } catch (e) {
    if (statusEl) { statusEl.textContent = '✗ 验证失败'; statusEl.style.color = 'var(--red-text)'; }
    toast('验证失败: ' + e.message, 'err');
  }
}

async function checkXPosterStatus() {
  try {
    const j = await api('GET', '/api/x/poster/status');
    state.xPosterConfigured = !!j.configured;
    const statusEl = $('xPosterConnStatus');
    if (statusEl && j.configured) {
      statusEl.textContent = '✓ 已连接';
      statusEl.style.color = 'var(--green-text)';
    }
  } catch {}
}

async function loadXData(force = false) {
  if (!state.xConfigured) return;
  try {
    const url = force ? '/api/x/data?force=1' : '/api/x/data';
    const j = await api('GET', url);
    state.xLastData = {
      sentiment: j.sentiment,
      trending: j.trending || [],
      tweets: j.top_tweets || [],
      narrative: j.narrative || '',
      meta: j.meta || {},
    };
    renderXPage();
    // Auto-fill the AI analysis box since Grok already returned narrative
    if ($('xAnalysis') && j.narrative) $('xAnalysis').textContent = j.narrative;
    checkXStatus();
  } catch (e) {
    toast('加载 X 数据失败: ' + e.message, 'err');
  }
}

function renderXPage() {
  const data = state.xLastData;
  if (!data) return;
  const { sentiment: s, trending, tweets, meta } = data;

  // KPIs
  if ($('xKpiScore')) $('xKpiScore').textContent = s?.score ?? '—';
  if ($('xKpiLabel')) {
    const label = s?.label || '—';
    const labelMap = { bullish: '看涨', bearish: '看跌', neutral: '中性' };
    $('xKpiLabel').textContent = labelMap[label] || label;
    $('xKpiLabel').style.color = label === 'bullish' ? 'var(--green-text)' : label === 'bearish' ? 'var(--red-text)' : 'var(--text-3)';
  }

  // Grok returns sentiment.summary + total_analyzed; derive pos/neg breakdown from trending
  const bullCount = trending.filter(c => c.label === 'bullish').length;
  const bearCount = trending.filter(c => c.label === 'bearish').length;
  const neuCount = trending.filter(c => c.label === 'neutral').length;
  const totalCoins = trending.length || 1;

  if ($('xKpiPos')) $('xKpiPos').textContent = bullCount;
  if ($('xKpiPosPct')) $('xKpiPosPct').textContent = ((bullCount / totalCoins) * 100).toFixed(0) + '%';
  if ($('xKpiNeg')) $('xKpiNeg').textContent = bearCount;
  if ($('xKpiNegPct')) $('xKpiNegPct').textContent = ((bearCount / totalCoins) * 100).toFixed(0) + '%';
  if ($('xKpiTotal')) $('xKpiTotal').textContent = s?.total_analyzed ?? meta?.sources_used ?? '—';
  if ($('xBarPos')) $('xBarPos').textContent = bullCount;
  if ($('xBarNeu')) $('xBarNeu').textContent = neuCount;
  if ($('xBarNeg')) $('xBarNeg').textContent = bearCount;
  if ($('xLastUpdate') && meta?.fetched_at) {
    $('xLastUpdate').textContent = new Date(meta.fetched_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  // Gauge
  const gauge = getChart('xSentimentGauge');
  if (gauge) {
    gauge.setOption({
      series: [{
        type: 'gauge', startAngle: 180, endAngle: 0, min: 0, max: 100,
        pointer: { show: true, length: '65%', width: 5, itemStyle: { color: '#6c5ce7' } },
        axisLine: { lineStyle: { width: 14, color: [[0.25, '#e74c3c'], [0.5, '#f39c12'], [0.75, '#00b894'], [1, '#0984e3']] } },
        axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
        detail: { formatter: '{value}', fontSize: 28, fontWeight: 700, fontFamily: '"JetBrains Mono",monospace', color: '#6c5ce7', offsetCenter: [0, '30%'] },
        title: { show: false },
        data: [{ value: s?.score ?? 50 }],
      }],
    }, true);
  }

  // Trending coins — Grok schema: {coin, mentions, sentiment_pct, label, headline}
  const trendList = $('xTrendingList');
  if (trendList) {
    if (!trending.length) {
      trendList.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-3);font-size:12px">暂无热门币种</div>';
    } else {
      const maxMentions = Math.max(...trending.map(c => c.mentions || 0), 1);
      trendList.innerHTML = trending.slice(0, 15).map((c, i) => {
        const labelColor = c.label === 'bullish' ? 'var(--green)' : c.label === 'bearish' ? 'var(--red)' : 'var(--text-3)';
        const labelText = { bullish: '看涨', bearish: '看跌', neutral: '中性' }[c.label] || c.label;
        const barWidth = (c.mentions / maxMentions) * 100;
        return `<div style="padding:10px 12px;background:var(--surface-2);border-radius:8px">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
            <div style="display:flex;align-items:center;gap:8px">
              <span style="font-size:11px;color:var(--text-3);font-family:var(--mono);width:20px">#${i + 1}</span>
              <span style="font-size:13px;font-weight:700">${escapeHtml(c.coin || '')}</span>
              <span style="padding:1px 6px;background:${labelColor};color:#fff;border-radius:3px;font-size:9.5px;font-weight:600">${labelText}</span>
            </div>
            <span style="font-size:11px;font-family:var(--mono);color:var(--text-2);font-weight:600">${c.mentions || 0} 条</span>
          </div>
          <div style="height:4px;background:var(--border);border-radius:2px;overflow:hidden;margin-bottom:4px"><div style="width:${barWidth}%;height:100%;background:${labelColor}"></div></div>
          ${c.headline ? `<div style="font-size:10.5px;color:var(--text-2);line-height:1.5;margin-top:4px">${escapeHtml(c.headline)}</div>` : ''}
          <div style="display:flex;justify-content:space-between;font-size:10.5px;color:var(--text-3);margin-top:4px">
            <span>情绪分 <span style="font-family:var(--mono);font-weight:600;color:var(--text-2)">${c.sentiment_pct ?? '—'}</span></span>
          </div>
        </div>`;
      }).join('');
    }
  }

  // Top tweets — Grok schema: {author, handle, text, sentiment_label, url, engagement_estimate}
  const tweetsList = $('xTweetsList');
  if (tweetsList) {
    if (!tweets.length) {
      tweetsList.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-3);font-size:12px">暂无推文</div>';
    } else {
      const engIcon = { viral: '🔥 爆款', high: '↑ 高', medium: '→ 中', low: '↓ 低' };
      tweetsList.innerHTML = tweets.map(t => {
        const sLabel = t.sentiment_label === 'positive' ? '正面' : t.sentiment_label === 'negative' ? '负面' : '中性';
        const sColor = t.sentiment_label === 'positive' ? 'var(--green)' : t.sentiment_label === 'negative' ? 'var(--red)' : 'var(--text-3)';
        const initial = (t.author || 'X').trim()[0] || 'X';
        const engText = engIcon[t.engagement_estimate] || t.engagement_estimate || '';
        return `<div style="padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--surface-2)">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
            <div style="display:flex;align-items:center;gap:8px;min-width:0;flex:1">
              <div style="width:28px;height:28px;border-radius:50%;background:linear-gradient(135deg,#6c5ce7,#a29bfe);display:grid;place-items:center;color:#fff;font-size:11px;font-weight:700;flex-shrink:0">${escapeHtml(initial)}</div>
              <div style="min-width:0;flex:1"><div style="font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(t.author || '')}</div><div style="font-size:10.5px;color:var(--text-3)">@${escapeHtml(t.handle || '')}</div></div>
            </div>
            <span style="padding:2px 7px;background:${sColor};color:#fff;border-radius:3px;font-size:10px;font-weight:600;flex-shrink:0">${sLabel}</span>
          </div>
          <div style="font-size:12px;color:var(--text);line-height:1.6;margin-bottom:8px">${escapeHtml((t.text || '').slice(0, 280))}${(t.text || '').length > 280 ? '…' : ''}</div>
          <div style="display:flex;align-items:center;gap:12px;font-size:10.5px;color:var(--text-3);font-family:var(--mono)">
            ${engText ? `<span>互动 ${engText}</span>` : ''}
            ${t.url ? `<a href="${escapeHtml(t.url)}" target="_blank" style="margin-left:auto;color:var(--purple);text-decoration:none">查看 ↗</a>` : ''}
          </div>
        </div>`;
      }).join('');
    }
  }
}

async function runXAIAnalysis() {
  if (!state.xConfigured) { toast('请先连接 xAI API', 'err'); return; }
  const analysisEl = $('xAnalysis');
  if (analysisEl) analysisEl.textContent = '分析中...';
  try {
    // Uses cached Grok data - narrative already generated in fetch_all()
    const j = await api('POST', '/api/x/analyze', {});
    const text = j.narrative || j.analysis || '暂无分析';
    if (analysisEl) analysisEl.textContent = text;
    // Refresh the full page too since analyze returns all fields
    if (j.overview) {
      state.xLastData = {
        sentiment: j.overview,
        trending: j.trending || [],
        tweets: j.top_tweets || [],
        narrative: text,
        meta: j.meta || {},
      };
      renderXPage();
    }
  } catch (e) {
    if (analysisEl) analysisEl.textContent = '分析失败: ' + e.message;
  }
}

pageHooks.xtrend = function () {
  if (state.xConfigured && !state.xLastData) loadXData();
  checkXStatus(); // refresh cache age display
};

// ============== GROK ANALYST CHAT PAGE ==============
async function refreshGrokMeta() {
  try {
    const j = await api('GET', '/api/xai/status');
    state.xaiConfigured = !!j.configured;
    updateEmptyStates();
    if ($('grokModelPill')) $('grokModelPill').textContent = '模型: ' + (j.model || '—');
    if ($('grokModelMeta')) $('grokModelMeta').textContent = j.model || '—';
    if ($('grokSysPromptChars')) $('grokSysPromptChars').textContent = (j.system_prompt_chars || 0).toLocaleString() + ' chars';
    if ($('grokSessionCount')) $('grokSessionCount').textContent = j.sessions || 0;
    if ($('grokTotalCalls')) $('grokTotalCalls').textContent = j.total_calls || 0;
    if ($('grokTotalCost')) $('grokTotalCost').textContent = '$' + (j.total_cost_usd || 0).toFixed(4);
    if ($('grokCostMeta')) $('grokCostMeta').textContent = `累计 ${j.total_calls || 0} 次调用 · $${(j.total_cost_usd || 0).toFixed(4)}`;
  } catch {}
}

function appendGrokMsg(role, text, meta, dataButtonId) {
  const container = $('grokMessages');
  if (!container) return;
  const isUser = role === 'user';
  const avatar = isUser
    ? '<div style="width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,#e17055,#fab1a0);display:grid;place-items:center;flex-shrink:0;color:#fff;font-size:12px;font-weight:700">U</div>'
    : '<div style="width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,#6c5ce7,#a29bfe);display:grid;place-items:center;flex-shrink:0"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" style="width:16px;height:16px"><circle cx="12" cy="12" r="10"/><circle cx="9" cy="10" r="1" fill="currentColor"/><circle cx="15" cy="10" r="1" fill="currentColor"/><path d="M8 15s1.5 2 4 2 4-2 4-2"/></svg></div>';
  const metaLine = meta ? `<div style="font-size:10.5px;color:var(--text-3);margin-top:6px;padding-top:6px;border-top:1px solid rgba(0,0,0,.05)">${escapeHtml(meta)}</div>` : '';
  const dataBtn = dataButtonId ? `<button onclick="showGrokData('${dataButtonId}')" style="margin-top:8px;padding:5px 12px;border:1px solid var(--purple);border-radius:6px;background:var(--purple-soft);color:var(--purple-dark);font-size:11px;cursor:pointer;font-weight:600">📊 查看数据面板</button>` : '';
  const displayText = isUser ? text : stripJsonBlocks(text);
  const bubble = isUser
    ? `<div style="flex:1;max-width:75%;background:var(--surface-2);border-radius:12px 0 12px 12px;padding:12px 14px;font-size:12.5px;line-height:1.7;margin-left:auto">${escapeHtml(text)}</div>`
    : `<div style="flex:1;background:var(--purple-soft);border-radius:0 12px 12px 12px;padding:12px 14px;font-size:12.5px;line-height:1.7"><div style="font-weight:600;margin-bottom:4px;color:var(--purple-dark)">Grok 分析师</div><div style="white-space:pre-wrap">${escapeHtml(displayText)}</div>${metaLine}${dataBtn}</div>`;
  const div = document.createElement('div');
  div.style.cssText = `display:flex;gap:10px;align-items:flex-start;${isUser ? 'flex-direction:row-reverse' : ''}`;
  div.innerHTML = avatar + bubble;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function showGrokData(id) {
  if (state.grok.lastStructuredId === id && state.grok.lastStructured) {
    renderGrokStructured(state.grok.lastStructured);
    // Scroll report card into view
    const card = $('grokReportCard');
    if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function stripJsonBlocks(text) {
  // Remove JSON code fences (they're rendered separately as the report card)
  return String(text).replace(/```(?:json)?\s*\{[\s\S]*?\}\s*```/g, '').trim();
}

async function sendGrokMessage() {
  if (!state.grok.apiKey) { toast('请先在设置中配置 xAI API Key 并点击应用', 'err'); return; }
  const input = $('grokInput');
  const text = (input?.value || '').trim();
  if (!text) return;
  input.value = '';
  const forceLive = $('grokForceLive')?.checked || false;
  const isOfficialXai = state.grok.baseUrl.includes('api.x.ai');

  appendGrokMsg('user', text);
  const thinking = document.createElement('div');
  thinking.id = 'grokThinking';
  thinking.style.cssText = 'display:flex;gap:10px;align-items:flex-start;opacity:.6';
  thinking.innerHTML = '<div style="width:32px;height:32px;border-radius:8px;background:var(--purple-soft);display:grid;place-items:center;flex-shrink:0"><div style="width:10px;height:10px;border-radius:50%;background:var(--purple);animation:pulse 1s infinite"></div></div><div style="flex:1;padding:12px 14px;font-size:12.5px;color:var(--text-3)">Grok 正在思考...</div>';
  $('grokMessages').appendChild(thinking);
  $('grokMessages').scrollTop = $('grokMessages').scrollHeight;

  // Load system prompt once
  if (!state.grok.systemPrompt) {
    try {
      const r = await fetch('/api/xai/system-prompt');
      const s = await r.json();
      if (s.prompt) state.grok.systemPrompt = s.prompt;
    } catch {}
    if (!state.grok.systemPrompt) state.grok.systemPrompt = 'You are a crypto X sentiment analyst. Reply in Chinese.';
  }

  // Build conversation history (last 10 turns)
  if (!state.grok.history) state.grok.history = [];
  const messages = [
    { role: 'system', content: state.grok.systemPrompt },
    ...state.grok.history.slice(-10),
    { role: 'user', content: text },
  ];

  const body = { model: state.grok.model, messages, temperature: 0.5 };
  if (forceLive && isOfficialXai) {
    const now = new Date();
    body.search_parameters = {
      mode: 'on', sources: [{ type: 'x' }],
      from_date: new Date(now - 6 * 3600000).toISOString().slice(0, 10),
      to_date: now.toISOString().slice(0, 10),
      max_search_results: 20, return_citations: true,
    };
  }

  try {
    const resp = await fetch(`${state.grok.baseUrl}/chat/completions`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${state.grok.apiKey}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.text();
      throw new Error(`${resp.status}: ${err.slice(0, 200)}`);
    }
    const data = await resp.json();
    thinking.remove();

    const fullReply = (data.choices?.[0]?.message?.content || '').trim();
    const usage = data.usage || {};
    const costTag = `本次: ~$${((usage.prompt_tokens||0)/1e6*0.2+(usage.completion_tokens||0)/1e6*0.5).toFixed(5)} (${usage.prompt_tokens||0}→${usage.completion_tokens||0} tokens)`;

    // Extract structured JSON block from reply
    let structuredData = null;
    let displayReply = fullReply;
    const jsonMatch = fullReply.match(/```(?:json)?\s*(\{[\s\S]*?\})\s*```/);
    if (jsonMatch) {
      try {
        structuredData = JSON.parse(jsonMatch[1]);
        // Strip the JSON block from displayed text
        displayReply = fullReply.replace(jsonMatch[0], '').trim();
      } catch {}
    }

    // Append message with optional "查看数据" button
    const msgId = 'grok-data-' + Date.now();
    appendGrokMsg('assistant', displayReply, costTag, structuredData ? msgId : null);

    if (structuredData) {
      // Store for the button click
      state.grok.lastStructured = structuredData;
      state.grok.lastStructuredId = msgId;
    }

    // Save to local history (save full reply including JSON for context)
    state.grok.history.push({ role: 'user', content: text });
    state.grok.history.push({ role: 'assistant', content: fullReply });
    if (state.grok.history.length > 40) state.grok.history = state.grok.history.slice(-40);
  } catch (e) {
    thinking.remove();
    appendGrokMsg('assistant', '请求失败: ' + e.message);
  }
}

function renderGrokStructured(data) {
  // Detect tweet drafts
  if (data.candidates && Array.isArray(data.candidates)) {
    renderGrokDrafts(data);
  } else {
    renderGrokReport(data);
  }
}

function renderGrokReport(report) {
  const card = $('grokReportCard');
  const body = $('grokReportBody');
  if (!card || !body) return;
  card.style.display = '';
  state.lastGrokReport = report;

  const section = (label, content) => `<div class="grok-report-section"><div class="grok-report-label">${label}</div><div class="grok-report-value">${content}</div></div>`;

  let html = '';
  if (report.headline) {
    html += `<div style="padding:10px;background:var(--purple-soft);border-radius:6px;margin-bottom:10px;font-weight:600;color:var(--purple-dark);font-size:13px">${escapeHtml(report.headline)}</div>`;
  }
  if (report.sentiment) {
    const s = report.sentiment;
    const labelColors = { bullish: 'var(--green)', bearish: 'var(--red)', neutral: 'var(--text-3)', volatile: 'var(--amber)' };
    const labelTexts = { bullish: '看涨', bearish: '看跌', neutral: '中性', volatile: '剧烈波动' };
    html += section('情绪指标',
      `<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <span style="font-size:20px;font-weight:700;font-family:var(--mono);color:${labelColors[s.label] || 'var(--text)'}">${s.overall_score ?? '—'}</span>
        <span style="padding:2px 8px;background:${labelColors[s.label] || 'var(--text-3)'};color:#fff;border-radius:4px;font-size:10px;font-weight:600">${labelTexts[s.label] || s.label}</span>
        <span style="font-size:10.5px;color:var(--text-3)">置信度 ${((s.confidence || 0) * 100).toFixed(0)}%</span>
      </div>`);
  }
  if (report.key_narratives?.length) {
    html += section('关键叙事', report.key_narratives.slice(0, 3).map(n =>
      `<div style="padding:6px;background:var(--surface-2);border-radius:5px;margin-bottom:4px">
        <div style="font-weight:600;font-size:11.5px;margin-bottom:2px">${escapeHtml(n.theme || '')}</div>
        <div style="font-size:11px;color:var(--text-2);line-height:1.5">${escapeHtml(n.summary || '')}</div>
        ${n.coins_involved?.length ? `<div style="margin-top:3px;display:flex;gap:3px;flex-wrap:wrap">${n.coins_involved.map(c => `<span style="padding:1px 5px;background:var(--purple-soft);color:var(--purple-dark);border-radius:3px;font-size:9.5px;font-weight:600">${escapeHtml(c)}</span>`).join('')}</div>` : ''}
      </div>`).join(''));
  }
  if (report.trending_coins?.length) {
    html += section('热度币种', report.trending_coins.slice(0, 5).map(c => {
      const delta = c.mention_delta_pct ?? 0;
      const deltaColor = delta > 0 ? 'var(--green-text)' : delta < 0 ? 'var(--red-text)' : 'var(--text-3)';
      return `<div style="display:flex;align-items:center;justify-content:space-between;padding:4px 6px;border-bottom:1px solid var(--border);font-size:11px">
        <span style="font-weight:700">${escapeHtml(c.symbol || '')}</span>
        <span style="font-family:var(--mono);color:${deltaColor}">${delta > 0 ? '+' : ''}${delta}%</span>
        <span style="color:var(--text-2);font-size:10.5px;flex:1;text-align:right;margin-left:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(c.key_driver || '')}</span>
      </div>`;
    }).join(''));
  }
  if (report.risks?.length) {
    html += section('风险提示', report.risks.slice(0, 3).map(r =>
      `<div style="font-size:11px;color:var(--red-text);margin-bottom:3px">⚠ ${escapeHtml(r.description || '')}</div>`).join(''));
  }
  if (report.opportunities?.length) {
    html += section('机会点', report.opportunities.slice(0, 3).map(o =>
      `<div style="font-size:11px;color:var(--green-text);margin-bottom:3px">✦ ${escapeHtml(o.description || '')}</div>`).join(''));
  }
  if (report.actionable_signals?.length) {
    html += section('可行动信号', report.actionable_signals.slice(0, 4).map(s =>
      `<div style="padding:6px;background:var(--amber-soft);border-left:2px solid var(--amber);border-radius:4px;margin-bottom:4px">
        <div style="font-size:11px;font-weight:600">${escapeHtml(s.signal || '')}</div>
        <div style="font-size:10.5px;color:var(--text-2);margin-top:2px">→ ${escapeHtml(s.suggested_action || '')}</div>
      </div>`).join(''));
  }
  body.innerHTML = html;
}

function renderGrokDrafts(data) {
  const card = $('grokDraftsCard');
  const body = $('grokDraftsBody');
  const meta = $('grokDraftsMeta');
  if (!card || !body) return;
  card.style.display = '';
  if (meta) meta.textContent = `${(data.candidates || []).length} 条候选`;

  body.innerHTML = (data.candidates || []).map((c, i) => `
    <div class="grok-draft-card">
      <div class="grok-draft-meta">
        <span>候选 #${i + 1} · ${escapeHtml(c.style || '')} · ${escapeHtml(c.target_audience || '')}</span>
        <span style="font-family:var(--mono);font-weight:600">${c.char_count || (c.text || '').length}/280</span>
      </div>
      <div class="grok-draft-text">${escapeHtml(c.text || '')}</div>
      ${c.hashtags?.length ? `<div style="margin-bottom:6px;font-size:10.5px;color:var(--purple)">${c.hashtags.map(h => escapeHtml(h)).join(' ')}</div>` : ''}
      <div class="grok-draft-actions">
        <button class="grok-draft-btn" onclick="postGrokDraft(${i}, true)">预览</button>
        <button class="grok-draft-btn primary" onclick="postGrokDraft(${i}, false)">发布到 X</button>
      </div>
    </div>
  `).join('');

  state.lastGrokDrafts = data.candidates || [];
}

async function postGrokDraft(index, dryRun) {
  const drafts = state.lastGrokDrafts || [];
  const draft = drafts[index];
  if (!draft) return;
  if (!dryRun && !state.xPosterConfigured) {
    toast('请先在设置中配置 X 发推凭证', 'err');
    return;
  }
  if (!dryRun && !confirm(`确认发布到 X 吗?\n\n"${draft.text}"`)) return;

  try {
    const j = await api('POST', '/api/x/poster/post', { text: draft.text, dry_run: dryRun });
    if (dryRun) {
      toast(`预览: ${j.char_count} 字符`, 'info');
    } else if (j.id) {
      toast(`已发布! 查看: ${j.url}`, 'ok');
      window.open(j.url, '_blank');
    } else {
      toast('发布成功', 'ok');
    }
  } catch (e) {
    toast('发布失败: ' + e.message, 'err');
  }
}

async function resetGrokSession() {
  if (!confirm('清空当前对话?')) return;
  try {
    await api('POST', '/api/xai/reset', { session_id: 'default' });
    $('grokMessages').innerHTML = '';
    // Re-add welcome message
    $('grokMessages').innerHTML = `
      <div style="display:flex;gap:10px;align-items:flex-start">
        <div style="width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,#6c5ce7,#a29bfe);display:grid;place-items:center;flex-shrink:0"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" style="width:16px;height:16px"><circle cx="12" cy="12" r="10"/><circle cx="9" cy="10" r="1" fill="currentColor"/><circle cx="15" cy="10" r="1" fill="currentColor"/><path d="M8 15s1.5 2 4 2 4-2 4-2"/></svg></div>
        <div style="flex:1;background:var(--purple-soft);border-radius:0 12px 12px 12px;padding:12px 14px;font-size:12.5px;line-height:1.7">
          <div style="font-weight:600;margin-bottom:4px;color:var(--purple-dark)">Grok 分析师</div>对话已重置。有什么想了解的？
        </div>
      </div>`;
    $('grokReportCard').style.display = 'none';
    $('grokDraftsCard').style.display = 'none';
  } catch (e) { toast('重置失败: ' + e.message, 'err'); }
}

pageHooks.grok = function () {
  refreshGrokMeta();
};


window.addEventListener('resize', () => {
  Object.values(state.charts).forEach(c => c && c.resize && c.resize());
});

// ============== TWEET PIPELINE ==============
let pipelineBusy = false;

async function pipelineGenerate() {
  if (pipelineBusy) return;
  pipelineBusy = true;
  const btn = $('btnPipelineGenerate');
  const statusEl = $('pipelineStatus');
  if (btn) btn.disabled = true;
  if (statusEl) statusEl.textContent = '生成中...';

  const body = {};
  const provider = $('pipelineProvider')?.value;
  const apiKey = ($('pipelineApiKey')?.value || '').trim();
  const model = ($('pipelineModel')?.value || '').trim();
  const baseUrl = ($('pipelineBaseUrl')?.value || '').trim();
  const imageProvider = $('pipelineImageProvider')?.value;
  const imageApiKey = ($('pipelineImageApiKey')?.value || '').trim();
  const imageModel = ($('pipelineImageModel')?.value || '').trim();
  const imageBaseUrl = ($('pipelineImageBaseUrl')?.value || '').trim();
  if (provider) body.provider = provider;
  if (apiKey) body.api_key = apiKey;
  if (model) body.model = model;
  if (baseUrl) body.custom_base_url = baseUrl;
  if (imageProvider) body.image_provider = imageProvider;
  if (imageApiKey) body.image_api_key = imageApiKey;
  if (imageModel) body.image_model = imageModel;
  if (imageBaseUrl) body.image_custom_base_url = imageBaseUrl;

  try {
    const data = await api('POST', '/api/x/pipeline/generate', body);
    pipelineRenderCandidates(data);
    toast('推文生成完成', 'ok');
  } catch (e) {
    toast('生成失败: ' + e.message, 'err');
  } finally {
    pipelineBusy = false;
    if (btn) btn.disabled = false;
    if (statusEl) statusEl.textContent = '就绪';
  }
}

function pipelineRenderCandidates(data) {
  const container = $('pipelineCandidates');
  const emptyEl = $('pipelineEmpty');
  const ctxEl = $('pipelineContext');
  const ctxText = $('pipelineContextText');
  const costEl = $('pipelineCost');
  if (!container) return;

  const candidates = data.candidates || [];
  if (!candidates.length) {
    container.innerHTML = '';
    if (emptyEl) emptyEl.style.display = '';
    if (ctxEl) ctxEl.style.display = 'none';
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';
  if (ctxEl) ctxEl.style.display = '';
  if (ctxText) ctxText.textContent = data.context_summary || '—';
  if (costEl) costEl.textContent = `费用 $${(data.cost_usd || 0).toFixed(4)}`;

  if (data.image_error) toast('图片 LLM 失败，已使用本地市场图卡: ' + data.image_error, 'info', 4200);

  const styleColors = {
    '理性分析': { bg: 'var(--blue-soft)', color: 'var(--blue)', border: 'var(--blue)' },
    '激进观点': { bg: 'var(--red-soft)', color: 'var(--red-text)', border: 'var(--red)' },
    '幽默吐槽': { bg: 'var(--amber-soft)', color: 'var(--amber-text)', border: 'var(--amber)' },
  };

  container.innerHTML = candidates.map((c, i) => {
    const sc = styleColors[c.style] || { bg: 'var(--surface-2)', color: 'var(--text-2)', border: 'var(--border)' };
    const imageSvg = pipelineImageSvg(c.image_card || {}, i);
    return `
      <div class="card" style="border-top:3px solid ${sc.border};display:flex;flex-direction:column">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
          <span style="padding:3px 8px;background:${sc.bg};color:${sc.color};border-radius:4px;font-size:10.5px;font-weight:600">${c.style || '风格 ' + (i+1)}</span>
          <span style="font-family:var(--mono);font-size:10.5px;color:var(--text-3)">${c.char_count}/280</span>
        </div>
        <div id="pipelineImage${i}" data-svg="${escHtml(imageSvg)}" style="margin-bottom:12px;border:1px solid var(--border);border-radius:8px;overflow:hidden;background:#111827">${imageSvg}</div>
        <div style="font-size:13px;line-height:1.7;flex:1;margin-bottom:12px;white-space:pre-wrap" id="pipelineText${i}">${escHtml(c.text)}</div>
        <div style="display:flex;gap:4px;font-size:10px;color:var(--text-3);margin-bottom:10px;flex-wrap:wrap">
          ${(c.data_sources || []).map(s => `<span style="padding:2px 6px;background:var(--surface-2);border-radius:3px">${s}</span>`).join('')}
          <span style="padding:2px 6px;background:var(--surface-2);border-radius:3px">置信度 ${((c.confidence || 0) * 100).toFixed(0)}%</span>
        </div>
        <div style="display:flex;gap:6px">
          <button onclick="pipelineManualPost(${i})" style="flex:1;padding:6px;border:1px solid var(--border);border-radius:6px;background:var(--surface);font-size:11px;cursor:pointer">手动</button>
          <button onclick="pipelineEdit(${i})" style="flex:1;padding:6px;border:1px solid var(--border);border-radius:6px;background:var(--surface);font-size:11px;cursor:pointer">编辑</button>
          <button onclick="pipelineDryRun(${i})" style="flex:1;padding:6px;border:1px solid var(--border);border-radius:6px;background:var(--surface);font-size:11px;cursor:pointer">Dry Run</button>
          <button onclick="pipelinePublish(${i})" style="flex:1;padding:6px;border:none;border-radius:6px;background:var(--purple);color:#fff;font-size:11px;font-weight:600;cursor:pointer">发布</button>
        </div>
      </div>`;
  }).join('');

  state.pipelineCandidates = candidates;
}

function escHtml(s) { return escapeHtml(s); }

function pipelineImageSvg(card, idx = 0) {
  const trend = String(card.trend || 'neutral').toLowerCase();
  const accent = trend === 'bullish' ? '#00b894' : trend === 'bearish' ? '#e74c3c' : trend === 'risk' ? '#f39c12' : '#6c5ce7';
  const bullets = Array.isArray(card.bullets) ? card.bullets.slice(0, 4) : [];
  const safe = v => escapeHtml(v == null ? '' : String(v));
  const bulletSvg = bullets.map((b, i) => `<text x="56" y="${258 + i * 34}" fill="#d1d5db" font-size="22" font-family="Arial, sans-serif">• ${safe(b)}</text>`).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 675" width="100%" role="img" aria-label="${safe(card.alt_text || card.title || 'market card')}">
    <defs><linearGradient id="pg${idx}" x1="0" x2="1" y1="0" y2="1"><stop offset="0" stop-color="#111827"/><stop offset="1" stop-color="#1f2937"/></linearGradient></defs>
    <rect width="1200" height="675" fill="url(#pg${idx})"/>
    <rect x="32" y="32" width="1136" height="611" rx="26" fill="rgba(255,255,255,.035)" stroke="rgba(255,255,255,.12)"/>
    <text x="56" y="92" fill="#9ca3af" font-size="20" font-family="Arial, sans-serif">${safe(card.subtitle || '实时市场')}</text>
    <text x="56" y="158" fill="#ffffff" font-size="46" font-weight="700" font-family="Arial, sans-serif">${safe(card.title || '市场观察')}</text>
    <rect x="56" y="202" width="390" height="132" rx="18" fill="rgba(255,255,255,.06)" stroke="${accent}"/>
    <text x="84" y="250" fill="#9ca3af" font-size="20" font-family="Arial, sans-serif">${safe(card.metric_label || '现价')}</text>
    <text x="84" y="306" fill="${accent}" font-size="44" font-weight="700" font-family="Arial, sans-serif">${safe(card.metric_value || '-')}</text>
    ${bulletSvg}
    <path d="M760 420 C820 330,880 470,940 380 S1060 320,1120 260" fill="none" stroke="${accent}" stroke-width="12" stroke-linecap="round"/>
    <circle cx="1120" cy="260" r="12" fill="${accent}"/>
    <text x="56" y="604" fill="#9ca3af" font-size="18" font-family="Arial, sans-serif">${safe(card.risk_note || '非投资建议')}</text>
    <text x="1006" y="604" fill="#9ca3af" font-size="18" font-family="Arial, sans-serif">Liquidation Agent</text>
  </svg>`;
}

function pipelineCandidateSvg(idx) {
  const el = $('pipelineImage' + idx);
  return el?.dataset?.svg || pipelineImageSvg(state.pipelineCandidates?.[idx]?.image_card || {}, idx);
}

function svgToPngDataUrl(svg) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    img.onload = () => {
      try {
        const canvas = document.createElement('canvas');
        canvas.width = 1200;
        canvas.height = 675;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        resolve(canvas.toDataURL('image/png'));
      } catch (e) {
        URL.revokeObjectURL(url);
        reject(e);
      }
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('image render failed')); };
    img.src = url;
  });
}

function pipelineEdit(idx) {
  const el = $('pipelineText' + idx);
  if (!el || !state.pipelineCandidates) return;
  const current = state.pipelineCandidates[idx]?.text || '';
  const newText = prompt('编辑推文内容：', current);
  if (newText !== null && newText.trim()) {
    if (newText.length > 280) { toast('超过 280 字符限制', 'err'); return; }
    state.pipelineCandidates[idx].text = newText.trim();
    state.pipelineCandidates[idx].char_count = newText.trim().length;
    el.textContent = newText.trim();
    const parent = el.closest('.card');
    const charSpan = parent?.querySelector('[style*="mono"]');
    if (charSpan) charSpan.textContent = `${newText.trim().length}/280`;
  }
}

async function pipelineDryRun(idx) {
  if (!state.pipelineCandidates?.[idx]) return;
  const text = state.pipelineCandidates[idx].text;
  try {
    const mediaData = await svgToPngDataUrl(pipelineCandidateSvg(idx));
    const result = await api('POST', '/api/x/pipeline/publish', { text, dry_run: true, media_data: mediaData, media_alt_text: state.pipelineCandidates[idx].image_card?.alt_text || '' });
    toast(`[Dry Run] ${result.char_count || text.length} 字符，预览通过`, 'info');
  } catch (e) {
    toast('Dry Run 失败: ' + e.message, 'err');
  }
}

async function pipelineManualPost(idx) {
  if (!state.pipelineCandidates?.[idx]) return;
  const text = state.pipelineCandidates[idx].text || '';
  try {
    const mediaData = await svgToPngDataUrl(pipelineCandidateSvg(idx));
    if (navigator.clipboard && window.ClipboardItem) {
      const blob = await (await fetch(mediaData)).blob();
      await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
      toast('图片已复制到剪贴板，X 页面打开后可粘贴图片并点击发帖', 'ok', 4200);
    } else {
      toast('当前浏览器不支持图片剪贴板，已打开文字发帖页', 'info', 4200);
    }
  } catch {
    toast('图片复制失败，已打开文字发帖页', 'info', 4200);
  }
  window.open('https://twitter.com/intent/tweet?text=' + encodeURIComponent(text), '_blank');
}

async function pipelinePublish(idx) {
  if (!state.pipelineCandidates?.[idx]) return;
  const text = state.pipelineCandidates[idx].text;
  if (!confirm(`确认发布这条推文？\n\n"${text.slice(0, 100)}${text.length > 100 ? '...' : ''}"`)) return;
  try {
    const mediaData = await svgToPngDataUrl(pipelineCandidateSvg(idx));
    const result = await api('POST', '/api/x/pipeline/publish', { text, dry_run: false, media_data: mediaData, media_alt_text: state.pipelineCandidates[idx].image_card?.alt_text || '' });
    if (result.url) {
      toast('发布成功！', 'ok');
      window.open(result.url, '_blank');
    } else {
      toast('发布成功', 'ok');
    }
    pipelineLoadHistory();
  } catch (e) {
    toast('发布失败: ' + e.message, 'err');
  }
}

async function pipelineLoadHistory() {
  const el = $('pipelineHistory');
  if (!el) return;
  try {
    const data = await api('GET', '/api/x/poster/log?limit=10');
    const posted = data.posted || [];
    if (!posted.length) { el.innerHTML = '<span style="color:var(--text-3)">暂无记录</span>'; return; }
    el.innerHTML = `<table class="tbl"><thead><tr><th>时间</th><th>内容</th><th>链接</th></tr></thead><tbody>` +
      posted.map(p => `<tr><td>${fmt.ts(p.posted_at)}</td><td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(p.text)}</td><td>${p.url ? `<a href="${p.url}" target="_blank" style="color:var(--purple)">查看</a>` : '—'}</td></tr>`).join('') +
      `</tbody></table>`;
  } catch { el.innerHTML = '<span style="color:var(--text-3)">加载失败</span>'; }
}

// ============== AUTOMATION COST ESTIMATOR ==============
function updateCostEstimate() {
  const heatmapOn = $('autoHeatmap')?.checked;
  const heatmapInterval = Math.max(60, +($('autoHeatmapInterval')?.value || 600));
  const xOn = $('autoXSentiment')?.checked;
  const xInterval = Math.max(300, +($('autoXInterval')?.value || 900));

  const liquidationMapCost = 0.001;
  const heatmapCostPerHour = heatmapOn ? (3600 / heatmapInterval) * liquidationMapCost : 0;
  const xCostPerHour = xOn ? (3600 / xInterval) * 0.02 : 0;
  const total = heatmapCostPerHour + xCostPerHour;

  const el = $('autoCostEstimate');
  if (el) el.textContent = `预估费用: ${total < 0.001 ? '< $0.001' : '$' + total.toFixed(4)}/小时`;

  const breakdown = $('autoCostBreakdown');
  if (breakdown) {
    const parts = [];
    parts.push('市场数据: 交易所 API 免费');
    if (heatmapOn) parts.push(`清算地图 ${(3600/heatmapInterval).toFixed(1)}次/h × ${liquidationMapCost.toFixed(3)} = ${heatmapCostPerHour.toFixed(4)} USDC`);
    if (xOn) parts.push(`X情绪 ${(3600/xInterval).toFixed(1)}次/h × ~$0.02 = $${xCostPerHour.toFixed(4)}`);
    if (total === 0) parts.push('当前配置无额外费用');
    else parts.push(`合计约 $${total.toFixed(4)}/h ≈ $${(total*24).toFixed(3)}/天`);
    breakdown.textContent = parts.join(' | ');
  }
}

// ============== DATA STATS ==============
async function refreshDataStats() {
  try {
    const d = await api('GET', '/api/data/stats');
    const fmtBytes = b => b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB' : b >= 1024 ? (b / 1024).toFixed(1) + ' KB' : b + ' B';
    if ($('dataOrdersCount')) $('dataOrdersCount').textContent = (d.orders?.count || 0).toLocaleString();
    if ($('dataOrdersSize')) $('dataOrdersSize').textContent = '占用 ' + fmtBytes(d.orders?.size_bytes || 0);
    if ($('dataHeatmapCount')) $('dataHeatmapCount').textContent = (d.heatmap_snapshots?.count || 0).toLocaleString();
    if ($('dataHeatmapSize')) $('dataHeatmapSize').textContent = '占用 ' + fmtBytes(d.heatmap_snapshots?.size_bytes || 0);
    if ($('dataEventsCount')) $('dataEventsCount').textContent = (d.events?.count || 0).toLocaleString();
    if ($('dataEventsSize')) $('dataEventsSize').textContent = '占用 ' + fmtBytes(d.events?.size_bytes || 0);
  } catch {}
}

// ============== DATA ANALYSIS PAGE ==============
async function runAIAnalysis() {
  const btn = $('btnRunAnalysis');
  const statusEl = $('analyzeStatus');
  const reportEl = $('analyzeReport');
  if (btn) btn.disabled = true;
  if (statusEl) statusEl.textContent = '分析中...';
  try {
    const provider = $('llmProvider')?.value || 'anthropic';
    const apiKey = ($('llmKey')?.value || '').trim();
    const model = $('llmModelSelect')?.value || '';
    const statusData = await api('GET', '/api/agent/status');
    const result = await api('POST', '/api/analyze', {
      provider,
      api_key: apiKey,
      model,
      data: {
        status: statusData,
        last_snapshot: statusData.last_signal,
        last_risk: statusData.last_risk,
        heatmap: statusData.heatmap,
      },
      prompt: '请给出完整的市场分析报告，包含：市场状态、清算信号、策略动作、风险提醒四个部分。',
    });
    if (reportEl) {
      reportEl.innerHTML = (result.analysis || '无分析结果').replace(/\n/g, '<br>');
    }
    if (statusEl) { statusEl.textContent = '分析完成 · ' + new Date().toLocaleTimeString(); statusEl.style.color = 'var(--green-text)'; }
    toast('AI 分析完成', 'ok');
  } catch (e) {
    if (statusEl) { statusEl.textContent = '失败: ' + e.message; statusEl.style.color = 'var(--red-text)'; }
    toast('分析失败: ' + e.message, 'err');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ============== LOGS PAGE ==============
async function loadLogs() {
  const body = $('logsBody');
  const countEl = $('logsCount');
  if (!body) return;
  try {
    const data = await api('GET', '/api/events');
    const events = data.events || [];
    const filter = $('logsFilter')?.value || '';
    const filtered = filter ? events.filter(e => e.kind === filter) : events;
    if (countEl) countEl.textContent = filtered.length + ' 条';
    if (!filtered.length) { body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-3)">暂无日志</div>'; return; }
    body.innerHTML = filtered.map(e => {
      const kindColor = e.kind === 'error' ? 'var(--red)' : e.kind === 'signal' ? 'var(--green)' : e.kind === 'order' ? 'var(--purple)' : 'var(--text-3)';
      const ts = e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : '';
      return `<div style="padding:6px 8px;border-bottom:1px solid var(--border);display:flex;gap:8px;align-items:flex-start">
        <span style="color:${kindColor};font-weight:600;min-width:78px;font-size:10.5px">[${e.level || 'info'}:${e.kind || '-'}]</span>
        <span style="flex:1;color:var(--text);word-break:break-all">
          <span style="font-family:var(--mono);color:var(--text-3)">${escapeHtml(e.module || 'agent')}.${escapeHtml(e.action || '-')}</span>
          ${escapeHtml(e.message || '')}
          ${e.data && Object.keys(e.data).length ? `<pre style="margin:4px 0 0;color:var(--text-2);font-size:10px;white-space:pre-wrap">${escapeHtml(JSON.stringify(e.data, null, 2))}</pre>` : ''}
        </span>
        <span style="color:var(--text-3);font-size:10px;white-space:nowrap">${ts}</span>
      </div>`;
    }).join('');
  } catch (e) { body.innerHTML = '<div style="padding:20px;color:var(--red)">加载失败: ' + e.message + '</div>'; }
}

async function diagnoseLogs() {
  const card = $('logsDiagnosisCard');
  const body = $('logsDiagnosisBody');
  if (!card || !body) return;
  card.style.display = '';
  body.textContent = '正在分析日志...';
  try {
    const eventsData = await api('GET', '/api/events');
    const errors = (eventsData.events || []).filter(e => e.kind === 'error').slice(0, 20);
    const recentEvents = (eventsData.events || []).slice(0, 30);
    const prompt = '请分析以下系统日志，找出错误原因并给出解决办法：\n\n错误日志：\n' + JSON.stringify(errors, null, 2) + '\n\n最近事件：\n' + JSON.stringify(recentEvents.map(e => ({kind:e.kind, message:e.message, timestamp:e.timestamp})), null, 2);
    const result = await api('POST', '/api/agent/chat', {
      provider: $('llmProvider')?.value || 'anthropic',
      api_key: ($('llmKey')?.value || '').trim(),
      model: $('llmModelSelect')?.value || '',
      question: prompt,
    });
    body.textContent = result.answer || '无诊断结果';
  } catch (e) {
    body.textContent = '诊断失败: ' + e.message + '\n\n请确保已配置 LLM API Key。';
  }
}

// ============== BOOTSTRAP ==============
async function bootstrap() {
  // Navigation
  qsa('.nav-item').forEach(el => el.addEventListener('click', () => navTo(el.dataset.page)));
  qsa('[data-nav]').forEach(el => el.addEventListener('click', () => navTo(el.dataset.nav)));

  // Desktop keys (Electron injects values into #pk, #llmKey)
  await loadDesktopKeys();

  // Check keys on settings input change
  ['pk', 'llmKey', 'llmReviewKey'].forEach(id => {
    const el = $(id);
    if (el) el.addEventListener('input', () => {
      checkKeys();
      if (state.keysReady) { loadOrders(); loadEvents(); pollStatus(); renderDashCharts(); }
    });
  });

  // Initial key check
  checkKeys();

  // X API status check (runs regardless of LLM/PK)
  await checkXStatus();

  // Wire X connect + analyze buttons
  $('btnXConnect')?.addEventListener('click', connectX);
  $('btnXApplyModel')?.addEventListener('click', applyXModel);
  $('xRelayProvider')?.addEventListener('change', () => {
    const row = $('xCustomUrlRow');
    if (row) row.style.display = $('xRelayProvider').value === 'custom' ? '' : 'none';
  });
  $('btnXAnalyze')?.addEventListener('click', runXAIAnalysis);
  $('btnXRefresh')?.addEventListener('click', () => loadXData(false));
  $('btnXForceRefresh')?.addEventListener('click', () => {
    if (confirm('强制刷新会调用 xAI Grok API（每次约 $0.02-0.05，25 条 X 搜索 + tokens）。继续？')) loadXData(true);
  });

  // Wire X Poster OAuth connect button
  $('btnXPosterConnect')?.addEventListener('click', connectXPoster);

  // Wire LLM connect buttons
  $('btnLlmConnect')?.addEventListener('click', connectLLM);
  $('btnLlmReviewConnect')?.addEventListener('click', connectLLMReview);
  $('llmProvider')?.addEventListener('change', updateChatModelPill);
  $('llmModelSelect')?.addEventListener('change', updateChatModelPill);
  $('btnRefreshUsage')?.addEventListener('click', refreshUsageStats);
  $('btnSaveSettings')?.addEventListener('click', saveSettings);
  $('btnLlmApply')?.addEventListener('click', applyLlmModel);
  $('btnLlmReviewApply')?.addEventListener('click', applyLlmReviewModel);
  refreshUsageStats();
  updateChatModelPill();

  // Settings tab switching
  qsa('.stab-item', $('settingsNav')).forEach(tab => {
    tab.addEventListener('click', () => {
      qsa('.stab-item', $('settingsNav')).forEach(t => { t.classList.remove('active'); t.style.background = ''; t.style.color = 'var(--text-2)'; });
      tab.classList.add('active');
      tab.style.background = 'var(--purple-soft)';
      tab.style.color = 'var(--purple-dark)';
      const section = tab.dataset.stab;
      qsa('[data-settings-section]').forEach(el => {
        el.style.display = el.dataset.settingsSection === section ? '' : 'none';
      });
    });
  });
  // Init: show only 'api' section
  qsa('[data-settings-section]').forEach(el => {
    el.style.display = el.dataset.settingsSection === 'api' ? '' : 'none';
  });

  // Wire Grok analyst chat
  $('btnGrokSend')?.addEventListener('click', sendGrokMessage);
  $('btnGrokReset')?.addEventListener('click', resetGrokSession);
  $('grokInput')?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendGrokMessage(); }
  });
  qsa('.grok-quick').forEach(b => b.addEventListener('click', () => {
    const input = $('grokInput');
    if (input) { input.value = b.dataset.prompt || b.textContent; input.focus(); }
  }));
  $('btnGrokExport')?.addEventListener('click', () => {
    if (!state.lastGrokReport) { toast('暂无报告可导出', 'err'); return; }
    const blob = new Blob([JSON.stringify(state.lastGrokReport, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `grok-report-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  });

  // Also auto-check xAI chat + poster status on boot
  refreshGrokMeta();
  checkXPosterStatus();
  loadBinanceSymbols();

  // Wire AI analysis page
  $('btnRunAnalysis')?.addEventListener('click', runAIAnalysis);
  $('btnRefreshDataStats')?.addEventListener('click', refreshDataStats);
  refreshDataStats();

  // Wire automation cost estimator
  ['autoMarketPoll','autoMarketInterval','autoHeatmap','autoHeatmapInterval','autoXSentiment','autoXInterval','autoLlmReview','autoPaperTrade','autoSignalGen'].forEach(id => {
    const el = $(id);
    if (el) el.addEventListener('change', updateCostEstimate);
    if (el && el.type === 'number') el.addEventListener('input', updateCostEstimate);
  });
  updateCostEstimate();

  // Wire logs page
  const logsFilter = $('logsFilter');
  if (logsFilter) {
    ['tick', 'decision'].forEach(kind => {
      if (!qsa('option', logsFilter).some(opt => opt.value === kind)) {
        const opt = document.createElement('option');
        opt.value = kind;
        opt.textContent = kind;
        logsFilter.appendChild(opt);
      }
    });
  }
  $('btnLogsRefresh')?.addEventListener('click', loadLogs);
  $('btnLogsDiagnose')?.addEventListener('click', diagnoseLogs);
  $('logsFilter')?.addEventListener('change', loadLogs);
  pageHooks['logs'] = loadLogs;

  // Wire pipeline
  $('btnPipelineGenerate')?.addEventListener('click', pipelineGenerate);
  $('btnPipelineRefreshHistory')?.addEventListener('click', pipelineLoadHistory);
  $('btnMarketRefresh')?.addEventListener('click', () => refreshAgentData());
  $('btnHmRefresh')?.addEventListener('click', () => refreshLiquidationMap());
  ['hmSym','hdrSymbol','configSymbol'].forEach(id => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('change', () => { el.value = slashSymbol(normalizeUsdtSymbol(el.value)); });
    el.addEventListener('blur', () => { el.value = slashSymbol(normalizeUsdtSymbol(el.value)); });
  });
  $('hmInt')?.addEventListener('change', () => pageHooks.heatmap?.());
  $('hmSym')?.addEventListener('input', () => pageHooks.heatmap?.());

  // Load data only if keys are ready
  if (state.keysReady) {
    await Promise.all([loadOrders(), loadEvents()]);
    await pollStatus();
    renderDashCharts();
  }
  const initialPage = (location.hash || '').replace('#', '');
  if (initialPage && $('page-' + initialPage)) navTo(initialPage);

  // Start polling (functions internally check keysReady)
  setInterval(pollStatus, 5000);
  setInterval(loadOrders, 15000);
  setInterval(loadEvents, 10000);

  // Wire interactions
  wireChat();
  wireAgentControl();

  // Tab buttons
  qsa('.hm-tabs button, .perf-tabs button').forEach(btn => {
    btn.addEventListener('click', function () {
      qsa('button', this.parentElement).forEach(b => b.classList.remove('active'));
      this.classList.add('active');
    });
  });
  $('hdrExchange')?.addEventListener('change', async function () {
    const exchange = syncExchangeSelectors(this.value);
    renderExchangeCredentialFields();
    await persistAgentExchange(exchange);
  });
  $('configExchange')?.addEventListener('change', async function () {
    const exchange = syncExchangeSelectors(this.value);
    renderExchangeCredentialFields();
    await persistAgentExchange(exchange);
  });
  $('exchangeCredExchange')?.addEventListener('change', async function () {
    persistVisibleExchangeCredentials(this.dataset.currentExchange);
    const exchange = syncExchangeSelectors(this.value);
    renderExchangeCredentialFields();
    await persistAgentExchange(exchange);
  });
}

// ============== SAVE SETTINGS ==============
async function saveSettings() {
  const pk = ($('pk')?.value || '').trim();
  const llmKey = ($('llmKey')?.value || '').trim();
  const llmProvider = $('llmProvider')?.value || '';
  const llmModel = $('llmModelSelect')?.value || '';
  const llmContextLength = $('llmContextLength')?.value || '';
  const llmBaseUrl = ($('llmBaseUrl')?.value || '').trim();
  const llmReviewProvider = $('llmReviewProvider')?.value || '';
  const llmReviewKey = ($('llmReviewKey')?.value || '').trim();
  const llmReviewModel = $('llmReviewModelSelect')?.value || '';
  const llmReviewContextLength = $('llmReviewContextLength')?.value || '';
  const llmReviewBaseUrl = ($('llmReviewBaseUrl')?.value || '').trim();
  persistVisibleExchangeCredentials();
  const exchangeCredentials = normalizeExchangeCredentials({ exchange_credentials: state.exchangeCredentials });
  const binanceKey = exchangeCredentials.binance.api_key || '';
  const binanceSecret = exchangeCredentials.binance.secret || '';
  const selectedSymbol = normalizeUsdtSymbol($('configSymbol')?.value || $('hdrSymbol')?.value || 'BTC/USDT');
  const selectedExchange = normalizeExchange($('exchangeCredExchange')?.value || $('configExchange')?.value || $('hdrExchange')?.value || 'binance');
  syncExchangeSelectors(selectedExchange);

  // Build config payload for strategy config endpoint
  const config = {};
  if (llmProvider) config.llm_provider = llmProvider;
  if (llmModel) config.llm_model = llmModel;
  if (llmContextLength) config.llm_context_length = +llmContextLength;
  if (llmBaseUrl) config.llm_base_url = llmBaseUrl;
  if (llmReviewProvider) config.llm_review_provider = llmReviewProvider;
  if (llmReviewModel) config.llm_review_model = llmReviewModel;
  if (llmReviewContextLength) config.llm_review_context_length = +llmReviewContextLength;
  if (llmReviewBaseUrl) config.llm_review_base_url = llmReviewBaseUrl;
  config.coin = selectedSymbol.replace(/USDT$/, '');
  config.symbol = selectedSymbol;
  config.exchange = selectedExchange;

  // Automation settings
  config.poll_seconds = Math.max(10, +($('autoMarketInterval')?.value || 60));
  config.liq_map_snapshot_interval_seconds = Math.max(60, +($('autoHeatmapInterval')?.value || 600));
  config.llm_review_enabled = !!$('autoLlmReview')?.checked;
  config.safety_mode = $('configSafetyMode')?.value || $('safetyMode')?.value || 'paper';
  config.min_opportunity_score = Math.max(0, Math.min(100, +($('minOpportunityScore')?.value || 70)));
  config.decision_report_retention = Math.max(10, Math.min(1000, +($('decisionReportRetention')?.value || 100)));
  config.save_claw402_raw_samples = !!$('saveClawRawSamples')?.checked;

  try {
    await saveDesktopSettings({
      pk,
      llmProvider,
      llmKey,
      llmModel,
      llmBaseUrl,
      llmReviewProvider,
      llmReviewKey,
      llmReviewModel,
      llmReviewBaseUrl,
      binanceKey,
      binanceSecret,
      exchangeCredentials,
    });
    await api('POST', '/api/agent/config', config);
    state.pk = pk;
    state.llmKey = llmKey;
    checkKeys();
    toast('设置已保存', 'ok');
    try {
      await refreshAgentData();
    } catch {}
  } catch (e) {
    toast('保存失败: ' + e.message, 'err');
  }
}

async function saveDesktopSettings(settings) {
  if (!window.desktop) return;
  if (window.desktop.saveWallet) {
    await window.desktop.saveWallet(settings.pk || '');
  }
  if (!window.desktop.saveSettings) return;
  let existing = {};
  try {
    existing = window.desktop.getSettings ? await window.desktop.getSettings() : {};
  } catch {}
  const providers = [...(existing.llm_providers || [])];
  upsertProvider(providers, {
    id: settings.llmProvider,
    api_key: settings.llmKey,
    base_url: settings.llmBaseUrl,
    default_model: settings.llmModel,
  });
  upsertProvider(providers, {
    id: settings.llmReviewProvider,
    api_key: settings.llmReviewKey,
    base_url: settings.llmReviewBaseUrl,
    default_model: settings.llmReviewModel,
  });
  await window.desktop.saveSettings({
    llm_providers: providers,
    llm_default_provider: settings.llmProvider,
    llm_default_model: settings.llmModel,
    llm_review_provider: settings.llmReviewProvider,
    llm_review_model: settings.llmReviewModel,
    exchange_credentials: settings.exchangeCredentials || {},
    exchange_default: normalizeExchange($('configExchange')?.value || $('hdrExchange')?.value || 'binance'),
    binance_key: settings.binanceKey || '',
    binance_secret: settings.binanceSecret || '',
  });
}

function upsertProvider(providers, patch) {
  if (!patch.id) return;
  const current = providers.find(p => p.id === patch.id);
  const next = {
    ...(current || {}),
    id: patch.id,
    name: providerName(patch.id),
  };
  if ('api_key' in patch) next.api_key = patch.api_key || '';
  if ('base_url' in patch) next.base_url = patch.base_url || '';
  if ('default_model' in patch) next.default_model = patch.default_model || '';
  if (current) Object.assign(current, next);
  else providers.push(next);
}

function providerName(id) {
  const opt = [...($('llmProvider')?.options || []), ...($('llmReviewProvider')?.options || [])].find(o => o.value === id);
  return opt?.textContent || id;
}

function applyLlmModel() {
  const model = $('llmModelSelect')?.value || '';
  const provider = $('llmProvider')?.value || '';
  if (!model) { toast('请先选择模型', 'err'); return; }
  updateChatModelPill();
  toast(`已选择主模型: ${model} (${provider})`, 'ok');
}

function applyLlmReviewModel() {
  const model = $('llmReviewModelSelect')?.value || '';
  const provider = $('llmReviewProvider')?.value || '';
  if (!model) { toast('请先选择审核模型', 'err'); return; }
  toast(`已选择审核模型: ${model} (${provider})`, 'ok');
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bootstrap);
else bootstrap();

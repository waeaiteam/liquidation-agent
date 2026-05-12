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
  grok: { apiKey: '', model: 'grok-4.3', baseUrl: 'https://api.x.ai/v1', systemPrompt: '' },
};

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
  state.keysReady = !!pk;
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
  if (!res.ok) throw new Error(j.error || `HTTP ${res.status}`);
  return j;
}

// ============== NAVIGATION ==============
const pageHooks = {};
function navTo(page) {
  qsa('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.page === page));
  qsa('.page').forEach(el => el.classList.toggle('active', el.id === 'page-' + page));
  setTimeout(() => Object.values(state.charts).forEach(c => c && c.resize && c.resize()), 80);
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

// ============== DESKTOP BRIDGE ==============
async function loadDesktopKeys() {
  if (!window.desktop) return;
  try {
    const keys = await window.desktop.getKeys();
    if (keys?.pk && $('pk')) $('pk').value = keys.pk;
    if (keys?.llm_key && $('llmKey')) $('llmKey').value = keys.llm_key;
    if (keys?.llm_review_key && $('llmReviewKey')) $('llmReviewKey').value = keys.llm_review_key;
    if (keys?.binance_key && $('binanceApiKey')) $('binanceApiKey').value = keys.binance_key;
    if (keys?.binance_secret && $('binanceApiSecret')) $('binanceApiSecret').value = keys.binance_secret;
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
  const pill = $('hdrRun');
  if (pill) {
    if (running) { pill.className = 'hdr-pill pill-purple'; $('hdrRunText').textContent = '运行中'; }
    else { pill.className = 'hdr-pill pill-green'; $('hdrRunText').textContent = '已停止'; }
  }
  const startBtn = $('btnStart');
  const stopBtn = $('btnStop');
  if (startBtn) startBtn.style.display = running ? 'none' : '';
  if (stopBtn) stopBtn.style.display = running ? '' : 'none';
}

// ============== STATUS POLLING ==============
async function pollStatus() {
  try {
    const j = await api('GET', '/api/agent/status');
    state.lastStatus = j;
    renderHeader(j);
    renderDashKpis(j);
    renderDashSignals(j);
  } catch {}
}

async function refreshAgentData({ forceHeatmap = false } = {}) {
  const pk = state.pk || ($('pk')?.value || '').trim();
  if (forceHeatmap && !pk) { toast('请先在设置中填写钱包私钥', 'err'); return; }
  const btn = forceHeatmap ? $('btnHmRefresh') : $('btnMarketRefresh');
  const oldText = btn?.textContent;
  if (btn) { btn.disabled = true; btn.textContent = '获取中...'; }
  try {
    if (!forceHeatmap) {
      const result = await api('POST', '/api/market/refresh', {
        symbol: (state.lastStatus?.config?.symbol || $('hdrSymbol')?.value || 'BTCUSDT').replace('/', ''),
        exchange: (state.lastStatus?.config?.exchange || 'binance').toLowerCase(),
        interval: state.lastStatus?.config?.interval || '1m',
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
    toast('获取数据失败: ' + e.message, 'err');
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
  if ($('kpiExchange')) $('kpiExchange').textContent = (cfg.exchange || 'binance') + ' 永续';
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
  const snapshots = state.lastStatus?.heatmap?.snapshots || [];
  const latest = snapshots[0] || {};
  const normalized = latest.liq_map || {};
  const points = Array.isArray(normalized.points) ? normalized.points : [];
  if (!points.length) {
    chart.setOption({
      grid: { left: 50, right: 10, top: 10, bottom: 30 },
      xAxis: { type: 'category', data: [], show: false },
      yAxis: { type: 'category', data: [], show: false },
      series: [{ type: 'heatmap', data: [] }],
      graphic: [{ type: 'text', left: 'center', top: 'middle', style: { text: '暂无清算地图数据', fontSize: 13, fill: '#9ea2ab', fontFamily: 'inherit' } }],
      backgroundColor: '#1a1a2e',
    }, true);
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

pageHooks.heatmap = function () {
  if (!state.keysReady) return;
  const chart = getChart('hmDetailChart');
  if (chart) renderHeatmapChart(chart, 'full');
  renderHeatmapZones();
  const corrChart = getChart('hmCorrChart');
  if (corrChart) {
    const lastPrice = state.lastStatus?.last_snapshot?.price || state.lastStatus?.last_signal?.price;
    if (lastPrice) {
      const now = Date.now();
      const priceData = [[now, +lastPrice]];
      const densityData = [[now, 0]];
      corrChart.setOption({
        grid: { left: 50, right: 50, top: 10, bottom: 28 },
        tooltip: { trigger: 'axis', ...TT },
        xAxis: { type: 'time', ...AXIS },
        yAxis: [{ type: 'value', ...AXIS, name: '' }, { type: 'value', ...AXIS, name: '' }],
        graphic: [],
        series: [
          { type: 'line', smooth: true, showSymbol: true, data: priceData, lineStyle: { color: '#6c5ce7', width: 2 } },
          { type: 'bar', yAxisIndex: 1, data: densityData, itemStyle: { color: 'rgba(0,184,148,.4)' }, barWidth: '60%' },
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

function renderHeatmapZones() {
  const body = $('hmZonesBody');
  if (!body) return;
  const snapshot = state.lastStatus?.heatmap?.snapshots?.[0] || {};
  const clusters = snapshot.heatmap?.clusters || [];
  if (!clusters.length) {
    body.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-3);padding:18px">暂无真实清算地图聚类数据</td></tr>';
    return;
  }
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

// ============== MARKET PAGE ==============
pageHooks.market = function () {
  if (!state.keysReady) return;
  const snap = state.lastStatus?.last_snapshot || {};
  const market = snap.market || {};
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
    renderOrdersTable();
    renderDashRecentOrders(state.lastOrders);
  } catch {}
}

function renderOrdersTable() {
  const tbody = $('ordersBody');
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
      await api('POST', '/api/agent/start', { pk });
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
  if (provider) body.provider = provider;
  if (apiKey) body.api_key = apiKey;
  if (model) body.model = model;
  if (baseUrl) body.custom_base_url = baseUrl;

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

  const styleColors = {
    '理性分析': { bg: 'var(--blue-soft)', color: 'var(--blue)', border: 'var(--blue)' },
    '激进观点': { bg: 'var(--red-soft)', color: 'var(--red-text)', border: 'var(--red)' },
    '幽默吐槽': { bg: 'var(--amber-soft)', color: 'var(--amber-text)', border: 'var(--amber)' },
  };

  container.innerHTML = candidates.map((c, i) => {
    const sc = styleColors[c.style] || { bg: 'var(--surface-2)', color: 'var(--text-2)', border: 'var(--border)' };
    return `
      <div class="card" style="border-top:3px solid ${sc.border};display:flex;flex-direction:column">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
          <span style="padding:3px 8px;background:${sc.bg};color:${sc.color};border-radius:4px;font-size:10.5px;font-weight:600">${c.style || '风格 ' + (i+1)}</span>
          <span style="font-family:var(--mono);font-size:10.5px;color:var(--text-3)">${c.char_count}/280</span>
        </div>
        <div style="font-size:13px;line-height:1.7;flex:1;margin-bottom:12px;white-space:pre-wrap" id="pipelineText${i}">${escHtml(c.text)}</div>
        <div style="display:flex;gap:4px;font-size:10px;color:var(--text-3);margin-bottom:10px;flex-wrap:wrap">
          ${(c.data_sources || []).map(s => `<span style="padding:2px 6px;background:var(--surface-2);border-radius:3px">${s}</span>`).join('')}
          <span style="padding:2px 6px;background:var(--surface-2);border-radius:3px">置信度 ${((c.confidence || 0) * 100).toFixed(0)}%</span>
        </div>
        <div style="display:flex;gap:6px">
          <button onclick="pipelineEdit(${i})" style="flex:1;padding:6px;border:1px solid var(--border);border-radius:6px;background:var(--surface);font-size:11px;cursor:pointer">编辑</button>
          <button onclick="pipelineDryRun(${i})" style="flex:1;padding:6px;border:1px solid var(--border);border-radius:6px;background:var(--surface);font-size:11px;cursor:pointer">Dry Run</button>
          <button onclick="pipelinePublish(${i})" style="flex:1;padding:6px;border:none;border-radius:6px;background:var(--purple);color:#fff;font-size:11px;font-weight:600;cursor:pointer">发布</button>
        </div>
      </div>`;
  }).join('');

  state.pipelineCandidates = candidates;
}

function escHtml(s) { return escapeHtml(s); }

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
    const result = await api('POST', '/api/x/pipeline/publish', { text, dry_run: true });
    toast(`[Dry Run] ${result.char_count || text.length} 字符，预览通过`, 'info');
  } catch (e) {
    toast('Dry Run 失败: ' + e.message, 'err');
  }
}

async function pipelinePublish(idx) {
  if (!state.pipelineCandidates?.[idx]) return;
  const text = state.pipelineCandidates[idx].text;
  if (!confirm(`确认发布这条推文？\n\n"${text.slice(0, 100)}${text.length > 100 ? '...' : ''}"`)) return;
  try {
    const result = await api('POST', '/api/x/pipeline/publish', { text, dry_run: false });
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

  const heatmapCostPerHour = heatmapOn ? (3600 / heatmapInterval) * 0.001 : 0;
  const xCostPerHour = xOn ? (3600 / xInterval) * 0.02 : 0;
  const total = heatmapCostPerHour + xCostPerHour;

  const el = $('autoCostEstimate');
  if (el) el.textContent = `预估费用: ${total < 0.001 ? '< $0.001' : '$' + total.toFixed(4)}/小时`;

  const breakdown = $('autoCostBreakdown');
  if (breakdown) {
    const parts = [];
    parts.push('市场数据: 交易所 API 免费');
    if (heatmapOn) parts.push(`清算地图 ${(3600/heatmapInterval).toFixed(1)}次/h × 0.001 = ${heatmapCostPerHour.toFixed(4)} USDC`);
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
  $('btnLogsRefresh')?.addEventListener('click', loadLogs);
  $('btnLogsDiagnose')?.addEventListener('click', diagnoseLogs);
  $('logsFilter')?.addEventListener('change', loadLogs);
  pageHooks['logs'] = loadLogs;

  // Wire pipeline
  $('btnPipelineGenerate')?.addEventListener('click', pipelineGenerate);
  $('btnPipelineRefreshHistory')?.addEventListener('click', pipelineLoadHistory);
  $('btnMarketRefresh')?.addEventListener('click', () => refreshAgentData());
  $('btnHmRefresh')?.addEventListener('click', () => refreshAgentData({ forceHeatmap: true }));

  // Load data only if keys are ready
  if (state.keysReady) {
    await Promise.all([loadOrders(), loadEvents()]);
    pollStatus();
    renderDashCharts();
  }

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
  const binanceKey = ($('binanceApiKey')?.value || '').trim();
  const binanceSecret = ($('binanceApiSecret')?.value || '').trim();

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

  // Automation settings
  config.poll_seconds = Math.max(10, +($('autoMarketInterval')?.value || 60));
  config.liq_map_snapshot_interval_seconds = Math.max(60, +($('autoHeatmapInterval')?.value || 600));
  config.llm_review_enabled = !!$('autoLlmReview')?.checked;

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
    });
    await api('POST', '/api/agent/config', config);
    state.pk = pk;
    state.llmKey = llmKey;
    checkKeys();
    toast('设置已保存', 'ok');
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

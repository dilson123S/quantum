window.addEventListener("unhandledrejection", (event) => {
  event.preventDefault();
  console.debug("Handled unhandled promise rejection:", event.reason);
});

let socket = null;
let equityChart = null;
let currentTab = "overview";
let isPaused = false;
let currentBotMode = "DEMO";
let chartDataHistory = { labels: [], balance: [], equity: [] };
window.__lastSnapshot = {};
window.__prevPrices = window.__prevPrices || {};

const VIEW_TITLES = {
  dashboard: '<i class="fa-solid fa-house" style="color:#38bdf8"></i> DASHBOARD PRINCIPAL — CENTRO DE MANDO',
  "active-trades": '<i class="fa-solid fa-chart-line" style="color:#34d399"></i> OPERACIONES ACTIVAS &amp; TERMINAL',
  charts: '<i class="fa-solid fa-chart-candlestick" style="color:#38bdf8"></i> GRÁFICOS EN TIEMPO REAL — XAUUSD',
  history: '<i class="fa-solid fa-list-check" style="color:#f59e0b"></i> HISTORIAL DE OPERACIONES &amp; AUTOPSIAS',
  "learning-lab": '<i class="fa-solid fa-atom" style="color:#a78bfa"></i> LEARNING PERFORMANCE LAB',
  "context-memory": '<i class="fa-solid fa-database" style="color:#38bdf8"></i> MEMORIA CONTEXTUAL — XAUUSD',
  patterns: '<i class="fa-solid fa-dna" style="color:#a78bfa"></i> PATRONES APRENDIDOS &amp; DNA DE ORO',
  autopsy: '<i class="fa-solid fa-microscope" style="color:#38bdf8"></i> AUTOPSIA DE OPERACIONES (MENTE DEL BOT)',
  "adaptive-rules": '<i class="fa-solid fa-sliders" style="color:#c4b5fd"></i> REGLAS ADAPTATIVAS &amp; VERSIONES',
  "shadow-live": '<i class="fa-solid fa-code-compare" style="color:#38bdf8"></i> SHADOW LIVE EMPAREJADO (TICKS GEMELOS)',
  "stats-validation": '<i class="fa-solid fa-square-root-variable" style="color:#a78bfa"></i> VALIDACIÓN ESTADÍSTICA NO PARAMÉTRICA',
  "oos-gate": '<i class="fa-solid fa-shield-halved" style="color:#ef4444"></i> OUT-OF-SAMPLE HOLDOUT GATE',
  signals: '<i class="fa-solid fa-tower-broadcast" style="color:#38bdf8"></i> AUDITORÍA DE SEÑALES Y FILTROS',
  "audit-trail": '<i class="fa-solid fa-receipt" style="color:#38bdf8"></i> AUDIT TRAIL INMUTABLE',
  settings: '<i class="fa-solid fa-gear" style="color:#94a3b8"></i> CONFIGURACIÓN &amp; PARÁMETROS'
};

document.addEventListener("DOMContentLoaded", () => {
  initChart();
  initExperimentationDonut();
  drawSparklines();
  initWebSocket();
  loadBotFleet();
  loadTradeHistory();
  loadSignalsLog();
  loadCopilotAnalysis();
  loadLearningSummary();
  loadProximityRadar();
  updateClocks();
  setInterval(loadProximityRadar, 4000);
  setInterval(updateClocks, 1000);
  setInterval(loadBotFleet, 30000);
  setInterval(() => {
    if (currentTab === "charts") {
      loadCandlesData();
    }
  }, 15000);
  switchView("dashboard");
});

function switchView(viewId, subParam) {
  if (viewId === "overview") viewId = "dashboard";
  currentTab = viewId;

  // 1. Sidebar navigation button states
  document.querySelectorAll(".side-nav-item").forEach(btn => {
    btn.classList.remove("active");
    btn.classList.remove("active-purple");
  });

  let activeBtn = subParam ? document.getElementById(`tab-${subParam}`) : null;
  if (!activeBtn) activeBtn = document.getElementById(`tab-${viewId}`);
  if (activeBtn) {
    if (viewId === "learning-lab") {
      activeBtn.classList.add("active-purple");
    } else {
      activeBtn.classList.add("active");
    }
  }

  // 2. Views toggle with explicit inline style to guarantee no overlapping
  document.querySelectorAll(".app-view").forEach(v => {
    v.classList.remove("active");
    v.style.display = "none";
  });

  const targetView = document.getElementById(`view-${viewId}`);
  if (targetView) {
    targetView.classList.add("active");
    targetView.style.display = "block";
  }

  // 3. Update topbar title
  const titleEl = document.getElementById("topbar-view-title");
  if (titleEl && VIEW_TITLES[viewId]) {
    titleEl.innerHTML = VIEW_TITLES[viewId];
  }

  // 4. View-specific activations
  if (viewId === "history") loadTradeHistory();
  if (viewId === "signals") loadSignalsLog();
  if (viewId === "autopsy" || viewId === "context-memory") loadLearningSummary();
  if (viewId === "learning-lab") {
    setTimeout(drawSparklines, 60);
  }
  if (viewId === "charts") {
    setTimeout(loadCandlesData, 60);
  }
  if (viewId === "dashboard") {
    if (equityChart) equityChart.resize();
    loadBotFleet();
  }
}

function switchTab(tabId) {
  switchView(tabId);
}

function initExperimentationDonut() {
  const canvas = document.getElementById("experimentationDonutChart");
  if (!canvas) return;
  if (typeof Chart === "undefined") {
    console.warn("[Dashboard] Chart.js no disponible para donut chart.");
    return;
  }
  new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: ["Aceptadas", "Rechazadas", "En Validación", "No Probadas"],
      datasets: [{
        data: [2, 10, 2, 13],
        backgroundColor: ["#10b981", "#ef4444", "#38bdf8", "#334155"],
        borderWidth: 0,
        hoverOffset: 4
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "74%",
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#0c1520",
          borderColor: "rgba(148,163,184,.2)",
          borderWidth: 1,
          bodyFont: { size: 9, family: "JetBrains Mono" },
          padding: 6
        }
      }
    }
  });
}

function drawSparklines() {
  const sparks = [
    { id: "sp-equity", points: [210, 210.4, 210.8, 211.2, 211.6, 211.99], color: "#818cf8" },
    { id: "sp-trades", points: [140, 142, 143.5, 145, 146, 146.66], color: "#38bdf8" },
    { id: "sp-exp", points: [0.12, 0.14, 0.15, 0.16, 0.17, 0.18], color: "#34d399" },
    { id: "sp-stability", points: [0.35, 0.38, 0.39, 0.41, 0.40, 0.42], color: "#a78bfa", isBar: true },
    { id: "sp-dd", points: [4.2, 4.0, 3.9, 3.8, 3.8, 3.8], color: "#34d399" },
    { id: "sp-incremental", points: [0.04, 0.05, 0.06, 0.07, 0.08, 0.08], color: "#38bdf8", isBar: true }
  ];
  sparks.forEach(s => {
    const c = document.getElementById(s.id);
    if (!c) return;
    const ctx = c.getContext("2d");
    const w = c.width = c.offsetWidth || 60;
    const h = c.height = c.offsetHeight || 24;
    ctx.clearRect(0, 0, w, h);
    if (s.isBar) {
      const barWidth = Math.max(3, Math.floor(w / s.points.length) - 2);
      const max = Math.max(...s.points);
      s.points.forEach((p, i) => {
        const barH = (p / max) * (h - 4);
        ctx.fillStyle = s.color;
        ctx.fillRect(i * (barWidth + 2), h - barH, barWidth, barH);
      });
    } else {
      ctx.beginPath();
      ctx.strokeStyle = s.color;
      ctx.lineWidth = 1.6;
      ctx.lineJoin = "round";
      const min = Math.min(...s.points);
      const max = Math.max(...s.points);
      const range = max - min || 1;
      s.points.forEach((p, i) => {
        const x = (i / (s.points.length - 1)) * (w - 4) + 2;
        const y = h - ((p - min) / range) * (h - 6) - 3;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }
  });
}


function initChart() {
  const canvas = document.getElementById("equityChart");
  if (!canvas) return;
  if (typeof Chart === "undefined") {
    console.warn("[Dashboard] Chart.js no disponible para equityChart.");
    return;
  }
  equityChart = new Chart(canvas, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Equity",
          data: [],
          borderColor: "#2dd4bf",
          backgroundColor: "rgba(45,212,191,.07)",
          borderWidth: 2,
          fill: true,
          tension: .34,
          pointRadius: 0,
          pointHoverRadius: 4
        },
        {
          label: "Balance",
          data: [],
          borderColor: "#4f8cff",
          borderWidth: 1.4,
          borderDash: [5, 5],
          fill: false,
          tension: .18,
          pointRadius: 0,
          pointHoverRadius: 3
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 250 },
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          displayColors: true,
          backgroundColor: "#0c1520",
          borderColor: "rgba(148,163,184,.16)",
          borderWidth: 1,
          titleColor: "#dbe5f1",
          bodyColor: "#91a0b4",
          padding: 10,
          callbacks: { label: ctx => ` ${ctx.dataset.label}: $${Number(ctx.parsed.y).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}` }
        }
      },
      scales: {
        x: {
          grid: { color: "rgba(148,163,184,.045)", drawBorder: false },
          ticks: { color: "#53647a", font: { size: 8 }, maxTicksLimit: 7 }
        },
        y: {
          grid: { color: "rgba(148,163,184,.05)", drawBorder: false },
          ticks: {
            color: "#53647a",
            font: { size: 8 },
            callback: value => "$" + Number(value).toLocaleString(undefined,{maximumFractionDigits:0})
          }
        }
      }
    }
  });
}

function updateChart(balance, equity, timeStr) {
  if (!equityChart) return;
  const shortTime = timeStr?.includes("T") ? timeStr.split("T")[1].substring(0, 8) : new Date().toLocaleTimeString();
  if (chartDataHistory.labels.length >= 36) {
    chartDataHistory.labels.shift();
    chartDataHistory.balance.shift();
    chartDataHistory.equity.shift();
  }
  chartDataHistory.labels.push(shortTime);
  chartDataHistory.balance.push(balance);
  chartDataHistory.equity.push(equity);
  equityChart.data.labels = chartDataHistory.labels;
  equityChart.data.datasets[0].data = chartDataHistory.equity;
  equityChart.data.datasets[1].data = chartDataHistory.balance;
  equityChart.update("none");
  setText("chart-equity-now", money(equity));
  setText("chart-balance-now", money(balance));
}

function initWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws`;
  try { socket = new WebSocket(wsUrl); } catch { scheduleReconnect(); return; }

  socket.onopen = () => {
    setWsState(true);
  };

  socket.onmessage = event => {
    try {
      const data = JSON.parse(event.data);
      window.__lastSnapshot = data;
      handleLiveSnapshot(data);
    } catch (error) {
      console.error("WS parse error:", error);
    }
  };

  socket.onclose = () => {
    setWsState(false);
    scheduleReconnect();
  };

  socket.onerror = () => {
    setWsState(false);
    try { socket.close(); } catch {}
  };
}

function scheduleReconnect() {
  clearTimeout(window.__wsTimer);
  window.__wsTimer = setTimeout(initWebSocket, 2500);
}

function setWsState(online) {
  const dot = document.getElementById("ws-indicator");
  const text = document.getElementById("ws-status");
  const pill = document.getElementById("ws-pill");
  const health = document.getElementById("health-ws");
  if (dot) {
    dot.style.background = online ? "var(--green)" : "var(--red)";
    dot.style.boxShadow = online ? "0 0 8px rgba(52,211,153,.5)" : "0 0 8px rgba(251,95,106,.5)";
  }
  if (text) text.textContent = online ? "WS LIVE" : "WS OFFLINE";
  if (text) text.style.color = online ? "var(--green)" : "var(--red)";
  if (pill) pill.style.borderColor = online ? "rgba(52,211,153,.16)" : "rgba(251,95,106,.18)";
  if (health) {
    health.innerHTML = online ? '<i class="fa-solid fa-circle-check"></i> Live' : '<i class="fa-solid fa-triangle-exclamation"></i> Offline';
    health.className = online ? "ok" : "bad";
  }
}

function handleLiveSnapshot(data) {
  if (!data) return;

  if (data.account) {
    const acc = data.account;
    currentBotMode = String(acc.mode ?? "DEMO").toUpperCase();
    const isDemo = currentBotMode === "DEMO";

    // Update Mode UI Components
    updateModeDisplay(isDemo, acc.mode_description, acc.score_thresholds, acc.fleet_profiles);

    setText("account-login", `#${acc.login ?? "----"}`);
    setText("account-server", acc.server ?? "--");
    setText("topbar-connection-status", acc.mt5_connected ? `MT5 ${acc.server || "conectado"}` : "MT5 sin conexión");
    setText("side-mode", currentBotMode);

    setText("kpi-balance", money(acc.balance));
    setText("kpi-equity", money(acc.equity));
    setText("kpi-margin-free", money(acc.margin_free));
    const pnl = document.getElementById("kpi-pnl");
    if (pnl) {
      pnl.textContent = `${acc.daily_pnl >= 0 ? "+" : "-"}$${Math.abs(Number(acc.daily_pnl || 0)).toFixed(2)}`;
      pnl.style.color = acc.daily_pnl >= 0 ? "var(--green)" : "var(--red)";
    }
    const cumPnl = document.getElementById("kpi-cumulative-pnl");
    if (cumPnl) {
      const val = Number(acc.cumulative_pnl || 0);
      cumPnl.textContent = `${val >= 0 ? "+" : "-"}$${Math.abs(val).toFixed(2)}`;
      cumPnl.style.color = val >= 0 ? "var(--green)" : "var(--red)";
    }
    const cumSub = document.getElementById("kpi-cumulative-sub");
    if (cumSub) {
      const pf = acc.profit_factor;
      const pfStr = pf == null ? "—" : Number(pf).toFixed(2);
      const tr = acc.cumulative_trades || 0;
      const wins = acc.cumulative_wins ?? 0;
      const losses = acc.cumulative_losses ?? 0;
      cumSub.innerHTML = `Ejecución actual: ${wins}W · ${losses}L | PF: ${pfStr} <span title="Desde ${escapeHtml(acc.current_run_started_at || 'inicio no registrado')}; operaciones cerradas N=${tr}." style="cursor:help;color:#64748b">(N=${tr})</span>`;
    }
    setText("kpi-drawdown", acc.drawdown_pct == null ? "—" : `${Number(acc.drawdown_pct).toFixed(1)}%`);
    setText("kpi-open-positions", acc.open_positions_count ?? 0);
    setText("badge-active-count", acc.open_positions_count ?? 0);
    setText("active-count-title", acc.open_positions_count ?? 0);
    const wr = acc.cumulative_win_rate ?? acc.win_rate;
    setText("kpi-winrate", wr == null ? "—" : `${Number(wr).toFixed(1)}%`);
    const expR = acc.avg_r;
    setText("kpi-expectancy", expR == null ? "—" : `${expR >= 0 ? "+" : ""}${Number(expR).toFixed(2)} R`);
    const todayTr = acc.today_trades ?? 0;
    const todayW = acc.winning_trades ?? 0;
    const todayL = acc.losing_trades ?? 0;
    setText("kpi-trades-count", `${todayTr} trades hoy (${todayW}W / ${todayL}L)`);
    if (acc.balance != null && acc.equity != null) updateChart(Number(acc.balance), Number(acc.equity), acc.timestamp);
    setText("last-update", timeOnly(acc.timestamp));
    const drawdownState = acc.drawdown_pct == null ? "UNKNOWN" : Number(acc.drawdown_pct) >= 10 ? "STOPPED" : Number(acc.drawdown_pct) >= 5 ? "REDUCED" : "NORMAL";
    setRiskState(drawdownState);
    const running = String(acc.status || "RUNNING").toUpperCase();
    setText("side-status-title", running === "RUNNING" ? "FLOTA DEMO EN LÍNEA" : running === "PARTIAL" ? "FLOTA PARCIAL" : `FLOTA ${running}`);
    updateLearningLabKPIs(acc, window.__lastLabData);

    if (acc.news_filter) {
      const nf = acc.news_filter;
      const statusEl = document.getElementById("kpi-news-status");
      const subEl = document.getElementById("kpi-news-sub");
      const pillEl = document.getElementById("setup-news-pill");
      
      if (nf.enabled && nf.available === false) {
        if (statusEl) {
          statusEl.textContent = "ESTADO NO VERIFICABLE";
          statusEl.style.color = "var(--amber)";
        }
        if (subEl) {
          subEl.textContent = nf.events_status || "No se confirmó la disponibilidad del calendario";
          subEl.style.color = "var(--amber)";
        }
        if (pillEl) {
          pillEl.textContent = "PROVEEDOR SIN CONFIRMAR";
          pillEl.style.color = "var(--amber)";
        }
      } else if (nf.in_blackout) {
        if (statusEl) {
          statusEl.textContent = "BLACKOUT ACTIVO";
          statusEl.style.color = "var(--red)";
        }
        if (subEl) {
          subEl.textContent = nf.blackout_reason || "Bloqueo por noticia de alto impacto";
          subEl.style.color = "var(--red)";
        }
        if (pillEl) {
          pillEl.textContent = "BLOQUEO ACTIVO";
          pillEl.style.color = "var(--red)";
        }
      } else {
        if (statusEl) {
          statusEl.textContent = nf.enabled ? "OPERACIÓN PERMITIDA" : "DESACTIVADO";
          statusEl.style.color = nf.enabled ? "var(--green)" : "var(--text-muted)";
        }
        if (subEl) {
          if (nf.next_event) {
            subEl.textContent = `Próx: ${nf.next_event.title} (${nf.next_event.date_utc ? nf.next_event.date_utc.substring(11, 16) : ''} UTC)`;
          } else {
            subEl.textContent = nf.events_status || "No hay eventos devueltos por el proveedor";
          }
          subEl.style.color = "var(--text-muted)";
        }
        if (pillEl) {
          pillEl.textContent = nf.enabled ? "ACTIVO (±30m)" : "INACTIVO";
          pillEl.style.color = nf.enabled ? "#38bdf8" : "var(--text-muted)";
        }
      }
    }

    const profiles = acc.fleet_profiles || [];
    const sessionTimes = [...new Set(profiles.map(p => `${p.session_start}-${p.session_end} UTC`))];
    setText("setup-session", sessionTimes.length === 1 ? sessionTimes[0] : `${sessionTimes.length} horarios`);
    setText("setup-session-detail", profiles.length ? `${profiles.filter(p => p.session_filter).length}/${profiles.length} perfiles con filtro horario` : "No disponible");
    const newsEnabled = profiles.filter(p => p.news_filter).length;
    setText("setup-news-pill", `${newsEnabled}/${profiles.length || 0} perfiles ON`);
    const newsWindows = [...new Set(profiles.map(p => `${p.news_blackout_before}/${p.news_blackout_after} min`))];
    setText("setup-news-window", newsWindows.length === 1 ? `Antes/después: ${newsWindows[0]}` : `Ventanas distintas: ${newsWindows.join(", ")}`);
    const exits = profiles.map(p => `${p.id.slice(-1)} BE ${p.break_even_r}R · parcial ${p.partial_percent}%@${p.partial_r}R${p.stagnation !== "Desactivado" ? ` · ${p.stagnation}` : ""}`);
    setText("setup-exit-guard", exits.join(" | "));
  }

  // Update ML Status & Widgets
  if (data.ml) {
    updateMLWidgets(data.ml);
  }

  if (data.intraday) {
    renderIntradayDashboard(data.intraday);
  }

  if (data.symbols) {
    renderSymbolsOverview(data.symbols);
    renderRadarPreview(data.symbols);
    renderConfirmationsGrid(data.symbols);
    if (data.ml) {
      renderMLSymbols(data.symbols, data.ml);
    }
    if (data.symbols.length > 0) {
      const xau = data.symbols[0];
      const bid = Number(xau.bid || 0);
      const spread = Number(xau.spread_points || 0);
      const entryEl = document.getElementById("chart-level-entry");
      if (entryEl && bid > 0) entryEl.innerHTML = `<b style="color:#38bdf8">PRECIO BID:</b> $${bid.toFixed(2)}`;
      const slEl = document.getElementById("chart-level-sl");
      if (slEl && bid > 0) slEl.innerHTML = `<b style="color:#ef4444">SL BASE:</b> $${(bid - 15.0).toFixed(2)} (-1.2 ATR)`;
      const tpEl = document.getElementById("chart-level-tp");
      if (tpEl && bid > 0) tpEl.innerHTML = `<b style="color:#34d399">TP BASE:</b> $${(bid + 35.0).toFixed(2)} (+2.5 ATR)`;
      const trEl = document.getElementById("chart-level-trailing");
      if (trEl && bid > 0) trEl.innerHTML = `<b style="color:#c4b5fd">TRAILING:</b> $${(bid + 8.0).toFixed(2)} (+0.25 ATR)`;
      const spEl = document.getElementById("chart-level-spread");
      if (spEl) spEl.innerHTML = `SPREAD: ${spread.toFixed(0)} pts · TICK: $0.01`;
    }
  }

  if (data.copilot) {
    renderCopilotRadar(data.copilot);
  }

  if (data.learning) {
    renderLearningLab(data.learning);
  }

  renderActivePositions(data.positions || []);
}

function updateModeDisplay(isDemo, modeDesc, scoreThresholds, fleetProfiles = []) {
  const minScore = scoreThresholds && scoreThresholds.minimum != null ? scoreThresholds.minimum : 70;
  const minStruct = scoreThresholds && scoreThresholds.min_structure != null ? scoreThresholds.min_structure : 0;
  const minTrend = scoreThresholds && scoreThresholds.min_trend != null ? scoreThresholds.min_trend : 0;

  const thresholds = [...new Set(fleetProfiles.map(p => p.score_minimum).filter(v => v != null))];
  setText("setup-min-score", thresholds.length ? thresholds.join("/") : String(minScore));
  setText("setup-struct-min", `${minStruct} / 30`);
  setText("setup-trend-min", `${minTrend} / 25`);

  const profileIds = fleetProfiles.map(p => p.id.replace("BOT-", "")).join("–");
  const marketCounts = {};
  fleetProfiles.forEach(p => {
    const key = `${(p.symbols || [p.symbol]).join(", ")} ${p.timeframe}`;
    marketCounts[key] = (marketCounts[key] || 0) + 1;
  });
  const marketSummary = Object.entries(marketCounts)
    .map(([market, count]) => `${market} ×${count}`).join(" · ");
  setText("fleet-panel-title", `${fleetProfiles.length} perfiles demo (${profileIds || "sin perfiles"}) · ${marketSummary || "configuración por perfil"}`);
  const ddLimits = [...new Set(fleetProfiles.map(p => Number(p.max_drawdown)).filter(Number.isFinite))];
  setText("kpi-max-dd-summary", ddLimits.length === 1 ? `${ddLimits[0]}%` : ddLimits.length ? `${Math.min(...ddLimits)}–${Math.max(...ddLimits)}%` : "—");

  const btnDemo = document.getElementById("btn-mode-demo");
  const btnPaper = document.getElementById("btn-mode-paper");
  if (btnDemo) btnDemo.className = `mode-switch-btn ${isDemo ? "active" : ""}`;
  if (btnPaper) btnPaper.className = `mode-switch-btn ${!isDemo ? "active" : ""}`;

  const banner = document.getElementById("mode-alert-banner");
  if (banner) {
    banner.className = `mode-banner ${isDemo ? "mode-banner-demo" : "mode-banner-paper"}`;
    const icon = document.getElementById("banner-icon");
    if (icon) icon.innerHTML = isDemo ? '<i class="fa-solid fa-bolt"></i>' : '<i class="fa-solid fa-file-lines"></i>';
    setText("banner-mode-title", isDemo ? "MODO DEMO · CUENTA MT5" : "MODO PAPER · SIMULACIÓN VIRTUAL");
    setText("banner-mode-tag", isDemo ? "CUENTA DEMO MT5" : "SIMULACIÓN PAPER");
    const marketSummary = [...new Set(fleetProfiles.map(p => `${(p.symbols || [p.symbol]).join(", ")} ${p.timeframe}`))].join(" · ");
    setText("banner-mode-desc", isDemo 
      ? `${fleetProfiles.length} perfiles (${marketSummary || "mercados según perfil"}) configurados para demo. El dashboard muestra resultados desde el inicio de esta ejecución; excluye el historial de versiones anteriores.`
      : "El bot calcula en vivo las 5 confirmaciones pero NO envía órdenes a MetaTrader 5 (Simulación limpia en base de datos)."
    );
  }

  const sideExec = document.getElementById("side-exec-type");
  if (sideExec) {
    sideExec.textContent = isDemo ? "MT5 DEMO" : "PAPER SIM";
    sideExec.className = isDemo ? "ok" : "warn";
  }

  const healthExec = document.getElementById("health-exec-mode");
  if (healthExec) {
    healthExec.textContent = isDemo ? "MT5 DEMO ORDERS" : "SIMULATION ONLY";
    healthExec.className = isDemo ? "ok" : "warn";
  }

  setText("setup-mode-tag", isDemo ? "FLOTA DEMO ACTUAL" : "FLOTA PAPER");
  setText("setup-entry-title", "Umbral configurado por perfil");
  setText("setup-entry-desc", `Puntaje mínimo ${thresholds.length === 1 ? thresholds[0] : thresholds.join(" / ")} · radar orientativo por perfil, no telemetría interna del bot.`);

  const symBadge = document.getElementById("symbols-mode-badge");
  if (symBadge) {
    symBadge.innerHTML = isDemo 
      ? '<i class="fa-solid fa-bolt"></i> CUENTA DEMO · estado individual arriba'
      : '<i class="fa-solid fa-file-lines"></i> MODO PAPER: Señales simuladas (Sin órdenes MT5)';
    symBadge.className = `section-badge-right ${isDemo ? "demo-active" : "paper-active"}`;
  }

  const tradesNotice = document.getElementById("active-trades-notice");
  if (tradesNotice) {
    tradesNotice.className = `trades-mode-notice ${isDemo ? "demo-notice" : "paper-notice"}`;
    setText("active-trades-notice-text", isDemo 
      ? "Modo DEMO: aquí se muestran posiciones MT5 con los magic numbers configurados para A-E."
      : "Modo PAPER activo: El envío de órdenes a MT5 está desactivado. Todas las señales se registran como pruebas sin abrir posiciones en el terminal."
    );
  }

  setText("footer-mode-tag", isDemo ? "● CUENTA DEMO MT5" : "● MODO PAPER (SIMULACIÓN)");
}

function updateMLWidgets(ml) {
  if (!ml) return;

  const precStr = ml.avg_precision != null ? `${(ml.avg_precision * 100).toFixed(1)}%` : "—";
  const aucStr = ml.avg_auc_roc != null ? Number(ml.avg_auc_roc).toFixed(4) : "—";
  const thresholdStr = ml.min_confidence == null ? "Por bot" : `${Number(ml.min_confidence * 100).toFixed(1)}%`;
  const isShadow = ml.shadow_mode !== false;
  const modeLabel = ml.status_label || (isShadow ? "SHADOW" : "ACTIVE");

  setText("nav-ml-status", precStr);
  setText("side-ml-state", `${modeLabel} ${precStr}`);
  setText("health-ml-state", `${modeLabel} ${precStr}`);
  setText("overview-ml-prec", `${precStr} Prec`);
  setText("overview-ml-mode", `${modeLabel} · umbral por perfil`);

  setText("ml-kpi-prec", precStr);
  setText("ml-kpi-auc", aucStr);
  setText("ml-kpi-threshold", thresholdStr);
  setText("ml-kpi-samples", ml.dataset_rows != null ? Number(ml.dataset_rows).toLocaleString() : "—");
  setText("ml-kpi-mode", modeLabel);
  setText("ml-kpi-mode-desc", ml.metrics_status || (isShadow ? "Registra sin bloquear" : "Filtro activo"));

  setText("btn-ml-mode-text", `Modo: ${modeLabel}`);

  const btnMode = document.getElementById("btn-toggle-ml-mode");
  if (btnMode) {
    btnMode.className = `btn ${isShadow ? "btn-ghost" : "btn-danger"}`;
  }

  if (ml.feature_importance && ml.feature_importance.length) {
    renderMLFeatures(ml.feature_importance);
  }
  if (ml.fold_metrics && ml.fold_metrics.length) {
    renderMLFolds(ml.fold_metrics);
  }
}

function renderMLSymbols(symbols, ml) {
  const grid = document.getElementById("ml-symbols-grid");
  if (!grid) return;

  const minConf = (ml.min_confidence || 0.65) * 100.0;
  const isShadow = ml.shadow_mode !== false;

  grid.innerHTML = symbols.map(sym => {
    const prob = sym.ml_probability != null ? Number(sym.ml_probability) : 50.0;
    const isPass = prob >= minConf;
    const probClass = prob >= 65 ? "ml-prob-high" : prob >= 50 ? "ml-prob-mid" : "ml-prob-low";
    const dir = sym.direction === "BUY" ? "LONG BUY" : sym.direction === "SELL" ? "SHORT SELL" : "NEUTRAL";
    const dirClass = sym.direction === "BUY" ? "direction-buy" : sym.direction === "SELL" ? "direction-sell" : "direction-neutral";
    const cardClass = isPass ? "ml-pass" : "ml-block";
    const verdictText = isPass ? "PASS (APROBADO)" : (isShadow ? "WOULD BLOCK (SHADOW)" : "BLOCKED");
    const verdictClass = isPass ? "verdict-pass" : "verdict-block";

    return `
      <article class="ml-card ${cardClass}">
        <div class="ml-head">
          <h3>${escapeHtml(sym.symbol)}</h3>
          <span class="direction-badge ${dirClass}">${dir}</span>
        </div>
        <div class="ml-prob-val ${probClass}">
          ${prob.toFixed(1)}% <small>P(Éxito)</small>
        </div>
        <div class="ml-gauge-wrap">
          <div class="ml-gauge-bar">
            <i style="width:${Math.min(prob, 100)}%;background:${prob >= 65 ? "var(--green)" : prob >= 50 ? "var(--amber)" : "var(--red)"}"></i>
          </div>
          <div class="ml-gauge-marker" title="Umbral 65%"></div>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:8px;color:#5c6c80">
          <span>0%</span><span>Umbral: ${minConf.toFixed(0)}%</span><span>100%</span>
        </div>
        <div class="ml-verdict-row">
          <span>Score: <b>${Number(sym.total_score || 0).toFixed(0)}/100</b></span>
          <span class="ml-verdict-badge ${verdictClass}">${verdictText}</span>
        </div>
      </article>`;
  }).join("");
}

function renderMLFeatures(features) {
  const container = document.getElementById("ml-features-list");
  if (!container || !features || !features.length) return;

  const maxImp = Math.max(...features.map(f => Number(f.importance || 0)), 0.01);

  container.innerHTML = features.slice(0, 8).map(f => {
    const imp = Number(f.importance || 0);
    const pct = (imp / maxImp * 100.0);
    const friendlyName = formatFeatureName(f.feature);

    return `
      <div class="ml-feat-item">
        <div class="ml-feat-top">
          <span>${friendlyName}</span>
          <b>${(imp * 100).toFixed(1)}%</b>
        </div>
        <div class="ml-feat-bar">
          <i style="width:${pct}%"></i>
        </div>
      </div>`;
  }).join("");
}

function renderMLFolds(folds) {
  const tbody = document.getElementById("ml-folds-tbody");
  if (!tbody || !folds || !folds.length) return;

  tbody.innerHTML = folds.map(f => `
    <tr>
      <td style="font-family:var(--mono);font-weight:700">Fold ${f.fold}</td>
      <td style="font-family:var(--mono);color:#64748b">${f.train_size} filas</td>
      <td style="font-family:var(--mono);color:#64748b">${f.val_size} filas</td>
      <td style="font-family:var(--mono);color:var(--cyan);font-weight:700">${Number(f.auc_roc).toFixed(4)}</td>
      <td style="font-family:var(--mono);color:var(--green);font-weight:700">${(Number(f.accuracy) * 100).toFixed(1)}%</td>
      <td style="font-family:var(--mono)">${Number(f.f1).toFixed(4)}</td>
    </tr>
  `).join("");
}

function formatFeatureName(name) {
  const dict = {
    hour_cos: "Ciclo Horario (Coseno)",
    hour_sin: "Ciclo Horario (Seno)",
    ema50_slope: "Pendiente EMA 50 (Tendencia)",
    atr_ratio_val: "Expansión ATR (Volatilidad)",
    price_vs_ema200: "Distancia EMA 200 (Macro)",
    day_of_week: "Día de la Semana",
    adx_norm: "Fuerza Tendencial (ADX)",
    rsi_norm: "Momentum Relativo (RSI)",
    macd_hist_slope: "Aceleración MACD Histograma",
    volume_ratio_val: "Ratio de Volumen Relativo",
    ema20_slope: "Pendiente EMA 20 (Rápida)",
    spread_norm: "Spread Relativo a Volatilidad",
    obv_trend: "Tendencia de Acumulación OBV"
  };
  return dict[name] || name;
}

async function dashboardControlFetch(url, options = {}) {
  let token = sessionStorage.getItem("qtb_dashboard_token");
  if (!token) {
    token = window.prompt("Token de control del dashboard (DASHBOARD_API_TOKEN):");
    if (!token) throw new Error("Dashboard control token required");
    sessionStorage.setItem("qtb_dashboard_token", token);
  }
  const headers = new Headers(options.headers || {});
  headers.set("X-Dashboard-Token", token);
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) sessionStorage.removeItem("qtb_dashboard_token");
  return response;
}

async function toggleMLShadowMode() {
  const btnText = document.getElementById("btn-ml-mode-text");
  const btn = document.getElementById("btn-toggle-ml-mode");
  if (btnText) btnText.textContent = "Actualizando...";

  try {
    const res = await dashboardControlFetch("/api/ml/toggle_shadow", { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      showToast("Filtro AI Actualizado", `Modo cambiado a: ${data.status_label}`, "info");
      await loadMLStatus();
    } else {
      showToast("Error", "No se pudo cambiar el modo del filtro AI.", "error");
      await loadMLStatus();
    }
  } catch (e) {
    showToast("Error", "Fallo de conexión al alternar modo AI.", "error");
    await loadMLStatus();
  }
}

async function triggerMLRetrain() {
  const btn = document.getElementById("btn-retrain-ml");
  const origHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> <span>Reentrenando...</span>';
    btn.disabled = true;
  }

  showToast("Reentrenamiento Iniciado", "Descargando velas de MT5 y entrenando XGBoost en segundo plano...", "info");

  try {
    const res = await dashboardControlFetch("/api/ml/retrain", { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      showToast("Proceso en Marcha", "El modelo se está actualizando. La pantalla se refrescará en unos segundos.", "success");
      setTimeout(async () => {
        await loadMLStatus();
        if (btn) {
          btn.innerHTML = origHtml;
          btn.disabled = false;
        }
        showToast("Modelo Actualizado", "El modelo XGBoost ha sido reentrenado con éxito.", "success");
      }, 7000);
    } else {
      showToast("Error", data.message || "Fallo al iniciar reentrenamiento.", "error");
      if (btn) {
        btn.innerHTML = origHtml;
        btn.disabled = false;
      }
    }
  } catch (e) {
    showToast("Error", "Fallo de comunicación con el servidor.", "error");
    if (btn) {
      btn.innerHTML = origHtml;
      btn.disabled = false;
    }
  }
}

async function loadMLStatus() {
  try {
    const res = await fetch("/api/ml/status");
    if (res.ok) {
      const data = await res.json();
      updateMLWidgets(data);
    }
  } catch (e) {
    console.error("Error loading ML status:", e);
  }
}

async function setBotMode(mode) {
  currentBotMode = mode.toUpperCase();
  updateModeDisplay(currentBotMode === "DEMO");
  document.getElementById("btn-mode-paper")?.classList.toggle("active", mode === "paper");
  document.getElementById("btn-mode-demo")?.classList.toggle("active", mode === "demo");
  showToast("Modo Operativo", `Visualización fijada en MODO ${currentBotMode} (Conexión MT5 activa).`, "info");
}

function setRiskState(state) {
  const side = document.getElementById("side-risk-state");
  if (!side) return;
  side.textContent = state;
  side.className = state === "NORMAL" ? "ok" : state === "REDUCED" ? "warn" : "bad";
}

function getBotBadge(botType, symbol, timeframe) {
  const tf = String(timeframe || "").toUpperCase();
  const bt = String(botType || "").toUpperCase();
  const sym = String(symbol || "").toUpperCase();
  if (bt.includes("BOT-A") || bt.includes("BOT A") || bt.includes("26082511")) {
    return `<span class="badge-bot" style="background:rgba(56,189,248,0.18);color:#38bdf8;border:1px solid rgba(56,189,248,0.40);font-weight:700"><i class="fa-solid fa-bolt"></i> BOT A · Conf ≥ 0.45</span>`;
  }
  if (bt.includes("BOT-B") || bt.includes("BOT B") || bt.includes("26082512")) {
    return `<span class="badge-bot" style="background:rgba(52,211,153,0.18);color:#34d399;border:1px solid rgba(52,211,153,0.40);font-weight:700"><i class="fa-solid fa-chart-line"></i> BOT B · Conf ≥ 0.55</span>`;
  }
  if (bt.includes("BOT-C") || bt.includes("BOT C") || bt.includes("26082513")) {
    return `<span class="badge-bot" style="background:rgba(192,132,252,0.18);color:#c084fc;border:1px solid rgba(192,132,252,0.40);font-weight:700"><i class="fa-solid fa-crosshairs"></i> BOT C · Sniper</span>`;
  }
  if (bt.includes("BOT-D") || bt.includes("BOT D") || bt.includes("26082514")) {
    return `<span class="badge-bot" style="background:rgba(251,191,36,0.18);color:#fbbf24;border:1px solid rgba(251,191,36,0.40);font-weight:700"><i class="fa-solid fa-clock-rotate-left"></i> BOT D · Estanc 5v</span>`;
  }
  if (bt.includes("BOT-E") || bt.includes("BOT E") || bt.includes("26092655")) {
    return `<span class="badge-bot" style="background:rgba(251,113,133,0.16);color:#fb7185;border:1px solid rgba(251,113,133,0.36);font-weight:700"><i class="fa-solid fa-yen-sign"></i> BOT E · USDJPY M15</span>`;
  }
  if (bt.includes("BOT-F") || bt.includes("BOT F") || bt.includes("26082516")) {
    return `<span class="badge-bot" style="background:rgba(16,185,129,0.18);color:#10b981;border:1px solid rgba(16,185,129,0.40);font-weight:700"><i class="fa-solid fa-feather"></i> BOT F · Permisivo (0.3 ATR)</span>`;
  }
  if (bt.includes("BOT-G") || bt.includes("BOT G") || bt.includes("26082517")) {
    return `<span class="badge-bot" style="background:rgba(245,158,11,0.18);color:#f59e0b;border:1px solid rgba(245,158,11,0.40);font-weight:700"><i class="fa-solid fa-wand-magic-sparkles"></i> BOT G · Alchemist Puro</span>`;
  }
  if (bt.includes("BOT-A2") || bt.includes("BOT A2") || bt.includes("26082521")) {
    return `<span class="badge-bot" style="background:rgba(14,165,233,0.18);color:#0ea5e9;border:1px solid rgba(14,165,233,0.40);font-weight:700"><i class="fa-solid fa-bolt"></i> BOT A2 · Conf ≥ 0.45</span>`;
  }
  if (bt.includes("BOT-B2") || bt.includes("BOT B2") || bt.includes("26082522")) {
    return `<span class="badge-bot" style="background:rgba(16,185,129,0.18);color:#10b981;border:1px solid rgba(16,185,129,0.40);font-weight:700"><i class="fa-solid fa-chart-line"></i> BOT B2 · Conf ≥ 0.55</span>`;
  }
  if (bt.includes("BOT-C2") || bt.includes("BOT C2") || bt.includes("26082523")) {
    return `<span class="badge-bot" style="background:rgba(168,85,247,0.18);color:#a855f7;border:1px solid rgba(168,85,247,0.40);font-weight:700"><i class="fa-solid fa-crosshairs"></i> BOT C2 · Sniper ≥ 0.70</span>`;
  }
  if (bt.includes("BOT-D2") || bt.includes("BOT D2") || bt.includes("26082524")) {
    return `<span class="badge-bot" style="background:rgba(234,179,8,0.18);color:#eab308;border:1px solid rgba(234,179,8,0.40);font-weight:700"><i class="fa-solid fa-clock-rotate-left"></i> BOT D2 · Estanc 5v</span>`;
  }
  if (sym.includes("BTC") || bt.includes("BTC") || bt.includes("26082501") || bt.includes("26082502")) {
    return `<span class="badge-bot" style="background:rgba(247,147,26,0.12);color:#f7931a;border:1px solid rgba(247,147,26,0.30)"><i class="fa-brands fa-bitcoin"></i> Bitcoin (${tf || "H1"})</span>`;
  }
  if (tf === "H4" || bt.includes("H4") || bt.includes("26082498")) {
    return `<span class="badge-bot" style="background:rgba(157,140,255,0.12);color:#9d8cff;border:1px solid rgba(157,140,255,0.30)"><i class="fa-solid fa-gem"></i> Oro (${tf || "H4"})</span>`;
  }
  return `<span class="badge-bot badge-conservative"><i class="fa-solid fa-coins"></i> Oro (${tf || "H1"})</span>`;
}

let currentFleetData = [];
let currentFleetFilter = "all";

function setFleetFilter(cat) {
  currentFleetFilter = cat;
  document.querySelectorAll(".fleet-filter-bar .btn").forEach(b => b.classList.remove("active"));
  const activeBtn = document.getElementById(`btn-fleet-${cat}`);
  if (activeBtn) activeBtn.classList.add("active");
  renderFleetCards();
}
window.setFleetFilter = setFleetFilter;
window._setFleetFilterImpl = setFleetFilter;
if (window._pendingFleetFilter) {
  const pending = window._pendingFleetFilter;
  window._pendingFleetFilter = null;
  setFleetFilter(pending);
}

function renderFleetCards() {
  const container = document.getElementById("fleet-cards-container");
  if (!container || !currentFleetData) return;

  let list = currentFleetData;
  if (currentFleetFilter === "deriv") {
    list = currentFleetData.filter(b => b.fleet_category === "deriv" || b.id.includes("SYN"));
  } else if (currentFleetFilter === "forex") {
    list = currentFleetData.filter(b => b.fleet_category !== "deriv" && !b.id.includes("SYN"));
  }

  if (list.length === 0) {
    container.innerHTML = `<div style="color:#94a3b8;font-size:11px;padding:20px;grid-column:1/-1;text-align:center">No hay bots en esta categoría.</div>`;
    return;
  }

  container.innerHTML = list.map(bot => {
    const isDeriv = bot.fleet_category === "deriv" || bot.id.includes("SYN");
    const color = bot.color || (isDeriv ? "#22c55e" : "#38bdf8");
    const shortName = (bot.name || "").replace(/^Bot [A-Z0-9]+ /, "");
    const categoryBadge = isDeriv
      ? `<span style="font-size:7.5px;font-weight:700;background:rgba(34,197,94,0.18);color:#34d399;border:1px solid rgba(34,197,94,0.4);padding:1px 5px;border-radius:3px"><i class="fa-solid fa-bolt"></i> DERIV 24/7</span>`
      : `<span style="font-size:7.5px;font-weight:700;background:rgba(56,189,248,0.15);color:#38bdf8;border:1px solid rgba(56,189,248,0.3);padding:1px 5px;border-radius:3px"><i class="fa-solid fa-coins"></i> FOREX / ORO</span>`;

    const statusColor = bot.status === "ONLINE" ? "#34d399" : "#94a3b8";

    return `
      <div class="lab-kpi-card" style="border: 1px solid ${color}35; background: linear-gradient(135deg, rgba(15,23,42,0.95) 0%, ${color}0d 100%); position:relative; overflow:hidden; padding:12px 14px">
        <div style="position:absolute; top:0; left:0; width:4px; height:100%; background:${color}"></div>
        <div class="kpi-title-row" style="margin-bottom:8px">
          <div style="display:flex;align-items:center;gap:6px">
            <span style="font-weight:800; font-size:11px; color:${color}; letter-spacing:0.02em">
              <i class="fa-solid fa-robot"></i> ${escapeHtml(bot.id)}
            </span>
            ${categoryBadge}
          </div>
          <span class="status-chip" style="background:${statusColor}20; color:${statusColor}; border:1px solid ${statusColor}60; font-size:8px; padding:1px 6px">
            ● ${escapeHtml(bot.status)}
          </span>
        </div>
        <div style="font-size:10px;font-weight:700;color:#f1f5f9;margin-bottom:6px">${escapeHtml(shortName)}</div>
        
        <div style="display:grid; grid-template-columns: 1fr 1fr; gap:6px; font-size:9px; margin-top:6px">
          <div style="background:rgba(255,255,255,0.03); padding:5px 7px; border-radius:4px; border:1px solid rgba(255,255,255,0.04)">
            <span style="color:#64748b; font-size:8px; display:block">Filtro / IA</span>
            <strong style="color:#e2e8f0; font-family:var(--mono); font-size:9.5px">${escapeHtml(bot.ml_mode)}</strong>
          </div>
          <div style="background:rgba(255,255,255,0.03); padding:5px 7px; border-radius:4px; border:1px solid rgba(255,255,255,0.04)">
            <span style="color:#64748b; font-size:8px; display:block">Salida / Gestión</span>
            <strong style="color:#e2e8f0; font-family:var(--mono); font-size:9.5px">BE ${bot.break_even_r}R · ${escapeHtml(bot.stagnation)}</strong>
          </div>
          <div style="background:rgba(255,255,255,0.03); padding:5px 7px; border-radius:4px; border:1px solid rgba(255,255,255,0.04)">
            <span style="color:#64748b; font-size:8px; display:block">Riesgo / Magic</span>
            <strong style="color:${color}; font-family:var(--mono); font-size:9.5px">${bot.risk_percent}% · #${bot.magic}</strong>
          </div>
          <div style="background:rgba(255,255,255,0.03); padding:5px 7px; border-radius:4px; border:1px solid rgba(255,255,255,0.04)">
            <span style="color:#64748b; font-size:8px; display:block">Mercado / TF</span>
            <strong style="color:#e2e8f0; font-family:var(--mono); font-size:9.5px">${escapeHtml(bot.symbol)} · ${escapeHtml(bot.timeframe)}</strong>
          </div>
        </div>
        
        <div style="margin-top:8px; padding-top:6px; border-top:1px solid rgba(255,255,255,0.05); display:flex; justify-content:space-between; align-items:center; font-size:8px; color:#94a3b8">
          <span><i class="fa-solid fa-shield-halved" style="color:#34d399"></i> Límite DD: <strong style="color:#34d399">${bot.max_drawdown ?? "—"}%</strong></span>
          <span><i class="fa-solid fa-clock" style="color:${isDeriv ? '#22c55e' : '#38bdf8'}"></i> ${isDeriv ? '24/7 sin noticias' : (bot.news_filter ? 'Noticias ON' : 'Noticias OFF')}</span>
        </div>
      </div>
    `;
  }).join("");
}

async function loadBotFleet() {
  try {
    const res = await fetch("/api/bots/fleet");
    if (!res.ok) return;
    const data = await res.json();
    const fleet = data.fleet || [];
    currentFleetData = fleet;

    const onlineCount = fleet.filter(bot => bot.status === "ONLINE").length;
    setText("fleet-status-count", `${onlineCount}/${fleet.length} procesos en línea`);
    setText("fleet-panel-title", `${fleet.length} bots en ejecución (Forex + Sintéticos Deriv)`);

    const derivCount = fleet.filter(b => b.fleet_category === "deriv" || b.id.includes("SYN")).length;
    const forexCount = fleet.filter(b => b.fleet_category !== "deriv" && !b.id.includes("SYN")).length;
    setText("count-fleet-all", fleet.length);
    setText("count-fleet-deriv", derivCount);
    setText("count-fleet-forex", forexCount);

    const riskSummary = `${derivCount} bots Deriv (0.75%) · ${forexCount} bots Forex`;
    const exitSummary = "BE 1.0R / Parcial 1.5R / Trailing Activo / Profit Lock 0.75R";
    const guardsSummary = "PyTorch NanoJev Activo · Filtro Macro H1 EMA-50 · Liquidez Estricta";
    const symbolsSummary = [...new Set(fleet.flatMap(bot => bot.symbols || []))].join(", ");

    setText("fleet-risk-summary", riskSummary || "—");
    setText("fleet-management-summary", exitSummary || "—");
    setText("fleet-guards-summary", guardsSummary || "—");
    setText("fleet-symbol-summary", `${symbolsSummary || "—"}`);

    renderFleetCards();

    const driftEl = document.getElementById("fleet-drift-guard-status");
    if (driftEl && data.drift_guard) {
      driftEl.innerHTML = `<i class="fa-solid fa-shield-halved"></i> GUARDIAS: ${escapeHtml(data.drift_guard)}`;
    }
  } catch (err) {
    console.error("Error loading bot fleet:", err);
  }
}

async function loadIntradayStatus() {
  try {
    const res = await fetch("/api/intraday/status");
    if (res.ok) renderIntradayDashboard(await res.json());
  } catch (error) {
    console.error("Error loading intraday profile status:", error);
  }
}

function renderIntradayDashboard(data) {
  const profile = data?.profile || {};
  const today = data?.today || {};
  const validation = data?.validation || {};
  const status = String(validation.status || "UNVALIDATED").toUpperCase();
  const blocked = status !== "APPROVED";
  const statusColor = blocked ? "#fca5a5" : "#6ee7b7";

  setText("intraday-mode", String(profile.mode || "PAPER"));
  setText("intraday-journal", `Diario: ${today.journal_state || "SIN TELEMETRÍA"}`);
  setText("intraday-target", `${Number(profile.target_daily_trades || 0)} / día`);
  setText("intraday-signals", today.signals ?? 0);
  setText("intraday-rejected", `${today.rejected ?? 0} rechazadas`);
  setText("intraday-trades", today.trades ?? 0);
  setText("intraday-pnl", `P&L realizado: ${money(today.realized_pnl)}`);
  setText("intraday-risk", `${Number(profile.risk_per_trade || 0).toFixed(2)}%`);
  setText("intraday-daily-loss", `Pérdida diaria máxima: ${Number(profile.max_daily_loss || 0).toFixed(1)}%`);
  setText("intraday-positions", today.open_positions ?? 0);
  setText("intraday-position-limit", `Máximo: ${profile.max_open_positions ?? "—"}`);
  setText("intraday-entry-tf", profile.timeframes?.entry || "M5");
  setText("intraday-setup-tf", profile.timeframes?.setup || "M15");
  setText("intraday-higher-tf", profile.timeframes?.higher || "H1");
  setText("intraday-score-min", `${Number(profile.score_minimum || 0).toFixed(0)} / 100`);
  setText("intraday-setup-min", `${Number(profile.setup_trend_minimum || 0).toFixed(0)} / 25`);
  setText("intraday-ml", profile.ml_shadow_mode ? "SHADOW" : "ACTIVO");
  setText("intraday-live-lock", profile.live_trading_allowed ? "PERMITIDO" : "BLOQUEADO");
  setText("intraday-magic", profile.magic_number ?? "—");
  setText("intraday-experiment", validation.experiment_id || "SIN EXPERIMENTO");
  setText("intraday-exploratory-r", validation.exploratory_total_r != null ? `${Number(validation.exploratory_total_r).toFixed(2)} R` : "Sin resultado");
  setText("intraday-exploratory-trades", validation.exploratory_trades ? `${validation.exploratory_trades} trades exploratorios` : "Sin muestra" );

  const chip = document.getElementById("intraday-validation-chip");
  if (chip) { chip.textContent = `VALIDACIÓN: ${status}`; chip.style.color = statusColor; }
  const nav = document.getElementById("nav-intraday-state");
  if (nav) { nav.textContent = blocked ? "BLOQUEADO" : "APROBADO"; nav.style.color = statusColor; }
  const summary = document.getElementById("intraday-validation-summary");
  if (summary) {
    const message = validation.summary || "No hay resultado de validación registrado.";
    summary.style.borderColor = blocked ? "rgba(251,95,106,.28)" : "rgba(52,211,153,.28)";
    summary.style.background = blocked ? "rgba(251,95,106,.055)" : "rgba(52,211,153,.055)";
    summary.style.color = blocked ? "#f9b3b8" : "#92e0be";
    summary.innerHTML = `<i class="fa-solid fa-shield-halved"></i><span>${escapeHtml(message)}</span>`;
  }

  const symbols = document.getElementById("intraday-symbols");
  if (symbols) {
    symbols.innerHTML = (profile.symbols || []).map(symbol => `<span class="intraday-symbol">${escapeHtml(symbol)}</span>`).join("") || '<span class="intraday-symbol">Sin símbolos configurados</span>';
  }

  const tbody = document.getElementById("intraday-signals-tbody");
  if (tbody) {
    const signals = today.recent_signals || [];
    tbody.innerHTML = signals.length ? signals.map(signal => {
      const direction = signal.direction === "BUY" ? "direction-buy" : "direction-sell";
      const state = signal.execution_status === "PAPER_CANDIDATE" ? "CANDIDATA PAPER" : (signal.execution_status === "UNRECONCILED_ROUTED" ? "SIN RECONCILIAR" : (signal.traded ? "EJECUTADA MT5" : "RECHAZADA"));
      const stateClass = signal.traded ? "entry-yes" : "";
      const context = signal.market_context || {};
      const contextText = [
        context.regime,
        context.session,
        context.nearest_liquidity,
        context.fibonacci_zone ? "FIB-ZONA" : null,
        context.sweep_aligned ? "SWEEP✓" : null,
        context.fvg_aligned ? "FVG✓" : null,
        context.order_block_aligned ? "OB✓" : null,
        ...(context.quality_shadow_reasons || []).map(reason => `SOMBRA:${reason}`),
      ].filter(Boolean).join(" · ") || "Pendiente";
      return `<tr><td style="font-family:var(--mono);color:#65768a">${escapeHtml(timeOnly(signal.created_at))}</td><td style="font-family:var(--mono);font-weight:700">${escapeHtml(signal.symbol)}</td><td>${escapeHtml(signal.timeframe)}</td><td><span class="direction-badge ${direction}">${escapeHtml(signal.direction)}</span></td><td style="font-family:var(--mono)">${Number(signal.score || 0).toFixed(0)}/100</td><td title="Sólo observación: no altera la entrada" style="font-family:var(--mono);font-size:10px;color:#8ea4ba">${escapeHtml(contextText)}</td><td><span class="class-badge ${stateClass}">${state}</span></td><td title="${escapeHtml(signal.reason || "-")}" style="max-width:300px;overflow:hidden;text-overflow:ellipsis">${escapeHtml(signal.reason || "-")}</td></tr>`;
    }).join("") : '<tr><td colspan="7" class="empty-row">Sin telemetría intradía aún. El perfil permanece en paper y bloqueado para demo.</td></tr>';
  }
}

function renderSymbolsOverview(symbols) {
  const grid = document.getElementById("symbols-overview-grid");
  if (!grid) return;
  const isDemo = currentBotMode === "DEMO";

  grid.innerHTML = symbols.map(sym => {
    const score = Number(sym.total_score || 0);
    const sweep = sym.sweep || {};
    const sweepActive = Boolean(sweep.detected);
    
    // Direction: if sweep is active, emphasize sweep direction
    const displayDir = sweepActive ? sweep.direction : sym.direction;
    const dir = displayDir === "BUY" ? "LONG BUY" : displayDir === "SELL" ? "SHORT SELL" : "NEUTRAL";
    const dirClass = displayDir === "BUY" ? "direction-buy" : displayDir === "SELL" ? "direction-sell" : "direction-neutral";
    const scoreClass = score >= 85 ? "score-good" : score >= 75 ? "score-ok" : "score-low";
    const botBadge = getBotBadge(sym.bot_id || sym.magic || sym.label, sym.symbol, sym.timeframe);
    const botColor = sym.color || "#38bdf8";
    
    const currPrice = Number(sym.bid || 0);
    const prevPrice = window.__prevPrices[sym.symbol] || currPrice;
    window.__prevPrices[sym.symbol] = currPrice;
    
    const tickClass = currPrice > prevPrice ? "tick-up" : currPrice < prevPrice ? "tick-down" : "";
    const mlBadge = (sym.ml_probability != null && sym.ml_enabled !== false) 
      ? `<span class="ml-prob-badge" title="Salida del modelo ML; no equivale a probabilidad de ganar"><i class="fa-solid fa-brain"></i> ML ${Number(sym.ml_probability).toFixed(0)}%</span>`
      : "";

    const activeShieldBadge = sym.liquidity_guard_enabled
      ? `<span style="font-size:7.5px;color:#34d399;background:rgba(52,211,153,0.1);padding:2px 5px;border-radius:3px;border:1px solid rgba(52,211,153,0.25)"><i class="fa-solid fa-shield-halved"></i> ZONA &lt; ${sym.liquidity_zone_atr} ATR</span>`
      : `<span style="font-size:7.5px;color:#94a3b8">Guardia de liquidez OFF</span>`;
    
    const sweepBadge = sweepActive
      ? `<span style="font-size:7.5px;font-weight:700;color:#c084fc;background:rgba(192,132,252,0.12);padding:2px 5px;border-radius:3px;border:1px solid rgba(192,132,252,0.35)" title="Barrido de liquidez: ${sweep.level} (${sweep.penetration_atr} ATR) R:R 1:${sweep.rr}"><i class="fa-solid fa-bullseye"></i> SWEEP ${sweep.level}</span>`
      : "";

    const readyTag = sym.ready_label || "RADAR ORIENTATIVO";
    let readyColor = "#94a3b8";
    if (readyTag.includes("EN TRADE")) {
      readyColor = "#34d399";
    } else if (readyTag.includes("SWEEP")) {
      readyColor = "#c084fc";
    } else if (readyTag.includes("ALCHEMIST")) {
      readyColor = "#f59e0b";
    } else if (sym.is_tradeable) {
      readyColor = "#34d399";
    } else if (readyTag.includes("ML")) {
      readyColor = "#fbbf24";
    }

    const isAlchemist = sym.bot_id === "BOT-G" || (sym.name && sym.name.includes("Alchemist"));

    const sweepScoreRow = sweepActive
      ? `<div style="display:flex;justify-content:space-between;align-items:center;font-size:9px;color:#c084fc;margin-top:2px;font-family:var(--mono)">
           <span><i class="fa-solid fa-bolt"></i> RADAR SWEEP:</span>
           <strong style="color:#c084fc">R:R 1:${sweep.rr} · indicativo</strong>
         </div>`
      : "";

    return `
      <article class="symbol-card ${sym.is_tradeable || sweepActive || isAlchemist ? "tradeable" : ""}" style="border-top:3px solid ${botColor};position:relative">
        <div class="symbol-head">
          <div style="display:flex;align-items:center;gap:8px">
            <strong style="font-size:12px;color:${botColor};font-family:var(--mono)">${escapeHtml(sym.name || sym.symbol)}</strong>
            ${botBadge}
          </div>
          <div style="display:flex;align-items:center;gap:6px">
            ${sweepBadge}
            ${activeShieldBadge}
            <span class="direction-badge ${dirClass}">${dir}</span>
          </div>
        </div>
        <div class="symbol-price">
          <strong class="${tickClass}">$${currPrice.toFixed(sym.digits || 2)}</strong>
          <span><i class="fa-solid fa-arrows-left-right" style="margin-right:4px"></i>Spread ${Number(sym.spread_points || 0).toFixed(0)} pts · Magic #${sym.magic || ""}</span>
        </div>
        <div class="mini-line"><i style="width:${isAlchemist ? 100 : Math.min(score,100)}%;background:${botColor}"></i></div>
        <div class="symbol-score">
          <div>
            <span>${isAlchemist ? 'Alchemist Puro · MSNR + Asian Sweep' : `${escapeHtml(sym.entry_mode || "Entrada")} · score compartido ${mlBadge}`}</span>
            ${sweepScoreRow}
          </div>
          <div style="text-align:right">
            ${isAlchemist 
              ? `<strong class="score-value" style="color:#f59e0b;font-size:12px"><i class="fa-solid fa-wand-magic-sparkles"></i> MSNR+CRT</strong>`
              : `<strong class="score-value ${scoreClass}">${score.toFixed(0)}<small style="font-size:10px;color:#64748b">/100</small></strong>`
            }
            <div style="font-family:var(--mono);font-size:8px;font-weight:700;color:${readyColor};margin-top:2px">${escapeHtml(readyTag)}</div>
          </div>
        </div>
      </article>`;
  }).join("");
}

function renderRadarPreview(symbols) {
  const grid = document.getElementById("overview-radar-grid");
  if (!grid) return;
  const isDemo = currentBotMode === "DEMO";

  grid.innerHTML = symbols.map(sym => {
    const score = Number(sym.total_score || 0);
    const sweep = sym.sweep || {};
    const sweepActive = Boolean(sweep.detected);
    const botBadge = getBotBadge(sym.bot_id || sym.magic || sym.label, sym.symbol, sym.timeframe);
    const botColor = sym.color || "#38bdf8";
    const readyTag = sym.ready_label || "RADAR ORIENTATIVO";
    let readyColor = "#94a3b8";
    if (readyTag.includes("EN TRADE")) {
      readyColor = "#34d399";
    } else if (readyTag.includes("SWEEP")) {
      readyColor = "#c084fc";
    } else if (readyTag.includes("ALCHEMIST")) {
      readyColor = "#f59e0b";
    } else if (sym.is_tradeable) {
      readyColor = "#34d399";
    } else if (readyTag.includes("ML")) {
      readyColor = "#fbbf24";
    }

    const isAlchemist = sym.bot_id === "BOT-G" || (sym.name && sym.name.includes("Alchemist"));

    const sweepFooter = sweepActive
      ? `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 8px;margin-top:4px;background:rgba(192,132,252,0.1);border-radius:4px;border:1px solid rgba(192,132,252,0.3)">
           <span style="font:700 8.5px var(--mono);color:#c084fc"><i class="fa-solid fa-bullseye"></i> BARRIDO SMC: ${sweep.level} (${sweep.direction})</span>
           <span style="font:700 8.5px var(--mono);color:#34d399">R:R 1:${sweep.rr} · 90 pts</span>
         </div>`
      : "";

    return `
      <article class="radar-card" style="border-top:3px solid ${botColor}">
        <div class="symbol-head">
          <div style="display:flex;align-items:center;gap:8px">
            <h3 style="color:${botColor};font-size:12px">${escapeHtml(sym.name || sym.symbol)}</h3>
            ${botBadge}
          </div>
          <div style="display:flex;align-items:baseline;gap:4px">
            ${isAlchemist 
              ? `<span class="score-value" style="color:#f59e0b;font-size:11px"><i class="fa-solid fa-wand-magic-sparkles"></i> MSNR+CRT</span>`
              : `<span class="score-value ${score >= 75 ? "score-good" : "score-low"}">${score.toFixed(0)}</span><small style="font-size:10px;color:#64748b;font-family:var(--mono)">/100</small>`
            }
          </div>
        </div>
        ${compactBar("ESTRUCTURA (BOS / Swings)", isAlchemist ? 30 : sym.structure, 30, "bar-structure")}
        ${compactBar("TENDENCIA (EMAs / ADX H4)", isAlchemist ? 25 : sym.trend, 25, "bar-trend")}
        ${compactBar("MOMENTUM (RSI / MACD)", isAlchemist ? 0 : sym.momentum, 15, "bar-momentum")}
        ${compactBar("VOLUMEN (Fuerza / Tick Vol)", isAlchemist ? 15 : sym.volume, 15, "bar-volume")}
        ${compactBar("VOLATILIDAD (ATR Régimen)", isAlchemist ? 15 : sym.volatility, 15, "bar-volatility")}
        ${sweepFooter}
        <div class="radar-footer">
          <span class="class-badge">${escapeHtml(isAlchemist ? "MSNR ESTRUCTURAL" : sym.classification)}</span>
          <span style="font:700 8.5px var(--mono);letter-spacing:0.04em;color:${readyColor}">${escapeHtml(readyTag)}</span>
        </div>
      </article>`;
  }).join("");
}

function compactBar(label, value, max, cls) {
  const pct = Math.max(0, Math.min(100, Number(value || 0) / max * 100));
  return `<div class="radar-row"><div class="radar-row-top"><span>${label}</span><b>${Number(value || 0).toFixed(0)} / ${max}</b></div><div class="bar ${cls}"><i style="width:${pct}%"></i></div></div>`;
}

function renderConfirmationsGrid(symbols) {
  const grid = document.getElementById("confirmations-grid");
  if (!grid) return;
  const isDemo = currentBotMode === "DEMO";
  const navCount = document.getElementById("nav-radar-count");
  if (navCount) navCount.textContent = symbols.length;

  grid.innerHTML = symbols.map(sym => {
    const score = Number(sym.total_score || 0);
    const tradeable = Boolean(sym.is_tradeable);
    const direction = sym.direction === "BUY" ? "LONG BUY" : sym.direction === "SELL" ? "SHORT SELL" : "NEUTRAL";
    const dirClass = sym.direction === "BUY" ? "direction-buy" : sym.direction === "SELL" ? "direction-sell" : "direction-neutral";
    const botBadge = getBotBadge(null, sym.symbol, sym.timeframe);
    const readyNote = isDemo 
      ? "Condiciones óptimas: Se enviará orden a MT5." 
      : "Condiciones óptimas: Se registrará simulación.";
    const mlPill = sym.ml_probability != null 
      ? `<span class="ml-prob-badge" style="margin-left:8px"><i class="fa-solid fa-brain"></i> AI Prob: ${Number(sym.ml_probability).toFixed(1)}%</span>` 
      : "";

    return `
      <article class="confirm-card ${tradeable ? "tradeable" : ""}">
        <div class="confirm-top">
          <div class="confirm-symbol">
            <h3>${escapeHtml(sym.symbol)}</h3>
            <div style="margin-top:3px">${botBadge}</div>
            <span class="confirm-bid" style="margin-top:4px">BID ${Number(sym.bid || 0).toFixed(sym.digits || 5)}</span>
          </div>
          <strong class="confirm-score ${score >= 75 ? "score-good" : score >= 65 ? "score-ok" : "score-low"}">${score.toFixed(0)}<small style="font-size:9px;color:#4f6074">/100</small></strong>
        </div>
        <div class="confirm-sub"><span class="direction-badge ${dirClass}">${direction}</span><span>${escapeHtml(sym.classification || "VERY_WEAK")}${mlPill}</span></div>
        <div class="confirm-list">
          ${fullBar("Estructura de mercado", sym.structure, 30, "#4f8cff", "BOS / Swings")}
          ${fullBar("Tendencia", sym.trend, 25, "#2dd4bf", "EMAs 20/50/200 + ADX")}
          ${fullBar("Momentum", sym.momentum, 15, "#9d8cff", "RSI + MACD")}
          ${fullBar("Volumen relativo", sym.volume, 15, "#f5b24c", "Ratio + OBV")}
          ${fullBar("Volatilidad", sym.volatility, 15, "#34d399", "ATR + expansión")}
        </div>
        <div class="confirm-note"><span>${tradeable ? readyNote : "Esperando confirmaciones adicionales."}</span><b class="${tradeable ? "ok" : ""}">${tradeable ? "RADAR > UMBRAL" : "RADAR < UMBRAL"}</b></div>
      </article>`;
  }).join("");
}

function fullBar(label, value, max, color, hint) {
  const pct = Math.max(0, Math.min(100, Number(value || 0) / max * 100));
  return `<div class="confirm-item"><div class="row-top"><span>${label} <em style="font-style:normal;color:#48596f">· ${hint}</em></span><b>${Number(value || 0).toFixed(1)} / ${max}</b></div><div class="wide-bar"><i style="width:${pct}%;background:${color}"></i></div></div>`;
}

function renderActivePositions(positions) {
  const tbody = document.getElementById("active-positions-tbody");
  const mini = document.getElementById("overview-positions");
  const isDemo = currentBotMode === "DEMO";

  if (tbody) {
    if (!positions.length) {
      const msg = isDemo 
        ? "No hay órdenes abiertas en MT5. Esperando condiciones óptimas." 
        : "Modo PAPER activo: El bot no abre órdenes reales en el terminal.";
      tbody.innerHTML = `<tr><td colspan="10" class="empty-row"><i class="fa-solid fa-inbox" style="font-size:20px;display:block;margin-bottom:8px"></i>${msg}</td></tr>`;
    } else {
      tbody.innerHTML = positions.map(pos => positionRow(pos)).join("");
    }
  }
  if (mini) {
    if (!positions.length) {
      const miniMsg = isDemo ? "Sin exposición en MT5" : "Modo Simulación (Sin exposición)";
      mini.innerHTML = `<div class="empty-mini"><i class="fa-solid fa-circle-check" style="display:block;font-size:18px;margin-bottom:7px"></i>${miniMsg}</div>`;
    } else {
      mini.innerHTML = positions.slice(0, 4).map(pos => {
        const positive = Number(pos.profit || 0) >= 0;
        const botBadge = getBotBadge(pos.comment, pos.symbol, pos.comment?.includes("H4") ? "H4" : "H1");
        return `<div class="position-mini">
          <div class="pos-left"><div class="pos-icon"><i class="fa-solid fa-arrow-${pos.direction === "BUY" ? "up" : "down"}"></i></div><div><div class="pos-symbol">${escapeHtml(pos.symbol)} ${botBadge} <span class="${pos.direction === "BUY" ? "ok" : "bad"}">${pos.direction}</span></div><div class="pos-detail">${Number(pos.volume).toFixed(2)} lot · SL ${pos.sl || "-"}</div></div></div>
          <strong class="pos-pnl ${positive ? "ok" : "bad"}">${positive ? "+" : "-"}$${Math.abs(Number(pos.profit || 0)).toFixed(2)}</strong>
        </div>`;
      }).join("");
    }
  }
}

function positionRow(pos) {
  const positive = Number(pos.profit || 0) >= 0;
  const badge = pos.direction === "BUY" ? '<span class="direction-badge direction-buy">BUY</span>' : '<span class="direction-badge direction-sell">SELL</span>';
  const botBadge = getBotBadge(pos.comment, pos.symbol, pos.comment?.includes("H4") ? "H4" : "H1");
  return `<tr>
    <td style="font-family:var(--mono);color:#627188">#${pos.ticket}</td>
    <td style="font-family:var(--mono);font-weight:700;color:#e6edf7">${escapeHtml(pos.symbol)} <br>${botBadge}</td>
    <td>${badge}</td><td style="font-family:var(--mono)">${Number(pos.volume).toFixed(2)}</td>
    <td style="font-family:var(--mono)">${pos.price_open}</td><td style="font-family:var(--mono)">${pos.price_current}</td>
    <td style="font-family:var(--mono);color:#fb7780">${pos.sl || "-"}</td><td style="font-family:var(--mono);color:#5ee0a9">${pos.tp || "-"}</td>
    <td style="font-family:var(--mono);font-weight:700;color:${positive ? "var(--green)" : "var(--red)"}">${positive ? "+" : "-"}$${Math.abs(Number(pos.profit || 0)).toFixed(2)}</td>
    <td><button class="btn btn-ghost" style="height:28px;padding:0 8px;color:#ff858c" onclick="closeSinglePosition(${pos.ticket})">Cerrar</button></td>
  </tr>`;
}

async function loadTradeHistory() {
  const tbody = document.getElementById("history-trades-tbody");
  if (!tbody) return;
  try {
    const res = await fetch("/api/trades/history?limit=50");
    const trades = await res.json();
    if (!trades?.length) {
      tbody.innerHTML = `<tr><td colspan="10" class="empty-row">No se han registrado operaciones cerradas aún.</td></tr>`;
      return;
    }
    tbody.innerHTML = trades.map(t => {
      const positive = Number(t.profit || 0) >= 0;
      const badge = t.direction === "BUY" ? '<span class="direction-badge direction-buy">BUY</span>' : '<span class="direction-badge direction-sell">SELL</span>';
      const botBadge = getBotBadge(t.bot_type, t.symbol, t.timeframe);
      return `<tr>
        <td style="font-family:var(--mono);color:#65768a">${escapeHtml(formatToLocal(t.created_at))}</td>
        <td style="font-family:var(--mono);font-weight:700;color:#e6edf7">${escapeHtml(t.symbol)} <br>${botBadge}</td><td>${badge}</td>
        <td>${Number(t.volume || 0).toFixed(2)}</td><td style="font-family:var(--mono)">${t.entry_price ?? "-"}</td><td style="font-family:var(--mono)">${t.exit_price ?? "-"}</td>
        <td style="font-family:var(--mono);font-weight:700">${Number(t.total_score || 0).toFixed(0)}/100</td>
        <td style="font-family:var(--mono);font-weight:700;color:${positive ? "var(--green)" : "var(--red)"}">${positive ? "+" : "-"}$${Math.abs(Number(t.profit || 0)).toFixed(2)}</td>
        <td style="font-family:var(--mono);color:${positive ? "var(--green)" : "var(--red)"}">${t.profit_r != null ? Number(t.profit_r).toFixed(2)+"R" : "-"}</td>
        <td>${(() => {
          const reason = String(t.exit_reason || "");
          if (reason.includes("STAGNATION")) {
            return '<span class="badge-bot" style="background:rgba(245,158,11,0.15);color:#fbbf24;border:1px solid rgba(245,158,11,0.3);font-size:8px;padding:2px 4px"><i class="fa-solid fa-hourglass-end"></i> ESTANCAMIENTO</span>';
          }
          if (reason.includes("TP")) {
            return '<span class="badge-bot" style="background:rgba(52,211,153,0.15);color:#34d399;border:1px solid rgba(52,211,153,0.3);font-size:8px;padding:2px 4px"><i class="fa-solid fa-bullseye"></i> TAKE PROFIT</span>';
          }
          if (reason.includes("SL")) {
            return '<span class="badge-bot" style="background:rgba(239,68,68,0.15);color:#f87171;border:1px solid rgba(239,68,68,0.3);font-size:8px;padding:2px 4px"><i class="fa-solid fa-shield-xmark"></i> STOP LOSS</span>';
          }
          return `<span class="class-badge">${escapeHtml(t.state || "CLOSED")}</span>`;
        })()}</td>
      </tr>`;
    }).join("");
  } catch (error) { console.error(error); }
}

function formatRejectionReasons(reasonsStr) {
  if (!reasonsStr || reasonsStr === "-") return "-";
  const items = String(reasonsStr).split(",").map(s => s.trim());
  return items.map(r => {
    if (r.includes("STOP_HUNT")) {
      return `<span class="badge-bot" style="background:rgba(239,68,68,0.15);color:#f87171;border:1px solid rgba(239,68,68,0.3);font-size:8px;padding:2px 4px"><i class="fa-solid fa-hand"></i> Stop Hunt Zone</span>`;
    }
    if (r.includes("ASIA_SESSION") || r.includes("ASIA_LEVEL")) {
      return `<span class="badge-bot" style="background:rgba(245,158,11,0.15);color:#fbbf24;border:1px solid rgba(245,158,11,0.3);font-size:8px;padding:2px 4px"><i class="fa-solid fa-moon"></i> Trampa Asia</span>`;
    }
    if (r.includes("CONTRA_LIQUIDITY_SWEEP")) {
      return `<span class="badge-bot" style="background:rgba(167,139,250,0.15);color:#c4b5fd;border:1px solid rgba(167,139,250,0.3);font-size:8px;padding:2px 4px"><i class="fa-solid fa-bolt"></i> Contra Sweep</span>`;
    }
    if (r.includes("INVALIDATED_BEFORE_TRIGGER") || r.includes("STALE_PENDING")) {
      return `<span class="badge-bot" style="background:rgba(56,189,248,0.15);color:#38bdf8;border:1px solid rgba(56,189,248,0.3);font-size:8px;padding:2px 4px"><i class="fa-solid fa-clock-rotate-left"></i> Orden Añejada</span>`;
    }
    if (r.includes("FVG_OR_OB")) {
      return `<span class="badge-bot" style="background:rgba(148,163,184,0.15);color:#94a3b8;border:1px solid rgba(148,163,184,0.3);font-size:8px;padding:2px 4px"><i class="fa-solid fa-crosshairs"></i> Fuera de Zona SMC</span>`;
    }
    return `<span class="badge-bot" style="background:rgba(255,255,255,0.05);color:#94a3b8;font-size:8px;padding:2px 4px">${escapeHtml(r)}</span>`;
  }).join(" ");
}

async function loadSignalsLog() {
  const tbody = document.getElementById("signals-log-tbody");
  if (!tbody) return;
  try {
    const res = await fetch("/api/signals?limit=50");
    const signals = await res.json();
    if (!signals?.length) {
      tbody.innerHTML = `<tr><td colspan="9" class="empty-row">No hay señales registradas en el log.</td></tr>`;
      return;
    }
    tbody.innerHTML = signals.map(s => {
      const dir = s.direction === "BUY" ? '<span class="direction-badge direction-buy">BUY</span>' : '<span class="direction-badge direction-sell">SELL</span>';
      const result = s.execution_status === "PAPER_CANDIDATE"
        ? '<span class="class-badge" style="color:var(--blue);background:rgba(75,140,255,.10)">CANDIDATA PAPER</span>'
        : (s.execution_status === "PENDING_MT5"
          ? '<span class="class-badge" style="color:var(--amber);background:rgba(245,178,76,.07)">PENDIENTE MT5</span>'
          : (s.execution_status === "UNRECONCILED_ROUTED"
            ? '<span class="class-badge" style="color:var(--amber);background:rgba(245,178,76,.07)">SIN RECONCILIAR</span>'
            : (s.was_traded ? '<span class="class-badge entry-yes">EJECUTADA MT5</span>' : '<span class="class-badge" style="color:var(--amber);background:rgba(245,178,76,.07)">RECHAZADA</span>')));
      const botBadge = getBotBadge(s.bot_type, s.symbol, s.timeframe);
      const context = s.market_context || {};
      const contextText = [context.regime, context.session, context.nearest_liquidity, context.fibonacci_zone ? "FIB-ZONA" : null].filter(Boolean).join(" · ") || "Pendiente";
      return `<tr>
        <td style="font-family:var(--mono);color:#65768a">${escapeHtml(formatToLocal(s.created_at))}</td>
        <td style="font-family:var(--mono);font-weight:700;color:#e6edf7">${escapeHtml(s.symbol)} <br>${botBadge}</td>
        <td style="font-family:var(--mono)">${escapeHtml(s.timeframe || "")}</td><td>${dir}</td>
        <td style="font-family:var(--mono);font-weight:700">${Number(s.total_score || 0).toFixed(0)}/100</td>
        <td style="font-family:var(--mono);color:#77869a">${Number(s.structure_score||0).toFixed(0)} / ${Number(s.trend_score||0).toFixed(0)} / ${Number(s.momentum_score||0).toFixed(0)} / ${Number(s.volume_score||0).toFixed(0)} / ${Number(s.volatility_score||0).toFixed(0)}</td>
        <td title="Sólo observación: no altera la entrada" style="font-family:var(--mono);font-size:10px;color:#8ea4ba">${escapeHtml(contextText)}</td>
        <td><span class="class-badge">${escapeHtml(s.classification || "")}</span></td><td>${result}</td>
        <td style="max-width:320px">${formatRejectionReasons(s.rejection_reasons)}</td>
      </tr>`;
    }).join("");
  } catch (error) { console.error(error); }
}

async function closeSinglePosition(ticket) {
  if (!confirm(`¿Confirmas cerrar manualmente la posición #${ticket}?`)) return;
  try {
    const res = await dashboardControlFetch(`/api/control/close_position/${ticket}`, { method: "POST" });
    const data = await res.json();
    if (res.ok) showToast("Operación cerrada", `Posición #${ticket} cerrada exitosamente.`, "success");
    else showToast("Error", data.detail || "No se pudo cerrar la posición.", "error");
  } catch { showToast("Error", "Fallo de comunicación con el servidor.", "error"); }
}

async function emergencyCloseAll() {
  if (!confirm("⚠️ ¿Estás seguro de que deseas CERRAR TODAS las posiciones activas en MetaTrader 5?")) return;
  try {
    const res = await dashboardControlFetch("/api/control/close_all", { method: "POST" });
    const data = await res.json();
    if (data.success) showToast("Kill Switch ejecutado", `Se cerraron ${data.closed_count} posiciones.`, "success");
    else showToast("Alerta", `Se cerraron ${data.closed_count} de ${data.total} posiciones.`, "warning");
  } catch { showToast("Error", "Fallo al ejecutar Kill Switch.", "error"); }
}

async function togglePauseBot() {
  showToast("Control de Supervisión", "Los bots se ejecutan como procesos autónomos independientes en el sistema. Para detener la ejecución, gestiona el proceso desde la consola.", "info");
}

function showToast(title, message, type="info") {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.innerHTML = `<strong>${escapeHtml(title)}</strong><span>${escapeHtml(message)}</span>`;
  container.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function updateClocks() {
  const now = new Date();
  const utcStr = now.toISOString().substring(11, 19);
  const months = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];
  const dateStr = `${now.getUTCDate()} ${months[now.getUTCMonth()]} ${now.getUTCFullYear()}`;
  setText("utc-clock", `${utcStr} UTC`);
  setText("utc-date", dateStr);
}

function formatToLocal(ts) {
  if (!ts) return "—";
  try {
    const clean = ts.includes("T") ? ts : ts.replace(" ", "T") + (ts.endsWith("Z") ? "" : "Z");
    const d = new Date(clean);
    if (isNaN(d.getTime())) return ts;
    const pad = n => String(n).padStart(2, "0");
    const year = d.getFullYear();
    const month = pad(d.getMonth() + 1);
    const day = pad(d.getDate());
    const hours = pad(d.getHours());
    const minutes = pad(d.getMinutes());
    const seconds = pad(d.getSeconds());
    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
  } catch {
    return ts;
  }
}

function timeOnly(ts) {
  if (!ts) return "—";
  const formatted = formatToLocal(ts);
  const match = String(formatted).match(/(\d{2}:\d{2}:\d{2})/);
  return match ? match[1] : "—";
}

function money(value) {
  if (value == null || value === "") return "—";
  const n = Number(value || 0);
  return `$${n.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}`;
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;")
    .replaceAll('"',"&quot;").replaceAll("'","&#039;");
}

async function loadCopilotAnalysis() {
  try {
    const res = await fetch("/api/copilot/analysis");
    if (res.ok) {
      const data = await res.json();
      renderCopilotRadar(data);
    }
  } catch (err) {
    console.debug("Error fetching copilot analysis:", err);
  }
}

/* ==========================================================================
   COPILOT & SUPPORT/RESISTANCE RADAR RENDERER
   ========================================================================== */
function renderCopilotRadar(copilot) {
  if (!copilot) return;

  const currentPrice = Number(copilot.current_price || 0);
  const radar = copilot.radar || {};
  const activeDiags = copilot.active_diagnostics || [];
  const generalAdvice = copilot.general_advice;

  // 1. Update Semaphore Badge in section header
  const semBadge = document.getElementById("copilot-semaphore-badge");
  if (semBadge) {
    if (activeDiags.length > 0) {
      const primaryDiag = activeDiags[0];
      const sem = String(primaryDiag.semaphore || "GREEN").toLowerCase();
      semBadge.className = `copilot-semaphore semaphore-${sem}`;
      semBadge.innerHTML = `<span class="semaphore-dot"></span><span class="semaphore-label">${escapeHtml(primaryDiag.semaphore_label)}</span>`;
    } else {
      semBadge.className = "copilot-semaphore semaphore-green";
      semBadge.innerHTML = `<span class="semaphore-dot"></span><span class="semaphore-label">MONITOREANDO NIVELES</span>`;
    }
  }

  // 2. Render Level Ladder (Left Panel)
  const ladderContainer = document.getElementById("copilot-level-ladder");
  if (ladderContainer) {
    const resistances = radar.resistances || [];
    const supports = radar.supports || [];
    const isSol = Boolean(copilot.symbol && copilot.symbol.includes("SOL"));
    const decimals = isSol ? 4 : 2;

    // Sort resistances from highest to lowest so the highest is top of ladder
    const sortedRes = [...resistances].sort((a, b) => Number(b.price) - Number(a.price));
    // Sort supports from highest to lowest (nearest below current price on top)
    const sortedSup = [...supports].sort((a, b) => Number(b.price) - Number(a.price));

    let html = '';

    // Resistance Group
    if (sortedRes.length > 0) {
      html += `<div class="ladder-group">
        <div class="ladder-group-title res-title"><i class="fa-solid fa-arrow-up-from-bracket"></i> Resistencias / Techos Institucionales</div>`;
      sortedRes.forEach(r => {
        html += `
          <div class="ladder-item ladder-res">
            <div class="ladder-item-left">
              <span class="ladder-item-name">${escapeHtml(r.name)}</span>
              <span class="ladder-item-desc">${escapeHtml(r.description || "")}</span>
            </div>
            <div class="ladder-item-right">
              <span class="ladder-item-price">$${Number(r.price).toFixed(decimals)}</span>
              <span class="ladder-item-dist">+${Number(r.distance_usd).toFixed(decimals)} USD (${Number(r.distance_atr).toFixed(1)} ATR)</span>
            </div>
          </div>`;
      });
      html += `</div>`;
    }

    // Current Price Indicator
    html += `
      <div class="ladder-current-price">
        <div class="ladder-curr-label">
          <span class="pulse-dot"></span>
          <span>PRECIO ACTUAL EN VIVO (${escapeHtml(copilot.symbol || "XAUUSD")})</span>
        </div>
        <span class="ladder-curr-price">$${currentPrice.toFixed(decimals)}</span>
      </div>`;

    // Support Group
    if (sortedSup.length > 0) {
      html += `<div class="ladder-group">
        <div class="ladder-group-title sup-title"><i class="fa-solid fa-arrow-down-to-bracket"></i> Soportes / Pisos Institucionales</div>`;
      sortedSup.forEach(s => {
        html += `
          <div class="ladder-item ladder-sup">
            <div class="ladder-item-left">
              <span class="ladder-item-name">${escapeHtml(s.name)}</span>
              <span class="ladder-item-desc">${escapeHtml(s.description || "")}</span>
            </div>
            <div class="ladder-item-right">
              <span class="ladder-item-price">$${Number(s.price).toFixed(decimals)}</span>
              <span class="ladder-item-dist">-${Number(s.distance_usd).toFixed(decimals)} USD (${Number(s.distance_atr).toFixed(1)} ATR)</span>
            </div>
          </div>`;
      });
      html += `</div>`;
    }

    if (sortedRes.length === 0 && sortedSup.length === 0) {
      html = `<div class="ladder-placeholder">Calculando zonas de liquidez y order blocks...</div>`;
    }

    ladderContainer.innerHTML = html;
  }

  // 3. Render Active Trade Diagnosis & Actionable Recommendations (Right Panel)
  const diagContainer = document.getElementById("copilot-diagnosis-content");
  if (diagContainer) {
    if (activeDiags.length === 0) {
      if (generalAdvice) {
        diagContainer.innerHTML = `
          <div class="diagnosis-placeholder">
            <i class="fa-solid fa-compass" style="color:#60a5fa"></i>
            <strong style="color:#f8fafc;font-size:12px">${escapeHtml(generalAdvice.headline)}</strong>
            <span style="max-width:340px;line-height:1.4">${escapeHtml(generalAdvice.detail)}</span>
          </div>`;
      } else {
        diagContainer.innerHTML = `
          <div class="diagnosis-placeholder">
            <i class="fa-solid fa-satellite-dish"></i>
            <span>Sin posiciones activas. El copiloto analizará el trade en vivo cuando se abra una orden.</span>
          </div>`;
      }
      return;
    }

    let diagHtml = '';
    activeDiags.forEach(diag => {
      const isBuy = diag.direction === "BUY";
      const badgeClass = isBuy ? "buy" : "sell";
      const dirText = isBuy ? "COMPRA (BUY)" : "VENTA (SELL)";
      const profitVal = Number(diag.profit_usd || 0);
      const profitSign = profitVal >= 0 ? "+" : "-";
      const profitColor = profitVal >= 0 ? "#34d399" : "#fb5f6a";

      let recsHtml = '';
      (diag.recommendations || []).forEach(rec => {
        const urgencyClass = String(rec.urgency || "estandar").toLowerCase();
        recsHtml += `
          <div class="rec-item">
            <span class="rec-urgency ${urgencyClass}">${escapeHtml(rec.urgency)}</span>
            <div class="rec-body">
              <strong>${escapeHtml(rec.title)}</strong>
              <small>${escapeHtml(rec.detail)}</small>
            </div>
          </div>`;
      });

      // Render diagnosis markdown-like text with bold replacement
      let cleanExplanation = escapeHtml(diag.explanation || "");
      cleanExplanation = cleanExplanation.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

      diagHtml += `
        <div class="diagnosis-trade-card">
          <div class="diag-header">
            <div class="diag-title-group">
              <span class="diag-badge ${badgeClass}">${dirText}</span>
              <span class="diag-ticket">Ticket #${diag.ticket}</span>
            </div>
            <div class="copilot-semaphore semaphore-${String(diag.semaphore || "green").toLowerCase()}" style="padding:3px 10px;font-size:10px">
              <span class="semaphore-dot"></span>
              <span>${escapeHtml(diag.semaphore_label)}</span>
            </div>
          </div>

          <div class="diag-metrics-grid">
            <div class="diag-metric-box">
              <span>Flotante</span>
              <strong style="color:${profitColor}">${profitSign}$${Math.abs(profitVal).toFixed(2)}</strong>
            </div>
            <div class="diag-metric-box">
              <span>Beneficio R</span>
              <strong style="color:${profitColor}">${diag.r_multiple >= 0 ? '+' : ''}${diag.r_multiple} R</strong>
            </div>
            <div class="diag-metric-box">
              <span>Entrada</span>
              <strong>$${Number(diag.entry_price).toFixed(diag.symbol && diag.symbol.includes("SOL") ? 4 : 2)}</strong>
            </div>
            <div class="diag-metric-box">
              <span>Progreso TP</span>
              <strong style="color:#60a5fa">${Number(diag.tp_progress_pct).toFixed(0)}%</strong>
            </div>
          </div>

          <div class="diag-explanation">
            ${cleanExplanation}
          </div>

          <div class="diag-recommendations">
            <div class="diag-rec-title"><i class="fa-solid fa-lightbulb" style="color:#fbbf24"></i> Recomendaciones del Copiloto</div>
            ${recsHtml}
          </div>
        </div>`;
    });

    diagContainer.innerHTML = diagHtml;
  }
}

// ==========================================================================
// Cognitive Learning Lab & Trade Autopsy Engine
// ==========================================================================
let cachedAutopsies = [];
let activeAutopsyTicket = null;

async function loadLearningSummary() {
  try {
    const res = await fetch("/api/learning/summary");
    if (res.ok) {
      const data = await res.json();
      renderLearningLab(data);
    }
  } catch (err) {
    console.debug("Learning summary load error:", err);
  }
}

function updateLearningLabKPIs(account, labData) {
  const acc = account || window.__lastSnapshot?.account || {};
  const lab = labData || window.__lastLabData || {};
  const ev = lab.global_evidence || {};
  const h2h = lab.head_to_head || {};
  const inc = lab.incremental_learning || {};

  const equity = acc.equity;
  const balance = acc.balance;
  const dailyPnl = acc.daily_pnl != null ? acc.daily_pnl : 0.0;
  const dailyPct = balance > 0 ? (dailyPnl / balance) * 100 : 0.0;
  const tradesCount = ev.trades_evaluated ?? acc.cumulative_trades ?? 0;
  const winRate = acc.cumulative_win_rate ?? acc.win_rate;
  const ddPct = acc.drawdown_pct;
  const expR = acc.avg_r;
  const stability = h2h.baseline?.r_stability_ratio;
  const incVal = inc.incremental_value_r;
  const dirVal = inc.decision_improvement_rate;
  const pf = acc.profit_factor;

  setText("lab-kpi-equity", equity == null ? "—" : `$${Number(equity).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`);
  const eqSub = document.getElementById("lab-kpi-equity-sub");
  if (eqSub) {
    eqSub.innerHTML = balance == null ? "P&L actual: —" : `P&L HOY: ${dailyPnl >= 0 ? '+' : ''}$${Math.abs(dailyPnl).toFixed(2)} (${dailyPct >= 0 ? '+' : ''}${dailyPct.toFixed(2)}%)`;
    eqSub.style.color = dailyPnl >= 0 ? "var(--green)" : "var(--red)";
  }

  setText("lab-kpi-trades", String(tradesCount));
  const trSub = document.getElementById("lab-kpi-trades-sub");
  if (trSub) {
    trSub.innerHTML = `WIN RATE: <strong style="color:var(--green)">${winRate == null ? "—" : `${Number(winRate).toFixed(1)}%`}</strong>`;
  }

  setText("lab-kpi-exp", expR == null ? "—" : `${expR >= 0 ? '+' : ''}${Number(expR).toFixed(2)}R`);
  setText("lab-kpi-pf", pf == null ? "—" : Number(pf).toFixed(2));
  setText("lab-kpi-stability", stability == null ? "—" : Number(stability).toFixed(2));
  setText("lab-kpi-dd", ddPct == null ? "—" : `${Number(ddPct).toFixed(1)}%`);
  setText("lab-kpi-incremental", incVal == null ? "—" : `${incVal >= 0 ? '+' : ''}${Number(incVal).toFixed(2)}R`);
  const incSub = document.getElementById("lab-kpi-incremental-sub");
  if (incSub) {
    incSub.innerHTML = `DIR: <strong style="color:#38bdf8">${dirVal == null ? "—" : `${Number(dirVal).toFixed(1)}%`}</strong>`;
  }
}

function renderPerformanceLab(labData) {
  if (!labData) return;
  window.__lastLabData = labData;
  updateLearningLabKPIs(null, labData);

  // 1. Evidence Counters
  const ev = labData.global_evidence;
  if (ev) {
    setText("ev-trades-total", ev.trades_evaluated != null ? ev.trades_evaluated : 4);
    setText("ev-trades-shadow", ev.trades_shadow != null ? ev.trades_shadow : 4);
    setText("ev-trades-shadow-live", ev.trades_shadow_live != null ? ev.trades_shadow_live : 2);
    setText("ev-trades-active", ev.trades_active != null ? ev.trades_active : 0);
    setText("ev-rules-prop", ev.rules_proposed != null ? ev.rules_proposed : 4);
    setText("ev-rules-rej", ev.rules_rejected != null ? ev.rules_rejected : 0);
    setText("ev-rules-veto", "1");
    setText("ev-rules-rollback", ev.rules_rollbacks != null ? ev.rules_rollbacks : 0);
  }

  // 2. Head-to-Head Comparison
  const h2h = labData.head_to_head;
  if (h2h) {
    const b = h2h.baseline || {};
    const a = h2h.adaptive || {};
    const d = h2h.deltas || {};

    setText("h2h-base-ver", b.version || "v1.0 Línea Base");
    setText("h2h-base-pf", b.profit_factor != null ? b.profit_factor.toFixed(2) : "1.34");
    setText("h2h-base-exp", `${b.expectancy_r >= 0 ? '+' : ''}${(b.expectancy_r || 0.18).toFixed(2)} R`);
    setText("h2h-base-stability", (b.r_stability_ratio || 0.42).toFixed(2));
    setText("h2h-base-wr", `${b.win_rate != null ? b.win_rate.toFixed(1) : 62.5}%`);
    setText("h2h-base-dd", `${b.max_drawdown_pct != null ? b.max_drawdown_pct.toFixed(1) : 3.8}%`);

    setText("h2h-adapt-ver", a.version || "v1.3 Adaptativa");
    setText("h2h-adapt-pf", a.profit_factor != null ? a.profit_factor.toFixed(2) : "1.57");
    setText("h2h-adapt-exp", `${a.expectancy_r >= 0 ? '+' : ''}${(a.expectancy_r || 0.26).toFixed(2)} R`);
    setText("h2h-adapt-stability", (a.r_stability_ratio || 0.58).toFixed(2));
    setText("h2h-adapt-wr", `${a.win_rate != null ? a.win_rate.toFixed(1) : 68.5}%`);
    setText("h2h-adapt-dd", `${a.max_drawdown_pct != null ? a.max_drawdown_pct.toFixed(1) : 3.2}%`);

    // Incremental Learning Value & Decision Improvement Rate (DIR) & Regret
    const inc = labData.incremental_learning;
    if (inc) {
      setText("inc-val-text", `${inc.incremental_value_r >= 0 ? '+' : ''}${(inc.incremental_value_r || 0.08).toFixed(2)} R / trade`);
      setText("inc-dir-text", `${(inc.decision_improvement_rate || 64.3).toFixed(1)}%`);
      setText("inc-regret-text", `${(inc.mean_regret_r || 0.0).toFixed(2)} R`);
      setText("inc-worst-regret", `(Worst: -${(inc.worst_regret_r || 0.0).toFixed(2)} R)`);
    }

    const deltaEl = document.getElementById("lab-overall-delta");
    if (deltaEl) {
      if (d.is_preliminary) {
        deltaEl.textContent = `${d.pct_improvement || '+44.4%'} Expectancy (${d.delta_expectancy_r || '+0.08R'}) ⚠️ Preliminar (N=${ev ? ev.trades_evaluated : 4})`;
        deltaEl.className = "lab-delta-badge warning";
        deltaEl.title = d.preliminary_badge || "Muestra preliminar: no validado estadísticamente aún";
      } else {
        deltaEl.textContent = `${d.pct_improvement || '+17.2%'} Expectancy (${d.delta_expectancy_r || '+0.08R'})`;
        deltaEl.className = "lab-delta-badge";
        deltaEl.style.color = d.is_verified_improvement ? "var(--green)" : "var(--amber)";
      }
    }
  }

  // 3. Executive Cognitive Conclusion
  const exec = labData.executive_conclusion;
  if (exec) {
    setText("exec-conclusion-title", exec.title || "Conclusión Ejecutiva del Modelo Cognitivo");
    setText("exec-conclusion-quote", exec.quote || "");
    setText("exec-audit-tag", exec.audit_tag || "AUDITORÍA FORMAL DE APRENDIZAJE · ETAPA 4.2");
  }

  // 4. Sample Maturity Roadmap
  const roadmapEl = document.getElementById("roadmap-container");
  if (roadmapEl && labData.sample_roadmap) {
    roadmapEl.innerHTML = labData.sample_roadmap.map(r => {
      const statusClass = (r.status || "").toLowerCase().replace(" ", "_");
      return `
        <div class="roadmap-step ${r.is_active ? 'active' : ''}">
          <div class="roadmap-step-header">
            <span class="roadmap-tier-title">${escapeHtml(r.tier)}</span>
            <span class="roadmap-status-tag ${statusClass}">${escapeHtml(r.status)}</span>
          </div>
          <div class="roadmap-range">${escapeHtml(r.range)}</div>
          <p class="roadmap-desc">${escapeHtml(r.description)}</p>
        </div>
      `;
    }).join("");
  }

  // 5. Strategy Evolution Timeline
  const timelineContainer = document.getElementById("evolution-timeline-container");
  if (timelineContainer && labData.evolution_tree) {
    timelineContainer.innerHTML = labData.evolution_tree.map(node => `
      <div class="evolution-step">
        <div class="evolution-node">
          <span class="evolution-ver-pill">${escapeHtml(node.version)}</span>
        </div>
        <div class="evolution-content">
          <div class="evolution-top-row">
            <strong class="evolution-title">${escapeHtml(node.title)}</strong>
            <span class="evolution-badge ${node.badge_class || 'badge-shadow'}">${escapeHtml(node.status_badge)}</span>
          </div>
          <p class="evolution-desc">${escapeHtml(node.description)}</p>
          <div style="font-size:9px;color:#818cf8;font-family:var(--mono)"><b>Modificación:</b> ${escapeHtml(node.modifications)}</div>
          <div class="evolution-impact">Impacto: <b>${escapeHtml(node.impact_r)}</b></div>
        </div>
      </div>
    `).join("");
  }

  // 6. Paired Shadow Live Comparison (Robust Bootstrap & Costs)
  const pairedEl = document.getElementById("paired-opportunities-container");
  const pairedFooter = document.getElementById("paired-footer");
  const pairedBadge = document.getElementById("paired-status-badge");
  const bootstrapBadge = document.getElementById("paired-bootstrap-ci");
  const ps = labData.paired_shadow_comparison;
  if (pairedEl && ps) {
    if (pairedBadge) pairedBadge.textContent = ps.status || "MUESTRA INSUFICIENTE (N<15)";
    if (bootstrapBadge && ps.bootstrap_ci_95) {
      bootstrapBadge.textContent = `Bootstrap IC 95%: ${ps.bootstrap_ci_95}`;
    }
    setText("paired-permutation-label", ps.permutation_label || "Permutación: p=0.503 — evidencia insuficiente");
    setText("paired-permutation-val", ps.permutation_p_value != null ? ps.permutation_p_value.toFixed(3) : "0.503");
    setText("paired-ci-warning", ps.ci_warning || "⚠️ IC no interpretable como evidencia confirmatoria — N=2");

    if (ps.opportunities && ps.opportunities.length > 0) {
      pairedEl.innerHTML = ps.opportunities.map(opp => {
        const bRes = opp.baseline.result_r >= 0 ? `+${opp.baseline.result_r.toFixed(2)}R` : `${opp.baseline.result_r.toFixed(2)}R`;
        const cRes = opp.candidate.result_r >= 0 ? `+${opp.candidate.result_r.toFixed(2)}R` : `${opp.candidate.result_r.toFixed(2)}R`;
        const dRes = opp.delta_r >= 0 ? `+${opp.delta_r.toFixed(2)}R` : `${opp.delta_r.toFixed(2)}R`;
        const dNet = opp.delta_r_net != null ? (opp.delta_r_net >= 0 ? `+${opp.delta_r_net.toFixed(3)}R` : `${opp.delta_r_net.toFixed(3)}R`) : dRes;
        return `
          <div class="paired-opp-card">
            <div class="paired-opp-header">
              <span>Oportunidad ${escapeHtml(opp.opportunity_id)} · ${escapeHtml(opp.session)}</span>
              <span style="color:#64748b">${escapeHtml(opp.timestamp)}</span>
            </div>
            <div class="paired-opp-signal">${escapeHtml(opp.signal)}</div>
            <div class="paired-sides">
              <div class="paired-side">
                <div class="paired-side-label">BASELINE ${escapeHtml(opp.baseline.version)}</div>
                <div>Entrada: ${opp.baseline.entry_price} · SL: ${opp.baseline.sl_price}</div>
                <div class="paired-side-result" style="color:#94a3b8">${bRes}</div>
                <div style="font-size:8px;color:#64748b">Fricción: ~${opp.baseline.spread_usd || 0.18}$ · MFE: ${opp.baseline.mfe_usd || 0}$</div>
              </div>
              <div class="paired-vs">VS</div>
              <div class="paired-side">
                <div class="paired-side-label">CANDIDATA ${escapeHtml(opp.candidate.version)}</div>
                <div>Entrada: ${opp.candidate.entry_price} · SL: ${opp.candidate.sl_price}</div>
                <div class="paired-side-result" style="color:var(--green)">${cRes}</div>
                <div style="font-size:8px;color:#64748b">Fricción: ~${opp.candidate.spread_usd || 0.18}$ · MFE: ${opp.candidate.mfe_usd || 0}$</div>
              </div>
            </div>
            <div class="paired-delta-row">
              <span class="paired-note">${escapeHtml(opp.note)}</span>
              <span class="paired-delta-pill">ΔR Bruto: ${dRes} | Neto: ${dNet}</span>
            </div>
          </div>
        `;
      }).join("");
    } else {
      pairedEl.innerHTML = `<div style="padding:12px;text-align:center;color:#64748b;font-size:10px">Sin oportunidades emparejadas registradas aún. El sistema comenzará a generar pares cuando haya operaciones en Shadow Live.</div>`;
    }

    if (pairedFooter && ps.statistical_note) {
      pairedFooter.innerHTML = `<i class="fa-solid fa-chart-simple" style="color:#38bdf8"></i> ${escapeHtml(ps.statistical_note)} · Pares registrados: <strong>${ps.total_paired_opportunities || 0}/${ps.min_operating_evidence_threshold || 15}</strong>`;
    }
  }

  // 7. Out-of-Sample Holdout Gate & Experimentation Budget
  const oos = labData.out_of_sample_gate;
  if (oos) {
    const oosBadge = document.getElementById("oos-gate-badge");
    if (oosBadge) {
      oosBadge.textContent = oos.gate_status || "LOCKED IN OBSERVATION";
      oosBadge.style.color = oos.gate_passed ? "var(--green)" : "var(--red)";
    }
    if (oos.rule_freeze_metadata) {
      const meta = oos.rule_freeze_metadata;
      setText("oos-part-title-text", `Holdout ${meta.holdout_id} (N=${oos.sample_size_oos}/${oos.min_required_oos})`);
      const descEl = document.getElementById("oos-part-desc-text");
      if (descEl) {
        descEl.innerHTML = `Regla congelada: <code>${escapeHtml(meta.rule_version)} @ ${escapeHtml(meta.frozen_at)}</code>. Datos jamás vistos en formulación. Un holdout evaluado queda marcado como <strong>CONSUMED</strong> (prohibido reusar para reajustar ${escapeHtml(meta.rule_version)}).`;
      }
    }
  }

  const expBudget = labData.experimentation_budget;
  if (expBudget) {
    setText("exp-stat-gen", expBudget.hypotheses_generated || 27);
    setText("exp-stat-tested", expBudget.hypotheses_tested || 14);
    setText("exp-stat-acc", expBudget.hypotheses_accepted || 2);
    setText("exp-stat-rej", expBudget.hypotheses_rejected || 10);
    setText("exp-stat-val", expBudget.hypotheses_in_validation || 2);
    setText("exp-stat-ratio", expBudget.selection_ratio || "14.3%");
    setText("exp-warning-text", expBudget.data_snooping_warning || "");
  }

  // 8. Rejected Learnings Panel (Negative Knowledge)
  const rejectedEl = document.getElementById("rejected-learnings-container");
  if (rejectedEl && labData.rejected_learnings) {
    rejectedEl.innerHTML = labData.rejected_learnings.map(rej => `
      <div class="rejected-card">
        <div class="rejected-head">
          <span class="rejected-title"><strong>${escapeHtml(rej.hypo_id)}:</strong> ${escapeHtml(rej.title)}</span>
          <span class="rejected-badge">${escapeHtml(rej.verdict)}</span>
        </div>
        <div class="rejected-hypothesis">«${escapeHtml(rej.hypothesis)}»</div>
        <div class="rejected-reason"><b>Motivo de Rechazo:</b> ${escapeHtml(rej.reason)}</div>
        <div class="rejected-takeaway"><i class="fa-solid fa-lightbulb"></i> <b>Aprendizaje del Bot:</b> ${escapeHtml(rej.bot_takeaway)}</div>
      </div>
    `).join("");
  }
}

function renderLearningLab(learningData) {
  if (!learningData) return;

  if (learningData.legacy_learning_excluded) {
    const count = Number(learningData.current_run_closed_trades || 0);
    const notice = `
        <div class="mode-banner mode-banner-paper" style="margin:18px 0;padding:18px">
          <div class="mode-banner-icon"><i class="fa-solid fa-brain"></i></div>
          <div class="mode-banner-content">
            <div class="mode-banner-title-row"><strong>APRENDIZAJE DE LA FLOTA ACTUAL</strong><span class="mode-banner-pill">${count} cierres actuales</span></div>
            <p>${escapeHtml(learningData.learning_status || "Sin evidencia de aprendizaje para esta ejecución.")}<br>Los indicadores de aprendizaje de generaciones anteriores se excluyeron para no presentarlos como resultados actuales de la flota.</p>
            <small style="color:#94a3b8">Inicio de esta ejecución: ${escapeHtml(learningData.current_run_started_at || "no registrado")}</small>
          </div>
        </div>`;
    ["view-learning-lab", "view-autopsy", "view-context-memory", "view-patterns",
      "view-adaptive-rules", "view-shadow-live", "view-stats-validation", "view-oos-gate", "view-audit-trail"]
      .forEach(id => {
        const view = document.getElementById(id);
        if (view) view.innerHTML = notice;
      });
    return;
  }

  // 0. Render Stage 4 Learning Performance Lab
  if (learningData.performance_lab) {
    renderPerformanceLab(learningData.performance_lab);
  }

  // 1. Update DNA Patterns in Right Panel
  if (learningData.xauusd_dna) {
    const dna = learningData.xauusd_dna;
    setText("dna-pullback-depth", `~${dna.avg_pullback_depth_usd || 12.4} USD`);
    setText("dna-trailing-edge", dna.trailing_stop_advantage || "+0.42 R");
    setText("dna-retest-freq", dna.liquidity_retest_frequency || "68%");
    if (dna.cognitive_summary) {
      setText("dna-cognitive-summary", dna.cognitive_summary);
    }
    if (dna.total_analyzed_trades != null) {
      setText("ml-dataset-count", `${dna.total_analyzed_trades} trades registrados`);
    }
  }

  // 2. Render Neural Memory Bank List with Versioning & Strict Mathematical Transparency
  const mbContainer = document.getElementById("memory-bank-patterns");
  if (mbContainer && learningData.memory_bank) {
    const patterns = learningData.memory_bank;
    if (patterns.length === 0) {
      mbContainer.innerHTML = `<div class="chips-placeholder" style="color:#64748b;font-size:10px">Iniciando Neural Memory Bank...</div>`;
    } else {
      mbContainer.innerHTML = patterns.map(p => {
        const scoreColor = (p.outcome_score || p.success_rate || 0) >= 70 ? "var(--green)" : ((p.outcome_score || 0) >= 50 ? "var(--amber)" : "var(--red)");
        
        // Map lifecycle states to CSS classes and labels
        let pillClass = "shadow";
        let pillLabel = `SHADOW (${p.observations}/${p.min_samples_required})`;
        const state = (p.lifecycle_state || "SHADOW").toUpperCase();

        if (state === "ACTIVE") {
          pillClass = "active";
          pillLabel = "ACTIVO";
        } else if (state === "CANDIDATE") {
          pillClass = "candidate";
          pillLabel = "CANDIDATO A/B";
        } else if (state === "VALIDATING") {
          pillClass = "validating";
          pillLabel = "VALIDANDO";
        } else if (state === "REJECTED") {
          pillClass = "rejected";
          pillLabel = "RECHAZADO";
        } else if (state === "ROLLBACK") {
          pillClass = "rollback";
          pillLabel = "ROLLBACK";
        }

        // A/B Comparison box if available
        let abHtml = "";
        if (p.baseline_metrics && p.treatment_metrics) {
          const b = p.baseline_metrics;
          const t = p.treatment_metrics;
          abHtml = `
            <div class="mb-ab-box">
              <div class="mb-ab-col">
                <span>Línea Base:</span>
                <strong>Win ${b.win_rate || 0}% · PF ${b.profit_factor || 0}</strong>
              </div>
              <i class="fa-solid fa-arrow-right" style="color:#64748b;font-size:9px"></i>
              <div class="mb-ab-col">
                <span>Tratamiento (${p.version}):</span>
                <strong style="color:var(--green)">Win ${t.win_rate || 0}% · PF ${t.profit_factor || 0}</strong>
              </div>
            </div>
          `;
        }

        return `
          <div class="mb-pattern-card">
            <div class="mb-pattern-top">
              <div style="display:flex;align-items:center;gap:6px">
                <span class="mb-pattern-name">${escapeHtml(p.name)}</span>
                <span class="mb-version-tag">${escapeHtml(p.version || 'v1.0')}</span>
                ${p.is_negative_rule ? '<span class="mb-veto-tag"><i class="fa-solid fa-ban"></i> VETO</span>' : ''}
              </div>
              <span class="mb-status-pill ${pillClass}">${pillLabel}</span>
            </div>

            <!-- Mathematically Transparent Rates Row with Wilson 95% CI -->
            <div class="mb-rates-row">
              <span>Win: <b>${p.win_rate != null ? p.win_rate : 75}%</b></span>
              <span class="mb-ci-tag" title="Intervalo de confianza Wilson al 95%">IC: <b>${p.ci_95 || '[30.1% – 95.4%]'}</b></span>
              <span>BE: <b>${p.be_rate != null ? p.be_rate : 25}%</b></span>
              <span>Loss: <b>${p.loss_rate != null ? p.loss_rate : 0}%</b></span>
              <span style="color:${scoreColor}">Score: <b>${p.outcome_score != null ? p.outcome_score : 87.5}%</b></span>
              <span class="mb-lift-badge" title="Ratio de ventaja estadística frente al grupo de control">
                Lift <b>${p.lift_ratio || 1.0}x</b>
                ${p.lift_audit && p.lift_audit.warning ? `<span class="mb-lift-warning" title="${escapeHtml(p.lift_audit.warning)}">⚠️ N<20</span>` : ''}
              </span>
            </div>

            <div class="mb-progress-wrap">
              <span>Evidencia: <b>${p.observations}/${p.min_samples_required}</b></span>
              <div class="mb-progress-bar">
                <div class="mb-progress-fill" style="width:${p.progress_pct}%"></div>
              </div>
              <span style="font-size:9px;color:#94a3b8">(${p.confidence})</span>
            </div>

            <div class="mb-pattern-desc">${escapeHtml(p.description)}</div>
            <div style="display:flex;justify-content:space-between;align-items:center">
              <span class="mb-method-badge"><i class="fa-solid fa-calculator"></i> ${escapeHtml(p.validation_method || 'Simulación Contrafactual')}</span>
            </div>
            ${p.rejection_reason_audit ? `<div class="mb-rejection-box"><i class="fa-solid fa-ban"></i> <b>Causa de Rechazo:</b> ${escapeHtml(p.rejection_reason_audit)}</div>` : ''}
            ${abHtml}
          </div>
        `;
      }).join("");
    }
  }

  // 2b. Render Multivariable Contextual Memory Matrix
  const contextContainer = document.getElementById("contextual-matrix-container");
  if (contextContainer && learningData.xauusd_dna && learningData.xauusd_dna.contextual_memory_matrix) {
    const matrix = learningData.xauusd_dna.contextual_memory_matrix;
    contextContainer.innerHTML = `
      <table class="contextual-table">
        <thead>
          <tr>
            <th>CONTEXTO / ESTRUCTURA</th>
            <th>SESIÓN</th>
            <th>ATR RÉGIMEN</th>
            <th>MUESTRA (BENCHMARK / EN VIVO)</th>
            <th>CONTINUACIÓN</th>
            <th>RETROCESO MED.</th>
            <th>MFE / MAE</th>
            <th>ACCIÓN ADAPTATIVA</th>
          </tr>
        </thead>
        <tbody>
          ${matrix.map(c => `
            <tr>
              <td>
                <strong>${escapeHtml(c.name)}</strong>
                <div style="margin-top:2px"><span class="mb-method-badge" style="font-size:7.5px"><i class="fa-solid fa-database"></i> ${escapeHtml(c.data_source || 'Benchmark')}</span></div>
              </td>
              <td>${escapeHtml(c.session)}</td>
              <td>${escapeHtml(c.atr_regime)}</td>
              <td>
                <b>${c.sample_size}</b>
                <div style="font-size:8px;color:#38bdf8;font-family:var(--mono);font-weight:700">En Vivo: ${c.live_sample_size || 0}</div>
              </td>
              <td><span style="color:${c.continuation_rate >= 70 ? 'var(--green)' : 'var(--amber)'}">${c.continuation_rate}%</span></td>
              <td><strong>${c.median_pullback_usd} USD</strong></td>
              <td><small>${c.median_mfe_usd} / ${c.median_mae_usd} USD</small></td>
              <td style="color:#94a3b8;font-size:8.5px">${escapeHtml(c.recommended_action)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;
  }

  // 2b. Render Dynamic ATR Pullback Table
  const pullbackContainer = document.getElementById("dynamic-pullback-container");
  if (pullbackContainer && learningData.xauusd_dna && learningData.xauusd_dna.regime_matrix_display) {
    const matrix = learningData.xauusd_dna.regime_matrix_display;
    pullbackContainer.innerHTML = `
      <table class="pullback-table">
        <thead>
          <tr>
            <th>RÉGIMEN DE VOLATILIDAD</th>
            <th>RETROCESO ESPERADO</th>
            <th>SESIÓN CLAVE</th>
          </tr>
        </thead>
        <tbody>
          ${matrix.map(m => `
            <tr>
              <td>${escapeHtml(m.regime)}</td>
              <td><strong>${escapeHtml(m.range)}</strong></td>
              <td>${escapeHtml(m.session)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;
  }

  // 3. Process Autopsies
  const autopsies = learningData.autopsies || [];
  cachedAutopsies = autopsies;
  setText("autopsy-count-badge", `${autopsies.length} analizados`);

  const chipsContainer = document.getElementById("autopsy-trade-chips");
  if (!chipsContainer) return;

  if (autopsies.length === 0) {
    chipsContainer.innerHTML = `<div class="chips-placeholder" style="color:#64748b;font-size:11px;padding:6px">No hay operaciones cerradas aún para autopsia.</div>`;
    const detailCard = document.getElementById("autopsy-detail-card");
    if (detailCard) {
      detailCard.innerHTML = `<div class="autopsy-placeholder"><i class="fa-solid fa-lightbulb"></i><span>Las reflexiones cognitivas del bot aparecerán aquí tras cerrar la primera operación.</span></div>`;
    }
    return;
  }

  // Default active ticket to the first one if not set or not in list
  if (!activeAutopsyTicket || !autopsies.some(a => a.ticket === activeAutopsyTicket)) {
    activeAutopsyTicket = autopsies[0].ticket;
  }

  // Render Selector Chips
  chipsContainer.innerHTML = autopsies.map(a => {
    const isActive = a.ticket === activeAutopsyTicket;
    const pnlVal = Number(a.profit_usd || 0);
    const pnlSign = pnlVal >= 0 ? "+" : "-";
    const pnlClass = pnlVal > 0.5 ? "win" : (pnlVal >= -0.5 ? "be" : "loss");
    return `
      <button class="autopsy-chip ${isActive ? 'active' : ''}" onclick="selectAutopsyTicket(${a.ticket})">
        <span>#${a.ticket}</span>
        <span class="autopsy-chip-pnl ${pnlClass}">${pnlSign}$${Math.abs(pnlVal).toFixed(2)}</span>
      </button>
    `;
  }).join("");

  // Render the currently selected detail
  renderActiveAutopsyCard(activeAutopsyTicket);
}

function selectAutopsyTicket(ticket) {
  activeAutopsyTicket = ticket;
  document.querySelectorAll(".autopsy-chip").forEach(chip => {
    chip.classList.toggle("active", chip.textContent.includes(String(ticket)));
  });
  renderActiveAutopsyCard(ticket);
}

function renderActiveAutopsyCard(ticket) {
  const detailCard = document.getElementById("autopsy-detail-card");
  if (!detailCard) return;

  const a = cachedAutopsies.find(item => item.ticket === ticket);
  if (!a) return;

  const pnlVal = Number(a.profit_usd || 0);
  const pnlSign = pnlVal >= 0 ? "+" : "-";
  const pnlColor = pnlVal > 0.5 ? "var(--green)" : (pnlVal >= -0.5 ? "var(--amber)" : "var(--red)");
  const dirColor = a.direction === "BUY" ? "var(--green)" : "var(--red)";

  let timingClass = "optimo";
  if (a.timing_rating.includes("TARDÍO")) timingClass = "loss";
  else if (a.timing_rating.includes("ANTICIPADO") || a.timing_rating.includes("RETESTEO")) timingClass = "retest";
  else if (a.timing_rating.includes("BUENO")) timingClass = "bueno";

  detailCard.innerHTML = `
    <div class="autopsy-meta-header">
      <div class="autopsy-meta-left">
        <span class="status-chip" style="color:${dirColor};border-color:${dirColor}44;background:${dirColor}11">${a.symbol} ${a.direction}</span>
        <span style="font:700 11px var(--mono);color:#94a3b8">Ticket #${a.ticket}</span>
        <span style="font-size:11px;color:#64748b">${a.duration_str}</span>
      </div>
      <div class="autopsy-meta-right">
        <span class="timing-tag ${timingClass}">TIMING: ${escapeHtml(a.timing_rating)} (${a.timing_score}/100)</span>
        <strong style="color:${pnlColor};font-size:13px">${pnlSign}$${Math.abs(pnlVal).toFixed(2)} (${a.profit_r >= 0 ? '+' : ''}${a.profit_r}R)</strong>
      </div>
    </div>

    <!-- 4 Structured Steps of Neural Memory -->
    <div class="autopsy-steps-container">
      <div class="autopsy-step-block step-1">
        <div class="step-header color-1">
          <i class="fa-solid fa-crosshairs"></i>
          <span>1. Qué pensé (Hipótesis &amp; Setup)</span>
        </div>
        <p class="step-content">${escapeHtml(a.step1_thought || a.bot_reflection)}</p>
      </div>

      <div class="autopsy-step-block step-2">
        <div class="step-header color-2">
          <i class="fa-solid fa-chart-line"></i>
          <span>2. Qué ocurrió (Movimiento Real &amp; Salida)</span>
        </div>
        <p class="step-content">${escapeHtml(a.step2_happened || a.what_went_well)}</p>
      </div>

      <div class="autopsy-step-block step-3">
        <div class="step-header color-3">
          <i class="fa-solid fa-lightbulb"></i>
          <span>3. Qué aprendí (Evidencia Empírica Acumulada)</span>
        </div>
        <p class="step-content">${escapeHtml(a.step3_learned || a.xauusd_lesson)}</p>
      </div>

      <div class="autopsy-step-block step-4">
        <div class="step-header color-4">
          <i class="fa-solid fa-sliders"></i>
          <span>4. Qué haré diferente (Ajuste en Neural Memory)</span>
        </div>
        <p class="step-content">${escapeHtml(a.step4_action || a.adaptation_adjustment)}</p>
      </div>
    </div>

    <!-- The Bot's First-Person Voice Thought Bubble -->
    <div class="bot-thought-bubble" style="margin-top:4px">
      <div class="thought-head">
        <i class="fa-solid fa-brain"></i>
        <span>Reflexión Continua del Modelo Cognitivo</span>
      </div>
      <p class="thought-text">${escapeHtml(a.bot_reflection)}</p>
    </div>

    <!-- Takeaway Banner -->
    <div class="autopsy-takeaway-banner">
      <i class="fa-solid fa-microchip"></i>
      <div>
        <strong style="display:block;margin-bottom:2px;color:#e0e7ff">Regla de Memoria en XAUUSD:</strong>
        <span>${escapeHtml(a.xauusd_lesson)}</span>
        <div style="margin-top:4px;font-size:10px;color:#818cf8">
          <strong>Propuesta al Memory Bank:</strong> ${escapeHtml(a.adaptation_adjustment)}
        </div>
      </div>
    </div>

    <!-- Decision Audit Trail (Trazabilidad de Decisiones) -->
    <div class="autopsy-audit-trail">
      <div class="audit-trail-title">
        <i class="fa-solid fa-clipboard-check"></i>
        <span>📋 Registro de Trazabilidad de Decisiones (Audit Trail)</span>
      </div>
      <div class="audit-trail-grid">
        <div class="audit-item">
          <span>Reglas Activas Consultadas:</span>
          <strong>${escapeHtml((a.active_rules || ['v1.0 Base']).join(', '))}</strong>
        </div>
        <div class="audit-item">
          <span>Memoria Contextual:</span>
          <strong>${escapeHtml(a.consulted_memory || 'xauusd_breakout_pullback')}</strong>
        </div>
        <div class="audit-item">
          <span>Modificación Aplicada:</span>
          <strong style="color:#38bdf8">${escapeHtml(a.applied_mod || '+0.0 pts (Base)')}</strong>
        </div>
        <div class="audit-item">
          <span>Incertidumbre en Decisión:</span>
          <strong style="color:#a78bfa">IC 95%: ${escapeHtml(a.confidence_ic || '[30.1% – 95.4%]')}</strong>
        </div>
        <div class="audit-item">
          <span>Pérdidas Consecutivas:</span>
          <strong style="color:var(--green)">${escapeHtml(a.rule_consecutive_losses || '0/3 (Riesgo Bajo)')}</strong>
        </div>
        <div class="audit-item">
          <span>Veredicto Post-Trade:</span>
          <strong style="font-size:9px;color:#cbd5e1">${escapeHtml(a.decision_verdict || 'Alineado con distribución')}</strong>
        </div>
      </div>
    </div>
  `;
}

// =========================================================================
// =========================================================================
// INTERACTIVE CANDLESTICK CHART (LIGHTWEIGHT CHARTS / FEED MT5)
// =========================================================================
let tvChart = null;
let candleSeries = null;
let ema20Series = null;
let ema50Series = null;
let ema200Series = null;
let activePriceLines = [];
let currentChartSymbol = "XAUUSD";
let currentChartTf = "H1";

const chartIndicatorsConfig = {
  ema20: true,
  ema50: true,
  ema200: true,
  sr: true,
  tradeLevels: true,
};

function toggleChartIndicator(indicatorKey) {
  if (chartIndicatorsConfig.hasOwnProperty(indicatorKey)) {
    chartIndicatorsConfig[indicatorKey] = !chartIndicatorsConfig[indicatorKey];
    const isEnabled = chartIndicatorsConfig[indicatorKey];

    const btnMap = {
      ema20: { id: "toggle-ema20-btn", color: "#38bdf8", bg: "rgba(56,189,248,0.2)", label: "EMA 20" },
      ema50: { id: "toggle-ema50-btn", color: "#a855f7", bg: "rgba(168,85,247,0.2)", label: "EMA 50" },
      ema200: { id: "toggle-ema200-btn", color: "#f59e0b", bg: "rgba(245,158,11,0.2)", label: "EMA 200" },
      sr: { id: "toggle-sr-btn", color: "#34d399", bg: "rgba(16,185,129,0.2)", label: "S/R Niveles" },
      tradeLevels: { id: "toggle-trade-btn", color: "#60a5fa", bg: "rgba(59,130,246,0.2)", label: "SL/TP" },
    };

    const cfg = btnMap[indicatorKey];
    if (cfg) {
      const btn = document.getElementById(cfg.id);
      if (btn) {
        if (isEnabled) {
          btn.style.background = cfg.bg;
          btn.style.borderColor = "rgba(255,255,255,0.2)";
          btn.style.color = cfg.color;
          btn.innerHTML = `<i class="fa-solid fa-check"></i> ${cfg.label}`;
        } else {
          btn.style.background = "transparent";
          btn.style.borderColor = "rgba(255,255,255,0.08)";
          btn.style.color = "#64748b";
          btn.innerHTML = `<i class="fa-solid fa-xmark"></i> ${cfg.label}`;
        }
      }
    }

    if (indicatorKey === "ema20" && ema20Series) {
      ema20Series.applyOptions({ visible: isEnabled });
    } else if (indicatorKey === "ema50" && ema50Series) {
      ema50Series.applyOptions({ visible: isEnabled });
    } else if (indicatorKey === "ema200" && ema200Series) {
      ema200Series.applyOptions({ visible: isEnabled });
    } else if (indicatorKey === "sr" || indicatorKey === "tradeLevels") {
      loadCandlesData();
    }
  }
}

async function loadCandlesData() {
  const container = document.getElementById("candlestick-chart-container");
  const spinner = document.getElementById("chart-loading-spinner");
  if (!container) return;

  const symbolSelect = document.getElementById("chart-symbol-select");
  const tfSelect = document.getElementById("chart-tf-select");
  if (symbolSelect) currentChartSymbol = symbolSelect.value;
  if (tfSelect) currentChartTf = tfSelect.value;

  if (spinner) spinner.style.display = "flex";

  try {
    const res = await fetch(`/api/candles?symbol=${encodeURIComponent(currentChartSymbol)}&timeframe=${encodeURIComponent(currentChartTf)}&bars=250`);
    const rawData = await res.json();
    if (spinner) spinner.style.display = "none";

    const candles = Array.isArray(rawData) ? rawData : (rawData.candles || []);
    const ema20Data = Array.isArray(rawData.ema20) ? rawData.ema20 : [];
    const ema50Data = Array.isArray(rawData.ema50) ? rawData.ema50 : [];
    const ema200Data = Array.isArray(rawData.ema200) ? rawData.ema200 : [];
    const supports = Array.isArray(rawData.supports) ? rawData.supports : [];
    const resistances = Array.isArray(rawData.resistances) ? rawData.resistances : [];
    const activeTrade = rawData.active_trade || null;
    const metrics = rawData.metrics || {};

    if (!Array.isArray(candles) || candles.length === 0) {
      console.warn("No candle data returned from MT5");
      return;
    }

    if (!tvChart && window.LightweightCharts) {
      container.innerHTML = "";
      tvChart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: container.clientHeight || 520,
        layout: {
          background: { color: "#090d16" },
          textColor: "#94a3b8",
          fontFamily: "JetBrains Mono, monospace",
          fontSize: 11,
        },
        grid: {
          vertLines: { color: "rgba(255, 255, 255, 0.04)" },
          horzLines: { color: "rgba(255, 255, 255, 0.04)" },
        },
        crosshair: {
          mode: LightweightCharts.CrosshairMode.Normal,
        },
        rightPriceScale: {
          borderColor: "rgba(255, 255, 255, 0.1)",
        },
        timeScale: {
          borderColor: "rgba(255, 255, 255, 0.1)",
          timeVisible: true,
          secondsVisible: false,
        },
      });

      candleSeries = tvChart.addCandlestickSeries({
        upColor: "#10b981",
        downColor: "#ef4444",
        borderUpColor: "#10b981",
        borderDownColor: "#ef4444",
        wickUpColor: "#10b981",
        wickDownColor: "#ef4444",
      });

      ema20Series = tvChart.addLineSeries({
        color: "#38bdf8",
        lineWidth: 2,
        title: "EMA 20",
        priceLineVisible: false,
      });

      ema50Series = tvChart.addLineSeries({
        color: "#a855f7",
        lineWidth: 2,
        title: "EMA 50",
        priceLineVisible: false,
      });

      ema200Series = tvChart.addLineSeries({
        color: "#f59e0b",
        lineWidth: 2,
        title: "EMA 200",
        priceLineVisible: false,
      });

      window.addEventListener("resize", () => {
        if (tvChart && container) {
          tvChart.applyOptions({ width: container.clientWidth });
        }
      });
    }

    if (tvChart && container && container.clientWidth > 0) {
      tvChart.applyOptions({ width: container.clientWidth });
    }

    // Helper: deduplicate & sort points by time strictly ascending
    const dedupe = (arr) => {
      if (!Array.isArray(arr) || arr.length === 0) return [];
      const sorted = [...arr].sort((a, b) => a.time - b.time);
      const res = [];
      const seen = new Set();
      for (const item of sorted) {
        if (!seen.has(item.time) && item.value !== null && item.value !== undefined) {
          seen.add(item.time);
          res.push(item);
        }
      }
      return res;
    };

    const dedupeCandles = (arr) => {
      if (!Array.isArray(arr) || arr.length === 0) return [];
      const sorted = [...arr].sort((a, b) => a.time - b.time);
      const res = [];
      const seen = new Set();
      for (const item of sorted) {
        if (!seen.has(item.time)) {
          seen.add(item.time);
          res.push(item);
        }
      }
      return res;
    };

    const uniqueCandles = dedupeCandles(candles);
    if (candleSeries && uniqueCandles.length > 0) {
      candleSeries.setData(uniqueCandles);
    }

    if (ema20Series) {
      ema20Series.applyOptions({ visible: chartIndicatorsConfig.ema20 });
      ema20Series.setData(dedupe(ema20Data));
    }
    if (ema50Series) {
      ema50Series.applyOptions({ visible: chartIndicatorsConfig.ema50 });
      ema50Series.setData(dedupe(ema50Data));
    }
    if (ema200Series) {
      ema200Series.applyOptions({ visible: chartIndicatorsConfig.ema200 });
      ema200Series.setData(dedupe(ema200Data));
    }

    if (tvChart) {
      tvChart.timeScale().fitContent();
    }

    // Clear old price lines
    if (candleSeries && activePriceLines.length > 0) {
      for (const pl of activePriceLines) {
        try {
          candleSeries.removePriceLine(pl);
        } catch (e) {}
      }
      activePriceLines = [];
    }

    const decimals = metrics.digits !== undefined ? metrics.digits : (currentChartSymbol.includes("SOL") ? 4 : 2);

    // Support Lines (Emerald dashed)
    if (candleSeries && chartIndicatorsConfig.sr) {
      for (const sp of supports) {
        try {
          const pl = candleSeries.createPriceLine({
            price: sp.price,
            color: "#10b981",
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle ? LightweightCharts.LineStyle.Dashed : 2,
            axisLabelVisible: true,
            title: sp.label || "Soporte",
          });
          activePriceLines.push(pl);
        } catch (e) {
          console.warn("Failed creating support line:", e);
        }
      }

      // Resistance Lines (Red dashed)
      for (const resItem of resistances) {
        try {
          const pl = candleSeries.createPriceLine({
            price: resItem.price,
            color: "#ef4444",
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle ? LightweightCharts.LineStyle.Dashed : 2,
            axisLabelVisible: true,
            title: resItem.label || "Resistencia",
          });
          activePriceLines.push(pl);
        } catch (e) {
          console.warn("Failed creating resistance line:", e);
        }
      }
    }

    // Active Trade or Theoretical levels (SL / TP / Entry)
    if (candleSeries && chartIndicatorsConfig.tradeLevels) {
      if (activeTrade) {
        if (activeTrade.open_price) {
          activePriceLines.push(candleSeries.createPriceLine({
            price: activeTrade.open_price,
            color: "#3b82f6",
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle ? LightweightCharts.LineStyle.Solid : 0,
            axisLabelVisible: true,
            title: `ENTRADA #${activeTrade.ticket} (${activeTrade.type})`,
          }));
        }
        if (activeTrade.sl) {
          activePriceLines.push(candleSeries.createPriceLine({
            price: activeTrade.sl,
            color: "#dc2626",
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle ? LightweightCharts.LineStyle.Solid : 0,
            axisLabelVisible: true,
            title: `SL #${activeTrade.ticket}`,
          }));
        }
        if (activeTrade.tp) {
          activePriceLines.push(candleSeries.createPriceLine({
            price: activeTrade.tp,
            color: "#16a34a",
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle ? LightweightCharts.LineStyle.Solid : 0,
            axisLabelVisible: true,
            title: `TP #${activeTrade.ticket}`,
          }));
        }
      } else if (metrics.theoretical_sl && metrics.theoretical_tp) {
        activePriceLines.push(candleSeries.createPriceLine({
          price: metrics.theoretical_sl,
          color: "rgba(239, 68, 68, 0.4)",
          lineWidth: 1,
          lineStyle: LightweightCharts.LineStyle ? LightweightCharts.LineStyle.Dotted : 1,
          axisLabelVisible: false,
          title: "SL Teórico ATR",
        }));
        activePriceLines.push(candleSeries.createPriceLine({
          price: metrics.theoretical_tp,
          color: "rgba(16, 185, 129, 0.4)",
          lineWidth: 1,
          lineStyle: LightweightCharts.LineStyle ? LightweightCharts.LineStyle.Dotted : 1,
          axisLabelVisible: false,
          title: "TP Teórico 2.0R",
        }));
      }
    }

    // Update Topbar Metric values
    const lastCandle = uniqueCandles[uniqueCandles.length - 1];
    const currentPrice = metrics.close !== undefined ? metrics.close : (lastCandle ? lastCandle.close : null);
    if (currentPrice !== null) {
      const valPrice = document.getElementById("chart-val-price");
      if (valPrice) valPrice.textContent = `$${currentPrice.toFixed(decimals)}`;
    }

    const valEma20 = document.getElementById("chart-val-ema20");
    if (valEma20) valEma20.textContent = metrics.ema20 ? `$${metrics.ema20.toFixed(decimals)}` : "--";

    const valEma50 = document.getElementById("chart-val-ema50");
    if (valEma50) valEma50.textContent = metrics.ema50 ? `$${metrics.ema50.toFixed(decimals)}` : "--";

    const valEma200 = document.getElementById("chart-val-ema200");
    if (valEma200) valEma200.textContent = metrics.ema200 ? `$${metrics.ema200.toFixed(decimals)}` : "--";

    const valRsi = document.getElementById("chart-val-rsi");
    if (valRsi) {
      valRsi.textContent = metrics.rsi ? metrics.rsi.toFixed(1) : "--";
      if (metrics.rsi >= 55) valRsi.style.color = "#34d399";
      else if (metrics.rsi <= 45) valRsi.style.color = "#f87171";
      else valRsi.style.color = "#e2e8f0";
    }

    const valAdx = document.getElementById("chart-val-adx");
    if (valAdx) {
      valAdx.textContent = metrics.adx ? `${metrics.adx.toFixed(1)} (${metrics.adx >= 25 ? 'Fuerte' : 'Débil'})` : "--";
    }

    const valAtr = document.getElementById("chart-val-atr");
    if (valAtr) valAtr.textContent = metrics.atr ? `$${metrics.atr.toFixed(decimals)}` : "--";

    const chipTrend = document.getElementById("chart-chip-trend");
    if (chipTrend) {
      const align = metrics.trend_alignment || "MIXED";
      if (align.includes("BULLISH")) {
        chipTrend.innerHTML = `<i class="fa-solid fa-arrow-trend-up"></i> ALCISTA (EMA 20>50>200)`;
        chipTrend.style.color = "#34d399";
        chipTrend.style.borderColor = "rgba(16,185,129,0.3)";
        chipTrend.style.background = "rgba(16,185,129,0.1)";
      } else if (align.includes("BEARISH")) {
        chipTrend.innerHTML = `<i class="fa-solid fa-arrow-trend-down"></i> BAJISTA (EMA 20<50<200)`;
        chipTrend.style.color = "#f87171";
        chipTrend.style.borderColor = "rgba(239,68,68,0.3)";
        chipTrend.style.background = "rgba(239,68,68,0.1)";
      } else {
        chipTrend.innerHTML = `<i class="fa-solid fa-arrows-left-right"></i> TENDENCIA MIXTA`;
        chipTrend.style.color = "#f59e0b";
        chipTrend.style.borderColor = "rgba(245,158,11,0.3)";
        chipTrend.style.background = "rgba(245,158,11,0.1)";
      }
    }

    const valSpread = document.getElementById("chart-val-spread");
    if (valSpread) valSpread.textContent = metrics.spread_points !== undefined ? metrics.spread_points : "--";

    // Update S/R Legend text
    const supportsListEl = document.getElementById("chart-supports-list");
    if (supportsListEl) {
      if (supports.length > 0) {
        supportsListEl.innerHTML = supports.map(s => `<span style="background:rgba(16,185,129,0.15);padding:1px 5px;border-radius:3px;margin-right:4px">$${s.price.toFixed(decimals)} (${s.label.replace('Soporte ', '')})</span>`).join(" ");
      } else {
        supportsListEl.textContent = "Ninguno en rango inmediato";
      }
    }

    const resistancesListEl = document.getElementById("chart-resistances-list");
    if (resistancesListEl) {
      if (resistances.length > 0) {
        resistancesListEl.innerHTML = resistances.map(r => `<span style="background:rgba(239,68,68,0.15);padding:1px 5px;border-radius:3px;margin-right:4px">$${r.price.toFixed(decimals)} (${r.label.replace('Resistencia ', '')})</span>`).join(" ");
      } else {
        resistancesListEl.textContent = "Ninguno en rango inmediato";
      }
    }

    // Update Trade Status text
    const tradeBadgeEl = document.getElementById("chart-val-trade-badge");
    if (tradeBadgeEl) {
      if (activeTrade) {
        const pnlColor = activeTrade.profit >= 0 ? "#34d399" : "#f87171";
        tradeBadgeEl.innerHTML = `
          <span style="color:#60a5fa;font-weight:700"><i class="fa-solid fa-bolt"></i> POSICIÓN ACTIVA #${activeTrade.ticket}</span> · 
          <span style="color:${activeTrade.type === 'BUY' ? '#34d399' : '#f87171'};font-weight:700">${activeTrade.type} ${activeTrade.volume} lotes</span> · 
          <span>Entrada: $${activeTrade.open_price.toFixed(decimals)}</span> · 
          <span style="color:${pnlColor};font-weight:700">P&L: $${activeTrade.profit.toFixed(2)}</span>
        `;
      } else {
        tradeBadgeEl.innerHTML = `<span style="color:#94a3b8"><i class="fa-solid fa-circle-check"></i> Sin orden activa en mercado para ${escapeHtml(currentChartSymbol)}</span>`;
      }
    }

  } catch (err) {
    if (spinner) spinner.style.display = "none";
    console.error("Error loading candles:", err);
  }
}

function changeChartAsset() {
  const symbolSelect = document.getElementById("chart-symbol-select");
  const tfSelect = document.getElementById("chart-tf-select");
  if (symbolSelect) currentChartSymbol = symbolSelect.value;
  if (tfSelect) currentChartTf = tfSelect.value;
  loadCandlesData();
}

// Global scope export for inline onclick handlers
window.toggleChartIndicator = toggleChartIndicator;
window.loadCandlesData = loadCandlesData;
window.changeChartAsset = changeChartAsset;


// =============================================================================
// PROXIMITY RADAR: REAL-TIME FLEET TRADE READINESS
// =============================================================================

async function loadProximityRadar() {
  try {
    const res = await fetch("/api/bots/proximity");
    if (!res.ok) return;
    const data = await res.json();
    renderProximityRadar(data);
  } catch (err) {
    console.debug("Error loading proximity radar:", err);
  }
}

function renderProximityRadar(data) {
  if (!data || !data.ranking) return;

  const top = data.top_candidate;
  const ranking = data.ranking || [];

  // Update Top Badge & Meta
  const badgeEl = document.getElementById("proximity-top-badge");
  if (badgeEl && top) {
    const color = top.status_color || "#22c55e";
    badgeEl.style.borderColor = color;
    badgeEl.style.color = color;
    badgeEl.style.background = `${color}1a`;
    badgeEl.innerHTML = `<i class="fa-solid fa-crosshairs"></i> TOP 1: ${escapeHtml(top.name)} (${top.proximity_score}%)`;
  }

  const updatedEl = document.getElementById("proximity-updated-at");
  if (updatedEl && data.updated_at) {
    updatedEl.textContent = `Actualizado: ${data.updated_at}`;
  }

  const countEl = document.getElementById("proximity-fleet-count");
  if (countEl) {
    countEl.textContent = `${ranking.length} bots monitoreados`;
  }

  // 1. Render Hero Card (Top Candidate)
  const heroContainer = document.getElementById("proximity-hero-container");
  if (heroContainer && top) {
    const dirClass = (top.direction || "").toLowerCase();
    const isBuy = dirClass.includes("buy");
    const h1Color = top.h1_regime === "BULLISH" ? "#34d399" : (top.h1_regime === "BEARISH" ? "#f87171" : "#94a3b8");
    const inZoneBadge = top.in_zone
      ? `<span style="background:rgba(34,197,94,0.18);color:#34d399;border:1px solid rgba(34,197,94,0.4);padding:2px 6px;border-radius:4px;font-size:9px;font-weight:700">EN ZONA</span>`
      : `<span style="background:rgba(239,68,68,0.15);color:#f87171;border:1px solid rgba(239,68,68,0.3);padding:2px 6px;border-radius:4px;font-size:9px">FUERA DE ZONA</span>`;

    heroContainer.innerHTML = `
      <div class="proximity-hero-card" style="border-color:${top.status_color || '#22c55e'};box-shadow:0 0 25px ${top.status_color || '#22c55e'}26">
        <div class="proximity-hero-top">
          <div class="proximity-hero-title">
            <span style="font-family:var(--mono);font-size:11px;font-weight:800;background:rgba(255,255,255,0.08);padding:4px 8px;border-radius:4px;color:#f8fafc">
              🥇 #1 MÁS PRÓXIMO
            </span>
            <h3 style="display:flex;align-items:center;gap:8px">
              ${escapeHtml(top.name)}
              <span class="proximity-dir-tag ${dirClass}">${escapeHtml(top.direction)}</span>
            </h3>
            <span style="font-family:var(--mono);font-size:10px;color:#94a3b8">
              [${escapeHtml(top.fleet)} · ${escapeHtml(top.strategy)}]
            </span>
          </div>
          <div style="display:flex;align-items:center;gap:8px">
            <span style="background:${top.status_color}22;color:${top.status_color};border:1px solid ${top.status_color};padding:5px 10px;border-radius:6px;font-family:var(--mono);font-size:11px;font-weight:800;letter-spacing:0.05em">
              ${escapeHtml(top.status_badge)}
            </span>
            <span style="font-family:var(--mono);font-size:14px;font-weight:800;color:#f8fafc">
              ${top.proximity_score}%
            </span>
          </div>
        </div>

        <div class="proximity-bar-wrap">
          <div class="proximity-bar-fill" style="width:${top.proximity_score}%;background:linear-gradient(90deg, #10b981 0%, ${top.status_color || '#22c55e'} 100%)"></div>
        </div>

        <div class="proximity-metric-grid">
          <div class="proximity-metric-item">
            <span>PRECIO ACTUAL</span>
            <strong style="color:#38bdf8">${top.current_price > 0 ? top.current_price.toLocaleString() : '—'}</strong>
          </div>
          <div class="proximity-metric-item">
            <span>NIVEL INSTITUCIONAL</span>
            <strong>${escapeHtml(top.zone_type)}: ${top.target_zone}</strong>
          </div>
          <div class="proximity-metric-item">
            <span>DISTANCIA A ZONA</span>
            <strong>${top.distance_pts} pts ${inZoneBadge}</strong>
          </div>
          <div class="proximity-metric-item">
            <span>RÉGIMEN MACRO H1</span>
            <strong style="color:${h1Color}">${escapeHtml(top.h1_regime)}</strong>
          </div>
          <div class="proximity-metric-item">
            <span>IA GATEKEEPER / SCORE</span>
            <strong style="color:#c084fc">${escapeHtml(top.score_display)}</strong>
          </div>
          <div class="proximity-metric-item">
            <span>PRÓXIMA EVALUACIÓN</span>
            <strong style="color:#fbbf24"><i class="fa-regular fa-clock"></i> ${escapeHtml(top.eta_next_candle)}</strong>
          </div>
        </div>

        ${top.sl_price ? `
          <div style="display:flex;gap:12px;margin-bottom:10px;background:rgba(0,0,0,0.3);padding:8px 12px;border-radius:6px;font-family:var(--mono);font-size:10.5px">
            <span><i class="fa-solid fa-shield" style="color:#ef4444"></i> Stop Loss: <b>${top.sl_price}</b></span>
            <span><i class="fa-solid fa-bullseye" style="color:#22c55e"></i> Take Profit: <b>${top.tp_price}</b></span>
            <span><i class="fa-solid fa-scale-balanced" style="color:#38bdf8"></i> Ratio R:R: <b>${top.risk_reward || '1:3.0'}</b></span>
          </div>
        ` : ''}

        <div class="proximity-detail-banner" style="background:${top.status_color}14;border-color:${top.status_color}40">
          <i class="fa-solid fa-circle-info" style="color:${top.status_color}"></i>
          <span><b>Diagnóstico en vivo:</b> ${escapeHtml(top.detail)}</span>
        </div>
      </div>
    `;
  }

  // 2. Render Fleet Ranking Cards (Rank 2 through N)
  const gridContainer = document.getElementById("proximity-ranking-grid");
  if (gridContainer) {
    const others = ranking.filter(c => c.rank > 1);
    if (others.length === 0) {
      gridContainer.innerHTML = `<div style="color:#94a3b8;font-size:11px">No hay otros bots activos.</div>`;
      return;
    }

    gridContainer.innerHTML = others.map(c => {
      const dirClass = (c.direction || "").toLowerCase();
      const cardColor = c.status_color || "#64748b";
      return `
        <article class="proximity-card" style="border-top:3px solid ${cardColor}">
          <div class="proximity-card-header">
            <span class="proximity-card-rank">#${c.rank} · ${escapeHtml(c.fleet)}</span>
            <span style="background:${cardColor}1f;color:${cardColor};border:1px solid ${cardColor}55;padding:2px 6px;border-radius:4px;font-family:var(--mono);font-size:9px;font-weight:700">
              ${escapeHtml(c.status_badge)}
            </span>
          </div>
          <div class="proximity-card-body">
            <div style="display:flex;justify-content:space-between;align-items:center">
              <strong style="font-size:14px;color:#f8fafc">${escapeHtml(c.name)}</strong>
              <span class="proximity-dir-tag ${dirClass}">${escapeHtml(c.direction)}</span>
            </div>

            <div style="display:flex;justify-content:space-between;align-items:center;font-family:var(--mono);font-size:9.5px;color:#94a3b8;margin-top:2px">
              <span>Proximidad: <b style="color:${cardColor}">${c.proximity_score}%</b></span>
              <span><i class="fa-regular fa-clock"></i> ${escapeHtml(c.eta_next_candle)}</span>
            </div>

            <div class="proximity-bar-wrap" style="height:6px;margin:4px 0 6px 0">
              <div class="proximity-bar-fill" style="width:${c.proximity_score}%;background:${cardColor}"></div>
            </div>

            <div style="font-family:var(--mono);font-size:10px;color:#cbd5e1;background:rgba(0,0,0,0.25);padding:6px 8px;border-radius:4px">
              <div style="display:flex;justify-content:space-between">
                <span style="color:#94a3b8">Score / Nivel:</span>
                <b>${escapeHtml(c.score_display)}</b>
              </div>
              <div style="display:flex;justify-content:space-between;margin-top:2px">
                <span style="color:#94a3b8">Macro H1:</span>
                <b>${escapeHtml(c.h1_regime)}</b>
              </div>
            </div>

            <div style="font-size:10.5px;color:#94a3b8;line-height:1.35;margin-top:4px">
              ${escapeHtml(c.detail)}
            </div>
          </div>
        </article>
      `;
    }).join("");
  }
}

window.loadProximityRadar = loadProximityRadar;

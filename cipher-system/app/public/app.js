const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const api = "";
const storageKey = "cipher_research_workspace_v2";
const defaults = {
  watchlist: ["SPY", "QQQ", "IWM", "NVDA", "AAPL"],
  journal: [],
  saves: [],
  refreshSeconds: 15,
  aoReferenceSeeded: false,
};
const stored = (() => {
  try {
    return { ...defaults, ...JSON.parse(localStorage.getItem(storageKey) || "{}") };
  } catch {
    return { ...defaults };
  }
})();

const state = {
  ticker: "SPY",
  view: "matrix",
  matrixExp: 5,
  matrixMode: "matrix",
  nodeGalaxy: false,
  nightExp: 1,
  timeframe: "5m",
  metric: "gex",
  windowPct: 0.25,
  matrix: null,
  night: null,
  bars: null,
  flow: null,
  auto: true,
  pollTimer: null,
  stream: null,
  live: false,
  trident: {},
  tridentErrors: {},
  tridentMetric: "gex",
  tridentDensity: "compact",
  backtest: null,
  scanner: null,
  scannerClusters: [],
  scannerMeta: null,
  rankingLab: null,
  rankingBusy: false,
  scannerBusy: false,
  scannerJobId: null,
  scannerProgress: null,
  scannerUniverseCount: 0,
  scannerMode: "short",
  scannerStrategy: "cipher",
  scannerFilter: "all",
  scannerHint: "Short term ? nearest/lowest-DTE expiration only; tighter strike window.",
  clusterExp: "nearest",
  nightSplit: false,
  nightTape: false,
  nightVP: false,
  nightXray: false,
  nightGhost: false,
  nightExpand: false,
  showFC: true,
  xrayMetric: "gex",
  tapeMin: 5000,
  tapeSQ: false,
  splitCache: {},
  workspace: 1,
  spyglassFilters: {
    premium: 5000,
    maxPrice: "all",
    type: "All",
    side: "Bid/Ask",
    money: "All",
  },
  watchData: {},
  ...stored,
};

function seedAccessObsidianReferenceState() {
  const before = JSON.stringify({ watchlist: state.watchlist, journal: state.journal, saves: state.saves, aoReferenceSeeded: state.aoReferenceSeeded });
  const observedWatchlist = ["NBIS", "SPY", "DRAM", "ARM", "CRDO", "RXT", "AVGO", "QQQ", "MSFT"];
  const defaultList = defaults.watchlist.join("|");
  const currentList = (state.watchlist || []).join("|");
  if (!state.watchlist?.length || currentList === defaultList) {
    state.watchlist = observedWatchlist;
  } else {
    state.watchlist = [...new Set([...state.watchlist, ...observedWatchlist])];
  }
  const observedJournal = [
    { id: "ao-journal-2026-07-15", date: "2026-07-15", result: "Profit", amount: "$616", note: "Seeded from AccessObsidian parity capture" },
    { id: "ao-journal-2026-07-16", date: "2026-07-16", result: "Profit", amount: "$400", note: "Seeded from AccessObsidian parity capture" },
  ];
  const journalIds = new Set((state.journal || []).map((entry) => entry.id));
  state.journal = [...(state.journal || []), ...observedJournal.filter((entry) => !journalIds.has(entry.id))];

  const observedSaves = [
    { id: "ao-save-msft-1", ticker: "MSFT", view: "1 Exp", created: "7/16/26", date: "7/16/26", price: "$402.50", levels: [{ strike: "410", score: "100" }, { strike: "400", score: "82" }, { strike: "415", score: "47" }, { strike: "395", score: "32" }], note: "Seeded chart-save parity card" },
    { id: "ao-save-panw-1", ticker: "PANW", view: "1 Exp", created: "7/14/26", date: "7/14/26", price: "$353.00", levels: [{ strike: "380", score: "100" }, { strike: "370", score: "55" }, { strike: "360", score: "38" }, { strike: "320", score: "33" }], note: "Seeded chart-save parity card" },
    { id: "ao-save-snps-1", ticker: "SNPS", view: "1 Exp", created: "7/13/26", date: "7/13/26", price: "$438.68", levels: [{ strike: "390", score: "100" }, { strike: "420", score: "77" }, { strike: "400", score: "35" }, { strike: "440", score: "27" }], note: "Seeded chart-save parity card" },
    { id: "ao-save-rxt-1", ticker: "RXT", view: "1 Exp", created: "7/6/26", date: "7/6/26", price: "$6.85", levels: [{ strike: "7.5", score: "100" }, { strike: "10", score: "68" }, { strike: "8", score: "56" }, { strike: "6", score: "18" }], note: "Seeded chart-save parity card" },
  ];
  const saveIds = new Set((state.saves || []).map((save) => save.id));
  state.saves = [...(state.saves || []), ...observedSaves.filter((save) => !saveIds.has(save.id))];
  state.aoReferenceSeeded = true;
  return before !== JSON.stringify({ watchlist: state.watchlist, journal: state.journal, saves: state.saves, aoReferenceSeeded: state.aoReferenceSeeded });
}

const persist = () =>
  localStorage.setItem(
    storageKey,
    JSON.stringify({
      watchlist: state.watchlist,
      journal: state.journal,
      saves: state.saves,
      refreshSeconds: state.refreshSeconds,
      aoReferenceSeeded: state.aoReferenceSeeded,
    }),
  );

if (seedAccessObsidianReferenceState()) persist();

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));

const fmt = (value) => {
  const amount = Math.abs(Number(value) || 0);
  const sign = value < 0 ? "-" : "";
  if (amount >= 1e9) return `${sign}$${(amount / 1e9).toFixed(2)}B`;
  if (amount >= 1e6) return `${sign}$${(amount / 1e6).toFixed(1)}M`;
  if (amount >= 1e3) return `${sign}$${(amount / 1e3).toFixed(1)}K`;
  // Available $0 / sub-$1 cells must not look like missing contracts ("—").
  if (amount < 1) return `${sign}$0`;
  return `${sign}$${amount.toFixed(0)}`;
};

const dollars = (value, digits = 2) =>
  Number.isFinite(Number(value)) ? `$${Number(value).toFixed(digits)}` : "—";

/** Calendar DTE from ISO YYYY-MM-DD (UTC date parts — matches expiry labels). */
const dte = (iso) => {
  const today = new Date().toISOString().slice(0, 10);
  const days = Math.max(
    0,
    Math.round((Date.parse(`${iso}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 864e5),
  );
  return `${days}d`;
};

/** Expiration column header: "Jul 24" over "4d" (ET calendar date from ISO). */
const expDateLabel = (iso) => {
  const [y, m, d] = String(iso || "")
    .slice(0, 10)
    .split("-")
    .map(Number);
  if (!y || !m || !d) return String(iso || "—");
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
};

const heat = (value, maximum) => {
  const ratio = Math.abs(value || 0) / Math.max(maximum, 1);
  if (!value) return "empty";
  const group = ratio > 0.65 ? 3 : ratio > 0.25 ? 2 : 1;
  return `${value > 0 ? "positive" : "negative"}-${group}`;
};

const fieldForMetric = () => (state.metric === "vex" ? "net_vex" : "net_gex");

function setLive(ok, label) {
  state.live = ok;
  const dot = $("#liveDot");
  const textEl = $("#liveLabel");
  if (dot) dot.classList.toggle("offline", !ok);
  if (textEl) textEl.textContent = label || (ok ? "Live" : "Offline");
}

function setStatus(text) {
  const node = $("#coverage");
  if (node) node.textContent = text;
}

function rowAbsStrength(row, metric, expirations) {
  return expirations.reduce((sum, exp) => {
    const cell = row.cells.find((c) => c.expiration === exp);
    return sum + Math.abs(cell?.[metric] || 0);
  }, 0);
}

function matrixDerived(data, metric, expirations) {
  const rows = data.rows || [];
  const spot = data.quote?.price_context;
  const summary = data.summary || {};
  const strengths = rows.map((row) => rowAbsStrength(row, metric, expirations));
  const sorted = [...strengths].filter((x) => x > 0).sort((a, b) => a - b);
  const pct = (p) =>
    sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))] : 0;
  const p85 = pct(85);
  const p65 = pct(65);
  const topPullStrike = summary.global_max_strike;
  let nearestFloor = null;
  let nearestCeiling = null;
  if (Number.isFinite(spot)) {
    const below = rows.filter((r) => r.strike < spot).sort((a, b) => b.strike - a.strike);
    const above = rows.filter((r) => r.strike > spot).sort((a, b) => a.strike - b.strike);
    nearestFloor =
      below.find((r) => rowAbsStrength(r, metric, expirations) >= p65)?.strike ??
      summary.put_wall_strike ??
      null;
    nearestCeiling =
      above.find((r) => rowAbsStrength(r, metric, expirations) >= p65)?.strike ??
      summary.call_wall_strike ??
      null;
  }
  let topCell = null;
  let topAbs = -1;
  for (const row of rows) {
    for (const exp of expirations) {
      const cell = row.cells.find((c) => c.expiration === exp);
      const abs = Math.abs(cell?.[metric] || 0);
      if (abs > topAbs) {
        topAbs = abs;
        topCell = { strike: row.strike, expiration: exp, value: cell?.[metric] || 0 };
      }
    }
  }
  return {
    strengths,
    p85,
    p65,
    topPullStrike: topPullStrike ?? topCell?.strike,
    topCell,
    nearestFloor,
    nearestCeiling,
    spot,
  };
}

function sniperKeepRow(row, derived, metric, expirations, idx) {
  const strength = derived.strengths[idx] || 0;
  const spot = derived.spot;
  const near =
    Number.isFinite(spot) && Math.abs(row.strike - spot) / Math.max(spot, 1e-9) <= 0.015;
  if (strength >= derived.p85 && strength > 0) return true;
  if (near && strength >= derived.p65 && strength > 0) return true;
  if (derived.topPullStrike != null && Math.abs(row.strike - derived.topPullStrike) < 1e-6) return true;
  if (derived.nearestFloor != null && Math.abs(row.strike - derived.nearestFloor) < 1e-6) return true;
  if (derived.nearestCeiling != null && Math.abs(row.strike - derived.nearestCeiling) < 1e-6) return true;
  if (row.is_spot_band) return true;
  return false;
}

function renderLevelsBar() {
  const bar = $("#levelsBar");
  if (!bar) return;
  const summary = state.matrix?.summary || {};
  const spot = state.matrix?.quote?.price_context;
  const metric = fieldForMetric();
  const exps = selectedExpirations();
  const derived = state.matrix ? matrixDerived(state.matrix, metric, exps) : {};
  const items = [
    ["Spot", dollars(spot)],
    ["Top pull", dollars(derived.topPullStrike ?? summary.global_max_strike, 1)],
    ["Floor", dollars(derived.nearestFloor ?? summary.put_wall_strike, 1)],
    ["Ceiling", dollars(derived.nearestCeiling ?? summary.call_wall_strike, 1)],
    ["Î³ flip", dollars(summary.gamma_flip_level, 2)],
    ["Mode", state.matrixMode === "sniper" ? "Sniper" : state.nodeGalaxy ? "Galaxy" : "Matrix"],
  ];
  bar.innerHTML = items
    .map(
      ([label, value]) =>
        `<div class="level-chip"><span>${label}</span><strong>${escapeHtml(String(value))}</strong></div>`,
    )
    .join("");
}

function selectedExpirations(data = state.matrix) {
  const all = data?.expirations || [];
  if (!all.length) return [];
  const n = Math.max(1, Number(state.matrixExp) || 12);
  return all.slice(0, n);
}

/** Paint a Strike Matrix heatmap into a mount element (shared by Matrix + Trident). */
function paintMatrixGrid(mount, data, opts = {}) {
  if (!mount || !data) return null;
  const metric = opts.metric || fieldForMetric();
  const showFC = opts.showFC ?? state.showFC;
  const matrixMode = opts.matrixMode || state.matrixMode;
  const expirations = selectedExpirations(data);
  const derived = matrixDerived(data, metric, expirations);
  const allRows = data.rows || [];
  let rows = [...allRows];
  if (matrixMode === "sniper") {
    rows = allRows.filter((row, idx) => sniperKeepRow(row, derived, metric, expirations, idx));
  }

  const showAgg = matrixMode === "sniper";
  const headSpecs = showAgg
    ? [
        { kind: "plain", label: "STRIKE" },
        { kind: "plain", label: "Î£ GEX" },
        { kind: "plain", label: "Î£ VEX" },
        ...expirations.map((exp) => ({ kind: "exp", iso: exp })),
      ]
    : [
        { kind: "plain", label: "STRIKE" },
        ...expirations.map((exp) => ({ kind: "exp", iso: exp })),
      ];
  mount.innerHTML = "";
  mount.className = "matrix";
  mount.style.gridTemplateColumns = showAgg
    ? `58px 72px 72px repeat(${Math.max(expirations.length, 1)}, minmax(64px, 1fr))`
    : `58px repeat(${Math.max(expirations.length, 1)}, minmax(64px, 1fr))`;
  mount.append(
    ...headSpecs.map((spec) => {
      const cell = document.createElement("div");
      if (spec.kind === "exp") {
        cell.className = "cell head exp-head";
        cell.innerHTML = `<span class="exp-date">${escapeHtml(expDateLabel(spec.iso))}</span><span class="exp-dte">${escapeHtml(dte(spec.iso))}</span>`;
        cell.title = `${expDateLabel(spec.iso)} ? ${dte(spec.iso)}`;
      } else {
        cell.className = "cell head";
        cell.textContent = spec.label;
      }
      return cell;
    }),
  );

  const values = rows.flatMap((row) =>
    expirations.map((exp) => {
      const item = row.cells.find((cell) => cell.expiration === exp);
      return item?.[metric] || 0;
    }),
  );
  const maximum = Math.max(...values.map(Math.abs), 1);

  for (const row of [...rows].reverse()) {
    const isTopPull =
      derived.topPullStrike != null && Math.abs(row.strike - derived.topPullStrike) < 1e-6;
    const isFloor =
      showFC &&
      derived.nearestFloor != null &&
      Math.abs(row.strike - derived.nearestFloor) < 1e-6;
    const isCeil =
      showFC &&
      derived.nearestCeiling != null &&
      Math.abs(row.strike - derived.nearestCeiling) < 1e-6;

    const strike = document.createElement("div");
    strike.className = `cell strike ${row.is_spot_band ? "spot" : ""} ${isTopPull ? "golden-row" : ""}`;
    strike.dataset.strike = String(row.strike);
    strike.textContent = Number(row.strike).toFixed(row.strike % 1 ? 1 : 0);
    if (isTopPull) strike.title = "Top pull / golden";
    else if (isFloor) strike.title = "Nearest floor";
    else if (isCeil) strike.title = "Nearest ceiling";
    mount.append(strike);

    if (showAgg) {
      const sumGex = expirations.reduce((s, exp) => {
        const c = row.cells.find((x) => x.expiration === exp);
        return s + (c?.net_gex || 0);
      }, 0);
      const sumVex = expirations.reduce((s, exp) => {
        const c = row.cells.find((x) => x.expiration === exp);
        return s + (c?.net_vex || 0);
      }, 0);
      for (const sumVal of [sumGex, sumVex]) {
        const agg = document.createElement("div");
        const useMax = Math.max(Math.abs(sumGex), Math.abs(sumVex), 1);
        agg.className = `cell agg ${heat(sumVal, useMax)} ${isTopPull ? "golden" : ""}`;
        agg.textContent = fmt(sumVal);
        mount.append(agg);
      }
    }

    for (const expiration of expirations) {
      const item = row.cells.find((cell) => cell.expiration === expiration);
      const cell = document.createElement("div");
      const value = item?.[metric];
      const isTopCell =
        derived.topCell &&
        Math.abs(derived.topCell.strike - row.strike) < 1e-6 &&
        derived.topCell.expiration === expiration;
      const unavailable = !item?.available;
      const listed = Boolean(item?.listed);
      cell.className = `cell ${
        unavailable ? (listed ? "empty listed-zero" : "empty missing") : heat(value, maximum)
      } ${row.is_spot_band ? "spot" : ""} ${
        !unavailable && (isTopCell || (isTopPull && Math.abs(value || 0) / maximum > 0.5)) ? "golden" : ""
      } ${!unavailable && isFloor ? "floor-cell" : ""} ${!unavailable && isCeil ? "ceil-cell" : ""} ${
        item?.oi_assumed_zero ? "oi-assumed" : ""
      }`;
      cell.dataset.strike = String(row.strike);
      if (item?.available) {
        cell.title = `${metric === "net_vex" ? "VEX" : "GEX"} ${fmt(value)} ? OI ${(item.call_oi || 0) + (item.put_oi || 0)}${
          item.oi_assumed_zero ? " ? OI missing?0" : ""
        }${isTopCell ? " ? TOP PULL" : ""}`;
        cell.textContent = fmt(value);
      } else if (listed) {
        cell.title = "Listed contract but missing usable gamma, IV, or open interest";
        cell.textContent = "—";
      } else {
        cell.title = "No listed contract at this strike/expiry";
        cell.textContent = "—";
      }
      mount.append(cell);
    }
  }
  return { derived, rows, expirations, metric };
}

function scrollWrapToStrike(wrap, strike) {
  if (!wrap || !Number.isFinite(Number(strike))) return;
  const el = wrap.querySelector(`[data-strike="${strike}"]`);
  if (el) {
    const top = el.offsetTop - wrap.clientHeight / 2 + el.clientHeight / 2;
    wrap.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
  }
}

function scrollMatrixToStrike(strike) {
  scrollWrapToStrike($("#matrixWrap"), strike);
}

function renderMatrix() {
  const data = state.matrix;
  if (!data) return;
  const galaxyOn = state.nodeGalaxy;
  $("#matrix")?.classList.toggle("hidden", galaxyOn);
  $("#galaxyCanvas")?.classList.toggle("hidden", !galaxyOn);
  $("#spotTag")?.classList.toggle("hidden", galaxyOn);
  if (galaxyOn) {
    renderNodeGalaxy();
    renderLevelsBar();
    return;
  }

  const painted = paintMatrixGrid($("#matrix"), data);
  if (!painted) return;
  const { derived, rows, expirations } = painted;

  const spot = data.quote?.price_context;
  const chg = data.quote?.day_change_pct;
  const spotTag = $("#spotTag");
  if (spotTag) {
    const spotLabel = Number.isFinite(spot)
      ? Number(spot) % 1
        ? Number(spot).toFixed(2)
        : String(Math.round(Number(spot)))
      : "—";
    spotTag.textContent = `SPOT ${spotLabel}`;
    const spotRow = $("#matrix")?.querySelector(".strike.spot");
    if (spotRow) {
      spotTag.style.position = "absolute";
      spotTag.style.left = "4px";
      spotTag.style.top = `${spotRow.offsetTop + Math.max(0, (spotRow.offsetHeight - 16) / 2)}px`;
      spotTag.style.marginTop = "0";
    }
  }
  $("#quote").innerHTML = `<strong>${escapeHtml(data.ticker)}</strong>${dollars(spot)}`;
  const chgEl = $("#dayChg");
  if (chgEl) {
    if (Number.isFinite(chg)) {
      chgEl.textContent = `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`;
      chgEl.className = `chg ${chg >= 0 ? "up" : "down"}`;
    } else {
      chgEl.textContent = "";
    }
  }
  $("#feedBadge").textContent = `${(data.feed || "opra").toUpperCase()} + OI`;
  const totalExps = (data.expirations || []).length;
  const shownExps = expirations.length;
  const contracts =
    expirations.reduce(
      (sum, exp) => sum + Number(data.coverage?.per_expiration?.[exp]?.contracts_in_window || 0),
      0,
    ) || data.coverage?.contracts || 0;
  setStatus(
    `${data.ticker} · ${Number(contracts).toLocaleString()} contracts · ${shownExps}/${totalExps || shownExps} expirations · ${rows.length} strikes shown`,
  );
  $("#updated").textContent = `updated ${new Date(data.as_of).toLocaleTimeString()}`;
  const cacheEl = $("#cacheAge");
  if (cacheEl) cacheEl.textContent = "server cache 60s";
  const prov = $("#provenance");
  if (prov) {
    prov.textContent = "";
    prov.title = data.caveat || "Public-OI heuristic — not verified dealer positioning";
  }
  renderLevelsBar();
  updateHeaderTitle();
  requestAnimationFrame(() => {
    const spotPx = data.quote?.price_context;
    const band = (data.rows || []).find((r) => r.is_spot_band);
    if (band || Number.isFinite(spotPx)) scrollMatrixToStrike(band?.strike ?? spotPx);
    const tag = $("#spotTag");
    const spotRow = $("#matrix")?.querySelector(".strike.spot");
    if (tag && spotRow) {
      tag.style.top = `${spotRow.offsetTop + Math.max(0, (spotRow.offsetHeight - 16) / 2)}px`;
    }
  });
}

function renderNodeGalaxy() {
  const data = state.matrix;
  const canvas = $("#galaxyCanvas");
  const wrap = $("#matrixWrap");
  if (!data || !canvas || !wrap) return;
  const metric = fieldForMetric();
  const expirations = selectedExpirations();
  const derived = matrixDerived(data, metric, expirations);
  let rows = data.rows || [];
  if (state.matrixMode === "sniper") {
    rows = rows.filter((row, idx) => sniperKeepRow(row, derived, metric, expirations, idx));
  }
  const dpr = window.devicePixelRatio || 1;
  const width = wrap.clientWidth || 800;
  const height = wrap.clientHeight || 480;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#080a10";
  ctx.fillRect(0, 0, width, height);

  const spot = data.quote?.price_context;
  const nodes = [];
  for (const row of rows) {
    for (const exp of expirations) {
      const cell = row.cells.find((c) => c.expiration === exp);
      const value = cell?.[metric] || 0;
      if (!cell?.available || Math.abs(value) < 1) continue;
      nodes.push({ strike: row.strike, exp, value, abs: Math.abs(value), x: 0, y: 0 });
    }
  }
  nodes.sort((a, b) => b.abs - a.abs);
  const top = nodes.slice(0, 80);
  const maxAbs = Math.max(...top.map((n) => n.abs), 1);
  const cx = width / 2;
  const cy = height / 2;

  top.forEach((n) => {
    const expIdx = Math.max(0, expirations.indexOf(n.exp));
    const strikeNorm = spot ? (n.strike - spot) / (spot * 0.15) : 0;
    n.x = 80 + (expIdx + 1) * ((width - 160) / Math.max(expirations.length + 1, 2)) + (Math.random() - 0.5) * 20;
    n.y = cy - strikeNorm * (height * 0.35) + (Math.random() - 0.5) * 16;
  });

  for (let iter = 0; iter < 40; iter++) {
    for (let i = 0; i < top.length; i++) {
      for (let j = i + 1; j < top.length; j++) {
        const a = top[i];
        const b = top[j];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.hypot(dx, dy) || 0.01;
        const minDist = 18 + 10 * ((a.abs + b.abs) / maxAbs);
        if (dist < minDist) {
          const push = ((minDist - dist) / dist) * 0.35;
          a.x -= dx * push;
          a.y -= dy * push;
          b.x += dx * push;
          b.y += dy * push;
        }
      }
      const n = top[i];
      n.x += (cx - n.x) * 0.002;
      n.y += (cy - n.y) * 0.001;
      n.x = Math.min(width - 30, Math.max(30, n.x));
      n.y = Math.min(height - 30, Math.max(30, n.y));
    }
  }

  ctx.lineWidth = 1;
  for (let i = 0; i < top.length; i++) {
    for (let j = i + 1; j < top.length; j++) {
      if (Math.abs(top[i].strike - top[j].strike) > 1e-6) continue;
      ctx.strokeStyle = "#2a334488";
      ctx.beginPath();
      ctx.moveTo(top[i].x, top[i].y);
      ctx.lineTo(top[j].x, top[j].y);
      ctx.stroke();
    }
  }

  for (const n of top) {
    const r = 4 + 14 * (n.abs / maxAbs);
    const isTop =
      derived.topPullStrike != null && Math.abs(n.strike - derived.topPullStrike) < 1e-6;
    if (isTop) {
      ctx.fillStyle = "#e4ae24";
      ctx.shadowColor = "#e4ae24aa";
      ctx.shadowBlur = 12;
    } else if (n.value >= 0) {
      ctx.fillStyle = `rgba(146,0,213,${0.35 + 0.65 * (n.abs / maxAbs)})`;
      ctx.shadowBlur = 0;
    } else {
      ctx.fillStyle = `rgba(217,12,31,${0.35 + 0.65 * (n.abs / maxAbs)})`;
      ctx.shadowBlur = 0;
    }
    ctx.beginPath();
    ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;
    if (r > 10 || isTop) {
      ctx.fillStyle = "#d9dfeb";
      ctx.font = "10px IBM Plex Sans, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(String(n.strike), n.x, n.y - r - 4);
    }
  }

  ctx.fillStyle = "#778293";
  ctx.font = "11px IBM Plex Sans, sans-serif";
  ctx.textAlign = "left";
  ctx.fillText(
    `Node Galaxy ? ${data.ticker} ? ${state.metric.toUpperCase()} ? ${top.length} nodes ? gold = top pull`,
    12,
    18,
  );
  if (Number.isFinite(spot)) {
    ctx.strokeStyle = "#e9dfc966";
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(40, cy);
    ctx.lineTo(width - 40, cy);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#e9e2d4";
    ctx.fillText(`SPOT ${spot.toFixed(2)}`, 12, cy - 6);
  }

  setStatus(
    `Node Galaxy ? ${(data.coverage?.contracts || 0).toLocaleString()} contracts ? ${top.length} exposure nodes`,
  );
  $("#updated").textContent = `updated ${new Date(data.as_of).toLocaleTimeString()}`;
}

function matrixCsvLines(data, { includeTicker = false } = {}) {
  if (!data) return null;
  const metric = fieldForMetric();
  const exps = selectedExpirations(data);
  const derived = matrixDerived(data, metric, exps);
  let rows = data.rows || [];
  if (state.matrixMode === "sniper") {
    rows = rows.filter((row, idx) => sniperKeepRow(row, derived, metric, exps, idx));
  }
  const header = [
    ...(includeTicker ? ["ticker"] : []),
    "strike",
    ...exps.map((e) => `${e}_${state.metric}`),
    "sum_gex",
    "sum_vex",
  ].join(",");
  const lines = [
    header,
    ...[...rows].reverse().map((row) => {
      const cells = exps.map((exp) => {
        const c = row.cells.find((x) => x.expiration === exp);
        return c?.[metric] ?? "";
      });
      const sumGex = exps.reduce(
        (s, exp) => s + (row.cells.find((x) => x.expiration === exp)?.net_gex || 0),
        0,
      );
      const sumVex = exps.reduce(
        (s, exp) => s + (row.cells.find((x) => x.expiration === exp)?.net_vex || 0),
        0,
      );
      return [
        ...(includeTicker ? [data.ticker || ""] : []),
        row.strike,
        ...cells,
        sumGex,
        sumVex,
      ].join(",");
    }),
  ];
  return lines;
}

function downloadCsv(filename, lines) {
  if (!lines?.length) return;
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

function downloadText(filename, text) {
  const blob = new Blob([text || ""], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

function exportMatrixCsv() {
  const data = state.matrix;
  if (!data) return;
  const lines = matrixCsvLines(data);
  downloadCsv(`cipher-matrix-${data.ticker}-${state.matrixMode}-${state.metric}.csv`, lines);
}

function exportTridentCsv(ticker = null) {
  const tickers = ticker ? [ticker] : ["SPY", "QQQ", "IWM"];
  if (ticker) {
    const data = state.trident[ticker];
    const lines = matrixCsvLines(data);
    if (!lines) return setStatus(`${ticker} matrix not loaded`);
    downloadCsv(`cipher-trident-${ticker}-${state.matrixMode}-${state.metric}.csv`, lines);
    return setStatus(`Exported ${ticker} CSV`);
  }
  const chunks = [];
  let header = null;
  for (const t of tickers) {
    const data = state.trident[t];
    const lines = matrixCsvLines(data, { includeTicker: true });
    if (!lines) continue;
    if (!header) {
      header = lines[0];
      chunks.push(header);
    }
    chunks.push(...lines.slice(1));
  }
  if (chunks.length < 2) return setStatus("Trident matrices not loaded");
  downloadCsv(`cipher-trident-SPY-QQQ-IWM-${state.matrixMode}-${state.metric}.csv`, chunks);
  setStatus("Exported Trident CSV (SPY ? QQQ ? IWM)");
}


const VIEW_TITLES = {
  matrix: "Cipher — Strike Matrix",
  night: "Cipher — Night Vision",
  spyglass: "Cipher — Spyglass",
  watchlists: "Cipher — Watchlists",
  journal: "Cipher — Journal",
  trident: "Cipher — Trident",
  saves: "Cipher — Chart Saves",
  scanner: "Cipher — Setup Scanner",
  rankingLab: "Cipher — Ranking Lab",
  gptAnalyst: "Cipher — GPT Analyst",
  bio: "Cipher — Bio",
  contractSearch: "Cipher — Contract Search",
  woAdmin: "Cipher — WO Admin",
  mispricingAdmin: "Cipher — Mispricing Admin",
  stormAdmin: "Cipher — Storm Admin",
  settings: "Cipher — Settings",
};

function updateHeaderTitle() {
  document.title = VIEW_TITLES[state.view] || "Cipher";
  const brand = $("#brandTitle");
  if (brand) {
    const short = {
      matrix: "CIPHER STRIKE MATRIX",
      night: "CIPHER NIGHT VISION",
      spyglass: "CIPHER SPYGLASS",
      scanner: "CIPHER SETUP SCANNER",
      rankingLab: "CIPHER RANKING LAB",
      trident: "CIPHER TRIDENT",
      saves: "CIPHER CHART SAVES",
      gptAnalyst: "CIPHER GPT ANALYST",
      bio: "CIPHER BIO",
      contractSearch: "CIPHER CONTRACT SEARCH",
      woAdmin: "CIPHER WO ADMIN",
      mispricingAdmin: "CIPHER MISPRICING ADMIN",
      stormAdmin: "CIPHER STORM ADMIN",
      watchlists: "CIPHER MY WATCHLISTS",
      journal: "CIPHER TRADING JOURNAL",
      settings: "CIPHER SETTINGS",
    };
    brand.textContent = short[state.view] || "CIPHER";
  }
}

function syncTickerHash(ticker = state.ticker) {
  const next = `#${String(ticker || "SPY").toUpperCase()}`;
  if (location.hash.toUpperCase() !== next) {
    history.replaceState(null, "", next);
  }
}

function tickerFromHash() {
  const raw = (location.hash || "").replace(/^#/, "").trim().toUpperCase();
  return /^[A-Z.]{1,8}$/.test(raw) ? raw : null;
}

/** Recompute Night Vision levels/xray for the selected expiration depth. */
function nightPayloadForRender(data) {
  if (!data?.rows?.length) return data;
  const allExps = data.expirations || [];
  const want = Math.max(1, Number(state.nightExp) || 1);
  let exps = allExps.slice(0, want);
  if (!exps.length) return data;
  if (exps.length === allExps.length && data.levels?.length && data.xray?.length) return data;

  const build = (selected) => {
    const levels = [];
    const xray = [];
    for (const row of data.rows) {
      const cells = (row.cells || []).filter((c) => selected.includes(c.expiration));
      if (!cells.some((c) => c.available || c.net_gex || c.net_vex)) continue;
      const net_gex = cells.reduce((s, c) => s + (c.net_gex || 0), 0);
      const net_vex = cells.reduce((s, c) => s + (c.net_vex || 0), 0);
      if (!net_gex && !net_vex) continue;
      levels.push({ price: row.strike, net_gex, abs_gex: Math.abs(net_gex), net_vex, abs_vex: Math.abs(net_vex) });
      xray.push({ strike: row.strike, net_gex, net_vex, abs_gex: Math.abs(net_gex), abs_vex: Math.abs(net_vex) });
    }
    return { levels, xray };
  };
  let { levels, xray } = build(exps);
  if (!levels.length && exps.length < allExps.length) {
    exps = allExps.slice(0, Math.min(allExps.length, Math.max(want, 5)));
    ({ levels, xray } = build(exps));
  }
  levels.sort((a, b) => b.abs_gex - a.abs_gex);
  const top = levels.slice(0, 12);
  const peak = top[0] || null;
  const spot = data.quote?.price_context || 0;
  const visible = [...top].sort((a, b) => b.price - a.price);
  for (const level of visible) {
    if (peak && level.price === peak.price) level.kind = "global";
    else if (level.price >= spot) level.kind = "above_spot";
    else level.kind = "below_spot";
  }
  const xraySorted = [...xray].sort((a, b) => b.abs_gex - a.abs_gex).slice(0, 40);
  for (const row of xraySorted) {
    if (peak && row.strike === peak.price) row.kind = "global";
    else if (row.strike >= spot) row.kind = "above_spot";
    else row.kind = "below_spot";
  }
  xraySorted.sort((a, b) => b.strike - a.strike);
  return { ...data, levels: visible, peak, xray: xraySorted, expirations: exps };
}

function volumeProfile(bars, bins = 24) {
  if (!bars?.length) return null;
  const lows = bars.map((b) => b.low).filter(Number.isFinite);
  const highs = bars.map((b) => b.high).filter(Number.isFinite);
  if (!lows.length) return null;
  const lo = Math.min(...lows);
  const hi = Math.max(...highs);
  const step = Math.max((hi - lo) / bins, 0.01);
  const hist = Array.from({ length: bins }, (_, i) => ({
    price: lo + (i + 0.5) * step,
    volume: 0,
  }));
  bars.forEach((bar) => {
    if (!Number.isFinite(bar.volume)) return;
    const mid = (bar.high + bar.low) / 2;
    const idx = Math.min(bins - 1, Math.max(0, Math.floor((mid - lo) / step)));
    hist[idx].volume += bar.volume;
  });
  const maxVol = Math.max(...hist.map((h) => h.volume), 1);
  const poc = hist.reduce((a, b) => (b.volume > a.volume ? b : a));
  return { hist, maxVol, poc };
}

function drawNightPane(canvas, noteEl, titleEl, data, bars, label) {
  if (!canvas || !data) return;
  const chart = canvas.parentElement;
  const dpr = window.devicePixelRatio || 1;
  const width = chart.clientWidth || 800;
  const height = chart.clientHeight || 480;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const levels = data.levels || [];
  const metricKey = state.xrayMetric === "vex" ? "net_vex" : "net_gex";
  const prices = [
    ...bars.flatMap((bar) => [bar.high, bar.low, bar.open, bar.close]),
    ...levels.map((level) => level.price),
    data.quote?.price_context,
    ...(state.nightGhost ? (data.ghost || []).map((g) => g.price) : []),
  ].filter(Number.isFinite);
  if (!prices.length) {
    if (noteEl) noteEl.textContent = "Waiting for price bars / exposure levels";
    return;
  }
  const padL = state.nightVP ? 52 : 8;
  const padR = 56;
  const padT = 28;
  const padB = 28;
  const high = Math.max(...prices);
  const low = Math.min(...prices);
  const range = Math.max(high - low, 0.01);
  const y = (price) => padT + ((high - price) / range) * (height - padT - padB);
  const plotW = width - padL - padR;

  ctx.strokeStyle = "rgba(29,36,48,0.55)";
  ctx.lineWidth = 1;
  for (let i = 0; i < 6; i++) {
    const yy = padT + ((height - padT - padB) * i) / 5;
    ctx.beginPath();
    ctx.moveTo(padL, yy);
    ctx.lineTo(width - padR, yy);
    ctx.stroke();
  }

  if (state.nightVP) {
    const vp = volumeProfile(bars, 28);
    if (vp) {
      vp.hist.forEach((bin) => {
        const w = (bin.volume / vp.maxVol) * (padL - 4);
        const yy = y(bin.price);
        const isPoc = bin === vp.poc;
        ctx.fillStyle = isPoc ? "rgba(228,174,36,0.55)" : "rgba(146,0,213,0.28)";
        ctx.fillRect(padL - w - 2, yy - 3, w, 6);
      });
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = "rgba(228,174,36,0.7)";
      ctx.beginPath();
      ctx.moveTo(padL, y(vp.poc.price));
      ctx.lineTo(width - padR, y(vp.poc.price));
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  const visibleBars = bars.slice(-120);
  const gap = plotW / Math.max(visibleBars.length, 1);
  const bodyW = Math.max(2, Math.min(7, gap * 0.62));
  visibleBars.forEach((bar, index) => {
    if (![bar.open, bar.high, bar.low, bar.close].every(Number.isFinite)) return;
    const x = padL + index * gap + gap / 2;
    const up = bar.close >= bar.open;
    ctx.strokeStyle = up ? "#ae14df" : "#da1021";
    ctx.fillStyle = up ? "#ae14df" : "#da1021";
    ctx.beginPath();
    ctx.moveTo(x, y(bar.high));
    ctx.lineTo(x, y(bar.low));
    ctx.stroke();
    const top = y(Math.max(bar.open, bar.close));
    const bottom = y(Math.min(bar.open, bar.close));
    ctx.fillRect(x - bodyW / 2, top, bodyW, Math.max(1.5, bottom - top));
  });

  const colors = { global: "#e4ae24", above_spot: "#b000db", below_spot: "#d70a1b" };
  levels.forEach((level) => {
    const yy = y(level.price);
    const color = colors[level.kind] || "#889";
    ctx.strokeStyle = color;
    ctx.lineWidth = state.nightXray ? (level.kind === "global" ? 2.4 : 1.8) : level.kind === "global" ? 2 : 1.4;
    ctx.globalAlpha = state.nightXray ? 0.95 : 0.85;
    ctx.beginPath();
    ctx.moveTo(padL, yy);
    ctx.lineTo(width - padR, yy);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillStyle = color;
    ctx.fillRect(width - padR - 44, yy - 8, 42, 15);
    ctx.fillStyle = level.kind === "global" ? "#161208" : "#fff";
    ctx.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.textAlign = "center";
    ctx.fillText(Number(level.price).toFixed(2), width - padR - 23, yy + 3);
  });

  if (state.nightGhost && data.ghost?.length) {
    const ghost = data.ghost;
    const gGap = plotW / Math.max(ghost.length - 1, 1);
    ctx.strokeStyle = "rgba(180,200,220,0.55)";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ghost.forEach((pt, i) => {
      const x = padL + i * gGap;
      const yy = y(pt.price);
      if (i === 0) ctx.moveTo(x, yy);
      else ctx.lineTo(x, yy);
    });
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(180,200,220,0.35)";
    ghost.forEach((pt, i) => {
      ctx.beginPath();
      ctx.arc(padL + i * gGap, y(pt.price), 2.2, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  ctx.fillStyle = "#697586";
  ctx.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace";
  ctx.textAlign = "right";
  [high, (high + low) / 2, low].forEach((price) => {
    ctx.fillText(Number(price).toFixed(2), width - 8, y(price) + 3);
  });

  const spot = data.quote?.price_context;
  if (Number.isFinite(spot)) {
    const yy = y(spot);
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = "#e9dfc9";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, yy);
    ctx.lineTo(width - padR, yy);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  const expLabel = { 1: "1 Exp", 5: "Compact", 12: "Full", 36: "Leap" }[state.nightExp] || `${state.nightExp} exp`;
  if (titleEl) {
    titleEl.textContent = `${label || data.ticker || ""} ? ${state.timeframe} ? ${expLabel}${state.nightGhost ? " ? Ghost" : ""}`;
  }
  if (noteEl) {
    const bits = [
      `${(data.feed || "opra").toUpperCase()} + OI`,
      `${levels.length} levels`,
      state.timeframe,
      `${visibleBars.length} bars`,
    ];
    if (state.nightVP) bits.push("VP");
    if (state.nightXray) bits.push(`X-Ray ${metricKey.toUpperCase()}`);
    if (state.nightGhost) bits.push("Ghost");
    noteEl.textContent = levels.length ? bits.join(" ? ") : "No calculated levels available";
  }
}

function renderXrayPanel() {
  const panel = $("#xrayPanel");
  if (!panel) return;
  panel.classList.toggle("hidden", !state.nightXray);
  if (!state.nightXray) return;
  const data = nightRenderablePayload();
  const rows = data?.xray || [];
  const field = state.xrayMetric === "vex" ? "net_vex" : "net_gex";
  const head = $("#xrayMetricHead");
  if (head) head.textContent = state.xrayMetric.toUpperCase();
  $$("[data-xray-metric]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.xrayMetric === state.xrayMetric);
  });
  const body = $("#xrayBody");
  if (!body) return;
  body.innerHTML =
    rows
      .map((row) => {
        const kind = row.kind === "global" ? "global" : row.kind === "above_spot" ? "above" : "below";
        return `<tr class="${kind}"><td>${Number(row.strike).toFixed(Number.isInteger(row.strike) ? 0 : 1)}</td><td>${fmt(row[field])}</td></tr>`;
      })
      .join("") || `<tr><td colspan="2">No X-Ray rows</td></tr>`;
}

function renderTapePanel() {
  const panel = $("#tapePanel");
  if (!panel) return;
  panel.classList.toggle("hidden", !state.nightTape);
  if (!state.nightTape) return;
  $$("[data-tape-min]").forEach((btn) => {
    btn.classList.toggle("active", Number(btn.dataset.tapeMin) === state.tapeMin);
  });
  const sqBtn = document.querySelector('[data-action="toggle-sq"]');
  if (sqBtn) sqBtn.classList.toggle("active", state.tapeSQ);
  let prints = state.flow?.prints || [];
  prints = prints.filter((p) => (p.premium || 0) >= state.tapeMin);
  if (state.tapeSQ) {
    prints = prints.filter((p) => ["SPY", "QQQ"].includes((p.ticker || "").toUpperCase()) || p.ticker === state.ticker);
  }
  const list = $("#tapeList");
  if (!list) return;
  list.innerHTML =
    prints
      .slice(0, 60)
      .map((row) => {
        const side = row.side === "buy" ? "up" : row.side === "sell" ? "down" : "";
        return `<div class="tape-row">
          <span>${escapeHtml(row.ticker)}</span>
          <span>${formatEt(row.time)}</span>
          <span class="prem ${side}">${fmt(row.premium)}</span>
          <span class="meta">${row.size}Ã— @ ${Number(row.price).toFixed(2)} ? ${dollars(row.strike, 1)} ${row.type === "call" ? "C" : "P"} ? ${escapeHtml(row.tier || "")}</span>
        </div>`;
      })
      .join("") || `<div class="tape-row"><span class="meta">No prints ? $${(state.tapeMin / 1000).toFixed(0)}k</span></div>`;
}

function nightRenderablePayload(primary = state.night, fallback = state.matrix) {
  const prepared = nightPayloadForRender(primary);
  if (prepared?.levels?.length) return prepared;
  const backup = nightPayloadForRender(fallback);
  return backup?.levels?.length ? backup : prepared;
}

async function loadSplitData() {
  await Promise.all(
    ["SPY", "QQQ"].map(async (ticker) => {
      try {
        const [night, barData] = await Promise.all([
          fetchJson(
            `${api}/api/night-vision?ticker=${ticker}&feed=opra&depth=${state.windowPct}&expirations=${state.nightExp}&fresh=1`,
          ),
          fetchJson(`${api}/api/bars?ticker=${ticker}&timeframe=${state.timeframe}&limit=200`),
        ]);
        state.splitCache[ticker] = { night, bars: barData };
      } catch {
        /* ignore */
      }
    }),
  );
}

function renderNight() {
  const charts = $("#nightCharts");
  const chartB = $("#chartB");
  charts?.classList.toggle("split", state.nightSplit);
  chartB?.classList.toggle("hidden", !state.nightSplit);
  $("#chart")?.classList.toggle("expanded", state.nightExpand);

  $$('[data-action="toggle-split"]').forEach((b) => {
    b.classList.toggle("active", state.nightSplit);
    b.setAttribute("aria-pressed", state.nightSplit ? "true" : "false");
  });
  $$('[data-action="toggle-tape"]').forEach((b) => {
    b.classList.toggle("active", state.nightTape);
    b.setAttribute("aria-pressed", state.nightTape ? "true" : "false");
  });
  $$('[data-action="toggle-vp"]').forEach((b) => {
    b.classList.toggle("active", state.nightVP);
    b.setAttribute("aria-pressed", state.nightVP ? "true" : "false");
  });
  $$('[data-action="toggle-xray"]').forEach((b) => {
    b.classList.toggle("active", state.nightXray);
    b.setAttribute("aria-pressed", state.nightXray ? "true" : "false");
  });
  $$('[data-action="toggle-ghost"]').forEach((b) => {
    b.classList.toggle("active", state.nightGhost);
    b.setAttribute("aria-pressed", state.nightGhost ? "true" : "false");
  });
  $$("[data-night-exp]").forEach((b) => {
    const on = Number(b.dataset.nightExp) === Number(state.nightExp);
    b.classList.toggle("active", on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  });
  $$("[data-frame]").forEach((b) => {
    const on = b.dataset.frame === state.timeframe;
    b.classList.toggle("active", on);
  });

  if (state.nightSplit) {
    const spy = state.splitCache.SPY || { night: state.night, bars: state.bars };
    const qqq = state.splitCache.QQQ || {};
    drawNightPane(
      $("#nvCanvas"),
      $("#chartNote"),
      $("#paneTitleA"),
      nightRenderablePayload(spy.night, state.matrix),
      spy.bars?.bars || [],
      "SPY",
    );
    drawNightPane(
      $("#nvCanvasB"),
      $("#chartNoteB"),
      $("#paneTitleB"),
      nightRenderablePayload(qqq.night, state.splitCache.QQQ?.matrix),
      qqq.bars?.bars || [],
      "QQQ",
    );
  } else {
    drawNightPane(
      $("#nvCanvas"),
      $("#chartNote"),
      $("#paneTitleA"),
      nightRenderablePayload(),
      state.bars?.bars || [],
      state.ticker,
    );
  }
  renderXrayPanel();
  renderTapePanel();
  updateHeaderTitle();
}

function captureChart() {
  const canvas = $("#nvCanvas");
  if (!canvas) return;
  const link = document.createElement("a");
  link.download = `cipher-night-${state.ticker}-${state.timeframe}-${Date.now()}.png`;
  link.href = canvas.toDataURL("image/png");
  link.click();
  setLive(true, "Captured");
}

function renderQuote(q) {
  if (!q) return;
  const spot = q.price_context;
  const ticker = escapeHtml(q.ticker || state.ticker);
  // Access Obsidian header pattern: VIEW / TICKER $price +chg%
  $("#quote").innerHTML = `<strong>${ticker}</strong> ${dollars(spot)}`;
  const chgEl = $("#dayChg");
  if (chgEl && Number.isFinite(q.day_change_pct)) {
    chgEl.textContent = `${q.day_change_pct >= 0 ? "+" : ""}${q.day_change_pct.toFixed(2)}%`;
    chgEl.className = `chg ${q.day_change_pct >= 0 ? "up" : "down"}`;
  }
}

async function fetchJson(url, options = {}) {
  const attempts = options.retries ?? 2;
  let lastError = null;
  for (let attempt = 0; attempt <= attempts; attempt++) {
    try {
      const response = await fetch(url, options);
      const data = await response.json().catch(() => ({}));
      if (response.ok) return data;
      lastError = new Error(data.error || `HTTP ${response.status}`);
      if (![502, 503, 504].includes(response.status) || attempt === attempts) throw lastError;
    } catch (error) {
      lastError = error;
      if (attempt === attempts) throw error;
    }
    await new Promise((resolve) => setTimeout(resolve, 700 * (attempt + 1)));
  }
  throw lastError || new Error("Request failed");
}

function queryBase() {
  return `ticker=${encodeURIComponent(state.ticker)}&feed=opra&depth=${state.windowPct}`;
}

async function load(ticker = $("#ticker").value.trim().toUpperCase(), { quiet = false } = {}) {
  if (!/^[A-Z.]{1,8}$/.test(ticker)) return;
  state.ticker = ticker;
  $("#ticker").value = ticker;
  syncTickerHash(ticker);
  if (!quiet) setStatus("Loading read-only market data…");
  const base = queryBase();
  try {
    const matrixData = await fetchJson(
      `${api}/api/matrix?${base}&expirations=${Math.max(1, Number(state.matrixExp) || 12)}`,
    );
    state.matrix = matrixData;
    renderQuote(matrixData.quote);
    updateHeaderTitle();
    setLive(true, "Live");
    renderMatrix();
  } catch (error) {
    setLive(false, "Data error");
    setStatus(error.message || "Data unavailable");
    $("#quote").textContent = `${ticker} unavailable`;
    const note = $("#chartNote");
    if (note) note.textContent = error.message || "Unable to load local research data";
    return;
  }

  const [nightResult, barsResult, flowResult] = await Promise.allSettled([
    fetchJson(`${api}/api/night-vision?${base}&expirations=${state.nightExp}`),
    fetchJson(`${api}/api/bars?ticker=${encodeURIComponent(ticker)}&timeframe=${state.timeframe}&limit=200`),
    fetchJson(
      `${api}/api/flow?ticker=${encodeURIComponent(ticker)}&feed=opra&min=${state.spyglassFilters.premium}`,
    ),
  ]);
  if (nightResult.status === "fulfilled") state.night = nightResult.value;
  if (barsResult.status === "fulfilled") state.bars = barsResult.value;
  if (flowResult.status === "fulfilled") state.flow = flowResult.value;
  renderNight();
  if (!["matrix", "night"].includes(state.view)) renderResearch();

  if (state.nightSplit) {
    loadSplitData()
      .then(() => {
        if (state.view === "night") renderNight();
      })
      .catch(() => {});
  }
}

function stopStream() {
  if (state.stream) {
    state.stream.close();
    state.stream = null;
  }
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function startLive() {
  stopStream();
  if (!state.auto) return;
  const interval = Math.max(10, Number(state.refreshSeconds) || 15);
  const url =
    `${api}/api/stream?ticker=${encodeURIComponent(state.ticker)}` +
    `&feed=opra&depth=${state.windowPct}&expirations=10&min=${state.spyglassFilters.premium}&interval=${interval}`;
  try {
    const es = new EventSource(url);
    state.stream = es;
    es.addEventListener("hello", () => setLive(true, "Streaming"));
    es.addEventListener("quote", (event) => {
      try {
        const q = JSON.parse(event.data);
        if (state.matrix) state.matrix.quote = { ...state.matrix.quote, ...q };
        if (state.night) state.night.quote = { ...state.night.quote, ...q };
        renderQuote(q);
        renderLevelsBar();
      } catch { /* ignore */ }
    });
    es.addEventListener("matrix", (event) => {
      try {
        const payload = JSON.parse(event.data);
        state.matrix = payload;
        state.night = {
          ...payload,
          levels: payload.levels || state.night?.levels,
          xray: payload.xray || state.night?.xray,
          ghost: payload.ghost || state.night?.ghost,
          peak: payload.peak || state.night?.peak,
        };
        renderMatrix();
        if (state.view === "night") renderNight();
        setLive(true, "Live");
      } catch { /* ignore */ }
    });
    es.addEventListener("flow", (event) => {
      try {
        const payload = JSON.parse(event.data);
        state.flow = {
          ...(state.flow || {}),
          as_of: payload.as_of,
          count: payload.count,
          prints: payload.prints,
        };
        if (state.view === "spyglass") renderSpyglass();
        if (state.view === "night" && state.nightTape) renderTapePanel();
      } catch { /* ignore */ }
    });
    es.addEventListener("error", () => {
      /* EventSource also fires on network drop; fall back to polling */
    });
    es.onerror = () => {
      setLive(false, "Reconnecting?");
      es.close();
      state.stream = null;
      if (state.auto && !state.pollTimer) {
        state.pollTimer = setInterval(() => load(state.ticker, { quiet: true }), interval * 1000);
      }
    };
  } catch {
    state.pollTimer = setInterval(() => load(state.ticker, { quiet: true }), interval * 1000);
  }
  // Also refresh bars on the same cadence (SSE does not push bars).
  state.pollTimer = setInterval(async () => {
    if (!state.auto) return;
    try {
      state.bars = await fetchJson(
        `${api}/api/bars?ticker=${encodeURIComponent(state.ticker)}&timeframe=${state.timeframe}&limit=200`,
      );
      if (state.view === "night") renderNight();
    } catch { /* ignore */ }
  }, interval * 1000);
}

function setAuto(enabled) {
  state.auto = enabled;
  $("#auto").checked = enabled;
  $("#nightAuto").checked = enabled;
  if (enabled) {
    startLive();
  } else {
    stopStream();
    setLive(false, "Paused");
  }
}

function switchView(view) {
  state.view = view;
  updateHeaderTitle();
  syncMatrixToolbar();
  $$(".nav[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $("#matrixView").classList.toggle("active", view === "matrix");
  $("#nightView").classList.toggle("active", view === "night");
  $("#tridentView")?.classList.toggle("active", view === "trident");
  $("#researchView").classList.toggle("active", !["matrix", "night", "trident"].includes(view));
  if (view === "night") {
    if (state.nightSplit && !state.splitCache.QQQ) {
      loadSplitData().then(() => requestAnimationFrame(renderNight));
    } else {
      requestAnimationFrame(renderNight);
    }
  }
  if (view === "trident") {
    syncTridentToolbar();
    if (!Object.keys(state.trident).length) loadTrident();
    else renderTrident();
  }
  if (!["matrix", "night", "trident"].includes(view)) renderResearch();
  if (view === "settings") refreshWeightLab();
}

function syncMatrixToolbar() {
  $$("#matrixView [data-window], #tridentToolbar [data-window]").forEach((item) => {
    const on = Number(item.dataset.window) === Number(state.windowPct);
    item.classList.toggle("active", on);
    item.setAttribute("aria-pressed", on ? "true" : "false");
  });
  $$("#matrixView [data-matrix-exp], #tridentToolbar [data-matrix-exp]").forEach((item) => {
    const on = Number(item.dataset.matrixExp) === Number(state.matrixExp);
    item.classList.toggle("active", on);
    item.setAttribute("aria-pressed", on ? "true" : "false");
  });
  $$("#matrixView [data-metric], #tridentToolbar [data-metric]").forEach((item) => {
    const on = item.dataset.metric === state.metric;
    item.classList.toggle("active", on);
    item.setAttribute("aria-pressed", on ? "true" : "false");
  });
  $$("#matrixView [data-matrix-mode], #tridentToolbar [data-matrix-mode]").forEach((item) => {
    const on = item.dataset.matrixMode === state.matrixMode;
    item.classList.toggle("active", on);
    item.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

function syncTridentToolbar() {
  syncMatrixToolbar();
  const root = $("#tridentToolbar");
  if (!root) return;
  root.querySelectorAll("[data-metric]").forEach((item) => {
    const on = item.dataset.metric === state.metric;
    item.classList.toggle("active", on);
    item.setAttribute("aria-pressed", on ? "true" : "false");
  });
  root.querySelectorAll("[data-matrix-mode]").forEach((item) => {
    const on = item.dataset.matrixMode === state.matrixMode;
    item.classList.toggle("active", on);
    item.setAttribute("aria-pressed", on ? "true" : "false");
  });
  root.querySelectorAll("[data-matrix-exp]").forEach((item) => {
    item.classList.toggle("active", Number(item.dataset.matrixExp) === Number(state.matrixExp));
  });
  root.querySelectorAll("[data-window]").forEach((item) => {
    item.classList.toggle("active", Number(item.dataset.window) === state.windowPct);
  });
  root.querySelectorAll("[data-action='toggle-fc']").forEach((item) => {
    item.classList.toggle("active", state.showFC);
    item.setAttribute("aria-pressed", state.showFC ? "true" : "false");
  });
  const full = state.tridentDensity === "full";
  root.querySelectorAll("[data-density]").forEach((item) => {
    const on = item.dataset.density === (full ? "full" : "compact");
    item.classList.toggle("active", on);
  });
  $$(".trident-wrap").forEach((wrap) => wrap.classList.toggle("full-density", full));
}

function renderTridentLevels(ticker, data, derived) {
  const bar = $(`#tridentLevels${ticker}`);
  if (!bar || !data) return;
  const summary = data.summary || {};
  const spot = data.quote?.price_context;
  const items = [
    ["Spot", dollars(spot)],
    ["Top pull", dollars(derived?.topPullStrike ?? summary.global_max_strike, 1)],
    ["Floor", dollars(derived?.nearestFloor ?? summary.put_wall_strike, 1)],
    ["Ceiling", dollars(derived?.nearestCeiling ?? summary.call_wall_strike, 1)],
  ];
  bar.innerHTML = items
    .map(
      ([label, value]) =>
        `<div class="level-chip"><span>${label}</span><strong>${escapeHtml(String(value))}</strong></div>`,
    )
    .join("");
}

function renderTrident() {
  const tickers = ["SPY", "QQQ", "IWM"];
  syncTridentToolbar();
  let ready = 0;
  for (const ticker of tickers) {
    const data = state.trident[ticker];
    const error = state.tridentErrors?.[ticker];
    const mount = $(`#tridentMatrix${ticker}`);
    const spotEl = $(`#tridentSpot${ticker}`);
    if (error && !data) {
      if (spotEl) spotEl.textContent = "Error";
      if (mount) mount.innerHTML = `<div class="trident-empty">${escapeHtml(ticker)} matrix unavailable: ${escapeHtml(error)}</div>`;
      continue;
    }
    if (!data || !mount) {
      if (spotEl) spotEl.textContent = "Loading…";
      if (mount) mount.innerHTML = `<div class="cell head">Loading ${ticker}…</div>`;
      continue;
    }
    ready += 1;
    const painted = paintMatrixGrid(mount, data);
    const spot = data.quote?.price_context;
    if (spotEl) spotEl.textContent = dollars(spot);
    renderTridentLevels(ticker, data, painted?.derived);
    requestAnimationFrame(() => {
      const wrap = $(`#tridentWrap${ticker}`);
      const band = (data.rows || []).find((r) => r.is_spot_band);
      scrollWrapToStrike(wrap, band?.strike ?? spot);
    });
  }
  const cov = $("#tridentCoverage");
  if (cov) {
    cov.textContent = `SPY · QQQ · IWM · ${ready}/3 loaded · ${state.metric.toUpperCase()}`;
  }
  const prov = $("#tridentProvenance");
  if (prov) {
    prov.textContent = "";
    prov.title = "Public-OI heuristic — not verified dealer positioning";
  }
  const upd = $("#tridentUpdated");
  if (upd) upd.textContent = `updated ${new Date().toLocaleTimeString()}`;
  updateHeaderTitle();
}

async function loadTrident() {
  renderTrident();
  const depth = state.windowPct === 0.25 ? 0.35 : state.windowPct || 0.06;
  const expN = Math.max(1, Number(state.matrixExp) || 12);
  for (const ticker of ["SPY", "QQQ", "IWM"]) {
      const mount = $(`#tridentMatrix${ticker}`);
      const spotEl = $(`#tridentSpot${ticker}`);
      try {
        const data = await fetchJson(
          `${api}/api/matrix?ticker=${ticker}&feed=opra&depth=${depth}&expirations=${expN}`,
        );
        state.trident[ticker] = data;
        delete state.tridentErrors[ticker];
        if (state.view === "trident") renderTrident();
      } catch (error) {
        delete state.trident[ticker];
        state.tridentErrors[ticker] = error.message || "request failed";
        if (state.view === "trident") renderTrident();
      }
  }
}

function panel(title, subtitle, body, tools = "") {
  return `<div class="research-toolbar"><div><h1>${title}</h1><p>${subtitle}</p></div>${tools}</div><div class="research-body">${body}</div>`;
}

function formatEt(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString("en-US", {
      timeZone: "America/New_York",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return String(iso).slice(11, 19);
  }
}

function renderSpyglass() {
  const filters = state.spyglassFilters;
  let prints = state.flow?.prints || [];
  prints = prints.filter((row) => {
    if (row.premium < filters.premium) return false;
    if (filters.maxPrice !== "all" && row.price > filters.maxPrice) return false;
    if (filters.type === "Calls" && row.type !== "call") return false;
    if (filters.type === "Puts" && row.type !== "put") return false;
    if (filters.side === "Ask" && row.side !== "buy") return false;
    if (filters.side === "Bid" && row.side !== "sell") return false;
    if (filters.money === "OTM") {
      const spot = state.matrix?.quote?.price_context || 0;
      const otm = (row.type === "call" && row.strike > spot) || (row.type === "put" && row.strike < spot);
      if (!otm) return false;
    }
    if (filters.money === "ITM") {
      const spot = state.matrix?.quote?.price_context || 0;
      const otm = (row.type === "call" && row.strike > spot) || (row.type === "put" && row.strike < spot);
      if (otm) return false;
    }
    return true;
  });

  const premiumButtons = [
    [5000, "$5k"],
    [10000, "$10k"],
    [25000, "$25k"],
    [50000, "$50k"],
    [100000, "$100k"],
  ]
    .map(
      ([value, label]) =>
        `<button class="spy-filter ${filters.premium === value ? "active" : ""}" data-spy-premium="${value}">${label}</button>`,
    )
    .join("");
  const priceButtons = [
    [0.5, "?$0.50"],
    [0.25, "?$0.25"],
    [0.1, "?$0.10"],
    ["all", "All px"],
  ]
    .map(
      ([value, label]) =>
        `<button class="spy-filter ${String(filters.maxPrice) === String(value) ? "active" : ""}" data-spy-price="${value}">${label}</button>`,
    )
    .join("");
  const typeButtons = ["All", "Calls", "Puts"]
    .map((value) => `<button class="spy-filter ${filters.type === value ? "active" : ""}" data-spy-type="${value}">${value}</button>`)
    .join("");
  const sideButtons = ["Bid/Ask", "Ask", "Bid"]
    .map((value) => `<button class="spy-filter ${filters.side === value ? "active" : ""}" data-spy-side="${value}">${value}</button>`)
    .join("");
  const moneyButtons = ["All", "OTM", "ITM"]
    .map((value) => `<button class="spy-filter ${filters.money === value ? "active" : ""}" data-spy-money="${value}">${value}</button>`)
    .join("");

  const rowsHtml =
    prints
      .slice(0, 80)
      .map((row) => {
        const sideLabel = row.side === "buy" ? "Ask" : row.side === "sell" ? "Bid" : "—";
        const otm =
          row.otm_pct == null ? "—" : `${row.otm_pct >= 0 ? "+" : ""}${Number(row.otm_pct).toFixed(1)}%`;
        return `<tr>
          <td>${escapeHtml(row.ticker)}</td>
          <td>${formatEt(row.time)}</td>
          <td class="${row.side === "buy" ? "up" : row.side === "sell" ? "down" : ""}">${fmt(row.premium)}</td>
          <td>${row.size}</td>
          <td>${Number(row.price).toFixed(2)}</td>
          <td>${dollars(row.strike, 1)}</td>
          <td>${escapeHtml(row.expiration || "—")}</td>
          <td>${row.type === "call" ? "C" : "P"}</td>
          <td>${sideLabel}</td>
          <td>${otm}</td>
          <td class="tier">${escapeHtml(row.tier || "")}</td>
        </tr>`;
      })
      .join("") || `<tr><td colspan="11">No prints match these filters. Prints come from latest OPRA trades on the chain.</td></tr>`;

  const body = `
    <div class="spyglass-head">
      <div class="trade-date"><span>TRADE DATE</span><strong>${new Date().toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}</strong></div>
      <div class="contract-price">${priceButtons}</div>
    </div>
    <div class="filter-strip">
      <div>${premiumButtons}</div>
      <div>${typeButtons}</div>
      <div>${sideButtons}</div>
      <div>${moneyButtons}</div>
      <button class="spy-filter" data-action="refresh-flow">Refresh tape</button>
    </div>
    <div class="notice">Live tape from Alpaca option latest trades. Side is inferred vs bid/ask (${state.flow?.count ?? 0} matching prints). Not verified dealer flow.</div>
    <div class="data-card spy-table"><table>
      <thead><tr><th>TICKER</th><th>TIME ET</th><th>SIZE (PREM)</th><th>CONTRACTS</th><th>PX</th><th>STRIKE</th><th>EXPIRATION</th><th>C/P</th><th>BID/ASK</th><th>% OTM</th><th>TIER</th></tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table></div>`;
  $("#researchContent").innerHTML = panel("Spyglass", `${state.ticker} ? options print tape`, body);
}

function watchRow(ticker, data) {
  const quote = data?.quote || data || {};
  const price = quote.price_context ?? data?.price_context;
  const pct = Number(quote.day_change_pct);
  const dollarChange =
    Number.isFinite(Number(price)) && Number.isFinite(pct) && pct !== -100
      ? Number(price) - Number(price) / (1 + pct / 100)
      : null;
  const level = data?.summary?.global_max_strike;
  const changeClass = Number.isFinite(pct) && pct >= 0 ? "up" : "down";
  return `<tr>
    <td><button class="ticker-link" data-action="open-ticker" data-ticker="${ticker}">$${escapeHtml(ticker)}</button></td>
    <td class="${changeClass}">${Number.isFinite(pct) ? `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%` : "—"}</td>
    <td class="${changeClass}">${Number.isFinite(dollarChange) ? `${dollarChange >= 0 ? "+" : "âˆ’"}$${Math.abs(dollarChange).toFixed(2)}` : "—"}</td>
    <td>${dollars(price)}</td>
    <td class="gold-text">${dollars(level, 1)}</td>
    <td><button class="mini danger" data-action="remove-watch" data-ticker="${ticker}">Ã—</button></td>
  </tr>`;
}

function renderWatchlists() {
  const rows =
    state.watchlist.map((ticker) => watchRow(ticker, state.watchData[ticker])).join("") ||
    '<tr><td colspan="6">No saved tickers.</td></tr>';
  const body = `<div class="watch-toolbar"><span>MY WATCHLISTS</span><span>${state.watchlist.length} symbols</span><button class="tool" data-action="refresh-watchlist">Refresh prices</button></div>
  <div class="data-card watch-table"><table><thead><tr><th>TICKER</th><th>% CHANGE</th><th>$ CHANGE</th><th>PRICE</th><th>COMPACT</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
  <div class="notice">Watchlists stay in this browser only. Compact is the local dominant exposure level from the loaded public-OI reconstruction.</div>`;
  $("#researchContent").innerHTML = panel(
    "My Watchlists",
    "Your tickers, today's move, and the local compact level at a glance.",
    body,
    '<button class="tool" data-action="watch-current">+ Add current</button>',
  );
}

async function loadWatchlistData() {
  renderWatchlists();
  await Promise.all(
    state.watchlist.map(async (ticker) => {
      try {
        const data = await fetchJson(
          `${api}/api/matrix?ticker=${ticker}&feed=opra&depth=${state.windowPct}&expirations=${state.scannerMode === "leap" ? 36 : 3}`,
        );
        state.watchData[ticker] = data;
      } catch {
        /* ignore */
      }
    }),
  );
  if (state.view === "watchlists") renderWatchlists();
}

function moneyNumber(value) {
  const parsed = Number(String(value || "0").replace(/[^0-9.-]/g, ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

function renderJournal() {
  const now = new Date();
  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);
  const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  const byDay = new Map(state.journal.map((entry) => [entry.date, entry]));
  const blanks = Array.from({ length: monthStart.getDay() }, () => '<div class="journal-day muted"></div>').join("");
  const days = Array.from({ length: daysInMonth }, (_, index) => {
    const day = index + 1;
    const iso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const entry = byDay.get(iso);
    const value = entry ? moneyNumber(entry.amount) * (entry.result === "Loss" ? -1 : 1) : 0;
    const cls = value > 0 ? "up" : value < 0 ? "down" : "";
    return `<div class="journal-day ${cls}"><span>${day}</span>${entry ? `<strong>${value >= 0 ? "+" : "âˆ’"}$${Math.abs(value).toLocaleString()}</strong><button class="mini danger" data-action="remove-journal" data-id="${entry.id}">Ã—</button>` : ""}</div>`;
  }).join("");
  const monthTotal = state.journal.reduce((sum, entry) => sum + moneyNumber(entry.amount) * (entry.result === "Loss" ? -1 : 1), 0);
  const body = `<div class="journal-head"><div><span>MONTH P&L</span><strong class="${monthTotal >= 0 ? "up" : "down"}">${monthTotal >= 0 ? "+" : "âˆ’"}$${Math.abs(monthTotal).toLocaleString()}</strong></div><button class="tool" data-action="capture-chart">Save image</button></div>
  <div class="form-card journal-form"><label>Date<input id="journalDate" type="date" value="${new Date().toISOString().slice(0, 10)}"></label><label>Result<select id="journalResult"><option>Profit</option><option>Loss</option></select></label><label>Amount<input id="journalAmount" placeholder="$0"></label><label class="form-grow">Notes<input id="journalNote" placeholder="What did the setup teach you?"></label><button class="tool pink" data-action="save-journal">Save day</button></div>
  <div class="journal-calendar"><div class="journal-week">SUN</div><div class="journal-week">MON</div><div class="journal-week">TUE</div><div class="journal-week">WED</div><div class="journal-week">THU</div><div class="journal-week">FRI</div><div class="journal-week">SAT</div>${blanks}${days}</div>`;
  $("#researchContent").innerHTML = panel("Journal", `${new Intl.DateTimeFormat("en", { month: "long", year: "numeric" }).format(now)} trading journal`, body);
}

function exposureRowsFrom(data, field = "net_gex") {
  return (data?.rows || []).map((row) => ({
    strike: row.strike,
    value: row.cells.reduce((sum, cell) => sum + (cell[field] || 0), 0),
    gex: row.cells.reduce((sum, cell) => sum + (cell.net_gex || 0), 0),
  }));
}

function renderSaves() {
  const cards =
    state.saves
      .slice()
      .reverse()
      .map(
        (save) => {
          const levels = (save.levels || [])
            .map((level) => `<span><b>${escapeHtml(level.strike)}</b><em>${escapeHtml(level.score)}</em></span>`)
            .join("");
          return `<article class="save-card rich-save-card">
            <button class="mini danger save-delete" data-action="remove-save" data-id="${save.id}">Ã—</button>
            <div class="save-preview ${save.thumbnail ? "has-thumb" : ""}">${save.thumbnail ? `<img src="${escapeHtml(save.thumbnail)}" alt="${escapeHtml(save.ticker)} saved chart preview">` : `<span>${escapeHtml(save.ticker)}</span>`}</div>
            <div class="save-meta"><span>DATE ADDED</span><strong>${escapeHtml(save.date || save.created)}</strong></div>
            <div class="save-meta"><span>TICKER</span><strong>$${escapeHtml(save.ticker)}</strong></div>
            <div class="save-meta"><span>PRICE</span><strong>${escapeHtml(save.price || "—")}</strong></div>
            <div class="save-meta"><span>VIEW</span><strong>${escapeHtml(save.view)}</strong></div>
            <div class="save-levels"><span>TOP LEVELS</span>${levels || `<small>${escapeHtml(save.note || "No level snapshot saved")}</small>`}</div>
          </article>`;
        },
      )
      .join("") || '<div class="empty-panel">No local chart saves yet.</div>';
  $("#researchContent").innerHTML = panel(
    "Chart Saves",
    "Snapshots you've saved from Night Vision, with the top pull at capture time.",
    `<div class="save-grid">${cards}</div>`,
    '<button class="tool" data-action="save-chart">Save current chart</button>',
  );
}

function fmtLevels(levels) {
  if (!Array.isArray(levels) || !levels.length) return "—";
  return levels.map((x) => Number(x).toFixed(Number.isInteger(Number(x)) ? 0 : 1)).join(", ");
}

function renderScanner() {
  const hints = {
    short: "Short term ? nearest/lowest-DTE expiration only; tighter strike window.",
    long: "Long term ? nearest/lowest-DTE expiration only; wider strike window for structure.",
    leap: "LEAP ? nearest/lowest-DTE expiration only; widest strike window (label, not multi-exp fetch).",
  };
  const modes = [
    ["short", "Short term"],
    ["long", "Long term"],
    ["leap", "LEAP"],
  ]
    .map(
      ([id, label]) =>
        `<button class="scan-mode ${state.scannerMode === id ? "active" : ""}" data-scan-mode="${id}">${label}</button>`,
    )
    .join("");

  const scanning = state.scannerBusy;
  const cipherBtn = `<button class="tool pink scan-primary" data-action="run-scan" data-scan-strategy="cipher" ${
    scanning ? "disabled" : ""
  }>${scanning ? "Scanning?" : "Cipher Model Scan"}</button>`;

  const row2 = [
    ["liquidity", "Liq scan"],
    ["cluster", "Cluster scan"],
  ]
    .map(
      ([id, label]) =>
        `<button class="scan-mode ${state.scannerStrategy === id ? "active" : ""}" data-scan-strategy="${id}">${label}</button>`,
    )
    .join("");

  const flash = [
    ["flash", "Flash BETA"],
    ["flash_index", "Flash Index BETA"],
    ["flash_agentic", "Flash Agentic BETA"],
  ]
    .map(
      ([id, label]) =>
        `<button class="scan-mode gold ${state.scannerStrategy === id ? "active" : ""}" data-scan-strategy="${id}">${label}</button>`,
    )
    .join("");

  const meta = state.scannerMeta || {};
  const picks = state.scanner || [];
  const directionFilters = [
    ["all", "All"],
    ["upside", "Upside"],
    ["downside", "Downside"],
  ]
    .map(
      ([id, label]) =>
        `<button class="scan-filter ${state.scannerFilter === id ? "active" : ""}" data-scan-filter="${id}">${label}</button>`,
    )
    .join("");
  const filteredPicks =
    state.scannerFilter === "all"
      ? picks
      : picks.filter((item) => {
          const dir = String(item.direction || "neutral").toLowerCase();
          if (state.scannerFilter === "upside") return ["bullish", "upside", "long", "call"].includes(dir);
          if (state.scannerFilter === "downside") return ["bearish", "downside", "short", "put"].includes(dir);
          return dir === state.scannerFilter;
        });
  const clusters = state.scannerClusters || [];
  const progress = state.scannerProgress || {};
  const pct = progress.pct ?? (scanning ? 0 : picks.length ? 100 : 0);
  const progressMsg =
    progress.message ||
    (scanning
      ? `Scanning the universe? ${progress.done || 0}/${progress.total || meta.universe_size || "—"} tickers (${pct}%)`
      : "");

  const clusterCards =
    state.scannerStrategy === "cluster" && clusters.length
      ? clusters
          .map((cluster) => {
            const members = cluster.members || [];
            const body = members
              .map((m) => {
                const band =
                  m.low != null && m.high != null && m.low !== m.high
                    ? `${Number(m.low).toFixed(1)}–${Number(m.high).toFixed(1)}`
                    : dollars(m.center ?? m.low, 1);
                return `<button class="cluster-member" data-action="open-ticker" data-ticker="${m.ticker}">
                  <strong>${escapeHtml(m.ticker)}</strong>
                  <span>${escapeHtml(band)}</span>
                  <em>${Number(m.score || 0).toFixed(0)}</em>
                </button>`;
              })
              .join("");
            return `<article class="cluster-card kind-${escapeHtml(cluster.kind || "")}">
              <header>
                <div>
                  <strong>${escapeHtml(cluster.title)}</strong>
                  <p>${escapeHtml(cluster.blurb || "")}</p>
                </div>
                <span>${cluster.count}</span>
              </header>
              <div class="cluster-members">${body}</div>
            </article>`;
          })
          .join("")
      : "";

  const cards =
    filteredPicks
      .map((item, index) => {
        const dir = (item.direction || "").toUpperCase();
        const dirClass = dir === "BULLISH" ? "bull" : dir === "BEARISH" ? "bear" : "";
        const agent = item.agent_state
          ? `<span class="agent-state ${escapeHtml(item.agent_state)}">${escapeHtml(item.agent_state)}</span>`
          : "";
        const vacLabel = ["flash", "flash_index", "flash_agentic"].includes(state.scannerStrategy)
          ? "ATR targets"
          : "Vacuum targets";
        const completed = item.agent_state === "completed" ? " completed-play" : "";
        const rewardRisk = Number.isFinite(Number(item.reward_risk))
          ? `R:R ${Number(item.reward_risk).toFixed(2)}`
          : "R:R —";
        const actionability = item.actionable === true ? "actionable" : "research-only";
        const primarySetup = item.cluster?.label || item.setups?.[0]?.label || item.setup_type || item.setup_kind || "Cipher setup";
        const setupRank = item.setups?.[0]?.peak_count ? `#${item.setups[0].peak_count}` : `#${item.rank || index + 1}`;
        const runway =
          item.flash?.components?.atr != null
            ? `${Math.round(item.flash.components.atr * 100)}%`
            : `${Math.max(0, Math.min(100, Math.round((item.vacuum_count || 0) * 18 + (item.peak_count || 0) * 3)))}%`;
        const targetTags = (item.setups || [])
          .slice(0, 8)
          .map(
            (setup) =>
              `<span class="target-tag ${escapeHtml(setup.kind || "")}">${escapeHtml(
                (setup.strikes || []).join(" ? ") || setup.label || "",
              )}</span>`,
          )
          .join("");
        return `<article class="cipher-card ${dirClass}${completed}">
          <header>
            <div class="cipher-rank">#${item.rank || index + 1}</div>
            <button class="ticker-link cipher-ticker" data-action="open-ticker" data-ticker="${item.ticker}">$${escapeHtml(item.ticker)}</button>
            <span class="dir-pill ${dirClass}">${escapeHtml(dir || "—")}</span>
            ${agent}
            <span class="score-pill big">${Number(item.score || 0).toFixed(0)}/100</span>
            <span class="cipher-spot">${dollars(item.spot)}</span>
          </header>
          <div class="setup-strip">
            <span class="setup-name">${escapeHtml(primarySetup)}</span>
            <span>${escapeHtml(setupRank)}</span>
            <span>${escapeHtml(item.mode_label || state.scannerMode)}</span>
            <span>${escapeHtml(`${rewardRisk} · ${actionability}`)}</span>
          </div>
          <div class="cipher-levels">
            <div><span>Major supports</span><strong>${escapeHtml(fmtLevels(item.supports))}</strong></div>
            <div><span>Major resistances</span><strong>${escapeHtml(fmtLevels(item.resistances))}</strong></div>
            <div><span>Pull target</span><strong class="gold-text">${dollars(item.pull_target, 1)}</strong></div>
            <div><span>${vacLabel}</span><strong>${escapeHtml(fmtLevels(item.vacuum_targets))}</strong></div>
            <div><span>Pivot</span><strong>${dollars(item.first_resistance ?? item.first_support, 1)}</strong></div>
            <div><span>Stretch</span><strong>${dollars(item.flash?.stretch ?? item.pull_target, 1)}</strong></div>
            <div><span>Invalidation</span><strong>${dollars(item.invalidation ?? item.close_under ?? item.reclaim, 1)}</strong></div>
            <div><span>Runway clarity</span><strong>${runway}</strong></div>
          </div>
          <p class="cipher-read"><span class="cipher-read-label">Cipher read</span> ${escapeHtml(item.read || item.reason || "")}</p>
          <div class="target-tags">${targetTags}</div>
        </article>`;
      })
      .join("") ||
    (scanning
      ? ""
      : picks.length
        ? '<div class="empty-panel">No setups match the active direction filter.</div>'
        : '<div class="empty-panel">Pick a timeframe and run a scan to surface the top 30 setups across the universe.</div>');

  const flashHints = {
    flash: "Flash BETA ? intraday model on the 12 most-liquid names (nearest-exp GEX/VEX floors/ceilings + session/VP/momentum).",
    flash_index: "Flash Index BETA ? same Flash model restricted to SPY, QQQ, and IWM. Live pulse ? not logged.",
    flash_agentic: "Flash Agentic BETA ? surfaces only armed/triggered setups and greys out completed plays.",
    cluster: "Cluster ranking: quad (4+ peaks) > triple (3) > battle > golden/walls. Weights in data/weight_lab/cluster_score_weights.json. GEX is a public-OI heuristic ? not dealer truth.",
  };
  const hintText =
    flashHints[state.scannerStrategy] ||
    hints[state.scannerMode] ||
    state.scannerHint ||
    "";

  // Setup Scanner always locks to nearest (lowest DTE) ? no multi-exp picks.
  state.clusterExp = "nearest";

  const progressBlock = scanning
    ? `<div class="scan-progress">
        <div class="scan-progress-bar"><i style="width:${pct}%"></i></div>
        <span>${escapeHtml(progressMsg)}</span>
      </div>`
    : meta.scanned
      ? `<div class="scan-status">Scanned ${meta.scanned}/${meta.universe_size || meta.scanned} · ${meta.qualified ?? picks.length} qualified · ${meta.elapsed_ms || 0}ms · top ${picks.length}</div>`
      : "";

  const canSaveSnap =
    state.scannerStrategy === "cluster" &&
    picks.some((p) => (p.setups || []).length) &&
    !scanning;

  const clusterExpButtons = ["Nearest (1 Exp)", "Fri Jul 24", "Fri Jul 31", "Fri Aug 7", "Fri Aug 14", "Fri Aug 21", "Fri Aug 28", "Fri Sep 4", "Fri Sep 11"]
    .map((label, index) => `<button class="scan-mode ${index === 0 ? "active" : ""}" data-action="cluster-exp-ui" title="Local scanner currently uses nearest expiration only">${label}</button>`)
    .join("");

  const body = `
    <div class="scanner-shell">
      <p class="scanner-blurb">The local scanner ranks the universe and surfaces the 30 highest-conviction setup candidates.</p>
      <p class="scanner-caveat notice">Local clean-room reconstruction from public OI/Greeks ? not the hosted Access Obsidian weights.</p>
      <div class="scanner-control stacked">
        <div class="scan-row">
          <div class="scan-modes">${modes}</div>
          ${cipherBtn}
          <button class="tool" data-action="export-scan" ${picks.length ? "" : "disabled"}>Download .CSV</button>
          <button class="tool pink" data-action="save-scan-snapshot" ${canSaveSnap ? "" : "disabled"} title="Persist current cluster picks for forward backtest (no re-scan)">Save snapshot for backtest</button>
        </div>
        <div class="scan-row">
          <div class="scan-modes">${row2}</div>
          <label class="cluster-exp compact-select">CLUSTER EXP
            <select id="clusterExpSelect" disabled title="Setup Scanner always uses nearest (lowest DTE) expiration">
              <option value="nearest" selected>Nearest (1 Exp)</option>
            </select>
          </label>
          <div class="cluster-exp-pills" aria-label="Captured AccessObsidian cluster expiration choices; local scan uses nearest only">${clusterExpButtons}</div>
        </div>
        <div class="scan-row">
          <div class="scan-modes">${flash}</div>
        </div>
        <p class="scan-hint">${escapeHtml(hintText)}</p>
      </div>
      <div class="scan-filters">${directionFilters}</div>
      <div class="notice gold-notice">Scans aren't auto-saved ? download CSV or (Cluster) Save snapshot for backtest. A full-universe scan can take a few minutes; keep this screen open and check back when it finishes.</div>
      ${progressBlock}
      ${clusterCards ? `<div class="cluster-grid">${clusterCards}</div>` : ""}
      <div class="cipher-card-grid flash-card-grid">${cards}</div>
    </div>
    <div class="notice">${meta.caveat || "Research-only reconstruction ? not proprietary Access Obsidian weights. Not trade advice."}</div>`;
  $("#researchContent").innerHTML = panel(
    "Setup Scanner",
    "The local scanner ranks the universe and surfaces the 30 highest-conviction setup candidates.",
    body,
  );
}

async function runScanner(strategy) {
  if (strategy) state.scannerStrategy = strategy;
  if (state.scannerBusy) return;
  state.scannerBusy = true;
  state.scanner = [];
  state.scannerClusters = [];
  state.scannerProgress = { done: 0, total: state.scannerUniverseCount || 250, pct: 0 };
  state.scannerMeta = { universe_size: state.scannerUniverseCount || 250 };
  renderScanner();
  try {
    const mode = ["flash", "flash_index", "flash_agentic"].includes(state.scannerStrategy)
      ? "short"
      : state.scannerMode;
    const strategyParam = state.scannerStrategy || "cipher";
    // All Setup Scanner strategies fetch nearest (lowest DTE) only ? 1 expiration.
    state.clusterExp = "nearest";
    const start = await fetchJson(
      `${api}/api/scan?mode=${encodeURIComponent(mode)}&strategy=${encodeURIComponent(strategyParam)}&limit=30&feed=opra&async=1&cluster_exp=nearest`,
    );
    const jobId = start.job_id;
    state.scannerJobId = jobId;
    state.scannerUniverseCount = start.universe_size || state.scannerUniverseCount;
    let data = null;
    for (;;) {
      await new Promise((r) => setTimeout(r, 700));
      const job = await fetchJson(`${api}/api/scan/job?id=${encodeURIComponent(jobId)}`);
      state.scannerProgress = {
        done: job.done || 0,
        total: job.total || state.scannerUniverseCount,
        pct: job.pct || 0,
        message: job.message,
      };
      if (state.view === "scanner") renderScanner();
      if (job.status === "done") {
        data = job.result;
        break;
      }
      if (job.status === "error") throw new Error(job.error || job.message || "Scan failed");
    }
    state.scanner = data.top || [];
    state.scannerClusters = data.clusters || [];
    state.scannerMeta = {
      scanned: data.scanned,
      universe_size: data.universe_size,
      qualified: data.qualified,
      failed: data.failed,
      elapsed_ms: data.elapsed_ms,
      caveat: data.caveat,
      as_of: data.as_of,
      hint: data.hint,
    };
    state.scannerUniverseCount = data.universe_size;
    state.scannerHint = data.hint;
  } catch (error) {
    state.scanner = [];
    state.scannerClusters = [];
    state.scannerMeta = { caveat: error.message || "Scan failed" };
  } finally {
    state.scannerBusy = false;
    state.scannerJobId = null;
    state.scannerProgress = null;
    if (state.view === "scanner") renderScanner();
  }
}

function exportScanner() {
  if (!state.scanner?.length) return;
  const lines = [
    "rank,ticker,scan_type,state,setup_type,spot,score,strength,direction,target,invalidation,reward_risk,geometry_valid,actionable,supports,resistances,pull_target,vacuum_targets,validation_errors,read,mode",
    ...state.scanner.map((item) =>
      [
        item.rank,
        item.ticker,
        item.strategy || state.scannerStrategy,
        item.state || item.agent_state || "",
        JSON.stringify(item.setup_type || item.cluster?.label || item.setups?.[0]?.label || ""),
        item.spot,
        item.score,
        item.strength ?? "",
        item.direction,
        item.target ?? item.pull_target ?? "",
        item.invalidation ?? item.close_under ?? item.reclaim ?? "",
        item.reward_risk ?? "",
        item.geometry_valid ?? "",
        item.actionable ?? "",
        JSON.stringify((item.supports || []).join("|")),
        JSON.stringify((item.resistances || []).join("|")),
        item.pull_target,
        JSON.stringify((item.vacuum_targets || []).join("|")),
        JSON.stringify((item.validation_errors || []).join("|")),
        JSON.stringify(item.read || item.reason || ""),
        item.mode,
      ].join(","),
    ),
  ];
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `cipher-model-${state.scannerMode}-${state.scannerStrategy}-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function saveScanSnapshot() {
  const picks = (state.scanner || []).filter((p) => (p.setups || []).length);
  if (!picks.length) {
    setStatus("No cluster setups to save ? run Cluster scan first");
    return;
  }
  try {
    setStatus("Saving cluster snapshot for backtest?");
    const data = await fetchJson(`${api}/api/backtest?action=ingest-scan`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({
        picks,
        mode: state.scannerMode || "short",
        feed: "opra",
        cluster_exp: state.clusterExp || "nearest",
        meta: {
          strategy: state.scannerStrategy,
          scanned: state.scannerMeta?.scanned,
          as_of: state.scannerMeta?.as_of,
        },
      }),
    });
    if (data.error) throw new Error(data.error);
    setStatus(`Snapshot saved ? n=${data.n || 0} ? ${data.path || "ok"}`);
  } catch (error) {
    setStatus(error.message || "Snapshot save failed");
  }
}

function renderSettings() {
  const feed = (state.matrix?.feed || "opra").toUpperCase();
  const wl = state.weightLab || {};
  const ws = wl.weights_summary || {};
  const fs = wl.flash_weights_summary || {};
  const bt = state.backtest || {};
  const byKind = bt.by_kind || {};
  const btKindRows = Object.keys(byKind).length
    ? Object.entries(byKind)
        .map(([kind, row]) => {
          const hit = row.hit_rate != null ? `${(row.hit_rate * 100).toFixed(0)}%` : "—";
          const toward =
            row.toward_rate != null ? ` ? toward ${(row.toward_rate * 100).toFixed(0)}%` : "";
          const ttt =
            row.avg_time_to_touch_days != null ? ` ? ttt ${row.avg_time_to_touch_days}d` : "";
          return `<div class="setting-row"><span>${escapeHtml(kind)}</span><strong>${hit} · n=${row.n ?? 0}${toward}${ttt}</strong></div>`;
        })
        .join("")
    : `<div class="setting-row"><span>By kind</span><strong>?</strong></div>`;
  const btModeRaw = bt.mode || (bt.mode_counts?.forward ? "forward" : bt.n_reports != null ? "forward" : null);
  const btMode =
    btModeRaw === "forward"
      ? "forward (post-snapshot bars)"
      : btModeRaw === "magnetism"
        ? "magnetism (sticky-level proxy)"
        : btModeRaw === "mixed"
          ? "mixed forward + magnetism"
          : btModeRaw || "—";
  const btReportsNote =
    bt.n_reports != null ? `<div class="setting-row"><span>Forward reports</span><strong>${bt.n_reports}</strong></div>` : "";

  const cipherN = ws.n ?? wl.commercial_rows;
  const flashN = fs.n ?? wl.flash_rows;
  const overfitHints = [];
  if (cipherN != null && Number(cipherN) < 30) {
    overfitHints.push(`Cipher fit n=${cipherN} (<30) ? treat weights as exploratory.`);
  }
  if (ws.r_squared != null && Number(ws.r_squared) > 0.95) {
    overfitHints.push(`Cipher R?=${ws.r_squared} (>0.95) ? possible overfit; add more dated CSVs.`);
  }
  if (flashN != null && Number(flashN) < 30) {
    overfitHints.push(`Flash fit n=${flashN} (<30) ? sample too small for stable runway weights.`);
  }
  if (fs.r_squared != null && Number(fs.r_squared) > 0.95) {
    overfitHints.push(`Flash R?=${fs.r_squared} (>0.95) ? reference fit may be overfit.`);
  }
  const wlHintHtml = overfitHints.length
    ? `<p class="wl-hint warn">${overfitHints.map((h) => escapeHtml(h)).join(" ? ")}</p>`
    : `<p class="wl-hint">Cipher Model needs more dated commercial CSVs for a stable fit. Flash Index (IWM/SPY/QQQ) is reference-only ? not training labels.</p>`;

  const body = `<div class="settings-grid">
    <article class="setting-card plan-card">
      <h2>Your plan</h2>
      <div class="plan-badge">LOCAL</div>
      <p>Every recreated research feature is unlocked locally. This is a clean-room local implementation, not an Access Obsidian account entitlement.</p>
    </article>
    <article class="setting-card">
      <h2>Plan & connection</h2>
      <p>Local read-only research terminal. Market data via Alpaca OPRA + SIP/IEX. Credentials stay in <code>app/.env</code> on the core service ? the browser never sees keys.</p>
      <span class="badge ok">Connected ? ${feed} + contract OI</span>
    </article>
    <article class="setting-card">
      <h2>Live refresh</h2>
      <select id="refreshSelect">
        <option value="10" ${state.refreshSeconds === 10 ? "selected" : ""}>10 seconds</option>
        <option value="15" ${state.refreshSeconds === 15 ? "selected" : ""}>15 seconds</option>
        <option value="30" ${state.refreshSeconds === 30 ? "selected" : ""}>30 seconds</option>
        <option value="60" ${state.refreshSeconds === 60 ? "selected" : ""}>60 seconds</option>
      </select>
      <p>SSE stream + bar refresh cadence when Live / Auto refresh is enabled.</p>
      <div class="setting-row"><span>Timezone</span><strong>America/New_York</strong></div>
    </article>
    <article class="setting-card">
      <h2>Alpaca data</h2>
      <p>OPRA options feed for Strike Matrix, Night Vision, and Spyglass. Stock bars use SIP (fallback IEX). Cipher does not place trades or generate orders.</p>
      <label>API KEY ID<input type="text" value="CONNECTED" readonly title="Keys are server-side only" aria-label="API key status"></label>
      <label>API SECRET KEY<input type="password" value="••••••••••••" readonly title="Keys are server-side only" aria-label="API key masked"></label>
      <div class="scan-actions" style="margin-top:10px"><button class="tool pink" disabled>Connect</button><button class="tool" disabled>Disconnect</button></div>
      <p class="scan-hint">Your keys, your control: this local build keeps secrets server-side and redacted. The browser shows connection status only.</p>
    </article>
    <article class="setting-card weight-lab-card">
      <h2>Cipher weight lab</h2>
      <p>Calibrate Cipher Model + Flash scores against commercial CSV labels. Rank/score approximation only ? not proprietary Access Obsidian weights. Public-OI GEX is heuristic, not dealer truth.</p>
      <div class="setting-row"><span>Cipher Model rows</span><strong>${wl.commercial_rows ?? "—"}</strong></div>
      <div class="setting-row"><span>Flash runway rows</span><strong>${wl.flash_rows ?? "—"}</strong></div>
      <div class="setting-row"><span>Local feature tickers</span><strong>${wl.feature_tickers ?? "—"}</strong></div>
      <div class="setting-row"><span>Cipher R? / Ï„ / n</span><strong>${ws.r_squared != null ? `${ws.r_squared} / ${ws.kendall_tau_rank} / n=${ws.n ?? "—"}` : "—"}</strong></div>
      <div class="setting-row"><span>Flash R? / Ï„ / n</span><strong>${fs.r_squared != null ? `${fs.r_squared} / ${fs.kendall_tau_rank} / n=${fs.n ?? "—"}` : "—"}</strong></div>
      <div class="setting-row"><span>Cipher active</span><strong>${wl.active ? "YES" : "no"}</strong></div>
      <div class="setting-row"><span>Flash active</span><strong>${wl.flash_active ? "YES" : "no"}</strong></div>
      <div class="setting-row"><span>Cluster ranking</span><strong>${escapeHtml(((wl.cluster_score_weights || {}).hard_rank_order || ["quad", "triple", "battle"]).slice(0, 4).join(" > "))}${((wl.cluster_score_weights || {}).hard_rank_order || []).length > 4 ? " > ?" : ""}</strong></div>
      <div class="scan-actions" style="margin-top:10px">
        <button class="tool" data-action="wl-seed">Seed audit CSV</button>
        <button class="tool" data-action="wl-fit">Fit Cipher</button>
        <button class="tool" data-action="wl-dump">Dump live features</button>
        <button class="tool" data-action="wl-fit-local">Fit Cipher + local</button>
        <button class="tool" data-action="wl-fit-flash">Fit Flash</button>
        <button class="tool pink" data-action="wl-activate">${wl.active ? "Deactivate Cipher" : "Activate Cipher"}</button>
        <button class="tool pink" data-action="wl-activate-flash">${wl.flash_active ? "Deactivate Flash" : "Activate Flash"}</button>
        <button class="tool" data-action="wl-refresh">Refresh status</button>
      </div>
      ${wlHintHtml}
      <p class="scan-hint">Cipher Model CSVs ? <code>data/weight_lab/commercial/</code>. Flash runway ? <code>commercial/other/</code>. Flash Index is reference-only. Cluster: <code>cluster_score_weights.json</code> (quad &gt; triple). CLI: <code>fit --rank-loss</code> ? <code>show-cluster</code>.</p>
    </article>
    <article class="setting-card">
      <h2>Cluster backtest</h2>
      <p>Score cluster level hit-rates vs daily bars (forward after snapshot, or magnetism proxy when bars are not yet ahead of the snapshot).</p>
      <div class="setting-row"><span>Score mode</span><strong>${escapeHtml(String(btMode))}</strong></div>
      <div class="setting-row"><span>Overall hit rate</span><strong>${bt.overall_hit_rate != null ? `${(bt.overall_hit_rate * 100).toFixed(1)}%` : "—"}</strong></div>
      <div class="setting-row"><span>Setups scored</span><strong>${bt.n_setups ?? "—"}</strong></div>
      <div class="setting-row"><span>Horizon</span><strong>${bt.horizon ?? (bt.horizons ? bt.horizons.join("/") : 5)}d</strong></div>
      ${btReportsNote}
      ${btKindRows}
      <div class="scan-actions" style="margin-top:10px">
        <button class="tool pink" data-action="bt-run">Run cluster backtest</button>
        <button class="tool" data-action="bt-score">Rescore latest snapshot</button>
        <button class="tool" data-action="bt-rescore-due">Rescore due</button>
      </div>
      <p class="scan-hint">Public-OI heuristic ? not dealer truth. Scanner ? Save snapshot uses ingest (no re-scan). Forward: <code>python3 scripts/backtest_clusters.py rescore-due</code></p>
    </article>
    <article class="setting-card">
      <h2>Disclosure</h2>
      <p>GEX/VEX are documented local estimates from public OI and Greeks. Not proprietary dealer positioning. Spyglass aggressor side is inferred vs bid/ask. Flash / Ghost are research heuristics.</p>
    </article>
  </div>`;
  $("#researchContent").innerHTML = panel("Settings", "Local preference and data-status controls", body);
}

async function runClusterBacktest(action = "run") {
  try {
    const labels = {
      run: "Running cluster backtest?",
      score: "Rescoring cluster snapshot?",
      "rescore-due": "Forward rescoring due snapshots?",
    };
    setStatus(labels[action] || "Cluster backtest?");
    const qs =
      action === "rescore-due"
        ? `action=rescore-due&horizons=1,3,5`
        : `action=${encodeURIComponent(action)}&horizon=5&limit=20&mode=short`;
    const data = await fetchJson(`${api}/api/backtest?${qs}`);
    if (data.error) throw new Error(data.error);
    state.backtest = data;
    if (state.view === "settings") renderSettings();
    if (action === "rescore-due") {
      setStatus(`Forward rescore ? ${data.n_reports || 0} report(s) ? mode ${data.mode || "forward"}`);
    } else {
      const pct = data.overall_hit_rate != null ? `${(data.overall_hit_rate * 100).toFixed(1)}%` : "—";
      setStatus(`Cluster backtest ${data.mode || "—"} ? hit ${pct} ? ${data.n_setups || 0} setups`);
    }
  } catch (error) {
    setStatus(error.message || "Cluster backtest failed");
  }
}

async function refreshWeightLab() {
  try {
    state.weightLab = await fetchJson(`${api}/api/weight-lab?action=status`);
  } catch (error) {
    state.weightLab = { error: error.message };
  }
  if (state.view === "settings") renderSettings();
}

async function refreshRankingLab({ fresh = false } = {}) {
  state.rankingBusy = true;
  if (state.view === "rankingLab") renderRankingLab();
  try {
    const suffix = fresh ? "?fresh=1" : "";
    state.rankingLab = await fetchJson(`${api}/api/ranking-lab${suffix}`);
  } catch (error) {
    state.rankingLab = { error: error.message };
  } finally {
    state.rankingBusy = false;
  }
  if (state.view === "rankingLab") renderRankingLab();
  if (state.view === "gptAnalyst") renderGptAnalyst();
}

async function weightLabAction(action) {
  try {
    let urlAction = action;
    let local = "1";
    if (action === "activate") {
      urlAction = state.weightLab?.active ? "deactivate" : "activate";
    } else if (action === "activate-flash") {
      urlAction = state.weightLab?.flash_active ? "deactivate-flash" : "activate-flash";
    } else if (action === "fit") {
      local = "0";
    } else if (action === "fit-local") {
      urlAction = "fit";
      local = "1";
    } else if (action === "fit-flash") {
      urlAction = "fit-flash";
      local = "1";
    } else if (action === "refresh") {
      return refreshWeightLab();
    }
    const data = await fetchJson(
      `${api}/api/weight-lab?action=${encodeURIComponent(urlAction)}&local=${local}`,
    );
    if (data.status) state.weightLab = data.status;
    else if (urlAction === "status") state.weightLab = data;
    else await refreshWeightLab();
    if (state.view === "settings") renderSettings();
    if (data.r_squared != null) {
      setStatus(`Weight fit R?=${data.r_squared} Ï„=${data.kendall_tau_rank}`);
    } else {
      setStatus(`Weight lab: ${urlAction}`);
    }
  } catch (error) {
    setStatus(error.message || "Weight lab failed");
  }
}

function renderBio() {
  const quote = state.matrix?.quote || state.night?.quote || {};
  const summary = state.matrix?.summary || {};
  const rows = state.matrix?.rows || [];
  const spot = quote.price_context;
  const nearest = rows
    .filter((row) => Number.isFinite(Number(row.strike)))
    .sort((a, b) => Math.abs(a.strike - spot) - Math.abs(b.strike - spot))
    .slice(0, 5)
    .map((row) => {
      const total = row.cells.reduce((sum, cell) => sum + Math.abs(cell.net_gex || 0) + Math.abs(cell.net_vex || 0), 0);
      return `<tr><td>${dollars(row.strike, 1)}</td><td>${row.is_spot_band ? "Spot band" : "Nearby"}</td><td>${fmt(total)}</td></tr>`;
    })
    .join("");
  const body = `<div class="metric-grid">
    <div class="metric-card"><span>TICKER</span><strong>${escapeHtml(state.ticker)}</strong><small>${(state.matrix?.feed || "opra").toUpperCase()} options feed + public OI reconstruction.</small></div>
    <div class="metric-card"><span>SPOT</span><strong>${dollars(spot)}</strong><small>${Number.isFinite(quote.day_change_pct) ? `${quote.day_change_pct >= 0 ? "+" : ""}${quote.day_change_pct.toFixed(2)}% today` : "Change unavailable"}</small></div>
    <div class="metric-card"><span>GOLDEN LEVEL</span><strong>${dollars(summary.global_max_strike, 1)}</strong><small>Highest absolute exposure strike in the loaded matrix.</small></div>
  </div>
  <div class="data-card spy-table">
    <table><thead><tr><th>Strike</th><th>Context</th><th>Abs exposure</th></tr></thead><tbody>${nearest || '<tr><td colspan="3">Load a matrix to populate ticker bio.</td></tr>'}</tbody></table>
  </div>`;
  $("#researchContent").innerHTML = panel("Bio", `${state.ticker} market profile`, body);
}

function renderContractSearch() {
  const expirations = selectedExpirations();
  const rows = (state.matrix?.rows || []).flatMap((row) =>
    row.cells
      .filter((cell) => cell.available && expirations.includes(cell.expiration))
      .map((cell) => ({ strike: row.strike, ...cell })),
  );
  const contracts = rows
    .sort((a, b) => Math.abs(b.net_gex || 0) - Math.abs(a.net_gex || 0))
    .slice(0, 80)
    .map(
      (cell) => `<tr>
        <td>${escapeHtml(cell.expiration)}</td>
        <td>${dollars(cell.strike, 1)}</td>
        <td>${fmt(cell.net_gex || 0)}</td>
        <td>${fmt(cell.net_vex || 0)}</td>
        <td>${Number(cell.call_oi || 0).toLocaleString()}</td>
        <td>${Number(cell.put_oi || 0).toLocaleString()}</td>
      </tr>`,
    )
    .join("");
  const body = `<div class="filter-row">
    <button class="tool" data-action="refresh">Refresh</button>
    <span class="status">Top loaded contracts sorted by absolute GEX for ${escapeHtml(state.ticker)}.</span>
  </div>
  <div class="data-card spy-table">
    <table><thead><tr><th>Expiration</th><th>Strike</th><th>GEX</th><th>VEX</th><th>Call OI</th><th>Put OI</th></tr></thead><tbody>${contracts || '<tr><td colspan="6">Load Strike Matrix data to search contracts.</td></tr>'}</tbody></table>
  </div>`;
  $("#researchContent").innerHTML = panel("Contract Search", `${state.ticker} loaded option chain`, body);
}

function renderAdminSurface(kind) {
  const label =
    {
      woAdmin: "WO Admin",
      mispricingAdmin: "Mispricing Admin",
      stormAdmin: "Storm Admin",
    }[kind] || "Admin";
  const matrix = state.matrix || {};
  const summary = matrix.summary || {};
  const rows = matrix.rows || [];
  const strongest = rows
    .map((row) => ({
      strike: row.strike,
      gex: row.cells.reduce((sum, cell) => sum + (cell.net_gex || 0), 0),
      vex: row.cells.reduce((sum, cell) => sum + (cell.net_vex || 0), 0),
    }))
    .sort((a, b) => Math.abs(b.gex) - Math.abs(a.gex))
    .slice(0, 12);
  const rowsHtml = strongest
    .map((row) => `<tr><td>${dollars(row.strike, 1)}</td><td>${fmt(row.gex)}</td><td>${fmt(row.vex)}</td></tr>`)
    .join("");
  const body = `<div class="metric-grid">
    <div class="metric-card"><span>CHAIN</span><strong>${Number(matrix.coverage?.contracts || 0).toLocaleString()}</strong><small>Contracts in the active local reconstruction.</small></div>
    <div class="metric-card"><span>CALL WALL</span><strong>${dollars(summary.call_wall_strike, 1)}</strong><small>Highest upside call-open-interest pressure.</small></div>
    <div class="metric-card"><span>PUT WALL</span><strong>${dollars(summary.put_wall_strike, 1)}</strong><small>Highest downside put-open-interest pressure.</small></div>
  </div>
  <div class="data-card spy-table">
    <table><thead><tr><th>Strike</th><th>Net GEX</th><th>Net VEX</th></tr></thead><tbody>${rowsHtml || '<tr><td colspan="3">No matrix data loaded.</td></tr>'}</tbody></table>
  </div>`;
  $("#researchContent").innerHTML = panel(label, `${state.ticker} local operations surface`, body);
}

function gptProfile() {
  try {
    return {
      name: localStorage.getItem("cipher_gpt_profile_name") || "Cipher Research Analyst",
      focus: localStorage.getItem("cipher_gpt_profile_focus") || "options positioning, GEX/VEX, scanner rankings, flow, and invalidation levels",
    };
  } catch {
    return { name: "Cipher Research Analyst", focus: "options positioning and scanner rankings" };
  }
}

function setGptProfile() {
  const name = $("#gptProfileName")?.value?.trim() || "Cipher Research Analyst";
  const focus = $("#gptProfileFocus")?.value?.trim() || "options positioning and scanner rankings";
  localStorage.setItem("cipher_gpt_profile_name", name);
  localStorage.setItem("cipher_gpt_profile_focus", focus);
  setStatus("Saved ChatGPT analyst profile");
  renderGptAnalyst();
}

function rankingBriefLines() {
  const lab = state.rankingLab;
  if (!lab || lab.error) return [];
  const weights = (lab.rank_signal_weights || [])
    .slice(0, 8)
    .map((row) => `${row.label || row.feature}: ${(Number(row.weight || 0) * 100).toFixed(1)}% (${row.direction || "n/a"}, corr ${Number(row.correlation || 0).toFixed(2)})`);
  const kinds = (lab.cluster_kind_order || [])
    .slice(0, 6)
    .map((row) => `${row.kind}: signal ${Number(row.rank_signal || 0).toFixed(2)}, avg rank ${Number(row.avg_rank || 0).toFixed(1)}, n=${row.count || 0}`);
  return [
    `Ranking surrogate rows: ${lab.rows || 0} from ${lab.files || 0} saved files.`,
    `Feature weights: ${weights.join("; ") || "not enough saved rank data yet"}.`,
    `Cluster order: ${kinds.join("; ") || "not enough cluster data yet"}.`,
  ];
}

function renderRankingLab() {
  const lab = state.rankingLab;
  if (state.rankingBusy && !lab) {
    $("#researchContent").innerHTML = panel(
      "Ranking Lab",
      "Learning partial model signals from saved rankings",
      `<div class="notice">Building ranking surrogate from saved scans...</div>`,
    );
    return;
  }
  if (!lab) {
    $("#researchContent").innerHTML = panel(
      "Ranking Lab",
      "Learning partial model signals from saved rankings",
      `<div class="notice">No ranking snapshot loaded yet.</div><div class="scan-actions"><button class="tool pink" data-action="ranking-refresh">Load ranking model</button></div>`,
    );
    return;
  }
  if (lab.error) {
    $("#researchContent").innerHTML = panel(
      "Ranking Lab",
      "Learning partial model signals from saved rankings",
      `<div class="notice">${escapeHtml(lab.error)}</div><div class="scan-actions"><button class="tool pink" data-action="ranking-refresh">Retry</button></div>`,
    );
    return;
  }
  const weights = (lab.rank_signal_weights || [])
    .map(
      (row) => `<tr>
        <td>${escapeHtml(row.label || row.feature)}</td>
        <td>${(Number(row.weight || 0) * 100).toFixed(1)}%</td>
        <td>${Number(row.correlation || 0).toFixed(3)}</td>
        <td>${escapeHtml(row.direction || "")}</td>
        <td>${Math.round(Number(row.coverage || 0) * 100)}%</td>
      </tr>`,
    )
    .join("");
  const kinds = (lab.cluster_kind_order || [])
    .map(
      (row) => `<tr>
        <td>${escapeHtml(row.kind)}</td>
        <td>${Number(row.rank_signal || 0).toFixed(3)}</td>
        <td>${Number(row.avg_rank || 0).toFixed(2)}</td>
        <td>${row.count || 0}</td>
      </tr>`,
    )
    .join("");
  const sources = (lab.sources || [])
    .slice(0, 8)
    .map((row) => `<tr><td>${escapeHtml(row.file)}</td><td>${escapeHtml(row.strategy || "")}</td><td>${row.rows || 0}</td></tr>`)
    .join("");
  const body = `<div class="notice">${escapeHtml(lab.caveat || "")}</div>
    <div class="metric-grid">
      <div class="metric-card"><span>RANK ROWS</span><strong>${Number(lab.rows || 0).toLocaleString()}</strong><small>Saved picks converted into rank labels.</small></div>
      <div class="metric-card"><span>SOURCES</span><strong>${Number(lab.files || 0).toLocaleString()}</strong><small>Full scans, reports, and cluster snapshots.</small></div>
      <div class="metric-card"><span>LATEST</span><strong>${escapeHtml((lab.latest_source_as_of || lab.as_of || "").slice(0, 10) || "n/a")}</strong><small>Most recent saved ranking source.</small></div>
    </div>
    <div class="scan-actions" style="margin-top:10px">
      <button class="tool pink" data-action="ranking-refresh">Refresh model</button>
      <button class="tool" data-action="ranking-fresh">Force reread</button>
      <button class="tool" data-action="gpt-copy">Copy GPT prompt with ranks</button>
    </div>
    <div class="settings-grid rank-lab-grid">
      <article class="setting-card">
        <h2>Rank signal weights</h2>
        <div class="spy-table"><table><thead><tr><th>Feature</th><th>Weight</th><th>Corr</th><th>Dir</th><th>Seen</th></tr></thead><tbody>${weights || '<tr><td colspan="5">Run and save more scans to populate weights.</td></tr>'}</tbody></table></div>
      </article>
      <article class="setting-card">
        <h2>Cluster order</h2>
        <div class="spy-table"><table><thead><tr><th>Kind</th><th>Signal</th><th>Avg rank</th><th>N</th></tr></thead><tbody>${kinds || '<tr><td colspan="4">No cluster rows found.</td></tr>'}</tbody></table></div>
      </article>
    </div>
    <div class="data-card spy-table rank-source-table"><table><thead><tr><th>Source</th><th>Strategy</th><th>Rows</th></tr></thead><tbody>${sources || '<tr><td colspan="3">No saved source files found.</td></tr>'}</tbody></table></div>
    <div class="wl-hint">${escapeHtml(lab.formula || "")}</div>`;
  $("#researchContent").innerHTML = panel("Ranking Lab", "Partial model reconstruction from current cluster/scanner rankings", body);
}

function buildGptPrompt() {
  const profile = gptProfile();
  const quote = state.matrix?.quote || state.night?.quote || {};
  const summary = state.matrix?.summary || {};
  const expirations = selectedExpirations();
  const matrixRows = (state.matrix?.rows || [])
    .slice()
    .sort((a, b) => Math.abs((a.strike || 0) - (quote.price_context || 0)) - Math.abs((b.strike || 0) - (quote.price_context || 0)))
    .slice(0, 14)
    .map((row) => {
      const gex = row.cells.reduce((sum, cell) => sum + (cell.net_gex || 0), 0);
      const vex = row.cells.reduce((sum, cell) => sum + (cell.net_vex || 0), 0);
      return `${row.strike}: GEX ${fmt(gex)}, VEX ${fmt(vex)}${row.is_spot_band ? " (spot band)" : ""}`;
    });
  const scanRows = (state.scanner || [])
    .slice(0, 10)
    .map((item) => `#${item.rank || ""} ${item.ticker} ${item.direction || ""} score ${Number(item.score || 0).toFixed(0)} pull ${dollars(item.pull_target, 1)} invalidation ${dollars(item.close_under ?? item.reclaim, 1)} read: ${item.read || item.reason || ""}`);
  const flowRows = (state.flow?.prints || [])
    .slice(0, 12)
    .map((row) => `${row.ticker || state.ticker} ${row.type || ""} ${dollars(row.strike, 1)} ${row.side || "unknown"} premium ${fmt(row.premium || 0)} exp ${row.expiration || ""}`);
  const rankLines = rankingBriefLines();
  return `You are ${profile.name}. Analyze this Cipher options research workspace for ${state.ticker}.

Profile focus: ${profile.focus}

Rules:
- Treat GEX/VEX as local public-OI heuristics, not verified dealer positioning.
- Do not give financial advice. Provide scenario analysis, levels, invalidation, risks, and what further data to collect.
- Compare scanner output, matrix levels, flow/tape, and Night Vision context.
- End with a concise research checklist for the next session.

Ticker: ${state.ticker}
Spot: ${dollars(quote.price_context)}
Day change: ${Number.isFinite(quote.day_change_pct) ? `${quote.day_change_pct.toFixed(2)}%` : "unknown"}
Loaded expirations: ${expirations.join(", ") || "none"}
Global max / top pull: ${dollars(summary.global_max_strike, 1)}
Call wall: ${dollars(summary.call_wall_strike, 1)}
Put wall: ${dollars(summary.put_wall_strike, 1)}
Gamma flip: ${dollars(summary.gamma_flip_level, 2)}

Nearby matrix rows:
${matrixRows.join("\n") || "No matrix rows loaded."}

Scanner picks:
${scanRows.join("\n") || "No scanner run loaded. Ask whether to run Cipher Model, Cluster, Flash, or Flash Index next."}

Recent flow:
${flowRows.join("\n") || "No flow loaded."}

Ranking surrogate:
${rankLines.join("\n") || "Ranking Lab not loaded. Ask whether to refresh Ranking Lab after saving scans."}

Task:
1. Produce a structured read on the current setup.
2. Identify bullish and bearish scenarios with exact levels.
3. Point out any missing data or contradictions.
4. Recommend the next scans/features to collect for improving the reconstruction.`;
}

async function copyGptPrompt() {
  const prompt = buildGptPrompt();
  try {
    await navigator.clipboard.writeText(prompt);
    setStatus("Copied ChatGPT research prompt");
  } catch {
    const area = $("#gptPrompt");
    if (area) {
      area.focus();
      area.select();
    }
    setStatus("Select and copy the ChatGPT prompt");
  }
  if (state.view === "gptAnalyst") renderGptAnalyst();
}

function downloadGptPrompt() {
  const ticker = String(state.ticker || "cipher").toLowerCase();
  downloadText(`cipher-chatgpt-${ticker}-${new Date().toISOString().slice(0, 10)}.txt`, buildGptPrompt());
  setStatus("Downloaded ChatGPT prompt");
}

function openChatGpt() {
  window.open("https://chatgpt.com/", "_blank", "noopener,noreferrer");
  setStatus("Opened ChatGPT. Paste the copied Cipher prompt into your logged-in session.");
}

function renderGptAnalyst() {
  const profile = gptProfile();
  const prompt = buildGptPrompt();
  const body = `<div class="notice">Login-only handoff: this does not use an OpenAI API key and does not automate ChatGPT in the background. It packages the current Cipher workspace for your logged-in ChatGPT session.</div>
  <div class="settings-grid">
    <article class="setting-card">
      <h2>ChatGPT profile</h2>
      <label>Profile name<input id="gptProfileName" type="text" value="${escapeHtml(profile.name)}"></label>
      <label>Research focus<input id="gptProfileFocus" type="text" value="${escapeHtml(profile.focus)}"></label>
      <div class="scan-actions" style="margin-top:10px">
        <button class="tool pink" data-action="gpt-save-profile">Save profile</button>
        <button class="tool" data-action="gpt-copy">Copy prompt</button>
        <button class="tool" data-action="gpt-open">Open ChatGPT</button>
        <button class="tool" data-action="gpt-download">Download prompt</button>
      </div>
    </article>
    <article class="setting-card">
      <h2>Context included</h2>
      <div class="setting-row"><span>Ticker</span><strong>${escapeHtml(state.ticker)}</strong></div>
      <div class="setting-row"><span>Matrix rows</span><strong>${state.matrix?.rows?.length || 0}</strong></div>
      <div class="setting-row"><span>Scanner picks</span><strong>${state.scanner?.length || 0}</strong></div>
      <div class="setting-row"><span>Flow prints</span><strong>${state.flow?.prints?.length || 0}</strong></div>
    </article>
  </div>
  <div class="data-card gpt-prompt-card"><textarea id="gptPrompt" readonly>${escapeHtml(prompt)}</textarea></div>`;
  $("#researchContent").innerHTML = panel("GPT Analyst", "Login-only ChatGPT research handoff", body);
}

function renderResearch() {
  const renderers = {
    spyglass: renderSpyglass,
    watchlists: renderWatchlists,
    journal: renderJournal,
    saves: renderSaves,
    scanner: renderScanner,
    rankingLab: renderRankingLab,
    gptAnalyst: renderGptAnalyst,
    bio: renderBio,
    contractSearch: renderContractSearch,
    woAdmin: () => renderAdminSurface("woAdmin"),
    mispricingAdmin: () => renderAdminSurface("mispricingAdmin"),
    stormAdmin: () => renderAdminSurface("stormAdmin"),
    settings: renderSettings,
  };
  renderers[state.view]?.();
  if (state.view === "watchlists" && !Object.keys(state.watchData).length) loadWatchlistData();
  if (state.view === "rankingLab" && !state.rankingLab && !state.rankingBusy) refreshRankingLab();
  if (state.view === "spyglass" && !state.flow) {
    fetchJson(
      `${api}/api/flow?ticker=${encodeURIComponent(state.ticker)}&feed=opra&min=${state.spyglassFilters.premium}&fresh=1`,
    )
      .then((data) => {
        state.flow = data;
        if (state.view === "spyglass") renderSpyglass();
      })
      .catch(() => {});
  }
}

function saveJournal() {
  const entry = {
    id: crypto.randomUUID(),
    date: $("#journalDate").value,
    result: $("#journalResult").value,
    amount: $("#journalAmount").value || "$0",
    note: $("#journalNote").value || "—",
  };
  state.journal.push(entry);
  persist();
  renderJournal();
}

function addWatchlist(ticker = state.ticker) {
  if (!state.watchlist.includes(ticker)) state.watchlist.push(ticker);
  persist();
  if (state.view === "watchlists") renderWatchlists();
}

function saveChart() {
  const peak = state.night?.peak || state.matrix?.summary;
  const quote = state.night?.quote || state.matrix?.quote || {};
  const derived = state.matrix ? matrixDerived(state.matrix, fieldForMetric(), selectedExpirations()) : {};
  const rankedLevels = exposureRowsFrom(state.matrix, fieldForMetric())
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    .slice(0, 4)
    .map((row, index, rows) => ({
      strike: Number(row.strike).toFixed(Number.isInteger(Number(row.strike)) ? 0 : 1),
      score: Math.round((Math.abs(row.value) / Math.max(Math.abs(rows[0]?.value || 0), 1)) * 100) || (index === 0 ? 100 : 0),
    }));
  const canvas = $("#nvCanvas");
  let thumbnail = "";
  try {
    thumbnail = canvas ? canvas.toDataURL("image/png") : "";
  } catch {
    thumbnail = "";
  }
  state.saves.push({
    id: crypto.randomUUID(),
    ticker: state.nightSplit ? "SPY/QQQ" : state.ticker,
    view: `${state.nightExp || 1} Exp`,
    created: new Date().toLocaleString(),
    date: new Date().toLocaleDateString(undefined, { month: "numeric", day: "numeric", year: "2-digit" }),
    price: dollars(quote.price_context),
    topPull: dollars(peak?.price || peak?.global_max_strike || derived.topPullStrike, 1),
    levels: rankedLevels,
    thumbnail,
    note: `Spot ${dollars(quote.price_context)} ? ${state.timeframe} ? ${state.nightExp} exp ? top-pull ${dollars(peak?.price || peak?.global_max_strike || derived.topPullStrike, 1)} ? overlays ${[state.nightVP && "VP", state.nightXray && "X-Ray", state.nightGhost && "Ghost", state.nightTape && "TS"].filter(Boolean).join("/") || "levels"}`,
  });
  persist();
  setLive(true, "Chart saved");
  if (state.view === "saves") renderSaves();
}

async function refreshFlow() {
  try {
    const min = state.view === "night" ? state.tapeMin : state.spyglassFilters.premium;
    state.flow = await fetchJson(
      `${api}/api/flow?ticker=${encodeURIComponent(state.ticker)}&feed=opra&min=${min}&fresh=1`,
    );
    if (state.view === "spyglass") renderSpyglass();
    if (state.view === "night") renderTapePanel();
  } catch (error) {
    if (state.view === "spyglass") {
      const notice = $("#researchContent .notice");
      if (notice) notice.textContent = error.message || "Flow unavailable";
    }
  }
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.view) return switchView(button.dataset.view);
  if (button.dataset.matrixMode) {
    state.matrixMode = button.dataset.matrixMode;
    $$("[data-matrix-mode]").forEach((item) => {
      const on = item.dataset.matrixMode === state.matrixMode;
      item.classList.toggle("active", on);
      item.setAttribute("aria-pressed", on ? "true" : "false");
    });
    if (state.view === "trident") return renderTrident();
    return renderMatrix();
  }
  if (button.dataset.matrixExp) {
    state.matrixExp = Number(button.dataset.matrixExp);
    $$("[data-matrix-exp]").forEach((item) => {
      item.classList.toggle("active", Number(item.dataset.matrixExp) === state.matrixExp);
    });
    const need = state.matrixExp;
    if (state.view === "trident") {
      const sample = state.trident.SPY || state.trident.QQQ || state.trident.IWM;
      const have = sample?.expirations?.length || 0;
      if (need > have) return loadTrident();
      return renderTrident();
    }
    const have = state.matrix?.expirations?.length || 0;
    if (need > have) return load(state.ticker);
    return renderMatrix();
  }
  if (button.dataset.nightExp) {
    state.nightExp = Number(button.dataset.nightExp);
    $$("[data-night-exp]").forEach((item) => item.classList.toggle("active", item === button));
    return load(state.ticker);
  }
  if (button.dataset.frame) {
    state.timeframe = button.dataset.frame;
    $$("[data-frame]").forEach((item) => item.classList.toggle("active", item.dataset.frame === state.timeframe));
    document.querySelector(".time-menu")?.removeAttribute("open");
    return load(state.ticker);
  }
  if (button.dataset.metric && !button.dataset.action) {
    state.metric = button.dataset.metric;
    $$("#matrixView [data-metric], #tridentToolbar [data-metric]").forEach((item) => {
      const on = item.dataset.metric === state.metric;
      item.classList.toggle("active", on);
      item.setAttribute("aria-pressed", on ? "true" : "false");
    });
    if (state.view === "trident") return renderTrident();
    return state.view === "night" ? renderNight() : renderMatrix();
  }
  if (button.dataset.window) {
    state.windowPct = Number(button.dataset.window);
    $$("[data-window]").forEach((item) => item.classList.toggle("active", Number(item.dataset.window) === state.windowPct));
    if (state.view === "trident") return loadTrident();
    stopStream();
    return load(state.ticker).then(() => {
      if (state.auto) startLive();
    });
  }
  if (button.dataset.density) {
    const density = button.dataset.density;
    const inTrident = button.closest("#tridentToolbar");
    if (inTrident) {
      state.tridentDensity = density;
      $$("#tridentToolbar [data-density]").forEach((item) => {
        const on = item.dataset.density === density;
        item.classList.toggle("active", on);
        item.setAttribute("aria-pressed", on ? "true" : "false");
      });
      $$(".trident-wrap").forEach((wrap) => wrap.classList.toggle("full-density", density === "full"));
      return;
    }
    $$("#matrixView [data-density]").forEach((item) => {
      const on = item === button;
      item.classList.toggle("active", on);
      item.setAttribute("aria-pressed", on ? "true" : "false");
    });
    return $("#matrixWrap")?.classList.toggle("full-density", density === "full");
  }
  if (button.dataset.action === "node-galaxy") {
    state.nodeGalaxy = !state.nodeGalaxy;
    button.classList.toggle("active", state.nodeGalaxy);
    button.setAttribute("aria-pressed", state.nodeGalaxy ? "true" : "false");
    return renderMatrix();
  }
  if (button.dataset.action === "export-matrix") return exportMatrixCsv();
  if (button.dataset.action === "export-trident") return exportTridentCsv();
  if (button.dataset.action === "export-trident-pane") {
    return exportTridentCsv(button.dataset.ticker);
  }
  if (button.dataset.action === "snap-spot") {
    const spot = state.matrix?.quote?.price_context;
    const rows = state.matrix?.rows || [];
    const band = rows.find((r) => r.is_spot_band) || rows.find((r) => Math.abs(r.strike - spot) / spot < 0.01);
    return scrollMatrixToStrike(band?.strike ?? spot);
  }
  if (button.dataset.action === "snap-golden") {
    const metric = fieldForMetric();
    const derived = state.matrix ? matrixDerived(state.matrix, metric, selectedExpirations()) : {};
    return scrollMatrixToStrike(derived.topPullStrike);
  }
  if (button.dataset.action === "toggle-fc") {
    state.showFC = !state.showFC;
    $$("[data-action='toggle-fc']").forEach((item) => {
      item.classList.toggle("active", state.showFC);
      item.setAttribute("aria-pressed", state.showFC ? "true" : "false");
    });
    if (state.view === "trident") return renderTrident();
    return renderMatrix();
  }
  if (button.dataset.action === "trident-snap-spot") {
    for (const ticker of ["SPY", "QQQ", "IWM"]) {
      const data = state.trident[ticker];
      const wrap = $(`#tridentWrap${ticker}`);
      const spot = data?.quote?.price_context;
      const band = (data?.rows || []).find((r) => r.is_spot_band);
      scrollWrapToStrike(wrap, band?.strike ?? spot);
    }
    return;
  }
  if (button.dataset.action === "trident-snap-golden") {
    for (const ticker of ["SPY", "QQQ", "IWM"]) {
      const data = state.trident[ticker];
      const wrap = $(`#tridentWrap${ticker}`);
      if (!data) continue;
      const derived = matrixDerived(data, fieldForMetric(), selectedExpirations(data));
      scrollWrapToStrike(wrap, derived.topPullStrike);
    }
    return;
  }
  if (button.dataset.action === "refresh") return load(state.ticker);
  if (button.dataset.action === "refresh-flow") return refreshFlow();
  if (button.dataset.action === "watch-current") return addWatchlist();
  if (button.dataset.action === "toggle-rail") {
    document.querySelector(".app")?.classList.toggle("rail-collapsed");
    return;
  }
  if (button.dataset.action === "logout") {
    setStatus("Local Cipher session ? no remote logout. Clear browser storage manually if needed.");
    return;
  }
  if (button.dataset.action === "open-ticker") {
    $("#ticker").value = button.dataset.ticker;
    switchView("matrix");
    stopStream();
    return load(button.dataset.ticker).then(() => {
      if (state.auto) startLive();
    });
  }
  if (button.dataset.action === "remove-watch") {
    state.watchlist = state.watchlist.filter((ticker) => ticker !== button.dataset.ticker);
    persist();
    return renderWatchlists();
  }
  if (button.dataset.action === "save-journal") return saveJournal();
  if (button.dataset.action === "remove-journal") {
    state.journal = state.journal.filter((entry) => entry.id !== button.dataset.id);
    persist();
    return renderJournal();
  }
  if (button.dataset.action === "refresh-trident") return loadTrident();
  if (button.dataset.action === "refresh-watchlist") return loadWatchlistData();
  if (button.dataset.action === "trident-metric") {
    state.metric = button.dataset.metric;
    return renderTrident();
  }
  if (button.dataset.action === "run-cluster") return runScanner("cluster");
  if (button.dataset.action === "run-liq") return runScanner("liquidity");
  if (button.dataset.action === "export-scan") return exportScanner();
  if (button.dataset.action === "save-scan-snapshot") return saveScanSnapshot();
  if (button.dataset.action === "wl-seed") return weightLabAction("seed");
  if (button.dataset.action === "wl-fit") return weightLabAction("fit");
  if (button.dataset.action === "wl-fit-local") return weightLabAction("fit-local");
  if (button.dataset.action === "wl-fit-flash") return weightLabAction("fit-flash");
  if (button.dataset.action === "wl-dump") return weightLabAction("dump");
  if (button.dataset.action === "wl-activate") return weightLabAction("activate");
  if (button.dataset.action === "wl-activate-flash") return weightLabAction("activate-flash");
  if (button.dataset.action === "wl-refresh") return weightLabAction("refresh");
  if (button.dataset.action === "bt-run") return runClusterBacktest("run");
  if (button.dataset.action === "bt-score") return runClusterBacktest("score");
  if (button.dataset.action === "bt-rescore-due") return runClusterBacktest("rescore-due");
  if (button.dataset.action === "ranking-refresh") return refreshRankingLab();
  if (button.dataset.action === "ranking-fresh") return refreshRankingLab({ fresh: true });
  if (button.dataset.action === "gpt-save-profile") return setGptProfile();
  if (button.dataset.action === "gpt-copy") return copyGptPrompt();
  if (button.dataset.action === "gpt-open") return openChatGpt();
  if (button.dataset.action === "gpt-download") return downloadGptPrompt();
  if (button.dataset.scanStrategy) {
    state.scannerStrategy = button.dataset.scanStrategy;
    renderScanner();
    return runScanner(button.dataset.scanStrategy);
  }
  if (button.dataset.scanFilter) {
    state.scannerFilter = button.dataset.scanFilter;
    return renderScanner();
  }
  if (button.dataset.spyPremium) {
    state.spyglassFilters.premium = Number(button.dataset.spyPremium);
    return renderSpyglass();
  }
  if (button.dataset.spyPrice) {
    state.spyglassFilters.maxPrice =
      button.dataset.spyPrice === "all" ? "all" : Number(button.dataset.spyPrice);
    return renderSpyglass();
  }
  if (button.dataset.spyType) {
    state.spyglassFilters.type = button.dataset.spyType;
    return renderSpyglass();
  }
  if (button.dataset.spySide) {
    state.spyglassFilters.side = button.dataset.spySide;
    return renderSpyglass();
  }
  if (button.dataset.spyMoney) {
    state.spyglassFilters.money = button.dataset.spyMoney;
    return renderSpyglass();
  }
  if (button.dataset.scanMode) {
    state.scannerMode = button.dataset.scanMode;
    return renderScanner();
  }
  if (button.dataset.action === "save-chart") return saveChart();
  if (button.dataset.action === "capture-chart") return captureChart();
  if (button.dataset.action === "toggle-split") {
    state.nightSplit = !state.nightSplit;
    if (state.nightSplit) {
      return loadSplitData().then(() => renderNight());
    }
    return renderNight();
  }
  if (button.dataset.action === "toggle-tape") {
    state.nightTape = !state.nightTape;
    if (state.nightTape && !state.flow) {
      return refreshFlow().then(() => {
        if (state.view === "night") renderNight();
      });
    }
    return renderNight();
  }
  if (button.dataset.action === "toggle-vp") {
    state.nightVP = !state.nightVP;
    return renderNight();
  }
  if (button.dataset.action === "toggle-xray") {
    state.nightXray = !state.nightXray;
    return renderNight();
  }
  if (button.dataset.action === "toggle-ghost") {
    state.nightGhost = !state.nightGhost;
    return renderNight();
  }
  if (button.dataset.action === "expand-chart") {
    state.nightExpand = !state.nightExpand;
    return renderNight();
  }
  if (button.dataset.action === "close-tape") {
    state.nightTape = false;
    return renderNight();
  }
  if (button.dataset.action === "toggle-sq") {
    state.tapeSQ = !state.tapeSQ;
    return renderTapePanel();
  }
  if (button.dataset.tapeMin) {
    state.tapeMin = Number(button.dataset.tapeMin);
    return renderTapePanel();
  }
  if (button.dataset.xrayMetric) {
    state.xrayMetric = button.dataset.xrayMetric;
    return renderNight();
  }
  if (button.dataset.action === "workspace") {
    state.workspace = Number(button.dataset.ws) || 1;
    $$("[data-action=workspace]").forEach((b) => b.classList.toggle("active", Number(b.dataset.ws) === state.workspace));
    return;
  }
  if (button.dataset.action === "remove-save") {
    state.saves = state.saves.filter((save) => save.id !== button.dataset.id);
    persist();
    return renderSaves();
  }
  if (button.dataset.action === "run-scan") return runScanner();
  if (button.dataset.action === "cluster-exp-ui") {
    state.clusterExp = "nearest";
    setStatus("Local scanner parity UI shown; execution remains locked to nearest expiration.");
    return renderScanner();
  }
});

$("#ticker").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    stopStream();
    load().then(() => {
      if (state.auto) startLive();
    });
  }
});
$("#auto").addEventListener("change", (event) => setAuto(event.target.checked));
$("#nightAuto").addEventListener("change", (event) => setAuto(event.target.checked));
document.addEventListener("change", (event) => {
  if (event.target.id === "refreshSelect") {
    state.refreshSeconds = Number(event.target.value);
    persist();
    if (state.auto) startLive();
  }
  if (event.target.id === "clusterExpSelect") {
    // Locked to nearest ? ignore deeper picks if the control is ever re-enabled.
    state.clusterExp = "nearest";
    event.target.value = "nearest";
  }
});
window.addEventListener("resize", () => {
  if (state.view === "night") renderNight();
  if (state.view === "matrix" && state.nodeGalaxy) renderNodeGalaxy();
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopStream();
    return;
  }
  if (state.auto) startLive();
});
window.addEventListener("hashchange", () => {
  const fromHash = tickerFromHash();
  if (!fromHash || fromHash === state.ticker) return;
  $("#ticker").value = fromHash;
  stopStream();
  load(fromHash).then(() => {
    if (state.auto) startLive();
  });
});

(async function boot() {
  setLive(false, "Loading?");
  const fromHash = tickerFromHash();
  if (fromHash) {
    state.ticker = fromHash;
    $("#ticker").value = fromHash;
  }
  syncMatrixToolbar();
  updateHeaderTitle();
  $$('[data-action="toggle-fc"]').forEach((b) => {
    b.classList.toggle("active", state.showFC);
    b.setAttribute("aria-pressed", state.showFC ? "true" : "false");
  });
  await load(state.ticker);
  setAuto(true);
})();

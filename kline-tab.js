// kline-tab.js
// ════════════════════════════════════════════
//  K線圖 Tab 功能模組（純 Canvas 實作）
//  面板：K線主圖 + 成交量 + KD(9,3)
// ════════════════════════════════════════════

const KLINE = (() => {
  'use strict';

  // ── 常數 ──────────────────────────────────
  const API = 'https://api.finmindtrade.com/api/v4/data';
  const UP   = '#ff3d6a';
  const DN   = '#00ff9d';
  const UP_F = 'rgba(255,61,106,0.8)';
  const DN_F = 'rgba(0,255,157,0.8)';
  const BG   = '#060f1c';

  const MA_STYLES = [
    { n: 5,   color: '#00ff9d', w: 1.5 },
    { n: 20,  color: '#ff8c42', w: 1.5 },
    { n: 60,  color: '#00cfff', w: 1.5 },
    { n: 120, color: '#ff3d6a', w: 1.2 },
    { n: 240, color: '#a78bfa', w: 1.2 },
  ];

  const PAD = { l: 60, r: 8, t: 8, b: 4 };

  // ── 狀態 ──────────────────────────────────
  let raw       = [];   // [{date,open,high,low,close,volume}]
  let ind       = {};   // computed indicators
  let view      = { s: 0, e: 0 };
  let hIdx      = -1;
  let isDrag    = false;
  let dragRef   = {};
  let touchRef  = {};
  let rafPending = false;
  let mainCtx, volCtx, kdCtx;

  // ── 指標計算 ──────────────────────────────

  function ma(arr, n) {
    return arr.map((_, i) => {
      if (i < n - 1) return null;
      let s = 0; for (let j = i - n + 1; j <= i; j++) s += arr[j];
      return s / n;
    });
  }

  function bb(arr, n = 20, k = 2) {
    const mid = ma(arr, n);
    return arr.map((_, i) => {
      if (mid[i] === null) return { u: null, m: null, l: null };
      const sl = arr.slice(i - n + 1, i + 1);
      const m  = mid[i];
      const sd = Math.sqrt(sl.reduce((s, v) => s + (v - m) ** 2, 0) / n);
      return { u: m + k * sd, m, l: m - k * sd };
    });
  }

  function kd(data, n = 9, sm = 3) {
    const K = new Array(data.length).fill(null);
    const D = new Array(data.length).fill(null);
    let pk = 50, pd = 50;
    for (let i = n - 1; i < data.length; i++) {
      const sl = data.slice(i - n + 1, i + 1);
      const hi = Math.max(...sl.map(x => x.high));
      const lo = Math.min(...sl.map(x => x.low));
      const rsv = hi === lo ? 50 : (data[i].close - lo) / (hi - lo) * 100;
      pk = pk * (sm - 1) / sm + rsv / sm;
      pd = pd * (sm - 1) / sm + pk  / sm;
      K[i] = pk; D[i] = pd;
    }
    return { K, D };
  }

  function compute(data) {
    const cl = data.map(x => x.close);
    const b  = bb(cl);
    const kv = kd(data);
    return {
      ma5:   ma(cl, 5),
      ma20:  ma(cl, 20),
      ma60:  ma(cl, 60),
      ma120: ma(cl, 120),
      ma240: ma(cl, 240),
      bbU:   b.map(x => x.u),
      bbM:   b.map(x => x.m),
      bbL:   b.map(x => x.l),
      K: kv.K,
      D: kv.D,
    };
  }

  // ── API 取資料 ────────────────────────────

  async function fetchData(code) {
    const end   = new Date();
    const start = new Date();
    start.setDate(start.getDate() - 860);  // ~2.4年，確保 MA240 有足夠暖身期
    const fmt = d => d.toISOString().slice(0, 10);
    const token = (document.getElementById('klTokenInput')?.value || '').trim();

    const params = new URLSearchParams({
      dataset: 'TaiwanStockPrice',
      data_id: code,
      start_date: fmt(start),
      end_date: fmt(end),
    });
    if (token) params.set('token', token);

    const resp = await fetch(`${API}?${params}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const json = await resp.json();
    if (json.status !== 200) throw new Error(json.msg || 'API 錯誤');
    if (!json.data?.length) throw new Error('無資料（代碼可能有誤）');

    return json.data
      .map(r => ({
        date:   r.date.slice(0, 10),
        open:   +r.open,
        high:   +r.max,
        low:    +r.min,
        close:  +r.close,
        volume: +r.Trading_Volume,
      }))
      .filter(r => r.open > 0)
      .sort((a, b) => a.date.localeCompare(b.date));
  }

  // ── Canvas 初始化 ─────────────────────────

  function setupCanvases() {
    const wrap = document.getElementById('klChartWrap');
    if (!wrap) return;
    const W = wrap.clientWidth;
    const avail = Math.max(300, Math.min(window.innerHeight - 210, 580));

    const mH = Math.round(avail * 0.57);
    const vH = Math.round(avail * 0.22);
    const kH = Math.round(avail * 0.21);

    [['klMainCanvas', mH], ['klVolCanvas', vH], ['klKdCanvas', kH]].forEach(([id, h]) => {
      const c = document.getElementById(id);
      if (!c) return;
      c.width = W; c.height = h;
      c.style.width = W + 'px'; c.style.height = h + 'px';
    });

    mainCtx = document.getElementById('klMainCanvas')?.getContext('2d');
    volCtx  = document.getElementById('klVolCanvas')?.getContext('2d');
    kdCtx   = document.getElementById('klKdCanvas')?.getContext('2d');
  }

  // ── 畫布輔助 ──────────────────────────────

  function ca(ctx, padB = PAD.b) {
    const { width: W, height: H } = ctx.canvas;
    return {
      x1: PAD.l, x2: W - PAD.r,
      y1: PAD.t, y2: H - padB,
      W: W - PAD.l - PAD.r,
      H: H - PAD.t - padB,
    };
  }

  function py(price, mn, mx, a) {
    return a.y1 + (1 - (price - mn) / (mx - mn)) * a.H;
  }

  function ix(i, a) {
    const n = view.e - view.s + 1;
    return a.x1 + (i - view.s + 0.5) * (a.W / n);
  }

  function barW(a) {
    return a.W / (view.e - view.s + 1);
  }

  function fmtP(p) {
    return p >= 100 ? p.toFixed(1) : p.toFixed(2);
  }

  function fmtVol(v) {
    if (v >= 1e8) return (v / 1e8).toFixed(1) + '億';
    if (v >= 1e4) return (v / 1e4).toFixed(0) + '萬';
    return v.toString();
  }

  // ── 繪圖：主圖 ────────────────────────────

  function drawMain() {
    if (!mainCtx) return;
    const ctx = mainCtx;
    const a = ca(ctx);
    const { width: W, height: H } = ctx.canvas;

    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, W, H);

    // 計算 Y 範圍（涵蓋可見 OHLC + 均線 + BB）
    let mn = Infinity, mx = -Infinity;
    for (let i = view.s; i <= view.e; i++) {
      const r = raw[i]; if (!r) continue;
      mn = Math.min(mn, r.low);
      mx = Math.max(mx, r.high);
      [ind.bbU[i], ind.bbL[i]].forEach(v => {
        if (v != null) { mn = Math.min(mn, v); mx = Math.max(mx, v); }
      });
    }
    const pad = (mx - mn) * 0.04;
    mn -= pad; mx += pad;
    if (mn >= mx) mx = mn + 1;

    // Y 格線 + 標籤
    const ticks = 5;
    ctx.font = '10px Space Mono, monospace';
    ctx.textAlign = 'right';
    for (let t = 0; t <= ticks; t++) {
      const p = mn + (mx - mn) * t / ticks;
      const y = py(p, mn, mx, a);
      ctx.strokeStyle = 'rgba(0,200,255,0.06)';
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(a.x1, y); ctx.lineTo(a.x2, y); ctx.stroke();
      ctx.fillStyle = '#4a7a9a';
      ctx.fillText(fmtP(p), a.x1 - 4, y + 4);
    }

    // Clip
    ctx.save();
    ctx.beginPath(); ctx.rect(a.x1, a.y1, a.x2 - a.x1, a.y2 - a.y1); ctx.clip();

    const bw = barW(a);
    const cw = Math.max(1, bw * 0.65);

    // BB 填充
    ctx.beginPath();
    let ok = false;
    for (let i = view.s; i <= view.e; i++) {
      if (ind.bbU[i] == null) { ok = false; continue; }
      const x = ix(i, a), y = py(ind.bbU[i], mn, mx, a);
      ok ? ctx.lineTo(x, y) : ctx.moveTo(x, y); ok = true;
    }
    for (let i = view.e; i >= view.s; i--) {
      if (ind.bbL[i] == null) continue;
      ctx.lineTo(ix(i, a), py(ind.bbL[i], mn, mx, a));
    }
    ctx.closePath();
    ctx.fillStyle = 'rgba(100,140,200,0.05)';
    ctx.fill();

    // BB 線
    [
      { arr: ind.bbU, c: 'rgba(140,160,200,0.45)', d: [4, 3] },
      { arr: ind.bbL, c: 'rgba(140,160,200,0.45)', d: [4, 3] },
      { arr: ind.bbM, c: 'rgba(200,140,60,0.35)',  d: [3, 3] },
    ].forEach(({ arr, c, d }) => {
      ctx.beginPath(); ctx.strokeStyle = c; ctx.lineWidth = 1;
      ctx.setLineDash(d); let ok = false;
      for (let i = view.s; i <= view.e; i++) {
        if (arr[i] == null) { ok = false; continue; }
        const x = ix(i, a), y = py(arr[i], mn, mx, a);
        ok ? ctx.lineTo(x, y) : ctx.moveTo(x, y); ok = true;
      }
      ctx.stroke(); ctx.setLineDash([]);
    });

    // 均線
    MA_STYLES.forEach(({ n, color, w }) => {
      const arr = ind['ma' + n]; if (!arr) return;
      ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = w; let ok = false;
      for (let i = view.s; i <= view.e; i++) {
        if (arr[i] == null) { ok = false; continue; }
        const x = ix(i, a), y = py(arr[i], mn, mx, a);
        ok ? ctx.lineTo(x, y) : ctx.moveTo(x, y); ok = true;
      }
      ctx.stroke();
    });

    // 蠟燭
    for (let i = view.s; i <= view.e; i++) {
      const r = raw[i]; if (!r) continue;
      const x  = ix(i, a);
      const up = r.close >= r.open;
      const col = up ? UP : DN;
      const yH = py(r.high, mn, mx, a), yL = py(r.low, mn, mx, a);
      const yO = py(r.open, mn, mx, a), yC = py(r.close, mn, mx, a);
      const bT = Math.min(yO, yC), bH = Math.max(1, Math.abs(yC - yO));

      // 影線
      ctx.strokeStyle = col; ctx.lineWidth = Math.max(1, cw * 0.12);
      ctx.beginPath(); ctx.moveTo(x, yH); ctx.lineTo(x, yL); ctx.stroke();

      // 實體
      if (cw >= 2) {
        ctx.fillStyle = up ? UP_F : DN_F;
        ctx.fillRect(x - cw / 2, bT, cw, bH);
        if (!up) {   // 跌：實心綠
          // already filled
        } else {     // 漲：空心紅加框
          ctx.strokeStyle = UP; ctx.lineWidth = 1;
          ctx.strokeRect(x - cw / 2, bT, cw, bH);
        }
      } else {
        ctx.fillStyle = col;
        ctx.fillRect(x - 0.5, Math.min(yO, yC), 1, bH);
      }
    }

    // 十字線
    if (hIdx >= view.s && hIdx <= view.e) {
      const x = ix(hIdx, a);
      ctx.strokeStyle = 'rgba(0,200,255,0.35)'; ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(x, a.y1); ctx.lineTo(x, a.y2); ctx.stroke();
      ctx.setLineDash([]);
    }

    ctx.restore();

    // 框線
    ctx.strokeStyle = 'rgba(0,200,255,0.12)'; ctx.lineWidth = 1;
    ctx.strokeRect(a.x1, a.y1, a.x2 - a.x1, a.y2 - a.y1);

    // 左上角均線圖例
    if (bw >= 6) {
      let lx = a.x1 + 4, ly = a.y1 + 11;
      ctx.font = '9px Space Mono, monospace'; ctx.textAlign = 'left';
      MA_STYLES.forEach(({ n, color }) => {
        const v = hIdx >= 0 ? ind['ma' + n][hIdx] : ind['ma' + n][view.e];
        if (v == null) return;
        ctx.fillStyle = color;
        ctx.fillText(`MA${n}:${fmtP(v)}`, lx, ly);
        lx += ctx.measureText(`MA${n}:${fmtP(v)}`).width + 8;
        if (lx > a.x2 - 60) { lx = a.x1 + 4; ly += 12; }
      });
    }
  }

  // ── 繪圖：成交量 ──────────────────────────

  function drawVol() {
    if (!volCtx) return;
    const ctx = volCtx; const a = ca(ctx);
    const { width: W, height: H } = ctx.canvas;
    ctx.fillStyle = BG; ctx.fillRect(0, 0, W, H);

    let vMax = 0;
    for (let i = view.s; i <= view.e; i++) if (raw[i]) vMax = Math.max(vMax, raw[i].volume);
    if (!vMax) { ctx.strokeStyle = 'rgba(0,200,255,0.12)'; ctx.strokeRect(a.x1, a.y1, a.x2 - a.x1, a.y2 - a.y1); return; }

    ctx.font = '9px Space Mono, monospace'; ctx.textAlign = 'right'; ctx.fillStyle = '#4a7a9a';
    ctx.fillText(fmtVol(vMax), a.x1 - 3, a.y1 + 9);

    ctx.save(); ctx.beginPath(); ctx.rect(a.x1, a.y1, a.x2 - a.x1, a.y2 - a.y1); ctx.clip();
    const bw = barW(a); const ew = Math.max(1, bw * 0.65);

    for (let i = view.s; i <= view.e; i++) {
      const r = raw[i]; if (!r) continue;
      const x  = ix(i, a);
      const bH = (r.volume / vMax) * a.H;
      const up = r.close >= r.open;
      ctx.fillStyle = up ? 'rgba(255,61,106,0.55)' : 'rgba(0,255,157,0.55)';
      ctx.fillRect(x - ew / 2, a.y2 - bH, ew, bH);
    }

    // 十字線
    if (hIdx >= view.s && hIdx <= view.e) {
      const x = ix(hIdx, a);
      ctx.strokeStyle = 'rgba(0,200,255,0.3)'; ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(x, a.y1); ctx.lineTo(x, a.y2); ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.restore();

    ctx.font = '9px Space Mono, monospace'; ctx.textAlign = 'left'; ctx.fillStyle = '#2d5a7a';
    ctx.fillText('VOL', a.x1 + 4, a.y1 + 9);
    ctx.strokeStyle = 'rgba(0,200,255,0.12)'; ctx.lineWidth = 1;
    ctx.strokeRect(a.x1, a.y1, a.x2 - a.x1, a.y2 - a.y1);
  }

  // ── 繪圖：KD ─────────────────────────────

  function drawKD() {
    if (!kdCtx) return;
    const ctx = kdCtx; const padB = 18; const a = ca(ctx, padB);
    const { width: W, height: H } = ctx.canvas;
    ctx.fillStyle = BG; ctx.fillRect(0, 0, W, H);

    const toY = v => a.y1 + (1 - v / 100) * a.H;

    // 格線 & 標籤
    [80, 50, 20].forEach(lv => {
      const y = toY(lv);
      ctx.strokeStyle = lv === 50 ? 'rgba(0,200,255,0.06)' : 'rgba(0,200,255,0.1)';
      ctx.setLineDash(lv === 50 ? [] : [3, 3]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(a.x1, y); ctx.lineTo(a.x2, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = '8px Space Mono, monospace'; ctx.textAlign = 'right'; ctx.fillStyle = '#3d6a8a';
      ctx.fillText(lv, a.x1 - 2, y + 3);
    });

    ctx.save(); ctx.beginPath(); ctx.rect(a.x1, a.y1, a.x2 - a.x1, a.y2 - a.y1); ctx.clip();

    [
      { arr: ind.K, c: '#00cfff', d: null, w: 1.5 },
      { arr: ind.D, c: '#ff3d6a', d: [4, 2], w: 1.5 },
    ].forEach(({ arr, c, d, w }) => {
      if (!arr) return;
      ctx.beginPath(); ctx.strokeStyle = c; ctx.lineWidth = w;
      if (d) ctx.setLineDash(d); let ok = false;
      for (let i = view.s; i <= view.e; i++) {
        if (arr[i] == null) { ok = false; continue; }
        const x = ix(i, a), y = toY(arr[i]);
        ok ? ctx.lineTo(x, y) : ctx.moveTo(x, y); ok = true;
      }
      ctx.stroke(); ctx.setLineDash([]);
    });

    // 十字線
    if (hIdx >= view.s && hIdx <= view.e) {
      const x = ix(hIdx, a);
      ctx.strokeStyle = 'rgba(0,200,255,0.3)'; ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(x, a.y1); ctx.lineTo(x, a.y2); ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.restore();

    // KD 數值
    const di = hIdx >= 0 && hIdx < raw.length ? hIdx : view.e;
    const kv = ind.K[di], dv = ind.D[di];
    ctx.font = '9px Space Mono, monospace'; ctx.textAlign = 'left';
    if (kv != null) {
      ctx.fillStyle = '#00cfff'; ctx.fillText(`K:${kv.toFixed(1)}`, a.x1 + 4, a.y1 + 9);
      ctx.fillStyle = '#ff3d6a'; ctx.fillText(`D:${dv != null ? dv.toFixed(1) : '—'}`, a.x1 + 54, a.y1 + 9);
    } else {
      ctx.fillStyle = '#2d5a7a'; ctx.fillText('KD(9,3)', a.x1 + 4, a.y1 + 9);
    }

    // X 軸日期
    const n = view.e - view.s + 1;
    const step = Math.max(1, Math.round(n / 5));
    ctx.font = '8px Space Mono, monospace'; ctx.textAlign = 'center'; ctx.fillStyle = '#4a7a9a';
    for (let i = view.s; i <= view.e; i += step) {
      if (i >= raw.length) break;
      ctx.fillText(raw[i].date.slice(5), ix(i, a), H - 3);
    }

    ctx.strokeStyle = 'rgba(0,200,255,0.12)'; ctx.lineWidth = 1;
    ctx.strokeRect(a.x1, a.y1, a.x2 - a.x1, a.y2 - a.y1);
  }

  // ── Stat 卡更新 ───────────────────────────

  function updateStat() {
    const el = document.getElementById('klStatBar');
    if (!el || !raw.length) return;
    const i   = hIdx >= 0 && hIdx < raw.length ? hIdx : raw.length - 1;
    const r   = raw[i];
    const pre = i > 0 ? raw[i - 1].close : r.open;
    const chg = r.close - pre;
    const pct = (chg / pre * 100).toFixed(2);
    const isUp = chg >= 0;
    const f = v => v == null ? '—' : fmtP(v);
    const bU = ind.bbU[i], bL = ind.bbL[i], bM = ind.bbM[i];
    const bbW = (bU != null && bL != null && bM) ? ((bU - bL) / bM * 100).toFixed(1) + '%' : '—';
    const kv = ind.K[i], dv = ind.D[i];

    const cells = [
      { l: '收盤', v: r.close.toLocaleString(), c: isUp ? UP : DN },
      { l: '漲跌%', v: `${isUp ? '▲' : '▼'}${Math.abs(pct)}%`, c: isUp ? UP : DN },
      { l: 'MA5',  v: f(ind.ma5[i]),  c: '#00ff9d' },
      { l: 'MA20', v: f(ind.ma20[i]), c: '#ff8c42' },
      { l: 'MA60', v: f(ind.ma60[i]), c: '#00cfff' },
      { l: 'MA120',v: f(ind.ma120[i]),c: '#ff3d6a' },
      { l: 'MA240',v: f(ind.ma240[i]),c: '#a78bfa' },
      { l: 'BB寬', v: bbW, c: '#7ab3cc' },
      { l: 'K值',  v: kv != null ? kv.toFixed(1) : '—', c: '#00cfff' },
      { l: 'D值',  v: dv != null ? dv.toFixed(1) : '—', c: '#ff3d6a' },
    ];
    el.innerHTML = cells.map(({ l, v, c }) =>
      `<div class="kl-stat-cell">
        <div class="kl-stat-lbl">${l}</div>
        <div class="kl-stat-val" style="color:${c}">${v}</div>
      </div>`).join('');
  }

  // ── Tooltip ───────────────────────────────

  function showTip(e, i) {
    const tip = document.getElementById('klTooltip');
    if (!tip || i < 0 || i >= raw.length) { if (tip) tip.style.display = 'none'; return; }
    const r = raw[i]; const up = r.close >= r.open;
    const col = up ? UP : DN;
    const f = v => v == null ? '—' : fmtP(v);
    const kv = ind.K[i], dv = ind.D[i];

    tip.innerHTML = `
      <div style="font-family:'Space Mono',monospace;font-size:.58rem;color:#7ab3cc;margin-bottom:3px">${r.date}</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:1px 10px;font-size:.6rem;margin-bottom:4px">
        <span style="color:#4a7a9a">開</span><span style="color:${col}">${r.open.toFixed(2)}</span>
        <span style="color:#4a7a9a">高</span><span style="color:${col}">${r.high.toFixed(2)}</span>
        <span style="color:#4a7a9a">低</span><span style="color:${col}">${r.low.toFixed(2)}</span>
        <span style="color:#4a7a9a">收</span><span style="color:${col};font-weight:700">${r.close.toFixed(2)}</span>
      </div>
      <div style="font-size:.57rem;line-height:1.8">
        <span style="color:#00ff9d">MA5:${f(ind.ma5[i])}</span> <span style="color:#ff8c42">MA20:${f(ind.ma20[i])}</span><br>
        <span style="color:#00cfff">MA60:${f(ind.ma60[i])}</span> <span style="color:#ff3d6a">MA120:${f(ind.ma120[i])}</span><br>
        <span style="color:#a78bfa">MA240:${f(ind.ma240[i])}</span>
        ${ind.bbU[i] != null ? `<br><span style="color:#8aa">BB:${fmtP(ind.bbL[i])}~${fmtP(ind.bbU[i])}</span>` : ''}
        ${kv != null ? `<br><span style="color:#00cfff">K:${kv.toFixed(1)}</span> <span style="color:#ff3d6a">D:${dv != null ? dv.toFixed(1) : '—'}</span>` : ''}
      </div>`;
    tip.style.display = 'block';

    // 定位
    const canv = document.getElementById('klMainCanvas');
    const rect = canv.getBoundingClientRect();
    const lx = e.clientX - rect.left + 14;
    tip.style.left = (lx + 150 > rect.width ? lx - 170 : lx) + 'px';
    tip.style.top  = '8px';
  }

  // ── 觸發重繪 ──────────────────────────────

  function redraw() {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => {
      drawMain(); drawVol(); drawKD(); updateStat();
      rafPending = false;
    });
  }

  // ── 互動事件 ──────────────────────────────

  function setupInteraction() {
    const mc = document.getElementById('klMainCanvas');
    if (!mc || mc._klineInit) return;
    mc._klineInit = true;

    // 滾輪縮放
    mc.addEventListener('wheel', e => {
      e.preventDefault();
      const a = ca(mainCtx);
      const frac = Math.max(0, Math.min(1, (e.clientX - mc.getBoundingClientRect().left - a.x1) / a.W));
      const n = view.e - view.s + 1;
      const cursor = view.s + frac * n;
      const factor = e.deltaY > 0 ? 1.15 : 0.87;
      const newN = Math.max(20, Math.min(raw.length, Math.round(n * factor)));
      let s = Math.round(cursor - frac * newN);
      let e2 = s + newN - 1;
      if (e2 >= raw.length) { e2 = raw.length - 1; s = e2 - newN + 1; }
      if (s < 0) { s = 0; e2 = Math.min(raw.length - 1, newN - 1); }
      view.s = s; view.e = e2;
      redraw();
    }, { passive: false });

    // 滑鼠拖曳
    mc.addEventListener('mousedown', e => {
      isDrag = true; mc.style.cursor = 'grabbing';
      dragRef = { x: e.clientX, s: view.s, e: view.e };
    });

    const onMove = e => {
      if (isDrag) {
        const a = ca(mainCtx);
        const bw = a.W / (dragRef.e - dragRef.s + 1);
        const sh = Math.round((dragRef.x - e.clientX) / bw);
        const n = dragRef.e - dragRef.s + 1;
        let s = dragRef.s + sh, ev = dragRef.e + sh;
        if (ev >= raw.length) { ev = raw.length - 1; s = ev - n + 1; }
        if (s < 0) { s = 0; ev = Math.min(raw.length - 1, n - 1); }
        view.s = s; view.e = ev; hIdx = -1;
        redraw(); return;
      }
      const rect = mc.getBoundingClientRect();
      if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) {
        if (hIdx !== -1) { hIdx = -1; document.getElementById('klTooltip').style.display = 'none'; redraw(); }
        return;
      }
      const a = ca(mainCtx);
      const rel = e.clientX - rect.left - a.x1;
      const bw = a.W / (view.e - view.s + 1);
      const ni = Math.min(view.e, Math.max(view.s, Math.round(rel / bw - 0.5) + view.s));
      if (ni !== hIdx) { hIdx = ni; redraw(); }
      showTip(e, hIdx);
    };

    const onUp = () => { isDrag = false; mc.style.cursor = 'crosshair'; };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    mc.addEventListener('mouseleave', () => {
      if (!isDrag) { hIdx = -1; document.getElementById('klTooltip').style.display = 'none'; redraw(); }
    });

    // 觸控
    mc.addEventListener('touchstart', e => {
      e.preventDefault();
      if (e.touches.length === 1) {
        touchRef = { type: 'pan', x: e.touches[0].clientX, s: view.s, e: view.e };
      } else {
        const dx = e.touches[0].clientX - e.touches[1].clientX;
        const dy = e.touches[0].clientY - e.touches[1].clientY;
        touchRef = { type: 'pinch', dist: Math.hypot(dx, dy), s: view.s, e: view.e };
      }
    }, { passive: false });

    mc.addEventListener('touchmove', e => {
      e.preventDefault();
      const a = ca(mainCtx);
      if (touchRef.type === 'pan' && e.touches.length === 1) {
        const bw = a.W / (touchRef.e - touchRef.s + 1);
        const sh = Math.round((touchRef.x - e.touches[0].clientX) / bw);
        const n = touchRef.e - touchRef.s + 1;
        let s = touchRef.s + sh, ev = touchRef.e + sh;
        if (ev >= raw.length) { ev = raw.length - 1; s = ev - n + 1; }
        if (s < 0) { s = 0; ev = Math.min(raw.length - 1, n - 1); }
        view.s = s; view.e = ev; redraw();
      } else if (touchRef.type === 'pinch' && e.touches.length === 2) {
        const dx = e.touches[0].clientX - e.touches[1].clientX;
        const dy = e.touches[0].clientY - e.touches[1].clientY;
        const dist = Math.hypot(dx, dy);
        const n0 = touchRef.e - touchRef.s + 1;
        const newN = Math.max(20, Math.min(raw.length, Math.round(n0 * touchRef.dist / dist)));
        const mid = Math.round((touchRef.s + touchRef.e) / 2);
        let s = mid - Math.floor(newN / 2);
        let ev = s + newN - 1;
        if (ev >= raw.length) { ev = raw.length - 1; s = ev - newN + 1; }
        if (s < 0) { s = 0; ev = Math.min(raw.length - 1, newN - 1); }
        view.s = s; view.e = ev; redraw();
      }
    }, { passive: false });

    mc.addEventListener('touchend', () => { touchRef = {}; });
  }

  // ── 日期區間 ──────────────────────────────

  function setDateRange(bars) {
    if (!raw.length) return;
    view.e = raw.length - 1;
    view.s = Math.max(0, raw.length - bars);
    document.querySelectorAll('.kl-range-btn').forEach(b => b.classList.remove('active'));
    const map = { 65: 'klRange3M', 130: 'klRange6M', 250: 'klRange1Y', 500: 'klRange2Y' };
    if (map[bars]) document.getElementById(map[bars])?.classList.add('active');
    redraw();
  }

  // ── 候選股切換列 ──────────────────────────

  function renderSwitcher() {
    const sw = document.getElementById('klSwitcher');
    if (!sw) return;
    const stocks = (window.scanResults || []).slice(0, 10);
    if (!stocks.length) { sw.style.display = 'none'; return; }
    sw.style.display = 'flex';
    sw.innerHTML = stocks.map((s, i) => {
      const up = s.chg >= 0;
      return `<div class="stock-btn ${i === 0 ? 'active' : ''}" onclick="KLINE.loadFromScan('${s.code}',this)">
        <div class="sb-code">${s.code}</div>
        <div class="sb-name">${s.name}</div>
        <div class="sb-chg ${up ? 'up' : 'dn'}">${up ? '▲' : '▼'}${Math.abs(s.chg_pct).toFixed(1)}%</div>
      </div>`;
    }).join('');
  }

  // ── 代碼提示 ──────────────────────────────

  function codeHint(val) {
    const hint = document.getElementById('klHintList');
    if (!hint) return;
    const q = val.trim();
    if (!q || !(window.allStockData?.length)) { hint.style.display = 'none'; return; }
    const m = window.allStockData.filter(s => s.code.startsWith(q) || s.name.includes(q)).slice(0, 6);
    if (!m.length) { hint.style.display = 'none'; return; }
    hint.innerHTML = m.map(s => `
      <div onclick="KLINE.pickHint('${s.code}')"
        style="padding:7px 12px;font-size:.7rem;cursor:pointer;
               display:flex;justify-content:space-between;align-items:center;
               border-bottom:1px solid var(--border);">
        <span>
          <span style="font-family:'Space Mono',monospace;color:var(--c1);margin-right:6px">${s.code}</span>
          <span style="color:var(--t1)">${s.name}</span>
        </span>
        <span style="font-size:.6rem;color:var(--t3)">${s.market || ''}</span>
      </div>`).join('');
    hint.style.display = 'block';
  }

  // ── 主入口：載入 K 線 ─────────────────────

  async function load(code) {
    if (!code) return;
    const inp = document.getElementById('klCodeInput');
    if (inp) inp.value = code;

    const known = (window.allStockData || []).find(x => x.code === code) ||
                  (window.scanResults  || []).find(x => x.code === code);

    document.getElementById('klTitle').textContent = known ? `${code} ${known.name}` : code;
    document.getElementById('klSub').textContent   = '拉取 FinMind 資料...';
    document.getElementById('klTitleRow').style.display = 'flex';
    document.getElementById('klLoading').style.display  = 'flex';
    document.getElementById('klError').style.display    = 'none';
    document.getElementById('klEmpty').style.display    = 'none';
    document.getElementById('klChartWrap').style.display = 'none';
    document.getElementById('klStatBar').style.display   = 'none';

    try {
      raw = await fetchData(code);
      ind = compute(raw);

      // 預設顯示最近 250 筆（約 1 年）
      const defN = Math.min(250, raw.length);
      view.e = raw.length - 1;
      view.s = Math.max(0, raw.length - defN);
      hIdx = -1;

      document.getElementById('klLoading').style.display  = 'none';
      document.getElementById('klChartWrap').style.display = 'block';
      document.getElementById('klStatBar').style.display   = 'grid';
      document.getElementById('klSub').textContent =
        `${raw[0].date} ～ ${raw[raw.length - 1].date}　共 ${raw.length} 筆`;

      // 更新日期按鈕 active 狀態
      document.querySelectorAll('.kl-range-btn').forEach(b => b.classList.remove('active'));
      document.getElementById('klRange1Y')?.classList.add('active');

      setupCanvases();
      setupInteraction();
      redraw();

    } catch (err) {
      document.getElementById('klLoading').style.display = 'none';
      document.getElementById('klError').style.display   = 'flex';
      document.getElementById('klErrorMsg').textContent  = err.message;
    }
  }

  // ── Public API ────────────────────────────

  function search() {
    const inp = document.getElementById('klCodeInput');
    if (!inp) return;
    const code = inp.value.trim().replace(/\D/g, '');
    if (!code) return;
    const hint = document.getElementById('klHintList');
    if (hint) hint.style.display = 'none';
    inp.blur();
    load(code);
  }

  function loadFromScan(code, btn) {
    document.querySelectorAll('#klSwitcher .stock-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    load(code);
  }

  function pickHint(code) {
    const inp = document.getElementById('klCodeInput');
    if (inp) inp.value = code;
    const hint = document.getElementById('klHintList');
    if (hint) hint.style.display = 'none';
    search();
  }

  function onTabActivated(selectedStock) {
    renderSwitcher();
    // 若有目前選股且尚未載入，自動帶入
    if (selectedStock && raw.length === 0) {
      load(selectedStock.code);
    }
  }

  function onWindowResize() {
    if (!raw.length) return;
    setupCanvases(); redraw();
  }

  window.addEventListener('resize', onWindowResize);

  return { load, search, loadFromScan, pickHint, setDateRange, onTabActivated, codeHint, renderSwitcher };
})();

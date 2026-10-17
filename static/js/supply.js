// サプライチェーン: 素材の有向グラフを左(上流)→右(下流)に描く。仕様は docs/DESIGN_supply.md
const SUPPLY_LIBS = [
  "https://unpkg.com/cytoscape@3.30.2/dist/cytoscape.min.js",
  "https://unpkg.com/elkjs@0.9.3/lib/elk.bundled.js",
];
const SUPPLY_KIND = {raw: "原料", mid: "中間財", prod: "最終製品", recycled: "リサイクル資源"};
const SUPPLY_COLOR = {
  raw:  {fill: "#fdf1de", line: "#b37314"},
  mid:  {fill: "#e3f0f4", line: "#2f7489"},
  prod: {fill: "#e6f2e0", line: "#4a8a37"},
  recycled: {fill: "#efe9f8", line: "#6f4fb0"},
};
const SUPPLY_NODE_H = 28;
// 回収される素材の印（循環の矢印）。箱の右端に描く。凡例も同じ形（app.css の .k-ret）
const SUPPLY_REC_ICON = "data:image/svg+xml;utf8," + encodeURIComponent(
  "<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12' fill='none' stroke='#6f4fb0' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'>"
  + "<path d='M10 6a4 4 0 0 1-6.8 2.8'/><path d='M2 6a4 4 0 0 1 6.8-2.8'/><path d='M8.8 1.2v2h-2'/><path d='M3.2 10.8v-2h2'/></svg>");
const SUPPLY_REC_W = 14;  // 印のぶん箱を広げる
const SUPPLY_MAX_ZOOM = 3;
let supplyInit = null;  // Promise。ライブラリとグラフの読込は一度だけ
let supply = null;      // {cy, byId, parents, kids, retFrom, retTo, selected, co}
// 企業: 能力シェアの色。上位 5 社に固定順で振り、6 位から下はその他の灰色
const SUPPLY_SHARE_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"];
const SUPPLY_SHARE_OTHER = "#b7c0bd";
const SUPPLY_STATUS = {listed: "上場", private: "非上場", foreign: "海外"};
let supplyGroup = false;  // 能力シェアを親会社でまとめる

function supplySelectedId(){ return supply?.selected || ""; }

function supplyLoadScript(src){
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error(`load ${src}`));
    document.head.appendChild(s);
  });
}

async function loadSupply(sel){
  if(!supplyInit) supplyInit = supplySetup().catch(e => {
    supplyInit = null;
    $("#supply-msg").textContent = `読み込めませんでした (${e.message})`;
    throw e;
  });
  try { await supplyInit; } catch(e){ return; }
  // 他タブにいる間は display:none で大きさ 0。戻ったときに測り直す
  supply.cy.resize();
  if(sel && supply.byId[sel]) supplySelect(sel, true);
  else if(sel && supply.co.byId[sel]) supplySelectCompany(sel, true);
}

async function supplySetup(){
  const [graph, companies] = await Promise.all([
    api("/api/supply/graph"), api("/api/supply/companies"), ...SUPPLY_LIBS.map(supplyLoadScript)]);
  const byId = {}, parents = {}, kids = {}, retFrom = {}, retTo = {};
  graph.materials.forEach(m => { byId[m.id] = m; parents[m.id] = []; kids[m.id] = []; retFrom[m.id] = []; retTo[m.id] = []; });
  graph.flows.forEach(([a, b]) => { kids[a].push(b); parents[b].push(a); });
  // 回収（使い終わった製品 → リサイクル資源）。循環になるので線にせず、配置と祖先・子孫にも使わない。
  // 回収される素材の箱に印を付け、選んだときに相手の箱を紫の枠で示す
  (graph.returns || []).forEach(([a, b]) => { retTo[a].push(b); retFrom[b].push(a); });

  // 箱の幅はラベルの文字幅で決めてから ELK に渡す
  const font = `600 12px ${getComputedStyle(document.body).fontFamily}`;
  if(document.fonts?.ready) await document.fonts.ready;
  const ctx = document.createElement("canvas").getContext("2d");
  ctx.font = font;
  graph.materials.forEach(m => { m.w = Math.ceil(ctx.measureText(m.name).width) + 20 + (retTo[m.id].length ? SUPPLY_REC_W : 0); });

  const cy = cytoscape({
    container: $("#supply-map"),
    elements: [
      // 箱から指を動かしてもマップを動かす（既定では箱の上で始めたドラッグは何もしない）。押せば選ぶのは変わらない
      ...graph.materials.map(m => ({data: {id: m.id, label: m.name, kind: m.kind, w: m.w, rec: retTo[m.id].length > 0},
                                    pannable: true})),
      ...graph.flows.map(([a, b]) => ({data: {id: `${a}>${b}`, source: a, target: b}})),
    ],
    style: supplyStyle(font),
    layout: {name: "preset"},
    minZoom: 0.2, maxZoom: SUPPLY_MAX_ZOOM,
    boxSelectionEnabled: false, autoungrabify: true,
  });
  $("#supply-msg").remove();
  supply = {cy, byId, parents, kids, retFrom, retTo, selected: "", co: supplyCompanyIndex(companies), countries: {}};
  await supplyLayout();
  cy.fit(undefined, 16);

  // ページがピンチで拡大されているとマップが画面を埋め、2 本指をマップが取ってページを縮小できなくなる。
  // 拡大中はマップの拡大・移動を止め、ピンチとスクロールをブラウザに渡す
  const vv = window.visualViewport;
  if(vv){
    const sync = () => {
      const free = vv.scale <= 1.01;
      cy.userZoomingEnabled(free);
      cy.userPanningEnabled(free);
    };
    vv.addEventListener("resize", sync);
    sync();
  }

  cy.on("tap", "node", e => supplySelect(e.target.id(), false));
  cy.on("tap", e => { if(e.target === cy) supplyClear(); });
  $("#supply-reset").onclick = () => { supplyClear(); $("#supply-q").value = ""; cy.animate({fit: {padding: 16}}, {duration: 300}); };
  $("#supply-merge").onchange = async () => { await supplyLayout(); cy.fit(undefined, 16); };
  // 候補は素材名、社名（上場は「社名 4 桁コード」）。証券コードだけでも引ける
  const names = $("#supply-names"), index = {}, cos = companies.companies;
  const addName = (label, id) => {
    index[label] = id;
    const o = document.createElement("option");
    o.value = label;
    names.appendChild(o);
  };
  graph.materials.forEach(m => addName(m.name, m.id));
  cos.forEach(c => addName(c.code ? `${c.name} ${c.code.slice(0, 4)}` : c.name, c.id));
  $("#supply-q").onchange = e => {
    const v = e.target.value.trim(), up = v.toUpperCase();
    if(!v) return;
    const id = index[v]
      || cos.find(c => c.code && /^[0-9][0-9A-Z]{3}$/.test(up) && c.code.slice(0, 4) === up)?.id
      || graph.materials.find(m => m.name.includes(v))?.id
      || cos.find(c => c.name.includes(v))?.id;
    if(!id) return;
    if(supply.byId[id]) supplySelect(id, true);
    else supplySelectCompany(id, true);
  };
  supplyPanel();
  supplySheetSetup();
}

function supplyStyle(font){
  const fam = font.replace(/^600 12px /, "");
  return [
    {selector: "core", style: {"active-bg-opacity": 0}},
    {selector: "node", style: {
      shape: "round-rectangle", width: "data(w)", height: SUPPLY_NODE_H,
      label: "data(label)", "text-valign": "center", "text-halign": "center",
      "font-family": fam, "font-size": 12, "font-weight": 600, color: "#222",
      "background-color": n => SUPPLY_COLOR[n.data("kind")].fill,
      "border-width": 1.5, "border-color": n => SUPPLY_COLOR[n.data("kind")].line,
      "z-index-compare": "manual", "z-index": 10,
      "transition-property": "opacity", "transition-duration": "150ms",
    }},
    {selector: "edge", style: {
      width: 1.4, "line-color": "#b7c0bd", "target-arrow-color": "#b7c0bd",
      "target-arrow-shape": "triangle", "arrow-scale": 0.8,
      "curve-style": "straight",
      "z-index-compare": "manual", "z-index": 1,
      // 強調した線は箱より手前に描くので、端のタップを線が取ってしまう。線は押させない
      events: "no",
    }},
    // ELK の曲がり角をそのまま使う。始点と終点も ELK が決めた位置
    {selector: "edge[?routed]", style: {
      "curve-style": e => e.data("sw").length ? "segments" : "straight",
      "segment-weights": e => e.data("sw").length ? e.data("sw") : [0.5],
      "segment-distances": e => e.data("sd").length ? e.data("sd") : [0],
      "edge-distances": "endpoints",
      "source-endpoint": "data(se)", "target-endpoint": "data(te)",
    }},
    {selector: "node.dim", style: {opacity: 0.25}},
    {selector: "edge.dim", style: {opacity: 0.12}},
    {selector: "edge.hl", style: {
      width: 2.6, "line-color": "#0077b6", "target-arrow-color": "#0077b6", "z-index": 20,
    }},
    {selector: "node.sel", style: {"border-width": 3, "border-color": "#0077b6"}},
    // 回収される素材: 右端に印、文字は印のぶん左へ
    {selector: "node[?rec]", style: {
      "background-image": SUPPLY_REC_ICON, "background-width": 11, "background-height": 11,
      "background-position-x": "100%", "background-offset-x": -6, "background-clip": "none",
      "text-margin-x": -SUPPLY_REC_W / 2,
    }},
    // 選んだ素材の回収の相手（回収元か、使い終わったときの行き先）
    {selector: "node.ret", style: {"border-width": 3, "border-color": "#6f4fb0", opacity: 1}},
  ];
}

async function supplyLayout(){
  const {cy} = supply;
  const merge = $("#supply-merge").checked;
  const res = await new ELK().layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.layered.mergeEdges": String(merge),
      "elk.spacing.nodeNode": "12",
      "elk.layered.spacing.nodeNodeBetweenLayers": "36",
      "elk.layered.spacing.edgeNodeBetweenLayers": "12",
    },
    children: cy.nodes().map(n => ({id: n.id(), width: n.data("w"), height: SUPPLY_NODE_H})),
    edges: cy.edges().map(e => ({id: e.id(), sources: [e.source().id()], targets: [e.target().id()]})),
  });
  const center = {};
  cy.batch(() => {
    res.children.forEach(c => {
      center[c.id] = {x: c.x + c.width / 2, y: c.y + c.height / 2};
      cy.getElementById(c.id).position(center[c.id]);
    });
    res.edges.forEach(e => {
      const sec = e.sections?.[0];
      if(!sec) return;
      const el = cy.getElementById(e.id);
      const s = sec.startPoint, t = sec.endPoint;
      const sc = center[el.source().id()], tc = center[el.target().id()];
      // Cytoscape の segments は始点→終点の線分に対する (比率, 垂直距離) で曲がり角を持つ
      const dx = t.x - s.x, dy = t.y - s.y, len2 = dx * dx + dy * dy || 1, len = Math.sqrt(len2);
      const sw = [], sd = [];
      (sec.bendPoints || []).forEach(b => {
        const px = b.x - s.x, py = b.y - s.y;
        sw.push((px * dx + py * dy) / len2);
        sd.push((py * dx - px * dy) / len);
      });
      el.data({
        routed: true, sw, sd,
        se: `${s.x - sc.x}px ${s.y - sc.y}px`,
        te: `${t.x - tc.x}px ${t.y - tc.y}px`,
      });
    });
  });
}

function supplyWalk(start, next){
  const seen = new Set(), st = [start];
  while(st.length) next[st.pop()].forEach(m => { if(!seen.has(m)){ seen.add(m); st.push(m); } });
  return seen;
}

function supplySelect(id, fly){
  const {cy, parents, kids, retFrom, retTo} = supply;
  supply.selected = id;
  const up = supplyWalk(id, parents), down = supplyWalk(id, kids);
  const onUp = n => n === id || up.has(n), onDown = n => n === id || down.has(n);
  cy.batch(() => {
    cy.elements().removeClass("dim hl sel ret");
    cy.nodes().forEach(n => { if(!onUp(n.id()) && !onDown(n.id())) n.addClass("dim"); });
    cy.edges().forEach(e => {
      const a = e.source().id(), b = e.target().id();
      e.addClass((up.has(a) && onUp(b)) || (onDown(a) && down.has(b)) ? "hl" : "dim");
    });
    // 回収の相手は経路の外でも薄くせず紫の枠で残す（回収をたどって広げない。たどると輪を一周して系統全部が残る）
    [...retFrom[id], ...retTo[id]].forEach(n => cy.getElementById(n).removeClass("dim").addClass("ret"));
    cy.getElementById(id).addClass("sel");
  });
  supplySheet(true);
  if(fly) supplyFly(cy.nodes().not(".dim"), 350, cy.getElementById(id));
  else supplyReveal(cy.getElementById(id));
  supplyPanel(up, down);
  writeRoute("supply", id);
  supplyCountries(id);
}

function supplySelectCompany(id, fly){
  const {cy} = supply, made = new Set((supply.co.makesByCo[id] || []).map(x => x.material));
  supply.selected = id;
  cy.batch(() => {
    cy.elements().removeClass("dim hl sel ret");
    cy.nodes().forEach(n => n.addClass(made.has(n.id()) ? "sel" : "dim"));
    cy.edges().addClass("dim");
  });
  supplySheet(true);
  if(fly && made.size) supplyFly(cy.nodes().not(".dim"), 350);
  supplyCompanyPanel(id);
  writeRoute("supply", id);
}

function supplyClear(){
  if(!supply) return;
  supply.selected = "";
  supply.cy.elements().removeClass("dim hl sel ret");
  supplySheet(false);
  supplyPanel();
  writeRoute("supply", "");
}

// ---- スマホの詳細シート ----
// 900px 未満は詳細パネルを下から出すシート（画面の下 45%）にする。中身はタブで短いので広げる状態は持たない
const SUPPLY_SHEET_MQ = matchMedia("(max-width:899px)");
const SUPPLY_SHEET_H = 0.45;  // app.css の --supply-sheet と同じ

function supplySheet(open){
  $("#supply-panel").classList.toggle("open", open);
}

function supplySheetSetup(){
  const p = $("#supply-panel"), body = $("#supply-body"), grip = $("#supply-grip");
  // 下へスワイプで閉じる。つまみからはいつでも、中身からは一番上までスクロールしているときだけ
  let drag = null;
  p.addEventListener("touchstart", e => {
    if(!SUPPLY_SHEET_MQ.matches || e.touches.length !== 1) return;
    if(!grip.contains(e.target) && body.scrollTop > 0) return;
    drag = {y0: e.touches[0].clientY, dy: 0, grip: grip.contains(e.target)};
  }, {passive: true});
  p.addEventListener("touchmove", e => {
    if(!drag) return;
    drag.dy = e.touches[0].clientY - drag.y0;
    // 中身で上へ指を動かすのはスクロール
    if(drag.dy < 0 && !drag.grip){ drag = null; p.style.transform = ""; return; }
    e.preventDefault();
    p.style.transition = "none";
    p.style.transform = `translateY(${Math.max(0, drag.dy)}px)`;
  }, {passive: false});
  const end = () => {
    if(!drag) return;
    const {dy} = drag;
    drag = null;
    p.style.transition = p.style.transform = "";
    if(dy > 40) supplySheet(false);
  };
  p.addEventListener("touchend", end);
  p.addEventListener("touchcancel", end);
  grip.onclick = () => supplySheet(false);
  SUPPLY_SHEET_MQ.addEventListener("change", () => supply.cy.resize());
}

// マップのうちシートに覆われていない縦の範囲（マップ内の座標）
function supplyMapView(){
  const {cy} = supply, h = cy.height();
  if(!SUPPLY_SHEET_MQ.matches || !$("#supply-panel").classList.contains("open")) return [0, h];
  const top = cy.container().getBoundingClientRect().top;
  const y1 = Math.min(h, window.innerHeight * (1 - SUPPLY_SHEET_H) - top);
  return y1 - Math.max(0, -top) < 120 ? [0, h] : [Math.max(0, -top), y1];
}

// eles が見える範囲に収まるよう寄る。経路が短いと寄りすぎるので拡大は 1.2 まで。
// スマホは見える範囲が狭く、長い経路だと箱が読めないので 0.6 より引かず、focus（選んだ素材）を真ん中に置く
function supplyFly(eles, duration, focus){
  const {cy} = supply, [y0, y1] = supplyMapView(), pad = 24, bb = eles.boundingBox();
  const fit = Math.min(1.2, (cy.width() - 2 * pad) / Math.max(bb.w, 1), (y1 - y0 - 2 * pad) / Math.max(bb.h, 1));
  const floor = SUPPLY_SHEET_MQ.matches ? 0.6 : cy.minZoom();
  const zoom = Math.max(floor, fit);
  const c = zoom > fit && focus ? focus.position() : {x: bb.x1 + bb.w / 2, y: bb.y1 + bb.h / 2};
  cy.stop();
  cy.animate({zoom, pan: {x: cy.width() / 2 - zoom * c.x, y: (y0 + y1) / 2 - zoom * c.y}}, {duration});
}

// 押した箱がシートの下に隠れるなら、見える範囲へ上げる
function supplyReveal(node){
  const [y0, y1] = supplyMapView(), y = node.renderedPosition().y, half = node.renderedHeight() / 2 + 12;
  const dy = y + half > y1 ? y1 - half - y : y - half < y0 ? y0 + half - y : 0;
  if(dy) supply.cy.animate({panBy: {x: 0, y: dy}}, {duration: 250});
}

function supplyChips(ids){
  if(!ids.length) return '<span class="sub">なし</span>';
  return `<div class="supply-chips">${ids.map(i => {
    const m = supply.byId[i];
    return `<button type="button" class="supply-chip" data-id="${i}"><i class="k-${m.kind}"></i>${m.name}</button>`;
  }).join("")}</div>`;
}

function supplyPanel(up, down){
  const p = $("#supply-body"), id = supply.selected;
  p.scrollTop = 0;
  if(!id){
    p.innerHTML = '<p class="sub">素材を選ぶと、原料と行き先が出ます。</p>';
    return;
  }
  const m = supply.byId[id], order = Object.keys(supply.byId);
  const sorted = set => order.filter(i => set.has(i));
  const raws = sorted(up).filter(i => ["raw", "recycled"].includes(supply.byId[i].kind));
  const prods = sorted(down).filter(i => supply.byId[i].kind === "prod");
  const makers = (supply.co.makesByMat[id] || []).length;
  p.innerHTML = `<h2>${m.name} <span class="supply-kind k-${m.kind}">${SUPPLY_KIND[m.kind]}</span></h2>
    <div class="sub supply-meta">上流 ${up.size} ・ 下流 ${down.size}</div>
    <div class="supply-tabs" role="tablist">
      <button type="button" data-tab="overview">概要</button>
      <button type="button" data-tab="co">企業${makers ? ` ${makers}` : ""}</button>
      <button type="button" data-tab="ctry" hidden>輸入元</button>
      <button type="button" data-tab="monthly">推移</button>
    </div>
    <div class="supply-pane" data-pane="overview">
    ${m.note ? `<p class="supply-note">${m.note}</p>` : ""}
    <h3>直接の原料</h3>${supplyChips(supply.parents[id])}
    <h3>これから作られるもの</h3>${supplyChips(supply.kids[id])}
    ${!["raw", "recycled"].includes(m.kind) ? `<h3>元をたどった原料</h3>${supplyChips(raws)}` : ""}
    ${down.size ? `<h3>行き着く最終製品</h3>${supplyChips(prods)}` : ""}
    ${m.kind === "recycled" ? `<h3>回収元</h3>${supplyChips(supply.retFrom[id])}` : ""}
    ${supply.retTo[id].length ? `<h3>使い終わると</h3>${supplyChips(supply.retTo[id])}` : ""}
    </div>
    <div class="supply-pane" data-pane="co">
      <h3>作っている企業</h3>${supplyMakers(id)}
      ${(supply.co.capsByMat[id] || []).map(supplyShare).join("")}
    </div>
    <div class="supply-pane" data-pane="ctry" id="supply-countries"></div>
    <div class="supply-pane" data-pane="monthly">
      <h3>月次の生産・輸出入</h3><div id="supply-monthly" class="sub">読み込み中…</div>
    </div>`;
  p.querySelectorAll(".supply-tabs button").forEach(b => b.onclick = () => supplyShowTab(b.dataset.tab));
  supplyShowTab(supplyTab);
  supplyBindPanel(p);
  supplyMonthly(id);
}

// 素材の詳細のタブ。選んだタブは素材を選び直しても続ける（無いタブなら概要）
let supplyTab = "overview";

function supplyShowTab(name){
  const p = $("#supply-body"), btn = p.querySelector(`.supply-tabs [data-tab="${name}"]`);
  if(!btn || btn.hidden) name = "overview";
  else supplyTab = name;
  p.querySelectorAll(".supply-tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  p.querySelectorAll(".supply-pane").forEach(el => { el.hidden = el.dataset.pane !== name; });
  // 隠れている間に描いたグラフは幅 0 で合わせているので、出したときに合わせ直す
  if(name === "monthly" && supplyChart) requestAnimationFrame(() => supplyChart?.timeScale().fitContent());
}

function supplyBindPanel(p){
  p.querySelectorAll("[data-id]").forEach(b => b.onclick = () => supplySelect(b.dataset.id, true));
  p.querySelectorAll("[data-co]").forEach(b => b.onclick = () => supplySelectCompany(b.dataset.co, true));
  p.querySelectorAll(".supply-group").forEach(b => b.onchange = () => {
    supplyGroup = b.checked;
    // 開いている表だけ描き直す（月次のグラフは作り直さない）
    p.querySelectorAll(".supply-share").forEach(el => {
      const t = supply.co.caps[el.dataset.cap];
      el.outerHTML = supplyShare(t);
    });
    supplyBindPanel(p);
  });
}

// ---- 輸入元の国 ----

const SUPPLY_COUNTRY_TOP = 5;

async function supplyCountries(id){
  let d = supply.countries[id];
  if(!d){
    try { d = supply.countries[id] = await api(`/api/supply/countries/${id}`); }
    catch(e){ return; }  // 国別は補助。失敗しても素材の表示は続ける
  }
  if(supply.selected !== id || !d.countries.length || !d.total) return;
  const box = $("#supply-countries");
  if(!box) return;
  // 上位 5 か国（0.1% 未満は出さない）+ その他
  const top = d.countries.slice(0, SUPPLY_COUNTRY_TOP).filter(c => c.share >= 0.001);
  const rest = d.countries.slice(top.length);
  const segs = top.map((c, i) => ({name: c.name, v: c.value, share: c.share, color: SUPPLY_SHARE_COLORS[i]}));
  if(rest.length) segs.push({name: `その他 ${rest.length} か国`, v: rest.reduce((a, c) => a + c.value, 0),
                             share: rest.reduce((a, c) => a + c.share, 0), color: SUPPLY_SHARE_OTHER});
  // 中央は 12 か月の輸入量の合計。桁が多いと穴に収まらないので万・億で丸める
  const v = d.total;
  const sum = v >= 1e8 ? `${fmt(v / 1e8, 1)}億` : v >= 1e6 ? `${fmt(v / 1e4)}万` : v >= 1e4 ? `${fmt(v / 1e4, 1)}万` : fmt(v);
  box.innerHTML = `<h3>輸入元の国</h3>
    <div class="supply-pie">${supplyDonut(segs, d.unit, sum, `${d.unit}・12か月計`, "国別の輸入シェア")}
      <table class="supply-tbl supply-share-tbl">${segs.map(s => `<tr title="${fmt(s.v)} ${d.unit}">
        <td class="l"><i style="background:${s.color}"></i>${s.name}</td><td>${(s.share * 100).toFixed(1)}%</td></tr>`).join("")}</table></div>
    <div class="sub supply-src">${d.from}〜${d.to}${d.sokuho ? "（最新月は速報）" : ""}の輸入量 ${fmt(v)} ${d.unit}・普通貿易統計（品別国別表）</div>`;
  $("#supply-body .supply-tabs [data-tab=ctry]").hidden = false;
  if(supplyTab === "ctry") supplyShowTab("ctry");
}

// ---- 企業 ----

function supplyCompanyIndex(d){
  const byId = {}, makesByMat = {}, makesByCo = {}, capsByMat = {}, caps = {}, kids = {}, stakes = {};
  d.companies.forEach(c => { byId[c.id] = c; kids[c.id] = []; stakes[c.id] = []; });
  d.companies.forEach(c => {
    if(c.parent_id && !c.owners.length) kids[c.parent_id].push(c.id);
    c.owners.forEach(([o, pct]) => stakes[o].push([c.id, pct]));
  });
  d.makes.forEach(m => {
    (makesByMat[m.material] ||= []).push(m);
    (makesByCo[m.company] ||= []).push(m);
  });
  d.capacities.forEach(t => { caps[t.id] = t; t.materials.forEach(m => (capsByMat[m] ||= []).push(t)); });
  return {byId, makesByMat, makesByCo, capsByMat, caps, kids, stakes,
          sources: d.sources, trade: d.trade, capacities: d.capacities};
}

// 親をたどった一番上の会社。グループでまとめるときの単位
function supplyTop(id){
  const {byId} = supply.co;
  let cur = id;
  while(byId[cur].parent_id) cur = byId[cur].parent_id;
  return cur;
}

function supplyCoChip(id, extra){
  const c = supply.co.byId[id];
  const code = c.code ? `<span class="supply-code">${c.code.slice(0, 4)}</span>` : "";
  return `<button type="button" class="supply-chip co" data-co="${id}">${c.name}${code}${extra ? `<span class="sub">${extra}</span>` : ""}</button>`;
}

function supplyCoChips(items){
  if(!items.length) return '<span class="sub">なし</span>';
  return `<div class="supply-chips">${items.map(([id, extra]) => supplyCoChip(id, extra)).join("")}</div>`;
}

function supplyMakers(mat){
  const list = supply.co.makesByMat[mat] || [];
  if(!list.length) return '<span class="sub">まだありません</span>';
  // 出典つき（能力表など）を先に、出典未確認を後に。どちらも json の企業順
  const order = Object.keys(supply.co.byId);
  const rank = m => (m.source ? 0 : 1e6) + order.indexOf(m.company);
  const sorted = [...list].sort((a, b) => rank(a) - rank(b));
  const unverified = sorted.filter(m => !m.source).length;
  return `<div class="supply-chips">${sorted.map(m =>
    supplyCoChip(m.company).replace('class="supply-chip co"', `class="supply-chip co${m.source ? "" : " unverified"}"`)).join("")}</div>
    ${unverified ? `<div class="sub supply-hint">点線は出典未確認（${unverified} 社）</div>` : ""}`;
}

function supplyShareRows(t){
  const sums = new Map();
  t.rows.forEach(([id, v]) => {
    const k = supplyGroup ? supplyTop(id) : id;
    sums.set(k, (sums.get(k) || 0) + v);
  });
  const total = [...sums.values()].reduce((a, b) => a + b, 0);
  return {total, rows: [...sums].sort((a, b) => b[1] - a[1])
    .map(([id, v], i) => ({id, v, share: v / total, color: SUPPLY_SHARE_COLORS[i] || SUPPLY_SHARE_OTHER}))};
}

// 区画は [{name, v, share, color, id?}]。id があれば押すとその企業。区切りは 2px の白
// 中央は 2 行（label と sub）。unit は区画に触れたときの値の単位
function supplyDonut(segs, unit, label, sub, aria){
  const R = 56, r0 = 34, C = 64;
  let a0 = -Math.PI / 2;
  const pt = (rad, a) => `${(C + rad * Math.cos(a)).toFixed(2)} ${(C + rad * Math.sin(a)).toFixed(2)}`;
  const paths = segs.map(s => {
    const a1 = a0 + s.share * 2 * Math.PI, big = a1 - a0 > Math.PI ? 1 : 0;
    const d = s.share >= 0.9999
      ? `M${pt(R, 0)}A${R} ${R} 0 1 1 ${pt(R, Math.PI)}A${R} ${R} 0 1 1 ${pt(R, 0)}M${pt(r0, 0)}A${r0} ${r0} 0 1 0 ${pt(r0, Math.PI)}A${r0} ${r0} 0 1 0 ${pt(r0, 0)}Z`
      : `M${pt(R, a0)}A${R} ${R} 0 ${big} 1 ${pt(R, a1)}L${pt(r0, a1)}A${r0} ${r0} 0 ${big} 0 ${pt(r0, a0)}Z`;
    const co = s.id ? ` data-co="${s.id}"` : "";
    a0 = a1;
    return `<path d="${d}" fill="${s.color}"${co}><title>${s.name} ${fmt(s.v)} ${unit}（${(s.share * 100).toFixed(1)}%）</title></path>`;
  }).join("");
  return `<svg class="supply-donut" viewBox="0 0 128 128" role="img" aria-label="${aria}">${paths}
    <text x="64" y="62" text-anchor="middle" class="t1">${label}</text>
    <text x="64" y="76" text-anchor="middle" class="t2">${sub}</text></svg>`;
}

// 上位 5 社 + その他
function supplyShareSegs(rows){
  const segs = rows.slice(0, SUPPLY_SHARE_COLORS.length).map(r => ({...r, name: supply.co.byId[r.id].name}));
  const rest = rows.slice(SUPPLY_SHARE_COLORS.length);
  if(rest.length) segs.push({v: rest.reduce((a, r) => a + r.v, 0), color: SUPPLY_SHARE_OTHER,
                             name: `その他 ${rest.length} 社`, share: rest.reduce((a, r) => a + r.share, 0)});
  return segs;
}

function supplyShare(t){
  const {total, rows} = supplyShareRows(t), src = supply.co.sources[t.source];
  const link = t.url || src.url;
  return `<div class="supply-share" data-cap="${t.id}">
    <h3>${t.label}のシェア</h3>
    ${t.note ? `<div class="sub supply-hint">${t.note}</div>` : ""}
    <div class="supply-share-body">${supplyDonut(supplyShareSegs(rows), t.unit, fmt(total), t.unit, "会社別シェア")}
      <table class="supply-tbl supply-share-tbl"><tr><th class="l">${supplyGroup ? "グループ" : "会社"}</th><th>能力</th><th>シェア</th></tr>
      ${rows.map(r => `<tr class="supply-row" data-co="${r.id}"><td class="l"><i style="background:${r.color}"></i>${supply.co.byId[r.id].name}</td>
        <td>${fmt(r.v)}</td><td>${(r.share * 100).toFixed(1)}%</td></tr>`).join("")}</table></div>
    <div class="sub supply-src">${t.unit}・${t.as_of.slice(0, 4)}年${Number(t.as_of.slice(5))}月末・${link ? `<a href="${link}" target="_blank" rel="noopener">${src.title}</a>` : src.title}</div>
    <label class="sub supply-tg"><input type="checkbox" class="supply-group"${supplyGroup ? " checked" : ""}>親会社でまとめる</label>
  </div>`;
}

function supplyCompanyPanel(id){
  const {byId, makesByCo, kids, stakes, trade, capacities} = supply.co, c = byId[id];
  const p = $("#supply-body");
  p.scrollTop = 0;
  if(supplyChart){ supplyChart.remove(); supplyChart = null; supplySeries = []; }
  const made = makesByCo[id] || [];
  const order = Object.keys(supply.byId);
  const mats = order.filter(m => made.some(x => x.material === m));
  // 能力表に載っている行。シェアは表の合計に対する割合（まとめない）
  const caps = capacities.filter(t => t.rows.some(r => r[0] === id)).map(t => {
    const v = t.rows.find(r => r[0] === id)[1], tot = t.rows.reduce((a, r) => a + r[1], 0);
    return `<tr class="supply-row" data-id="${t.materials[0]}"><td class="l">${t.label.replace(/生産能力$/, "")}</td>
      <td>${fmt(v)}</td><td class="l">${t.unit}</td><td>${(v / tot * 100).toFixed(1)}%</td></tr>`;
  });
  const parents = c.owners.length ? c.owners.map(([o, pct]) => [o, `${pct}%`])
    : c.parent_id ? [[c.parent_id, ""]] : [];
  const children = [...kids[id].map(k => [k, ""]), ...stakes[id].map(([k, pct]) => [k, `${pct}%`])];
  const sellTo = trade.filter(t => t.seller === id).map(t => [t.buyer, ""]);
  const buyFrom = trade.filter(t => t.buyer === id).map(t => [t.seller, ""]);
  const srcKeys = [...new Set([c.source, ...made.map(m => m.source),
    ...trade.filter(t => t.seller === id || t.buyer === id).map(t => t.source)].filter(Boolean))];
  const code = c.code ? c.code.slice(0, 4) : "";
  p.innerHTML = `<h2>${c.name} <span class="supply-kind k-co">${SUPPLY_STATUS[c.status]}${code ? ` ${code}` : ""}</span></h2>
    ${code ? `<div class="sub supply-meta"><a href="#chart/${c.code}" id="supply-chart-link">チャートを開く</a></div>` : ""}
    ${c.note ? `<p class="supply-note">${c.note}</p>` : ""}
    <h3>作っている素材</h3>${mats.length ? `<div class="supply-chips">${mats.map(m => {
      const x = supply.byId[m], ok = made.find(y => y.material === m).source;
      return `<button type="button" class="supply-chip${ok ? "" : " unverified"}" data-id="${m}"><i class="k-${x.kind}"></i>${x.name}</button>`;
    }).join("")}</div>` : '<span class="sub">なし</span>'}
    ${caps.length ? `<h3>生産能力</h3><table class="supply-tbl"><tr><th class="l">品目</th><th>能力</th><th class="l">単位</th><th>シェア</th></tr>${caps.join("")}</table>` : ""}
    <h3>${c.owners.length ? "出資者" : "親会社"}</h3>${supplyCoChips(parents)}
    <h3>子会社・出資先</h3>${supplyCoChips(children)}
    <h3>販売先</h3>${supplyCoChips(sellTo)}
    <h3>仕入先</h3>${supplyCoChips(buyFrom)}
    <div class="sub supply-src">${srcKeys.length ? `出典: ${srcKeys.map(k => supply.co.sources[k].title).join(" / ")}（確認 ${srcKeys.map(k => supply.co.sources[k].checked).join(" / ")}）` : "出典未確認"}</div>`;
  supplyBindPanel(p);
  const a = $("#supply-chart-link");
  if(a) a.onclick = e => { e.preventDefault(); gotoChart(c.code); };
}

const SUPPLY_METRIC = {
  production: {label: "生産", color: "#2f7489"},
  shipment:   {label: "出荷", color: "#9aa3ab"},
  inventory:  {label: "月末在庫", color: "#c4cad0"},
  import:     {label: "輸入", color: "#b37314"},
  export:     {label: "輸出", color: "#4a8a37"},
  processing: {label: "原油処理", color: "#7a5195"},
  domestic_sales: {label: "国内向販売", color: "#c2185b"},
  apparent:   {label: "見掛け消費", color: "#0077b6"},
};
const SUPPLY_CHART_METRICS = ["production", "import", "export", "processing", "domestic_sales", "apparent"];
let supplyChart = null;
let supplySeries = [];  // いま描いている lightweight-charts の系列
let supplyMonthlyData = null;  // {id, series, picked}。picked は表で選んだ系列の番号の Set（空なら既定の系列）

// 時刻は "YYYY-MM-DD" か {year, month, day} で来る
const supplyYm = t => typeof t === "string" ? t.slice(0, 7)
  : t && typeof t === "object" ? `${t.year}-${String(t.month).padStart(2, "0")}` : null;

async function supplyMonthly(id){
  let d;
  try { d = await api(`/api/supply/monthly/${id}`); }
  catch(e){ if(supply.selected === id) $("#supply-monthly").textContent = `読み込めませんでした (${e.message})`; return; }
  if(supply.selected !== id) return;
  const box = $("#supply-monthly");
  if(supplyChart){ supplyChart.remove(); supplyChart = null; supplySeries = []; }
  if(!d.series.length){ box.textContent = "統計の対応がまだありません。"; return; }
  supplyMonthlyData = {id, series: d.series, picked: new Set()};
  box.className = "";
  box.innerHTML = `<div class="sub supply-unit" id="supply-unit"></div>
    <div id="supply-legend" class="supply-legend-v"></div>
    <div class="supply-chart-wrap"><div id="supply-chart"></div><div id="supply-tip" hidden></div></div>
    <table class="supply-tbl"><tr><th class="l">指標</th><th>最新月</th><th>値</th><th class="l">単位</th></tr>
    ${d.series.map((x, i) => {
      const [m, v, rev] = x.points[x.points.length - 1];
      return `<tr class="supply-row" data-i="${i}"><td class="l"><i style="background:${SUPPLY_METRIC[x.metric].color}"></i>${SUPPLY_METRIC[x.metric].label}</td>
        <td>${rev === 1 ? '<span class="supply-rev">速報</span>' : ""}${m}</td><td>${fmt(v)}</td><td class="l">${x.unit}</td></tr>`;
    }).join("")}</table>
    <div class="sub supply-hint">指標を押すと選んだ指標だけ（複数可）。もう一度押すと外す</div>`;
  box.querySelectorAll(".supply-row").forEach(tr => tr.onclick = () => {
    const i = Number(tr.dataset.i), picked = supplyMonthlyData.picked;
    picked.has(i) ? picked.delete(i) : picked.add(i);
    supplyDrawChart();
  });
  supplyDrawChart();
}

function supplyChartLines(){
  const {series, picked} = supplyMonthlyData;
  if(picked.size) return series.filter((_, i) => picked.has(i));
  // 既定は生産の単位（無ければ最初の系列の単位）にそろう系列だけ
  const unit = (series.find(x => x.metric === "production") || series[0]).unit;
  return series.filter(x => x.unit === unit && SUPPLY_CHART_METRICS.includes(x.metric));
}

function supplyLegend(lines){
  // 凡例は色と指標名だけ。値は触れた月の吹き出しに出す（指標ごとに最新月が違うので並べない）
  $("#supply-legend").innerHTML = lines.map(x =>
    `<span><i style="background:${SUPPLY_METRIC[x.metric].color}"></i>${SUPPLY_METRIC[x.metric].label}</span>`).join("");
}

function supplyTip(lines, param){
  const tip = $("#supply-tip"), month = supplyYm(param.time);
  if(!month || !param.point){ tip.hidden = true; return; }
  tip.innerHTML = `<b>${month}</b>${lines.map(x => {
    const p = x.points.find(q => q[0] === month);
    return `<div><i style="background:${SUPPLY_METRIC[x.metric].color}"></i>${SUPPLY_METRIC[x.metric].label}<span>${p ? fmt(p[1]) : "–"}</span></div>`;
  }).join("")}`;
  tip.hidden = false;
  // 触れた位置の横に出す。右半分なら左側へ
  const w = tip.parentElement.clientWidth, {x, y} = param.point;
  tip.style.left = `${x < w / 2 ? x + 12 : Math.max(0, x - 12 - tip.offsetWidth)}px`;
  tip.style.top = `${Math.max(0, Math.min(y - tip.offsetHeight / 2, 180 - tip.offsetHeight))}px`;
}

function supplyEnsureChart(){
  if(supplyChart) return supplyChart;
  supplyChart = LightweightCharts.createChart($("#supply-chart"), {
    height: 180, autoSize: true,
    layout: {background: {color: "#fff"}, textColor: "#66707a", fontSize: 11, attributionLogo: false},
    grid: {vertLines: {visible: false}, horzLines: {color: "#e8ecf0"}},
    rightPriceScale: {borderVisible: false}, leftPriceScale: {borderVisible: false},
    timeScale: {borderVisible: false, tickMarkFormatter: (t, type) => {
      const s = supplyYm(t) || "";
      return type === 0 ? `${s.slice(0, 4)}年` : `${Number(s.slice(5))}月`;  // 0 = 年の目盛り
    }},
    // 値は吹き出しに全系列ぶん出すので、横線の値ラベルは出さない
    crosshair: {horzLine: {visible: false, labelVisible: false}},
    handleScroll: false, handleScale: false,
    localization: {priceFormatter: v => fmt(v), timeFormatter: t => supplyYm(t) || ""},
  });
  supplyChart.subscribeCrosshairMove(param => supplyTip(supplyChartLines(), param));
  return supplyChart;
}

function supplyDrawChart(){
  const {picked} = supplyMonthlyData;
  document.querySelectorAll(".supply-row").forEach(tr =>
    tr.classList.toggle("sel", picked.has(Number(tr.dataset.i))));
  const lines = supplyChartLines();
  // 単位ごとに縦軸を分ける。1 つ目は右、2 つ目は左、3 つ目からは目盛りを出さない
  const units = [...new Set(lines.map(x => x.unit))];
  const scaleOf = u => ["right", "left"][units.indexOf(u)] || `u${units.indexOf(u)}`;
  $("#supply-unit").textContent = !units.length ? ""
    : units.length === 1 ? `単位 ${units[0]}`
    : `単位 ${units.map((u, i) => `${u}（${["右", "左"][i] || "目盛りなし"}）`).join(" / ")}`;
  supplyLegend(lines);
  $("#supply-tip").hidden = true;
  if(!window.LightweightCharts) return;
  // グラフを作り直すと一瞬高さが 0 になり、スマホでページが上へ跳ぶ。系列だけ差し替える
  const chart = supplyEnsureChart();
  supplySeries.forEach(s => chart.removeSeries(s));
  supplySeries = lines.map(x => {
    const s = chart.addLineSeries({color: SUPPLY_METRIC[x.metric].color, lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false, priceScaleId: scaleOf(x.unit)});
    s.setData(x.points.map(([m, v]) => ({time: `${m}-01`, value: v})));
    return s;
  });
  chart.applyOptions({leftPriceScale: {visible: units.length > 1}});
  chart.timeScale().fitContent();
}

window.loadSupply = loadSupply;
window.supplySelectedId = supplySelectedId;

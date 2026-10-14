// サプライチェーン: 素材の有向グラフを左(上流)→右(下流)に描く。仕様は docs/DESIGN_supply.md
const SUPPLY_LIBS = [
  "https://unpkg.com/cytoscape@3.30.2/dist/cytoscape.min.js",
  "https://unpkg.com/elkjs@0.9.3/lib/elk.bundled.js",
];
const SUPPLY_KIND = {raw: "原料", mid: "中間財", prod: "最終製品"};
const SUPPLY_COLOR = {
  raw:  {fill: "#fdf1de", line: "#b37314"},
  mid:  {fill: "#e3f0f4", line: "#2f7489"},
  prod: {fill: "#e6f2e0", line: "#4a8a37"},
};
const SUPPLY_NODE_H = 28;
let supplyInit = null;  // Promise。ライブラリとグラフの読込は一度だけ
let supply = null;      // {cy, byId, parents, kids, selected}

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
}

async function supplySetup(){
  const [graph] = await Promise.all([api("/api/supply/graph"), ...SUPPLY_LIBS.map(supplyLoadScript)]);
  const byId = {}, parents = {}, kids = {};
  graph.materials.forEach(m => { byId[m.id] = m; parents[m.id] = []; kids[m.id] = []; });
  graph.flows.forEach(([a, b]) => { kids[a].push(b); parents[b].push(a); });

  // 箱の幅はラベルの文字幅で決めてから ELK に渡す
  const font = `600 12px ${getComputedStyle(document.body).fontFamily}`;
  if(document.fonts?.ready) await document.fonts.ready;
  const ctx = document.createElement("canvas").getContext("2d");
  ctx.font = font;
  graph.materials.forEach(m => { m.w = Math.ceil(ctx.measureText(m.name).width) + 20; });

  const cy = cytoscape({
    container: $("#supply-map"),
    elements: [
      ...graph.materials.map(m => ({data: {id: m.id, label: m.name, kind: m.kind, w: m.w}})),
      ...graph.flows.map(([a, b]) => ({data: {id: `${a}>${b}`, source: a, target: b}})),
    ],
    style: supplyStyle(font),
    layout: {name: "preset"},
    minZoom: 0.2, maxZoom: 3,
    boxSelectionEnabled: false, autoungrabify: true,
  });
  $("#supply-msg").remove();
  supply = {cy, byId, parents, kids, selected: ""};
  await supplyLayout();
  cy.fit(undefined, 16);

  cy.on("tap", "node", e => supplySelect(e.target.id(), false));
  cy.on("tap", e => { if(e.target === cy) supplyClear(); });
  $("#supply-reset").onclick = () => { supplyClear(); $("#supply-q").value = ""; cy.animate({fit: {padding: 16}}, {duration: 300}); };
  $("#supply-merge").onchange = async () => { await supplyLayout(); cy.fit(undefined, 16); };
  const names = $("#supply-names"), index = {};
  graph.materials.forEach(m => {
    index[m.name] = m.id;
    const o = document.createElement("option");
    o.value = m.name;
    names.appendChild(o);
  });
  $("#supply-q").onchange = e => {
    const v = e.target.value.trim();
    if(!v) return;
    const id = index[v] || graph.materials.find(m => m.name.includes(v))?.id;
    if(id) supplySelect(id, true);
  };
  supplyPanel();
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
  const {cy, parents, kids} = supply;
  supply.selected = id;
  const up = supplyWalk(id, parents), down = supplyWalk(id, kids);
  const onUp = n => n === id || up.has(n), onDown = n => n === id || down.has(n);
  cy.batch(() => {
    cy.elements().removeClass("dim hl sel");
    cy.nodes().forEach(n => { if(!onUp(n.id()) && !onDown(n.id())) n.addClass("dim"); });
    cy.edges().forEach(e => {
      const a = e.source().id(), b = e.target().id();
      e.addClass((up.has(a) && onUp(b)) || (onDown(a) && down.has(b)) ? "hl" : "dim");
    });
    cy.getElementById(id).addClass("sel");
  });
  if(fly){
    // 経路が短いと寄りすぎるので、寄せる間だけ拡大の上限を下げる
    const max = cy.maxZoom();
    cy.maxZoom(1.2);
    cy.animate({fit: {eles: cy.nodes().not(".dim"), padding: 24}},
               {duration: 350, complete: () => cy.maxZoom(max)});
  }
  supplyPanel(up, down);
  writeRoute("supply", id);
}

function supplyClear(){
  if(!supply) return;
  supply.selected = "";
  supply.cy.elements().removeClass("dim hl sel");
  supplyPanel();
  writeRoute("supply", "");
}

function supplyChips(ids){
  if(!ids.length) return '<span class="sub">なし</span>';
  return `<div class="supply-chips">${ids.map(i => {
    const m = supply.byId[i];
    return `<button type="button" class="supply-chip" data-id="${i}"><i class="k-${m.kind}"></i>${m.name}</button>`;
  }).join("")}</div>`;
}

function supplyPanel(up, down){
  const p = $("#supply-panel"), id = supply.selected;
  if(!id){
    p.innerHTML = '<p class="sub">素材を選ぶと、原料と行き先が出ます。</p>';
    return;
  }
  const m = supply.byId[id], order = Object.keys(supply.byId);
  const sorted = set => order.filter(i => set.has(i));
  const raws = sorted(up).filter(i => supply.byId[i].kind === "raw");
  const prods = sorted(down).filter(i => supply.byId[i].kind === "prod");
  p.innerHTML = `<h2>${m.name} <span class="supply-kind k-${m.kind}">${SUPPLY_KIND[m.kind]}</span></h2>
    <div class="sub supply-meta">上流 ${up.size} ・ 下流 ${down.size}</div>
    ${m.note ? `<p class="supply-note">${m.note}</p>` : ""}
    <h3>直接の原料</h3>${supplyChips(supply.parents[id])}
    <h3>これから作られるもの</h3>${supplyChips(supply.kids[id])}
    ${m.kind !== "raw" ? `<h3>元をたどった原料</h3>${supplyChips(raws)}` : ""}
    ${down.size ? `<h3>行き着く最終製品</h3>${supplyChips(prods)}` : ""}
    <h3>月次の量</h3><div id="supply-monthly" class="sub">読み込み中…</div>`;
  p.querySelectorAll(".supply-chip").forEach(b => b.onclick = () => supplySelect(b.dataset.id, true));
  supplyMonthly(id);
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

async function supplyMonthly(id){
  let d;
  try { d = await api(`/api/supply/monthly/${id}`); }
  catch(e){ if(supply.selected === id) $("#supply-monthly").textContent = `読み込めませんでした (${e.message})`; return; }
  if(supply.selected !== id) return;
  const box = $("#supply-monthly");
  if(supplyChart){ supplyChart.remove(); supplyChart = null; }
  if(!d.series.length){ box.textContent = "統計の対応がまだありません。"; return; }
  // グラフは生産の単位（無ければ最初の系列の単位）にそろう系列だけ
  const unit = (d.series.find(x => x.metric === "production") || d.series[0]).unit;
  const lines = d.series.filter(x => x.unit === unit && SUPPLY_CHART_METRICS.includes(x.metric));
  box.className = "";
  box.innerHTML = `<div class="sub supply-unit">単位 ${unit}</div><div id="supply-chart"></div>
    <table class="supply-tbl"><tr><th class="l">指標</th><th>最新月</th><th>値</th><th class="l">単位</th></tr>
    ${d.series.map(x => {
      const [m, v, rev] = x.points[x.points.length - 1];
      return `<tr><td class="l"><i style="background:${SUPPLY_METRIC[x.metric].color}"></i>${SUPPLY_METRIC[x.metric].label}</td>
        <td>${m}${rev === 1 ? '<span class="supply-rev">速報</span>' : ""}</td><td>${fmt(v)}</td><td class="l">${x.unit}</td></tr>`;
    }).join("")}</table>`;
  if(!lines.length || !window.LightweightCharts) return;
  const el = $("#supply-chart");
  supplyChart = LightweightCharts.createChart(el, {
    height: 180, width: el.clientWidth,
    layout: {background: {color: "#fff"}, textColor: "#66707a", fontSize: 11, attributionLogo: false},
    grid: {vertLines: {visible: false}, horzLines: {color: "#e8ecf0"}},
    rightPriceScale: {borderVisible: false}, timeScale: {borderVisible: false},
    handleScroll: false, handleScale: false,
    localization: {priceFormatter: v => fmt(v)},
  });
  lines.forEach(x => {
    const s = supplyChart.addLineSeries({color: SUPPLY_METRIC[x.metric].color, lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false});
    s.setData(x.points.map(([m, v]) => ({time: `${m}-01`, value: v})));
  });
  supplyChart.timeScale().fitContent();
}

window.loadSupply = loadSupply;
window.supplySelectedId = supplySelectedId;

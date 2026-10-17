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
  // 回収（使い終わった製品 → リサイクル資源）。循環になるので配置と祖先・子孫には使わない
  const returns = graph.returns || [];
  returns.forEach(([a, b]) => { retTo[a].push(b); retFrom[b].push(a); });

  // 箱の幅はラベルの文字幅で決めてから ELK に渡す
  const font = `600 12px ${getComputedStyle(document.body).fontFamily}`;
  if(document.fonts?.ready) await document.fonts.ready;
  const ctx = document.createElement("canvas").getContext("2d");
  ctx.font = font;
  graph.materials.forEach(m => { m.w = Math.ceil(ctx.measureText(m.name).width) + 20; });
  const measure = s => Math.ceil(ctx.measureText(s).width) + 20;

  const cy = cytoscape({
    container: $("#supply-map"),
    elements: [
      ...graph.materials.map(m => ({data: {id: m.id, label: m.name, kind: m.kind, w: m.w}})),
      ...graph.flows.map(([a, b]) => ({data: {id: `${a}>${b}`, source: a, target: b}})),
      ...returns.map(([a, b]) => ({classes: "ret", data: {id: `${a}~${b}`, source: a, target: b, sw: [0.5], sd: [0], se: "0px 0px", te: "0px 0px"}})),
    ],
    style: supplyStyle(font),
    layout: {name: "preset"},
    minZoom: 0.2, maxZoom: SUPPLY_MAX_ZOOM,
    boxSelectionEnabled: false, autoungrabify: true,
  });
  $("#supply-msg").remove();
  supply = {cy, byId, parents, kids, retFrom, retTo, selected: "", co: supplyCompanyIndex(companies), measure, countries: {}};
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

  cy.on("tap", "node", e => { if(!e.target.hasClass("ctry")) supplySelect(e.target.id(), false); });
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
    // 回収の線。右の製品から左のリサイクル資源へ、配置の下を回って戻る（配置が済んでから曲げ具合を決める）
    // 箱より奥に描く（ほかの箱の下をくぐるときに文字を隠さない）
    {selector: "edge.ret", style: {
      width: 1.6, "line-color": "#6f4fb0", "target-arrow-color": "#6f4fb0", "line-style": "dashed",
      "line-dash-pattern": [6, 4], "curve-style": "round-segments", "segment-radius": 10,
      "segment-weights": "data(sw)", "segment-distances": "data(sd)", "edge-distances": "endpoints",
      "source-endpoint": "data(se)", "target-endpoint": "data(te)",
      opacity: 0.7, "z-index": 5,
    }},
    {selector: "edge.ret.dim", style: {opacity: 0.12}},
    {selector: "edge.ret.hl", style: {width: 2.4, opacity: 1, "z-index": 6}},
    // 輸入元の国。選んだ素材の左に一時的に置く
    {selector: "node.ctry", style: {
      "background-color": "#fff", "border-color": "#7a8794", "border-style": "dashed", "border-width": 1.2,
      "font-size": 11, "font-weight": 500, color: "#333", "z-index": 30,
    }},
    {selector: "edge.ctry", style: {
      width: "data(lw)", "line-color": "#b37314", "target-arrow-color": "#b37314", opacity: 0.75, "z-index": 25,
      // 国の箱の右端から素材の箱の左端へ。中心から引くと隣の国の箱の下をくぐる
      "curve-style": "straight", "source-endpoint": "90deg", "target-endpoint": "270deg",
    }},
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
    edges: cy.edges().not(".ret").map(e => ({id: e.id(), sources: [e.source().id()], targets: [e.target().id()]})),
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
    supplyBendReturns(center);
  });
}

// 回収の線は右（製品）から左（リサイクル資源）へ戻る。配置の外を回る直角の線にする:
// 製品の真下へ降りて配置の下端の外を左へ進み、リサイクル資源の真下から入る。真下に箱があれば
// 右へ出て配置の右端の外を降りる（どちらも箱にぶつかるなら、ぶつかる数の少ないほう）。
// 資源の真下に箱があれば、資源の列の左の隙間を上って左から入る（列の隙間には箱が無い）。
// 回収の線は 8px ずつ外へずらして重ねない（資源が違う線も）。曲がり角は segments と同じ (比率, 垂直距離) で持つ
function supplyBendReturns(center){
  const {cy} = supply, H = SUPPLY_NODE_H;
  const box = id => { const c = center[id], w = cy.getElementById(id).data("w"); return {x1: c.x - w / 2, x2: c.x + w / 2, y1: c.y - H / 2, y2: c.y + H / 2}; };
  const boxes = Object.keys(center).map(id => ({id, ...box(id)}));
  const bottom = Math.max(...boxes.map(b => b.y2)), right = Math.max(...boxes.map(b => b.x2));
  const hits = (x1, y1, x2, y2, skip) => boxes.filter(b => !skip.includes(b.id)
    && Math.min(x1, x2) < b.x2 && Math.max(x1, x2) > b.x1 && Math.min(y1, y2) < b.y2 && Math.max(y1, y2) > b.y1).length;
  // 資源の列（横に重なる箱）の左端。左から入る線はその 8px 左を上る
  const colLeft = tb => Math.min(...boxes.filter(o => o.x1 < tb.x2 && o.x2 > tb.x1).map(o => o.x1));
  const lane = {};
  cy.edges(".ret").forEach((e, k) => {
    const a = e.source().id(), b = e.target().id();
    if(!center[a] || !center[b]) return;
    const j = lane[b] = (lane[b] ?? -1) + 1;
    const sb = box(a), tb = box(b), Y = bottom + 16 + k * 8, X = right + 16 + k * 8;
    const sc = center[a], tc = center[b], skip = [a, b];
    const down = hits(sc.x, sb.y2, sc.x + 1, Y, skip);
    const side = hits(sb.x2, sc.y, X, sc.y + 1, skip);
    // 始点と終点（箱の縁）と曲がり角
    const [s, bends] = down <= side
      ? [{x: sc.x, y: sb.y2}, [[sc.x, Y]]]
      : [{x: sb.x2, y: sc.y}, [[X, sc.y], [X, Y]]];
    let t = {x: tc.x, y: tb.y2};
    if(hits(tc.x, tb.y2, tc.x + 1, Y, skip)){
      const L = colLeft(tb) - 8 - j * 4;
      bends.push([L, Y], [L, tc.y]);
      t = {x: tb.x1, y: tc.y};
    } else {
      bends.push([tc.x, Y]);
    }
    const dx = t.x - s.x, dy = t.y - s.y, len2 = dx * dx + dy * dy || 1, len = Math.sqrt(len2);
    const sw = [], sd = [];
    bends.forEach(([x, y]) => {
      const px = x - s.x, py = y - s.y;
      sw.push((px * dx + py * dy) / len2);
      sd.push((py * dx - px * dy) / len);
    });
    e.data({sw, sd, se: `${s.x - sc.x}px ${s.y - sc.y}px`, te: `${t.x - tc.x}px ${t.y - tc.y}px`});
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
  cy.remove(".ctry");
  const up = supplyWalk(id, parents), down = supplyWalk(id, kids);
  const onUp = n => n === id || up.has(n), onDown = n => n === id || down.has(n);
  cy.batch(() => {
    cy.elements().removeClass("dim hl sel");
    cy.nodes().forEach(n => { if(!onUp(n.id()) && !onDown(n.id())) n.addClass("dim"); });
    cy.edges().not(".ret").forEach(e => {
      const a = e.source().id(), b = e.target().id();
      e.addClass((up.has(a) && onUp(b)) || (onDown(a) && down.has(b)) ? "hl" : "dim");
    });
    // 回収の線は選んだ素材が端にあるときだけ強調する
    cy.edges(".ret").forEach(e => e.addClass(e.source().id() === id || e.target().id() === id ? "hl" : "dim"));
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
  supplyCountries(id, fly);
}

function supplySelectCompany(id, fly){
  const {cy} = supply, made = new Set((supply.co.makesByCo[id] || []).map(x => x.material));
  supply.selected = id;
  cy.remove(".ctry");
  cy.batch(() => {
    cy.elements().removeClass("dim hl sel");
    cy.nodes().forEach(n => n.addClass(made.has(n.id()) ? "sel" : "dim"));
    cy.edges().addClass("dim");
  });
  if(fly && made.size){
    const max = cy.maxZoom();
    cy.maxZoom(1.2);
    cy.animate({fit: {eles: cy.nodes().not(".dim"), padding: 24}},
               {duration: 350, complete: () => cy.maxZoom(max)});
  }
  supplyCompanyPanel(id);
  writeRoute("supply", id);
}

function supplyClear(){
  if(!supply) return;
  supply.selected = "";
  supply.cy.remove(".ctry");
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
  const raws = sorted(up).filter(i => ["raw", "recycled"].includes(supply.byId[i].kind));
  const prods = sorted(down).filter(i => supply.byId[i].kind === "prod");
  p.innerHTML = `<h2>${m.name} <span class="supply-kind k-${m.kind}">${SUPPLY_KIND[m.kind]}</span></h2>
    <div class="sub supply-meta">上流 ${up.size} ・ 下流 ${down.size}</div>
    ${m.note ? `<p class="supply-note">${m.note}</p>` : ""}
    <h3>直接の原料</h3>${supplyChips(supply.parents[id])}
    <h3>これから作られるもの</h3>${supplyChips(supply.kids[id])}
    ${!["raw", "recycled"].includes(m.kind) ? `<h3>元をたどった原料</h3>${supplyChips(raws)}` : ""}
    ${down.size ? `<h3>行き着く最終製品</h3>${supplyChips(prods)}` : ""}
    ${m.kind === "recycled" ? `<h3>回収元</h3>${supplyChips(supply.retFrom[id])}` : ""}
    ${supply.retTo[id].length ? `<h3>使い終わると</h3>${supplyChips(supply.retTo[id])}` : ""}
    <h3>作っている企業</h3>${supplyMakers(id)}
    ${(supply.co.capsByMat[id] || []).map(supplyShare).join("")}
    <div id="supply-countries"></div>
    <h3>月次の量</h3><div id="supply-monthly" class="sub">読み込み中…</div>`;
  supplyBindPanel(p);
  supplyMonthly(id);
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

async function supplyCountries(id, fly){
  let d = supply.countries[id];
  if(!d){
    try { d = supply.countries[id] = await api(`/api/supply/countries/${id}`); }
    catch(e){ return; }  // 国別は補助。失敗しても素材の表示は続ける
  }
  if(supply.selected !== id || !d.countries.length || !d.total) return;
  // 上位 5 か国（0.1% 未満は出さない）+ その他。その他も 0.1% 未満なら出さない
  const top = d.countries.slice(0, SUPPLY_COUNTRY_TOP).filter(c => c.share >= 0.001);
  const rest = d.countries.slice(top.length), restShare = rest.reduce((a, c) => a + c.share, 0);
  const items = top.map(c => ({key: c.code, name: c.name, share: c.share}));
  if(restShare >= 0.001) items.push({key: "etc", name: `その他 ${rest.length} か国`, share: restShare});
  const pct = s => `${(s * 100).toFixed(s < 0.1 ? 1 : 0)}%`;
  const {cy, measure} = supply, target = cy.getElementById(id), p = target.position();
  const labels = items.map(c => `${c.name} ${pct(c.share)}`);
  const w = Math.max(...labels.map(measure));
  const gap = SUPPLY_NODE_H + 8, h = (items.length - 1) * gap + SUPPLY_NODE_H;
  // 国の列は素材のすぐ左。箱と重なるなら上下に空きを探す（左へ押し出すと上流の多い素材で画面の外まで行く）。
  // 先に薄い箱も避け、無ければ強調中の箱だけ避ける。上下にも空きが無いときだけ、重ならなくなるまで左へずらす
  let x = p.x - target.width() / 2 - 56 - w / 2, y = p.y;
  const lit = cy.nodes().not(".dim").not(target).map(n => n.boundingBox());
  const all = cy.nodes().not(target).map(n => n.boundingBox());
  const hit = (bx, by, boxes) => boxes.find(b =>
    bx - w / 2 < b.x2 + 8 && bx + w / 2 > b.x1 - 8 && by - h / 2 < b.y2 + 4 && by + h / 2 > b.y1 - 4);
  const reach = h + 4 * gap;
  const dys = [0];
  for(let d = gap / 2; d <= reach; d += gap / 2) dys.push(-d, d);
  const dy = [all, lit].map(boxes => dys.find(d => !hit(x, p.y + d, boxes))).find(d => d !== undefined);
  if(dy !== undefined) y = p.y + dy;
  else for(let b = hit(x, y, lit); b; b = hit(x, y, lit)) x = b.x1 - 24 - w / 2;
  cy.add(items.flatMap((c, i) => [
    {group: "nodes", classes: "ctry", data: {id: `ctry_${c.key}`, label: labels[i], kind: "mid", w},
     position: {x, y: y + (i - (items.length - 1) / 2) * gap}},
    {group: "edges", classes: "ctry", data: {id: `ctry_${c.key}>${id}`, source: `ctry_${c.key}`, target: id,
     lw: 1 + c.share * 9}},
  ]));
  if(fly){
    // 国と素材と直接の前後に寄せる（原油のように下流がマップ全体だと国が読めない大きさになる）
    const near = cy.nodes(".ctry").union(target).union(target.incomers("node")).union(target.outgoers("node"));
    // 素材を選んだときの寄せが途中なら止める。止めると上限を戻す complete が走らないので初期値に戻す
    cy.stop();
    cy.maxZoom(1.2);
    cy.animate({fit: {eles: near, padding: 24}}, {duration: 300, complete: () => cy.maxZoom(SUPPLY_MAX_ZOOM)});
  }
  const box = $("#supply-countries");
  if(!box) return;
  box.innerHTML = `<h3>輸入元の国（${d.from}〜${d.to}${d.sokuho ? "、最新月は速報" : ""}）</h3>
    <table class="supply-tbl supply-share-tbl"><tr><th class="l">国</th><th>輸入量</th><th>シェア</th></tr>
    ${d.countries.slice(0, 10).map(c => `<tr><td class="l">${c.name}</td><td>${fmt(c.value)}</td><td>${(c.share * 100).toFixed(1)}%</td></tr>`).join("")}
    ${d.countries.length > 10 ? `<tr><td class="l sub">ほか ${d.countries.length - 10} か国</td><td>${fmt(d.countries.slice(10).reduce((a, c) => a + c.value, 0))}</td><td>${(d.countries.slice(10).reduce((a, c) => a + c.share, 0) * 100).toFixed(1)}%</td></tr>` : ""}
    </table>
    <div class="sub supply-src">${d.unit}・普通貿易統計（品別国別表）の直近 12 か月の合計</div>`;
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

function supplyDonut(rows, total, unit){
  // 上位 5 社 + その他。区切りは 2px の白
  const segs = rows.slice(0, SUPPLY_SHARE_COLORS.length).map(r => ({...r, name: supply.co.byId[r.id].name}));
  const rest = rows.slice(SUPPLY_SHARE_COLORS.length);
  if(rest.length) segs.push({v: rest.reduce((a, r) => a + r.v, 0), color: SUPPLY_SHARE_OTHER,
                             name: `その他 ${rest.length} 社`, share: rest.reduce((a, r) => a + r.share, 0)});
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
  return `<svg class="supply-donut" viewBox="0 0 128 128" role="img" aria-label="会社別シェア">${paths}
    <text x="64" y="62" text-anchor="middle" class="t1">${fmt(total)}</text>
    <text x="64" y="76" text-anchor="middle" class="t2">${unit}</text></svg>`;
}

function supplyShare(t){
  const {total, rows} = supplyShareRows(t), src = supply.co.sources[t.source];
  const link = t.url || src.url;
  return `<div class="supply-share" data-cap="${t.id}">
    <h3>${t.label}のシェア</h3>
    ${t.note ? `<div class="sub supply-hint">${t.note}</div>` : ""}
    <div class="supply-share-body">${supplyDonut(rows, total, t.unit)}
      <table class="supply-tbl supply-share-tbl"><tr><th class="l">${supplyGroup ? "グループ" : "会社"}</th><th>能力</th><th>シェア</th></tr>
      ${rows.map(r => `<tr class="supply-row" data-co="${r.id}"><td class="l"><i style="background:${r.color}"></i>${supply.co.byId[r.id].name}</td>
        <td>${fmt(r.v)}</td><td>${(r.share * 100).toFixed(1)}%</td></tr>`).join("")}</table></div>
    <div class="sub supply-src">${t.unit}・${t.as_of.slice(0, 4)}年${Number(t.as_of.slice(5))}月末・${link ? `<a href="${link}" target="_blank" rel="noopener">${src.title}</a>` : src.title}</div>
    <label class="sub supply-tg"><input type="checkbox" class="supply-group"${supplyGroup ? " checked" : ""}>親会社でまとめる</label>
  </div>`;
}

function supplyCompanyPanel(id){
  const {byId, makesByCo, kids, stakes, trade, capacities} = supply.co, c = byId[id];
  const p = $("#supply-panel");
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

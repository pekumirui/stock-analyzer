// ランキングタブ（docs/DESIGN_ranking.md）。決算発表後パフォーマンスを上昇率 / 下落率に分ける
const rkEsc = s => String(s ?? "").replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;");
const rkMd = iso => iso ? `${+iso.slice(5,7)}/${+iso.slice(8,10)}` : "–";
const rkMonth = ym => ym ? `${ym.slice(0,4)}年${+ym.slice(5,7)}月` : "";
const rkSession = s => s === "intraday"
  ? `<span class="qb qb-1Q">場中</span>` : `<span class="qb">引け後</span>`;
const rkSessionTxt = s => s === "intraday" ? "場中" : "引け後";
const rkPctParen = v => v==null ? "–" : `<span class="${v>=0?'pos':'neg'}">(${v>0?'+':''}${fmt(v,1)}%)</span>`;
const rkName = r => {
  const raw = String(r.name||""), name = rkEsc(raw), code = rkEsc(r.code.slice(0,4));
  return `<td class="l name pin1" title="${name}"><span class="clicky" onclick="gotoChart('${rkEsc(r.code)}')">${rkEsc(shortName(raw))}</span>${under(`<span class="sub">${code}</span>`)}</td>`;
};
const rkMarket = r => { const m = fmtMarket(r.market); return `<td class="l ${m.cls}">${rkEsc(m.label || "–")}</td>`; };
const RK_SHOT_ROWS = 20;
const RK_DIR = {up: "上昇率", down: "下落率"};
// 経過日数。次の決算がまだなら営業日数だけ。終わっていれば終点の 〜M/D
const rkElapsed = r => r.next_disclosed_date
  ? `〜${rkMd(r.end_date)}`
  : (r.bdays == null ? "–" : `${fmt(r.bdays)}日`);
const rkElapsedTxt = r => r.next_disclosed_date
  ? `〜${rkMd(r.end_date)}`
  : (r.bdays == null ? "" : `${r.bdays}日`);
let rkData = null;
let rkDir = "up";
let rkShot = null;   // X に投稿する画像（Promise<Blob>）。共有はクリックの中で呼ぶので先に作っておく
let rkSeq = 0;       // 月を続けて変えたとき、古い応答で上書きしない

// 上昇はプラスだけ大きい順、下落はマイナスだけ小さい順。0 と欠測はどちらにも入れない
function rkView(d){
  const rows = (d.rows || []).filter(r => r.return_to_next != null);
  const list = rkDir === "up"
    ? rows.filter(r => r.return_to_next > 0).sort((a, b) => b.return_to_next - a.return_to_next || String(a.code).localeCompare(String(b.code)))
    : rows.filter(r => r.return_to_next < 0).sort((a, b) => a.return_to_next - b.return_to_next || String(a.code).localeCompare(String(b.code)));
  return list.map((r, i) => ({...r, rank: i + 1}));
}

function rkPaint(){
  const d = rkData;
  if(!d) return;
  const view = rkView(d);
  if(!d.rows.length){
    $("#rk-info").textContent = d.months.length ? "この月の決算はありません" : "決算のデータがまだありません";
    $("#rk-tbl").innerHTML = ""; $("#rk-share").hidden = true; rkShot = null;
    return;
  }
  if(!view.length){
    $("#rk-info").textContent = `この月の${RK_DIR[rkDir]}はありません`;
    $("#rk-tbl").innerHTML = ""; $("#rk-share").hidden = true; rkShot = null;
    return;
  }
  $("#rk-info").textContent = `${view.length} 銘柄 / ${rkMd(d.as_of)} 終値まで`;
  let h = "<thead><tr><th class='pin0'>順位</th><th class='l name pin1'>銘柄</th>"
        + "<th class='l'>市場</th><th class='l'>業種</th>"
        + "<th class='grp' title='決算後に買えた最初の値から、次の決算の前日終値まで。下段は対TOPIX'>騰落率"
        + under(`<span class="sub">(対TOPIX)</span>`) + "</th>"
        + "<th>決算日</th>"
        + "<th title='決算後に買ってから次の決算の前日までの営業日数。終わっていれば終点の日付'>経過日数</th></tr></thead><tbody>";
  for(const r of view){
    h += `<tr><td class="num pin0">${r.rank}</td>${rkName(r)}
      ${rkMarket(r)}<td class="l" title="${rkEsc(r.sector33 || "")}">${rkEsc(fmtSector(r.sector33) || "–")}</td>
      <td class="num grp">${pct(r.return_to_next)}${under(rkPctParen(r.alpha_to_next))}</td>
      <td class="num">${rkMd(r.disclosed_date)}${under(rkSession(r.session))}</td>
      <td class="num">${rkElapsed(r)}</td></tr>`;
  }
  $("#rk-tbl").innerHTML = h + "</tbody>";
  // 順位と銘柄を左に固定する（銘柄は順位の幅だけずらす）。市場・業種は横に送ると銘柄の下に隠れる
  pinCols($("#rk-tbl"), 2);
  $("#rk-share").hidden = false;
  rkShot = rkDrawShot({...d, rows: view});
}

async function loadRanking(month){
  const q = new URLSearchParams();
  const m = month ?? $("#rk-month").value;
  if(m) q.set("month", m);
  const seq = ++rkSeq;
  $("#rk-info").textContent = "読み込み中…";
  $("#rk-share").hidden = true;
  let d;
  try { d = await api(`/api/ranking/earnings-perf?${q}`); }
  catch(e){
    if(seq !== rkSeq) return;
    rkData = null; rkShot = null;
    $("#rk-info").textContent = "読み込みに失敗しました";
    $("#rk-tbl").innerHTML = ""; $("#rk-share").hidden = true;
    return;
  }
  if(seq !== rkSeq) return;
  rkData = d;
  $("#rk-month").innerHTML = d.months.map(x => `<option value="${x}"${x===d.month?" selected":""}>${rkMonth(x)}決算</option>`).join("");
  $("#rk-month").hidden = !d.months.length;
  rkPaint();
}

function rkTitle(d, n){
  return `${rkMonth(d.month)}決算 ${RK_DIR[rkDir]}ランキングTOP${n}`;
}
function rkPost(d, n){
  return `${rkTitle(d, n)}\n計測方法は決算発表後初値〜\n※場中決算は翌営業日初値`;
}

// 上位の表を PNG にする。見た目は画面の表に合わせる（白地・罫線・正は赤、負は緑）
function rkDrawShot(d){
  const rows = d.rows.slice(0, RK_SHOT_ROWS);
  const css = getComputedStyle(document.documentElement);
  const color = k => css.getPropertyValue(k).trim();
  const font = getComputedStyle(document.body).fontFamily;
  const S = 2, W = 760, PAD = 24, ROW = 40, HEAD = 60;
  const H = HEAD + ROW * (rows.length + 1) + 40;
  const cv = document.createElement("canvas");
  cv.width = W * S; cv.height = H * S;
  const g = cv.getContext("2d");
  g.scale(S, S);
  g.fillStyle = "#fff"; g.fillRect(0, 0, W, H);
  g.textBaseline = "middle";
  g.fillStyle = color("--text");
  g.font = `600 20px ${font}`;
  g.fillText(rkTitle(d, rows.length), PAD, 30);
  // 列: 画面の表と同じ（対TOPIX は騰落率列名の下、場中 / 引け後は決算日の下）
  const X = {rank: 52, name: 64, mkt: 268, sec: 360, perf: 548, date: 618, held: W - PAD};
  const cols = [["順位", X.rank, "right"], ["銘柄", X.name, "left"], ["市場", X.mkt, "left"],
                ["業種", X.sec, "left"], ["騰落率", X.perf, "right"], ["決算日", X.date, "right"],
                ["経過日数", X.held, "right"]];
  const line = y => { g.strokeStyle = color("--border"); g.lineWidth = 1; g.beginPath(); g.moveTo(PAD, y); g.lineTo(W - PAD, y); g.stroke(); };
  let y = HEAD + ROW / 2;
  g.font = `11px ${font}`; g.fillStyle = color("--sub");
  for(const [t, x, a] of cols){
    g.textAlign = a;
    if(t === "騰落率"){
      g.fillText("騰落率", x, y - 7);
      g.fillText("(対TOPIX)", x, y + 9);
    } else {
      g.fillText(t, x, y);
    }
  }
  line(HEAD + ROW);
  const sign = v => v == null ? "–" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
  const tone = v => v == null ? color("--sub") : v >= 0 ? color("--up") : color("--down");
  const even = color("--row");
  const fit = (t, w) => { while(g.measureText(t).width > w && t.length > 2) t = t.slice(0, -2) + "…"; return t; };
  for(const [i, r] of rows.entries()){
    y += ROW;
    if(i % 2 === 1){ g.fillStyle = even; g.fillRect(PAD, y - ROW / 2 + 1, W - PAD * 2, ROW - 1); }
    const y1 = y - 7, y2 = y + 9;
    g.textAlign = "left"; g.fillStyle = color("--text"); g.font = `12px ${font}`;
    const mk = fmtMarket(r.market);
    const dot = {"mkt mkt-p": "#2563eb", "mkt mkt-s": "#9ca3af", "mkt mkt-g": "#16a34a"}[mk.cls];
    if(dot){ g.fillStyle = dot; g.fillRect(X.mkt, y - 4, 7, 7); g.fillStyle = color("--text"); }
    g.fillText(fit(mk.label || "–", 72), X.mkt + (dot ? 11 : 0), y);
    g.fillText(fit(fmtSector(r.sector33).normalize("NFKC") || "–", 106), X.sec, y);
    // J-Quants の名前は全角英数字。画像では半角に寄せて幅を取らない
    g.font = `14px ${font}`;
    g.fillText(fit(r.name.normalize("NFKC").replace(/\s+/g, " "), 180), X.name, y1);
    g.font = `11px ${font}`; g.fillStyle = color("--sub");
    g.fillText(r.code.slice(0,4), X.name, y2);
    const cells = [
      [String(r.rank), X.rank, "right", color("--text"), y, "14px"],
      [sign(r.return_to_next), X.perf, "right", tone(r.return_to_next), y1, "600 14px"],
      [r.alpha_to_next == null ? "" : `(${sign(r.alpha_to_next)})`, X.perf, "right", tone(r.alpha_to_next), y2, "11px"],
      [rkMd(r.disclosed_date), X.date, "right", color("--text"), y1, "14px"],
      [rkSessionTxt(r.session), X.date, "right", color("--sub"), y2, "11px"],
      [rkElapsedTxt(r), X.held, "right", color("--text"), y, "14px"],
    ];
    for(const [t, x, a, fill, yy, size] of cells){
      if(!t) continue;
      g.textAlign = a; g.fillStyle = fill; g.font = `${size} ${font}`;
      g.fillText(t, x, yy);
    }
    line(y + ROW / 2);
  }
  return new Promise(res => cv.toBlob(res, "image/png"));
}

async function rkShare(){
  if(!rkData || !rkShot) return;
  const blob = await rkShot;
  if(!blob) return;
  const view = rkView(rkData);
  const n = Math.min(view.length, RK_SHOT_ROWS);
  const text = rkPost(rkData, n);
  const file = new File([blob], `earnings-perf-${rkDir}-${rkData.month}.png`, {type: "image/png"});
  // スマホは共有シートで画像ごと X へ。PC は画像を保存して X の投稿画面を開く（画像は手で添付）
  const touch = matchMedia("(pointer: coarse)").matches;
  if(touch && navigator.canShare && navigator.canShare({files: [file]})){
    try { await navigator.share({files: [file], text}); } catch(e) {}
    return;
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = file.name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  window.open(`https://x.com/intent/post?text=${encodeURIComponent(text)}`, "_blank", "noopener");
}

$("#rk-month").onchange = () => loadRanking();
$("#rk-share").onclick = rkShare;
document.querySelectorAll("#rk-kind .ftab").forEach(b => {
  b.onclick = () => {
    const dir = b.dataset.dir;
    if(!dir || dir === rkDir) return;
    rkDir = dir;
    document.querySelectorAll("#rk-kind .ftab").forEach(x => x.classList.toggle("on", x === b));
    if(rkData) rkPaint();
  };
});
new ResizeObserver(() => pinCols($("#rk-tbl"), 2)).observe($("#tab-ranking .tbl-wrap"));

window.loadRanking = loadRanking;

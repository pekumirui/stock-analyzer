// ランキングタブ（docs/DESIGN_ranking.md）。今は決算発表後パフォーマンスだけ
const rkEsc = s => String(s ?? "").replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;");
const rkMd = iso => iso ? `${+iso.slice(5,7)}/${+iso.slice(8,10)}` : "–";
const rkMonth = ym => ym ? `${ym.slice(0,4)}年${+ym.slice(5,7)}月` : "";
const rkSession = s => s === "intraday"
  ? `<span class="qb qb-1Q">場中</span>` : `<span class="qb">引け後</span>`;
const rkSessionTxt = s => s === "intraday" ? "場中" : "引け後";
const rkAlpha = v => v==null ? " " : `<span class="sub">α</span> ${pct(v)}`;
const rkName = r => {
  const raw = String(r.name||""), name = rkEsc(raw), code = rkEsc(r.code.slice(0,4));
  return `<td class="l name pin1" title="${name}"><span class="clicky" onclick="gotoChart('${rkEsc(r.code)}')">${rkEsc(shortName(raw))}</span>${under(`<span class="sub">${code}</span>`)}</td>`;
};
const RK_SHOT_ROWS = 20;
// 終点。次の決算がまだの回は現在まで
const rkEnd = r => r.next_disclosed_date ? rkMd(r.end_date) : "現在";
let rkData = null;
let rkShot = null;   // X に投稿する画像（Promise<Blob>）。共有はクリックの中で呼ぶので先に作っておく
let rkSeq = 0;       // 月を続けて変えたとき、古い応答で上書きしない

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
  if(!d.rows.length){
    $("#rk-info").textContent = d.months.length ? "この月の決算はありません" : "決算のデータがまだありません";
    $("#rk-tbl").innerHTML = ""; $("#rk-share").hidden = true; rkShot = null;
    return;
  }
  $("#rk-info").textContent = `${d.rows.length} 銘柄 / ${rkMd(d.as_of)} 終値まで`;
  let h = "<thead><tr><th class='pin0'>順位</th><th class='l name pin1'>銘柄</th>"
        + "<th class='grp'>決算日</th>"
        + "<th title='決算後に買えた最初の値から、次の決算の前日終値まで。下段は対TOPIX'>パフォーマンス</th>"
        + "<th title='次の決算の前日。まだなら現在'>終点</th></tr></thead><tbody>";
  for(const r of d.rows){
    h += `<tr><td class="num pin0">${r.rank}</td>${rkName(r)}
      <td class="num grp">${rkMd(r.disclosed_date)}${under(rkSession(r.session))}</td>
      <td class="num">${pct(r.return_to_next)}${under(rkAlpha(r.alpha_to_next))}</td>
      <td class="num">${rkEnd(r)}${under(r.days == null ? " " : `${fmt(r.days)}日`)}</td></tr>`;
  }
  $("#rk-tbl").innerHTML = h + "</tbody>";
  pinCols($("#rk-tbl"), 2);
  $("#rk-share").hidden = false;
  rkShot = rkDrawShot(d);
}

function rkTitle(d, n){
  return `${rkMonth(d.month)}決算 決算発表後パフォーマンス 上位${n}`;
}

// 上位の表を PNG にする。見た目は画面の表に合わせる（白地・罫線・正は赤、負は緑）
function rkDrawShot(d){
  const rows = d.rows.slice(0, RK_SHOT_ROWS);
  const css = getComputedStyle(document.documentElement);
  const color = k => css.getPropertyValue(k).trim();
  const font = getComputedStyle(document.body).fontFamily;
  const S = 2, W = 760, PAD = 24, ROW = 40, HEAD = 92;
  const H = HEAD + ROW * (rows.length + 1) + 40;
  const cv = document.createElement("canvas");
  cv.width = W * S; cv.height = H * S;
  const g = cv.getContext("2d");
  g.scale(S, S);
  g.fillStyle = "#fff"; g.fillRect(0, 0, W, H);
  g.textBaseline = "middle";
  g.fillStyle = color("--text");
  g.font = `600 20px ${font}`;
  g.fillText(rkTitle(d, rows.length), PAD, 32);
  g.fillStyle = color("--sub");
  g.font = `13px ${font}`;
  g.fillText(`決算後に買えた最初の値から次の決算の前日終値まで（まだなら ${rkMd(d.as_of)} 終値まで）`, PAD, 62);
  // 列: 画面の表と同じ 5 列（発表は決算日の下、α はパフォーマンスの下、日数は終点の下）
  const cols = [["順位", 64, "right"], ["銘柄", 80, "left"], ["決算日", 430, "right"],
                ["パフォーマンス", 580, "right"], ["終点", W - PAD, "right"]];
  const line = y => { g.strokeStyle = color("--border"); g.lineWidth = 1; g.beginPath(); g.moveTo(PAD, y); g.lineTo(W - PAD, y); g.stroke(); };
  let y = HEAD + ROW / 2;
  g.font = `11px ${font}`; g.fillStyle = color("--sub");
  for(const [t, x, a] of cols){ g.textAlign = a; g.fillText(t, x, y); }
  line(HEAD + ROW);
  const sign = v => v == null ? "–" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
  const tone = v => v == null ? color("--sub") : v >= 0 ? color("--up") : color("--down");
  const even = color("--row");
  for(const [i, r] of rows.entries()){
    y += ROW;
    if(i % 2 === 1){ g.fillStyle = even; g.fillRect(PAD, y - ROW / 2 + 1, W - PAD * 2, ROW - 1); }
    const y1 = y - 7, y2 = y + 9;
    g.font = `14px ${font}`;
    // J-Quants の名前は全角英数字。画像では半角に寄せて幅を取らない
    let name = r.name.normalize("NFKC").replace(/\s+/g, " ");
    g.textAlign = "left"; g.fillStyle = color("--text");
    while(g.measureText(name).width > 280 && name.length > 2) name = name.slice(0, -2) + "…";
    g.fillText(name, 80, y1);
    g.font = `11px ${font}`; g.fillStyle = color("--sub");
    g.fillText(r.code.slice(0,4), 80, y2);
    const cells = [
      [String(r.rank), 64, "right", color("--text"), y, "14px"],
      [rkMd(r.disclosed_date), 430, "right", color("--text"), y1, "14px"],
      [rkSessionTxt(r.session), 430, "right", color("--sub"), y2, "11px"],
      [sign(r.return_to_next), 580, "right", tone(r.return_to_next), y, "600 14px"],
      [r.alpha_to_next == null ? "" : `α ${sign(r.alpha_to_next)}`, 580, "right", tone(r.alpha_to_next), y2, "11px"],
      [rkEnd(r), W - PAD, "right", color("--text"), y1, "14px"],
      [r.days == null ? "" : `${r.days}日`, W - PAD, "right", color("--sub"), y2, "11px"],
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
  const n = Math.min(rkData.rows.length, RK_SHOT_ROWS);
  const text = `${rkTitle(rkData, n)}（次の決算の前日終値まで）`;
  const file = new File([blob], `earnings-perf-${rkData.month}.png`, {type: "image/png"});
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
new ResizeObserver(() => pinCols($("#rk-tbl"), 2)).observe($("#tab-ranking .tbl-wrap"));

window.loadRanking = loadRanking;

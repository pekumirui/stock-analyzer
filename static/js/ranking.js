// ランキングタブ（docs/DESIGN_ranking.md）。今は決算発表後パフォーマンスだけ
const rkEsc = s => String(s ?? "").replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;");
const rkMd = iso => iso ? `${+iso.slice(5,7)}/${+iso.slice(8,10)}` : "–";
const rkMonth = ym => ym ? `${ym.slice(0,4)}年${+ym.slice(5,7)}月` : "";
const rkSession = s => s === "intraday" ? "場中" : "引け後";
const RK_SHOT_ROWS = 20;
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
  let h = "<tr><th>順位</th><th class='l'>銘柄</th><th>決算日</th><th class='l'>発表</th>"
        + "<th title='決算後に買えた最初の値から'>パフォーマンス</th><th>対TOPIX</th><th>経過日数</th></tr>";
  for(const r of d.rows){
    h += `<tr><td>${r.rank}</td>
      <td class="l"><span class="clicky" onclick="gotoChart('${rkEsc(r.code)}')">${rkEsc(r.name)}</span> <small>${rkEsc(r.code.slice(0,4))}</small></td>
      <td>${rkMd(r.disclosed_date)}</td><td class="l">${rkSession(r.session)}</td>
      <td>${pct(r.return_to_date)}</td><td>${pct(r.alpha_to_date)}</td><td>${fmt(r.days)}日</td></tr>`;
  }
  $("#rk-tbl").innerHTML = h;
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
  const S = 2, W = 760, PAD = 24, ROW = 30, HEAD = 92;
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
  g.fillText(`${rkMd(d.as_of)} 終値まで / 決算後に買えた最初の値から（引け後は翌営業日の始値、場中は当日の終値）`, PAD, 62);
  // 列: [見出し, 右端 or 左端の x, 揃え]
  const cols = [["順位", 64, "right"], ["銘柄", 80, "left"], ["決算日", 430, "right"], ["発表", 450, "left"],
                ["パフォーマンス", 600, "right"], ["対TOPIX", 680, "right"], ["経過", W - PAD, "right"]];
  const line = y => { g.strokeStyle = color("--border"); g.lineWidth = 1; g.beginPath(); g.moveTo(PAD, y); g.lineTo(W - PAD, y); g.stroke(); };
  let y = HEAD + ROW / 2;
  g.font = `12px ${font}`; g.fillStyle = color("--sub");
  for(const [t, x, a] of cols){ g.textAlign = a; g.fillText(t, x, y); }
  line(HEAD + ROW);
  const sign = v => v == null ? "–" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
  const tone = v => v == null ? color("--sub") : v >= 0 ? color("--up") : color("--down");
  for(const r of rows){
    y += ROW;
    g.font = `14px ${font}`;
    // J-Quants の名前は全角英数字。画像では半角に寄せて幅を取らない
    const name = r.name.normalize("NFKC").replace(/\s+/g, " ");
    const cells = [String(r.rank), name, rkMd(r.disclosed_date), rkSession(r.session),
                   sign(r.return_to_date), sign(r.alpha_to_date), `${r.days}日`];
    cells.forEach((v, i) => {
      const [, x, a] = cols[i];
      g.textAlign = a;
      g.fillStyle = i === 4 ? tone(r.return_to_date) : i === 5 ? tone(r.alpha_to_date) : color("--text");
      if(i === 4) g.font = `600 14px ${font}`;
      let s = v;
      if(i === 1){
        const code = ` ${r.code.slice(0,4)}`;
        while(g.measureText(s + code).width > 290 && s.length > 2) s = s.slice(0, -2) + "…";
        s += code;
      }
      g.fillText(s, x, y);
      g.font = `14px ${font}`;
    });
    line(y + ROW / 2);
  }
  return new Promise(res => cv.toBlob(res, "image/png"));
}

async function rkShare(){
  if(!rkData || !rkShot) return;
  const blob = await rkShot;
  if(!blob) return;
  const n = Math.min(rkData.rows.length, RK_SHOT_ROWS);
  const text = `${rkTitle(rkData, n)}（${rkMd(rkData.as_of)} 終値まで）`;
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

window.loadRanking = loadRanking;

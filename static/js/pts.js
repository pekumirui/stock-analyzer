const ptsEsc = s => String(s ?? "").replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;");
const ptsMd = iso => iso ? `${+iso.slice(5,7)}/${+iso.slice(8,10)}` : "–";
const ptsFollow = v => v==null ? "–" : fmt(v,2);
let ptsRanking = "night_up";

async function loadPts(opts = {}){
  const q = new URLSearchParams({ranking: ptsRanking});
  const date = opts.date ?? $("#pts-date").value;
  if(date) q.set("date", date);
  if(opts.at) q.set("at", opts.at);
  let d;
  try { d = await api(`/api/pts?${q}`); }
  catch(e){ $("#pts-info").textContent = "読み込みに失敗しました"; $("#pts-tbl").innerHTML = ""; return; }
  $("#pts-date").innerHTML = d.dates.map(x => `<option value="${x}"${x===d.date?" selected":""}>${x}</option>`).join("");
  $("#pts-at").innerHTML = d.snapshots.map(s =>
    `<option value="${s.fetched_at}"${s.fetched_at===d.fetched_at?" selected":""}>${s.fetched_at.slice(5,16)}${s.as_of?`（${s.as_of}現在）`:""}</option>`).join("");
  $("#pts-at").hidden = !d.snapshots.length;
  if(!d.rows.length){
    $("#pts-info").textContent = d.dates.length ? "この日のランキングはありません" : "PTS のデータがまだありません";
    $("#pts-summary").textContent = "";
    $("#pts-tbl").innerHTML = "";
    return;
  }
  const next = d.rows.find(r => r.next_date)?.next_date;
  $("#pts-info").textContent = `${d.date} 終値比 / 翌営業日 ${next || "未取込"}`;
  const s = d.summary;
  $("#pts-summary").textContent = s && s.n
    ? `翌始値あり ${s.n} 銘柄: PTS→翌始値 平均 ${s.avg_open_vs_pts_pct>0?"+":""}${fmt(s.avg_open_vs_pts_pct,2)}% / 追随率 中央値 ${ptsFollow(s.median_follow_ratio)}`
    : "";
  let h = "<tr><th>順位</th><th class='l'>銘柄</th><th class='l'>市場</th><th>終値</th><th>PTS</th><th>終値比</th><th>PTS出来高</th>"
        + "<th>翌始値</th><th>終値→翌始値</th><th>PTS→翌始値</th><th title='1=PTSどおり、0=終値に戻った'>追随率</th></tr>";
  for(const r of d.rows){
    const nd = r.next_date && r.next_date !== next ? ` <small>${ptsMd(r.next_date)}</small>` : "";
    h += `<tr><td>${r.rank}</td>
      <td class="l"><span class="clicky" onclick="gotoChart('${ptsEsc(r.code)}')">${ptsEsc(r.name)}</span> <small>${ptsEsc(r.code.slice(0,4))}</small></td>
      <td class="l">${ptsEsc(r.market)}</td><td>${fmt(r.base_close,1)}</td><td>${fmt(r.pts_price,1)}</td>
      <td>${pct(r.pts_chg_pct)}</td><td>${fmt(r.volume)}</td>
      <td>${fmt(r.next_open,1)}${nd}</td><td>${pct(r.open_gap_pct)}</td><td>${pct(r.open_vs_pts_pct)}</td>
      <td>${ptsFollow(r.follow_ratio)}</td></tr>`;
  }
  $("#pts-tbl").innerHTML = h;
}

$("#pts-date").onchange = () => loadPts();
document.querySelectorAll("#pts-ranking .ftab").forEach(b => b.onclick = () => {
  if(b.dataset.ranking === ptsRanking) return;
  ptsRanking = b.dataset.ranking;
  document.querySelectorAll("#pts-ranking .ftab").forEach(x => x.classList.toggle("on", x === b));
  loadPts();
});
$("#pts-at").onchange = e => loadPts({at: e.target.value});

window.loadPts = loadPts;

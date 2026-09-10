// ---- スクリーナー ----
async function loadScreener(){
  const st=$("#sc-stage").value, rs=$("#sc-rs").value||0;
  const d = await api(`/api/screener?${st?`stage=${st}&`:""}rs_min=${rs}`);
  $("#sc-date").textContent = `基準日: ${d.date}`;
  let h = "<tr><th class='l'>銘柄</th><th class='l'>業種</th><th>株価</th><th>RS</th><th>Stage</th><th>52週安値比</th><th>52週高値比</th><th>MA50</th><th>MA200</th></tr>";
  for(const r of d.results){
    h += `<tr><td class="l"><span class="clicky" onclick="gotoChart('${r.code}')">${r.name}</span> <small>${r.code.slice(0,4)}</small></td>
      <td class="l">${r.sector||''}</td><td>${fmt(r.close,1)}</td><td><b>${r.rs_rank??'–'}</b></td>
      <td>${r.stage?`<span class="badge st${r.stage}">S${r.stage}</span>`:'–'}</td>
      <td>${pct(r.pct_from_low52)}</td><td>${pct(r.pct_from_high52)}</td>
      <td>${fmt(r.ma50,1)}</td><td>${fmt(r.ma200,1)}</td></tr>`;
  }
  $("#sc-tbl").innerHTML = h;
}

window.loadScreener = loadScreener;

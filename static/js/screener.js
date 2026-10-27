const scEsc = s => String(s ?? "").replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;");

async function loadScreener(){
  const st=$("#sc-stage").value, rs=$("#sc-rs").value||0;
  const d = await api(`/api/screener?${st?`stage=${st}&`:""}rs_min=${rs}`);
  $("#sc-date").textContent = `基準日: ${d.date}`;
  let h = "<thead><tr><th class='l name pin0'>銘柄</th><th class='l grp'>業種</th>"
        + "<th>株価</th><th>RS</th><th>Stage</th><th title='52週安値比'>安値比</th><th title='52週高値比'>高値比</th>"
        + "<th>MA50</th><th>MA200</th></tr></thead><tbody>";
  for(const r of d.results){
    const raw = String(r.name||""), name = scEsc(raw), code = scEsc(r.code);
    h += `<tr><td class="l name pin0" title="${name}"><span class="clicky" onclick="gotoChart('${code}')">${scEsc(shortName(raw))}</span>${under(`<span class="sub">${scEsc(r.code.slice(0,4))}</span>`)}</td>
      <td class="l grp" title="${scEsc(r.sector||"")}">${scEsc(fmtSector(r.sector))}</td><td class="num">${fmt(r.close,1)}</td><td class="num">${r.rs_rank??"–"}</td>
      <td>${r.stage?`<span class="badge st${r.stage}">S${r.stage}</span>`:"–"}</td>
      <td class="num">${pct(r.pct_from_low52)}</td><td class="num">${pct(r.pct_from_high52)}</td>
      <td class="num">${fmt(r.ma50,1)}</td><td class="num">${fmt(r.ma200,1)}</td></tr>`;
  }
  $("#sc-tbl").innerHTML = h + "</tbody>";
  pinCols($("#sc-tbl"), 1);
}

new ResizeObserver(() => pinCols($("#sc-tbl"), 1)).observe($("#tab-screener .tbl-wrap"));
window.loadScreener = loadScreener;

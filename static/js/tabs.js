// ---- tabs ----
document.querySelectorAll("nav button").forEach(b => b.onclick = () => {
  document.querySelectorAll("nav button").forEach(x=>x.classList.remove("active"));
  b.classList.add("active");
  ["earnings","chart","screener","monthly"].forEach(t => $("#tab-"+t).style.display = t===b.dataset.tab?"":"none");
  if (b.dataset.tab==="screener" && !$("#sc-tbl").innerHTML) loadScreener();
  if (b.dataset.tab==="monthly") loadMonthlyList();
});

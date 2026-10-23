"use strict";
const $ = (id) => document.getElementById(id);
const state = { report: null, filter: "verified", query: "", selected: "0062", shown: [] };
const statusLabels = { verified: "完整几何链通过", retained: "停机前缀 · 未通过", pending: "尚未完成完整链" };
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const megabytes = (value) => (value / 1e6).toFixed(1) + " MB";

function filteredRows() {
  return state.report.rows.filter(row => {
    const matches = state.filter === "all" || (state.filter === "basic" ? row.tier === 0 : row.status === state.filter);
    return matches && [row.part, row.name, row.category, row.scope].join(" ").toLowerCase().includes(state.query.toLowerCase());
  });
}

function setFilter(filter) {
  state.filter = filter;
  document.querySelectorAll("[data-filter]").forEach(button => {
    const active = button.dataset.filter === filter;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  renderRows();
}

function renderRows() {
  const rows = state.shown = filteredRows();
  $("table-count").textContent = `显示 ${rows.length} / 71 份文件 · 证据口径：完整原几何、连续材料与本机回放`;
  $("part-table").innerHTML = rows.length ? rows.map(row => {
    const tag = row.status === "verified" ? "teal-tag" : row.status === "retained" ? "amber" : "pending-tag";
    const name = row.name.replace(/^STKJ-MT01-[\d-]+\s*/, "");
    const metrics = row.replay?.sampled_max_mm;
    const evidence = row.replay ? `<p>${row.replay.states} 个保存状态，${row.replay.moves.toLocaleString("zh-CN")} 条运动。${Number.isFinite(metrics) ? `保守最大采样偏差 ${metrics.toFixed(6)} mm；` : ""}沿用原 0.05 mm 判据。</p>` : "";
    const partialLinks = (row.partial_replays || []).map(replay => `<p><a class="replay-link" href="${escape(replay.url)}" target="_blank" rel="noopener">第 ${replay.original_solid_index + 1} 个实体 ↗</a><span class="file-size">${replay.states} 状态 · ${megabytes(replay.bytes)}</span></p>`).join("");
    const partialDisplay = row.partial_replays?.length > 3 ? `<details class="component-replays"><summary>${row.partial_replays.length} 个构件回放</summary>${partialLinks}</details>` : partialLinks;
    return `<tr id="part-${row.no}"><td><span class="part-id">${escape(row.part)} / ${row.solids} ${row.solids===1 ? "个原实体" : "次实体出现"}</span><span class="part-name">${escape(name)}</span><span class="part-meta">${escape(row.tier_name)}</span></td><td><span class="tag ${tag}">${statusLabels[row.status]}</span><span class="part-meta">${row.status === "verified" ? "制造交付仍未验证" : "不计入 16 份已覆盖文件"}</span></td><td><span class="scope-title">${escape(row.category)}</span><p class="scope-text">${escape(row.scope)}</p><details><summary>证据与后续</summary>${evidence}<p>${escape(row.evidence_note)}</p><p>下一步：${escape(row.next_action)}</p></details></td><td>${row.replay ? `<button class="replay-link" data-play="${escape(row.part)}" aria-label="播放 ${escape(row.name)} 的实际材料过程">查看回放 ↗</button><span class="file-size">${megabytes(row.replay.bytes)}</span>` : partialDisplay || `<span class="part-meta">${row.status === "retained" ? "失败前缀已保留" : "待补齐完整证据"}</span>`}</td></tr>`;
  }).join("") : '<tr><td colspan="4" class="empty-state">未找到匹配的零件，请尝试编号或更短的关键词。</td></tr>';
  $("part-table").querySelectorAll("[data-play]").forEach(button => button.addEventListener("click", () => selectDemo(button.dataset.play, true)));
}

function renderGrid() {
  $("part-grid").innerHTML = [...state.report.rows].sort((a,b) => a.no-b.no).map(row => `<button class="${row.status}" data-row="${row.no}" aria-label="${escape(row.name)}：${statusLabels[row.status]}" title="${escape(row.name)} · ${statusLabels[row.status]}"></button>`).join("");
  $("part-grid").querySelectorAll("button").forEach(button => button.addEventListener("click", () => {
    state.query = "";
    $("part-search").value = "";
    setFilter("all");
    const row = $("part-" + button.dataset.row);
    row.classList.add("highlighted");
    row.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
    setTimeout(() => row.classList.remove("highlighted"), 2500);
  }));
}

function selectDemo(part, scroll = false) {
  const row = state.report.rows.find(row => row.part === part);
  if (!row?.replay) return;
  state.selected = part;
  document.querySelectorAll("[data-demo]").forEach(tab => {
    const selected = tab.dataset.demo === part;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  if ($("tab-" + part)) $("demo-panel").setAttribute("aria-labelledby", "tab-" + part);
  else { $("demo-panel").removeAttribute("aria-labelledby"); $("demo-panel").setAttribute("aria-label", row.name + "实际过程回放"); }
  $("demo-title").textContent = row.name.replace(/^STKJ-MT01-/, "");
  $("demo-meta").textContent = `${row.replay.states} 个已保存材料状态 · ${megabytes(row.replay.bytes)}`;
  $("demo-scope").textContent = row.scope;
  $("open-replay").href = row.replay.url;
  $("replay-frame").title = row.name + "实际材料过程回放";
  $("viewer-loading").hidden = false;
  $("viewer-loading").textContent = "正在加载实际材料回放…";
  $("replay-frame").src = row.replay.url;
  if (scroll) $("replay").scrollIntoView({ block: "start" });
}

let frameResize = null;
$("replay-frame").addEventListener("load", () => {
  const iframe = $("replay-frame");
  if (iframe.getAttribute("src") === "about:blank") return;
  try {
    const doc = iframe.contentDocument;
    if (!doc?.getElementById("replay-data")) throw new Error("回放页面未正确载入");
    const style = doc.createElement("style");
    style.textContent = `body{background:#fff}main{max-width:none;padding:18px 22px}main>header{display:none}.layout{margin-top:0;grid-template-columns:minmax(0,1fr) 265px;gap:16px}.stage{height:370px}.iso-plate,aside{border-radius:5px}.cardhead,.cardfoot{padding:10px 15px}.controls{padding:12px 15px}.metrics{padding:16px;gap:12px}.metric strong{font-size:16px}.sidebody{padding:0 16px 16px}.sidebody pre{max-height:60px;font-size:10px}.step-list{max-height:100px}.footer{display:none}.stepname{font-size:16px}.note{font-size:11px}@media(max-width:800px){main{padding:12px}.layout{grid-template-columns:1fr}.stage{height:310px}.metrics{grid-template-columns:repeat(4,1fr)}.metric strong{font-size:14px}.metric small{font-size:10px}.sidebody{padding:0 16px 16px}.sidebody>h2{margin-top:12px}.step-list{max-height:90px}.sidebody pre{max-height:50px}}@media(max-width:420px){.metrics{grid-template-columns:repeat(2,1fr)}.stage{height:285px}.buttons{gap:5px}.buttons button,.buttons select{font-size:11px;padding:6px 8px}.toggles{gap:8px;font-size:10px}.cardhead,.cardfoot{font-size:9px}}`;
    doc.head.appendChild(style);
    frameResize?.disconnect();
    frameResize = new ResizeObserver(() => {
      const main = doc.querySelector("main");
      iframe.style.height = Math.ceil(main.getBoundingClientRect().height + 4) + "px";
    });
    frameResize.observe(doc.querySelector("main"));
    $("viewer-loading").hidden = true;
  } catch (error) {
    $("viewer-loading").textContent = "嵌入回放未载入，请使用右上角“独立打开”。";
    console.error(error);
  }
});

document.querySelectorAll("[data-demo]").forEach(tab => {
  tab.addEventListener("click", () => selectDemo(tab.dataset.demo));
  tab.addEventListener("keydown", event => {
    const tabs = [...document.querySelectorAll("[data-demo]")];
    const at = tabs.indexOf(tab);
    const next = event.key === "ArrowRight" ? (at+1)%tabs.length : event.key === "ArrowLeft" ? (at+tabs.length-1)%tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length-1 : null;
    if (next === null) return;
    event.preventDefault();
    tabs[next].click(); tabs[next].focus();
  });
});
document.querySelectorAll("[data-filter]").forEach(button => button.addEventListener("click", () => state.report && setFilter(button.dataset.filter)));
$("part-search").addEventListener("input", event => { state.query = event.target.value.trim(); if (state.report) renderRows(); });
$("download-csv").addEventListener("click", () => {
  if (!state.report) return;
  const escapeCell = value => '"' + String(value ?? "").replace(/^[=+@-]/, value => "'"+value).replace(/"/g, '""') + '"';
  const rows = [["编号","名称","原实体出现次数","难度档位","当前证据","覆盖范围","下一步"], ...state.shown.map(row => [row.part,row.name,row.solids,row.tier_name,statusLabels[row.status],row.scope,row.next_action])];
  const url = URL.createObjectURL(new Blob(["\uFEFF" + rows.map(row => row.map(escapeCell).join(",")).join("\r\n")], {type:"text/csv;charset=utf-8"}));
  const anchor = document.createElement("a"); anchor.href=url;anchor.download="GenLaw-零件证据清单-2026-10.csv";anchor.click();setTimeout(() => URL.revokeObjectURL(url),1000);
});
$("print-report").addEventListener("click", () => window.print());

fetch("data/report.json").then(response => {
  if (!response.ok) throw new Error("证据清单请求失败："+response.status);
  return response.json();
}).then(report => {
  state.report = report;
  const formatted = new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(new Date(report.updated_at));
  $("updated-at").textContent = formatted + " · 北京时间";
  $("updated-at").dateTime = report.updated_at;
  renderRows();renderGrid();selectDemo(state.selected);
  window.genlawReport = Object.freeze({ get state() { return { loaded:true, rows:report.rows.length, filtered:state.shown.length, filter:state.filter, selected:state.selected }; }});
}).catch(error => {
  $("table-count").textContent = "证据清单暂未载入，请刷新或下载页底报告数据。";
  $("viewer-loading").textContent = "清单未载入，可使用右上角“独立打开”观看安装板回放。";
  console.error(error);
});

const navLinks = [...document.querySelectorAll(".site-header nav a")];
const observer = new IntersectionObserver(entries => {
  for (const entry of entries) if (entry.isIntersecting) navLinks.forEach(link => link.classList.toggle("active",link.hash === "#"+entry.target.id));
}, {rootMargin:"-10% 0px -60% 0px"});
navLinks.forEach(link => observer.observe(document.querySelector(link.hash)));

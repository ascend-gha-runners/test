/**
 * Ascend CI / E2E Test Quality Report Dashboard
 * Client Application Logic
 */

let appData = null;
let selectedDate = null;
let currentTab = "by-date";
let autoRefreshTimer = null;
let autoRefreshSeconds = 30;
let countdown = autoRefreshSeconds;

// DOM Elements
const elLastUpdated = document.getElementById("last-updated");
const elAutoRefreshTimer = document.getElementById("auto-refresh-timer");
const elBtnToggleRefresh = document.getElementById("btn-toggle-refresh");
const elBtnManualRefresh = document.getElementById("btn-manual-refresh");
const elThemeToggle = document.getElementById("btn-theme-toggle");

// KPI Elements
const kpiTodayTotal = document.getElementById("kpi-today-total");
const kpiTodayPass = document.getElementById("kpi-today-pass");
const kpiTodayRate = document.getElementById("kpi-today-rate");
const kpiTotalRuns = document.getElementById("kpi-total-runs");
const kpiOverallRate = document.getElementById("kpi-overall-rate");
const kpiActiveSuites = document.getElementById("kpi-active-suites");
const kpiRecentFailures = document.getElementById("kpi-recent-failures");

// Filter inputs
const searchInput = document.getElementById("search-input");
const statusFilter = document.getElementById("status-filter");

// Content containers
const dateStripContainer = document.getElementById("date-strip");
const tabContentByDate = document.getElementById("content-by-date");
const tabContentByCase = document.getElementById("content-by-case");
const tabContentMatrix = document.getElementById("content-matrix");
const tabContentFailures = document.getElementById("content-failures");

// Initialize application
async function initApp() {
  setupTheme();
  setupEventListeners();
  await loadData();
  startAutoRefresh();
}

// Setup Theme
function setupTheme() {
  const saved = localStorage.getItem("report-theme") || "dark";
  document.documentElement.setAttribute("data-theme", saved);
  updateThemeIcon(saved);
}

function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") || "dark";
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("report-theme", next);
  updateThemeIcon(next);
}

function updateThemeIcon(theme) {
  if (elThemeToggle) {
    elThemeToggle.innerHTML = theme === "dark" ? "☀️ 亮色" : "🌙 暗色";
  }
}

// Fetch report data
async function loadData(silent = false) {
  try {
    const url = `data/report_data.json?_t=${Date.now()}`;
    const resp = await fetch(url);
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    appData = await resp.json();
    renderAll();
    if (!silent) {
      console.log("[Dashboard] Data refreshed successfully");
    }
  } catch (err) {
    console.error("[Dashboard] Failed to load report data:", err);
    if (!silent) {
      alert("无法加载测试报告数据，请稍后刷新重试: " + err.message);
    }
  }
}

// Auto Refresh Control
function startAutoRefresh() {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer);
  countdown = autoRefreshSeconds;
  autoRefreshTimer = setInterval(() => {
    countdown--;
    if (elAutoRefreshTimer) {
      elAutoRefreshTimer.textContent = `${countdown}s`;
    }
    if (countdown <= 0) {
      countdown = autoRefreshSeconds;
      loadData(true);
    }
  }, 1000);
}

function stopAutoRefresh() {
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
  if (elAutoRefreshTimer) {
    elAutoRefreshTimer.textContent = "已暂停";
  }
}

function toggleAutoRefresh() {
  if (autoRefreshTimer) {
    stopAutoRefresh();
    elBtnToggleRefresh.innerHTML = "▶ 开启自动刷新";
  } else {
    startAutoRefresh();
    elBtnToggleRefresh.innerHTML = "⏸ 暂停自动刷新";
  }
}

// Render Master
function renderAll() {
  if (!appData) return;

  // Header meta
  if (elLastUpdated) {
    elLastUpdated.textContent = appData.meta.generated_at_beijing || appData.meta.generated_at;
  }

  renderKPIs();
  renderDateStrip();
  renderCurrentTab();
}

// Render KPIs
function renderKPIs() {
  const today = appData.today || {};
  const meta = appData.meta || {};

  kpiTodayTotal.textContent = today.total || 0;
  kpiTodayPass.textContent = `${today.passed || 0} 通过 / ${today.failed || 0} 失败`;
  kpiTodayRate.textContent = `${today.pass_rate || 100}%`;
  kpiTodayRate.className = `rate-badge ${today.failed > 0 ? "badge-danger" : "badge-success"}`;

  kpiTotalRuns.textContent = meta.total_runs || 0;
  kpiOverallRate.textContent = `${meta.overall_pass_rate || 100}%`;
  kpiOverallRate.className = `rate-badge ${meta.overall_pass_rate < 90 ? "badge-danger" : "badge-success"}`;

  kpiActiveSuites.textContent = meta.total_test_cases || 0;

  const failCount = (appData.recent_failures || []).length;
  kpiRecentFailures.textContent = failCount;
  kpiRecentFailures.className = failCount > 0 ? "kpi-value badge-danger" : "kpi-value";
}

// Render Date Strip for Day View
function renderDateStrip() {
  if (!dateStripContainer) return;

  const dates = appData.dates_list || [];
  if (dates.length === 0) {
    dateStripContainer.innerHTML = `<div class="empty-state">暂无测试日期记录</div>`;
    return;
  }

  if (!selectedDate || !dates.includes(selectedDate)) {
    selectedDate = dates[0]; // Default to newest date
  }

  let html = "";
  for (const d of dates) {
    const dayData = appData.by_date[d] || {};
    const summary = dayData.summary || {};
    const isAct = d === selectedDate ? "active" : "";
    const rate = summary.pass_rate !== undefined ? `${summary.pass_rate}%` : "100%";
    const rateColor = summary.failed > 0 ? "var(--danger)" : "var(--success)";

    html += `
      <div class="date-pill ${isAct}" onclick="selectDate('${d}')">
        <div class="date-pill-label">${d}</div>
        <div class="date-pill-rate" style="color: ${rateColor}">${rate}</div>
        <div class="date-pill-meta">${summary.passed || 0}✓ / ${summary.failed || 0}✗ (${summary.total || 0}次)</div>
      </div>
    `;
  }
  dateStripContainer.innerHTML = html;
}

function selectDate(dateStr) {
  selectedDate = dateStr;
  renderDateStrip();
  if (currentTab === "by-date") {
    renderByDateTab();
  }
}

// Render Tabs
function renderCurrentTab() {
  // Update Tab Badges
  const badgeDate = document.getElementById("badge-tab-date");
  if (badgeDate && appData.dates_list) badgeDate.textContent = appData.dates_list.length;

  const badgeCase = document.getElementById("badge-tab-case");
  if (badgeCase && appData.test_cases_list) badgeCase.textContent = appData.test_cases_list.length;

  const badgeFail = document.getElementById("badge-tab-fail");
  if (badgeFail && appData.recent_failures) badgeFail.textContent = appData.recent_failures.length;

  // Render active tab content
  if (currentTab === "by-date") renderByDateTab();
  else if (currentTab === "by-case") renderByCaseTab();
  else if (currentTab === "matrix") renderMatrixTab();
  else if (currentTab === "failures") renderFailuresTab();
}

// 1. By Date Tab
function renderByDateTab() {
  if (!tabContentByDate) return;

  const dayData = appData.by_date[selectedDate] || { runs: [] };
  const query = (searchInput.value || "").toLowerCase().trim();
  const filterStat = statusFilter.value;

  let filtered = dayData.runs.filter((r) => {
    // Status filter
    if (filterStat === "success" && r.conclusion !== "success") return false;
    if (filterStat === "failure" && r.conclusion !== "failure" && r.conclusion !== "timed_out") return false;

    // Search query
    if (query) {
      const matchName = (r.name || "").toLowerCase().includes(query);
      const matchTitle = (r.title || "").toLowerCase().includes(query);
      const matchWf = (r.workflow_name || "").toLowerCase().includes(query);
      const matchCluster = (r.jobs || []).some(j => (j.cluster || "").toLowerCase().includes(query));
      if (!matchName && !matchTitle && !matchWf && !matchCluster) return false;
    }
    return true;
  });

  if (filtered.length === 0) {
    tabContentByDate.innerHTML = `
      <div class="empty-state">
        <h3>未找到符合条件的测试运行</h3>
        <p>所选日期 (${selectedDate}) 或当前筛选条件下暂无测试记录</p>
      </div>
    `;
    return;
  }

  let html = `<div class="runs-list">`;
  for (const r of filtered) {
    const isSuccess = r.conclusion === "success";
    const statusIcon = isSuccess ? "✓" : "✗";
    const statusClass = isSuccess ? "status-success" : "status-failure";
    const statusText = isSuccess ? "PASSED" : (r.conclusion || "FAILED").toUpperCase();
    const duration = formatDuration(r.duration_seconds);
    const runTime = (r.created_at || "").replace("T", " ").replace("Z", " UTC");

    html += `
      <div class="run-card" id="run-${r.id}">
        <div class="run-header" onclick="toggleRunAccordion('${r.id}')">
          <div class="run-header-left">
            <div class="status-icon ${statusClass}">${statusIcon}</div>
            <div class="run-title-group">
              <h3>${escapeHtml(r.title || r.name)}</h3>
              <div class="run-meta">
                <span>📁 <code>${escapeHtml(r.workflow_name)}</code></span>
                <span>⏱️ ${duration}</span>
                <span>🕒 ${runTime}</span>
                <span>🌿 ${escapeHtml(r.branch || "main")}</span>
                <span>📌 #${r.id}</span>
                ${r.jobs_count ? `<span>📊 ${r.jobs_passed}/${r.jobs_count} Jobs 通过</span>` : ""}
              </div>
            </div>
          </div>
          <div class="run-header-right">
            <span class="rate-badge ${isSuccess ? 'badge-success' : 'badge-danger'}">${statusText}</span>
            <a href="${r.url}" target="_blank" class="btn" onclick="event.stopPropagation()">查看 GitHub 运行 ↗</a>
            <button class="btn" onclick="toggleRunAccordion('${r.id}'); event.stopPropagation()">展开集群明细 ▾</button>
          </div>
        </div>
        <div class="run-body" id="body-${r.id}">
          ${renderJobsTable(r.jobs || [])}
        </div>
      </div>
    `;
  }
  html += `</div>`;
  tabContentByDate.innerHTML = html;
}

function renderJobsTable(jobs) {
  if (!jobs || jobs.length === 0) {
    return `<div style="padding: 12px; color: var(--text-muted); font-size: 0.85rem;">该运行无细分子 Job 或为单一 Runner 执行</div>`;
  }

  let rows = "";
  for (const j of jobs) {
    const isSucc = j.conclusion === "success";
    const dur = formatDuration(j.duration_seconds);
    const failStep = j.failed_step ? `<span style="color: var(--danger); font-weight: 500;">(失败步骤: ${escapeHtml(j.failed_step)})</span>` : "";

    rows += `
      <tr>
        <td><span class="cluster-tag">${escapeHtml(j.cluster || "default")}</span></td>
        <td><strong>${escapeHtml(j.name)}</strong> ${failStep}</td>
        <td>
          <span class="rate-badge ${isSucc ? 'badge-success' : 'badge-danger'}">
            ${isSucc ? '✓ 通过' : '✗ 失败'}
          </span>
        </td>
        <td>${dur}</td>
        <td><a href="${j.url}" target="_blank" class="btn" style="padding: 3px 8px; font-size: 0.78rem;">Job 日志 ↗</a></td>
      </tr>
    `;
  }

  return `
    <table class="jobs-table">
      <thead>
        <tr>
          <th>目标集群 (Cluster)</th>
          <th>Job / 矩阵测试项</th>
          <th>状态</th>
          <th>耗时</th>
          <th>日志链接</th>
        </tr>
      </thead>
      <tbody>
        ${rows}
      </tbody>
    </table>
  `;
}

function toggleRunAccordion(runId) {
  const el = document.getElementById(`body-${runId}`);
  if (el) {
    el.classList.toggle("open");
  }
}

// 2. By Test Case Tab
function renderByCaseTab() {
  if (!tabContentByCase) return;

  const cases = appData.test_cases_list || [];
  const query = (searchInput.value || "").toLowerCase().trim();

  let filtered = cases.filter((c) => {
    if (query) {
      const matchId = (c.id || "").toLowerCase().includes(query);
      const matchName = (c.name || "").toLowerCase().includes(query);
      const matchWf = (c.workflow || "").toLowerCase().includes(query);
      if (!matchId && !matchName && !matchWf) return false;
    }
    return true;
  });

  if (filtered.length === 0) {
    tabContentByCase.innerHTML = `<div class="empty-state"><h3>未找到匹配的测试用例</h3></div>`;
    return;
  }

  let html = `<div class="catalog-grid">`;
  for (const tc of filtered) {
    const isSucc = tc.latest_conclusion === "success";
    const statusBadge = isSucc ? '<span class="rate-badge badge-success">✓ 正常</span>' : '<span class="rate-badge badge-danger">✗ 异常</span>';
    const rateBadge = tc.pass_rate >= 90 ? 'badge-success' : (tc.pass_rate >= 70 ? 'badge-warning' : 'badge-danger');
    const docLink = tc.doc_url ? `<a href="${tc.doc_url}" target="_blank" style="color: var(--accent); text-decoration: none; font-size: 0.78rem;">平台文档 ↗</a>` : "";

    let assertionsHtml = "";
    if (tc.assertions && tc.assertions.hard && tc.assertions.hard.length > 0) {
      assertionsHtml = `
        <div class="tc-assertions">
          <strong>硬断言要求 (Hard Assertions):</strong>
          <ul style="padding-left: 18px; margin-top: 4px; color: var(--text-secondary);">
            ${tc.assertions.hard.slice(0, 3).map(a => `<li>${escapeHtml(a)}</li>`).join("")}
          </ul>
        </div>
      `;
    }

    html += `
      <div class="tc-card">
        <div>
          <div class="tc-header">
            <div>
              <span class="tc-id">${escapeHtml(tc.id)}</span>
              <h3 class="tc-title">${escapeHtml(tc.name)}</h3>
            </div>
            ${statusBadge}
          </div>
          <div class="tc-desc">工作流: <code>${escapeHtml(tc.workflow)}</code> ${docLink ? " | " + docLink : ""}</div>
          ${assertionsHtml}
        </div>
        <div class="tc-footer">
          <div>
            通过率: <span class="rate-badge ${rateBadge}">${tc.pass_rate}%</span>
            <span style="font-size: 0.75rem; margin-left: 6px;">(${tc.passed}/${tc.total} 次)</span>
          </div>
          <div>
            ${tc.latest_url ? `<a href="${tc.latest_url}" target="_blank" class="btn" style="font-size: 0.75rem; padding: 4px 8px;">最新执行 ↗</a>` : ""}
          </div>
        </div>
      </div>
    `;
  }
  html += `</div>`;
  tabContentByCase.innerHTML = html;
}

// 3. Matrix View Tab
function renderMatrixTab() {
  if (!tabContentMatrix) return;

  const matrix = appData.matrix || { dates: [], rows: [] };
  const dates = matrix.dates || [];
  const rows = matrix.rows || [];

  if (dates.length === 0 || rows.length === 0) {
    tabContentMatrix.innerHTML = `<div class="empty-state"><h3>暂无矩阵数据</h3></div>`;
    return;
  }

  let headCols = dates.map(d => `<th>${d.substring(5)}</th>`).join("");
  let bodyRows = "";

  for (const r of rows) {
    let cells = "";
    for (const d of dates) {
      const statusObj = r.statuses[d];
      if (!statusObj) {
        cells += `<td><span class="matrix-cell matrix-empty">-</span></td>`;
      } else if (statusObj.conclusion === "success") {
        cells += `<td><a href="${statusObj.url}" target="_blank" class="matrix-cell matrix-pass" title="${d}: 通过">✓</a></td>`;
      } else {
        cells += `<td><a href="${statusObj.url}" target="_blank" class="matrix-cell matrix-fail" title="${d}: 失败">✗</a></td>`;
      }
    }

    bodyRows += `
      <tr>
        <td>
          <div style="font-weight: 600;">${escapeHtml(r.name)}</div>
          <code style="font-size: 0.75rem; color: var(--text-muted);">${escapeHtml(r.id)}</code>
        </td>
        ${cells}
      </tr>
    `;
  }

  tabContentMatrix.innerHTML = `
    <div class="matrix-container">
      <table class="matrix-table">
        <thead>
          <tr>
            <th>测试用例 / 能力验证项</th>
            ${headCols}
          </tr>
        </thead>
        <tbody>
          ${bodyRows}
        </tbody>
      </table>
    </div>
  `;
}

// 4. Failures Tab
function renderFailuresTab() {
  if (!tabContentFailures) return;

  const fails = appData.recent_failures || [];
  if (fails.length === 0) {
    tabContentFailures.innerHTML = `
      <div class="empty-state">
        <h3 style="color: var(--success);">🎉 近期无任何失败记录！</h3>
        <p>所有 E2E 集群测试用例均处于全绿稳定通过状态。</p>
      </div>
    `;
    return;
  }

  let html = `<div class="runs-list">`;
  for (const f of fails) {
    const jobItems = (f.failed_jobs || []).map(j => `
      <li style="margin-bottom: 6px;">
        <span class="cluster-tag">${escapeHtml(j.cluster || "unknown")}</span>
        <strong>${escapeHtml(j.name)}</strong>
        ${j.failed_step ? `<span style="color: var(--danger);">[步骤失败: ${escapeHtml(j.failed_step)}]</span>` : ""}
        ${j.url ? `<a href="${j.url}" target="_blank" class="btn" style="padding: 2px 6px; font-size: 0.72rem; margin-left: 8px;">查看失败 Step ↗</a>` : ""}
      </li>
    `).join("");

    html += `
      <div class="run-card" style="border-color: var(--danger-border);">
        <div class="run-header" style="cursor: default;">
          <div class="run-header-left">
            <div class="status-icon status-failure">✗</div>
            <div class="run-title-group">
              <h3>${escapeHtml(f.title || f.workflow_name)}</h3>
              <div class="run-meta">
                <span>📁 <code>${escapeHtml(f.workflow_name)}</code></span>
                <span>📅 日期: ${f.date}</span>
                <span>🕒 ${f.created_at}</span>
                <span>🌿 ${escapeHtml(f.branch || "main")}</span>
              </div>
            </div>
          </div>
          <div class="run-header-right">
            <a href="${f.url}" target="_blank" class="btn btn-primary" style="background: var(--danger); border-color: var(--danger-border);">进入 Actions 排查 ↗</a>
          </div>
        </div>
        ${jobItems ? `
          <div style="background: var(--bg-primary); padding: 14px 20px; border-top: 1px solid var(--border-color);">
            <div style="font-size: 0.82rem; color: var(--text-secondary); margin-bottom: 8px;"><strong>受影响的集群 / 失败 Jobs:</strong></div>
            <ul style="padding-left: 18px; font-size: 0.85rem;">${jobItems}</ul>
          </div>
        ` : ""}
      </div>
    `;
  }
  html += `</div>`;
  tabContentFailures.innerHTML = html;
}

// Utilities
function formatDuration(sec) {
  if (!sec || sec < 0) return "< 1s";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m === 0) return `${s}s`;
  return `${m}m ${s}s`;
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Event Listeners
function setupEventListeners() {
  // Tab switching
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentTab = btn.getAttribute("data-tab");

      // Hide all contents
      tabContentByDate.style.display = currentTab === "by-date" ? "block" : "none";
      tabContentByCase.style.display = currentTab === "by-case" ? "block" : "none";
      tabContentMatrix.style.display = currentTab === "matrix" ? "block" : "none";
      tabContentFailures.style.display = currentTab === "failures" ? "block" : "none";

      // Show/Hide date strip
      if (dateStripContainer) {
        dateStripContainer.style.display = currentTab === "by-date" ? "flex" : "none";
      }

      renderCurrentTab();
    });
  });

  // Search and filter
  if (searchInput) {
    searchInput.addEventListener("input", () => renderCurrentTab());
  }
  if (statusFilter) {
    statusFilter.addEventListener("change", () => renderCurrentTab());
  }

  // Refresh buttons
  if (elBtnManualRefresh) {
    elBtnManualRefresh.addEventListener("click", () => {
      countdown = autoRefreshSeconds;
      loadData(false);
    });
  }
  if (elBtnToggleRefresh) {
    elBtnToggleRefresh.addEventListener("click", toggleAutoRefresh);
  }
  if (elThemeToggle) {
    elThemeToggle.addEventListener("click", toggleTheme);
  }
}

// Run on page load
window.addEventListener("DOMContentLoaded", initApp);

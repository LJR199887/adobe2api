document.addEventListener("DOMContentLoaded", async () => {
  const rawFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const res = await rawFetch(...args);
    if (res.status === 401) {
      window.location.href = "/login";
    }
    return res;
  };

  async function ensureAuthenticated() {
    try {
      const res = await rawFetch("/api/v1/auth/me", { method: "GET" });
      if (!res.ok) {
        window.location.href = "/login";
        return false;
      }
      return true;
    } catch (err) {
      window.location.href = "/login";
      return false;
    }
  }

  if (!(await ensureAuthenticated())) {
    return;
  }

  // Tabs
  const tabBtns = document.querySelectorAll(".tab-btn");
  const tabPanes = document.querySelectorAll(".tab-pane");
  const LOGS_POLL_MS = 10000;

  function isLogsTabActive() {
    const logsPane = document.getElementById("logs");
    return Boolean(logsPane && logsPane.classList.contains("active"));
  }

  tabBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      tabBtns.forEach(b => b.classList.remove("active"));
      tabPanes.forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(btn.dataset.target).classList.add("active");
      if (btn.dataset.target === "logs") {
        logsCurrentPage = 1;
        loadLogs();
      } else if (logsAutoTimer) {
        clearTimeout(logsAutoTimer);
        logsAutoTimer = null;
      }
    });
  });

  // Token Management
  const tokenInput = document.getElementById("tokenInput");
  const tokenFile = document.getElementById("tokenFile");
  const addBtn = document.getElementById("addBtn");
  const addMsg = document.getElementById("addMsg");
  const openAddTokenModalBtn = document.getElementById("openAddTokenModalBtn");
  const tokenModal = document.getElementById("tokenModal");
  const tokenModalCloseBtn = document.getElementById("tokenModalCloseBtn");
  const openCookieImportBtn = document.getElementById("openCookieImportBtn");
  const exportTokensBtn = document.getElementById("exportTokensBtn");
  const exportCookiesBtn = document.getElementById("exportCookiesBtn");
  const deleteTokensBatchBtn = document.getElementById("deleteTokensBatchBtn");
  const enableAutoRefreshBatchBtn = document.getElementById("enableAutoRefreshBatchBtn");
  const disableAutoRefreshBatchBtn = document.getElementById("disableAutoRefreshBatchBtn");
  const refreshTokensBatchBtn = document.getElementById("refreshTokensBatchBtn");
  const checkInvalidTokensBatchBtn = document.getElementById("checkInvalidTokensBatchBtn");
  const refreshModal = document.getElementById("refreshModal");
  const refreshModalCloseBtn = document.getElementById("refreshModalCloseBtn");
  const refreshBtn = document.getElementById("refreshBtn");
  const refreshCreditsBatchBtn = document.getElementById("refreshCreditsBatchBtn");
  const tokenSelectAll = document.getElementById("tokenSelectAll");
  const tbody = document.querySelector("#tokenTable tbody");
  const tokenTotalCount = document.getElementById("tokenTotalCount");
  const tokenActiveCount = document.getElementById("tokenActiveCount");
  const tokenFilteredCount = document.getElementById("tokenFilteredCount");
  const tokenSelectedCount = document.getElementById("tokenSelectedCount");
  const tokenStatusFilter = document.getElementById("tokenStatusFilter");
  const tokenCreditsFilter = document.getElementById("tokenCreditsFilter");
  const clearTokenFiltersBtn = document.getElementById("clearTokenFiltersBtn");
  const selectAllFilteredTokensBtn = document.getElementById("selectAllFilteredTokensBtn");
  const clearTokenSelectionBtn = document.getElementById("clearTokenSelectionBtn");
  const tokenPagination = document.getElementById("tokenPagination");
  const tokenPrevBtn = document.getElementById("tokenPrevBtn");
  const tokenNextBtn = document.getElementById("tokenNextBtn");
  const tokenPageInfo = document.getElementById("tokenPageInfo");
  const tokenPageSizeSelect = document.getElementById("tokenPageSizeSelect");
  const tokenJumpInput = document.getElementById("tokenJumpInput");
  const tokenJumpBtn = document.getElementById("tokenJumpBtn");
  const tokenSelectedIds = new Set();
  let logsAutoTimer = null;
  let latestTokens = [];
  let latestTokenSummary = null;
  let latestTokenPagination = null;
  const TOKEN_PAGE_SIZE_OPTIONS = [20, 50, 100, 200, 500, 1000, 2000];
  const TOKEN_PAGE_SIZE_STORAGE_KEY = "adobe2api.tokenPageSize";
  function readTokenPageSize() {
    try {
      const stored = Number(localStorage.getItem(TOKEN_PAGE_SIZE_STORAGE_KEY) || 50);
      return TOKEN_PAGE_SIZE_OPTIONS.includes(stored) ? stored : 50;
    } catch (_) {
      return 50;
    }
  }
  let tokenPageSize = readTokenPageSize();
  let tokenCurrentPage = 1;
  let tokenTotalPages = 1;

  const STATUS_MAP = {
    "active": "生效中",
    "exhausted": "额度耗尽",
    "invalid": "已失效",
    "error": "请求异常",
    "disabled": "已禁用"
  };

  function getTokenFilters() {
    return {
      status: String(tokenStatusFilter?.value || "").trim().toLowerCase(),
      credits: String(tokenCreditsFilter?.value || "").trim().toLowerCase(),
    };
  }

  function resetTokenFilters() {
    if (tokenStatusFilter) tokenStatusFilter.value = "";
    if (tokenCreditsFilter) tokenCreditsFilter.value = "";
    tokenCurrentPage = 1;
    tokenSelectedIds.clear();
    loadTokens();
  }

  async function loadTokens() {
    try {
      const filters = getTokenFilters();
      const params = new URLSearchParams({
        page: String(tokenCurrentPage),
        page_size: String(tokenPageSize),
      });
      if (filters.status) params.set("status", filters.status);
      if (filters.credits) params.set("credits", filters.credits);
      const res = await fetch(`/api/v1/tokens?${params.toString()}`);
      const data = await res.json();
      const tokens = Array.isArray(data?.tokens)
        ? data.tokens
        : Array.isArray(data?.items)
          ? data.items
          : [];
      latestTokenSummary = data?.summary || null;
      latestTokenPagination = data?.pagination || null;
      if (latestTokenPagination) {
        tokenCurrentPage = Number(latestTokenPagination.page || tokenCurrentPage) || 1;
        tokenTotalPages = Math.max(1, Number(latestTokenPagination.total_pages || 1) || 1);
      }
      renderTable(tokens, latestTokenSummary, latestTokenPagination);
    } catch (err) {
      console.error(err);
      latestTokens = [];
      latestTokenSummary = null;
      latestTokenPagination = null;
      tokenSelectedIds.clear();
      renderTokenSummary([], null, null);
      renderTokenPagination(null);
      tbody.innerHTML = `<tr><td colspan="9" class="empty-state" style="color: #ffb4bc;">加载失败</td></tr>`;
    }
  }

  function getCurrentPageTokens(tokens = latestTokens) {
    return Array.isArray(tokens) ? tokens : [];
  }

  function renderTokenSummary(tokens, summary = null, pagination = null) {
    const list = Array.isArray(tokens) ? tokens : [];
    const fallbackTotal = list.length;
    const fallbackActive = list.filter((t) => String(t?.status || "").toLowerCase() === "active").length;
    const total = Number.isFinite(Number(summary?.total)) ? Number(summary.total) : fallbackTotal;
    const active = Number.isFinite(Number(summary?.active)) ? Number(summary.active) : fallbackActive;
    const filtered = Number.isFinite(Number(summary?.filtered))
      ? Number(summary.filtered)
      : Number.isFinite(Number(pagination?.total))
        ? Number(pagination.total)
        : fallbackTotal;
    if (tokenTotalCount) tokenTotalCount.textContent = String(total);
    if (tokenActiveCount) tokenActiveCount.textContent = String(active);
    if (tokenFilteredCount) tokenFilteredCount.textContent = String(filtered);
    updateTokenSelectionSummary();
  }

  function updateTokenSelectionSummary() {
    const selectedCount = tokenSelectedIds.size;
    if (tokenSelectedCount) tokenSelectedCount.textContent = String(selectedCount);
    if (clearTokenSelectionBtn) clearTokenSelectionBtn.disabled = selectedCount <= 0;
    if (enableAutoRefreshBatchBtn) enableAutoRefreshBatchBtn.disabled = selectedCount <= 0;
    if (disableAutoRefreshBatchBtn) disableAutoRefreshBatchBtn.disabled = selectedCount <= 0;
    if (refreshTokensBatchBtn) refreshTokensBatchBtn.disabled = selectedCount <= 0;
    if (checkInvalidTokensBatchBtn) checkInvalidTokensBatchBtn.disabled = selectedCount <= 0;
    if (selectAllFilteredTokensBtn) {
      const filteredCount = Array.isArray(latestTokens) ? latestTokens.length : 0;
      selectAllFilteredTokensBtn.disabled = filteredCount <= 0 || selectedCount >= filteredCount;
    }
  }

  function renderTokenPagination(pagination) {
    const total = Math.max(0, Number(pagination?.total || 0));
    const pageSize = Math.max(1, Number(pagination?.page_size || tokenPageSize || 50));
    tokenPageSize = pageSize;
    tokenTotalPages = Math.max(1, Number(pagination?.total_pages || 1));
    tokenCurrentPage = Math.min(
      Math.max(1, Number(pagination?.page || tokenCurrentPage) || 1),
      tokenTotalPages
    );

    if (tokenPageInfo) {
      tokenPageInfo.textContent = `第 ${tokenCurrentPage} / ${tokenTotalPages} 页`;
    }
    if (tokenPageSizeSelect) tokenPageSizeSelect.value = String(tokenPageSize);
    if (tokenJumpInput) {
      tokenJumpInput.max = String(tokenTotalPages);
      tokenJumpInput.value = String(tokenCurrentPage);
    }
    if (tokenPrevBtn) tokenPrevBtn.disabled = tokenCurrentPage <= 1;
    if (tokenNextBtn) tokenNextBtn.disabled = tokenCurrentPage >= tokenTotalPages;
    if (tokenJumpBtn) tokenJumpBtn.disabled = tokenTotalPages <= 1;
    if (tokenPagination) tokenPagination.style.display = total > pageSize ? "flex" : "none";
  }

  function syncTokenSelectAllState() {
    if (!tokenSelectAll) return;
    const tokenIds = getCurrentPageTokens().map((t) => String(t.id || "")).filter(Boolean);
    const selectedCount = tokenIds.filter((id) => tokenSelectedIds.has(id)).length;
    const total = tokenIds.length;
    if (total === 0) {
      tokenSelectAll.indeterminate = false;
      tokenSelectAll.checked = false;
      updateTokenSelectionSummary();
      return;
    }
    tokenSelectAll.indeterminate = selectedCount > 0 && selectedCount < total;
    tokenSelectAll.checked = total > 0 && selectedCount === total;
    updateTokenSelectionSummary();
  }

  function openDialog(modalEl) {
    if (!modalEl) return;
    modalEl.classList.add("open");
    modalEl.setAttribute("aria-hidden", "false");
  }

  function closeDialog(modalEl) {
    if (!modalEl) return;
    modalEl.classList.remove("open");
    modalEl.setAttribute("aria-hidden", "true");
  }

  function formatExpiry(token) {
    if (!token || token.expires_at == null) {
      return '<span style="color:#7f96ad;">未知</span>';
    }
    const remain = Number(token.remaining_seconds || 0);
    const abs = Math.abs(remain);
    const days = Math.floor(abs / 86400);
    const hours = Math.floor((abs % 86400) / 3600);
    const mins = Math.floor((abs % 3600) / 60);
    const rel = days > 0 ? `${days}天${hours}小时` : `${hours}小时${mins}分`;
    if (remain <= 0 || token.is_expired) {
      return `<span style="color:#ffb4bc;">已过期 (${token.expires_at_text || '-'})</span>`;
    }
    if (remain < 3600 * 6) {
      return `<span style="color:#ffca58;">剩余 ${rel}<br><span style="color:#7f96ad;">${token.expires_at_text || '-'}</span></span>`;
    }
    return `<span style="color:#a8bfd8;">剩余 ${rel}<br><span style="color:#7f96ad;">${token.expires_at_text || '-'}</span></span>`;
  }

  function formatCredits(token) {
    const available = Number(token?.credits_available);
    const total = Number(token?.credits_total);
    const availableUntil = String(token?.credits_available_until || "").trim();
    const err = String(token?.credits_error || "").trim();

    if (err) {
      return `<span style="color:#ffb4bc;">刷新失败</span><br><span style="color:#7f96ad;">${escapeHtml(err)}</span>`;
    }
    if (!Number.isFinite(available) || !Number.isFinite(total)) {
      return `<span style="color:#7f96ad;">未获取</span>`;
    }

    const resetText = availableUntil ? new Date(availableUntil).toLocaleString() : "-";
    return `<span style="color:#a8bfd8;">${available} / ${total}</span><br><span style="color:#7f96ad;">重置 ${resetText}</span>`;
  }

  function renderTable(tokens, summary = null, pagination = null) {
    latestTokens = Array.isArray(tokens) ? tokens : [];
    latestTokenSummary = summary;
    latestTokenPagination = pagination;
    renderTokenSummary(latestTokens, summary, pagination);
    const availableIds = new Set(latestTokens.map((t) => String(t.id || "")).filter(Boolean));
    Array.from(tokenSelectedIds).forEach((id) => {
      if (!availableIds.has(id)) tokenSelectedIds.delete(id);
    });

    renderTokenPagination(pagination);
    const pageTokens = getCurrentPageTokens();

    if (!latestTokens.length) {
      const total = Number(summary?.total || 0);
      const filtered = Number(summary?.filtered || pagination?.total || 0);
      const emptyText = total > 0 && filtered === 0
        ? "当前筛选条件下没有 Token。"
        : "当前没有可用的 Token，请在上方添加。";
      tbody.innerHTML = `<tr><td colspan="9" class="empty-state">${emptyText}</td></tr>`;
      syncTokenSelectAllState();
      return;
    }

    tbody.innerHTML = "";
    pageTokens.forEach(t => {
      const tr = document.createElement("tr");
      const tokenId = String(t.id || "").trim();
      const selectedAttr = tokenSelectedIds.has(tokenId) ? "checked" : "";

      const statusClass = `status-${t.status.toLowerCase()}`;
      const isStatusActive = t.status === "active";
      const isFrozen = t.status === "exhausted" || t.status === "invalid";
      const displayStatus = STATUS_MAP[t.status.toLowerCase()] || t.status;
      const tokenProfileName = String(t.refresh_profile_name || "").trim();
      const tokenProfileEmail = String(t.refresh_profile_email || "").trim();
      const refreshProfileNameSafe = escapeHtml(tokenProfileName);
      const refreshProfileEmailSafe = escapeHtml(tokenProfileEmail);
      const accountName = refreshProfileNameSafe || '<span style="color:#7f96ad;">手动 Token</span>';
      const accountEmail = refreshProfileEmailSafe || '<span style="color:#7f96ad;">-</span>';
      const autoEnabled = t.auto_refresh && t.auto_refresh_enabled !== false;
      const autoRefreshCell = t.auto_refresh
        ? `<div style="display: flex; align-items: center;"><button class="switch-btn ${autoEnabled ? "on" : "off"}" onclick="toggleAutoRefresh('${t.id}', ${autoEnabled ? "false" : "true"})" title="${autoEnabled ? "点击关闭自动刷新" : "点击开启自动刷新"}"><span class="switch-knob"></span></button><span class="switch-text">${autoEnabled ? "开启" : "关闭"}</span></div>`
        : `<div style="display: flex; align-items: center;"><button class="switch-btn off" disabled title="手动 token 不支持自动刷新"><span class="switch-knob"></span></button><span class="switch-text" style="color:#7f96ad;">手动</span></div>`;
      
      const d = new Date(t.added_at * 1000);
      const dateStr = d.toLocaleString();

      const refreshTokenBtn = t.auto_refresh
        ? `<button class="action-mini" onclick="refreshToken('${t.id}')">刷新Token</button>`
        : `<button class="action-mini" disabled title="仅自动刷新 token 支持刷新">刷新Token</button>`;
      const statusBtn = isFrozen
        ? `<button class="action-mini" disabled title="额度耗尽或已失效 token 不可启用">不可启用</button>`
        : `<button class="action-mini" onclick="toggleToken('${t.id}', '${isStatusActive ? 'disabled' : 'active'}')">${isStatusActive ? '禁用Token' : '启用Token'}</button>`;
      const actionsGrid = `
        <div class="action-btns">
          <button class="action-mini" onclick="refreshTokenCredits('${t.id}')">刷新积分</button>
          ${refreshTokenBtn}
          ${statusBtn}
          <button class="action-mini danger" onclick="deleteToken('${t.id}')">删除Token</button>
        </div>
      `;

      tr.innerHTML = `
        <td><input type="checkbox" class="token-select" data-id="${tokenId}" ${selectedAttr} /></td>
        <td style="color: #a8bfd8; font-size: 12px;" title="添加时间: ${dateStr}">${accountName}<br>${accountEmail}</td>
        <td class="token-val">${t.value}</td>
        <td><span class="status-badge ${statusClass}">${displayStatus}</span></td>
        <td>${autoRefreshCell}</td>
        <td style="font-size:12px; line-height:1.35;">${formatCredits(t)}</td>
        <td style="color: ${t.fails > 0 ? '#ffb4bc' : '#a8bfd8'};">${t.fails}</td>
        <td style="font-size:12px; line-height:1.35;">${formatExpiry(t)}</td>
        <td>${actionsGrid}</td>
      `;
      tbody.appendChild(tr);
    });
    syncTokenSelectAllState();
  }

  addBtn.addEventListener("click", async () => {
    let tokens = [];
    try {
      tokens = await collectTokensFromInputs();
    } catch (err) {
      showMsg(addMsg, err.message || "文件解析失败", true);
      return;
    }

    if (!tokens.length) {
      showMsg(addMsg, "请先输入 Token 内容或上传文件", true);
      return;
    }

    addBtn.disabled = true;
    try {
      const endpoint = tokens.length > 1 ? "/api/v1/tokens/batch" : "/api/v1/tokens";
      const payload = tokens.length > 1 ? { tokens } : { token: tokens[0] };
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      let data = null;
      try {
        data = await res.json();
      } catch (_) {
        data = null;
      }

      if (res.ok) {
        tokenInput.value = "";
        if (tokenFile) tokenFile.value = "";
        showMsg(
          addMsg,
          buildImportSummaryText("Token导入", data),
          getImportFailedCount(data) > 0,
          { duration: 8000 }
        );
        loadTokens();
        closeDialog(tokenModal);
      } else {
        let detail = "导入失败，请重试";
        const detailPayload = getImportDetailPayload(data);
        if (detailPayload && typeof detailPayload === "object") {
          detail = buildImportSummaryText("Token导入", detailPayload);
        } else if (typeof detailPayload === "string" && detailPayload.trim()) {
          detail = detailPayload;
        }
        showMsg(addMsg, detail, true);
      }
    } catch (err) {
      showMsg(addMsg, err.message || "导入失败", true);
    }
    addBtn.disabled = false;
  });

  refreshBtn.addEventListener("click", async () => {
    showToast("Token 列表刷新中...", false, { duration: 0 });
    try {
      await loadTokens();
      showToast("Token 列表已刷新", false);
    } catch (err) {
      showToast("Token 列表刷新失败", true);
    }
  });

  [tokenStatusFilter, tokenCreditsFilter].forEach((filterEl) => {
    if (!filterEl) return;
    filterEl.addEventListener("change", () => {
      tokenCurrentPage = 1;
      tokenSelectedIds.clear();
      loadTokens();
    });
  });

  if (clearTokenFiltersBtn) {
    clearTokenFiltersBtn.addEventListener("click", resetTokenFilters);
  }

  if (selectAllFilteredTokensBtn) {
    selectAllFilteredTokensBtn.addEventListener("click", () => {
      latestTokens.forEach((token) => {
        const tid = String(token?.id || "").trim();
        if (tid) tokenSelectedIds.add(tid);
      });
      renderTable(latestTokens, latestTokenSummary, latestTokenPagination);
    });
  }

  if (clearTokenSelectionBtn) {
    clearTokenSelectionBtn.addEventListener("click", () => {
      tokenSelectedIds.clear();
      renderTable(latestTokens, latestTokenSummary, latestTokenPagination);
    });
  }

  if (tokenSelectAll) {
    tokenSelectAll.addEventListener("change", () => {
      const checked = Boolean(tokenSelectAll.checked);
      const pageTokens = getCurrentPageTokens();
      if (checked) {
        pageTokens.forEach((t) => {
          const tid = String(t.id || "").trim();
          if (tid) tokenSelectedIds.add(tid);
        });
      } else {
        pageTokens.forEach((t) => {
          const tid = String(t.id || "").trim();
          if (tid) tokenSelectedIds.delete(tid);
        });
      }
      tbody.querySelectorAll("input.token-select").forEach((el) => {
        el.checked = checked;
      });
      syncTokenSelectAllState();
    });
  }

  if (tbody) {
    tbody.addEventListener("change", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLInputElement)) return;
      if (!target.classList.contains("token-select")) return;
      const tid = String(target.dataset.id || "").trim();
      if (!tid) return;
      if (target.checked) tokenSelectedIds.add(tid);
      else tokenSelectedIds.delete(tid);
      syncTokenSelectAllState();
    });
  }

  if (openAddTokenModalBtn) {
    openAddTokenModalBtn.addEventListener("click", () => openDialog(tokenModal));
  }
  if (tokenModalCloseBtn) {
    tokenModalCloseBtn.addEventListener("click", () => closeDialog(tokenModal));
  }
  if (tokenModal) {
    tokenModal.addEventListener("click", (event) => {
      if (event.target === tokenModal) closeDialog(tokenModal);
    });
  }

  if (openCookieImportBtn) {
    openCookieImportBtn.addEventListener("click", async () => {
      openDialog(refreshModal);
      if (cookieInput) cookieInput.focus();
    });
  }
  if (refreshModalCloseBtn) {
    refreshModalCloseBtn.addEventListener("click", () => closeDialog(refreshModal));
  }
  if (refreshModal) {
    refreshModal.addEventListener("click", (event) => {
      if (event.target === refreshModal) closeDialog(refreshModal);
    });
  }

  window.deleteToken = async (id) => {
    if (!confirm("确定要删除这个 Token 吗？")) return;
    try {
      const res = await fetch(`/api/v1/tokens/${id}`, { method: "DELETE" });
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(txt || "删除失败");
      }
      await loadTokens();
    } catch (err) {
      alert(err.message || "删除失败");
    }
  };

  window.toggleToken = async (id, newStatus) => {
    try {
      const res = await fetch(`/api/v1/tokens/${id}/status?status=${newStatus}`, { method: "PUT" });
      if (!res.ok) {
        const text = await res.text();
        alert(`状态更新失败: ${text}`);
        return;
      }
      loadTokens();
    } catch (err) {
      alert("状态更新失败");
    }
  };

  window.refreshToken = async (id) => {
    showToast("Token 刷新中...", false, { duration: 0 });
    try {
      const res = await fetch(`/api/v1/tokens/${id}/refresh`, { method: "POST" });
      if (!res.ok) {
        let detail = "刷新失败";
        try {
          const body = await res.json();
          detail = body.detail || JSON.stringify(body);
        } catch (_) {
          detail = await res.text();
        }
        alert(`刷新失败: ${detail || "unknown error"}`);
        showToast(`Token 刷新失败：${detail || "unknown error"}`, true);
        return;
      }
      showMsg(refreshMsg, "刷新成功", false);
      showToast("Token 刷新成功", false);
      await loadTokens();
    } catch (err) {
      alert("刷新失败");
      showToast("Token 刷新失败", true);
    }
  };

  window.refreshTokenCredits = async (id) => {
    showToast("Token 积分刷新中...", false, { duration: 0 });
    try {
      const res = await fetch(`/api/v1/tokens/${id}/credits/refresh`, { method: "POST" });
      if (!res.ok) {
        let detail = "刷新积分失败";
        try {
          const body = await res.json();
          detail = body.detail || JSON.stringify(body);
        } catch (_) {
          detail = await res.text();
        }
        alert(detail || "刷新积分失败");
        showToast(`刷新积分失败：${detail || "unknown error"}`, true);
        return;
      }
      await loadTokens();
      showToast("Token 积分刷新成功", false);
    } catch (err) {
      alert("刷新积分失败");
      showToast("Token 积分刷新失败", true);
    }
  };

  window.toggleAutoRefresh = async (id, enabled) => {
    try {
      const res = await fetch(`/api/v1/tokens/${id}/auto-refresh?enabled=${enabled ? "true" : "false"}`, {
        method: "PUT"
      });
      if (!res.ok) {
        let detail = "自动刷新设置失败";
        try {
          const body = await res.json();
          detail = body.detail || JSON.stringify(body);
        } catch (_) {
          detail = await res.text();
        }
        alert(detail || "自动刷新设置失败");
        return;
      }
      await loadTokens();
    } catch (err) {
      alert("自动刷新设置失败");
    }
  };

  async function setSelectedAutoRefresh(enabled) {
    const selectedIds = Array.from(tokenSelectedIds);
    if (!selectedIds.length) {
      alert("请先选择要操作的 Token");
      return;
    }
    const actionText = enabled ? "开启" : "关闭";
    const targetBtn = enabled ? enableAutoRefreshBatchBtn : disableAutoRefreshBatchBtn;
    if (enableAutoRefreshBatchBtn) enableAutoRefreshBatchBtn.disabled = true;
    if (disableAutoRefreshBatchBtn) disableAutoRefreshBatchBtn.disabled = true;
    showToast(`批量${actionText}自动刷新中...`, false, { duration: 0 });
    try {
      const res = await fetch("/api/v1/tokens/auto-refresh-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: selectedIds, enabled }),
      });
      if (!res.ok) {
        let detail = `批量${actionText}自动刷新失败`;
        try {
          const body = await res.json();
          detail = body.detail || JSON.stringify(body);
        } catch (_) {
          detail = await res.text();
        }
        showToast(`批量${actionText}自动刷新失败：${detail || "unknown error"}`, true);
        return;
      }
      const data = await res.json();
      const ok = Number(data.updated_count || 0);
      const skipped = Number(data.skipped_count || 0);
      const missing = Number(data.missing_count || 0);
      const failed = Number(data.failed_count || 0);
      showToast(
        `${actionText}自动刷新完成：成功 ${ok}，跳过 ${skipped}，缺失 ${missing}，失败 ${failed}`,
        failed > 0
      );
      await loadTokens();
    } catch (err) {
      showToast(`批量${actionText}自动刷新失败`, true);
    } finally {
      if (enableAutoRefreshBatchBtn) enableAutoRefreshBatchBtn.disabled = false;
      if (disableAutoRefreshBatchBtn) disableAutoRefreshBatchBtn.disabled = false;
      if (targetBtn) targetBtn.disabled = false;
      updateTokenSelectionSummary();
    }
  }

  if (enableAutoRefreshBatchBtn) {
    enableAutoRefreshBatchBtn.addEventListener("click", () => {
      setSelectedAutoRefresh(true);
    });
  }

  if (disableAutoRefreshBatchBtn) {
    disableAutoRefreshBatchBtn.addEventListener("click", () => {
      setSelectedAutoRefresh(false);
    });
  }

  if (refreshTokensBatchBtn) {
    refreshTokensBatchBtn.addEventListener("click", async () => {
      const selectedIds = Array.from(tokenSelectedIds);
      if (!selectedIds.length) {
        alert("请先选择要刷新 Token 的账号");
        return;
      }

      refreshTokensBatchBtn.disabled = true;
      showToast(`批量刷新 Token 中...`, false, { duration: 0 });
      try {
        const res = await fetch("/api/v1/tokens/refresh-batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ids: selectedIds }),
        });
        if (!res.ok) {
          let detail = "批量刷新 Token 失败";
          try {
            const body = await res.json();
            detail = body.detail || JSON.stringify(body);
          } catch (_) {
            detail = await res.text();
          }
          showToast(`批量刷新 Token 失败：${detail || "unknown error"}`, true);
          return;
        }
        const data = await res.json();
        const ok = Number(data.refreshed_count || 0);
        const skipped = Number(data.skipped_count || 0);
        const fail = Number(data.failed_count || 0);
        showToast(`批量刷新 Token 完成：成功 ${ok}，跳过 ${skipped}，失败 ${fail}`, fail > 0);
        await loadTokens();
      } catch (err) {
        showToast("批量刷新 Token 失败", true);
      } finally {
        refreshTokensBatchBtn.disabled = false;
      }
    });
  }

  if (checkInvalidTokensBatchBtn) {
    checkInvalidTokensBatchBtn.addEventListener("click", async () => {
      const selectedIds = Array.from(tokenSelectedIds);
      if (!selectedIds.length) {
        alert("请先选择要检测的 Token");
        return;
      }
      const ok = confirm(
        `将主动检测选中的 ${selectedIds.length} 个生效 Token。只有返回 Token invalid or expired 时，才会标记为已失效并关闭自动刷新。确定继续吗？`
      );
      if (!ok) return;

      checkInvalidTokensBatchBtn.disabled = true;
      showToast("正在检测失效 Token...", false, { duration: 0 });
      try {
        const res = await fetch("/api/v1/tokens/check-invalid-batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ids: selectedIds }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(data?.detail || "检测失效 Token 失败");
        }
        const invalid = Number(data.invalid_count || 0);
        const changed = Number(data.changed_count || 0);
        const valid = Number(data.valid_count || 0);
        const skipped = Number(data.skipped_count || 0);
        const failed = Number(data.failed_count || 0);
        const disabled = Number(data.disabled_auto_refresh_count || 0);
        showToast(
          `检测完成：已失效 ${invalid}，新标记 ${changed}，正常 ${valid}，禁用自动刷新 ${disabled}，跳过 ${skipped}，失败 ${failed}`,
          failed > 0,
          { duration: 8000 }
        );
        await loadTokens();
      } catch (err) {
        showToast(err.message || "检测失效 Token 失败", true, { duration: 8000 });
      } finally {
        checkInvalidTokensBatchBtn.disabled = false;
        updateTokenSelectionSummary();
      }
    });
  }

  if (refreshCreditsBatchBtn) {
    refreshCreditsBatchBtn.addEventListener("click", async () => {
      refreshCreditsBatchBtn.disabled = true;
      showToast("批量刷新积分中...", false, { duration: 0 });
      try {
        const res = await fetch("/api/v1/tokens/credits/refresh-batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        if (!res.ok) {
          let detail = "批量刷新积分失败";
          try {
            const body = await res.json();
            detail = body.detail || JSON.stringify(body);
          } catch (_) {
            detail = await res.text();
          }
          showToast(`批量刷新积分失败：${detail || "unknown error"}`, true);
          return;
        }
        const data = await res.json();
        const ok = Number(data.refreshed_count || 0);
        const fail = Number(data.failed_count || 0);
        showToast(`批量刷新完成：成功 ${ok}，失败 ${fail}`, false);
        await loadTokens();
      } catch (err) {
        showToast("批量刷新积分失败", true);
      } finally {
        refreshCreditsBatchBtn.disabled = false;
      }
    });
  }

  if (deleteTokensBatchBtn) {
    deleteTokensBatchBtn.addEventListener("click", async () => {
      const selectedIds = Array.from(tokenSelectedIds);
      if (!selectedIds.length) {
        alert("请先选择要删除的 Token");
        return;
      }
      if (!confirm(`确定批量删除选中的 ${selectedIds.length} 个 Token 吗？`)) return;

      deleteTokensBatchBtn.disabled = true;
      try {
        const res = await fetch("/api/v1/tokens/delete-batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ids: selectedIds }),
        });
        if (!res.ok) {
          let detail = "批量删除失败";
          try {
            const body = await res.json();
            detail = body.detail || JSON.stringify(body);
          } catch (_) {
            detail = await res.text();
          }
          throw new Error(detail || "批量删除失败");
        }

        const data = await res.json();
        const deletedIds = Array.isArray(data.deleted_ids) ? data.deleted_ids : [];
        deletedIds.forEach((id) => tokenSelectedIds.delete(String(id || "")));
        await loadTokens();

        const deletedCount = Number(data.deleted_count || 0);
        const missingCount = Number(data.missing_count || 0);
        showToast(
          missingCount > 0
            ? `批量删除完成：成功 ${deletedCount}，未找到 ${missingCount}`
            : `批量删除完成：成功删除 ${deletedCount} 个 Token`,
          false,
          { duration: 5000 }
        );
      } catch (err) {
        alert(err.message || "批量删除失败");
        showToast(err.message || "批量删除失败", true);
      } finally {
        deleteTokensBatchBtn.disabled = false;
      }
    });
  }

  if (exportTokensBtn) {
    exportTokensBtn.addEventListener("click", async () => {
      exportTokensBtn.disabled = true;
      try {
        const selectedIds = Array.from(tokenSelectedIds);
        const payload = selectedIds.length ? { ids: selectedIds } : { ids: null };
        const res = await fetch("/api/v1/tokens/export", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!res.ok) {
          const txt = await res.text();
          throw new Error(txt || "导出 Token 失败");
        }
        const data = await res.json();
        const total = Number(data.total || 0);
        if (total <= 0) {
          alert("没有可导出的 Token");
          return;
        }
        downloadJsonFile(`tokens-export-${nowStamp()}.json`, data);
        alert(`导出成功：${total} 个 Token`);
      } catch (err) {
        alert(err.message || "导出 Token 失败");
      } finally {
        exportTokensBtn.disabled = false;
      }
    });
  }

  if (exportCookiesBtn) {
    exportCookiesBtn.addEventListener("click", async () => {
      exportCookiesBtn.disabled = true;
      try {
        const selectedIds = Array.from(tokenSelectedIds);
        const payload = selectedIds.length ? { ids: selectedIds } : { ids: null };
        const res = await fetch("/api/v1/refresh-profiles/export-cookies", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!res.ok) {
          const txt = await res.text();
          throw new Error(txt || "导出 Cookie 失败");
        }
        const data = await res.json();
        const total = Number(data.total || 0);
        if (total <= 0) {
          alert("没有可导出的 Cookie");
          return;
        }
        const output = {
          exported_at: Math.floor(Date.now() / 1000),
          total,
          items: Array.isArray(data.items)
            ? data.items.map((it) => ({
                id: it.id,
                name: it.name,
                cookie: it.cookie,
              }))
            : [],
        };
        downloadJsonFile(`refresh-cookies-export-${nowStamp()}.json`, output);
        alert(`导出成功：${total} 个 Cookie`);
      } catch (err) {
        alert(err.message || "导出 Cookie 失败");
      } finally {
        exportCookiesBtn.disabled = false;
      }
    });
  }

  // Config Management
  const confApiKey = document.getElementById("confApiKey");
  const confAdminUsername = document.getElementById("confAdminUsername");
  const confAdminPassword = document.getElementById("confAdminPassword");
  const confPublicBaseUrl = document.getElementById("confPublicBaseUrl");
  const confUseProxy = document.getElementById("confUseProxy");
  const confProxy = document.getElementById("confProxy");
  const confResourceUseProxy = document.getElementById("confResourceUseProxy");
  const confResourceProxy = document.getElementById("confResourceProxy");
  const testProxyBtn = document.getElementById("testProxyBtn");
  const proxyTestResult = document.getElementById("proxyTestResult");
  const confGenerateTimeout = document.getElementById("confGenerateTimeout");
  const confRetryEnabled = document.getElementById("confRetryEnabled");
  const confRetryMaxAttempts = document.getElementById("confRetryMaxAttempts");
  const confRetryBackoffSeconds = document.getElementById("confRetryBackoffSeconds");
  const confRetryOnStatusCodes = document.getElementById("confRetryOnStatusCodes");
  const confRetryOnErrorTypes = document.getElementById("confRetryOnErrorTypes");
  const confTokenRotationStrategy = document.getElementById("confTokenRotationStrategy");
  const confRefreshIntervalHours = document.getElementById("confRefreshIntervalHours");
  const confBatchConcurrency = document.getElementById("confBatchConcurrency");
  const confGeneratedMaxSizeMb = document.getElementById("confGeneratedMaxSizeMb");
  const confGeneratedPruneSizeMb = document.getElementById("confGeneratedPruneSizeMb");
  const confUseUpstreamResultUrl = document.getElementById("confUseUpstreamResultUrl");
  const confImgBedEnabled = document.getElementById("confImgBedEnabled");
  const confImgBedApiUrl = document.getElementById("confImgBedApiUrl");
  const confImgBedApiKey = document.getElementById("confImgBedApiKey");
  const generatedUsageInfo = document.getElementById("generatedUsageInfo");
  const configCatBtns = document.querySelectorAll(".config-cat-btn");
  const configCatPanes = document.querySelectorAll(".config-cat-pane");
  const saveConfigBtn = document.getElementById("saveConfigBtn");
  const configMsg = document.getElementById("configMsg");
  const cookieInput = document.getElementById("cookieInput");
  const cookieFile = document.getElementById("cookieFile");
  const importCookieBtn = document.getElementById("importCookieBtn");
  const refreshMsg = document.getElementById("refreshMsg");
  let currentBatchConcurrency = 5;
  // Logs
  const logsTbody = document.querySelector("#logsTable tbody");
  const refreshLogsBtn = document.getElementById("refreshLogsBtn");
  const backfillInvalidTokenLogsBtn = document.getElementById("backfillInvalidTokenLogsBtn");
  const clearLogsBtn = document.getElementById("clearLogsBtn");
  const logStatsRange = document.getElementById("logStatsRange");
  const logStatsUpdatedAt = document.getElementById("logStatsUpdatedAt");
  const logsStatsImageCount = document.getElementById("logsStatsImageCount");
  const logsStatsVideoCount = document.getElementById("logsStatsVideoCount");
  const logsStatsTotalCount = document.getElementById("logsStatsTotalCount");
  const logsStatsFailCount = document.getElementById("logsStatsFailCount");
  const logsPrevBtn = document.getElementById("logsPrevBtn");
  const logsNextBtn = document.getElementById("logsNextBtn");
  const logsPageInfo = document.getElementById("logsPageInfo");
  const logsFailedOnly = document.getElementById("logsFailedOnly");
  const logsFailedAccount = document.getElementById("logsFailedAccount");
  const clearLogFiltersBtn = document.getElementById("clearLogFiltersBtn");
  const previewModal = document.getElementById("previewModal");
  const previewContent = document.getElementById("previewContent");
  const previewCloseBtn = document.getElementById("previewCloseBtn");
  const previewDownloadBtn = document.getElementById("previewDownloadBtn");
  const errorDetailModal = document.getElementById("errorDetailModal");
  const errorDetailCode = document.getElementById("errorDetailCode");
  const errorDetailContent = document.getElementById("errorDetailContent");
  const errorDetailCloseBtn = document.getElementById("errorDetailCloseBtn");
  const promptDetailModal = document.getElementById("promptDetailModal");
  const promptDetailContent = document.getElementById("promptDetailContent");
  const promptDetailCloseBtn = document.getElementById("promptDetailCloseBtn");
  const appToast = document.getElementById("appToast");
  const LOGS_PAGE_SIZE = 20;
  let logsCurrentPage = 1;
  let logsTotalPages = 1;
  let logsRunningTotal = 0;

  function getSelectedLogAccount() {
    return String(logsFailedAccount?.value || "").trim();
  }

  function isFailedOnlyFilterEnabled() {
    return Boolean(logsFailedOnly?.checked);
  }

  function getLogsQueryParams() {
    const params = new URLSearchParams();
    params.set("limit", String(LOGS_PAGE_SIZE));
    params.set("page", String(logsCurrentPage));
    if (isFailedOnlyFilterEnabled()) {
      params.set("failed_only", "true");
    }
    const account = getSelectedLogAccount();
    if (account) {
      params.set("account", account);
    }
    return params;
  }

  function matchesLogAccount(item, account) {
    const target = String(account || "").trim().toLowerCase();
    if (!target) return true;
    const values = [
      item?.token_account_email,
      item?.token_account_name,
      item?.token_id,
    ];
    return values.some((value) => String(value || "").trim().toLowerCase() === target);
  }

  function buildFailedAccountOptionLabel(item) {
    const email = String(item?.token_account_email || "").trim();
    const name = String(item?.token_account_name || "").trim();
    const tokenId = String(item?.token_id || "").trim();
    const failedCount = Number(item?.failed_count || 0);
    const primary = email || name || tokenId || "Unknown account";
    const extra = [];
    if (name && name !== primary) extra.push(name);
    if (email && email !== primary) extra.push(email);
    if (tokenId && tokenId !== primary) extra.push(`ID ${tokenId}`);
    const suffix = failedCount > 0 ? ` (${failedCount})` : "";
    return `${primary}${extra.length ? ` - ${extra.join(" | ")}` : ""}${suffix}`;
  }

  async function loadFailedAccounts() {
    if (!logsFailedAccount) return;
    const previousValue = getSelectedLogAccount();
    try {
      const res = await fetch("/api/v1/logs/failed-accounts?limit=200");
      if (!res.ok) {
        throw new Error("failed to load failed accounts");
      }
      const data = await res.json();
      const items = Array.isArray(data?.items) ? data.items : [];
      logsFailedAccount.innerHTML = '<option value="">全部账号</option>';
      items.forEach((item) => {
        const accountKey = String(item?.account_key || "").trim();
        if (!accountKey) return;
        const option = document.createElement("option");
        option.value = accountKey;
        option.textContent = buildFailedAccountOptionLabel(item);
        logsFailedAccount.appendChild(option);
      });
      if (previousValue) {
        const hasOption = Array.from(logsFailedAccount.options).some(
          (option) => String(option.value || "").trim() === previousValue
        );
        logsFailedAccount.value = hasOption ? previousValue : "";
      }
    } catch (_) {
      logsFailedAccount.innerHTML = '<option value="">全部账号</option>';
    }
  }

  if (testProxyBtn) {
    testProxyBtn.textContent = "检测代理与业务权限";
    const proxyHelp = testProxyBtn.nextElementSibling;
    if (proxyHelp && proxyHelp.classList.contains("help")) {
      proxyHelp.textContent = "会先检测基础代理和资源代理的网络连通性，再用当前有效 token 检测基础代理是否真的能访问积分接口。检测时会直接使用你当前表单里的值，不需要先保存配置。";
    }
  }
  if (proxyTestResult && !String(proxyTestResult.textContent || "").trim()) {
    proxyTestResult.textContent = "点击上方按钮后，会在这里显示连通性检测和业务权限检测结果。";
  }

  function switchConfigPane(targetId) {
    if (!targetId) return;
    configCatBtns.forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.target === targetId);
    });
    configCatPanes.forEach((pane) => {
      pane.classList.toggle("active", pane.id === targetId);
    });
  }

  configCatBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      switchConfigPane(String(btn.dataset.target || ""));
    });
  });

  if (configCatBtns.length > 0) {
    const currentActive = Array.from(configCatBtns).find((btn) =>
      btn.classList.contains("active")
    );
    switchConfigPane(
      String(currentActive?.dataset?.target || configCatBtns[0]?.dataset?.target || "")
    );
  }

  async function loadConfig() {
    try {
      const res = await fetch("/api/v1/config");
      if (res.ok) {
        const data = await res.json();
        confApiKey.value = data.api_key || "";
        confAdminUsername.value = data.admin_username || "admin";
        confAdminPassword.value = data.admin_password || "admin";
        confPublicBaseUrl.value = data.public_base_url || "";
        confUseProxy.checked = data.use_proxy || false;
        confProxy.value = data.proxy || "";
        confResourceUseProxy.checked = data.resource_use_proxy || false;
        confResourceProxy.value = data.resource_proxy || "";
        confGenerateTimeout.value = Number(data.generate_timeout || 300);
        confRetryEnabled.checked = Boolean(data.retry_enabled ?? true);
        confRetryMaxAttempts.value = Number(data.retry_max_attempts || 3);
        confRetryBackoffSeconds.value = Number(data.retry_backoff_seconds ?? 1.0);
        confRetryOnStatusCodes.value = Array.isArray(data.retry_on_status_codes)
          ? data.retry_on_status_codes.join(",")
          : "429,451,500,502,503,504";
        confRetryOnErrorTypes.value = Array.isArray(data.retry_on_error_types)
          ? data.retry_on_error_types.join(",")
          : "timeout,connection,proxy";
        confTokenRotationStrategy.value = String(data.token_rotation_strategy || "round_robin");
        confRefreshIntervalHours.value = Number(data.refresh_interval_hours || 15);
        currentBatchConcurrency = Math.max(1, Math.min(100, Number(data.batch_concurrency || 5)));
        confBatchConcurrency.value = currentBatchConcurrency;
        confGeneratedMaxSizeMb.value = Number(data.generated_max_size_mb || 1024);
        confGeneratedPruneSizeMb.value = Number(data.generated_prune_size_mb || 200);
        confUseUpstreamResultUrl.checked = Boolean(data.use_upstream_result_url || false);
        confImgBedEnabled.checked = Boolean(data.imgbed_enabled || false);
        confImgBedApiUrl.value = data.imgbed_api_url || "";
        confImgBedApiKey.value = data.imgbed_api_key || "";
        if (generatedUsageInfo) {
          const usageMb = Number(data.generated_usage_mb || 0);
          const fileCount = Number(data.generated_file_count || 0);
          generatedUsageInfo.textContent = `当前占用：${Number.isFinite(usageMb) ? usageMb : 0} MB（${Number.isFinite(fileCount) ? fileCount : 0} 个文件）`;
        }
      }
    } catch (err) {
      console.error("加载配置失败", err);
    }
  }

  saveConfigBtn.addEventListener("click", async () => {
    saveConfigBtn.disabled = true;
    try {
      // 保留未在此页面显示的配置项
      const currentRes = await fetch("/api/v1/config");
      const currentData = await currentRes.json();
      
      const payload = {
        ...currentData,
        api_key: confApiKey.value.trim(),
        admin_username: confAdminUsername.value.trim() || "admin",
        admin_password: confAdminPassword.value || "admin",
        public_base_url: confPublicBaseUrl.value.trim(),
        use_proxy: confUseProxy.checked,
        proxy: confProxy.value.trim(),
        resource_use_proxy: confResourceUseProxy.checked,
        resource_proxy: confResourceProxy.value.trim(),
        generate_timeout: Math.max(1, Number(confGenerateTimeout.value || 300)),
        retry_enabled: confRetryEnabled.checked,
        retry_max_attempts: Math.max(1, Math.min(10, Number(confRetryMaxAttempts.value || 3))),
        retry_backoff_seconds: Math.max(0, Math.min(30, Number(confRetryBackoffSeconds.value || 1))),
        retry_on_status_codes: String(confRetryOnStatusCodes.value || "")
          .split(",")
          .map(s => Number(String(s).trim()))
          .filter(n => Number.isInteger(n) && n >= 100 && n <= 599),
        retry_on_error_types: String(confRetryOnErrorTypes.value || "")
          .split(",")
          .map(s => String(s).trim().toLowerCase())
          .filter(Boolean),
        token_rotation_strategy: String(confTokenRotationStrategy.value || "round_robin").trim() || "round_robin",
        refresh_interval_hours: Number(confRefreshIntervalHours.value || 15),
        batch_concurrency: Math.max(1, Math.min(100, Number(confBatchConcurrency.value || 5))),
        generated_max_size_mb: Math.max(100, Math.min(102400, Number(confGeneratedMaxSizeMb.value || 1024))),
        generated_prune_size_mb: Math.max(10, Math.min(10240, Number(confGeneratedPruneSizeMb.value || 200))),
        use_upstream_result_url: confUseUpstreamResultUrl.checked,
        imgbed_enabled: confImgBedEnabled.checked,
        imgbed_api_url: confImgBedApiUrl.value.trim(),
        imgbed_api_key: confImgBedApiKey.value.trim(),
      };

      if (!payload.admin_username) {
        throw new Error("管理员账号不能为空");
      }
      if (!payload.admin_password) {
        throw new Error("管理员密码不能为空");
      }

      if (!Number.isInteger(payload.refresh_interval_hours) || payload.refresh_interval_hours < 1 || payload.refresh_interval_hours > 24) {
        throw new Error("自动刷新间隔必须是 1-24 的整数小时");
      }
      if (!Number.isInteger(payload.batch_concurrency) || payload.batch_concurrency < 1 || payload.batch_concurrency > 100) {
        throw new Error("批量导入/积分并发数必须是 1-100 的整数");
      }
      if (!Number.isInteger(payload.generated_max_size_mb) || payload.generated_max_size_mb < 100 || payload.generated_max_size_mb > 102400) {
        throw new Error("生成文件空间上限必须是 100-102400 的整数 MB");
      }
      if (!Number.isInteger(payload.generated_prune_size_mb) || payload.generated_prune_size_mb < 10 || payload.generated_prune_size_mb > 10240) {
        throw new Error("触发后清理量必须是 10-10240 的整数 MB");
      }
      if (payload.generated_prune_size_mb >= payload.generated_max_size_mb) {
        throw new Error("触发后清理量必须小于生成文件空间上限");
      }
      if (payload.use_proxy && !/^https?:\/\//i.test(payload.proxy)) {
        throw new Error("基础代理地址必须以 http:// 或 https:// 开头");
      }
      if (payload.resource_use_proxy && !/^https?:\/\//i.test(payload.resource_proxy)) {
        throw new Error("资源代理地址必须以 http:// 或 https:// 开头");
      }
      if (payload.imgbed_enabled) {
        if (!/^https?:\/\//i.test(payload.imgbed_api_url)) {
          throw new Error("图床 API 地址必须以 http:// 或 https:// 开头");
        }
        if (!payload.imgbed_api_key) {
          throw new Error("开启图传模式时，图床密钥不能为空");
        }
      }
      if (!Number.isInteger(payload.retry_max_attempts) || payload.retry_max_attempts < 1 || payload.retry_max_attempts > 10) {
        throw new Error("最大尝试次数必须是 1-10 的整数");
      }
      if (!Number.isFinite(payload.retry_backoff_seconds) || payload.retry_backoff_seconds < 0 || payload.retry_backoff_seconds > 30) {
        throw new Error("重试退避基数必须是 0-30 的数字");
      }
      if (!["round_robin", "random"].includes(payload.token_rotation_strategy)) {
        throw new Error("Token 轮换策略无效");
      }

      const res = await fetch("/api/v1/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        showMsg(configMsg, "配置已保存", false);
        showToast("配置已保存", false);
        await loadConfig();
      } else {
        showMsg(configMsg, "保存失败，请检查服务状态", true);
        showToast("保存失败，请检查服务状态", true);
      }
    } catch (err) {
      showMsg(configMsg, err.message, true);
      showToast(err.message || "保存失败", true);
    }
    saveConfigBtn.disabled = false;
  });

  function formatProxyConnectivityItem(title, item) {
    const data = item && typeof item === "object" ? item : {};
    const enabled = Boolean(data.enabled);
    const statusCode = data.status_code == null ? null : Number(data.status_code);
    let statusText = "连接失败";
    if (!enabled) {
      statusText = "未启用";
    } else if (Boolean(data.ok)) {
      statusText = "连接成功";
    } else if (statusCode != null) {
      statusText = "目标已响应";
    }
    const elapsedText = Number.isFinite(Number(data.elapsed_ms))
      ? `${Number(data.elapsed_ms)} ms`
      : "-";
    const statusCodeText = statusCode == null ? "-" : String(statusCode);
    const proxyText = String(data.proxy || "").trim() || "未填写";
    const targetText = String(data.target_url || "").trim() || "-";
    let messageText = String(data.message || "").trim() || "-";
    if (enabled && statusCode != null && [401, 403].includes(statusCode)) {
      messageText = "已收到上游响应，说明代理链路是通的；当前检测请求本身没有业务权限。";
    }
    return [
      `${title}`,
      `状态：${statusText}`,
      `代理地址：${proxyText}`,
      `检测目标：${targetText}`,
      `耗时：${elapsedText}`,
      `HTTP 状态码：${statusCodeText}`,
      `详细信息：${messageText}`,
    ].join("\n");
  }

  function formatProxyBusinessItem(title, item) {
    const data = item && typeof item === "object" ? item : {};
    const enabled = Boolean(data.enabled);
    const hasToken = Boolean(String(data.token_id || "").trim());
    const statusCode = data.status_code == null ? null : Number(data.status_code);
    let statusText = "检测失败";
    if (!enabled) {
      statusText = "未启用";
    } else if (!hasToken) {
      statusText = "未执行";
    } else if (Boolean(data.ok)) {
      statusText = "权限检测成功";
    } else if (statusCode != null) {
      statusText = "权限检测失败";
    }
    const elapsedText = Number.isFinite(Number(data.elapsed_ms))
      ? `${Number(data.elapsed_ms)} ms`
      : "-";
    const statusCodeText = statusCode == null ? "-" : String(statusCode);
    const tokenIdText = String(data.token_id || "").trim() || "-";
    const tokenSourceText = String(data.token_source || "").trim() || "-";
    const tokenPreviewText = String(data.token_preview || "").trim() || "-";
    const accountIdText = String(data.account_id || "").trim() || "-";
    const messageText = String(data.message || "").trim() || "-";
    return [
      `${title}`,
      `状态：${statusText}`,
      `检测目标：${String(data.target_url || "").trim() || "-"}`,
      `耗时：${elapsedText}`,
      `HTTP 状态码：${statusCodeText}`,
      `Token ID：${tokenIdText}`,
      `Token 来源：${tokenSourceText}`,
      `Token 预览：${tokenPreviewText}`,
      `Account ID：${accountIdText}`,
      `详细信息：${messageText}`,
    ].join("\n");
  }

  function formatProxyTestResult(payload) {
    const data = payload && typeof payload === "object" ? payload : {};
    const connectivity = data.connectivity && typeof data.connectivity === "object"
      ? data.connectivity
      : data;
    const business = data.business && typeof data.business === "object"
      ? data.business
      : {};
    const connectivitySections = [
      formatProxyConnectivityItem("基础代理", connectivity.basic),
      formatProxyConnectivityItem("资源代理", connectivity.resource),
    ];
    const businessSections = [
      formatProxyBusinessItem("基础代理业务权限", business.basic),
    ];
    return [
      "代理检测结果",
      "",
      "一、连通性检测",
      connectivitySections.join("\n\n"),
      "",
      "二、业务权限检测",
      businessSections.join("\n\n"),
    ].join("\n");
  }

  async function handleProxyTest() {
    if (proxyTestResult) {
      proxyTestResult.textContent = "正在检测代理连通性和业务权限，请稍候...";
    }
    const payload = {
      use_proxy: confUseProxy.checked,
      proxy: confProxy.value.trim(),
      resource_use_proxy: confResourceUseProxy.checked,
      resource_proxy: confResourceProxy.value.trim(),
    };
    if (payload.use_proxy && !/^https?:\/\//i.test(payload.proxy)) {
      throw new Error("基础代理地址必须以 http:// 或 https:// 开头");
    }
    if (payload.resource_use_proxy && !/^https?:\/\//i.test(payload.resource_proxy)) {
      throw new Error("资源代理地址必须以 http:// 或 https:// 开头");
    }
    const res = await fetch("/api/v1/proxy/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "代理与业务权限检测失败");
    }
    if (proxyTestResult) {
      proxyTestResult.textContent = formatProxyTestResult(data);
    }
    showToast("代理与业务权限检测已完成", false);
  }

  if (testProxyBtn) {
    testProxyBtn.addEventListener("click", async () => {
      testProxyBtn.disabled = true;
      if (proxyTestResult) {
        proxyTestResult.textContent = "正在检测代理连通性和业务权限，请稍候...";
      }
      try {
        await handleProxyTest();
      } catch (err) {
        if (proxyTestResult) {
          proxyTestResult.textContent = String(
            err?.message || err || "代理与业务权限检测失败"
          );
        }
        showToast(err.message || "代理与业务权限检测失败", true);
      } finally {
        testProxyBtn.disabled = false;
      }
    });
  }

  function formatTs(ts) {
    if (!ts) return "-";
    const d = new Date(Number(ts) * 1000);
    if (Number.isNaN(d.getTime())) return "-";
    return d.toLocaleString();
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function buildPromptSummary(value) {
    const raw = String(value || "").trim();
    if (!raw) return "-";
    const chars = Array.from(raw);
    if (chars.length <= 4) return raw;
    return `${chars.slice(0, 4).join("")}...`;
  }

  function truncateText(value, maxLen) {
    const text = String(value || "");
    if (text.length <= maxLen) return text;
    return `${text.slice(0, maxLen)}...`;
  }

  function parseTokenJsonPayload(value) {
    if (Array.isArray(value)) {
      return value.map((v) => String(v || "").trim()).filter(Boolean);
    }
    if (value && typeof value === "object") {
      if (Array.isArray(value.tokens)) {
        return value.tokens.map((v) => String(v || "").trim()).filter(Boolean);
      }
      if (typeof value.token === "string") {
        const single = value.token.trim();
        return single ? [single] : [];
      }
    }
    return [];
  }

  async function collectTokensFromInputs() {
    const textTokens = String(tokenInput?.value || "")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);

    const fileList = Array.from(tokenFile?.files || []);
    const fileTokens = [];
    for (const file of fileList) {
      const raw = await file.text();
      const trimmed = String(raw || "").trim();
      if (!trimmed) continue;

      const lowerName = String(file.name || "").toLowerCase();
      if (lowerName.endsWith(".json")) {
        let parsed;
        try {
          parsed = JSON.parse(trimmed);
        } catch (_) {
          throw new Error(`文件 ${file.name} 不是有效 JSON`);
        }
        const parsedTokens = parseTokenJsonPayload(parsed);
        if (!parsedTokens.length) {
          throw new Error(`文件 ${file.name} 未找到可用 token`);
        }
        fileTokens.push(...parsedTokens);
        continue;
      }

      fileTokens.push(
        ...trimmed
          .split(/\r?\n/)
          .map((line) => line.trim())
          .filter(Boolean)
      );
    }

    const unique = [];
    const seen = new Set();
    for (const token of [...textTokens, ...fileTokens]) {
      const key = String(token || "").trim();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      unique.push(key);
    }
    return unique;
  }

  function downloadJsonFile(filename, payload) {
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json;charset=utf-8"
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function nowStamp() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  }

  function cookieToHeaderString(value) {
    if (typeof value === "string") {
      const txt = value.trim();
      if (!txt) return "";
      if (txt.toLowerCase().startsWith("cookie:")) {
        return txt.slice(7).trim();
      }
      return txt;
    }
    if (Array.isArray(value)) {
      const pairs = [];
      value.forEach((item) => {
        if (typeof item === "string") {
          const txt = item.trim();
          if (txt) pairs.push(txt);
          return;
        }
        if (!item || typeof item !== "object") return;
        const name = String(item.name || "").trim();
        if (!name) return;
        pairs.push(`${name}=${String(item.value || "").trim()}`);
      });
      return pairs.join("; ");
    }
    if (value && typeof value === "object") {
      if (Array.isArray(value.cookies)) return cookieToHeaderString(value.cookies);
      if (value.cookie != null) return cookieToHeaderString(value.cookie);
    }
    return "";
  }

  function toCookieBatchItems(value) {
    if (Array.isArray(value)) {
      if (value.length > 0 && value.every((item) => item && typeof item === "object" && "name" in item && "value" in item)) {
        const cookie = cookieToHeaderString(value);
        return cookie ? [{ name: null, cookie }] : [];
      }
      return value.map((item, idx) => {
        if (!item || typeof item !== "object") {
          throw new Error(`第 ${idx + 1} 项不是对象`);
        }
        const cookie = cookieToHeaderString(item.cookie != null ? item.cookie : item.cookies != null ? item.cookies : item);
        if (!cookie) {
          throw new Error(`第 ${idx + 1} 项缺少 cookie`);
        }
        return {
          name: String(item.name || "").trim() || null,
          cookie,
        };
      });
    }
    if (value && typeof value === "object") {
      if (Array.isArray(value.items)) return toCookieBatchItems(value.items);
      const cookie = cookieToHeaderString(value.cookie != null ? value.cookie : value.cookies != null ? value.cookies : value);
      if (!cookie) throw new Error("cookie 内容为空");
      return [{ name: String(value.name || "").trim() || null, cookie }];
    }
    const cookie = cookieToHeaderString(value);
    if (!cookie) throw new Error("cookie 内容为空");
    return [{ name: null, cookie }];
  }

  function getImportDetailPayload(payload) {
    if (payload && typeof payload === "object" && payload.detail !== undefined) {
      return payload.detail;
    }
    return payload;
  }

  function getImportSuccessCount(payload) {
    const value = Number(
      payload?.success_count != null
        ? payload.success_count
        : payload?.added_count != null
          ? payload.added_count
          : 0
    );
    return Number.isFinite(value) ? value : 0;
  }

  function getImportFailedCount(payload) {
    const value = Number(
      payload?.error_count != null
        ? payload.error_count
        : payload?.failed_count != null
          ? payload.failed_count
          : 0
    );
    return Number.isFinite(value) ? value : 0;
  }

  function getImportDuplicateCount(payload) {
    const value = Number(
      payload?.duplicate_count != null
        ? payload.duplicate_count
        : payload?.deduplicated_count != null
          ? payload.deduplicated_count
          : 0
    );
    return Number.isFinite(value) ? value : 0;
  }

  function getImportRequestDuplicateCount(payload) {
    const value = Number(payload?.request_duplicate_count ?? 0);
    return Number.isFinite(value) ? value : 0;
  }

  function getImportListDuplicateCount(payload) {
    const value = Number(payload?.list_duplicate_count ?? 0);
    return Number.isFinite(value) ? value : 0;
  }

  function getImportOverwrittenCount(payload) {
    const value = Number(payload?.overwritten_count ?? 0);
    return Number.isFinite(value) ? value : 0;
  }

  function buildImportSummaryText(label, payload) {
    const success = getImportSuccessCount(payload);
    const failed = getImportFailedCount(payload);
    const duplicate = getImportDuplicateCount(payload);
    const requestDuplicate = getImportRequestDuplicateCount(payload);
    const listDuplicate = getImportListDuplicateCount(payload);
    const overwritten = getImportOverwrittenCount(payload);
    const parts = [
      `${label}完成：成功 ${success}`,
      `失败 ${failed}`,
      `重复 ${duplicate}`,
    ];
    if (requestDuplicate > 0) {
      parts.push(`本次导入内重复 ${requestDuplicate}`);
    }
    if (listDuplicate > 0) {
      parts.push(`与列表重复 ${listDuplicate}`);
    }
    if (overwritten > 0) {
      parts.push(`已覆盖 ${overwritten}`);
    }
    return parts.join("，");
  }

  async function importCookies() {
    const text = String(cookieInput?.value || "").trim();
    if (!text) {
      showMsg(refreshMsg, "请先粘贴或上传 Cookie", true);
      return;
    }

    let items = [];
    try {
      let parsed = text;
      try {
        parsed = JSON.parse(text);
      } catch (_) {
        parsed = text;
      }
      items = toCookieBatchItems(parsed);
    } catch (err) {
      showMsg(refreshMsg, err.message || "Cookie 解析失败", true);
      return;
    }

    if (!items.length) {
      showMsg(refreshMsg, "未找到可导入的 Cookie", true);
      return;
    }

    try {
      if (importCookieBtn) importCookieBtn.disabled = true;
      showMsg(refreshMsg, `Cookie 导入中，共 ${items.length} 项...`, false, { duration: 0 });

      const res = await fetch("/api/v1/refresh-profiles/import-cookie-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items }),
      });

      let data = null;
      try {
        data = await res.json();
      } catch (_) {
        data = null;
      }

      if (!res.ok) {
        const detailPayload = getImportDetailPayload(data);
        if (detailPayload && typeof detailPayload === "object") {
          showMsg(
            refreshMsg,
            buildImportSummaryText("Cookie导入", detailPayload),
            true,
            { duration: 8000 }
          );
          return;
        }

        const detailText =
          (typeof detailPayload === "string" && detailPayload.trim())
            ? detailPayload
            : "Cookie 导入失败";
        throw new Error(detailText);
      }

      showMsg(
        refreshMsg,
        buildImportSummaryText("Cookie导入", data),
        getImportFailedCount(data) > 0,
        { duration: 8000 }
      );
      if (cookieInput) cookieInput.value = "";
      if (cookieFile) cookieFile.value = "";
      await loadTokens();
    } catch (err) {
      showMsg(refreshMsg, err.message || "Cookie 导入失败", true, { duration: 8000 });
    } finally {
      if (importCookieBtn) importCookieBtn.disabled = false;
    }
  }

  if (cookieFile) {
    cookieFile.addEventListener("change", async () => {
      const files = cookieFile.files ? Array.from(cookieFile.files) : [];
      if (!files.length) return;
      try {
        if (files.length === 1) {
          const text = await files[0].text();
          if (cookieInput) cookieInput.value = text;
          showMsg(refreshMsg, `已读取 1 个文件：${files[0].name}`, false, { duration: 5000 });
          return;
        }

        const items = [];
        for (const file of files) {
          const raw = await file.text();
          const baseName = String(file.name || "").replace(/\.(json|txt)$/i, "").trim();
          let parsed = raw;
          try {
            parsed = JSON.parse(raw);
          } catch (_) {
            // plain text cookie string
          }
          const cookie = cookieToHeaderString(parsed);
          if (!cookie) continue;
          items.push({
            name: baseName || null,
            cookie,
          });
        }
        if (cookieInput) {
          cookieInput.value = JSON.stringify(items, null, 2);
        }
        showMsg(refreshMsg, `已读取 ${files.length} 个文件，解析出 ${items.length} 个 Cookie`, false, { duration: 6000 });
      } catch (err) {
        showMsg(refreshMsg, "读取 Cookie 文件失败", true);
      }
    });
  }

  if (importCookieBtn) importCookieBtn.addEventListener("click", importCookies);
  // profile operation handlers are attached as window methods above.

  async function loadLogs() {
    if (!logsTbody) return;
    try {
      const rangeValue = logStatsRange ? String(logStatsRange.value || "today") : "today";
      const logParams = getLogsQueryParams();
      const [runningResult, logsResult, statsResult] = await Promise.allSettled([
        fetch("/api/v1/logs/running?limit=200"),
        fetch(`/api/v1/logs?${logParams.toString()}`),
        fetch(`/api/v1/logs/stats?range=${encodeURIComponent(rangeValue)}`),
      ]);

      let runningItems = [];
      if (runningResult.status === "fulfilled" && runningResult.value.ok) {
        const runningData = await runningResult.value.json();
        runningItems = Array.isArray(runningData.items) ? runningData.items : [];
      }

      if (logsResult.status !== "fulfilled" || !logsResult.value.ok) {
        throw new Error("加载日志失败");
      }

      const logsData = await logsResult.value.json();
      logsCurrentPage = Math.max(1, Number(logsData.page || logsCurrentPage || 1));
      logsTotalPages = Math.max(1, Number(logsData.total_pages || 1));
      renderLogsPagination();
      await loadFailedAccounts();
      renderLogs(logsData.logs || [], runningItems);

      if (statsResult.status === "fulfilled" && statsResult.value.ok) {
        const statsData = await statsResult.value.json();
        renderLogStats(statsData);
      } else {
        renderLogStats(null);
      }
    } catch (err) {
      logsTbody.innerHTML = `<tr><td colspan="8" class="empty-state" style="color: #ffb4bc;">${err.message || "日志加载失败"}</td></tr>`;
      logsRunningTotal = 0;
      logsTotalPages = Math.max(1, logsCurrentPage || 1);
      renderLogsPagination();
      renderLogStats(null);
    }
  }

  function renderLogStats(stats) {
    const imageCount = Number(stats?.generated_images || 0);
    const videoCount = Number(stats?.generated_videos || 0);
    const totalCount = Number(stats?.total_requests || 0);
    const failCount = Number(stats?.failed_requests || 0);

    if (logsStatsImageCount) logsStatsImageCount.textContent = String(imageCount);
    if (logsStatsVideoCount) logsStatsVideoCount.textContent = String(videoCount);
    if (logsStatsTotalCount) logsStatsTotalCount.textContent = String(totalCount);
    if (logsStatsFailCount) logsStatsFailCount.textContent = String(failCount);

    if (!logStatsUpdatedAt) return;
    if (!stats) {
      logStatsUpdatedAt.textContent = "统计信息暂不可用";
      return;
    }

    const selectedLabel = logStatsRange?.selectedOptions?.[0]?.textContent || "当前范围";
    const endTs = Number(stats.end_ts || 0);
    const updatedText = endTs > 0 ? new Date(endTs * 1000).toLocaleString() : "-";
    logStatsUpdatedAt.textContent = `${selectedLabel}统计，更新于 ${updatedText}`;
  }

  function renderLogsPagination() {
    const safeTotalPages = Math.max(1, Number(logsTotalPages || 1));
    const safeCurrent = Math.min(Math.max(1, Number(logsCurrentPage || 1)), safeTotalPages);
    logsCurrentPage = safeCurrent;
    logsTotalPages = safeTotalPages;

    if (logsPageInfo) {
      logsPageInfo.textContent = `第 ${safeCurrent} / ${safeTotalPages} 页`;
    }
    if (logsPrevBtn) {
      logsPrevBtn.disabled = safeCurrent <= 1;
    }
    if (logsNextBtn) {
      logsNextBtn.disabled = safeCurrent >= safeTotalPages;
    }
  }

  function buildLogRow(item, { forceInProgress = false } = {}) {
    const tr = document.createElement("tr");
    const dt = new Date((item.ts || 0) * 1000);
    const dateText = dt.toLocaleDateString();
    const timeText = dt.toLocaleTimeString();
    const t = Number(item.duration_sec || 0);
    const status = Number(item.status_code || 0);
    const taskStatus = forceInProgress ? "IN_PROGRESS" : String(item.task_status || "").toUpperCase();
    const previewUrl = normalizePreviewUrl(String(item.preview_url || "").trim());
    const failedTaskStatuses = new Set(["FAILED", "ERROR", "CANCELLED"]);
    const generationOperations = new Set(["api.generate", "chat.completions", "images.generations"]);
    const generationPaths = new Set(["/api/v1/generate", "/v1/chat/completions", "/v1/images/generations"]);
    const operation = String(item.operation || "").trim();
    const path = String(item.path || "").trim();
    const isGenerationRequest = generationOperations.has(operation) || generationPaths.has(path);
    const missingGenerationResult = status >= 200 && status < 300
      && taskStatus !== "IN_PROGRESS"
      && isGenerationRequest
      && !previewUrl;
    const isFailed = !forceInProgress && (
      status >= 400 || failedTaskStatuses.has(taskStatus) || missingGenerationResult
    );
    const isRunning = !isFailed && taskStatus === "IN_PROGRESS";
    const isSuccess = !isRunning && !isFailed;
    const stateClass = isRunning ? "running" : (isFailed ? "failed" : "success");
    const stateLabel = isRunning
      ? "进行中"
      : (isFailed ? "生成失败" : "已完成");
    const stateIcon = isRunning
      ? `<span class="icon-spinner" aria-hidden="true"></span>`
      : (isFailed
        ? `<span class="icon-error" aria-hidden="true">!</span>`
        : `<span class="icon-check" aria-hidden="true">✓</span>`);
    const errCode = String(item.error_code || "").trim();
    const failedStatusText = status >= 400 ? String(status) : stateLabel;
    const failedStateContent = errCode
      ? `<button class="log-state log-state-btn failed" data-error-code="${escapeHtml(errCode)}" type="button">${stateIcon}<span>${escapeHtml(failedStatusText)}</span></button>`
      : `<span class="log-state failed"><span class="icon-error" aria-hidden="true">!</span><span>${escapeHtml(failedStatusText)}</span></span>`;
    const stateContent = isFailed ? failedStateContent : `${stateIcon}<span>${stateLabel}</span>`;
    const statusCell = isFailed ? stateContent : `<span class="log-state ${stateClass}">${stateContent}</span>`;
    const taskProgressRaw = Number(item.task_progress);
    const progressCell = taskStatus === "IN_PROGRESS"
      ? `<span class="status-badge status-active">${Number.isFinite(taskProgressRaw) ? Math.round(taskProgressRaw) : 0}%</span>`
      : `<span style="color:#7f96ad;">-</span>`;
    const previewKind = String(item.preview_kind || "").trim();
    const tokenName = String(item.token_account_name || "").trim();
    const tokenEmail = String(item.token_account_email || "").trim();
    const tokenId = String(item.token_id || "").trim();
    const tokenSource = String(item.token_source || "").trim();
    const tokenAttempt = Number(item.token_attempt || 0);
    const tokenTitleParts = [];
    if (tokenName) tokenTitleParts.push(`账号: ${tokenName}`);
    if (tokenId) tokenTitleParts.push(`ID: ${tokenId}`);
    if (tokenSource) tokenTitleParts.push(`来源: ${tokenSource}`);
    if (tokenAttempt > 0) tokenTitleParts.push(`尝试: 第${tokenAttempt}次`);
    const tokenTitle = escapeHtml(tokenTitleParts.join(" | "));
    const accountParts = [];
    accountParts.push(
      tokenEmail
        ? `<span class="log-account-email">${escapeHtml(tokenEmail)}</span>`
        : `<span class="log-account-email">-</span>`
    );
    const modelText = String(item.model || "-");
    const modelParamsText = String(item.model_params || "").trim();
    const promptText = String(item.prompt_preview || "").trim();
    const promptSummary = buildPromptSummary(promptText);
    const tokenCell = `<div class="log-account-cell">${accountParts.join("<br>")}</div>`;
    const previewCell = previewUrl
      ? `<button class="small preview-btn" data-url="${encodeURIComponent(previewUrl)}" data-kind="${previewKind || ""}">查看</button>`
      : `<span style="color:#7f96ad;">-</span>`;
    const modelTitle = escapeHtml([modelText, modelParamsText].filter(Boolean).join(" | "));
    const modelCell = `
      <div class="log-model-cell">
        <span class="log-model-name">${escapeHtml(modelText)}</span>
        ${modelParamsText ? `<span class="log-model-meta">${escapeHtml(modelParamsText)}</span>` : ""}
      </div>
    `;
    tr.innerHTML = `
      <td class="log-time-cell"><span class="date">${dateText}</span><span class="time">${timeText}</span></td>
      <td>${statusCell}</td>
      <td style="color:#a8bfd8;">${t}</td>
      <td>${progressCell}</td>
      <td title="${tokenTitle}">${tokenCell}</td>
      <td title="${modelTitle || escapeHtml(modelText)}">${modelCell}</td>
      <td class="log-prompt-cell">${promptText ? `<button class="log-prompt-btn" data-full-prompt="${encodeURIComponent(promptText)}" type="button">${escapeHtml(promptSummary)}</button>` : "-"}</td>
      <td>${previewCell}</td>
    `;
    if (isRunning) tr.classList.add("log-row-running");
    return tr;
  }

  function renderLogs(logs, runningItems = []) {
    if (logsAutoTimer) {
      clearTimeout(logsAutoTimer);
      logsAutoTimer = null;
    }
    const selectedAccount = getSelectedLogAccount();
    const runningRows = isFailedOnlyFilterEnabled()
      ? []
      : (Array.isArray(runningItems) ? runningItems : []).filter((item) =>
          matchesLogAccount(item, selectedAccount)
        );
    logsRunningTotal = runningRows.length;
    const allRows = [
      ...runningRows,
      ...(Array.isArray(logs) ? logs : []),
    ];

    if (!allRows.length) {
      logsTbody.innerHTML = `<tr><td colspan="8" class="empty-state">暂无请求日志</td></tr>`;
      return;
    }

    logsTbody.innerHTML = "";
    runningRows.forEach((item) => {
      logsTbody.appendChild(buildLogRow(item, { forceInProgress: true }));
    });
    (Array.isArray(logs) ? logs : []).forEach((item) => {
      logsTbody.appendChild(buildLogRow(item));
    });

    if (logsRunningTotal > 0 && isLogsTabActive()) {
      logsAutoTimer = setTimeout(() => {
        if (isLogsTabActive()) loadLogs();
      }, LOGS_POLL_MS);
    }
  }

  function inferPreviewKind(url) {
    const lowered = String(url || "").toLowerCase();
    if (/(\.mp4|\.webm|\.ogg)(\?|$)/.test(lowered)) return "video";
    return "image";
  }

  function normalizePreviewUrl(url) {
    const raw = String(url || "").trim();
    if (!raw) return "";

    if (/^https?:\/\//i.test(raw)) {
      try {
        const u = new URL(raw);
        if (/^\/(generated)\//.test(u.pathname)) {
          return `${window.location.origin}${u.pathname}${u.search || ""}`;
        }
      } catch (_) {
        // ignore parse errors and return original
      }
      return raw;
    }

    if (raw.startsWith("/")) {
      return `${window.location.origin}${raw}`;
    }
    return raw;
  }

  function closePreview() {
    if (!previewModal || !previewContent) return;
    previewModal.classList.remove("open");
    previewModal.setAttribute("aria-hidden", "true");
    previewContent.innerHTML = "";
    if (previewDownloadBtn) {
      previewDownloadBtn.setAttribute("href", "#");
      previewDownloadBtn.setAttribute("download", "");
    }
  }

  function closeErrorDetail() {
    if (!errorDetailModal || !errorDetailContent || !errorDetailCode) return;
    errorDetailModal.classList.remove("open");
    errorDetailModal.setAttribute("aria-hidden", "true");
    errorDetailCode.textContent = "错误信息";
    errorDetailContent.innerHTML = "";
  }

  function closePromptDetail() {
    if (!promptDetailModal || !promptDetailContent) return;
    promptDetailModal.classList.remove("open");
    promptDetailModal.setAttribute("aria-hidden", "true");
    promptDetailContent.textContent = "";
  }

  async function openErrorDetailByCode(code) {
    const errCode = String(code || "").trim();
    if (!errCode || !errorDetailModal || !errorDetailCode || !errorDetailContent) return;
    errorDetailCode.textContent = "错误信息";
    errorDetailContent.innerHTML = `<pre>加载中...</pre>`;
    errorDetailModal.classList.add("open");
    errorDetailModal.setAttribute("aria-hidden", "false");
    try {
      const res = await fetch(`/api/v1/logs/errors/${encodeURIComponent(errCode)}`);
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(txt || `获取错误详情失败 (${res.status})`);
      }
      const data = await res.json();
      const message = String(data?.message || "").trim() || "暂无错误信息";
      errorDetailContent.innerHTML = `<pre>${escapeHtml(message)}</pre>`;
    } catch (err) {
      errorDetailContent.innerHTML = `<pre>${escapeHtml(err.message || "获取错误详情失败")}</pre>`;
    }
  }

  function buildDownloadFilename(url, kind) {
    try {
      const u = new URL(url, window.location.origin);
      const fromPath = (u.pathname.split("/").pop() || "").trim();
      if (fromPath) return fromPath;
    } catch (err) {
      // ignore parse errors and fallback
    }
    const ext = kind === "video" ? "mp4" : "png";
    return `asset-${Date.now()}.${ext}`;
  }

  function openPreview(url, kind) {
    if (!previewModal || !previewContent || !url) return;
    const mediaKind = kind || inferPreviewKind(url);
    if (mediaKind === "video") {
      previewContent.innerHTML = `<video controls autoplay playsinline src="${url}"></video>`;
    } else {
      previewContent.innerHTML = `<img src="${url}" alt="预览图" />`;
    }
    if (previewDownloadBtn) {
      previewDownloadBtn.setAttribute("href", url);
      previewDownloadBtn.setAttribute("download", buildDownloadFilename(url, mediaKind));
    }
    previewModal.classList.add("open");
    previewModal.setAttribute("aria-hidden", "false");
  }

  function openPromptDetail(text) {
    if (!promptDetailModal || !promptDetailContent) return;
    promptDetailContent.textContent = String(text || "").trim() || "暂无提示词";
    promptDetailModal.classList.add("open");
    promptDetailModal.setAttribute("aria-hidden", "false");
  }

  if (logsTbody) {
    logsTbody.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const promptBtn = target.closest("[data-full-prompt]");
      if (promptBtn instanceof HTMLElement) {
        const fullPrompt = String(promptBtn.getAttribute("data-full-prompt") || "").trim();
        openPromptDetail(decodeURIComponent(fullPrompt));
        return;
      }
      if (target.classList.contains("preview-btn")) {
        const encodedUrl = target.getAttribute("data-url") || "";
        const kind = (target.getAttribute("data-kind") || "").trim();
        if (!encodedUrl) return;
        openPreview(decodeURIComponent(encodedUrl), kind);
        return;
      }
      const clickableErrorEl = target.closest("[data-error-code]");
      if (clickableErrorEl instanceof HTMLElement) {
        const code = String(clickableErrorEl.getAttribute("data-error-code") || "").trim();
        if (!code) return;
        openErrorDetailByCode(code);
      }
    });
  }

  if (previewCloseBtn) {
    previewCloseBtn.addEventListener("click", closePreview);
  }

  if (previewModal) {
    previewModal.addEventListener("click", (event) => {
      if (event.target === previewModal) closePreview();
    });
  }

  if (errorDetailCloseBtn) {
    errorDetailCloseBtn.addEventListener("click", closeErrorDetail);
  }

  if (errorDetailModal) {
    errorDetailModal.addEventListener("click", (event) => {
      if (event.target === errorDetailModal) closeErrorDetail();
    });
  }

  if (promptDetailCloseBtn) {
    promptDetailCloseBtn.addEventListener("click", closePromptDetail);
  }

  if (promptDetailModal) {
    promptDetailModal.addEventListener("click", (event) => {
      if (event.target === promptDetailModal) closePromptDetail();
    });
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closePreview();
      closeErrorDetail();
      closePromptDetail();
      closeDialog(tokenModal);
      closeDialog(refreshModal);
    }
  });

  if (refreshLogsBtn) {
    refreshLogsBtn.addEventListener("click", () => {
      logsCurrentPage = 1;
      loadLogs();
    });
  }

  if (logsFailedOnly) {
    logsFailedOnly.addEventListener("change", () => {
      logsCurrentPage = 1;
      loadLogs();
    });
  }

  if (logsFailedAccount) {
    logsFailedAccount.addEventListener("change", () => {
      logsCurrentPage = 1;
      loadLogs();
    });
  }

  if (clearLogFiltersBtn) {
    clearLogFiltersBtn.addEventListener("click", () => {
      if (logsFailedOnly) logsFailedOnly.checked = false;
      if (logsFailedAccount) logsFailedAccount.value = "";
      logsCurrentPage = 1;
      loadLogs();
    });
  }

  if (logStatsRange) {
    logStatsRange.addEventListener("change", () => {
      logsCurrentPage = 1;
      loadLogs();
    });
  }

  if (logsPrevBtn) {
    logsPrevBtn.addEventListener("click", () => {
      if (logsCurrentPage <= 1) return;
      logsCurrentPage -= 1;
      loadLogs();
    });
  }

  if (tokenPrevBtn) {
    tokenPrevBtn.addEventListener("click", () => {
      if (tokenCurrentPage <= 1) return;
      tokenCurrentPage -= 1;
      tokenSelectedIds.clear();
      loadTokens();
    });
  }

  if (tokenNextBtn) {
    tokenNextBtn.addEventListener("click", () => {
      if (tokenCurrentPage >= tokenTotalPages) return;
      tokenCurrentPage += 1;
      tokenSelectedIds.clear();
      loadTokens();
    });
  }

  if (tokenPageSizeSelect) {
    tokenPageSizeSelect.addEventListener("change", () => {
      const selectedSize = Number(tokenPageSizeSelect.value || 50);
      tokenPageSize = TOKEN_PAGE_SIZE_OPTIONS.includes(selectedSize) ? selectedSize : 50;
      try {
        localStorage.setItem(TOKEN_PAGE_SIZE_STORAGE_KEY, String(tokenPageSize));
      } catch (_) {
        // Ignore private-mode storage failures; the current selection still applies.
      }
      tokenCurrentPage = 1;
      tokenSelectedIds.clear();
      loadTokens();
    });
  }

  if (tokenJumpBtn && tokenJumpInput) {
    tokenJumpBtn.addEventListener("click", () => {
      const requestedPage = Number(tokenJumpInput.value || 1);
      if (!Number.isFinite(requestedPage)) return;
      const safePage = Math.min(Math.max(1, Math.floor(requestedPage)), tokenTotalPages);
      if (safePage === tokenCurrentPage) {
        tokenJumpInput.value = String(tokenCurrentPage);
        return;
      }
      tokenCurrentPage = safePage;
      tokenSelectedIds.clear();
      loadTokens();
    });

    tokenJumpInput.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      tokenJumpBtn.click();
    });
  }

  if (logsNextBtn) {
    logsNextBtn.addEventListener("click", () => {
      if (logsCurrentPage >= logsTotalPages) return;
      logsCurrentPage += 1;
      loadLogs();
    });
  }

  if (backfillInvalidTokenLogsBtn) {
    backfillInvalidTokenLogsBtn.addEventListener("click", async () => {
      if (!confirm("将扫描请求日志中的 Token invalid or expired 记录，并把对应账号标记为已失效，同时禁用自动刷新。确定继续吗？")) return;
      backfillInvalidTokenLogsBtn.disabled = true;
      try {
        const res = await fetch("/api/v1/logs/backfill-invalid-token-exhausted", { method: "POST" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data?.detail || "标记失败");
        const changed = Number(data?.changed_count || 0);
        const disabled = Number(data?.disabled_auto_refresh_count || 0);
        const matched = Number(data?.matched_logs || 0);
        const skipped = Number(data?.skipped_count || 0);
        showToast(`检测 ${matched} 条日志，标记已失效 ${changed} 个账号，禁用自动刷新 ${disabled} 个${skipped ? `，跳过 ${skipped} 个` : ""}`, false, { duration: 7000 });
        await Promise.allSettled([loadTokens(), loadLogs()]);
      } catch (err) {
        showToast(err.message || "标记失败", true, { duration: 7000 });
      } finally {
        backfillInvalidTokenLogsBtn.disabled = false;
      }
    });
  }

  if (clearLogsBtn) {
    clearLogsBtn.addEventListener("click", async () => {
      if (!confirm("确定清空请求日志吗？")) return;
      try {
        const res = await fetch("/api/v1/logs", { method: "DELETE" });
        if (!res.ok) throw new Error("清空失败");
        logsCurrentPage = 1;
        loadLogs();
      } catch (err) {
        alert(err.message || "清空失败");
      }
    });
  }


  function showMsg(el, text, isError, options = {}) {
    if (!el) return;
    const duration = Number(options?.duration ?? 3000);
    if (el._msgTimer) {
      clearTimeout(el._msgTimer);
      el._msgTimer = null;
    }
    el.textContent = text;
    el.style.color = isError ? "#ffb4bc" : "#4de2c4";
    if (duration > 0) {
      el._msgTimer = setTimeout(() => {
        el.textContent = "";
        el._msgTimer = null;
      }, duration);
    }
  }

  let toastTimer = null;
  function showToast(text, isError = false, options = {}) {
    if (!appToast) return;
    const duration = Number(options?.duration ?? 2200);
    appToast.textContent = String(text || "").trim();
    appToast.classList.remove("success", "error", "show");
    appToast.classList.add(isError ? "error" : "success");
    appToast.classList.add("show");
    if (toastTimer) {
      clearTimeout(toastTimer);
      toastTimer = null;
    }
    if (duration > 0) {
      toastTimer = setTimeout(() => {
        appToast.classList.remove("show");
      }, duration);
    }
  }

  // Init
  loadTokens();
  loadConfig();
  renderLogsPagination();
});

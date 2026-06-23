/* AutoRead WebUI 管理页面 */

const bridge = window.AstrBotPluginPage;

// ======================================================================
// API 封装
// ======================================================================

async function apiGet(endpoint, params = {}) {
  try {
    return await bridge.apiGet(endpoint, params);
  } catch (err) {
    console.error(`[AutoRead] apiGet ${endpoint}:`, err);
    throw err;
  }
}

async function apiPost(endpoint, body = {}) {
  try {
    return await bridge.apiPost(endpoint, body);
  } catch (err) {
    console.error(`[AutoRead] apiPost ${endpoint}:`, err);
    throw err;
  }
}

async function uploadFile(endpoint, file) {
  try {
    return await bridge.upload(endpoint, file);
  } catch (err) {
    console.error(`[AutoRead] upload ${endpoint}:`, err);
    throw err;
  }
}

// ======================================================================
// UI 工具
// ======================================================================

function showMessage(text, type = "success") {
  const el = document.getElementById("messages");
  el.hidden = false;
  el.className = `messages ${type}`;
  el.textContent = text;
  setTimeout(() => { el.hidden = true; }, 5000);
}

function showError(text) {
  showMessage(text, "error");
}

function escapeHtml(str) {
  if (!str) return "";
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function truncate(str, max = 80) {
  if (!str) return "";
  return str.length > max ? str.slice(0, max) + "…" : str;
}

function formatTime(iso) {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString("zh-CN", {
      month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

// ======================================================================
// Tab 切换
// ======================================================================

function showTab(tabName) {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === tabName);
  });
  document.querySelectorAll("[data-tab-panel]").forEach(panel => {
    panel.hidden = panel.dataset.tabPanel !== tabName;
  });

  // 切换到设置页时自动加载设置和 providers
  if (tabName === "settings") {
    loadSettings();
    loadProviders();
  }
}

// ======================================================================
// 面板开关
// ======================================================================

let _currentPanel = null;

function openPanel(panelId, renderFn) {
  closePanel();
  const panel = document.getElementById(panelId);
  panel.hidden = false;
  renderFn();
  _currentPanel = panelId;
}

function closePanel() {
  if (_currentPanel) {
    document.getElementById(_currentPanel).hidden = true;
    _currentPanel = null;
  }
}

// ======================================================================
// 总览
// ======================================================================

async function loadOverview() {
  try {
    const data = await apiGet("overview");
    document.getElementById("ov-books").textContent = data.books_count;
    document.getElementById("ov-notes").textContent = data.notes_count;
    document.getElementById("ov-active").textContent = data.active_sessions_count;

    const errCard = document.getElementById("ov-error-card");
    if (data.last_error) {
      errCard.hidden = false;
      document.getElementById("ov-error").textContent = truncate(data.last_error, 120);
    } else {
      errCard.hidden = true;
    }
  } catch (err) {
    showError("加载总览失败: " + err.message);
  }
}

// ======================================================================
// 书籍列表
// ======================================================================

let _booksPage = 1;

async function loadBooks(page = 1) {
  _booksPage = page;
  const query = document.getElementById("books-search").value.trim();

  try {
    const data = await apiGet("books", { query, page, page_size: 20 });
    renderBooksTable(data.items);
    renderBooksPagination(data);
    updateNotesBookFilter(data.items);
  } catch (err) {
    showError("加载书籍列表失败: " + err.message);
  }
}

function renderBooksTable(items) {
  const tbody = document.getElementById("books-tbody");
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty">暂无书籍。请上传 txt/md 文件导入。</td></tr>`;
    return;
  }
  tbody.innerHTML = items.map(b => `
    <tr>
      <td>${escapeHtml(b.title)}</td>
      <td><code>${escapeHtml(b.book_id)}</code></td>
      <td>${b.total_chars.toLocaleString()}</td>
      <td>${b.total_chunks}</td>
      <td>${b.notes_count}</td>
      <td>${b.is_active ? "是" : "-"}</td>
      <td>${b.progress.percent}% (${b.progress.max_current_chunk_index}/${b.progress.total_chunks})</td>
      <td>${formatTime(b.created_at)}</td>
      <td>
        <button class="btn btn-small btn-secondary" data-action="book-detail" data-book-id="${escapeHtml(b.book_id)}">详情</button>
      </td>
    </tr>
  `).join("");
}

function renderBooksPagination(data) {
  const el = document.getElementById("books-pagination");
  el.innerHTML = `
    共 ${data.total} 条，第 ${data.page} / ${Math.ceil(data.total / data.page_size) || 1} 页
    <button class="btn btn-small" ${data.page <= 1 ? "disabled" : ""} data-action="books-prev">上一页</button>
    <button class="btn btn-small" ${data.page * data.page_size >= data.total ? "disabled" : ""} data-action="books-next">下一页</button>
  `;
}

async function loadBookDetail(bookId) {
  try {
    const data = await apiGet(`books/${bookId}`);
    openPanel("book-detail-panel", () => renderBookDetail(data));
  } catch (err) {
    showError("加载书籍详情失败: " + err.message);
  }
}

function renderBookDetail(data) {
  const body = document.getElementById("book-detail-body");
  const sessionsHtml = data.active_sessions.length
    ? data.active_sessions.map(s => `
        <div style="margin-bottom:6px;padding:8px;border:1px solid var(--border);border-radius:var(--radius);font-size:13px">
          会话: <code>${escapeHtml(s.session_id)}</code><br>
          进度: ${s.current_chunk_index}/${s.total_chunks} |
          状态: ${s.paused ? "已暂停" : "阅读中"}<br>
          上次: ${formatTime(s.last_read_at)} | 下次: ${formatTime(s.next_read_at)}
        </div>`).join("")
    : "<p style=\"color:var(--muted);font-size:13px\">暂无活跃会话</p>";

  body.innerHTML = `
    <dl>
      <dt>书名</dt><dd>${escapeHtml(data.title)}</dd>
      <dt>book_id</dt><dd><code>${escapeHtml(data.book_id)}</code></dd>
      <dt>来源</dt><dd>${escapeHtml(data.source_type)}</dd>
      <dt>总字符数</dt><dd>${data.total_chars.toLocaleString()}</dd>
      <dt>切片数</dt><dd>${data.total_chunks}</dd>
      <dt>笔记数</dt><dd>${data.notes_count}</dd>
      <dt>创建时间</dt><dd>${formatTime(data.created_at)}</dd>
    </dl>
    <div class="field-block" style="margin-top:12px">
      <h4>存储路径</h4>
      <p>${escapeHtml(data.source_path)}</p>
    </div>
    <div class="field-block">
      <h4>活跃会话</h4>
      ${sessionsHtml}
    </div>
  `;
}

// ======================================================================
// 上传
// ======================================================================

async function uploadBook() {
  const input = document.getElementById("upload-input");
  const file = input.files[0];
  if (!file) {
    showError("请先选择一个文件");
    return;
  }

  const ext = "." + file.name.split(".").pop().toLowerCase();
  if (ext !== ".txt" && ext !== ".md") {
    showError("仅支持 .txt 和 .md 文件");
    return;
  }

  const btn = document.getElementById("btn-upload");
  btn.disabled = true;
  btn.textContent = "上传中…";

  try {
    const result = await uploadFile("books/upload", file);
    showMessage(`已导入《${result.title}》，${result.total_chunks} 段，共 ${result.total_chars.toLocaleString()} 字符`);
    input.value = "";
    await loadOverview();
    await loadBooks();
  } catch (err) {
    showError("上传失败: " + (err.message || "未知错误"));
  } finally {
    btn.disabled = false;
    btn.textContent = "上传并导入";
  }
}

// ======================================================================
// 会话列表
// ======================================================================

async function loadSessions() {
  try {
    const data = await apiGet("sessions");
    renderSessionsTable(data.items);
  } catch (err) {
    showError("加载会话列表失败: " + err.message);
  }
}

function renderSessionsTable(items) {
  const tbody = document.getElementById("sessions-tbody");
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty">暂无活跃阅读任务。请通过聊天 /read start 或自然对话开始阅读。</td></tr>`;
    return;
  }
  tbody.innerHTML = items.map(s => `
    <tr>
      <td><code>${escapeHtml(s.session_id)}</code></td>
      <td>${escapeHtml(s.title)}</td>
      <td>${s.current_chunk_index}/${s.total_chunks}</td>
      <td>${s.paused ? "⏸ 已暂停" : "\u{1f4d6} 阅读中"}</td>
      <td>${formatTime(s.last_read_at)}</td>
      <td>${formatTime(s.next_read_at)}</td>
    </tr>
  `).join("");
}

// ======================================================================
// 笔记列表（只读）
// ======================================================================

let _notesPage = 1;

async function loadNotes(page = 1) {
  _notesPage = page;
  const bookId = document.getElementById("notes-book-filter").value;
  const keyword = document.getElementById("notes-keyword").value.trim();

  try {
    const data = await apiGet("notes", { book_id: bookId, keyword, page, page_size: 20 });
    renderNotesTable(data.items);
    renderNotesPagination(data);
  } catch (err) {
    showError("加载笔记失败: " + err.message);
  }
}

function renderNotesTable(items) {
  const tbody = document.getElementById("notes-tbody");
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty">暂无阅读笔记。</td></tr>`;
    return;
  }
  tbody.innerHTML = items.map(n => `
    <tr>
      <td>${formatTime(n.created_at)}</td>
      <td>${escapeHtml(n.book_title)}</td>
      <td>${escapeHtml(n.chapter_title || n.chapter)}</td>
      <td>${n.chunk_index}</td>
      <td><span class="badge" style="font-size:10px">${escapeHtml(n.record_type || 'chunk_note')}</span></td>
      <td>${escapeHtml(n.model_role || n.provider_display_name || '-')}</td>
      <td>${(n.importance_score || 0).toFixed(2)}${n.needs_deeper_review ? ' *' : ''}</td>
      <td class="wrap">${escapeHtml(truncate(n.summary || '', 50))}</td>
      <td>
        <button class="btn btn-small btn-secondary" data-action="note-detail" data-book-id="${escapeHtml(n.book_id)}" data-note-id="${escapeHtml(n.note_id || n.record_id || '')}">查看</button>
      </td>
    </tr>
  `).join("");
}

function renderNotesPagination(data) {
  const el = document.getElementById("notes-pagination");
  el.innerHTML = `
    共 ${data.total} 条，第 ${data.page} / ${Math.ceil(data.total / data.page_size) || 1} 页
    <button class="btn btn-small" ${data.page <= 1 ? "disabled" : ""} data-action="notes-prev">上一页</button>
    <button class="btn btn-small" ${data.page * data.page_size >= data.total ? "disabled" : ""} data-action="notes-next">下一页</button>
  `;
}

async function loadNoteDetail(bookId, noteId) {
  try {
    const data = await apiGet(`notes/${bookId}/${noteId}`);
    openPanel("note-detail-panel", () => renderNoteDetail(data));
  } catch (err) {
    showError("加载笔记详情失败: " + err.message);
  }
}

function renderNoteDetail(data) {
  const body = document.getElementById("note-detail-body");
  const mu = data.model_usage || {};
  body.innerHTML = `
    <dl>
      <dt>record_id</dt><dd><code>${escapeHtml(data.record_id || data.note_id)}</code></dd>
      <dt>类型</dt><dd>${escapeHtml(data.record_type || 'chunk_note')}</dd>
      <dt>书名</dt><dd>${escapeHtml(data.book_title)}</dd>
      <dt>段索引</dt><dd>${data.chunk_index} / ${data.chunk_total || '?'}</dd>
      <dt>章节</dt><dd>${escapeHtml(data.chapter_title || data.chapter)}</dd>
      <dt>重要性</dt><dd>${(data.importance_score || 0).toFixed(2)} ${data.needs_deeper_review ? '(需复核)' : ''}</dd>
      <dt>模型角色</dt><dd>${escapeHtml(mu.model_role || data.model_role || '-')}</dd>
      <dt>Provider</dt><dd>${escapeHtml(mu.provider_display_name || data.provider_display_name || '-')}</dd>
      <dt>创建时间</dt><dd>${formatTime(data.created_at)}</dd>
      <dt>标签</dt><dd>${(data.tags || []).join(', ') || '-'}</dd>
    </dl>
    <div class="field-block">
      <h4>摘要</h4>
      <p>${escapeHtml(data.summary)}</p>
    </div>
    <div class="field-block">
      <h4>细节</h4>
      <p>${escapeHtml(data.detail || '（未记录）')}</p>
    </div>
    <div class="field-block">
      <h4>感想</h4>
      <p>${escapeHtml(data.reflection || '（未记录）')}</p>
    </div>
    <div class="field-block">
      <h4>长期记忆</h4>
      <p>${escapeHtml(data.memory_note || '（未记录）')}</p>
    </div>
    <div class="field-block">
      <h4>分享文案</h4>
      <p>${escapeHtml(data.share_message || '（未设置）')}</p>
    </div>
    <div class="field-block">
      <h4>待解问题</h4>
      <p>${(data.open_questions || []).map(q => escapeHtml(q)).join('<br>') || '（无）'}</p>
    </div>
  `;
}

function updateNotesBookFilter(bookItems) {
  const select = document.getElementById("notes-book-filter");
  const current = select.value;
  const existingValues = new Set(
    Array.from(select.options).map(o => o.value)
  );
  for (const b of (bookItems || [])) {
    if (!existingValues.has(b.book_id)) {
      const opt = document.createElement("option");
      opt.value = b.book_id;
      opt.textContent = b.title;
      select.appendChild(opt);
    }
  }
  if (current) select.value = current;
}

// ======================================================================
// 设置
// ======================================================================

async function loadSettings() {
  try {
    const data = await apiGet("settings");
    renderSettings(data.settings);
  } catch (err) {
    showError("加载设置失败: " + err.message);
  }
}

function renderSettings(settings) {
  const form = document.getElementById("settings-form");

  for (const [key, value] of Object.entries(settings)) {
    const el = form.querySelector(`[name="${key}"]`);
    if (!el) continue;

    if (el.type === "checkbox") {
      el.checked = !!value;
    } else if (el.tagName === "SELECT" || el.tagName === "TEXTAREA" || el.tagName === "INPUT") {
      el.value = value ?? "";
    }
  }

  // 更新模型策略组显示
  updateModelStrategyUI(settings.reading_model_strategy || "two_stage");
}

function updateModelStrategyUI(strategy) {
  const twoStage = document.getElementById("setting-two-stage-group");
  const single = document.getElementById("setting-single-group");
  if (twoStage) twoStage.hidden = strategy !== "two_stage";
  if (single) single.hidden = strategy !== "fixed_single";
}

function updateModelModeUI(mode) {
  // 旧函数，保留兼容
  updateModelStrategyUI(mode);
}

async function saveSettings() {
  const form = document.getElementById("settings-form");
  const formData = new FormData(form);
  const patch = {};

  for (const [key, value] of formData.entries()) {
    // 区分 checkbox 和普通字段
    const el = form.querySelector(`[name="${key}"]`);
    if (el && el.type === "checkbox") {
      patch[key] = el.checked;
    } else if (el && el.type === "number") {
      patch[key] = value === "" ? null : Number(value);
    } else {
      patch[key] = value;
    }
  }

  // 移除 null 值（未填写的数字字段）
  for (const k of Object.keys(patch)) {
    if (patch[k] === null) delete patch[k];
  }

  const btn = document.getElementById("btn-save-settings");
  btn.disabled = true;
  btn.textContent = "保存中…";

  try {
    const result = await apiPost("settings", { settings: patch });
    showMessage(result.message || "设置已保存");
  } catch (err) {
    showError("保存设置失败: " + (err.message || "未知错误"));
  } finally {
    btn.disabled = false;
    btn.textContent = "保存设置";
  }
}

function updateModelModeUI(mode) {
  const group = document.getElementById("setting-fixed-provider-group");
  if (group) {
    group.hidden = mode !== "fixed_provider";
  }
}

// ======================================================================
// Providers
// ======================================================================

async function loadProviders() {
  try {
    const data = await apiGet("providers");
    renderProviderOptions(data);
  } catch (err) {
    console.error("[AutoRead] loadProviders error:", err);
    document.getElementById("provider-list").innerHTML =
      `<p class="hint">无法获取模型列表: ${escapeHtml(err.message)}</p>`;
  }
}

function renderProviderOptions(data) {
  const list = document.getElementById("provider-list");
  const hint = document.getElementById("provider-hint");

  if (!data.items || !data.items.length) {
    list.innerHTML = "";
    hint.hidden = false;
    if (data.message) hint.textContent = data.message;
    return;
  }

  hint.hidden = true;
  list.innerHTML = data.items.map(p => `
    <button type="button" class="provider-item"
      data-provider-id="${escapeHtml(p.provider_id)}"
      data-provider-name="${escapeHtml(p.display_name)}"
      title="${escapeHtml(p.type || 'chat')}">${escapeHtml(p.display_name)}</button>
  `).join("");

  // 点击 provider 项自动填入 cheap provider
  list.querySelectorAll(".provider-item").forEach(item => {
    item.addEventListener("click", () => {
      const pid = item.dataset.providerId;
      const name = item.dataset.providerName;
      // 填入 cheap 和 quality 两个字段（用户可后续分别修改）
      const cheapEl = document.getElementById("setting-cheap-provider-id");
      const qualityEl = document.getElementById("setting-quality-provider-id");
      if (cheapEl && !cheapEl.value) cheapEl.value = pid;
      if (qualityEl && !qualityEl.value) qualityEl.value = pid;
      document.querySelector('[name="cheap_provider_display_name"]').value = name;
      document.querySelector('[name="quality_provider_display_name"]').value = name;

      // 高亮选中
      list.querySelectorAll(".provider-item").forEach(el => el.classList.remove("selected"));
      item.classList.add("selected");
    });
  });
}

// ======================================================================
// 刷新全部
// ======================================================================

async function refreshAll() {
  await Promise.all([
    loadOverview(),
    loadBooks(),
    loadSessions(),
    loadNotes(),
  ]);
  try {
    const data = await apiGet("books", { page: 1, page_size: 100 });
    updateNotesBookFilter(data.items);
  } catch { /* ignore */ }
}

// ======================================================================
// 事件委托
// ======================================================================

document.addEventListener("click", async (e) => {
  const target = e.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;

  switch (action) {
    case "book-detail":
      await loadBookDetail(target.dataset.bookId);
      break;
    case "note-detail":
      await loadNoteDetail(target.dataset.bookId, target.dataset.noteId);
      break;
    case "books-prev":
      await loadBooks(_booksPage - 1);
      break;
    case "books-next":
      await loadBooks(_booksPage + 1);
      break;
    case "notes-prev":
      await loadNotes(_notesPage - 1);
      break;
    case "notes-next":
      await loadNotes(_notesPage + 1);
      break;
  }
});

// ======================================================================
// 按钮事件
// ======================================================================

document.getElementById("btn-refresh").addEventListener("click", refreshAll);
document.getElementById("btn-upload").addEventListener("click", uploadBook);
document.getElementById("btn-books-search").addEventListener("click", () => loadBooks(1));
document.getElementById("btn-notes-search").addEventListener("click", () => loadNotes(1));
document.getElementById("btn-close-book-detail").addEventListener("click", closePanel);
document.getElementById("btn-close-note-detail").addEventListener("click", closePanel);

// 搜索框回车
document.getElementById("books-search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") loadBooks(1);
});
document.getElementById("notes-keyword").addEventListener("keydown", (e) => {
  if (e.key === "Enter") loadNotes(1);
});
document.getElementById("notes-book-filter").addEventListener("change", () => loadNotes(1));

// Tab 切换
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => showTab(btn.dataset.tab));
});

// 设置页：模型策略切换
document.getElementById("setting-model-strategy").addEventListener("change", (e) => {
  updateModelStrategyUI(e.target.value);
});

// 设置页：刷新 providers
document.getElementById("btn-refresh-providers").addEventListener("click", loadProviders);

// 设置页：保存
document.getElementById("settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await saveSettings();
});

// 设置页：重新加载
document.getElementById("btn-reload-settings").addEventListener("click", loadSettings);

// 点击面板外部关闭
document.addEventListener("click", (e) => {
  if (_currentPanel) {
    const panel = document.getElementById(_currentPanel);
    if (panel && !panel.contains(e.target) && e.target !== panel) {
      if (!e.target.closest("[data-action]")) {
        closePanel();
      }
    }
  }
});

// 键盘 Escape 关闭面板
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closePanel();
});

// ======================================================================
// 启动
// ======================================================================

await bridge.ready();
await refreshAll();

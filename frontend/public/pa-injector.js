/**
 * 商品订单阶段回复注入脚本。
 * 在商品管理表格中插入「拍下回复」「付款回复」两列。
 */
(function () {
  "use strict";

  const API = "/api/v1";
  const STAGES = [
    { key: "purchase", label: "拍下回复", color: "#8b5cf6" },
    { key: "paid", label: "付款回复", color: "#0ea5e9" },
  ];
  const PARAMS = [
    "stage",
    "account_id",
    "account_name",
    "item_id",
    "item_title",
    "item_price",
    "order_no",
    "order_status",
    "buyer_id",
    "buyer_name",
    "chat_id",
    "amount",
    "quantity",
    "spec_name",
    "spec_value",
    "placed_at",
    "timestamp",
  ];

  let modalState = {
    accountId: "",
    itemId: "",
    itemTitle: "",
    stage: "purchase",
    replyType: "text",
  };
  let rowCache = new WeakMap();
  let statusCacheKey = "";
  let statusCache = {};
  let priceStatusCacheKey = "";
  let priceStatusCache = {};
  let scanTimer = null;
  let lastAccountId = "";

  const token = () => localStorage.getItem("auth_token") || "";
  const jsonHeaders = () => ({
    Authorization: "Bearer " + token(),
    "Content-Type": "application/json",
  });
  const authHeaders = () => ({ Authorization: "Bearer " + token() });

  function toast(message, type) {
    const el = document.createElement("div");
    el.textContent = message;
    el.style.cssText =
      "position:fixed;top:20px;right:20px;z-index:99999;padding:10px 16px;border-radius:8px;" +
      "font-size:13px;font-weight:600;box-shadow:0 8px 28px rgba(15,23,42,.24);" +
      "font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,sans-serif;" +
      (type === "success"
        ? "background:#065f46;color:#bbf7d0;"
        : "background:#7f1d1d;color:#fecaca;");
    document.body.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transition = "opacity .22s";
      setTimeout(() => el.remove(), 240);
    }, 2400);
  }

  async function apiGet(path) {
    const res = await fetch(path, { headers: authHeaders() });
    if (!res.ok) {
      let detail = "";
      try {
        detail = (await res.json()).detail || "";
      } catch (_) {}
      throw new Error(detail || "请求失败");
    }
    return res.json();
  }

  async function apiSend(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: jsonHeaders(),
      body: body == null ? undefined : JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.success === false) {
      throw new Error(data.message || data.detail || "保存失败");
    }
    return data;
  }

  function cssEscape(value) {
    if (window.CSS && CSS.escape) return CSS.escape(value);
    return String(value).replace(/["\\]/g, "\\$&");
  }

  function getAccountId() {
    const selectors = [
      "select",
      "[data-account-id]",
      "[name='account_id']",
      "[name='cookie_id']",
    ];
    for (const selector of selectors) {
      const el = document.querySelector(selector);
      if (!el) continue;
      const value = el.value || el.getAttribute("data-account-id") || "";
      if (["10", "20", "50", "100"].includes(value)) continue;
      if (value && /^\w[\w:-]{2,}$/.test(value)) return value;
    }
    return "";
  }

  function findProductTable() {
    const tables = Array.from(document.querySelectorAll("table"));
    return tables.find((table) => {
      const text = table.textContent || "";
      return text.includes("默认回复") && text.includes("商品");
    });
  }

  function headerIndex(table, label) {
    return Array.from(table.querySelectorAll("thead th")).findIndex((th) =>
      (th.textContent || "").includes(label),
    );
  }

  function textAt(cells, index) {
    if (index < 0 || index >= cells.length) return "";
    return (cells[index].textContent || "").trim();
  }

  function extractItemId(row, cells, itemIdx) {
    const itemCell = itemIdx >= 0 ? cells[itemIdx] : null;
    const link = itemCell
      ? itemCell.querySelector('a[href*="item?id="], a[href*="itemId="]')
      : row.querySelector('a[href*="item?id="], a[href*="itemId="]');
    if (link) {
      const href = link.getAttribute("href") || "";
      const match = href.match(/[?&](?:item|itemId|id)=([0-9]{8,})/i) || href.match(/\b\d{8,}\b/);
      if (match) return match[1] || match[0];
    }
    const text = textAt(cells, itemIdx);
    const match = text.match(/\b\d{8,}\b/);
    return match ? match[0] : "";
  }

  function extractRowData(table, row) {
    const cells = Array.from(row.querySelectorAll("td"));
    const accountIdx = headerIndex(table, "账号ID");
    const itemIdx = headerIndex(table, "商品ID");
    const titleIdx = headerIndex(table, "商品标题");
    const itemId = extractItemId(row, cells, itemIdx);
    let accountId = textAt(cells, accountIdx).match(/\b[\w:-]{6,}\b/)?.[0] || "";
    let itemTitle = textAt(cells, titleIdx).replace(/\s+/g, " ").slice(0, 80);

    if (!accountId && lastAccountId) accountId = lastAccountId;
    if (!accountId) accountId = getAccountId();
    if (accountId) lastAccountId = accountId;

    if (!itemTitle) for (const cell of cells) {
      const text = (cell.textContent || "").trim().replace(/\s+/g, " ");
      if (!text || text === itemId) continue;
      if (accountId && text === accountId) continue;
      if (/^\d+$/.test(text)) continue;
      if (
        [
          "已擦亮",
          "未擦亮",
          "已配置",
          "未配置",
          "已关闭",
          "已开启",
          "编辑删除",
        ].some((skip) => text.includes(skip))
      ) {
        continue;
      }
      itemTitle = text.slice(0, 80);
      break;
    }

    return { accountId, itemId, itemTitle };
  }

  function badge(stage, status, rowData) {
    const meta = STAGES.find((item) => item.key === stage);
    const state = status || {};
    const configured = !!state.has_config;
    const enabled = !!state.enabled;
    const label = configured ? (enabled ? "已配置" : "已关闭") : "未配置";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.stageReplyButton = stage;
    btn.dataset.accountId = rowData.accountId || "";
    btn.dataset.itemId = rowData.itemId || "";
    btn.dataset.itemTitle = rowData.itemTitle || "";
    btn.title = meta.label;
    btn.textContent = label;
    const color = meta.color;
    if (configured && enabled) {
      btn.style.cssText =
        `background:${color}1f;color:${color};border:0;` +
        "padding:4px 8px;border-radius:4px;font-size:12px;font-weight:600;cursor:pointer;";
    } else if (configured) {
      btn.style.cssText =
        "background:#fef3c7;color:#b45309;border:0;padding:4px 8px;border-radius:4px;" +
        "font-size:12px;font-weight:600;cursor:pointer;";
    } else {
      btn.style.cssText =
        "background:#f1f5f9;color:#64748b;border:0;padding:4px 8px;border-radius:4px;" +
        "font-size:12px;font-weight:600;cursor:pointer;";
    }
    const handleClick = (event) => {
      event.stopPropagation();
      openModal(rowData.accountId, rowData.itemId, rowData.itemTitle, stage);
    };
    btn.onclick = handleClick;
    btn.addEventListener("click", handleClick);
    return btn;
  }

  function priceBadge(status, rowData) {
    const state = status || {};
    const configured = !!state.has_config;
    const enabled = !!state.enabled;
    const label = configured ? (enabled ? "已启用" : "已关闭") : "未配置";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.priceAdjustButton = "1";
    btn.dataset.accountId = rowData.accountId || "";
    btn.dataset.itemId = rowData.itemId || "";
    btn.dataset.itemTitle = rowData.itemTitle || "";
    btn.title = "拍下改价";
    btn.textContent = label;
    if (configured && enabled) {
      btn.style.cssText =
        "background:#dcfce7;color:#15803d;border:0;padding:4px 8px;border-radius:4px;" +
        "font-size:12px;font-weight:600;cursor:pointer;";
    } else if (configured) {
      btn.style.cssText =
        "background:#fef3c7;color:#b45309;border:0;padding:4px 8px;border-radius:4px;" +
        "font-size:12px;font-weight:600;cursor:pointer;";
    } else {
      btn.style.cssText =
        "background:#f1f5f9;color:#64748b;border:0;padding:4px 8px;border-radius:4px;" +
        "font-size:12px;font-weight:600;cursor:pointer;";
    }
    const handleClick = (event) => {
      event.stopPropagation();
      openPriceModal(rowData.accountId, rowData.itemId, rowData.itemTitle);
    };
    btn.onclick = handleClick;
    btn.addEventListener("click", handleClick);
    return btn;
  }

  async function loadStatuses(accountId, itemIds) {
    if (!accountId || itemIds.length === 0) return {};
    const key = `${accountId}:${itemIds.slice().sort().join(",")}`;
    if (statusCacheKey === key) return statusCache;
    const data = await apiGet(
      `${API}/purchase-actions/${encodeURIComponent(accountId)}/statuses?item_ids=${encodeURIComponent(
        itemIds.join(","),
      )}`,
    );
    statusCacheKey = key;
    statusCache = data.data || {};
    return statusCache;
  }

  async function loadPriceStatuses(accountId, itemIds) {
    if (!accountId || itemIds.length === 0) return {};
    const key = `${accountId}:${itemIds.slice().sort().join(",")}`;
    if (priceStatusCacheKey === key) return priceStatusCache;
    const data = await apiGet(
      `${API}/order-price-adjustments/${encodeURIComponent(accountId)}/statuses?item_ids=${encodeURIComponent(
        itemIds.join(","),
      )}`,
    );
    priceStatusCacheKey = key;
    priceStatusCache = data.data || {};
    return priceStatusCache;
  }

  function setRowCell(row, insertIdx, stage, rowData, status) {
    const existing = row.querySelector(`td[data-stage-reply="${stage}"]`);
    const cell = existing || document.createElement("td");
    cell.dataset.stageReply = stage;
    cell.style.cssText = "white-space:nowrap;text-align:center;";
    cell.innerHTML = "";
    cell.appendChild(badge(stage, status, rowData));
    if (!existing) {
      const cells = row.querySelectorAll("td");
      const anchor = stage === "purchase" ? cells[insertIdx] : row.querySelector('td[data-stage-reply="purchase"]');
      if (stage === "purchase" && anchor) anchor.before(cell);
      else if (anchor) anchor.after(cell);
    }
  }

  function setPriceCell(row, rowData, status) {
    const existing = row.querySelector('td[data-price-adjust="1"]');
    const cell = existing || document.createElement("td");
    cell.dataset.priceAdjust = "1";
    cell.style.cssText = "white-space:nowrap;text-align:center;";
    cell.innerHTML = "";
    cell.appendChild(priceBadge(status, rowData));
    if (!existing) {
      const anchor = row.querySelector('td[data-stage-reply="paid"]') || row.querySelector('td[data-stage-reply="purchase"]');
      if (anchor) anchor.after(cell);
    }
  }

  async function injectTable(table) {
    const drIdx = headerIndex(table, "默认回复");
    if (drIdx < 0) return false;
    const insertIdx = Math.max(0, headerIndex(table, "是否擦亮"));

    const headers = table.querySelectorAll("thead th");
    const insertHeader = headers[insertIdx] || headers[drIdx];
    for (const stage of STAGES) {
      if (!table.querySelector(`thead th[data-stage-reply="${stage.key}"]`)) {
        const th = document.createElement("th");
        th.dataset.stageReply = stage.key;
        th.className = insertHeader.className;
        th.textContent = stage.label;
        insertHeader.before(th);
      }
    }
    if (!table.querySelector('thead th[data-price-adjust="1"]')) {
      const th = document.createElement("th");
      th.dataset.priceAdjust = "1";
      th.className = insertHeader.className;
      th.textContent = "拍下改价";
      insertHeader.before(th);
    }

    const rows = Array.from(table.querySelectorAll("tbody tr")).filter((row) => row.querySelectorAll("td").length > 1);
    const rowDataList = rows.map((row) => {
      const cached = rowCache.get(row);
      if (cached) return cached;
      const data = extractRowData(table, row);
      rowCache.set(row, data);
      return data;
    });
    const accountId = rowDataList.find((data) => data.accountId)?.accountId || getAccountId();
    const itemIds = rowDataList.map((data) => data.itemId).filter(Boolean);
    let statuses = {};
    try {
      statuses = await loadStatuses(accountId, itemIds);
    } catch (error) {
      console.warn("[stage-reply] load statuses failed", error);
    }
    let priceStatuses = {};
    try {
      priceStatuses = await loadPriceStatuses(accountId, itemIds);
    } catch (error) {
      console.warn("[price-adjust] load statuses failed", error);
    }

    rows.forEach((row, index) => {
      const data = rowDataList[index];
      if (!data.itemId || !data.accountId) return;
      const status = statuses[data.itemId] || {};
      setRowCell(row, insertIdx, "purchase", data, status.purchase);
      setRowCell(row, insertIdx, "paid", data, status.paid);
      setPriceCell(row, data, priceStatuses[data.itemId]);
    });

    const empty = table.querySelector("tbody td[colspan]");
    if (empty && !empty.dataset.stageReplyColspan) {
      empty.dataset.stageReplyColspan = "1";
      empty.setAttribute("colspan", String((Number(empty.getAttribute("colspan")) || headers.length) + 3));
    }
    return true;
  }

  function buildModal() {
    if (document.getElementById("stage-reply-modal")) return;
    const modal = document.createElement("div");
    modal.id = "stage-reply-modal";
    modal.style.cssText =
      "display:none;position:fixed;inset:0;z-index:1000;align-items:center;justify-content:center;" +
      "font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,sans-serif;";
    modal.innerHTML = `
      <div data-close="1" style="position:absolute;inset:0;background:rgba(15,23,42,.55)"></div>
      <div style="position:relative;width:520px;max-width:95vw;max-height:88vh;overflow-y:auto;background:#fff;color:#0f172a;border-radius:12px;box-shadow:0 24px 72px rgba(15,23,42,.28);border:1px solid #e2e8f0;">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:18px 20px;border-bottom:1px solid #e2e8f0;">
          <div style="min-width:0">
            <h2 id="sr-title" style="margin:0;font-size:16px;font-weight:700;">阶段回复</h2>
            <p id="sr-subtitle" style="margin:4px 0 0;color:#64748b;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:390px;"></p>
          </div>
          <button id="sr-close" type="button" style="border:0;background:transparent;color:#64748b;cursor:pointer;font-size:22px;line-height:1;">×</button>
        </div>
        <div style="padding:18px 20px;display:flex;flex-direction:column;gap:14px;">
          <label style="display:flex;align-items:center;gap:8px;font-size:14px;font-weight:600;">
            <input id="sr-enabled" type="checkbox" style="width:16px;height:16px;">
            <span id="sr-enabled-label">启用阶段回复</span>
          </label>
          <div>
            <label style="display:block;font-size:13px;font-weight:600;color:#475569;margin-bottom:7px;">回复类型</label>
            <div style="display:flex;gap:8px;">
              <button class="sr-type" data-type="text" type="button">默认回复</button>
              <button class="sr-type" data-type="api" type="button">API接口</button>
            </div>
          </div>
          <div id="sr-text-pane">
            <label style="display:block;font-size:13px;font-weight:600;color:#475569;margin-bottom:7px;">回复内容</label>
            <textarea id="sr-content" rows="5" style="width:100%;box-sizing:border-box;border:1px solid #cbd5e1;border-radius:8px;padding:9px 11px;font-size:13px;line-height:1.55;resize:vertical;" placeholder="感谢拍下，订单号：{order_no}"></textarea>
          </div>
          <div>
            <label style="display:block;font-size:13px;font-weight:600;color:#475569;margin-bottom:7px;">回复图片</label>
            <div style="display:flex;gap:8px;align-items:center;">
              <input id="sr-image" type="text" style="flex:1;min-width:0;border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px;font-size:13px;" placeholder="/static/uploads/stage_reply/...">
              <label style="padding:8px 12px;border-radius:8px;border:1px solid #cbd5e1;font-size:13px;cursor:pointer;background:#f8fafc;">
                上传
                <input id="sr-upload" type="file" accept="image/*" style="display:none;">
              </label>
            </div>
          </div>
          <div id="sr-api-pane" style="display:none;">
            <label style="display:block;font-size:13px;font-weight:600;color:#475569;margin-bottom:7px;">API地址</label>
            <input id="sr-api-url" type="text" style="width:100%;box-sizing:border-box;border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px;font-size:13px;" placeholder="https://example.com/api/reply">
            <label style="display:block;font-size:13px;font-weight:600;color:#475569;margin:12px 0 7px;">超时时间（秒）</label>
            <input id="sr-api-timeout" type="number" min="1" max="120" value="80" style="width:120px;border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px;font-size:13px;">
          </div>
          <div>
            <div style="display:flex;flex-wrap:wrap;gap:6px;" id="sr-param-list"></div>
          </div>
        </div>
        <div style="display:flex;justify-content:space-between;gap:8px;padding:14px 20px;border-top:1px solid #e2e8f0;">
          <button id="sr-delete" type="button" style="padding:8px 14px;border-radius:8px;border:1px solid #fecaca;background:#fff;color:#dc2626;font-weight:600;cursor:pointer;">删除配置</button>
          <div style="display:flex;gap:8px;">
            <button id="sr-cancel" type="button" style="padding:8px 16px;border-radius:8px;border:1px solid #cbd5e1;background:#fff;color:#475569;font-weight:600;cursor:pointer;">取消</button>
            <button id="sr-save" type="button" style="padding:8px 16px;border-radius:8px;border:0;background:#2563eb;color:#fff;font-weight:700;cursor:pointer;">保存</button>
          </div>
        </div>
      </div>
    `;
    const style = document.createElement("style");
    style.textContent = `
      #stage-reply-modal .sr-type{flex:1;border:1px solid #cbd5e1;background:#fff;color:#475569;padding:8px 10px;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer}
      #stage-reply-modal .sr-type.active{background:#2563eb;border-color:#2563eb;color:white}
      #stage-reply-modal .sr-param{border:0;border-radius:6px;padding:3px 7px;background:#eef2ff;color:#3730a3;font-size:11px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;cursor:pointer}
      #stage-reply-modal input:focus,#stage-reply-modal textarea:focus{outline:none;border-color:#2563eb;box-shadow:0 0 0 3px rgba(37,99,235,.12)}
    `;
    modal.appendChild(style);
    document.body.appendChild(modal);

    modal.addEventListener("click", (event) => {
      if (event.target && event.target.getAttribute("data-close")) closeModal();
    });
    document.getElementById("sr-close").addEventListener("click", closeModal);
    document.getElementById("sr-cancel").addEventListener("click", closeModal);
    document.getElementById("sr-save").addEventListener("click", saveModal);
    document.getElementById("sr-delete").addEventListener("click", deleteConfig);
    document.getElementById("sr-upload").addEventListener("change", uploadImage);
    modal.querySelectorAll(".sr-type").forEach((button) => {
      button.addEventListener("click", () => setReplyType(button.dataset.type));
    });

    const params = document.getElementById("sr-param-list");
    PARAMS.forEach((name) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "sr-param";
      button.textContent = `{${name}}`;
      button.addEventListener("click", () => insertParam(`{${name}}`));
      params.appendChild(button);
    });
  }

  function setReplyType(type) {
    modalState.replyType = type === "api" ? "api" : "text";
    document.querySelectorAll("#stage-reply-modal .sr-type").forEach((button) => {
      button.classList.toggle("active", button.dataset.type === modalState.replyType);
    });
    document.getElementById("sr-text-pane").style.display = "";
    document.getElementById("sr-api-pane").style.display = modalState.replyType === "api" ? "" : "none";
  }

  function insertParam(value) {
    const target = document.getElementById("sr-content");
    const start = target.selectionStart || 0;
    const end = target.selectionEnd || start;
    target.value = target.value.slice(0, start) + value + target.value.slice(end);
    target.focus();
    target.selectionStart = target.selectionEnd = start + value.length;
  }

  function closeModal() {
    const modal = document.getElementById("stage-reply-modal");
    if (modal) modal.style.display = "none";
  }

  function bindDelegatedClicks() {
    if (document.documentElement.dataset.stageReplyDelegated === "1") return;
    document.documentElement.dataset.stageReplyDelegated = "1";
    document.addEventListener("click", (event) => {
      const button = event.target && event.target.closest
        ? event.target.closest("button[data-stage-reply-button]")
        : null;
      if (!button) return;
      event.preventDefault();
      event.stopPropagation();
      openModal(
        button.dataset.accountId || "",
        button.dataset.itemId || "",
        button.dataset.itemTitle || "",
        button.dataset.stageReplyButton || "purchase",
      );
    }, true);
    document.addEventListener("click", (event) => {
      const button = event.target && event.target.closest
        ? event.target.closest("button[data-price-adjust-button]")
        : null;
      if (!button) return;
      event.preventDefault();
      event.stopPropagation();
      openPriceModal(
        button.dataset.accountId || "",
        button.dataset.itemId || "",
        button.dataset.itemTitle || "",
      );
    }, true);
  }

  async function openModal(accountId, itemId, itemTitle, stage) {
    if (!accountId || !itemId) {
      toast("未识别到账号或商品ID", "error");
      return;
    }
    buildModal();
    modalState = { accountId, itemId, itemTitle, stage, replyType: "text" };
    const meta = STAGES.find((item) => item.key === stage);
    document.getElementById("sr-title").textContent = meta.label;
    document.getElementById("sr-subtitle").textContent = itemTitle || itemId;
    document.getElementById("sr-enabled-label").textContent = `启用${meta.label}`;
    document.getElementById("sr-enabled").checked = false;
    document.getElementById("sr-content").value = "";
    document.getElementById("sr-image").value = "";
    document.getElementById("sr-api-url").value = "";
    document.getElementById("sr-api-timeout").value = "80";
    setReplyType("text");

    try {
      const data = await apiGet(
        `${API}/purchase-actions/${encodeURIComponent(accountId)}/${encodeURIComponent(itemId)}?stage=${stage}`,
      );
      document.getElementById("sr-enabled").checked = !!data.enabled;
      document.getElementById("sr-content").value = data.reply_content || "";
      document.getElementById("sr-image").value = data.reply_image || "";
      document.getElementById("sr-api-url").value = data.api_url || "";
      document.getElementById("sr-api-timeout").value = data.api_timeout || 80;
      setReplyType(data.reply_type || "text");
    } catch (error) {
      toast(error.message || "加载配置失败", "error");
    }
    document.getElementById("stage-reply-modal").style.display = "flex";
  }

  async function saveModal() {
    const body = {
      enabled: document.getElementById("sr-enabled").checked,
      reply_type: modalState.replyType,
      reply_content: document.getElementById("sr-content").value,
      reply_image: document.getElementById("sr-image").value,
      api_url: document.getElementById("sr-api-url").value,
      api_timeout: Number(document.getElementById("sr-api-timeout").value) || 80,
    };
    try {
      await apiSend(
        "PUT",
        `${API}/purchase-actions/${encodeURIComponent(modalState.accountId)}/${encodeURIComponent(
          modalState.itemId,
        )}?stage=${modalState.stage}`,
        body,
      );
      toast("配置已保存", "success");
      statusCacheKey = "";
      closeModal();
      setTimeout(scanAll, 300);
    } catch (error) {
      toast(error.message || "保存失败", "error");
    }
  }

  async function deleteConfig() {
    try {
      await apiSend(
        "DELETE",
        `${API}/purchase-actions/${encodeURIComponent(modalState.accountId)}/${encodeURIComponent(
          modalState.itemId,
        )}?stage=${modalState.stage}`,
      );
      toast("配置已删除", "success");
      statusCacheKey = "";
      closeModal();
      setTimeout(scanAll, 300);
    } catch (error) {
      toast(error.message || "删除失败", "error");
    }
  }

  async function uploadImage(event) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const form = new FormData();
    form.append("image", file);
    try {
      const res = await fetch(
        `${API}/purchase-actions/${encodeURIComponent(modalState.accountId)}/${encodeURIComponent(
          modalState.itemId,
        )}/upload-image?stage=${modalState.stage}`,
        {
          method: "POST",
          headers: authHeaders(),
          body: form,
        },
      );
      const data = await res.json();
      if (!res.ok || data.success === false) throw new Error(data.message || data.detail || "上传失败");
      document.getElementById("sr-image").value = data.image_url || "";
      toast("图片已上传", "success");
    } catch (error) {
      toast(error.message || "上传失败", "error");
    } finally {
      event.target.value = "";
    }
  }

  function buildPriceModal() {
    if (document.getElementById("price-adjust-modal")) return;
    const modal = document.createElement("div");
    modal.id = "price-adjust-modal";
    modal.style.cssText =
      "display:none;position:fixed;inset:0;z-index:1001;align-items:center;justify-content:center;" +
      "font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,sans-serif;";
    modal.innerHTML = `
      <div data-price-close="1" style="position:absolute;inset:0;background:rgba(15,23,42,.55)"></div>
      <div style="position:relative;width:520px;max-width:95vw;background:#fff;color:#0f172a;border-radius:12px;box-shadow:0 24px 72px rgba(15,23,42,.28);border:1px solid #e2e8f0;">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:18px 20px;border-bottom:1px solid #e2e8f0;">
          <div style="min-width:0">
            <h2 style="margin:0;font-size:16px;font-weight:700;">拍下改价</h2>
            <p id="pa-subtitle" style="margin:4px 0 0;color:#64748b;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:390px;"></p>
          </div>
          <button id="pa-close" type="button" style="border:0;background:transparent;color:#64748b;cursor:pointer;font-size:22px;line-height:1;">×</button>
        </div>
        <div style="padding:18px 20px;display:flex;flex-direction:column;gap:14px;">
          <label style="display:flex;align-items:center;gap:8px;font-size:14px;font-weight:600;">
            <input id="pa-enabled" type="checkbox" style="width:16px;height:16px;">
            <span>启用拍下自动改价</span>
          </label>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
            <div>
              <label style="display:block;font-size:13px;font-weight:600;color:#475569;margin-bottom:7px;">改后商品总价（元）</label>
              <input id="pa-item-price" type="number" min="0.01" step="0.01" style="width:100%;box-sizing:border-box;border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px;font-size:13px;" placeholder="12.34">
            </div>
            <div>
              <label style="display:block;font-size:13px;font-weight:600;color:#475569;margin-bottom:7px;">改后邮费（元）</label>
              <input id="pa-post-fee" type="number" min="0" step="0.01" style="width:100%;box-sizing:border-box;border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px;font-size:13px;" placeholder="0.00">
            </div>
          </div>
          <div>
            <label style="display:block;font-size:13px;font-weight:600;color:#475569;margin-bottom:7px;">外部 API / WS 地址</label>
            <input id="pa-override-url" type="text" style="width:100%;box-sizing:border-box;border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px;font-size:13px;" placeholder="https://example.com/price 或 wss://example.com/ws">
          </div>
          <div>
            <label style="display:block;font-size:13px;font-weight:600;color:#475569;margin-bottom:7px;">外部接口超时（秒）</label>
            <input id="pa-override-timeout" type="number" min="1" max="120" value="10" style="width:120px;border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px;font-size:13px;">
          </div>
        </div>
        <div style="display:flex;justify-content:space-between;gap:8px;padding:14px 20px;border-top:1px solid #e2e8f0;">
          <button id="pa-delete" type="button" style="padding:8px 14px;border-radius:8px;border:1px solid #fecaca;background:#fff;color:#dc2626;font-weight:600;cursor:pointer;">删除配置</button>
          <div style="display:flex;gap:8px;">
            <button id="pa-cancel" type="button" style="padding:8px 16px;border-radius:8px;border:1px solid #cbd5e1;background:#fff;color:#475569;font-weight:600;cursor:pointer;">取消</button>
            <button id="pa-save" type="button" style="padding:8px 16px;border-radius:8px;border:0;background:#16a34a;color:#fff;font-weight:700;cursor:pointer;">保存</button>
          </div>
        </div>
      </div>
    `;
    const style = document.createElement("style");
    style.textContent = `
      #price-adjust-modal input:focus{outline:none;border-color:#16a34a;box-shadow:0 0 0 3px rgba(22,163,74,.12)}
    `;
    modal.appendChild(style);
    document.body.appendChild(modal);

    modal.addEventListener("click", (event) => {
      if (event.target && event.target.getAttribute("data-price-close")) closePriceModal();
    });
    document.getElementById("pa-close").addEventListener("click", closePriceModal);
    document.getElementById("pa-cancel").addEventListener("click", closePriceModal);
    document.getElementById("pa-save").addEventListener("click", savePriceModal);
    document.getElementById("pa-delete").addEventListener("click", deletePriceConfig);
  }

  function closePriceModal() {
    const modal = document.getElementById("price-adjust-modal");
    if (modal) modal.style.display = "none";
  }

  async function openPriceModal(accountId, itemId, itemTitle) {
    if (!accountId || !itemId) {
      toast("未识别到账号或商品ID", "error");
      return;
    }
    buildPriceModal();
    modalState = { accountId, itemId, itemTitle, stage: "purchase", replyType: "text" };
    document.getElementById("pa-subtitle").textContent = itemTitle || itemId;
    document.getElementById("pa-enabled").checked = false;
    document.getElementById("pa-item-price").value = "";
    document.getElementById("pa-post-fee").value = "0.00";
    document.getElementById("pa-override-url").value = "";
    document.getElementById("pa-override-timeout").value = "10";

    try {
      const data = await apiGet(
        `${API}/order-price-adjustments/${encodeURIComponent(accountId)}/${encodeURIComponent(itemId)}`,
      );
      document.getElementById("pa-enabled").checked = !!data.enabled;
      document.getElementById("pa-item-price").value = data.target_item_price || "";
      document.getElementById("pa-post-fee").value = data.target_post_fee || "0.00";
      document.getElementById("pa-override-url").value = data.override_url || "";
      document.getElementById("pa-override-timeout").value = data.override_timeout || 10;
    } catch (error) {
      toast(error.message || "加载配置失败", "error");
    }
    document.getElementById("price-adjust-modal").style.display = "flex";
  }

  async function savePriceModal() {
    const body = {
      enabled: document.getElementById("pa-enabled").checked,
      target_item_price: document.getElementById("pa-item-price").value,
      target_post_fee: document.getElementById("pa-post-fee").value || "0.00",
      override_url: document.getElementById("pa-override-url").value,
      override_timeout: Number(document.getElementById("pa-override-timeout").value) || 10,
    };
    try {
      await apiSend(
        "PUT",
        `${API}/order-price-adjustments/${encodeURIComponent(modalState.accountId)}/${encodeURIComponent(
          modalState.itemId,
        )}`,
        body,
      );
      toast("配置已保存", "success");
      priceStatusCacheKey = "";
      closePriceModal();
      setTimeout(scanAll, 300);
    } catch (error) {
      toast(error.message || "保存失败", "error");
    }
  }

  async function deletePriceConfig() {
    try {
      await apiSend(
        "DELETE",
        `${API}/order-price-adjustments/${encodeURIComponent(modalState.accountId)}/${encodeURIComponent(
          modalState.itemId,
        )}`,
      );
      toast("配置已删除", "success");
      priceStatusCacheKey = "";
      closePriceModal();
      setTimeout(scanAll, 300);
    } catch (error) {
      toast(error.message || "删除失败", "error");
    }
  }

  async function scanAll() {
    const table = findProductTable();
    if (!table) return;
    await injectTable(table);
  }

  function start() {
    bindDelegatedClicks();
    buildModal();
    buildPriceModal();
    scanAll();
    if (scanTimer) clearInterval(scanTimer);
    scanTimer = setInterval(scanAll, 2000);
    let lastHref = location.href;
    setInterval(() => {
      if (location.href !== lastHref) {
        lastHref = location.href;
        rowCache = new WeakMap();
        statusCacheKey = "";
        setTimeout(scanAll, 500);
      }
    }, 500);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();

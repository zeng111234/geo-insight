/* 共享前端工具：新页面统一从这里取 API 与格式化，避免每个页面重复一遍。 */
(function () {
  "use strict";
  // start.bat 走 http 同源；直接双击打开 HTML 时退回本地服务地址。
  var API = location.protocol === "file:" ? "http://127.0.0.1:8123/v1" : "/v1";
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (v) {
    return String(v === null || v === undefined ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  };
  var pct = function (v) {
    return v === null || v === undefined || Number.isNaN(v) ? "—" : (v * 100).toFixed(1) + "%";
  };
  var metricText = function (m) {
    if (!m) return "—";
    if (m.status === "insufficient_samples") return "样本不足";
    return m.value === null || m.value === undefined ? "—" : pct(m.value);
  };
  var num = function (v) { return v === null || v === undefined ? "—" : String(v); };

  // CSRF 双提交:写请求带 X-CSRF-Token(geo_csrf 由登录下发,非 HttpOnly)
  function csrfToken() {
    var m = document.cookie.match(/(?:^|;\s*)geo_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  async function request(path, options) {
    if (options && options.method && options.method !== "GET") {
      options.headers = Object.assign({}, options.headers, { "X-CSRF-Token": csrfToken() });
    }
    var res = await fetch(API + path, options);
    var body = await res.json().catch(function () { return {}; });
    if (!res.ok) {
      var e = new Error(body.message || ("HTTP " + res.status));
      e.code = body.code; e.status = res.status; e.request_id = body.request_id;
      throw e;
    }
    return body;
  }
  var get = function (path) { return request(path); };
  var post = function (path, payload) {
    return request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {})
    });
  };
  var patch = function (path, payload) {
    return request(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {})
    });
  };
  // DELETE 成功时是 204 无 body，request 里的 res.json() 会失败并吞成 {}，这是预期的
  var del = function (path) { return request(path, { method: "DELETE" }); };

  var toastTimer = null;
  function toast(msg, kind) {
    var el = $("toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "toast"; el.className = "toast";
      document.body.appendChild(el);
    }
    el.className = "toast show" + (kind ? " " + kind : "");
    el.innerHTML = esc(msg);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.className = "toast"; }, 4200);
  }

  function dates() {
    return "from=" + encodeURIComponent($("from").value) + "&to=" + encodeURIComponent($("to").value);
  }
  function tracker() { return $("tracker") ? $("tracker").value : ""; }

  async function fillTrackers() {
    var sel = $("tracker");
    if (!sel) return "";
    try {
      var d = await get("/trackers");
      var items = d.items || [];
      sel.innerHTML = items.map(function (x) {
        return '<option value="' + esc(x.id) + '">' + esc(x.brand_name || x.name || x.id) + "</option>";
      }).join("");
      // URL 带 tracker 时选中它（首页下钻/机会页「写这篇」跳转靠这个，
      // 否则永远选第一个演示对象，真实试点对象上的问题会被塞错对象）。
      var wanted = new URLSearchParams(location.search).get("tracker");
      var hit = wanted && items.some(function (x) { return x.id === wanted; });
      if (hit) sel.value = wanted;
      return sel.value || (items.length ? items[0].id : "");
    } catch (e) {
      sel.innerHTML = '<option value="">无监控对象</option>';
      return "";
    }
  }

  function fail(e, targets) {
    (targets || []).forEach(function (id) {
      var el = $(id);
      if (el) el.innerHTML = '<div class="notice error">读取失败：' + esc(e.message) +
        (e.request_id ? "（request_id " + esc(e.request_id) + "）" : "") + "</div>";
    });
    toast(e.message, "error");
  }

  function empty(text) { return '<div class="empty">' + esc(text) + "</div>"; }

  // ---- 明细分页：统一 10 行/页，长表不再无限下滑 ----
  // 客户端分页用 pagerSlice（数据已全量在手）；服务端分页用 pagerInit + pagerNav（按页请求）。
  var _pager = {};
  function pagerInit(id, redraw) {
    var st = _pager[id] || (_pager[id] = { page: 1 });
    st.redraw = redraw;
    return st;
  }
  function pagerReset(id) {
    if (_pager[id]) _pager[id].page = 1;
  }
  function pagerNav(id, page, pages, total) {
    if (!total) return "";
    return '<div class="pager">' +
      '<button type="button" class="pgbtn" data-pager="' + id + '" data-dir="-1"' +
        (page <= 1 ? " disabled" : "") + ">上一页</button>" +
      '<span class="pginfo">第 ' + page + " / " + pages + " 页 · 共 " + total + " 条</span>" +
      '<button type="button" class="pgbtn" data-pager="' + id + '" data-dir="1"' +
        (page >= pages ? " disabled" : "") + ">下一页</button>" +
      // 页码直达：信源明细两百多页时，只有上下页要翻几十次才到得了。
      (pages > 3 ? '<span class="pgjump">跳至 <input type="number" min="1" max="' + pages +
        '" value="' + page + '" data-pager-go="' + id + '" aria-label="页码"> / ' + pages +
        ' <button type="button" class="pgbtn" data-pager-jump="' + id + '">跳转</button></span>' : "") +
      "</div>";
  }
  function pagerSlice(id, items, redraw, size) {
    var st = pagerInit(id, redraw);
    size = size || 10;
    var pages = Math.max(1, Math.ceil(items.length / size));
    if (st.page > pages) st.page = pages;
    if (st.page < 1) st.page = 1;
    var start = (st.page - 1) * size;
    return {
      slice: items.slice(start, start + size),
      nav: pagerNav(id, st.page, pages, items.length),
      page: st.page,
    };
  }
  document.addEventListener("click", function (e) {
    var jump = e.target && e.target.closest ? e.target.closest("[data-pager-jump]") : null;
    if (jump && !jump.disabled) {
      var jid = jump.getAttribute("data-pager-jump");
      var input = document.querySelector('[data-pager-go="' + jid + '"]');
      var st = _pager[jid];
      if (st && input) {
        var v = parseInt(input.value, 10);
        if (!isNaN(v)) { st.page = Math.max(1, Math.min(parseInt(input.max, 10) || 1, v)); st.redraw(); }
      }
      return;
    }
    var btn = e.target && e.target.closest ? e.target.closest("[data-pager]") : null;
    if (!btn || btn.disabled) return;
    var st = _pager[btn.getAttribute("data-pager")];
    if (!st || !st.redraw) return;
    st.page += Number(btn.getAttribute("data-dir") || 0);
    if (st.page < 1) st.page = 1;
    st.redraw();
  });
  // 输入框里按回车也能跳
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter") return;
    var input = e.target && e.target.getAttribute ? e.target.getAttribute("data-pager-go") : null;
    if (!input) return;
    e.preventDefault();
    var st = _pager[input];
    var v = parseInt(e.target.value, 10);
    if (st && !isNaN(v)) { st.page = Math.max(1, Math.min(parseInt(e.target.max, 10) || 1, v)); st.redraw(); }
  });

  // 模型头像徽章：所有显示模型名的地方共用，避免纯文字墙。
  // 显示名与后端 domain/channels.py 的 8 模型一致；大多数模型用 CSS 首字母+品牌色，
  // 不依赖图片（离线可用）。DeepSeek 例外：它的标志是那只跃出水面的鲸鱼，
  // 用字母 D 是错的（用户指出）；这里用内联 SVG 鲸鱼剪影，仍不依赖外部图片。
  // DeepSeek 鲸鱼：白色身体 + 上方水花，viewBox 适应 20px 圆形头像。
  var DEEPSEEK_WHALE =
    '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" focusable="false">' +
    '<path fill="#fff" d="M2.6 13.9c1.7 0 3-.5 4.2-1.6 1.2-1.1 2.5-1.7 4.2-1.7 2.6 0 4.6 1.9 5 4.4 0 .4.3.7.8.7h1.7l-1.9 2.4c-.3.4-.8.6-1.3.6h-.3c-1.6 0-2.9-.7-3.9-1.9-1.3-1.6-3.2-2.5-5.4-2.5-1.6 0-3 .5-4.2 1.5-.5.4-1.2.4-1.7-.1l-1.1-1.1c-.2-.2-.3-.5-.3-.8 0-.6.5-1.1 1.1-1.1.7 0 1.3.1 1.9.2z"/>' +
    '<path fill="#fff" d="M15.6 5.2c.3-.3.8-.3 1.1 0l1 1.1 1.2-1c.3-.3.8-.2 1 .1.2.3.2.7-.1 1l-1.1 1 1.2 1.1c.3.3.3.8 0 1.1-.3.3-.8.3-1.1 0l-1.2-1.1-1 1.1c-.3.3-.8.3-1.1 0-.3-.3-.3-.8 0-1.1l1-1.1-1-1c-.3-.3-.3-.8 0-1.1z"/>' +
    "</svg>";
  var MODEL_META = {
    doubao:   { label: "豆包",      abbr: "豆", color: "#3b6cf0" },
    deepseek: { label: "DeepSeek",  abbr: "D",  color: "#4d6bfe", svg: DEEPSEEK_WHALE },
    kimi:     { label: "Kimi",      abbr: "K",  color: "#1f2430" },
    qwen:     { label: "千问",      abbr: "千", color: "#6155d6" },
    yuanbao:  { label: "元宝",      abbr: "元", color: "#e05a4d" },
    wenxin:   { label: "文心一言",  abbr: "文", color: "#2b7a5e" },
    quark:    { label: "夸克 AI",   abbr: "夸", color: "#5a67d8" },
    bing:     { label: "Bing AI",   abbr: "B",  color: "#0f7fb8" }
  };
  var modelMeta = function (key) {
    return MODEL_META[key] || { label: key || "未知", abbr: (key || "?").slice(0, 1).toUpperCase(), color: "#6c5ce7" };
  };
  // 头像内容：有 svg（DeepSeek 鲸鱼）用 svg，否则回退转义后的首字母。
  var modelAvatarHtml = function (m) {
    return m.svg ? m.svg : esc(m.abbr);
  };
  // 返回完整 HTML 片段（已 esc），可直接塞进 <td>。
  var modelBadge = function (key) {
    var m = modelMeta(key);
    return '<span class="model-badge"><span class="model-badge-av" style="background:' +
      esc(m.color) + '">' + modelAvatarHtml(m) + "</span><span>" + esc(m.label) + "</span></span>";
  };

  window.GEO = {
    API: API, $: $, esc: esc, pct: pct, metricText: metricText, num: num,
    get: get, post: post, patch: patch, del: del, toast: toast, dates: dates, tracker: tracker,
    fillTrackers: fillTrackers, fail: fail, empty: empty,
    modelBadge: modelBadge, modelMeta: modelMeta, modelAvatarHtml: modelAvatarHtml,
    pagerInit: pagerInit, pagerReset: pagerReset, pagerNav: pagerNav, pagerSlice: pagerSlice
  };
})();

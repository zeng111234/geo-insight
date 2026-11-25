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

  async function request(path, options) {
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
      return items.length ? items[0].id : "";
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

  window.GEO = {
    API: API, $: $, esc: esc, pct: pct, metricText: metricText, num: num,
    get: get, post: post, patch: patch, del: del, toast: toast, dates: dates, tracker: tracker,
    fillTrackers: fillTrackers, fail: fail, empty: empty,
    pagerInit: pagerInit, pagerReset: pagerReset, pagerNav: pagerNav, pagerSlice: pagerSlice
  };
})();

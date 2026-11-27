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
  // 用字母 D 是错的（用户指出）；这里用 DeepSeek 官方 logo 的内联 SVG（Simple Icons 收录），仍不依赖外部图片。
  // 官方 path 单色填充改白色，配品牌蓝圆底；viewBox 0 0 24 24。
  var DEEPSEEK_WHALE =
    '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" focusable="false">' +
    '<path fill="#fff" d="M23.748 4.651c-.254-.124-.364.113-.512.233-.051.04-.094.09-.137.137-.372.397-.806.657-1.373.626-.829-.046-1.537.214-2.163.848-.133-.782-.575-1.248-1.247-1.548-.352-.155-.708-.311-.955-.65-.172-.24-.219-.509-.305-.774-.055-.16-.11-.323-.293-.35-.2-.031-.278.136-.356.276-.313.572-.434 1.202-.422 1.84.027 1.436.633 2.58 1.838 3.393.137.094.172.187.129.323-.082.28-.18.553-.266.833-.055.179-.137.218-.328.14a5.5 5.5 0 0 1-1.737-1.179c-.857-.828-1.631-1.743-2.597-2.46a12 12 0 0 0-.689-.47c-.985-.957.13-1.743.387-1.836.27-.098.094-.433-.778-.428-.872.003-1.67.295-2.687.685a3 3 0 0 1-.465.136 9.6 9.6 0 0 0-2.883-.101c-1.885.21-3.39 1.1-4.497 2.622C.082 8.776-.231 10.854.152 13.02c.403 2.284 1.568 4.175 3.36 5.653 1.857 1.533 3.997 2.284 6.438 2.14 1.482-.085 3.132-.284 4.994-1.86.47.234.962.328 1.78.398.629.058 1.235-.031 1.705-.129.735-.155.684-.836.418-.961-2.155-1.004-1.682-.595-2.112-.926 1.095-1.295 2.768-3.598 3.284-6.733.05-.346.115-.834.108-1.114-.004-.171.035-.238.23-.257a4.2 4.2 0 0 0 1.545-.475c1.397-.763 1.96-2.016 2.093-3.517.02-.23-.004-.467-.247-.588M11.58 18.168c-2.088-1.642-3.101-2.183-3.52-2.16-.39.024-.32.472-.234.763.09.288.207.487.371.74.114.167.192.416-.113.603-.673.416-1.842-.14-1.897-.168-1.361-.801-2.5-1.86-3.301-3.306-.775-1.393-1.225-2.888-1.299-4.482-.02-.385.094-.522.477-.592a4.7 4.7 0 0 1 1.53-.038c2.131.311 3.946 1.264 5.467 2.774.868.86 1.525 1.887 2.202 2.89.72 1.066 1.494 2.082 2.48 2.915.348.291.626.513.892.677-.802.09-2.14.109-3.055-.615zm1.001-6.44a.306.306 0 0 1 .415-.287.3.3 0 0 1 .113.074.3.3 0 0 1 .086.214c0 .17-.136.307-.308.307a.303.303 0 0 1-.306-.307m3.11 1.596c-.2.081-.4.151-.591.16a1.25 1.25 0 0 1-.798-.254c-.274-.23-.47-.358-.551-.758a1.7 1.7 0 0 1 .015-.588c.07-.327-.007-.537-.238-.727-.188-.156-.426-.199-.689-.199a.6.6 0 0 1-.254-.078.253.253 0 0 1-.114-.358 1 1 0 0 1 .192-.21c.356-.202.767-.136 1.146.016.352.144.618.408 1.001.782.392.451.462.576.685.915.176.264.336.536.446.848.066.194-.02.353-.25.45"/>' +
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

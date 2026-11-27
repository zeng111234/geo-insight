/*
 * A3 壳（决策 009，2026-11-26）：全局积分徽章 + AI 助手按钮。
 *
 * 诚实边界：余额只从 /v1/credits 真账本读，失败显示「—」不编数字；
 * AI 助手抽屉明写「需 key，未接入」——指的是助手对话本身没接，没有表单、没有假分析。
 * 2026-11-27 纠偏：AI 写稿已在内容工作室真接（DeepSeek key 可用），抽屉不能再笼统宣称
 * 整体 AI 都不可用把用户误导走。这里只承认对话未接，并指路真正可用的写稿入口。
 * button 是死的，但话必须真——两边都不能说谎。
 *
 * 由 nav.render_page 在服务端统一注入，各页 top 栏 HTML 不动。
 * 打印/详情页（report/pilot/answer）不注入本脚本。
 */
(function () {
  "use strict";

  var API = location.protocol === "file:"
    ? "http://127.0.0.1:8123/v1"
    : "/v1";

  // 只定义自己用的两个类，不改任何既有样式规则。.drawer/.scrim/.no-print
  // 直接用 app.css 里已有的全局定义（打印时自动隐藏）。
  var STYLE_ID = "globalBarStyle";
  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent =
      ".global-credit-badge{border:1px solid var(--border,#d8dde7);background:var(--panel-2,#f6f7fa);" +
      "border-radius:999px;padding:3px 10px;font-size:var(--fs-label,12px);color:var(--muted,#6b7280);" +
      "white-space:nowrap;cursor:pointer;user-select:none}" +
      ".global-credit-badge b{color:var(--accent-ink,#334155);font-variant-numeric:tabular-nums}" +
      ".global-credit-badge:hover{border-color:var(--accent,#2563eb)}" +
      ".global-ai-btn{white-space:nowrap}";
    document.head.appendChild(style);
  }

  function buildDrawer() {
    // 独立 ID：index.html 已有 #drawer / #scrim，不能撞；只复用 class。
    var scrim = document.createElement("div");
    scrim.className = "scrim";
    scrim.id = "aiScrim";

    var drawer = document.createElement("aside");
    drawer.className = "drawer";
    drawer.id = "aiDrawer";
    drawer.setAttribute("aria-hidden", "true");
    drawer.innerHTML =
      '<header><div><h3>AI 助手</h3><div class="sub">接入状态</div></div>' +
      '<span class="spacer"></span>' +
      '<button type="button" class="ghost small" id="aiDrawerClose">关闭</button></header>' +
      '<div class="drawer-body">' +
      '<p><b>助手对话：需 key，未接入。</b></p>' +
      '<p class="muted">这个助手只会说明接入状态，不做对话问答、不代写内容、' +
      '不会扣分，也不会假装分析——积分账本是真账本。</p>' +
      '<p class="muted"><b>AI 写稿已经能用：</b>到「内容工作室」点新建文章，' +
      '面板里那个带 ✦ 的 AI 按钮会真调 DeepSeek 写出初稿（key 已在本机配置），' +
      '生成后必须人工核对事实再发布。</p>' +
      '<p class="muted">积分余额可在右上角徽章查看，点开是真实流水。</p>' +
      "</div>";
    document.body.appendChild(scrim);
    document.body.appendChild(drawer);

    function close() {
      drawer.classList.remove("show");
      drawer.setAttribute("aria-hidden", "true");
      scrim.classList.remove("show");
    }
    scrim.addEventListener("click", close);
    drawer.querySelector("#aiDrawerClose").addEventListener("click", close);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && drawer.classList.contains("show")) close();
    });
    return {
      open: function () {
        drawer.classList.add("show");
        drawer.setAttribute("aria-hidden", "false");
        scrim.classList.add("show");
      }
    };
  }

  function boot() {
    var header = document.querySelector("header.top");
    if (!header) return; // login.html 走 render_login，本来就不到这里
    ensureStyle();

    // studio.html 已有真实徽章（#creditBadge），不重复追加，避免重复 ID。
    if (!document.getElementById("creditBadge")) {
      var badge = document.createElement("span");
      badge.className = "global-credit-badge no-print";
      badge.id = "globalCreditBadge";
      badge.title = "查看积分流水（真实账本）";
      badge.innerHTML = '积分 <b id="globalCreditBalance">—</b>';
      header.appendChild(badge);

      // 徽章点开流水：只读取 /v1/credits，不做任何写操作。
      badge.addEventListener("click", async function () {
        try {
          var res = await fetch(API + "/credits");
          if (!res.ok) throw new Error("HTTP " + res.status);
          var d = await res.json();
          var lines = (d.entries || []).map(function (e) {
            return '<li>' + (e.delta > 0 ? "+" : "") + e.delta + " 分 · " +
              (e.reason || "") + "</li>";
          }).join("");
          var box = document.getElementById("globalCreditEntries");
          if (!box) {
            box = document.createElement("aside");
            box.className = "drawer";
            box.id = "globalCreditEntries";
            box.setAttribute("aria-hidden", "true");
            box.innerHTML = '<header><div><h3>积分流水</h3>' +
              '<div class="sub">账本是真的，定价是假的</div></div><span class="spacer"></span>' +
              '<button type="button" class="ghost small" id="globalCreditClose">关闭</button></header>' +
              '<div class="drawer-body"><ul id="globalCreditList"></ul></div>';
            document.body.appendChild(box);
            document.getElementById("globalCreditClose").addEventListener("click", function () {
              box.classList.remove("show");
              box.setAttribute("aria-hidden", "true");
            });
          }
          document.getElementById("globalCreditList").innerHTML = lines || "<li>暂无流水</li>";
          box.classList.add("show");
          box.setAttribute("aria-hidden", "false");
        } catch (e) {
          window.alert("积分读取失败：" + e.message);
        }
      });

      // 余额只从真账本读，失败降级为「—」，绝不写死数字。
      fetch(API + "/credits")
        .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
        .then(function (d) {
          document.getElementById("globalCreditBalance").textContent = d.balance + " 分";
        })
        .catch(function () {
          document.getElementById("globalCreditBalance").textContent = "—";
        });
    }

    var ai = buildDrawer();
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ghost small global-ai-btn no-print";
    btn.id = "globalAiButton";
    btn.textContent = "AI 助手";
    btn.title = "查看接入状态（当前未接入）";
    btn.addEventListener("click", ai.open);
    header.appendChild(btn);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();

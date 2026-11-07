/* =============================================================
 * OpenBB 中文投研工作台 · pages/settings.js
 * 设置（AI/外观/缓存/关于/启动关闭/养基宝）
 * 由 tools/split_pages.py 从 pages.js 机械切分生成（R17），逻辑零改动；
 * 重新生成请改 pages.js 后重跑脚本，勿直接编辑本文件的结构性段落。
 * ============================================================= */
(function (P) {
  "use strict";

  // ---- 跨模块依赖（core/兄弟模块导出，运行时解析） ----
  var VIEW_IDS = P.VIEW_IDS;
  var api = P.api;
  var charts = P.charts;
  var el = P.el;
  var state = P.state;
  var t = P.t;
  var toast = P.toast;

  // =============================================================
  // 十二之三、#/settings 设置（阶段 8 补齐，OB-V11/F069-F072）
  // =============================================================
  /** 构建设置页骨架（AI 配置 / 主题与缓存 / 关于）。
   * @param {HTMLElement} container DOM 容器节点
   */
  function buildSettingsShell(container) {
    container.innerHTML = "";
    var grid = el("div", "pg-grid");

    // —— S-01 AI 设置卡：服务商预设 / base_url / api_key / model + 测试连接 ——
    var aiCard = P.buildCard("ST-AI", t("set.card.ai"), "g-12", t("set.card.aiSub"));
    aiCard.body.id = "set-ai-body";
    grid.appendChild(aiCard.card);

    // —— S-02 外观卡：主题切换 + 字号档位（CSS 变量缩放，即时生效） ——
    var themeCard = P.buildCard("ST-TH", t("set.card.theme"), "g-6", t("set.card.themeSub"));
    themeCard.body.id = "set-theme-body";
    grid.appendChild(themeCard.card);

    // —— S-03 缓存卡：内存/磁盘/布局占用 + TTL 口径 + 清理（R3-13） ——
    var cacheCard = P.buildCard("ST-CA", t("set.card.cache"), "g-6", t("set.card.cacheSub"));
    cacheCard.body.id = "set-cache-body";
    grid.appendChild(cacheCard.card);

    // —— ST-CP 数据与合规卡（R3-13）：盘中估值 + 养基宝增强通道两个 opt-in 开关 ——
    var complianceCard = P.buildCard("ST-CP", t("set.card.compliance"), "g-12", t("set.card.complianceSub"));
    complianceCard.body.id = "set-compliance-body";
    grid.appendChild(complianceCard.card);

    // —— ST-YJ 养基宝连接卡（首启向导兜底）：注入签名盐（DPAPI/掩码）+ 绑定状态 + 去扫码 ——
    var yjbCard = P.buildCard("ST-YJ", t("set.card.yjb"), "g-12", t("set.card.yjbSub"));
    yjbCard.body.id = "set-yjb-body";
    grid.appendChild(yjbCard.card);

    // —— ST-CL 启动与关闭卡（R6-12 / F6-14）：点关闭按钮时「最小化到托盘 / 直接退出」——
    var closeCard = P.buildCard("ST-CL", t("set.card.close"), "g-12", t("set.card.closeSub"));
    closeCard.body.id = "set-close-body";
    grid.appendChild(closeCard.card);

    // —— ST-AC 访问方式卡（Web 化）：本机/局域网入口 + 扫码 + 口令状态 ——
    var accessCard = P.buildCard("ST-AC", t("set.card.access"), "g-12", t("set.card.accessSub"));
    accessCard.body.id = "set-access-body";
    grid.appendChild(accessCard.card);

    // —— S-05 关于卡：版本/许可证/数据源数 + 日志导出 + 恢复出厂 ——
    var aboutCard = P.buildCard("ST-AB", t("set.card.about"), "g-12", t("set.card.aboutSub"));
    aboutCard.body.id = "set-about-body";
    grid.appendChild(aboutCard.card);

    container.appendChild(grid);
  }

  /** 渲染「数据与合规」卡：盘中估值 + 养基宝增强通道两个 opt-in 开关（R3-13 S-04）。
   * 两个开关默认均为关；关闭后相关通道立即回退 T+1 官方净值口径。
   * @returns {void} 无返回值（异步渲染）
   */
  function paintSettingsCompliance() {
    var body = document.getElementById("set-compliance-body");
    if (!body) {
      return;
    }
    P.setLoading(body);
    api("/api/fund/estimation", {})
      .then(function (data) {
        body.innerHTML = "";
        var row = el("div", "page-toolbar");
        row.appendChild(el("span", "pg-toolbar-label", t("set.estimation.label")));
        var toggle = el("button", "pg-btn" + (data.enabled ? " primary" : ""),
          data.enabled ? t("set.estimation.on") : t("set.estimation.off"));
        toggle.type = "button";
        toggle.addEventListener("click", function () {
          fetch("/api/fund/estimation", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: !data.enabled })
          }).then(function (response) { return response.json(); })
            .then(function (res) {
              if (res.error) {
                toast(res.error);
                return;
              }
              toast(t("set.estimation.toggled"));
              paintSettingsCompliance();
            })
            .catch(function () { toast(t("set.estimation.fail")); });
        });
        row.appendChild(toggle);
        body.appendChild(row);
        // 常驻口径角标：估算非官方，关闭即回退 T+1
        body.appendChild(el("div", "pg-card-sub", t("set.estimation.note")));

        // 养基宝增强通道开关（Y-04 一级估值通道；未绑定时引导去扫码）
        return api("/api/fund/yjb/status", {}).then(function (yjb) {
          var yjbRow = el("div", "page-toolbar");
          yjbRow.appendChild(el("span", "pg-toolbar-label", t("set.yjb.label")));
          if (!yjb.bound) {
            yjbRow.appendChild(el("span", "pg-card-sub", t("set.yjb.unbound")));
            body.appendChild(yjbRow);
            body.setAttribute("data-state", "default");
            return;
          }
          var yjbToggle = el("button", "pg-btn" + (yjb.enabled ? " primary" : ""),
            yjb.enabled ? t("set.yjb.on") : t("set.yjb.off"));
          yjbToggle.type = "button";
          yjbToggle.addEventListener("click", function () {
            P.apiPost("/api/fund/yjb/toggle", { enabled: !yjb.enabled })
              .then(function () {
                toast(t("set.yjb.toggled"));
                paintSettingsCompliance();
              })
              .catch(function (error) { toast(error.message); });
          });
          yjbRow.appendChild(yjbToggle);
          body.appendChild(yjbRow);
          body.appendChild(el("div", "pg-card-sub", t("set.yjb.note")));
          body.setAttribute("data-state", "default");
        });
      })
      .catch(function (error) {
        P.setError(body, error.message, function () { paintSettingsCompliance(); });
      });
  }

  /** AI 配置表单字段构造（输入框样式与持仓页表单一致）。
   * @param {string} id 元素 id / 组件 id
   * @param {*} placeholder 占位文案
   * @param {*} width 宽度（栅格列数）
   * @param {*} secret 签名密钥（敏感信息）
   * @returns {*} 处理结果
   */
  function settingsField(id, placeholder, width, secret) {
    var input = document.createElement("input");
    input.id = id;
    input.placeholder = placeholder;
    if (secret) {
      input.type = "password";
    }
    input.autocomplete = "off";
    input.style.cssText = "width:" + (width || 220) + "px;background:var(--bg-panel);"
      + "border:1px solid var(--border);color:var(--text);border-radius:5px;padding:4px 8px";
    return input;
  }

  /** 渲染 AI 设置卡：服务商预设 + 配置回显（掩码）+ 保存 + 测试连接。 */
  function paintSettingsAI() {
    var body = document.getElementById("set-ai-body");
    if (!body) {
      return;
    }
    P.setLoading(body);
    api("/api/ai/config", {})
      .then(function (data) {
        body.innerHTML = "";
        var form = el("div", "page-toolbar");
        form.appendChild(el("span", "pg-toolbar-label", t("set.label.provider")));
        var provider = document.createElement("select");
        provider.id = "set-provider";
        provider.style.cssText = "background:var(--bg-panel);border:1px solid var(--border);"
          + "color:var(--text);border-radius:5px;padding:4px 8px";
        [
          { value: "deepseek", label: "DeepSeek", base: "https://api.deepseek.com/v1", model: "deepseek-chat" },
          { value: "qwen", label: "通义千问", base: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
          { value: "custom", label: t("set.provider.custom"), base: "", model: "" }
        ].forEach(function (preset) {
          var option = document.createElement("option");
          option.value = preset.value;
          option.textContent = preset.label;
          option.dataset.base = preset.base;
          option.dataset.model = preset.model;
          provider.appendChild(option);
        });
        form.appendChild(provider);
        form.appendChild(el("span", "pg-toolbar-label", t("set.label.baseUrl")));
        form.appendChild(settingsField("set-base-url", t("set.placeholder.baseUrl"), 240));
        form.appendChild(el("span", "pg-toolbar-label", t("set.label.apiKey")));
        form.appendChild(settingsField("set-api-key", data.configured ? t("set.placeholder.apiKeySaved") : t("set.placeholder.apiKeyNew"), 240, true));
        form.appendChild(el("span", "pg-toolbar-label", t("set.label.model")));
        form.appendChild(settingsField("set-model", t("set.placeholder.model"), 180));
        form.appendChild(el("span", "pg-spacer"));
        var save = el("button", "pg-btn primary", t("set.action.save"));
        save.type = "button";
        save.id = "set-ai-save";
        form.appendChild(save);
        // 测试连接：用当前表单值直连上游探测（api_key 留空时用已保存密钥）
        var testBtn = el("button", "pg-btn", t("set.action.test"));
        testBtn.type = "button";
        testBtn.id = "set-ai-test";
        form.appendChild(testBtn);
        body.appendChild(form);
        // 回显：base_url/model 明文（非敏感），api_key 只显示掩码状态
        document.getElementById("set-base-url").value = data.base_url || "";
        document.getElementById("set-model").value = data.model || "";
        // 按已保存的 base_url 反选服务商预设（匹配不到回落 custom）
        var matched = "custom";
        Array.prototype.slice.call(provider.options).forEach(function (option) {
          if (option.dataset.base && option.dataset.base === (data.base_url || "")) {
            matched = option.value;
          }
        });
        provider.value = matched;
        // 选择预设时自动填充 base_url/model（custom 不动已填内容）
        provider.addEventListener("change", function () {
          var option = provider.options[provider.selectedIndex];
          if (!option || !option.dataset || !option.dataset.base) {
            return;
          }
          document.getElementById("set-base-url").value = option.dataset.base;
          document.getElementById("set-model").value = option.dataset.model;
        });
        var status = el("div", "pg-card-sub");
        status.id = "set-ai-status";
        status.textContent = data.configured
          ? t("set.ai.configured", { model: data.model || t("set.ai.modelEmpty"), key: data.api_key || t("set.ai.keyMasked") })
          : t("set.ai.notConfigured");
        body.appendChild(status);
        body.appendChild(el("div", "pg-card-sub", t("set.ai.securityNote")));
        body.setAttribute("data-state", "default");

        save.addEventListener("click", function () {
          if (state.settings.saving) {
            return; // 防重复提交
          }
          state.settings.saving = true;
          save.disabled = true;
          save.textContent = t("set.action.saving");
          P.apiPost("/api/ai/config", {
            base_url: (document.getElementById("set-base-url").value || "").trim(),
            api_key: (document.getElementById("set-api-key").value || "").trim(),
            model: (document.getElementById("set-model").value || "").trim()
          })
            .then(function () {
              toast(t("set.toast.aiSaved"));
              state.settings.saving = false;
              save.disabled = false;
              save.textContent = t("set.action.save");
              paintSettingsAI(); // 回显最新状态（密钥掩码）
            })
            .catch(function (error) {
              toast(t("set.toast.aiFailed", { message: error.message }));
              state.settings.saving = false;
              save.disabled = false;
              save.textContent = t("set.action.save");
            });
        });

        // 测试连接：加载态 + 结果 toast（R3-13）
        testBtn.addEventListener("click", function () {
          if (state.settings.testing) {
            return;
          }
          state.settings.testing = true;
          testBtn.disabled = true;
          testBtn.textContent = t("set.action.testing");
          P.apiPost("/api/ai/test", {
            base_url: (document.getElementById("set-base-url").value || "").trim(),
            api_key: (document.getElementById("set-api-key").value || "").trim(),
            model: (document.getElementById("set-model").value || "").trim()
          })
            .then(function (detail) {
              // apiPost 成功即代表探测通过：data 透传 {ok, latency_ms, detail}
              toast(t("set.toast.testOk", { ms: (detail && detail.latency_ms) || 0 }));
            })
            .catch(function (error) {
              // 业务失败（未配置/鉴权/超时/上游异常）走 reject，error.message 已是中文
              toast(t("set.toast.testFail", { message: error.message }));
            })
            .then(function () {
              state.settings.testing = false;
              testBtn.disabled = false;
              testBtn.textContent = t("set.action.test");
            });
        });
      })
      .catch(function (error) {
        P.setError(body, error.message, paintSettingsAI);
      });
  }

  /** 渲染外观卡：主题切换 + 字号档位（R3-13 S-02）。
   * 字号通过 html 的 data-font 属性 + CSS 变量缩放，立即生效并持久化。
   */
  function paintSettingsTheme() {
    var body = document.getElementById("set-theme-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    var light = t("set.theme.light");
    var dark = t("set.theme.dark");
    var current = document.documentElement.getAttribute("data-theme") === "light" ? light : dark;
    var row = el("div", "page-toolbar");
    row.appendChild(el("span", "pg-toolbar-label", t("set.theme.current", { theme: current })));
    var toggle = el("button", "pg-btn", t(current === light ? "set.theme.toDark" : "set.theme.toLight"));
    toggle.type = "button";
    toggle.id = "set-theme-toggle";
    row.appendChild(toggle);
    body.appendChild(row);
    // 主题切换与 app.js 的 applyTheme 同口径：写 data-theme + localStorage["openbb-theme"]
    toggle.addEventListener("click", function () {
      var next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", next);
      try {
        window.localStorage.setItem("openbb-theme", next);
      } catch (error) {
        /* 隐私模式下忽略持久化，主题仍生效 */
      }
      paintSettingsTheme(); // 刷新按钮文案
      charts.forEach(function (instance) {
        try {
          instance.resize();
        } catch (error) {
          /* 已释放则忽略 */
        }
      });
      toast(t("set.theme.toggled"));
    });

    // 字号档位：小/中/大 → html[data-font] → CSS 变量缩放（R3-13 S-02）
    var fontRow = el("div", "page-toolbar");
    fontRow.appendChild(el("span", "pg-toolbar-label", t("set.font.label")));
    var currentFont = document.documentElement.getAttribute("data-font") || "md";
    [
      { value: "sm", label: t("set.font.sm") },
      { value: "md", label: t("set.font.md") },
      { value: "lg", label: t("set.font.lg") }
    ].forEach(function (item) {
      var button = el("button", "pg-btn" + (currentFont === item.value ? " primary" : ""), item.label);
      button.type = "button";
      button.addEventListener("click", function () {
        document.documentElement.setAttribute("data-font", item.value);
        try {
          window.localStorage.setItem("openbb-font", item.value);
        } catch (error) {
          /* 隐私模式下忽略持久化，字号仍生效 */
        }
        charts.forEach(function (instance) {
          try {
            instance.resize();
          } catch (error) {
            /* 已释放则忽略 */
          }
        });
        toast(t("set.font.toggled", { label: item.label }));
        paintSettingsTheme();
      });
      fontRow.appendChild(button);
    });
    body.appendChild(fontRow);
    body.appendChild(el("div", "pg-card-sub", t("set.font.note")));
    body.setAttribute("data-state", "default");
  }

  /** 渲染缓存卡：占用统计 + TTL 口径表 + 清理按钮（R3-13 S-03）。 */
  function paintSettingsCache() {
    var body = document.getElementById("set-cache-body");
    if (!body) {
      return;
    }
    P.setLoading(body);
    api("/api/cache/stats", {})
      .then(function (data) {
        body.innerHTML = "";
        var grid = el("div", "pg-metric-grid");
        [
          [t("set.cache.memory"), data.memory + " " + t("set.cache.entries") + " · " + (data.memory_mb || 0) + " MB", "flat"],
          [t("set.cache.disk"), data.disk + " " + t("set.cache.files") + " · " + (data.disk_mb || 0) + " MB", "flat"],
          [t("set.cache.layout"), (data.layout_kb || 0) + " KB", "flat"]
        ].forEach(function (item) {
          var cell = el("div", "pg-metric-cell");
          cell.appendChild(el("div", "pg-metric-label", item[0]));
          cell.appendChild(el("div", "pg-metric-value " + item[2], String(item[1])));
          grid.appendChild(cell);
        });
        body.appendChild(grid);

        var row = el("div", "page-toolbar");
        var clearBtn = el("button", "pg-btn", t("set.cache.clear"));
        clearBtn.type = "button";
        clearBtn.addEventListener("click", function () {
          clearBtn.disabled = true;
          clearBtn.textContent = t("set.cache.clearing");
          fetch("/api/cache/clear", { method: "POST" })
            .then(function (response) { return response.json(); })
            .then(function (result) {
              toast(result.error ? t("toast.cacheFailed") : t("toast.cacheCleared"));
              paintSettingsCache();
            })
            .catch(function () { toast(t("toast.cacheFailed")); })
            .then(function () {
              clearBtn.disabled = false;
              clearBtn.textContent = t("set.cache.clear");
            });
        });
        row.appendChild(clearBtn);
        row.appendChild(el("span", "pg-spacer"));
        row.appendChild(el("span", "pg-card-sub", t("set.cache.scope")));
        body.appendChild(row);

        // TTL 口径表（与 07_数据 数据字典 §9 同源）
        var table = el("table", "pg-table");
        var head = document.createElement("tr");
        [t("set.cache.ttlKey"), t("set.cache.ttlValue"), t("set.cache.ttlNote")].forEach(function (label) {
          var th = document.createElement("th");
          th.textContent = label;
          head.appendChild(th);
        });
        table.appendChild(head);
        (data.ttl_map || []).forEach(function (item) {
          var tr = document.createElement("tr");
          [item.key, String(item.ttl), item.note || "—"].forEach(function (text, index) {
            var td = document.createElement("td");
            td.textContent = text;
            if (index === 0) {
              td.className = "mono";
            }
            tr.appendChild(td);
          });
          table.appendChild(tr);
        });
        body.appendChild(table);
        body.setAttribute("data-state", "default");
      })
      .catch(function (error) {
        P.setError(body, error.message, paintSettingsCache);
      });
  }

  /** 渲染关于卡：版本/许可证/数据源数 + 日志导出 + 恢复出厂（R3-13 S-05）。 */
  function paintSettingsAbout() {
    var body = document.getElementById("set-about-body");
    if (!body) {
      return;
    }
    P.setLoading(body);
    api("/api/health", {})
      .then(function (data) {
        body.innerHTML = "";
        var app = data.app || {};
        var rows = [
          [t("set.about.software"), app.name || t("set.about.default")],
          [t("set.about.version"), app.version || "—"],
          [t("set.about.build"), data.build || "—"],
          [t("set.about.python"), data.python || "—"],
          [t("set.about.datasources"), t("set.about.datasourcesValue", { count: Object.keys(data.datasources || {}).length })],
          [t("set.about.license"), t("set.about.licenseValue")],
          [t("set.about.serverTime"), data.server_time || "—"]
        ];
        var table = el("table", "pg-table");
        rows.forEach(function (pair) {
          var tr = document.createElement("tr");
          var th = document.createElement("th");
          th.textContent = pair[0];
          var td = document.createElement("td");
          td.textContent = pair[1];
          tr.appendChild(th);
          tr.appendChild(td);
          table.appendChild(tr);
        });
        body.appendChild(table);

        // 日志导出：GET /api/logs/export 直接下载 zip
        var row = el("div", "page-toolbar");
        var exportBtn = el("button", "pg-btn", t("set.about.exportLogs"));
        exportBtn.type = "button";
        exportBtn.addEventListener("click", function () {
          exportBtn.disabled = true;
          window.open("/api/logs/export", "_blank");
          toast(t("set.about.exportStarted"));
          exportBtn.disabled = false;
        });
        row.appendChild(exportBtn);

        // 恢复出厂：二次确认弹窗（R3-13）；只清应用偏好，不动持仓/自选/密钥
        var resetBtn = el("button", "pg-btn danger", t("set.about.factoryReset"));
        resetBtn.type = "button";
        resetBtn.addEventListener("click", function () {
          if (!window.UI) {
            return;
          }
          window.UI.modal({
            title: t("set.about.factoryReset"),
            message: t("set.about.factoryWarn"),
            confirmText: t("set.about.factoryConfirm"),
            danger: true,
            /** 确认恢复出厂：显式提交 confirm + 全部偏好 scope。
             * @returns {void} 无返回值
             */
            onConfirm: function () {
              P.apiPost("/api/factory-reset", {
                confirm: true,
                scopes: ["prefs", "connectors", "dashboards", "ai"]
              })
                .then(function (result) {
                  var removed = (result && result.removed) || [];
                  toast(removed.length
                    ? t("set.about.factoryDone", { n: removed.length })
                    : t("set.about.factoryNothing"));
                  paintSettingsAbout();
                })
                .catch(function (error) { toast(error.message); });
            }
          });
        });
        row.appendChild(resetBtn);
        body.appendChild(row);
        body.appendChild(el("div", "pg-card-sub", t("set.about.factoryNote")));
        body.appendChild(el("div", "pg-card-sub", t("set.about.disclaimer")));
        body.setAttribute("data-state", "default");
      })
      .catch(function (error) {
        P.setError(body, error.message, paintSettingsAbout);
      });
  }

  /** 渲染置页入口：骨架 + 五卡数据（AI/外观/缓存/合规/关于）。 */

  function renderSettings() {
    var container = document.getElementById(VIEW_IDS.settings);
    if (!container.querySelector("#set-ai-body")) {
      buildSettingsShell(container);
    }
    paintSettingsAI();
    paintSettingsTheme();
    paintSettingsCache();
    paintSettingsAbout();
    paintSettingsCompliance();
    paintSettingsYjb();
    paintSettingsClose();
    paintSettingsAccess();
  }

  /** 渲染「启动与关闭」卡（R6-12 / F6-14）。
   *
   * 两个单选项写入 data/user_prefs.json 的 close_action；桌面壳在点关闭按钮时
   * 读取该值决定「隐藏到托盘」还是「直接退出」。默认 tray。
   *
   * @returns {void} 无返回值（异步渲染）
   */
  function paintSettingsClose() {
    var body = document.getElementById("set-close-body");
    if (!body) {
      return;
    }
    P.setLoading(body);
    api("/api/system/desktop-prefs", {}).then(function (data) {
      body.innerHTML = "";
      var current = data.close_action === "quit" ? "quit" : "tray";
      var list = el("div", "set-close-list");
      [
        ["tray", t("set.close.tray"), t("set.close.trayHint")],
        ["quit", t("set.close.quit"), t("set.close.quitHint")]
      ].forEach(function (option) {
        var row = el("label", "set-close-row" + (current === option[0] ? " active" : ""));
        var radio = document.createElement("input");
        radio.type = "radio";
        radio.name = "close-action";
        radio.value = option[0];
        radio.checked = current === option[0];
        radio.addEventListener("change", function () {
          if (!radio.checked) {
            return;
          }
          // Utils.api 只支持 GET，写偏好走原生 fetch（PUT + JSON）
          fetch("/api/system/desktop-prefs", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ close_action: option[0] })
          }).then(function (response) {
            return response.json();
          }).then(function (payload) {
            if (payload && payload.error) {
              throw new Error(payload.error);
            }
            current = option[0];
            body.querySelectorAll(".set-close-row").forEach(function (node) {
              node.classList.toggle("active", node.contains(radio));
            });
            toast(t("set.close.saved"));
          }).catch(function (error) {
            toast(error.message || t("state.errorHint"));
          });
        });
        row.appendChild(radio);
        var text = el("div", "set-close-text");
        text.appendChild(el("b", "", option[1]));
        text.appendChild(el("span", "", option[2]));
        row.appendChild(text);
        list.appendChild(row);
      });
      body.appendChild(list);
      body.appendChild(el("div", "pg-card-sub", t("set.close.note")));
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      P.setError(body, error.message || t("state.errorHint"), paintSettingsClose);
    });
  }

  /** 复制文本到剪贴板（优先 Clipboard API，回退 execCommand）。
   *
   * @param {string} text 待复制内容。
   * @returns {Promise<boolean>} 复制成功 true；失败 false（界面据此给真实反馈）。
   */
  function copyAccessUrl(text) {
    return new Promise(function (resolve) {
      var fallback = function () {
        var area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.left = "-9999px";
        document.body.appendChild(area);
        area.select();
        var done = false;
        try {
          done = document.execCommand("copy");
        } catch (error) {
          done = false;
        }
        document.body.removeChild(area);
        resolve(done);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { resolve(true); }, fallback);
        return;
      }
      fallback();
    });
  }

  /** 渲染「访问方式」卡（Web 化）：本机与局域网入口、扫码、口令状态。
   *
   * 局域网入口只在服务以 --lan（或 OPENBB_HOST=0.0.0.0）启动时出现；
   * 二维码内容即访问 URL，手机扫码进入的是同一应用、同一份本地数据。
   *
   * @returns {void} 无返回值（异步渲染）
   */
  function paintSettingsAccess() {
    var body = document.getElementById("set-access-body");
    if (!body) {
      return;
    }
    P.setLoading(body);
    api("/api/system/access", {}).then(function (data) {
      body.innerHTML = "";
      var list = el("div", "set-access-list");
      (data.points || []).forEach(function (point) {
        var row = el("div", "set-access-row");
        var meta = el("div", "set-access-meta");
        var labelKey = point.scope === "lan" ? "set.access.lan"
          : (point.scope === "public" ? "set.access.public" : "set.access.local");
        var publicReady = data.public && data.public.ready;
        if (point.scope === "public") {
          labelKey = publicReady ? "set.access.publicReady" : "set.access.publicPending";
        }
        meta.appendChild(el("span", "set-access-label", t(labelKey)));
        meta.appendChild(el("span", "set-access-url", point.url));
        var copy = el("button", "pg-btn", t("set.access.copy"));
        copy.type = "button";
        copy.addEventListener("click", function () {
          copyAccessUrl(point.url).then(function (success) {
            toast(success ? t("set.access.copied") : t("set.access.fail"));
          });
        });
        meta.appendChild(copy);
        row.appendChild(meta);
        if (point.qr) {
          var img = document.createElement("img");
          img.className = "set-access-qr";
          img.alt = point.url;
          img.src = point.qr;
          row.appendChild(img);
        }
        list.appendChild(row);
      });
      body.appendChild(list);
      body.appendChild(el("div", "set-access-note", t("set.access.scanHint")));
      if (!data.lan_visible) {
        body.appendChild(el("div", "set-access-note", t("set.access.onlyLocal")));
      }
      body.appendChild(el("div", "set-access-note",
        data.password_required ? t("set.access.passwordOn") : t("set.access.passwordOff")));
      var refreshBtn = el("button", "pg-btn", t("set.access.refresh"));
      refreshBtn.type = "button";
      refreshBtn.addEventListener("click", function () { paintSettingsAccess(); });
      body.appendChild(refreshBtn);
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      P.setError(body, error.message || t("set.access.fail"), paintSettingsAccess);
    });
  }

  /** 渲染「养基宝连接」卡：绑定/盐/安全存储三态 + 掩码注入签名盐 + 去扫码。
   * 首启向导（Y-00）的第二步落到这里：冻结包 data 缺盐时用户可在设置页补盐，
   * 全程 DPAPI 加密落盘，输入框 type=password 且提交后立即清空，不回显明文。
   * @returns {void} 无返回值（异步渲染）
   */
  function paintSettingsYjb() {
    var body = document.getElementById("set-yjb-body");
    if (!body) {
      return;
    }
    P.setLoading(body);
    api("/api/fund/yjb/status", {}).then(function (data) {
      body.innerHTML = "";
      var chips = el("div", "page-toolbar");
      [
        [t("set.yjb.chip.bound"), data.bound],
        [t("set.yjb.chip.secret"), data.secret_configured],
        [t("set.yjb.chip.secure"), data.secure_storage]
      ].forEach(function (pair) {
        chips.appendChild(el("span", "chip " + (pair[1] ? "" : "down"),
          pair[0] + "：" + (pair[1] ? t("set.yjb.ok") : t("set.yjb.missing"))));
      });
      chips.appendChild(el("span", "pg-spacer"));
      var goFund = el("button", "pg-btn", t("set.yjb.goScan"));
      goFund.type = "button";
      goFund.addEventListener("click", function () { window.location.hash = "#/yjb"; });
      chips.appendChild(goFund);
      body.appendChild(chips);

      if (data.setup_required) {
        body.appendChild(el("div", "pg-card-sub", t("set.yjb.setupHint")));
      }
      var formHost = el("div", "");
      formHost.id = "set-yjb-secret";
      P.buildYjbSecretForm(formHost, data.secret_configured, null, function () {
        // 保存/清除成功后只刷新本卡，不依赖养基宝页 DOM 是否在场
        paintSettingsYjb();
        if (document.getElementById("y00-body")) {
          P.loadYjbStatus();
        }
      });
      body.appendChild(formHost);
      body.appendChild(el("div", "pg-card-sub", t("set.yjb.maskNote")));
      if (data.is_frozen && data.data_dir) {
        body.appendChild(el("div", "pg-card-sub", t("set.yjb.dataDir") + data.data_dir));
      }
      body.setAttribute("data-state", "default");
      // 从首启向导跳来：滚动到本卡并聚焦签名盐输入框
      if (window.__yjbFocusSecret) {
        window.__yjbFocusSecret = false;
        if (body.scrollIntoView) {
          body.scrollIntoView({ block: "center" });
        }
        var input = document.querySelector("#set-yjb-secret input[type=password]");
        if (input) {
          input.focus();
        }
      }
    }).catch(function (error) {
      P.setError(body, error.message || t("state.errorHint"), paintSettingsYjb);
    });
  }

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.buildSettingsShell = buildSettingsShell;
  P.paintSettingsAI = paintSettingsAI;
  P.paintSettingsAbout = paintSettingsAbout;
  P.paintSettingsCache = paintSettingsCache;
  P.paintSettingsClose = paintSettingsClose;
  P.paintSettingsCompliance = paintSettingsCompliance;
  P.paintSettingsTheme = paintSettingsTheme;
  P.paintSettingsYjb = paintSettingsYjb;
  P.renderSettings = renderSettings;
  P.settingsField = settingsField;
})(window.Pages = window.Pages || {});

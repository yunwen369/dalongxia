/* =============================================================
 * OpenBB 中文投研工作台 · pages/connectors.js
 * 数据连接器
 * 由 tools/split_pages.py 从 pages.js 机械切分生成（R17），逻辑零改动；
 * 重新生成请改 pages.js 后重跑脚本，勿直接编辑本文件的结构性段落。
 * ============================================================= */
(function (P) {
  "use strict";

  // ---- 跨模块依赖（core/兄弟模块导出，运行时解析） ----
  var VIEW_IDS = P.VIEW_IDS;
  var api = P.api;
  var el = P.el;
  var state = P.state;
  var t = P.t;
  var toast = P.toast;

  // =============================================================
  // 十一、#/connectors 数据连接器（批次 5.4）
  // 展示：数据源开关 + 三市场能力矩阵 + D3 缺口（如实标注不可用）
  // =============================================================
  /** 渲染连接器页。
   * @returns {*} 处理结果
   */
  /** 源状态中文文案（与后端 capabilities 同口径，缺失时按启用/探测推导）。
   * @param {Object} item 数据源条目
   * @param {boolean} userDisabled 用户是否停用
   * @returns {string} 中文状态
   */
  function connectorStateText(item, userDisabled) {
    if (userDisabled) {
      return t("connectors.state.userDisabled");
    }
    if (item.status) {
      return String(item.status);
    }
    if (item.active) {
      return t("connectors.state.enabled");
    }
    if (item.enabled) {
      return item.stage === "installed" ? t("connectors.state.installed") : t("connectors.state.pending");
    }
    return t("connectors.state.disabled");
  }

  /** 源状态色阶：active=正常 / enabled 未探测=安装待接 / 停用=down / optin=warn。
   * @param {Object} item 数据源条目
   * @param {boolean} userDisabled 用户是否停用
   * @returns {string} CSS 类名
   */
  function connectorTone(item, userDisabled) {
    if (userDisabled) {
      return "down";
    }
    if (item.active) {
      return "flat";
    }
    if (item.stage === "optin") {
      return "warn";
    }
    return "down";
  }

  /** 渲染连接器页：C-01 总览 + C-02 源表格 + C-03 降级链 + C-04 四市场矩阵（R3-03/R3-13）。
   * @returns {void} 无返回值（异步渲染）
   */
  function renderConnectors() {
    var container = document.getElementById(VIEW_IDS.connectors);
    container.innerHTML = "";
    var grid = el("div", "pg-grid");

    var card = P.buildCard("C-01", t("connectors.cardTitle"), "g-12", t("connectors.cardSub"));
    card.body.id = "c01-body";
    grid.appendChild(card.card);
    // LG-01 整改：C-02 明细表独立成卡（基准 §五5.6：C-01 g-12 / C-02 g-12 / C-03 g-6 / C-04 g-6），
    // 此前折叠在 C-01 body 内导致"统计+缓存+源表"挤在一卡，网格结构偏离设计基准。
    var tableCard = P.buildCard("C-02", t("connectors.tableTitle"), "g-12", t("connectors.tableSub"));
    tableCard.body.id = "c02-body";
    grid.appendChild(tableCard.card);
    var chainCard = P.buildCard("C-03", t("connectors.chainTitle"), "g-6", t("connectors.chainSub"));
    chainCard.body.id = "c03-body";
    grid.appendChild(chainCard.card);
    var matrixCard = P.buildCard("C-04", t("connectors.matrixTitle"), "g-6", t("connectors.matrixSub"));
    matrixCard.body.id = "c04-body";
    grid.appendChild(matrixCard.card);
    container.appendChild(grid);

    var body = document.getElementById("c01-body");
    P.setLoading(body);
    P.setLoading(document.getElementById("c02-body"));
    document.getElementById("c03-body").setAttribute("data-state", "default");
    document.getElementById("c03-body").appendChild(el("div", "pg-card-sub", t("connectors.chainNote")));
    document.getElementById("c04-body").setAttribute("data-state", "default");

    api("/api/connectors/status", {})
      .then(function (data) {
        P.setBadges(card.card, data.source, data.caliber, false);
        body.innerHTML = "";

        var disabledSet = {};
        (data.disabled || []).forEach(function (name) {
          disabledSet[name] = true;
        });
        var sources = data.datasources || [];
        var enabledCount = sources.filter(function (item) {
          return item.enabled && !disabledSet[item.label];
        }).length;
        var activeCount = sources.filter(function (item) { return item.active; }).length;
        var userDisabledCount = sources.filter(function (item) {
          return disabledSet[item.label];
        }).length;
        var degradedCount = Math.max(0, sources.length - activeCount - userDisabledCount);

        // C-01 四统计 + 缓存管理
        var statGrid = el("div", "pg-metric-grid");
        [
          [t("connectors.stat.enabled"), enabledCount + " / " + sources.length, "flat"],
          [t("connectors.stat.active"), String(activeCount), "flat"],
          [t("connectors.stat.degraded"), String(degradedCount), "warn"],
          [t("connectors.stat.disabled"), String(userDisabledCount), "down"]
        ].forEach(function (item) {
          var cell = el("div", "pg-metric-cell");
          cell.appendChild(el("div", "pg-metric-label", item[0]));
          cell.appendChild(el("div", "pg-metric-value " + item[2], item[1]));
          statGrid.appendChild(cell);
        });
        body.appendChild(statGrid);

        var cacheRow = el("div", "page-toolbar");
        cacheRow.appendChild(el("span", "pg-toolbar-label", t("connectors.cacheLabel")));
        var clearBtn = el("button", "pg-btn", t("set.cache.clear"));
        clearBtn.type = "button";
        clearBtn.addEventListener("click", function () {
          clearBtn.disabled = true;
          clearBtn.textContent = t("set.cache.clearing");
          fetch("/api/cache/clear", { method: "POST" })
            .then(function (response) { return response.json(); })
            .then(function (result) {
              toast(result.error ? t("toast.cacheFailed") : t("toast.cacheCleared"));
            })
            .catch(function () { toast(t("toast.cacheFailed")); })
            .then(function () {
              clearBtn.disabled = false;
              clearBtn.textContent = t("set.cache.clear");
            });
        });
        cacheRow.appendChild(clearBtn);
        body.appendChild(cacheRow);

        // C-02：源表格（状态 pill / 启停 / 密钥 / 测试 / 限流 / TTL / 降级链）
        var tableBody = document.getElementById("c02-body");
        P.setBadges(tableCard.card, data.source, data.caliber, false);
        tableBody.innerHTML = "";
        var tableHost = el("div", "");
        tableBody.appendChild(tableHost);
        var rows = sources.map(function (item) {
          var userDisabled = !!disabledSet[item.label];
          return {
            label: item.label,
            state: connectorStateText(item, userDisabled),
            tone: connectorTone(item, userDisabled),
            enabled: item.enabled && !userDisabled,
            key: item.label.indexOf("ai") >= 0 || item.label === "akshare（东财/新浪等）"
              ? t("connectors.key.public")
              : t("connectors.key.public"),
            rate: item.rate_limit || "未登记",
            ttl: item.ttl || "—",
            fallback: item.fallback || "—",
            detail: item.detail || ""
          };
        });
        P.renderTable(tableHost, [
          {
            title: t("connectors.col.source"), cls: "l",
            /** 源名列：中文标签 + 探测说明。
             * @param {Object} row 数据行
             * @returns {HTMLElement} 单元格节点
             */
            render: function (row) {
              var box = el("div", "");
              box.appendChild(el("b", "", row.label));
              if (row.detail) {
                box.appendChild(el("div", "pg-card-sub", row.detail));
              }
              return box;
            }
          },
          {
            title: t("connectors.col.state"),
            /** 状态 pill：按 tone 上色。
             * @param {Object} row 数据行
             * @returns {HTMLElement} 单元格节点
             */
            render: function (row) {
              return el("span", "chip " + (row.tone === "flat" ? "up" : row.tone === "warn" ? "warn" : "down"), row.state);
            }
          },
          {
            title: t("connectors.col.key"),
            /** 密钥列：公开接口如实标注；AI 服务商密钥在设置页管理。
             * @param {Object} row 数据行
             * @returns {HTMLElement} 单元格节点
             */
            render: function (row) {
              return el("span", "pg-card-sub", row.key);
            }
          },
          {
            title: t("connectors.col.rate"),
            /** 限流策略列。
             * @param {Object} row 数据行
             * @returns {HTMLElement} 单元格节点
             */
            render: function (row) {
              return el("span", "pg-card-sub", row.rate);
            }
          },
          { title: t("connectors.col.ttl"), key: "ttl", cls: "mono" },
          {
            title: t("connectors.col.fallback"),
            /** 降级链列。
             * @param {Object} row 数据行
             * @returns {HTMLElement} 单元格节点
             */
            render: function (row) {
              return el("span", "pg-card-sub", row.fallback);
            }
          },
          {
            title: t("connectors.col.actions"),
            /** 操作列：测试连接 + 启用/停用。
             * @param {Object} row 数据行
             * @returns {HTMLElement} 单元格节点
             */
            render: function (row) {
              var box = el("div", "page-toolbar");
              var testBtn = el("button", "pg-btn", t("connectors.testConn"));
              testBtn.type = "button";
              testBtn.addEventListener("click", function (event) {
                event.stopPropagation();
                testBtn.disabled = true;
                testBtn.textContent = t("connectors.testing");
                fetch("/api/connectors/test?source=" + encodeURIComponent(row.label), { method: "POST" })
                  .then(function (response) { return response.json(); })
                  .then(function (result) {
                    var payload = result.data || {};
                    testBtn.textContent = payload.ok ? t("connectors.testOk") : t("connectors.testFail");
                    toast(row.label + " · " + (payload.ok ? t("connectors.testOk") : t("connectors.testFail"))
                      + (payload.detail ? "（" + payload.detail + "）" : ""));
                  })
                  .catch(function () {
                    testBtn.textContent = t("connectors.testFail");
                    toast(t("connectors.testFail"));
                  })
                  .then(function () { testBtn.disabled = false; });
              });
              box.appendChild(testBtn);
              var toggleBtn = el("button", "pg-btn", row.enabled ? t("connectors.disable") : t("connectors.enable"));
              toggleBtn.type = "button";
              toggleBtn.addEventListener("click", function (event) {
                event.stopPropagation();
                fetch("/api/connectors/toggle", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ source: row.label, enabled: !row.enabled })
                }).then(function (response) { return response.json(); })
                  .then(function (result) {
                    if (result.error) {
                      toast(result.error);
                      return;
                    }
                    toast(result.data && result.data.enabled ? t("connectors.enabledToast") : t("connectors.disabledToast"));
                    renderConnectors();
                  })
                  .catch(function () { toast(t("connectors.toggleFail")); });
              });
              box.appendChild(toggleBtn);
              return box;
            }
          }
        ], rows, null, "tall");
        tableBody.appendChild(el("div", "pg-card-sub", t("connectors.keyNote")));
        tableBody.setAttribute("data-state", "default");

        // C-03：三级降级链说明
        var chainBody = document.getElementById("c03-body");
        chainBody.innerHTML = "";
        var chains = [
          t("connectors.chain.quote"),
          t("connectors.chain.fund"),
          t("connectors.chain.news"),
          t("connectors.chain.sector")
        ];
        var list = el("ul", "pg-news-list");
        chains.forEach(function (text) {
          var li = el("li", "pg-news-item");
          li.appendChild(el("span", "pg-news-title", text));
          list.appendChild(li);
        });
        chainBody.appendChild(list);
        chainBody.setAttribute("data-state", "default");

        // C-04：四市场能力矩阵（含场外基金列；缺口如实标注）
        var matrixBody = document.getElementById("c04-body");
        matrixBody.innerHTML = "";
        var features = [
          ["quote", t("connectors.feature.quote")],
          ["kline", t("connectors.feature.kline")],
          ["minute", t("connectors.feature.minute")],
          ["index", t("connectors.feature.index")],
          ["stock_news", t("connectors.feature.stockNews")],
          ["sector", t("connectors.feature.sector")]
        ];
        var matrixRows = features.map(function (pair) {
          var row = { feature: pair[1] };
          ["a", "hk", "us", "fund"].forEach(function (market) {
            var cell = ((data.markets || {})[market] || {})[pair[0]] || {};
            row[market] = cell.available
              ? "✓ " + (cell.source || "")
              : t("connectors.unavailableShort");
          });
          return row;
        });
        var matrixHost = el("div", "");
        matrixBody.appendChild(matrixHost);
        P.renderTable(matrixHost, [
          { title: t("connectors.featureLabel"), key: "feature", cls: "name" },
          { title: t("stock.market.a"), key: "a" },
          { title: t("sectors.market.hk"), key: "hk" },
          { title: t("sectors.market.us"), key: "us" },
          { title: t("nav.fund"), key: "fund" }
        ], matrixRows, null, null);

        if ((data.gaps || []).length) {
          matrixBody.appendChild(el("div", "pg-card-sub", t("connectors.gapTitle")));
          var gapList = el("ul", "pg-news-list");
          data.gaps.forEach(function (gap) {
            var li = el("li", "pg-news-item");
            li.appendChild(el("span", "pg-news-title", gap.market_label + " · " + gap.feature_label));
            li.appendChild(el("span", "chip down", t("connectors.unavailable")));
            li.appendChild(el("span", "pg-news-time", gap.reason));
            gapList.appendChild(li);
          });
          matrixBody.appendChild(gapList);
        }
        matrixBody.setAttribute("data-state", "default");
        body.setAttribute("data-state", "default");
      })
      .catch(function (error) {
        P.setError(body, error.message, function () {
          renderConnectors();
        });
        var tableBody = document.getElementById("c02-body");
        if (tableBody) {
          P.setError(tableBody, error.message, function () {
            renderConnectors();
          });
        }
      });
  }

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.connectorStateText = connectorStateText;
  P.connectorTone = connectorTone;
  P.renderConnectors = renderConnectors;
})(window.Pages = window.Pages || {});

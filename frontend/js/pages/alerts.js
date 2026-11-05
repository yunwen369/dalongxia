/* =============================================================
 * OpenBB 中文投研工作台 · pages/alerts.js
 * 提醒中心 M6
 * 由 tools/split_pages.py 从 pages.js 机械切分生成（R17），逻辑零改动；
 * 重新生成请改 pages.js 后重跑脚本，勿直接编辑本文件的结构性段落。
 * ============================================================= */
(function (P) {
  "use strict";

  // ---- 跨模块依赖（core/兄弟模块导出，运行时解析） ----
  var VIEW_IDS = P.VIEW_IDS;
  var api = P.api;
  var el = P.el;
  var fmt = P.fmt;
  var state = P.state;
  var t = P.t;
  var toast = P.toast;

  // =============================================================
  // 八、#/alerts 提醒中心（第七轮 M6）
  // 契约：GET/POST /api/alerts（规则列表/新增）
  //       PATCH/DELETE /api/alerts/{id}
  //       GET /api/alerts/events、POST /api/alerts/events/read
  //       POST /api/alerts/evaluate?force=
  // 规则六类：price/pct_change/valuation/crowding/announcement/report_date
  // =============================================================
  // 需要数值阈值的规则类型（announcement/report_date 只要 target）
  var ALERT_VALUE_TYPES = { price: 1, pct_change: 1, valuation: 1, crowding: 1 };

  /** 渲染提醒中心：事件列表 + 规则管理（新增表单/启停/删除）+ 手动评估。 */
  function renderAlerts() {
    var page = document.getElementById(VIEW_IDS.alerts);
    if (!page) {
      return;
    }
    page.innerHTML = "";
    var grid = el("div", "pg-grid");

    var eventCard = P.buildCard("M6-01", t("alr.eventTitle"), "g-7", t("alr.eventSub"));
    eventCard.body.id = "m601-body";
    grid.appendChild(eventCard.card);

    var ruleCard = P.buildCard("M6-02", t("alr.ruleTitle"), "g-5", t("alr.ruleSub"));
    ruleCard.body.id = "m602-body";
    grid.appendChild(ruleCard.card);

    page.appendChild(grid);
    paintAlertsEvents();
    paintAlertsRules();
    page.setAttribute("data-state", "default");
  }

  /** 事件列表：未读徽标 + 全部已读 + 单条已读 + 只看未读开关。 */
  function paintAlertsEvents() {
    var body = document.getElementById("m601-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    body.appendChild(el("div", "pg-state", t("state.loading")));
    api("/api/alerts/events", { unread_only: state.alerts.unreadOnly ? "true" : "" }).then(function (data) {
      body.innerHTML = "";
      updateBell(data.unread || 0);
      var card = document.getElementById("M6-01");
      if (card) {
        P.setBadges(card, t("alr.eventSource"), t("alr.dedupNote"));
      }
      var bar = el("div", "page-toolbar");
      var unreadBtn = el("button", "pg-btn" + (state.alerts.unreadOnly ? " primary" : ""),
        t("alr.unreadOnlyBtn") + "（" + fmt(data.unread || 0, 0) + "）");
      unreadBtn.type = "button";
      unreadBtn.addEventListener("click", function () {
        state.alerts.unreadOnly = !state.alerts.unreadOnly;
        paintAlertsEvents();
      });
      var readAllBtn = el("button", "pg-btn", t("alr.readAllBtn"));
      readAllBtn.type = "button";
      readAllBtn.disabled = !(data.unread > 0);
      readAllBtn.addEventListener("click", function () {
        alertMarkRead("").then(function (result) {
          toast(t("alr.markedToast", { count: result.marked || 0 }));
          paintAlertsEvents();
        }).catch(function (error) {
          toast(error.message || t("state.errorHint"));
        });
      });
      bar.appendChild(unreadBtn);
      bar.appendChild(readAllBtn);
      body.appendChild(bar);

      var events = data.events || [];
      if (!events.length) {
        body.appendChild(el("div", "pg-state", t("alr.emptyEvents")));
        body.setAttribute("data-state", "empty");
        return;
      }
      var list = el("div", "alr-list");
      events.forEach(function (item) {
        var line = el("div", "alr-item" + (item.read ? "" : " unread"));
        line.appendChild(el("i", item.read ? "dot" : "dot-w"));
        var main = el("div", "alr-main");
        main.appendChild(el("span", "alr-type", item.type_label || ""));
        main.appendChild(el("span", "alr-target", item.target || ""));
        main.appendChild(el("div", "alr-msg", item.message || ""));
        line.appendChild(main);
        line.appendChild(el("span", "alr-time", item.time || ""));
        if (!item.read) {
          var readBtn = el("button", "pg-btn", t("alr.readBtn"));
          readBtn.type = "button";
          readBtn.addEventListener("click", function (event) {
            event.stopPropagation();
            alertMarkRead(item.id).then(function () {
              paintAlertsEvents();
            }).catch(function (error) {
              toast(error.message || t("state.errorHint"));
            });
          });
          line.appendChild(readBtn);
        }
        list.appendChild(line);
      });
      body.appendChild(list);
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      P.setError(body, error.message || t("state.errorHint"), paintAlertsEvents);
      body.setAttribute("data-state", "error");
    });
  }

  /** POST /api/alerts/events/read（event_id 空串=全部已读）。 */
  function alertMarkRead(eventId) {
    return window.fetch(
      "/api/alerts/events/read" + (eventId ? "?event_id=" + encodeURIComponent(eventId) : ""),
      { method: "POST" }
    ).then(function (response) {
      if (!response.ok) {
        throw new Error("请求失败（HTTP " + response.status + "）");
      }
      return response.json();
    }).then(function (body) {
      if (body && body.error) {
        throw new Error(body.error);
      }
      return body.data || {};
    });
  }

  /** 规则管理：新增表单（六类规则）+ 规则表（启停/删除/立即评估）。 */
  function paintAlertsRules() {
    var body = document.getElementById("m602-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    api("/api/alerts", {}).then(function (data) {
      body.innerHTML = "";
      updateBell(data.unread || 0);
      var labels = data.type_labels || {};

      // —— 新增表单 ——
      var form = el("div", "alr-form");
      var type = document.createElement("select");
      type.className = "alr-input";
      Object.keys(labels).forEach(function (key) {
        var option = document.createElement("option");
        option.value = key;
        option.textContent = labels[key];
        type.appendChild(option);
      });
      type.value = state.alerts.draftType || "price";
      var target = document.createElement("input");
      target.type = "text";
      target.className = "alr-input";
      target.placeholder = t("alr.targetPlaceholder");
      var op = document.createElement("select");
      op.className = "alr-input";
      [["gte", "≥"], ["lte", "≤"]].forEach(function (pair) {
        var option = document.createElement("option");
        option.value = pair[0];
        option.textContent = pair[1];
        op.appendChild(option);
      });
      var value = document.createElement("input");
      value.type = "number";
      value.step = "any";
      value.className = "alr-input";
      value.placeholder = t("scr.valuePlaceholder");
      var note = document.createElement("input");
      note.type = "text";
      note.className = "alr-input";
      note.placeholder = t("alr.notePlaceholder");
      var addBtn = el("button", "pg-btn primary", t("alr.addBtn"));
      addBtn.type = "button";

      /** 比较类规则才显示 op/value（announcement/report_date 只要目标）。 */
      function syncFormVisibility() {
        var needValue = !!ALERT_VALUE_TYPES[type.value];
        op.style.display = needValue ? "" : "none";
        value.style.display = needValue ? "" : "none";
      }
      type.addEventListener("change", function () {
        state.alerts.draftType = type.value;
        syncFormVisibility();
      });
      addBtn.addEventListener("click", function () {
        var payload = {
          type: type.value,
          target: (target.value || "").trim(),
          note: note.value || ""
        };
        if (!payload.target) {
          toast(t("alr.needTarget"));
          return;
        }
        if (ALERT_VALUE_TYPES[type.value]) {
          payload.op = op.value;
          if (value.value === "" || isNaN(Number(value.value))) {
            toast(t("alr.needValue"));
            return;
          }
          payload.value = Number(value.value);
        }
        alertAddRule(payload).then(function () {
          toast(t("alr.addedToast"));
          target.value = "";
          value.value = "";
          note.value = "";
          paintAlertsRules();
        }).catch(function (error) {
          toast(error.message || t("state.errorHint"));
        });
      });
      [type, target, op, value, note].forEach(function (node) { form.appendChild(node); });
      form.appendChild(addBtn);
      body.appendChild(form);
      syncFormVisibility();

      // —— 规则列表 ——
      var rules = data.rules || [];
      if (!rules.length) {
        body.appendChild(el("div", "pg-state", t("alr.emptyRules")));
        body.setAttribute("data-state", "empty");
        return;
      }
      var list = el("div", "alr-list");
      rules.forEach(function (rule) {
        var line = el("div", "alr-item rule" + (rule.enabled ? "" : " disabled"));
        line.appendChild(el("i", rule.enabled ? "dot" : "dot"));
        var main = el("div", "alr-main");
        main.appendChild(el("span", "alr-type", rule.type_label || labels[rule.type] || rule.type));
        main.appendChild(el("span", "alr-target", rule.target || ""));
        var condText = (rule.op === "gte" ? "≥ " : rule.op === "lte" ? "≤ " : "")
          + (rule.value !== undefined && rule.value !== null ? fmt(rule.value, 2) : "");
        main.appendChild(el("div", "alr-msg", condText + (rule.note ? " · " + rule.note : "")));
        line.appendChild(main);
        var toggle = el("button", "pg-btn", rule.enabled ? t("alr.disableBtn") : t("alr.enableBtn"));
        toggle.type = "button";
        toggle.addEventListener("click", function () {
          alertPatchRule(rule.id, { enabled: !rule.enabled }).then(function () {
            paintAlertsRules();
          }).catch(function (error) {
            toast(error.message || t("state.errorHint"));
          });
        });
        var remove = el("button", "pg-btn", t("alr.deleteBtn"));
        remove.type = "button";
        remove.addEventListener("click", function () {
          alertDeleteRule(rule.id).then(function () {
            toast(t("alr.deletedToast"));
            paintAlertsRules();
          }).catch(function (error) {
            toast(error.message || t("state.errorHint"));
          });
        });
        line.appendChild(toggle);
        line.appendChild(remove);
        list.appendChild(line);
      });
      body.appendChild(list);

      // —— 评估说明 + 手动立即评估 ——
      var footBar = el("div", "page-toolbar");
      var evalBtn = el("button", "pg-btn", t("alr.evalNowBtn"));
      evalBtn.type = "button";
      evalBtn.addEventListener("click", function () {
        evalBtn.disabled = true;
        alertEvaluate(true).then(function (result) {
          toast(t("alr.evalDoneToast", { count: result.new_events || 0 }));
          if (result.new_events) {
            paintAlertsEvents();
          }
        }).catch(function (error) {
          toast(error.message || t("state.errorHint"));
        }).finally(function () {
          evalBtn.disabled = false;
        });
      });
      footBar.appendChild(evalBtn);
      footBar.appendChild(el("span", "pg-card-sub", t("alr.throttleNote")));
      body.appendChild(footBar);
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      P.setError(body, error.message || t("state.errorHint"), paintAlertsRules);
      body.setAttribute("data-state", "error");
    });
  }

  /** POST /api/alerts（新增规则）。 */
  function alertAddRule(payload) {
    return window.fetch("/api/alerts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (response) {
      if (!response.ok) {
        throw new Error("请求失败（HTTP " + response.status + "）");
      }
      return response.json();
    }).then(function (body) {
      if (body && body.error) {
        throw new Error(body.error);
      }
      return body.data || {};
    });
  }

  /** PATCH /api/alerts/{id}。 */
  function alertPatchRule(ruleId, fields) {
    return window.fetch("/api/alerts/" + encodeURIComponent(ruleId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields)
    }).then(function (response) {
      if (!response.ok) {
        throw new Error("请求失败（HTTP " + response.status + "）");
      }
      return response.json();
    }).then(function (body) {
      if (body && body.error) {
        throw new Error(body.error);
      }
      return body.data || {};
    });
  }

  /** DELETE /api/alerts/{id}。 */
  function alertDeleteRule(ruleId) {
    return window.fetch("/api/alerts/" + encodeURIComponent(ruleId), { method: "DELETE" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("请求失败（HTTP " + response.status + "）");
        }
        return response.json();
      })
      .then(function (body) {
        if (body && body.error) {
          throw new Error(body.error);
        }
        return body.data || {};
      });
  }

  /** POST /api/alerts/evaluate?force=true（手动立即评估）。 */
  function alertEvaluate(force) {
    return window.fetch("/api/alerts/evaluate?force=" + (force ? "true" : "false"), { method: "POST" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("请求失败（HTTP " + response.status + "）");
        }
        return response.json();
      })
      .then(function (body) {
        if (body && body.error) {
          throw new Error(body.error);
        }
        return body.data || {};
      });
  }

  /** 顶栏铃铛未读徽标更新（其他页面轮询也复用）。 */
  function updateBell(unread) {
    var badge = document.getElementById("alerts-unread");
    if (!badge) {
      return;
    }
    var count = Number(unread) || 0;
    badge.textContent = count > 99 ? "99+" : String(count);
    badge.hidden = count === 0;
  }

  /** 铃铛轮询：120s 拉未读数 + 触发一次后端评估（后端 60s/10min/6h 节流兜底）。
   * 仅页面可见时轮询；异常静默（不打扰用户）。
   */

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.ALERT_VALUE_TYPES = ALERT_VALUE_TYPES;
  P.alertAddRule = alertAddRule;
  P.alertDeleteRule = alertDeleteRule;
  P.alertEvaluate = alertEvaluate;
  P.alertMarkRead = alertMarkRead;
  P.alertPatchRule = alertPatchRule;
  P.paintAlertsEvents = paintAlertsEvents;
  P.paintAlertsRules = paintAlertsRules;
  P.renderAlerts = renderAlerts;
  P.updateBell = updateBell;
})(window.Pages = window.Pages || {});

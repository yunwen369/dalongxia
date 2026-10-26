/* =============================================================
 * OpenBB 中文投研工作台 · Copilot 抽屉（批次 5.5）
 * 职责：对话、建议问题、未配置引导、跳设置；错误全部中文且含下一步动作。
 * 依赖接口：GET/POST /api/ai/config、POST /api/ai/chat。
 * 说明：本文件不存放任何密钥；配置由设置页写入 data/ai_config.json（api_key 为 DPAPI 密文，不入 Git）。
 * ============================================================= */

(function () {
  "use strict";

  // P1-13：共享工具别名（el/t 原本各 IIFE 重复定义于此）
  var el = window.Utils.el;
  var t = window.Utils.t;
  var toast = window.Utils.toast;

  var history = []; // [{role, content}]
  var configured = false;

  // 建议问题：覆盖板块/个股/基金/新闻四类常用场景
  var SUGGESTIONS = [
    t("copilot.suggest1"),
    t("copilot.suggest2"),
    t("copilot.suggest3"),
    t("copilot.suggest4")
  ];

  /** 打开设置页（AI 配置在「设置 → AI 设置」中维护）。 */
  function gotoSetting() {
    window.location.hash = "#/settings";
    toast(t("copilot.openedSetting"));
  }

  /** 构造一条消息气泡。
   * @param {*} role 消息角色（user/assistant）
   * @param {string} text 文本内容
   * @param {*} extraClass 附加 CSS 类名
   * @returns {*} 处理结果
   */
  function bubble(role, text, extraClass) {
    var item = el("div", "copilot-msg " + (role === "user" ? "user" : "assistant") + (extraClass ? " " + extraClass : ""));
    item.appendChild(el("div", "copilot-msg-role", role === "user" ? t("copilot.roleUser") : t("copilot.roleAssistant")));
    item.appendChild(el("div", "copilot-msg-body", text));
    return item;
  }

  /** 渲染消息列表。
   * @param {HTMLElement} container DOM 容器节点
   */
  function renderMessages(container) {
    var list = container.querySelector(".copilot-messages");
    if (!list) {
      return;
    }
    list.innerHTML = "";
    if (!history.length) {
      var empty = el("div", "copilot-empty");
      empty.appendChild(el("div", "copilot-empty-title", t("copilot.emptyTitle")));
      SUGGESTIONS.forEach(function (text) {
        var button = el("button", "copilot-suggest", text);
        button.type = "button";
        button.addEventListener("click", function () {
          send(text);
        });
        empty.appendChild(button);
      });
      list.appendChild(empty);
      return;
    }
    history.forEach(function (item) {
      list.appendChild(bubble(item.role, item.content));
    });
    list.scrollTop = list.scrollHeight;
  }

  /** 发送消息并渲染回复。
   * @param {string} text 文本内容
   * @returns {*} 处理结果
   */
  function send(text) {
    var container = document.getElementById("copilot");
    if (!container) {
      return;
    }
    var input = container.querySelector(".copilot-input");
    var button = container.querySelector(".copilot-send");
    var message = (text || (input && input.value) || "").trim();
    if (!message) {
      toast(t("copilot.inputRequired"));
      return;
    }
    history.push({ role: "user", content: message });
    if (input) {
      input.value = "";
    }
    renderMessages(container);
    if (button) {
      button.disabled = true;
      button.textContent = t("copilot.thinking");
    }
    var list = container.querySelector(".copilot-messages");
    var pending = bubble("assistant", t("copilot.generating"), "pending");
    if (list) {
      list.appendChild(pending);
      list.scrollTop = list.scrollHeight;
    }

    fetch("/api/ai/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history.slice(-10) })
    })
      .catch(function () {
        throw new Error(t("error.network"));
      })
      .then(function (response) {
        return response.json();
      })
      .then(function (payload) {
        if (payload.error) {
          var error = new Error(payload.error);
          error.code = payload.error_code;
          throw error;
        }
        history.push({ role: "assistant", content: payload.data.reply || t("copilot.emptyReply") });
        renderMessages(container);
      })
      .catch(function (error) {
        renderMessages(container);
        var listNode = container.querySelector(".copilot-messages");
        if (!listNode) {
          return;
        }
        var tip = error.code === "NOT_CONFIGURED"
          ? t("copilot.aiNotConfigured")
          : t("error.unknown") + error.message;
        var box = bubble("assistant", tip, "warn");
        if (error.code === "NOT_CONFIGURED") {
          var link = el("button", "copilot-suggest", t("copilot.goToSetting"));
          link.type = "button";
          link.addEventListener("click", gotoSetting);
          box.appendChild(link);
        }
        listNode.appendChild(box);
        listNode.scrollTop = listNode.scrollHeight;
      })
      .then(function () {
        if (button) {
          button.disabled = false;
          button.textContent = t("copilot.send");
        }
      });
  }

  /** 初始化抽屉：替换占位内容为完整对话界面。
   * @returns {*} 处理结果
   */
  function init() {
    var drawer = document.getElementById("copilot");
    if (!drawer) {
      return;
    }
    var body = drawer.querySelector(".copilot-body");
    if (!body) {
      return;
    }
    body.innerHTML = "";
    body.className = "copilot-body copilot-body-chat";

    var status = el("div", "copilot-status", t("copilot.notConfiguredStatus"));
    body.appendChild(status);

    var messages = el("div", "copilot-messages");
    body.appendChild(messages);

    var composer = el("div", "copilot-composer");
    var input = document.createElement("textarea");
    input.className = "copilot-input";
    input.rows = 2;
    input.placeholder = t("copilot.inputPlaceholder");
    composer.appendChild(input);
    var sendButton = el("button", "pg-btn copilot-send", t("copilot.send"));
    sendButton.type = "button";
    sendButton.addEventListener("click", function () {
      send();
    });
    input.addEventListener("keydown", function (event) {
      if (event.ctrlKey && event.key === "Enter") {
        event.preventDefault();
        send();
      }
    });
    composer.appendChild(sendButton);
    body.appendChild(composer);

    // 读取配置状态（仅用于顶部提示，不显示密钥）
    fetch("/api/ai/config", { cache: "no-store" })
      .then(function (response) {
        return response.json();
      })
      .then(function (payload) {
        var data = payload.data || {};
        configured = Boolean(data.configured);
        status.textContent = configured
          ? t("copilot.configured", { model: data.model || t("copilot.modelEmpty"), key: data.api_key || t("copilot.keyMasked") })
          : t("copilot.notConfiguredStatus");
        status.className = "copilot-status " + (configured ? "ok" : "warn");
      })
      .catch(function () {
        status.textContent = t("copilot.configFailed");
      });

    renderMessages(drawer);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

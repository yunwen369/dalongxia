/* =============================================================
 * OpenBB 中文投研工作台 · Copilot 抽屉（批次 5.5）
 * 职责：对话、建议问题、未配置引导、跳设置；错误全部中文且含下一步动作。
 * 依赖接口：GET/POST /api/ai/config、POST /api/ai/chat。
 * 说明：本文件不存放任何密钥；配置由设置页写入 data/ai_config.json（不入 Git）。
 * ============================================================= */

(function () {
  "use strict";

  // 建议问题：覆盖板块/个股/基金/新闻四类常用场景
  var SUGGESTIONS = [
    "帮我总结今天半导体板块的资金流向",
    "中微公司最近的财务趋势有什么变化",
    "东方人工智能主题混合C 的回撤情况如何",
    "今天有哪些利好存储芯片的新闻"
  ];

  var history = []; // [{role, content}]
  var configured = false;

  /** 创建元素（textContent 防注入）。
   * @param {string} tag HTML 标签名
   * @param {string} cls CSS 类名
   * @param {string} text 文本内容
   * @returns {*} 处理结果
   */
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) {
      node.className = cls;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  /** 打开设置页（设置视图尚未实现时给轻提示，不报错）。 */
  function gotoSetting() {
    window.location.hash = "#/settings";
    showToast("设置页将在阶段 6 后续接入，可先通过 .env 或 data/ai_config.json 配置");
  }

  /** 轻提示（复用全局 #toast）。
   * @param {string} message 提示文案（中文）
   */
  function showToast(message) {
    var node = document.getElementById("toast");
    if (!node) {
      return;
    }
    node.textContent = message;
    node.classList.add("show");
    window.clearTimeout(showToast._timer);
    showToast._timer = window.setTimeout(function () {
      node.classList.remove("show");
    }, 2600);
  }

  /** 构造一条消息气泡。
   * @param {*} role 消息角色（user/assistant）
   * @param {string} text 文本内容
   * @param {*} extraClass 附加 CSS 类名
   * @returns {*} 处理结果
   */
  function bubble(role, text, extraClass) {
    var item = el("div", "copilot-msg " + (role === "user" ? "user" : "assistant") + (extraClass ? " " + extraClass : ""));
    item.appendChild(el("div", "copilot-msg-role", role === "user" ? "我" : "Copilot"));
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
      empty.appendChild(el("div", "copilot-empty-title", "可以这样问我"));
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
      showToast("请输入问题后再发送");
      return;
    }
    history.push({ role: "user", content: message });
    if (input) {
      input.value = "";
    }
    renderMessages(container);
    if (button) {
      button.disabled = true;
      button.textContent = "思考中…";
    }
    var list = container.querySelector(".copilot-messages");
    var pending = bubble("assistant", "正在生成回复…", "pending");
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
        throw new Error("网络连接失败，请检查网络后重试。");
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
        history.push({ role: "assistant", content: payload.data.reply || "（空回复）" });
        renderMessages(container);
      })
      .catch(function (error) {
        renderMessages(container);
        var listNode = container.querySelector(".copilot-messages");
        if (!listNode) {
          return;
        }
        var tip = error.code === "NOT_CONFIGURED"
          ? "尚未配置 AI：请到「设置 → AI 设置」填写 base_url、api_key、model。"
          : "调用失败：" + error.message;
        var box = bubble("assistant", tip, "warn");
        if (error.code === "NOT_CONFIGURED") {
          var link = el("button", "copilot-suggest", "去设置");
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
          button.textContent = "发送";
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

    var status = el("div", "copilot-status", "未配置模型接口");
    body.appendChild(status);

    var messages = el("div", "copilot-messages");
    body.appendChild(messages);

    var composer = el("div", "copilot-composer");
    var input = document.createElement("textarea");
    input.className = "copilot-input";
    input.rows = 2;
    input.placeholder = "输入问题，Ctrl+Enter 发送";
    composer.appendChild(input);
    var sendButton = el("button", "pg-btn copilot-send", "发送");
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
          ? "已配置：" + (data.model || "模型未填写") + "（密钥 " + (data.api_key || "已掩码") + "）"
          : "未配置模型接口，发送将提示去设置";
        status.className = "copilot-status " + (configured ? "ok" : "warn");
      })
      .catch(function () {
        status.textContent = "配置状态读取失败，可稍后刷新";
      });

    renderMessages(drawer);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

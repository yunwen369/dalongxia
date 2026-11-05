/* =============================================================
 * OpenBB 中文投研工作台 · pages/yjb.js
 * 养基宝绑定
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
  var fmtPct = P.fmtPct;
  var state = P.state;
  var t = P.t;
  var toast = P.toast;

  // =============================================================
  // 十二、#/yjb 养基宝绑定（阶段 7）
  // 合规告知书 → 扫码绑定（2s 轮询）→ 增强开关 → 解绑；token 前端不可见
  // =============================================================
  var yjbTimer = null;
  // 估算展示偏好（Y-05/Y-06 角标「一键关闭」）：true=盘中估算口径，false=回退 T+1 官方净值。
  // 只影响展示口径，不关闭数据通道；持久化在本机 localStorage，用户可随时恢复。
  var yjbPrefs = { estimates: true };
  // R6-11 / F6-12：账户维度单一状态源 —— 账户概览与持仓估值两张卡共用，
  // 切换一次两卡同时变（原缺陷：概览卡没有切换控件，三账户永远合并显示）。
  var yjbAccount = "all";
  /** 账户概览最近一次接口数据（切换账户时本地重绘，不重复请求养基宝）。 */
  var yjbCollectCache = null;
  try {
    yjbPrefs.estimates = window.localStorage.getItem("openbb-yjb-estimates") !== "0";
  } catch (error) {
    yjbPrefs.estimates = true; // 隐私模式/禁用存储时退回默认（开启估算）
  }

  /** 写入估算展示偏好。
   * @param {boolean} enabled 是否启用盘中估算口径
   * @returns {void} 无返回值
   */
  function setYjbEstimates(enabled) {
    yjbPrefs.estimates = !!enabled;
    try {
      window.localStorage.setItem("openbb-yjb-estimates", yjbPrefs.estimates ? "1" : "0");
    } catch (error) {
      // 存储不可用不影响本次会话的展示口径
    }
  }

  /** 估算口径角标（常驻）：非官方·拟合估算·仅供参考 + 一键切换 T+1。
   * 关闭后 Y-05 今日收益、Y-06 涨跌幅列一律留空（不补 0），并标注 T+1 官方净值为准。
   * @returns {HTMLElement} 角标节点
   */
  function yjbEstimateBadge() {
    var wrap = el("span", "yjb-est-badge");
    var text = yjbPrefs.estimates ? t("yjb.estimates.badge") : t("yjb.estimates.offBadge");
    wrap.appendChild(el("span", "badge-src " + (yjbPrefs.estimates ? "state-degraded" : ""), text));
    var button = el("button", "yjb-est-close", yjbPrefs.estimates ? t("yjb.estimates.close") : t("yjb.estimates.restore"));
    button.type = "button";
    button.title = yjbPrefs.estimates ? t("yjb.estimates.closeTip") : t("yjb.estimates.restoreTip");
    button.addEventListener("click", function () {
      setYjbEstimates(!yjbPrefs.estimates);
      toast(yjbPrefs.estimates ? t("yjb.estimates.onToast") : t("yjb.estimates.offToast"));
      loadYjbCollect();
      loadYjbHoldings(yjbAccount);
    });
    wrap.appendChild(button);
    return wrap;
  }

  /** 首启向导（Y-00）：冻结包 data 缺 token/签名盐时显式引导，禁止静默空态。
   * 「不得显示已绑定」由 status.bound 决定；本卡只在 setup_required 时出现。
   * @param {Object} data /api/fund/yjb/status 的 data 段
   * @returns {void} 无返回值
   */
  function paintYjbWizard(data) {
    var card = document.getElementById("Y-00");
    var body = document.getElementById("y00-body");
    if (!card || !body) {
      return;
    }
    if (!data || !data.setup_required) {
      card.hidden = true;
      return;
    }
    card.hidden = false;
    body.innerHTML = "";
    var missing = data.setup_missing || [];
    var missText = missing.map(function (key) {
      return key === "signing_salt" ? t("yjb.wizard.missSecret") : t("yjb.wizard.missToken");
    }).join(t("yjb.wizard.and"));
    body.appendChild(el("div", "pg-state-title", t("yjb.wizard.title") + " " + missText));
    body.appendChild(el("div", "pg-state-hint", t("yjb.wizard.desc")));
    var list = el("ol", "yjb-wizard-steps");
    (data.setup_steps || []).forEach(function (step) {
      list.appendChild(el("li", "", step));
    });
    body.appendChild(list);
    var row = el("div", "page-toolbar");
    // 第一步：注入签名盐（走设置页，DPAPI 加密，掩码输入）
    var toSettings = el("button", "pg-btn primary", t("yjb.wizard.toSettings"));
    toSettings.type = "button";
    toSettings.addEventListener("click", function () {
      window.__yjbFocusSecret = true; // 设置页渲染后自动滚动到「养基宝连接」并聚焦密码框
      window.location.hash = "#/settings";
    });
    row.appendChild(toSettings);
    // 第二步：扫码授权（签名盐已配置才可用，避免点了必然失败）
    var toScan = el("button", "pg-btn", t("yjb.wizard.toScan"));
    toScan.type = "button";
    if (!data.secret_configured) {
      toScan.disabled = true;
      toScan.title = t("yjb.wizard.needSecretFirst");
    }
    toScan.addEventListener("click", function () {
      var target = document.getElementById("Y-03");
      if (target && target.scrollIntoView) {
        target.scrollIntoView({ block: "start" });
      }
      var qr = document.querySelector("#y03-body .pg-btn");
      if (qr && !qr.disabled) {
        qr.click();
      }
    });
    row.appendChild(toScan);
    row.appendChild(el("span", "pg-spacer"));
    if (data.data_dir) {
      row.appendChild(el("span", "pg-card-sub", t("yjb.wizard.dataDir") + data.data_dir));
    }
    body.appendChild(row);
    body.setAttribute("data-state", "empty");
  }

  /** 渲染养基宝页。 */
  function renderYjb() {
    var container = document.getElementById(VIEW_IDS.yjb);
    container.innerHTML = "";
    var grid = el("div", "pg-grid");
    // Y-00 首启向导：仅当凭据未齐（缺 token 或缺签名盐）时显示，默认隐藏
    var g0 = P.buildCard("Y-00", t("yjb.wizard.cardTitle"), "g-12", t("yjb.wizard.cardSub"));
    g0.body.id = "y00-body";
    g0.card.hidden = true;
    grid.appendChild(g0.card);
    var g1 = P.buildCard("Y-01", t("yjb.consentTitle"), "g-4", t("yjb.consentSub"));
    g1.body.id = "y01-body";
    var g2 = P.buildCard("Y-02", t("yjb.statusTitle"), "g-4", t("yjb.statusSub"));
    g2.body.id = "y02-body";
    var g3 = P.buildCard("Y-03", t("yjb.bindTitle"), "g-4", t("yjb.bindSub"));
    g3.body.id = "y03-body";
    var g4 = P.buildCard("Y-04", t("yjb.estimateTitle"), "g-12", t("yjb.estimateSub"));
    g4.body.id = "y04-body";
    // Y-05 / Y-06：养基宝账户概览与持仓估值（数据来自 account_collect / fund_hold）
    var g5 = P.buildCard("Y-05", t("yjb.collectTitle"), "g-12", t("yjb.collectSub"));
    g5.body.id = "y05-body";
    var g6 = P.buildCard("Y-06", t("yjb.holdTitle"), "g-12", t("yjb.holdSub"));
    g6.body.id = "y06-body";
    [g1, g2, g3, g4, g5, g6].forEach(function (item) { grid.appendChild(item.card); });
    container.appendChild(grid);
    var body = document.getElementById("y04-body");
    body.innerHTML = "";
    var toolbar = el("div", "page-toolbar");
    toolbar.appendChild(el("span", "pg-card-sub", t("fund.codeLabel")));
    var input = document.createElement("input");
    input.id = "yjb-code";
    input.value = "017811";
    input.style.cssText = "width:110px;background:var(--bg-panel);border:1px solid var(--border);color:var(--text);border-radius:5px;padding:4px 8px;font-family:var(--font-mono)";
    toolbar.appendChild(input);
    var query = el("button", "pg-btn", t("yjb.queryBtn"));
    query.type = "button";
    query.addEventListener("click", loadYjbEstimate);
    toolbar.appendChild(query);
    toolbar.appendChild(el("span", "pg-spacer"));
    toolbar.appendChild(el("span", "badge-src state-degraded", t("yjb.unofficial")));
    body.appendChild(toolbar);
    body.appendChild(el("div", "pg-card-sub", t("yjb.disclaimer")));
    var result = el("div", "");
    result.id = "yjb-result";
    body.appendChild(result);
    // 该区块是本地表单，不发起加载请求，显式收口为默认态（四态巡检要求）
    body.setAttribute("data-state", "default");
    loadYjbStatus();
    loadYjbCollect();
    loadYjbHoldings(yjbAccount);
  }

  /** 加载连接器状态与告知书。 */
  function loadYjbStatus() {
    api("/api/fund/yjb/status", {})
      .then(function (data) {
        // 首启向导先出：凭据未齐时用户第一眼就能看到"该做什么"，而不是空白区块
        paintYjbWizard(data);
        if (document.getElementById("y01-body")) {
          paintYjbConsent(data);
        }
        if (document.getElementById("y02-body")) {
          paintYjbStatus(data);
        }
        if (document.getElementById("y03-body")) {
          paintYjbBind(data);
        }
      })
      .catch(function (error) {
        var target = document.getElementById("y02-body") || document.getElementById("set-yjb-body");
        if (target) {
          P.setError(target, error.message, loadYjbStatus);
        }
      });
  }

  /** 告知书 + 确认按钮。
   * @param {Object} data 数据主体（接口 data 段）
   */
  function paintYjbConsent(data) {
    var body = document.getElementById("y01-body");
    body.innerHTML = "";
    var accepted = (data.consent || {}).accepted === true;
    var text = el("div", "pg-card-sub", data.consent_text || "");
    text.style.whiteSpace = "pre-wrap";
    text.style.maxHeight = "220px";
    text.style.overflow = "auto";
    body.appendChild(text);
    var row = el("div", "page-toolbar");
    var tip = el("span", "chip " + (accepted ? "" : "down"), accepted ? t("yjb.accepted") : t("yjb.unconfirmed"));
    row.appendChild(tip);
    if (!accepted) {
      var agree = el("button", "pg-btn primary", t("yjb.agree"));
      agree.type = "button";
      agree.addEventListener("click", function () {
        P.apiSend("POST", "/api/fund/yjb/consent", { accepted: true }).then(function () {
          toast(t("yjb.consentRecorded"));
          loadYjbStatus();
        });
      });
      row.appendChild(agree);
    }
    body.appendChild(row);
    body.setAttribute("data-state", "default");
  }

  /** 状态 + 增强开关 + 解绑。
   * @param {Object} data 数据主体（接口 data 段）
   */
  function paintYjbStatus(data) {
    var body = document.getElementById("y02-body");
    body.innerHTML = "";
    var grid = el("div", "pg-metric-grid");
    [[t("yjb.kv.binding"), data.bound ? t("yjb.bound") : t("yjb.unbound")], [t("yjb.kv.enhanced"), data.enabled ? t("yjb.enabled") : t("yjb.disabled")],
     [t("yjb.kv.secret"), data.secret_configured ? t("yjb.secretConfigured") : t("yjb.secretUnconfigured")],
     [t("yjb.kv.security"), data.secure_storage ? t("yjb.secure.dpapi") : t("yjb.unavailable")]].forEach(function (pair) {
      var cell = el("div", "pg-metric-cell");
      cell.appendChild(el("div", "pg-metric-label", pair[0]));
      cell.appendChild(el("div", "pg-metric-value", pair[1]));
      grid.appendChild(cell);
    });
    body.appendChild(grid);
    var row = el("div", "page-toolbar");
    var toggle = el("button", "pg-btn", data.enabled ? t("yjb.toggle.offBtn") : t("yjb.toggle.onBtn"));
    toggle.type = "button";
    toggle.addEventListener("click", function () {
      P.apiSend("POST", "/api/fund/yjb/toggle", { enabled: !data.enabled })
        .then(function () { toast(data.enabled ? t("yjb.toggle.onToast") : t("yjb.toggle.offToast")); loadYjbStatus(); })
        .catch(function (error) { toast(error.message); });
    });
    row.appendChild(toggle);
    if (data.bound) {
      var unbind = el("button", "pg-btn", t("yjb.unbind"));
      unbind.type = "button";
      unbind.addEventListener("click", function () {
        // RT-01 整改：解绑不可逆，必须先二次确认弹窗，再带 confirm=true 提交
        if (!window.UI) {
          return;
        }
        window.UI.modal({
          title: t("yjb.unbind"),
          message: t("yjb.unbindWarn"),
          confirmText: t("yjb.unbindConfirm"),
          danger: true,
          /** 确认解绑：显式提交 confirm=true（后端无此标识一律拒绝执行）。
           * @returns {void} 无返回值
           */
          onConfirm: function () {
            P.apiSend("POST", "/api/fund/yjb/unbind", { confirm: true }).then(function () {
              toast(t("yjb.unbindToast")); loadYjbStatus();
            }).catch(function (error) { toast(error.message); });
          }
        });
      });
      row.appendChild(unbind);
    }
    body.appendChild(row);
    body.appendChild(el("div", "pg-card-sub", data.notice || ""));
    body.setAttribute("data-state", "default");
  }

  /** 签名盐配置区（R1）：仅在未配置或用户点「更新密钥」时展开。
   * 密码框输入，提交后走 POST /api/fund/yjb/secret（后端 DPAPI 加密，不回显）。
   * RT-01 整改：已配置时追加「清除签名盐」危险按钮（二次确认 + confirm=true）。
   * @param {HTMLElement} host 容器节点
   * @param {boolean} [secretConfigured] 当前是否已配置签名盐（决定是否显示清除按钮）
   * @param {Function} [onCleared] 清除成功后的回调（默认刷新养基宝页状态）
   * @param {Function} [onSaved] 保存成功后的回调（默认刷新养基宝页状态）
   * @returns {void} 无返回值
   */
  function buildYjbSecretForm(host, secretConfigured, onCleared, onSaved) {
    host.innerHTML = "";
    var row = el("div", "page-toolbar");
    var newInput = document.createElement("input");
    newInput.type = "password";
    newInput.placeholder = t("yjb.secret.newPlaceholder");
    newInput.autocomplete = "off";
    newInput.style.cssText = "flex:1;min-width:120px;background:var(--bg-panel);" +
      "border:1px solid var(--border);color:var(--text);border-radius:5px;padding:4px 8px";
    var oldInput = document.createElement("input");
    oldInput.type = "password";
    oldInput.placeholder = t("yjb.secret.oldPlaceholder");
    oldInput.autocomplete = "off";
    oldInput.style.cssText = newInput.style.cssText;
    row.appendChild(newInput);
    row.appendChild(oldInput);
    var save = el("button", "pg-btn primary", t("yjb.secret.save"));
    save.type = "button";
    save.addEventListener("click", function () {
      var payload = { new: newInput.value, old: oldInput.value };
      if (!payload.new && !payload.old) {
        toast(t("yjb.secret.empty"));
        return;
      }
      save.disabled = true;
      P.apiSend("POST", "/api/fund/yjb/secret", payload).then(function () {
        // 立即清空输入框，避免明文停留在 DOM
        newInput.value = "";
        oldInput.value = "";
        toast(t("yjb.secret.saved"));
        if (typeof onSaved === "function") {
          onSaved();
        } else {
          loadYjbStatus();
        }
      }).catch(function (error) {
        save.disabled = false;
        toast(t("yjb.secret.saveFailed") + (error.message || ""));
      });
    });
    row.appendChild(save);
    host.appendChild(row);
    // RT-01 整改：清除签名盐属不可逆操作，独立危险按钮 + 二次确认弹窗；
    // 仅当已配置时才显示（未配置时无需清除）
    if (secretConfigured) {
      var clearRow = el("div", "page-toolbar");
      var clearBtn = el("button", "pg-btn danger", t("yjb.secret.clear"));
      clearBtn.type = "button";
      clearBtn.addEventListener("click", function () {
        if (!window.UI) {
          return;
        }
        window.UI.modal({
          title: t("yjb.secret.clear"),
          message: t("yjb.secret.clearWarn"),
          confirmText: t("yjb.secret.clear"),
          danger: true,
          /** 确认清除：仅提交 confirm=true（不带 new/old，后端据此判定清除意图）。
           * @returns {void} 无返回值
           */
          onConfirm: function () {
            P.apiSend("POST", "/api/fund/yjb/secret", { confirm: true }).then(function () {
              toast(t("yjb.secret.clearToast"));
              if (typeof onCleared === "function") { onCleared(); } else { loadYjbStatus(); }
            }).catch(function (error) { toast(t("yjb.secret.clearFailed") + (error.message || "")); });
          }
        });
      });
      clearRow.appendChild(clearBtn);
      host.appendChild(clearRow);
    }
    host.appendChild(el("div", "pg-card-sub", t("yjb.secret.note")));
  }

  /** 二维码与轮询。
   * @param {Object} data 数据主体（接口 data 段）
   */
  function paintYjbBind(data) {
    var body = document.getElementById("y03-body");
    body.innerHTML = "";
    if (!data.secret_configured) {
      var hint = el("div", "pg-state");
      hint.appendChild(el("div", "pg-state-title", t("yjb.qrUnavailable")));
      hint.appendChild(el("div", "pg-state-hint", t("yjb.qrHint")));
      body.appendChild(hint);
      // R1：提供加密输入框（DPAPI 落盘），未提供真实盐前仍保持「待配置」态
      var formHost = el("div", "");
      buildYjbSecretForm(formHost, false);
      body.appendChild(formHost);
      body.setAttribute("data-state", "empty");
      return;
    }
    var button = el("button", "pg-btn", t("yjb.qrButton"));
    button.type = "button";
    var box = el("div", "");
    button.addEventListener("click", function () {
      api("/api/fund/yjb/qrcode", {}).then(function (session) {
        box.innerHTML = "";
        if (session.image_base64) {
          var img = document.createElement("img");
          img.src = "data:image/png;base64," + session.image_base64;
          img.style.cssText = "width:200px;height:200px;background:#fff;border-radius:8px";
          box.appendChild(img);
        }
        var state = el("div", "pg-card-sub", t("yjb.qrWaiting"));
        state.id = "yjb-poll-state";
        box.appendChild(state);
        // R9：轮询次数与间隔由后端 expires_in 推导，不再硬编码 60 次
        var pollMs = 2000;
        var ttlSec = Number(session.expires_in) > 0 ? Number(session.expires_in) : 120;
        var maxAttempts = Math.max(1, Math.ceil((ttlSec * 1000) / pollMs));
        var attempts = 0;
        window.clearInterval(yjbTimer);
        yjbTimer = window.setInterval(function () {
          attempts += 1;
          if (attempts > maxAttempts) {
            window.clearInterval(yjbTimer);
            state.textContent = t("yjb.qrTimeout");
            return;
          }
          api("/api/fund/yjb/poll", { session_id: session.session_id }).then(function (status) {
            state.textContent = status.message || status.state;
            if (status.state === "success") {
              window.clearInterval(yjbTimer);
              P.apiSend("POST", "/api/fund/yjb/bind", { session_id: session.session_id })
                .then(function () { toast(t("yjb.bindSuccess")); loadYjbStatus(); })
                .catch(function (error) { state.textContent = t("yjb.bindFailed") + error.message; });
            } else if (status.state === "expired" || status.state === "timeout" || status.state === "error") {
              window.clearInterval(yjbTimer);
            }
          });
        }, pollMs);
      }).catch(function (error) {
        box.textContent = error.message;
      });
    });
    body.appendChild(button);
    body.appendChild(box);
    // 已配置时提供「更新密钥」入口：社区逆向盐随 App 版本漂移，需可随时替换
    var update = el("button", "pg-btn", t("yjb.secret.update"));
    update.type = "button";
    var updateHost = el("div", "");
    update.addEventListener("click", function () {
      if (updateHost.childNodes.length) {
        updateHost.innerHTML = "";
        return;
      }
      // 已配置态：显示清除按钮；清除成功后整体刷新回到「待配置」引导
      buildYjbSecretForm(updateHost, true, function () { loadYjbStatus(); });
    });
    body.appendChild(update);
    body.appendChild(updateHost);
    body.setAttribute("data-state", "default");
  }

  /** Y-05：养基宝账户概览（总资产 / 今日估算收益 / 各账户明细）。
   * @returns {void} 无返回值（异步渲染）
   */
  function loadYjbCollect() {
    var body = document.getElementById("y05-body");
    if (!body) {
      return;
    }
    P.setLoading(body);
    api("/api/fund/yjb/collect", { estimates: yjbPrefs.estimates ? 1 : 0 }).then(function (data) {
      yjbCollectCache = data;
      paintYjbCollect(body, data);
    }).catch(function (error) {
      P.setError(body, error.message || t("state.errorHint"), function () { loadYjbCollect(); });
    });
  }

  /** 渲染养基宝账户概览（全部账户合并 或 单账户视图）。
   *
   * R6-11 / F6-12：头部 chips 与「持仓与估值」卡共用 `yjbAccount`；
   * 单账户视图下四张指标卡改用该账户数值，账户小卡高亮当前项。
   *
   * @param {HTMLElement} body 内容容器
   * @param {Object} data `/api/fund/yjb/collect` 的 data 段
   * @returns {void} 无返回值
   */
  function paintYjbCollect(body, data) {
    var card = document.getElementById("Y-05");
    P.setBadges(card, data.source, data.caliber);
    var accounts = data.accounts || [];
    if (!data.total_assets && !accounts.length) {
      P.setEmpty(body, t("yjb.notBoundHint"), function () { loadYjbCollect(); });
      return;
    }
    body.innerHTML = "";
    var bar = el("div", "page-toolbar");
    bar.appendChild(yjbEstimateBadge());
    bar.appendChild(el("span", "pg-spacer"));
    // 账户维度 chips：全部 + 各账户（与持仓卡同款，数量来自 accounts）
    if (accounts.length > 1) {
      var chips = el("div", "pg-tabs");
      var options = [{ id: "all", title: t("yjb.hold.all"), n: accounts.length }];
      accounts.forEach(function (acc) {
        options.push({ id: acc.id, title: acc.title || acc.id, n: null });
      });
      options.forEach(function (opt) {
        var current = yjbAccount === opt.id || (yjbAccount === "all" && opt.id === "all");
        var btn = el("button", "pg-tab" + (current ? " active" : ""),
          opt.title + (opt.id === "all" ? "（" + accounts.length + "）" : ""));
        btn.type = "button";
        btn.addEventListener("click", function () {
          yjbAccount = opt.id;
          // 概览用本地缓存重绘（养基宝接口 5~9s，切换不该再等一次）；
          // 持仓卡按账户重新拉取（后端按 account_id 过滤）。
          if (yjbCollectCache) {
            paintYjbCollect(body, yjbCollectCache);
          }
          loadYjbHoldings(yjbAccount);
        });
        chips.appendChild(btn);
      });
      bar.appendChild(chips);
    }
    bar.appendChild(P.timeWithBj(t("yjb.asOfBJ") + (data.as_of || t("value.none")), "", "pg-card-sub"));
    body.appendChild(bar);

    var active = null;
    if (yjbAccount !== "all") {
      active = accounts.filter(function (acc) { return acc.id === yjbAccount; })[0] || null;
    }
    var kpis = el("div", "pg-kpis");
    var items;
    if (active) {
      // 单账户视图：账户资产（成本+累计收益，后端已下发 assets）+ 今日收益/收益率 + 累计收益 + 涨跌家数
      var assets = active.assets;
      if (assets === null || assets === undefined) {
        assets = (Number(active.hold_cost) || 0) + (Number(active.hold_income) || 0);
      }
      items = [
        [t("yjb.acc.assets"), fmt(assets, 2), "flat",
          t("yjb.kpi.yuan") + " ｜ " + t("yjb.kpi.approxWan") + fmt(assets / 10000, 2) + t("yjb.kpi.wanUnit")],
        [t("yjb.kpi.todayIncome"),
          active.today_income === null || active.today_income === undefined
            ? t("value.none")
            : (active.today_income > 0 ? "+" : "") + fmt(active.today_income, 2),
          active.today_income === null || active.today_income === undefined ? "flat" : P.pctClass(active.today_income),
          yjbPrefs.estimates ? t("yjb.kpi.estimateTag") : t("yjb.kpi.estimateOffTag")],
        [t("yjb.kpi.todayRate"), fmtPct(active.today_income_rate), P.pctClass(active.today_income_rate), ""],
        [t("yjb.kpi.holdIncome"), (active.hold_income > 0 ? "+" : "") + fmt(active.hold_income, 2),
          P.pctClass(active.hold_income), t("yjb.kpi.yuan")],
        [t("yjb.kpi.upDown"), active.up + " / " + active.down, "flat", ""]
      ];
    } else {
      // 全部账户：总资产（元为主体，附「≈万元」便于快速读数）
      var totalAssets = data.total_assets;
      var wanText = (totalAssets === null || totalAssets === undefined)
        ? "" : t("yjb.kpi.approxWan") + fmt(totalAssets / 10000, 2) + t("yjb.kpi.wanUnit");
      items = [
        [t("yjb.kpi.totalAssets"), fmt(totalAssets, 2), "flat", t("yjb.kpi.yuan") + (wanText ? " ｜ " + wanText : "")],
        [t("yjb.kpi.todayIncome"),
          data.today_income === null || data.today_income === undefined
            ? t("value.none")
            : (data.today_income > 0 ? "+" : "") + fmt(data.today_income, 2),
          data.today_income === null || data.today_income === undefined ? "flat" : P.pctClass(data.today_income),
          yjbPrefs.estimates ? t("yjb.kpi.estimateTag") : t("yjb.kpi.estimateOffTag")],
        [t("yjb.kpi.todayRate"), fmtPct(data.today_income_rate), P.pctClass(data.today_income_rate), ""],
        [t("yjb.kpi.holdIncome"), (data.hold_income > 0 ? "+" : "") + fmt(data.hold_income, 2),
          P.pctClass(data.hold_income), t("yjb.kpi.yuan")],
        [t("yjb.kpi.accounts"), String(accounts.length), "flat", t("yjb.kpi.countTag")]
      ];
    }
    items.forEach(function (row) {
      var box = el("div", "pg-kpi");
      box.appendChild(el("div", "pg-kpi-k", row[0]));
      box.appendChild(el("div", "pg-kpi-v " + row[2], row[1]));
      if (row[3]) {
        box.appendChild(el("div", "pg-kpi-x", row[3]));
      }
      kpis.appendChild(box);
    });
    body.appendChild(kpis);

    if (accounts.length) {
      var list = el("div", "pg-kpi-accounts");
      accounts.forEach(function (acc) {
        var box = el("div", "pg-kpi-acc" + (active && active.id === acc.id ? " active" : ""));
        var head = el("div", "pg-kpi-acc-head");
        head.appendChild(el("span", "pg-kpi-acc-name", acc.title || acc.id));
        head.appendChild(el("span", "pg-kpi-acc-id", acc.id));
        box.appendChild(head);
        var accAssets = acc.assets;
        if (accAssets === null || accAssets === undefined) {
          accAssets = (Number(acc.hold_cost) || 0) + (Number(acc.hold_income) || 0);
        }
        var rows = [
          [t("yjb.acc.assets"), fmt(accAssets, 2), "flat"],
          [t("yjb.kpi.todayIncome"), (acc.today_income > 0 ? "+" : "") + fmt(acc.today_income, 2), P.pctClass(acc.today_income)],
          [t("yjb.kpi.todayRate"), fmtPct(acc.today_income_rate), P.pctClass(acc.today_income_rate)],
          [t("yjb.kpi.holdIncome"), (acc.hold_income > 0 ? "+" : "") + fmt(acc.hold_income, 2), P.pctClass(acc.hold_income)],
          [t("yjb.kpi.upDown"), acc.up + " / " + acc.down, "flat"]
        ];
        rows.forEach(function (r) {
          var line = el("div", "pg-kpi-acc-row");
          line.appendChild(el("span", "", r[0]));
          line.appendChild(el("b", r[2], r[1]));
          box.appendChild(line);
        });
        // 点账户小卡等于切 chips（同一 state，单一实现）
        box.addEventListener("click", function () {
          yjbAccount = acc.id;
          if (yjbCollectCache) {
            paintYjbCollect(body, yjbCollectCache);
          }
          loadYjbHoldings(yjbAccount);
        });
        list.appendChild(box);
      });
      body.appendChild(list);
    }
    body.appendChild(el("div", "pg-card-sub", t("yjb.collectNote")));
    body.setAttribute("data-state", "default");
  }

  /** Y-06：养基宝持仓与估值（支持账户 Tab 切换）。
   * @param {string} accountId 账户 id；"all" 表示全部账户
   * @returns {void} 无返回值（异步渲染）
   */
  function loadYjbHoldings(accountId) {
    var body = document.getElementById("y06-body");
    if (!body) {
      return;
    }
    P.setLoading(body);
    api("/api/fund/yjb/holdings", {
      account_id: accountId || "all",
      estimates: yjbPrefs.estimates ? 1 : 0
    }).then(function (data) {
      var card = document.getElementById("Y-06");
      P.setBadges(card, data.source, data.caliber);
      var items = data.items || [];
      body.innerHTML = "";
      var bar = el("div", "page-toolbar");
      bar.appendChild(yjbEstimateBadge());
      var tabs = [{ id: "all", title: t("yjb.hold.all"), n: data.total }];
      (data.accounts || []).forEach(function (acc) {
        var n = items.filter(function (x) { return x.account_id === acc.id; }).length;
        tabs.push({ id: acc.id, title: acc.title || acc.id, n: n });
      });
      var current = data.account_id || "all";
      tabs.forEach(function (tab) {
        var btn = el("button", "pg-btn" + (tab.id === current ? " primary" : ""),
          tab.title + (current === "all" ? "（" + tab.n + "）" : ""));
        btn.type = "button";
        btn.addEventListener("click", function () { loadYjbHoldings(tab.id); });
        bar.appendChild(btn);
      });
      bar.appendChild(el("span", "pg-spacer"));
      bar.appendChild(el("span", "badge-src state-degraded", t("yjb.unofficial")));
      body.appendChild(bar);

      if (!items.length) {
        P.setEmpty(body, t("yjb.hold.empty"), function () { loadYjbHoldings(current); });
        return;
      }
      // 表格必须渲染到子容器：renderTable 会清空目标容器 innerHTML，
      // 若直接传 body，上面的工具栏（账户 Tab / 角标 / 一键关闭）会被一并清掉。
      var tableHost = el("div", "");
      body.appendChild(tableHost);
      P.renderTable(tableHost, [
        {
          title: t("yjb.hold.col.fund"), cls: "l",
          /** 基金名称列：代码 + 简称。
           * @param {Object} row 数据行
           * @returns {HTMLElement} 单元格节点
           */
          render: function (row) {
            var box = el("span", "pg-sym");
            box.appendChild(el("b", "", row.code));
            box.appendChild(document.createTextNode(" " + (row.name || "")));
            return box;
          }
        },
        {
          title: yjbPrefs.estimates ? t("yjb.hold.col.pct") : t("yjb.hold.col.pctT1"),
          num: true,
          render: function (row) {
            // 关闭估算：估算涨跌幅一律留空（不用 T+1 净值涨幅冒充盘中估算）
            if (row.pct === null || row.pct === undefined) {
              return el("span", "flat", t("value.none"));
            }
            return el("span", P.pctClass(row.pct), fmtPct(row.pct));
          }
        },
        { title: t("yjb.hold.col.nav"), num: true, render: function (row) { return row.nav === null ? t("value.none") : fmt(row.nav, 4); } },
        { title: t("yjb.hold.col.share"), num: true, render: function (row) { return row.share === null ? t("value.none") : fmt(row.share, 2); } },
        { title: t("yjb.hold.col.cost"), num: true, render: function (row) { return row.cost === null ? t("value.none") : fmt(row.cost, 4); } },
        { title: t("yjb.hold.col.money"), num: true, render: function (row) { return row.money === null ? t("value.none") : fmt(row.money, 2); } },
        { title: t("yjb.hold.col.earn"), num: true, render: function (row) { return row.hold_earn === null ? t("value.none") : el("span", P.pctClass(row.hold_earn), fmt(row.hold_earn, 2)); } },
        { title: t("yjb.hold.col.account"), render: function (row) { return el("span", "pg-card-sub", row.account_title || row.account_id); } },
        { title: t("yjb.hold.col.asOf"), render: function (row) { return el("span", "pg-card-sub", row.as_of || t("value.none")); } }
      ], items, function (row) {
        window.location.hash = "#/fund?code=" + row.code;
      });
      if (!yjbPrefs.estimates) {
        // 关闭估算后明确告知口径回退，避免用户误以为"数据丢了"
        // 注意：必须放在 renderTable 之后（renderTable 会清空容器 innerHTML）
        body.appendChild(el("div", "pg-card-sub", t("yjb.hold.t1Hint")));
      }
      body.setAttribute("data-state", "default");
    }).catch(function (error) {
      P.setError(body, error.message || t("state.errorHint"), function () { loadYjbHoldings(accountId); });
    });
  }

  /** 查询估值（显示命中源与口径）。 */
  function loadYjbEstimate() {
    var code = (document.getElementById("yjb-code").value || "").trim();
    var box = document.getElementById("yjb-result");
    P.setLoading(box);
    api("/api/fund/yjb/realtime", { code: code })
      .then(function (data) {
        box.innerHTML = "";
        var grid = el("div", "pg-metric-grid");
        [[t("fund.estimateTitle"), fmt(data.nav, 4)], [t("fund.estimateGrowth"), fmtPct(data.day_growth)],
         [t("fund.estimateSource"), data.source || t("value.none")], [t("fund.estimateCoverage"), data.coverage ? fmt(data.coverage, 2) + "%" : t("value.none")]]
          .forEach(function (pair) {
            var cell = el("div", "pg-metric-cell");
            cell.appendChild(el("div", "pg-metric-label", pair[0]));
            cell.appendChild(el("div", "pg-metric-value", pair[1]));
            grid.appendChild(cell);
          });
        box.appendChild(grid);
        box.appendChild(el("div", "pg-card-sub", data.caliber || ""));
        box.setAttribute("data-state", "default");
      })
      .catch(function (error) { P.setError(box, error.message, loadYjbEstimate); });
  }

  // ---- 导出到 window.Pages（供兄弟模块与 init 使用） ----
  P.buildYjbSecretForm = buildYjbSecretForm;
  P.loadYjbCollect = loadYjbCollect;
  P.loadYjbEstimate = loadYjbEstimate;
  P.loadYjbHoldings = loadYjbHoldings;
  P.loadYjbStatus = loadYjbStatus;
  P.paintYjbBind = paintYjbBind;
  P.paintYjbCollect = paintYjbCollect;
  P.paintYjbConsent = paintYjbConsent;
  P.paintYjbStatus = paintYjbStatus;
  P.paintYjbWizard = paintYjbWizard;
  P.renderYjb = renderYjb;
  P.setYjbEstimates = setYjbEstimates;
  P.yjbAccount = yjbAccount;
  P.yjbCollectCache = yjbCollectCache;
  P.yjbEstimateBadge = yjbEstimateBadge;
  P.yjbPrefs = yjbPrefs;
  P.yjbTimer = yjbTimer;
})(window.Pages = window.Pages || {});

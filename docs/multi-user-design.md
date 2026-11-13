# dalongxia 多用户 · 数据隔离 · 权限体系设计（草案 v1）

> 状态：**仅设计，未改任何代码**
> 日期：2026-11-08
> 适用版本：v2.1.0 之后（下一版本 v2.2.0）
> 依据：循理 2026-11-08 五条指令（§1 逐条对应）

---

## 1. 需求拆解与验收条款

你给的五条原话，拆成 12 条可验收的硬条款。后文每个设计决策都指向这里的编号。

| # | 你的原话 | 拆解为 | 验收方式 |
|---|---|---|---|
| R1 | 需要角色区分 | ① 至少两种角色：管理员 / 普通用户 | 普通用户登录后看不到用户管理入口；调管理类接口返回 403 |
| R2 | 现有数据都是测试，到时全部删除 | ② 交付时 `data/` 下业务数据清空，只保留目录与结构 | 交付包 `data/users/` 为空，`data/shared/cache/` 为空，无任何 `.json` 业务文件 |
| R2 | 只留下软件、功能、逻辑、接口 | ③ 不写数据迁移逻辑，不做历史数据兼容层 | 代码中无 `migrate_*` 遗留分支，无旧格式读取分支 |
| R2 | 不同用户的数据必须做到完全隔离 | ④ 用户 A 无法通过任何接口读写用户 B 的持仓/自选/提醒/仪表板/配置/摘要/审计/养基宝 | 隔离测试 20 条（见 §13.3）全部通过；含越权构造请求 |
| R2 | admin 除外 | ⑤ admin 可跨用户查看与运维，且该能力不可被普通用户获得 | admin 带 `?as=<uid>` 可读他人数据；普通用户带同参数返回 403 |
| R3 | 养基宝必须各绑各的账户 | ⑥ 每个用户独立绑定、独立 token、独立凭据；A 的绑定不影响 B | 两用户绑不同账号，互相看不到对方持仓；解绑互不影响 |
| R3 | 同上 | ⑦ 凭据文件按用户分目录，且密文带 uid 绑定校验 | 把 A 的密文文件拷到 B 目录，B 读取时判定为「损坏/未配置」 |
| R4 | 所有功能做成权限配置 | ⑧ 30 个前端视图全部可单独开关 | admin 关掉「财报摘要」→ 普通用户侧栏无此项，直输 hash 也进不去 |
| R4 | 由 admin 分配「哪些可以看、哪些不能」 | ⑨ 权限只由 admin 修改，普通用户无法自助扩权 | 普通用户调用权限修改接口返回 403；篡改 Cookie 无效 |
| R4 | 同上 | ⑩ 后端也必须拦，不能只靠前端隐藏 | 无权限时直接调 `/api/*` 返回 403（不是 401，不是空数据） |
| R4 | 同上 | ⑪ 权限变更即时生效，不需重启 | admin 改完权限，普通用户下一次点击即被拦（无需重新登录） |
| R5 | 先出设计，其余不得动 | ⑫ 本文件为唯一产物，代码零改动 | `git status` 只有本文件为新增 |

---

## 2. 路线选型

三条路都能做到「隔离」，成本与代价完全不同。

| | A. 目录隔离（单进程） | B. 多实例（每用户一进程） | C. SQLite 多租户 |
|---|---|---|---|
| 做法 | 每用户一个 `data/users/<uid>/` 目录，请求级切换根路径 | 每用户一个进程 + 端口，各自 `DATA_DIR` | 所有表加 `user_id` 列，查询强制带条件 |
| 隔离强度 | 逻辑隔离（应用层授权）＋ 物理目录分离 | **物理隔离**（进程级） | 逻辑隔离（SQL 层） |
| 改动量 | 中：18 个数据文件路径 + 中间件 + 权限层，约 1000 行 | 小：约 80 行启动器 | 大：121 个端点全部要加过滤条件，约 2500 行 |
| 内存 | 1 份（约 300–500 MB） | **× N**（3 人即 1.5 GB） | 1 份 |
| 源站限流 | 1 份（同花顺 1.1s 串行、401 退避共用） | **× N**，且各进程独立限流器 → 极易触发 IP 封禁 | 1 份 |
| 缓存命中率 | 高（共享 11MB 行情缓存） | 低（N 份重复缓存） | 高 |
| 备份/删除用户 | `mv` 一个目录 | 停进程 + 删目录 | SQL 删行 + 孤儿文件 |
| admin 跨用户查看 | 直接读别的目录，天然支持 | 要跨进程调 API，很别扭 | 去掉 where 条件即可 |
| 冻结打包影响 | 无 | 需多端口/多快捷方式，用户体验差 | 需引入 SQLite 依赖与建表迁移 |

**结论：选 A。**

关键理由（按权重）：

1. **源站限流是硬约束**。同花顺 q 域 1.1s 串行、401/403 按 IP 短时封禁，这是实测过的红线（见项目长期记忆）。B 方案 N 个进程各自限流，等于把单 IP 的请求量乘以 N，必然封禁。这一条直接否掉 B。
2. **C 是改动量最大、风险最集中的方案**。121 个端点每一个都要审「有没有漏加 user_id 过滤」，漏一个就是越权漏洞；而 A 方案把隔离收敛到**一个函数**（路径解析），审计面小一个数量级。
3. **A 天然满足「admin 除外」**。admin 只是被允许解析别人的路径，不需要额外机制。
4. A 的唯一弱点是「逻辑隔离不等于物理隔离」——进程被完全控制时无法防御。但这一弱点 B/C 同样存在（B 的进程间若有共享密钥/端口，C 的 SQL 连接串在同一进程内），且**服务必须能代表用户解密数据**这一前提决定了任何架构都无法防御「服务进程被控」。所以这不是 A 的劣势。

---

## 3. 角色与可见性模型

### 3.1 两种角色

| 角色 | 标识 | 数量 | 数据可见范围 | 权限来源 |
|---|---|---|---|---|
| 管理员 | `admin` | 1（可增设） | 自己的私有区 ＋ **任意用户**私有区 | 全集，**不可剥夺** |
| 普通用户 | `user` | N | 仅自己的私有区 | admin 逐项勾选 |

不做第三种角色（如「只读管理员」）。理由：多一种角色就多一套判定分支，而实际只有「能管人」和「不能管人」两档。若将来需要，用「普通用户 + 全部页面权限 − 用户管理能力」即可表达。

### 3.2 admin 的「数据不隔离」怎么落地

这里有个容易做错的地方，必须先定清楚。

❌ **错误做法**：admin 登录后直接看到「所有用户数据的合并视图」。
后果：admin 改自己的持仓时可能误改到别人；看板里混着多人的自选；语义混乱。

✅ **采用做法**：admin 有自己的私有区（`u_0001`），**默认视角就是自己**；需要看别人时，显式「切换视角」。

```
admin 正常使用  →  读写 data/users/u_0001/     （与自己无关的代码路径完全一致）
admin 切换视角  →  读写 data/users/u_0005/     （顶部出现橙色横幅「正在以 张三 的身份查看」）
admin 切回自己  →  读写 data/users/u_0001/
```

视角切换的实现：`POST /api/admin/impersonate {uid}` 把 `impersonate_uid` 写进当前 session 记录，中间件解析 uid 时优先取它。**服务端存，不放 Cookie**（放 Cookie 就能被改）。

红线：
- 只有 `role=admin` 能调 `/api/admin/impersonate`；
- 切换动作写 `audit_admin.jsonl`（谁、何时、切到谁）；
- 切换态下**禁止**调用用户管理接口（防止「以 A 身份删了 B」的糊涂账），需先切回自己。

### 3.3 首个管理员从哪来

首次启动时 `data/app/users.json` 不存在 → 进入**初始化向导**：

1. 生成 `u_0001`，用户名 `admin`，随机 12 位初始口令（大小写+数字，排除易混字符 `0O1lI`）；
2. 初始口令打印到控制台、写入 `data/app/initial_password.txt`（0600）、同时显示在初始化页面；
3. `must_change_pwd = true`，首次登录强制改密后才能进主界面；
4. 改密成功后自动删除 `initial_password.txt`。

不做「默认 admin/admin」——公网入口暴露时那是必被扫的洞。

---

## 4. 数据分区

现有 `data/` 下有 18 个业务文件/目录，必须逐个定性。**分错一个，要么越权，要么白白浪费共享缓存。**

### 4.1 共享区（所有用户读，系统写）

| 现状路径 | 大小量级 | 为什么共享 |
|---|---|---|
| `data/cache/` | 约 11 MB | 行情/K线/财报缓存，与「谁在看」无关；按用户拆会让命中率归零并放大源站请求 |
| `data/screener_snapshot.json` | 约 2.8 MB | 全市场快照，全体用户看到的是同一份市场；构建一次全员复用 |
| `data/board_cons.json` | 中等 | 板块成分股表，公共事实 |
| `data/concept_spotcheck.jsonl` | 小 | 概念排行每日抽检留痕，公共取证数据 |
| `data/bin/cloudflared.exe` | 53 MB | 隧道客户端二进制，全局唯一 |

### 4.2 用户私有区（完全隔离）

| 现状路径 | 定义位置 | 内容 | 泄露后果 |
|---|---|---|---|
| `data/portfolio.json` | `datasource/portfolio.py:30` | 持仓明细、成本、份额 | **最敏感**：直接暴露资产规模 |
| `data/portfolio_snapshots/` | `datasource/portfolio_trend.py:30` | 每日持仓快照 | 同上，且含时间序列 |
| `data/watchlist.json` | `datasource/watchlist.py:28` | 自选股与分组 | 暴露关注标的 |
| `data/alerts.json` | `datasource/alerts.py:30` | 价格/涨跌提醒规则 | 暴露交易意图 |
| `data/dashboards.json` | `datasource/dashboards.py:27` | 自定义仪表板布局 | 暴露关注维度 |
| `data/user_prefs.json` | `core/user_prefs.py:26` | 界面偏好、关闭行为 | 低，但属个人 |
| `data/connectors.json` | `core/source_prefs.py:28` | 数据源开关偏好 | 低 |
| `data/ai_config.json` | `config.py:65` | AI 模型配置，**含 api_key** | **密钥泄露＋费用被盗用** |
| `data/summaries/` | `datasource/ai_summary.py:41` | 财报 AI 摘要缓存 | 中含个人提问痕迹 |
| `data/audit/` | `core/audit.py:26` | 养基宝操作审计 | 暴露绑定与操作历史 |
| `data/yjb_token.json` | `config.py:66` / `yangjibao.py:106` | 养基宝登录态 | **可直接操作他人基金账户** |
| `data/yjb_state.json` | `yangjibao.py:107` | 养基宝会话状态 | 同上 |
| `data/yjb_secret.json` | `yangjibao.py:109` | 养基宝加密凭据（`YJB2` 魔数） | 同上 |
| `data/exports/` | `config.py:63` | 导出文件 | 含持仓的完整导出 |

### 4.3 两个隐藏耦合点（必须单独处理，否则设计有洞）

这两个不是「文件归属」问题，是**代码逻辑已经跨用户读文件**，只改路径不够。

**耦合点 1：`core/cache.py:288` 读 `DATA_DIR/dashboards.json`**

```python
layout_file = config.DATA_DIR / "dashboards.json"
```

缓存模块为了算「布局变更指纹」去读用户私有文件。多用户下这里会读到**错误用户**的布局，导致 A 的看板命中 B 的缓存键。

处置：改为由调用方（`routers/dashboards.py`）把当前用户的布局指纹作为参数传入，`cache.py` 不再自己碰 `DATA_DIR`。属于必须改的结构性问题。

**耦合点 2：三个跨用户后台线程**

| 线程 | 位置 | 它做什么 | 多用户下的问题 |
|---|---|---|---|
| `portfolio-snapshot` | `main.py:347` | 每日收盘后写持仓快照 | 现在只写一份；需**遍历全部 active 用户**各写一份 |
| `concept-spotcheck` | `main.py:355` | 概念排行抽检落 jsonl | 落到共享区即可，**无需改** |
| `fundflow-warm` | `main.py:372` | 板块预热（默认关闭） | 共享区，**无需改** |

红线：`portfolio-snapshot` 改造后必须显式枚举用户，**禁止**写成「只跑 admin」或「只跑第一个用户」——那会导致普通用户永远没有快照曲线，且不报错。

### 4.4 分区结论图

```
                     ┌──────────────────────────────┐
   所有用户共用 ───► │  cache/  screener_snapshot   │  只读，系统写
                     │  board_cons  spotcheck  bin  │
                     └──────────────────────────────┘

                     ┌──────────────────────────────┐
   张三 ───────────► │  users/u_0002/  portfolio …  │  ← 只有张三的 session 能解析到
                     └──────────────────────────────┘
                     ┌──────────────────────────────┐
   李四 ───────────► │  users/u_0003/  portfolio …  │  ← 只有李四的 session 能解析到
                     └──────────────────────────────┘
                     ┌──────────────────────────────┐
   admin ──────────► │  users/u_0001/  portfolio …  │  ← 默认读自己
        └─ 切换视角 ─►│  users/u_0002/ 或 u_0003/ …  │  ← 显式授权后可读
                     └──────────────────────────────┘
```

---

## 5. 目录结构

```
data/
├─ app/                          # 应用级，全局唯一，普通用户不可见
│  ├─ users.json                 # 用户表（含密码哈希）
│  ├─ sessions.json              # 会话表（token 哈希，不存明文 token）
│  ├─ app_config.json            # 全局配置：端口、隧道、日志级别、版本
│  ├─ initial_password.txt       # 仅首次启动存在，改密后自动删除
│  └─ audit_admin.jsonl          # 管理员操作审计（改权限/建用户/切换视角）
│
├─ shared/                       # 全局共享，所有用户可读
│  ├─ cache/
│  ├─ screener_snapshot.json
│  ├─ board_cons.json
│  ├─ concept_spotcheck.jsonl
│  └─ bin/cloudflared.exe
│
├─ users/
│  ├─ u_0001/                    # admin 的私有区
│  │  ├─ portfolio.json
│  │  ├─ portfolio_snapshots/
│  │  ├─ watchlist.json
│  │  ├─ alerts.json
│  │  ├─ dashboards.json
│  │  ├─ user_prefs.json
│  │  ├─ connectors.json
│  │  ├─ ai_config.json
│  │  ├─ summaries/
│  │  ├─ audit/yjb_audit.jsonl
│  │  ├─ yjb_token.json
│  │  ├─ yjb_state.json
│  │  └─ yjb_secret.json
│  ├─ u_0002/
│  └─ u_0003/
│
├─ exports/
│  └─ u_0002/                    # 导出按用户分目录
│
└─ _trash/                       # 删除用户时先 mv 到这里，保留 7 天后清空
   └─ u_0004_20261108T231500/
```

设计取舍说明：

- **`app/` 与 `users/` 平级而非塞进 `users/`**：`app/` 里的东西普通用户一条接口都不该读到，物理隔离比逻辑判断更可靠。
- **`shared/` 独立成层**：避免「某些文件在 `data/` 根、某些在子目录」的混乱，改造时一次性归位。
- **`_trash/` 而不是直接删**：删用户是不可逆操作，给 7 天反悔窗口。
- **uid 用 `u_0001` 而不是用户名**：用户名可改，uid 不可改，路径稳定。也不做「用户名做主目录名」，避免中文/特殊字符路径问题。

---

## 6. 用户表与密码学

### 6.1 `data/app/users.json`

```json
{
  "version": 1,
  "updated_at": "2026-11-08T23:30:00+08:00",
  "seq": 3,
  "users": [
    {
      "uid": "u_0001",
      "username": "admin",
      "display_name": "管理员",
      "role": "admin",
      "status": "active",
      "pwd_algo": "pbkdf2_sha256",
      "pwd_iterations": 600000,
      "pwd_salt": "base64:16字节",
      "pwd_hash": "base64:32字节",
      "must_change_pwd": false,
      "permissions": ["*"],
      "created_at": "2026-11-08T23:00:00+08:00",
      "created_by": "system",
      "last_login_at": "2026-11-08T23:20:00+08:00",
      "last_login_ip": "192.168.31.104",
      "failed_count": 0,
      "locked_until": null,
      "note": "初始管理员"
    },
    {
      "uid": "u_0002",
      "username": "zhangsan",
      "display_name": "张三",
      "role": "user",
      "status": "active",
      "pwd_algo": "pbkdf2_sha256",
      "pwd_iterations": 600000,
      "pwd_salt": "base64:16字节",
      "pwd_hash": "base64:32字节",
      "must_change_pwd": true,
      "permissions": [
        "view.today", "view.market", "view.sectors", "view.sector-val",
        "view.stock", "view.news", "view.fund", "view.charting",
        "view.portfolio", "view.watchlist", "view.yjb", "view.dashboard"
      ],
      "created_at": "2026-11-08T23:05:00+08:00",
      "created_by": "u_0001",
      "last_login_at": null,
      "last_login_ip": null,
      "failed_count": 0,
      "locked_until": null,
      "note": ""
    }
  ]
}
```

字段约束：

| 字段 | 约束 |
|---|---|
| `uid` | `u_` + 4 位零填充序号；**永不复用**（删了用户也不回收编号） |
| `username` | 3–32 位，`[a-zA-Z0-9_-]`，唯一，大小写不敏感判重；保留字拒绝（`admin/root/system/api/auth/static/null` 等） |
| `role` | `admin` / `user`；**至少保留 1 个 active 的 admin**，否则拒绝降级/停用 |
| `status` | `active` / `disabled`；`disabled` 的 session 立即失效 |
| `permissions` | 权限点数组；`role=admin` 时固定 `["*"]` 且不可改 |
| `locked_until` | 连续 5 次密码错误锁 15 分钟；成功登录清零 |

### 6.2 密码哈希：PBKDF2-HMAC-SHA256

选它而不是 bcrypt/argon2 的唯一原因：**`hashlib.pbkdf2_hmac` 是标准库，零新依赖**。

这一点很实际——本机 pip 无法正常解析 PEP503 索引（任何镜像都报 `No matching distribution found`），装新包要走「urllib 抓 wheel + 离线安装」的绕路；而冻结包每加一个 C 扩展都要重新验证 PyInstaller 打包。在这个约束下，引入 bcrypt 的收益（哈希强度略高）远小于成本（依赖风险 + 打包体积 + 装机不可靠）。

参数：

```python
iterations = 600_000      # OWASP 2023 建议 PBKDF2-HMAC-SHA256 ≥ 600k
salt       = secrets.token_bytes(16)
hash       = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt, iterations, 32)
```

单次校验约 150–250 ms（本机实测量级），对登录场景完全可接受；**不影响任何业务接口**（只在登录时算一次）。

比对必须用 `hmac.compare_digest`，不能用 `==`。

### 6.3 会话表 `data/app/sessions.json`

```json
{
  "version": 1,
  "sessions": {
    "sha256(token)的hex": {
      "uid": "u_0002",
      "created_at": "2026-11-08T23:20:00+08:00",
      "expires_at": "2026-11-15T23:20:00+08:00",
      "last_seen_at": "2026-11-08T23:45:00+08:00",
      "ip": "192.168.31.104",
      "ua_hash": "sha256前16位",
      "impersonate_uid": null
    }
  }
}
```

要点：

- **库里只存 token 的 sha256**。文件泄露不等于会话被劫持。
- token = `secrets.token_urlsafe(32)`（43 字符），放 `HttpOnly` + `SameSite=Lax` + `Secure`（https 时）Cookie。
- 有效期 7 天，滑动续期（每次请求 `last_seen_at` 更新，剩余不足 1 天时续到 7 天）。续期写盘要节流（同一 session 60 秒内最多写一次），否则每个 API 请求都写文件。
- 上限：单用户最多 5 个并发 session，超出踢最旧的（防「登录一次多一个」无限增长）。
- `impersonate_uid` 存在服务端，**不放 Cookie**。

---

## 7. 登录与会话链路

### 7.1 与现有 `/auth` 的关系

v2.1.0 已有 `GET /auth?token=xxx`（单口令直达），`access_middleware.py` 负责比对单一 `OPENBB_ACCESS_PASSWORD`。

多用户后改为：

```
GET  /auth                → 登录页（用户名 + 密码）
POST /auth/login          → 校验 → 建 session → Set-Cookie → 302 /
POST /auth/logout         → 删 session → 清 Cookie → 302 /auth
GET  /auth?token=xxx      → 保留（一次性免密链接，见 7.4）
```

**路由装配顺序红线不变**：`/auth` 相关路由必须早于 `mount("/")`，否则被 StaticFiles 吃掉变 404。这是踩过的坑，改的时候必须保留现有注释与顺序。

### 7.2 请求处理链路（改造后）

```
请求进来
  │
  ├─ 静态资源（/、/js/*、/css/*、/auth.html）→ 直接放行
  │
  ├─ /api/health、/api/health/*            → 直接放行（探活用，不含用户数据）
  │
  ├─ /auth、/auth/login、/auth/logout      → 登录路由，放行
  │
  └─ 其它 /api/*
       │
       ├─ 读 Cookie → session_token
       ├─ sha256 → 查 sessions 表
       │    ├─ 不存在 / 过期 → 401 {code:"UNAUTHORIZED"}
       │    └─ 命中 →
       │         ├─ 查 users 表取 role/status/permissions
       │         ├─ status != active → 401（并删 session）
       │         ├─ 解析生效 uid：
       │         │    ├─ impersonate_uid 非空 且 role==admin → 用它
       │         │    └─ 否则 → 自己的 uid
       │         ├─ 写入 request.state.uid / .role / .perms
       │         ├─ 写入 contextvar（供数据层解析路径）
       │         ├─ 权限判定（§9）
       │         └─ 进入路由处理
       └─ 请求结束 → 清理 contextvar
```

### 7.3 与旧口令模式的关系

保留 `OPENBB_ACCESS_PASSWORD` 环境变量作为**兜底**：

- 若 `users.json` 不存在且设了该环境变量 → 走旧的单口令模式（兼容「只有一个人用」的场景，避免强制走初始化向导）；
- 若 `users.json` 存在 → 多用户模式优先，该环境变量失效。

这样升级不会打断你现在正在用的入口。

### 7.4 免密直达链接（保留现有能力）

`GET /auth?token=<32位一次性令牌>` 仍可用，但语义从「口令」改为「一次性登录令牌」：

- 令牌存在 `users.json` 的 `login_tokens` 数组（每个用户可有 0–1 个）；
- 用过一次即失效（或 24 小时过期）；
- 用途：手机扫码/桌面快捷方式免输密码。

当前桌面 `.url` 里带的访问口令属于旧机制，改造后需重新生成一次。

---

## 8. 权限点清单

### 8.1 设计原则：以「页面」为主轴，不让你面对 58 个开关

你原话是「哪些**可以看**、哪些不能」。所以权限配置界面**主视图就是页面列表**，后端接口权限由页面自动推导。

```
┌─ 为 张三 分配权限 ────────────────────────────────┐
│                                                   │
│  [页面可见性]  [特殊能力]                          │  ← 两个 Tab
│                                                   │
│  总览                              [全选] [清空]  │
│   ☑ 今日研判      ☑ 自定义工作区                  │
│   ☑ 仪表板管理    ☑ 组件库                        │
│                                                   │
│  行情市场                                          │
│   ☑ 市场概览      ☑ 宏观数据      ☑ 板块云图      │
│   ☑ 板块详情      ☑ 资金流向      ☑ 基金详情      │
│   ☐ 基金组合诊断                                  │
│                                                   │
│  投研分析                                          │
│   ☑ 个股研究      ☑ 新闻情绪      ☐ 财报摘要      │
│   ☐ 公告库·正文   ☑ 工具          ☐ 候选池        │
│                                                   │
│  我的投资                                          │
│   ☑ 我的投资      ☑ 自选股        ☑ 养基宝        │
│   ☐ 经理画像                                      │
│                                                   │
│  系统                                              │
│   ☐ 系统设置      ☐ 连接器        ☑ 提醒中心      │
│                                                   │
│  跳转页（无侧栏入口，从其他页面进入）              │
│   ☑ K线分析  ☐ 估值分位  ☐ 拥挤度                 │
│   ☐ 筛选器   ☐ 组合回测  ☐ 定投计算               │
│                                                   │
│                              [取消]  [保存]       │
└───────────────────────────────────────────────────┘
```

### 8.2 30 个视图权限点（以 `frontend/js/pages/core.js` 的 `VIEW_IDS` 为准）

> 实测 `VIEW_IDS` 共 **30 个**键。此处按侧栏分组排列，其中 24 个有侧栏入口、6 个为跳转页。

| # | 权限点 | 视图 id | 中文名 | 侧栏分组 |
|---|---|---|---|---|
| 1 | `view.today` | `today` | 今日研判 | 总览 |
| 2 | `view.dashboard` | `dashboard` | 自定义工作区 | 总览 |
| 3 | `view.dashboard-manage` | `dashboard-manage` | 仪表板管理 | 总览 |
| 4 | `view.widget-library` | `widget-library` | 组件库 | 总览 |
| 5 | `view.market` | `market` | 市场概览 | 行情市场 |
| 6 | `view.macro` | `macro` | 宏观数据 | 行情市场 |
| 7 | `view.sectors` | `sectors` | 板块云图 | 行情市场 |
| 8 | `view.sector-val` | `sector-val` | 板块详情 | 行情市场 |
| 9 | `view.lhb` | `lhb` | 资金流向 | 行情市场 |
| 10 | `view.fund` | `fund` | 基金详情 | 行情市场 |
| 11 | `view.fund-combo` | `fund-combo` | 基金组合诊断 | 行情市场 |
| 12 | `view.stock` | `stock` | 个股研究 | 投研分析 |
| 13 | `view.news` | `news` | 新闻情绪 | 投研分析 |
| 14 | `view.fin` | `fin` | 财报摘要 | 投研分析 |
| 15 | `view.ann` | `ann` | 公告库·正文 | 投研分析 |
| 16 | `view.tools` | `tools` | 工具 | 投研分析 |
| 17 | `view.cand` | `cand` | 候选池 | 投研分析 |
| 18 | `view.portfolio` | `portfolio` | 我的投资 | 我的投资 |
| 19 | `view.watchlist` | `watchlist` | 自选股 | 我的投资 |
| 20 | `view.yjb` | `yjb` | 养基宝 | 我的投资 |
| 21 | `view.mgr` | `mgr` | 经理画像 | 我的投资 |
| 22 | `view.settings` | `settings` | 系统设置 | 系统 |
| 23 | `view.connectors` | `connectors` | 连接器 | 系统 |
| 24 | `view.alerts` | `alerts` | 提醒中心 | 系统 |
| 25 | `view.charting` | `charting` | K线分析 | 跳转页 |
| 26 | `view.valuation` | `valuation` | 估值分位 | 跳转页 |
| 27 | `view.crowding` | `crowding` | 拥挤度 | 跳转页 |
| 28 | `view.screener` | `screener` | 筛选器 | 跳转页 |
| 29 | `view.backtest` | `backtest` | 组合回测 | 跳转页 |
| 30 | `view.dca` | `dca` | 定投计算 | 跳转页 |

### 8.3 「特殊能力」权限点（5 项，必须单独勾选）

这 5 项不挂在任何页面上，因为它们的风险等级高于「看数据」。

| # | 权限点 | 含义 | 默认 | 说明 |
|---|---|---|---|---|
| 1 | `cap.system.write` | 改系统设置（端口、隧道、日志、清理） | 仅 admin | 影响全机所有用户 |
| 2 | `cap.factory.reset` | 恢复出厂 | 仅 admin | 会删自己的偏好文件 |
| 3 | `cap.ai.config` | 配置 AI 模型与 api_key | 普通用户默认开 | 各配各的 key，费用各付 |
| 4 | `cap.yjb.bind` | 绑定/解绑养基宝账户 | 普通用户默认开 | 不绑就用不了养基宝页 |
| 5 | `cap.data.export` | 数据导出（zip/CSV） | 普通用户默认开 | 导出的是自己的数据 |

> `cap.user.admin`（用户管理）不列为可分配项——它等价于「成为管理员」，只能通过改 `role` 授予。

### 8.4 视图 → 后端域映射表（内置，admin 不可见）

前端开关自动推导后端权限，避免「页面开了但接口 403」的割裂。

| 视图 | 推导出的后端域（读） |
|---|---|
| `view.today` | `market`, `sentiment`, `news`, `portfolio`(只读) |
| `view.dashboard` / `dashboard-manage` / `widget-library` | `dashboards`, `widgets` |
| `view.market` | `market`, `sector`, `moneyflow` |
| `view.macro` | `macro` |
| `view.sectors` | `sector`, `moneyflow` |
| `view.sector-val` | `sector`, `moneyflow`, `valuation` |
| `view.lhb` | `moneyflow`, `market` |
| `view.fund` / `fund-combo` / `backtest` / `dca` | `fund` |
| `view.stock` | `stock`, `market`, `news` |
| `view.news` | `news`, `sentiment` |
| `view.fin` | `stock`, `news` |
| `view.ann` | `news` |
| `view.tools` | `screener`, `market` |
| `view.cand` | `screener`, `stock` |
| `view.portfolio` | `portfolio` |
| `view.watchlist` | `watchlist` |
| `view.yjb` | `yjb` |
| `view.mgr` | `fund` |
| `view.settings` | `ai`, `connectors`, `system`(只读) |
| `view.connectors` | `connectors` |
| `view.alerts` | `alerts` |
| `view.charting` | `market`, `stock` |
| `view.valuation` | `valuation`, `sector` |
| `view.crowding` | `sector`, `moneyflow` |
| `view.screener` | `screener` |

写权限（`portfolio.write`、`watchlist.write`、`alerts.write`、`dashboards.write`、`connectors.write`、`ai.write`、`yjb.write`、`screener.write`）**跟随对应视图的可见性**：能看就能改自己的。唯一例外是 `screener.write`（重建全市场快照，20–40 秒、消耗源站配额），默认只给 admin，可在「特殊能力」里单独打开。

### 8.5 19 个后端域与端点分布（121 个端点，供实施时逐域挂载权限）

| 域 | 端点 | 写权限 | 归属 |
|---|---|---|---|
| `ai` | 6 | ✓ | `view.settings` |
| `alerts` | 7 | ✓ | `view.alerts` |
| `connectors` | 3 | ✓ | `view.connectors` |
| `dashboards` | 2 | ✓ | `view.dashboard` |
| `fund` | 16 | ─ | `view.fund` 系 |
| `macro` | 1 | ─ | `view.macro` |
| `market` | 7 | ─ | `view.market` 系 |
| `moneyflow` | 5 | ─ | `view.lhb` 系 |
| `news` | 9 | ─ | `view.news` / `view.ann` |
| `portfolio` | 11 | ✓ | `view.portfolio` |
| `screener` | 3 | ✓ | `view.screener` |
| `sector` | 12 | ─ | `view.sectors` 系 |
| `sentiment` | 3 | ─ | `view.news` |
| `stock` | 10 | ─ | `view.stock` 系 |
| `system` | 9 | ✓ | `view.settings` + `cap.system.write` |
| `valuation` | 1 | ─ | `view.valuation` |
| `watchlist` | 4 | ✓ | `view.watchlist` |
| `widgets` | 1 | ─ | `view.widget-library` |
| `yjb` | 11 | ✓ | `view.yjb` + `cap.yjb.bind` |
| **合计** | **121** | | |

其中 `system` 域的 9 个端点里，`/api/system/health`、`/api/system/info` 属公共探活，**不挂权限**（否则启动自检会挂）；其余 7 个挂 `cap.system.write` 或 `system.read`。

---

## 9. 权限 enforcement 的三层

只做一层一定会漏。三层各自独立，任一层拦下即返回 403。

### L1 · 中间件（路由域粗筛）

位置：`core/access_middleware.py`（改造现有）

职责：认证（你是谁）+ 装载权限 + 域级粗筛。

```python
DOMAIN_PREFIX = {
    "/api/portfolio": "portfolio",
    "/api/watchlist": "watchlist",
    "/api/alerts":    "alerts",
    # …共 19 条
}

# 伪代码
if path.startswith("/api/"):
    sess = load_session(cookie)
    if not sess: return 401
    uid, role, perms = resolve(sess)
    request.state.uid = uid
    ctx.set(uid)                       # contextvar，供数据层
    domain = match_domain(path)
    if domain and not has_perm(perms, f"{domain}.read"):
        return 403
```

**不在 L1 判 write**：同一域内 read/write 端点混在一起，靠路径猜方法不可靠（有 POST 但语义是查询的端点）。write 判定下沉到 L2。

### L2 · 端点级依赖注入

位置：各 `routers/*.py`，用 FastAPI `Depends`。

```python
from backend.app.core.guard import require_perm

@router.post("/api/portfolio/holdings")
def add_holding(payload: dict, _=Depends(require_perm("portfolio.write"))):
    ...
```

`require_perm` 从 `request.state.perms` 取值；`admin` 直接放行。

**红线**：新增端点必须带 `Depends(require_perm(...))`，否则视为漏洞。在 `tests/` 加一条元测试——遍历 `app.routes`，凡 `/api/` 且非公共清单的，必须有该依赖，否则测试失败。这条元测试比人工 review 可靠。

### L3 · 数据路径解析（最终防线）

位置：新增 `core/tenant.py`

```python
_ctx_uid: ContextVar[str | None] = ContextVar("uid", default=None)

def require_uid() -> str:
    uid = _ctx_uid.get()
    if not uid:
        raise RuntimeError("无用户上下文：禁止在请求外访问用户私有数据")
    return uid

def user_dir(uid: str | None = None) -> Path:
    u = uid or require_uid()
    if not _UID_RE.fullmatch(u):        # ^u_\d{4}$
        raise ValueError("非法 uid")
    return config.DATA_DIR / "users" / u

def user_file(name: str, uid: str | None = None) -> Path:
    p = (user_dir(uid) / name).resolve()
    if not str(p).startswith(str(config.DATA_DIR.resolve())):
        raise ValueError("路径越界")     # 防 ../ 穿越
    return p

def shared_file(name: str) -> Path:
    return config.DATA_DIR / "shared" / name
```

**这是最关键的一条红线**：

> `require_uid()` 在无上下文时**必须抛错**，绝不能回落到「默认用户」或「admin」。

一旦允许回落，任何在请求外被调用的函数（后台线程、启动钩子、测试）都会静默写进某个用户的目录，形成「数据串户」。而这种 bug 不会报错，只会让某天你发现自己的持仓里多了别人的股票。

改造清单：18 个模块级常量 → 函数调用。逐个如下（这是实施时的打勾表）：

| 模块 | 原常量 | 改为 |
|---|---|---|
| `core/audit.py:26` | `AUDIT_DIR` | `def _audit_dir(): return tenant.user_dir() / "audit"` |
| `core/source_prefs.py:28` | `CONNECTORS_FILE` | `tenant.user_file("connectors.json")` |
| `core/user_prefs.py:26` | `PREFS_FILE` | `tenant.user_file("user_prefs.json")` |
| `datasource/ai_summary.py:41` | `SUMMARY_DIR` | `tenant.user_dir() / "summaries"` |
| `datasource/alerts.py:30` | `ALERTS_FILE` | `tenant.user_file("alerts.json")` |
| `datasource/dashboards.py:27` | `DASHBOARDS_FILE` | `tenant.user_file("dashboards.json")` |
| `datasource/portfolio.py:30` | `PORTFOLIO_FILE` | `tenant.user_file("portfolio.json")` |
| `datasource/portfolio_trend.py:30` | `SNAP_DIR` | `tenant.user_dir() / "portfolio_snapshots"` |
| `datasource/screener.py:48` | `SNAPSHOT_FILE` | `tenant.shared_file("screener_snapshot.json")` |
| `datasource/sector.py:1458` | `board_cons.json` | `tenant.shared_file("board_cons.json")` |
| `datasource/sector_health.py:35` | `SPOTCHECK_FILE` | `tenant.shared_file("concept_spotcheck.jsonl")` |
| `datasource/watchlist.py:28` | `WATCHLIST_FILE` | `tenant.user_file("watchlist.json")` |
| `datasource/yangjibao.py:106` | `YJB_TOKEN_FILE` | `tenant.user_file("yjb_token.json")` |
| `datasource/yangjibao.py:107` | `YJB_STATE_FILE` | `tenant.user_file("yjb_state.json")` |
| `datasource/yangjibao.py:109` | `YJB_SECRET_FILE` | `tenant.user_file("yjb_secret.json")` |
| `config.py:65` | `AI_CONFIG_FILE` | `tenant.user_file("ai_config.json")` |
| `config.py:66` | `YJB_TOKEN_FILE` | 同上（config 里的这份是冗余定义，确认后可删） |
| `config.py:63` | `EXPORT_DIR` | `tenant.user_dir() / "exports"` |
| `routers/system.py:289-291` | 三个 in-function 路径 | 改 `tenant.user_file(...)`（**这里已经是调用时构建，改动最小**） |
| `core/cache.py:288` | 读 `dashboards.json` | 改为参数传入（见 §4.3 耦合点 1） |

`datasource/yangjibao.py:1134` 有一处 `data_dir = str(config.DATA_DIR)`，需确认用途后一并处理。

---

## 10. 养基宝各自绑定

### 10.1 现状

三个文件，全部在 `data/` 根：

| 文件 | 格式 | 内容 |
|---|---|---|
| `yjb_token.json` | `YJB1` + DPAPI 密文 | 养基宝登录 token |
| `yjb_state.json` | 明文 JSON | 会话状态（是否已绑、账号标识） |
| `yjb_secret.json` | `YJB2` + DPAPI 密文 | `{"new": ..., "old": ...}` 加密凭据 |

DPAPI 是 **Windows 用户级**加密：同一 Windows 账号的进程能解开全部密文。

### 10.2 多用户下的真实问题

**问题不是「解不开」，而是「都能解开」。**

所有用户的数据都由**同一个 Windows 账号的同一个进程**读写（这是网页多用户的必然形态）。所以：

- DPAPI 挡不住「A 的数据被 B 的请求读到」——它本来也不负责这个，负责这个的是 §9 的 L3 路径隔离；
- DPAPI 真正挡的是「**文件被拷到别的机器**」和「**磁盘被直接翻**」。

结论：**DPAPI 继续用，但必须补两道**。

### 10.3 方案：分目录 + 密文内嵌 uid 绑定

```
data/users/u_0002/yjb_token.json    ← YJB1 + DPAPI(A 的 token)
data/users/u_0003/yjb_token.json    ← YJB1 + DPAPI(B 的 token)
```

补两道：

**第一道 · payload 内嵌 uid**

加密前的 JSON 里加 `"_uid": "u_0002"`。读取后校验：

```python
data = secure_store.read_secure(YJB_TOKEN_FILE, b"YJB1")
if not data or data.get("_uid") != tenant.require_uid():
    return None          # 视为「未绑定」，不报错、不泄露
```

这样把 A 的密文文件手工拷到 B 的目录，B 读取时判定为损坏/未配置——防止「配置错位导致串号」这种运维事故。

**第二道 · 每个用户的文件独立盐值命名空间**

`yjb_state.json` 是明文，里面若存了账号昵称/手机号尾号，等于 B 能看到 A 的账号标识。处置：`yjb_state.json` 里的账号标识**只存脱敏形式**（如 `138****5678`），或整体改为 DPAPI 加密。

### 10.4 为什么不引入 AES-GCM

考虑过「主密钥 + 每用户 HKDF 派生 + AES-256-GCM」，逐条否掉：

| 理由 | 说明 |
|---|---|
| 需要新依赖 | 本机无 `cryptography` / `pycryptodome` / `nacl`（已实测确认）。本机 pip 无法解析 PEP503 索引，装包要走离线 wheel 绕路；冻结包还要重新验证 PyInstaller |
| 威胁模型收益边际 | 服务必须能代表用户解密 → 进程内一定有明文密钥 → 主密钥方案在「进程被控」场景下与 DPAPI 等价 |
| 自研加密不可取 | 用 HMAC-SHA256 手搓流密码虽可行，但「自己实现密码学」是明确应避免的做法 |
| 未来若上云再换 | 云端无 DPAPI，届时必须换 AES-GCM + KMS。当前是单机网页，不提前付这个成本 |

**若将来出现「跨机器部署」需求**，切换点很干净：只改 `secure_store.py` 一个文件的实现，上层 API 不变。

### 10.5 绑定流程（每用户独立）

```
普通用户点「养基宝」→ 未绑定时显示引导页
  │
  ├─ 输入养基宝账号密码（或扫码）
  ├─ 后端用该用户身份调养基宝登录 → 拿 token
  ├─ token 加密写 data/users/<uid>/yjb_token.json（payload 含 _uid）
  ├─ 写 data/users/<uid>/yjb_state.json（脱敏账号标识）
  ├─ 追加 data/users/<uid>/audit/yjb_audit.jsonl（绑定事件）
  └─ 返回「已绑定 138****5678」
```

红线：

- A 解绑只删 A 的文件，**不得影响 B 的 token**；
- 权限检查在 `cap.yjb.bind`，且必须**同时**校验 `view.yjb`；
- 养基宝的登录态失效时，只影响当前用户，不能全局清 token。

---

## 11. 前端改造点

| # | 位置 | 改动 |
|---|---|---|
| 1 | `frontend/auth.html` | 从「单口令框」改为「用户名 + 密码 + 登录按钮」，保留错误提示区；表单 `method="post" action="/auth/login"` |
| 2 | `frontend/index.html` | 导航 `<li>` 按权限渲染：登录后拉 `/api/auth/me` 拿 `permissions`，无 `view.xxx` 的项 `hidden`（不是 `display:none`，要 `hidden` 属性以便无障碍与测试断言） |
| 3 | `frontend/index.html` | 新增顶部「视角横幅」（admin 切换态时显示，橙色，含「切回我自己」按钮） |
| 4 | `frontend/js/pages/core.js` | `route()` 入口加守卫：目标视图不在 `P.perms` 内 → 跳 `#/today` 并 toast「无权限访问该页面」；同时把 `VIEW_IDS` 导出成 `P.viewIds` 供权限页使用 |
| 5 | `frontend/js/pages/settings.js` | 新增「用户管理」Tab（仅 `role=admin` 可见）：用户列表 / 新建 / 改密 / 停用 / 删除 / 分配权限 |
| 6 | 全局 fetch 钩子 | 现有 401 跳转逻辑保留；新增 403 处理：toast「无权限」，**不跳登录页**（403 不等于未登录） |
| 7 | 侧栏分组 | 某分组下所有项都无权限时，整个 `<details>` 隐藏（避免空分组） |

关于「前端隐藏」的定位：**前端隐藏只是体验，不是安全边界**。安全由 §9 的 L1/L2/L3 保证。这一点必须在代码注释里写清楚，防止后来者以为「前端藏了就够了」。

---

## 12. 测试数据清空与初始化

### 12.1 清空范围（交付时执行）

删除（不留备份，因为全是测试数据）：

```
data/portfolio.json
data/portfolio_snapshots/
data/watchlist.json
data/alerts.json
data/dashboards.json
data/user_prefs.json
data/connectors.json
data/ai_config.json
data/summaries/
data/audit/
data/yjb_token.json
data/yjb_state.json
data/yjb_secret.json
data/exports/
data/cache/
data/screener_snapshot.json
data/board_cons.json
data/concept_spotcheck.jsonl
data/_tmp/
```

保留：

```
data/README.md
data/bin/                       # cloudflared 二进制，不算数据
```

### 12.2 初始化流程（首次启动，`data/app/users.json` 不存在时）

1. 建目录骨架：`data/app`、`data/shared/cache`、`data/shared/bin`、`data/users`、`data/_trash`；
2. 若 `data/bin/cloudflared.exe` 在旧位置（`data/bin/`），**原地保留不再移动**（避免 53MB 拷贝），`public_tunnel.py` 的路径改为 `data/shared/bin/` 并兼容旧路径；
3. 生成 `u_0001` admin，随机初始口令；
4. 写 `users.json`、空 `sessions.json`；
5. 控制台打印初始口令 + 落 `initial_password.txt`；
6. 前端首次访问 `/auth` 时，若检测到 `must_change_pwd`，登录后强制跳改密页。

### 12.3 迁移脚本的定位

**不写迁移脚本。** 你的原话是「现有数据都是测试，到时全部删除即可，只留下软件、功能、逻辑、接口」。

所以：
- 不做「旧路径 → 新路径」的数据搬迁；
- 不做「旧 YJB1 密文 → 新格式」的解密兼容；
- 旧文件直接删。

唯一需要保留兼容的是 **`data/bin/cloudflared.exe`**（53MB，重下要时间），已在 §12.2 处理。

---

## 13. 分阶段实施计划

四个阶段，每阶段独立可验收、可回滚。**不建议合并**——隔离类改造一旦一次性铺开，出问题无法定位是哪一层漏的。

### P1 · 地基：用户表 + 登录 + 会话（不碰业务数据）

| 项 | 内容 |
|---|---|
| 新增 | `core/tenant.py`（uid 上下文 + 路径解析）、`core/user_store.py`（用户表 CRUD + PBKDF2）、`core/session_store.py`（会话表）、`routers/auth.py`（登录/登出/me/改密） |
| 改造 | `core/access_middleware.py`（口令比对 → session 查表）、`main.py`（`/auth` 路由组、初始化向导）、`frontend/auth.html` |
| 不动 | 所有 `datasource/*`、所有业务 router、所有数据路径 |
| 验收红线 | ① admin 能建用户；② 普通用户能登录/登出；③ 无 Cookie 访问 `/api/portfolio` 返回 401；④ 旧 `OPENBB_ACCESS_PASSWORD` 模式仍可用（无 `users.json` 时）；⑤ 首次启动生成 admin 并强制改密 |
| 风险 | 低。此时所有用户共享数据，但只有 admin 能用，等价于现在的单人口令模式 |

### P2 · 隔离：数据路径按用户解析

| 项 | 内容 |
|---|---|
| 改造 | §9.3 表中 18 处路径常量 → `tenant.*` 调用；`core/cache.py:288` 布局指纹改参数传入；`main.py` 的 `portfolio-snapshot` 线程改为遍历 active 用户 |
| 新增 | `_trash` 逻辑、用户删除时目录 `mv` |
| 验收红线 | 见 §13.3 隔离测试 20 条 |
| 风险 | **最高**。这一步决定隔离是否成立。必须逐文件验证，不能只看单元测试 |

### P3 · 权限：30 个视图 + 5 个特殊能力 + 三层 enforcement

| 项 | 内容 |
|---|---|
| 新增 | `core/permissions.py`（权限点目录 + 视图→域映射表 + `has_perm`）、`core/guard.py`（`require_perm` 依赖）、`routers/admin.py`（用户管理 + 权限分配 + 视角切换） |
| 改造 | 121 个端点逐个挂 `Depends(require_perm(...))`；`frontend/js/pages/core.js` 路由守卫；`index.html` 导航渲染；新增用户管理页 |
| 验收红线 | ① 关掉某页面 → 侧栏无此项 + 直输 hash 被拒 + 直接调该域 API 返回 403；② 普通用户调权限修改接口 403；③ 权限变更即时生效（不重登）；④ 新增端点元测试通过（防漏挂权限）；⑤ admin 权限不可被剥夺 |
| 风险 | 中。121 个端点逐个挂权限容易漏，靠元测试兜底 |

### P4 · 养基宝 + 收尾

| 项 | 内容 |
|---|---|
| 改造 | `datasource/yangjibao.py` 三文件路径 + payload 内嵌 `_uid` 校验；`yjb_state.json` 脱敏 |
| 新增 | 绑定引导页（未绑定态）、解绑二次确认 |
| 收尾 | 清空测试数据、初始化脚本、打包、三场景安装验证 |
| 验收红线 | ① 两用户绑不同账号，互不可见；② 拷密文文件到别人目录 → 判定未绑定；③ A 解绑不影响 B；④ 交付包 `data/` 无业务数据 |
| 风险 | 中。养基宝是外部服务，需真实账号验证 |

### 13.3 隔离测试 20 条（P2 必过，P3/P4 回归）

构造两个用户 A（`u_0002`）、B（`u_0003`），各自写入可识别的标记数据，然后：

| # | 场景 | 期望 |
|---|---|---|
| 1 | A 加自选 `600519` | B 的自选列表无 `600519` |
| 2 | A 加持仓 | B 的持仓为空 |
| 3 | A 建提醒 | B 的提醒列表为空 |
| 4 | A 存仪表板布局 | B 的布局是默认值 |
| 5 | A 改界面偏好 | B 的偏好不变 |
| 6 | A 存 AI 配置 | B 读到「未配置」 |
| 7 | A 触发 AI 摘要 | B 的 `summaries/` 目录仍为空 |
| 8 | A 绑养基宝 | B 显示「未绑定」 |
| 9 | A 解绑养基宝 | B 仍正常绑定 |
| 10 | A 有持仓快照 | B 的 `portfolio_snapshots/` 无 A 的日期 |
| 11 | A 导出 zip | B 的 `exports/` 无该文件 |
| 12 | A 的审计日志 | B 的 `audit/` 无 A 的事件 |
| 13 | 手工把 A 的 `portfolio.json` 拷到 B 目录并改 `_uid` 为 B | 读取时校验失败 → 返回空（防伪造） |
| 14 | 手工把 A 的 `yjb_token.json` 拷到 B 目录 | B 判定「未绑定」 |
| 15 | B 构造 `?as=u_0002` 请求 | 403 |
| 16 | B 篡改 Cookie 里的 uid 字段 | 无效（uid 只存服务端） |
| 17 | B 停用后旧 session 访问 | 401 |
| 18 | 后台快照线程跑完 | A、B 各有一份快照，内容正确 |
| 19 | 无 session 访问 `/api/*` 任意端点 | 401 |
| 20 | admin 切换到 A 视角 | 能读到 A 的数据；切回后读到自己 |

第 13、14 条是「物理拷贝」场景，专门验证 §10.3 的 uid 绑定校验；第 18 条专门验证 §4.3 耦合点 2。

---

## 14. 风险清单与待你确认的问题

### 14.1 已知风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 121 个端点漏挂 `Depends(require_perm(...))` | 越权 | 元测试遍历 `app.routes` 强制检查（§9 L2） |
| 后台线程在无 uid 上下文时写数据 | 数据串户，且**不报错** | `require_uid()` 抛错而非回落（§9 L3）；`portfolio-snapshot` 显式遍历用户 |
| `core/cache.py` 读 `dashboards.json` 的隐藏耦合 | 缓存键串户 | 改为参数传入（§4.3） |
| 冻结打包后 DPAPI 行为差异 | 凭据读不出 | 打包后专门跑一次绑定/解绑（P4 验收） |
| 公网入口 + 多用户 = 攻击面变大 | 暴力破解 | 5 次锁定 15 分钟 + 全局限速（同 IP 每分钟 10 次登录尝试） |
| 删除用户后 uid 复用 | 数据错位 | uid 永不复用（`seq` 单调递增） |

### 14.2 需要你拍板的 6 件事

设计里有 6 处我没有替你定，因为都涉及你的实际使用方式：

**Q1 · 你要几个管理员？**
- 方案甲：只有你一个 admin（推荐，最简）
- 方案乙：你和另一个人都是 admin

影响：admin 之间数据是否互相可见（当前设计是「admin 可看所有非 admin 用户」，如果两个 admin，是否互相可看需要明确）。

**Q2 · AI 模型配置，是各配各的还是统一配？**

现在 `ai_config.json` 里存 `api_key`。
- 方案甲：每用户私有，各自填 key、各自付费（推荐，符合「完全隔离」）
- 方案乙：admin 统一配一个 key，全员共用（省钱，但费用无法分摊，且一人滥用全体受影响）

若选乙，`ai_config.json` 要移到 `shared/`，并且 `cap.ai.config` 收归 admin。

**Q3 · 普通用户能不能自己改密码？**
- 方案甲：能（推荐）
- 方案乙：只能 admin 重置

**Q4 · 普通用户能不能看到「共享数据」里的全市场信息？**
这里指的是筛选器快照、板块成分这类公共数据 —— 当前设计是共享（不隔离）。如果你的意思是「连公共行情也要按权限屏蔽」，那 `view.screener` 关掉即可，不需要动共享区。确认一下我的理解对。

**Q5 · 删用户时，他的数据是彻底删还是保留 N 天？**
- 方案甲：`mv` 到 `_trash/`，7 天后清空（推荐，可反悔）
- 方案乙：移入 `_trash/` 永久保留（占空间但永不丢）
- 方案丙：立即物理删除

**Q6 · 手机/公网访问时，多人同时在线，源站限流怎么办？**
同花顺 1.1s 串行是全局限流器（按 IP）。3 个人同时刷板块页，请求会排队，第 3 个人可能等 3 秒以上。
- 方案甲：接受排队，只优化文案（「当前有 2 人同时查询，预计等待 3 秒」）
- 方案乙：给热门板块加更长的共享缓存（如 60 秒），牺牲实时性换响应
- 方案丙：只给 admin 开放板块类页面（成本最低，但不是你想要的「全员可用」）

---

## 15. 本文件之外，什么都没动

- 未修改任何 `.py` / `.js` / `.html` / `.css`
- 未修改 `data/` 下任何文件
- 未执行任何构建、打包、测试
- `git status` 应只有本文件为新增

等你确认 §14.2 的 6 个问题后，再按 §13 的 P1 开始动手。

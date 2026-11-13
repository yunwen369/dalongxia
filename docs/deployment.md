# 远程访问部署指南（Tailscale + 访问口令）

> P5 定版配套：让大龙虾投研工作台通过 Tailscale 异地安全访问。

## 一、架构

```
异地设备 ──Tailscale 虚拟网── 本地主机（100.x.x.x）
                                  │
                                  └── OpenBB-CN.exe --serve（6903）
                                       └── 访问口令中间件（Bearer/cookie）
```

- Tailscale 提供加密点对点通道（无需公网 IP、无需端口映射）；
- 访问口令中间件提供应用层鉴权（即使 Tailscale 内也需口令）；
- 服务模式 `--serve` 无头运行，不弹窗口、不占桌面。

## 二、服务端（本地主机）

### 1. 安装 Tailscale

- 下载：https://tailscale.com/download/windows
- 安装后用同一账号登录（与异地设备同一 Tailnet）；
- 记下本机 Tailscale IP（`100.x.x.x`，Tailscale 托盘 → 本机地址）。

### 2. 配置访问口令

设置环境变量 `OPENBB_ACCESS_PASSWORD`（≥4 字符）：

```bat
:: 临时（当前窗口）
set OPENBB_ACCESS_PASSWORD=你的口令

:: 永久（用户级，新开窗口生效）
setx OPENBB_ACCESS_PASSWORD 你的口令
```

> 口令不落盘、不入日志；进程启动时读取。修改后需重启服务生效。

### 3. 以服务模式启动

```bat
:: 安装版（需同时设 OPENBB_HOST=0.0.0.0 才能被 Tailscale 访问）
set OPENBB_HOST=0.0.0.0
"%LOCALAPPDATA%\Programs\OpenBB-CN\OpenBB-CN.exe" --serve

:: 源码版
set OPENBB_HOST=0.0.0.0
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 6903
```

- `--serve` 无头运行（不弹窗口、不开浏览器）；
- **必须设 `OPENBB_HOST=0.0.0.0`**（默认 `127.0.0.1` 仅本机访问，Tailscale 设备连不上）；
- 端口默认 6900，可用 `OPENBB_PORT` 覆盖。

### 4. 验证

本地浏览器访问 `http://127.0.0.1:6903/`：
- 页面正常打开（静态放行）；
- 任意 `/api/*` 接口无 token 返回 401；
- 带 `Authorization: Bearer 你的口令` 返回 200。

## 三、客户端（异地设备）

### 1. 安装并登录 Tailscale（同一 Tailnet）

### 2. 访问

浏览器打开 `http://<服务端Tailscale IP>:6903/`

首次访问 API 会 401。在浏览器控制台执行（写入 cookie，后续请求自动带）：

```js
document.cookie = "access_token=你的口令; path=/; max-age=86400";
```

或用支持自定义 Header 的工具（如 Postman）加 `Authorization: Bearer 你的口令`。

## 四、安全建议

- 口令长度 ≥12 位，含大小写数字符号；
- 不要在公共 Wi-Fi 下直连，始终走 Tailscale；
- 访问日志中不会记录口令正文，但会记录失败 IP（节流用）；
- 同 IP 连续 10 次失败后 60 秒内拒绝（429），防爆破。

## 五、停止服务

- 源码模式：`Ctrl+C`；
- 安装版服务模式：关闭进程（任务管理器结束 `OpenBB-CN.exe`）。

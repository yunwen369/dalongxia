# -*- coding: utf-8 -*-
"""养基宝扫码授权命令行助手（跨轮次可用）。

为什么需要它：二维码会话只存在进程内存里，而本工程的开发服务无法跨命令存活。
``/qr_code_state/{id}`` 是上游无状态接口，因此只要把 ``session_id`` 落盘，
下一次命令仍能轮询并完成绑定——不需要后台常驻服务。

用法（在工程根执行）：

    # 1) 生成二维码（保存 PNG + 落盘 session）
    .venv\\Scripts\\python.exe tools\\yjb_scan.py new

    # 2) 扫码后轮询并绑定（成功即列出账户与持仓估值样例）
    .venv\\Scripts\\python.exe tools\\yjb_scan.py poll

    # 3) 只查状态不绑定
    .venv\\Scripts\\python.exe tools\\yjb_scan.py status
"""

from __future__ import annotations

import base64
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.datasource import yangjibao  # noqa: E402

PENDING = ROOT / "B_交付归档" / "03_测试报告" / "20261024_r4" / "_pending_session.json"
QR_PNG = ROOT.parent.parent / "养基宝扫码登录二维码.png"


def _load_pending() -> dict:
    """读取落盘的待扫码会话。

    Returns:
        dict: `{session_id, url, created_at}`；不存在时返回空字典。
    """
    if not PENDING.exists():
        return {}
    try:
        return json.loads(PENDING.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def cmd_new() -> int:
    """生成二维码并落盘会话。

    Returns:
        int: 进程退出码。
    """
    if not yangjibao.secret_configured():
        print("✗ 签名盐未配置：请先在设置页「数据与合规」或 POST /api/fund/yjb/secret 写入")
        return 2
    result = yangjibao.start_qrcode_session()
    image = result.get("image_base64") or ""
    if image:
        QR_PNG.write_bytes(base64.b64decode(image))
    PENDING.write_text(json.dumps({
        "session_id": result["session_id"],
        "url": result["url"],
        "created_at": time.time(),
        "expires_in": result.get("expires_in"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("✓ 二维码已生成")
    print(f"  会话 id : {result['session_id']}")
    print(f"  码内容  : {result['url']}")
    print(f"  有效期  : {result.get('expires_in')} 秒")
    print(f"  图片    : {QR_PNG}")
    print("\n扫码方式（已按官方扩展源码核实）：")
    print("  用【微信扫一扫】扫此码 —— 官方扩展文案即「微信扫码登录 / 打开微信扫一扫 即可同步你的基金」")
    print("  微信会拉起养基宝页内授权；完成后本机 token 自动落盘")
    print("  ⚠ 本码 4 分钟内有效（官方同款超时），过期请重新执行 new，不要复用旧码")
    return 0


def _poll_once() -> dict:
    """按落盘会话轮询一次（不依赖进程内存）。

    Returns:
        dict: `{state, message, token?}`。
    """
    pending = _load_pending()
    session_id = str(pending.get("session_id") or "")
    if not session_id:
        return {"state": "missing", "message": "没有待扫码会话，请先执行 new"}
    # 上游是无状态接口：直接按 id 轮询，不依赖本地 _SESSIONS
    result = yangjibao._request(  # noqa: SLF001 - 复用同一签名与 https 优先逻辑
        "GET", yangjibao.BASE_OLD, f"/qr_code_state/{session_id}", use_old=True
    )
    if result["status"] == 404:
        return {"state": "expired", "message": "二维码已失效，请重新生成"}
    try:
        body = json.loads(result["text"])
    except json.JSONDecodeError:
        return {"state": "error", "message": "状态响应非 JSON"}
    if str(body.get("code")) != "200":
        return {"state": "error", "message": f"上游返回 code={body.get('code')} {body.get('message') or ''}"}
    data = body.get("data") or {}
    state = str(data.get("state") if data.get("state") is not None else "")
    token = str(data.get("token") or "")
    if state == "2" and token:
        return {"state": "success", "message": "扫码成功", "token": token,
                "nickname": data.get("nickname"), "avatar": data.get("avatar")}
    if state == "3":
        return {"state": "expired", "message": "二维码已失效，请重新生成"}
    if state in ("1", "0", ""):
        return {"state": "waiting", "message": "等待扫码"}
    return {"state": "scanning", "message": "已扫码，请在手机端确认"}


def cmd_status() -> int:
    """查询当前会话状态。

    Returns:
        int: 进程退出码。
    """
    state = _poll_once()
    print(f"state={state['state']}  message={state.get('message')}")
    if state.get("nickname"):
        print(f"账号昵称: {state['nickname']}")
    return 0 if state["state"] in ("success", "waiting", "scanning") else 1


def cmd_poll() -> int:
    """短轮询（最多 60s）并在成功后完成绑定与数据取样。

    Returns:
        int: 进程退出码。
    """
    deadline = time.time() + 60
    last = ""
    while time.time() < deadline:
        state = _poll_once()
        if state["message"] != last:
            print(f"  {time.strftime('%H:%M:%S')}  state={state['state']}  {state.get('message')}")
            last = state["message"]
        if state["state"] == "success":
            return _finish_bind(state["token"], state.get("nickname"))
        if state["state"] in ("expired", "missing", "error"):
            return 1
        time.sleep(2)
    print("轮询超时（60s），可再次执行 poll 或重新 new")
    return 3


def _finish_bind(token: str, nickname: str | None) -> int:
    """四要件校验 + 加密落盘 + 数据取样。

    Args:
        token: 扫码返回的 token。
        nickname: 上游返回的昵称（仅用于显示）。

    Returns:
        int: 进程退出码。
    """
    check = yangjibao.verify_token_four_elements(token)
    print(f"四要件校验: {check}")
    if not check.get("ok"):
        print("✗ token 校验未通过，未落盘")
        return 1
    yangjibao.save_token(token, "browser-plug")
    print(f"✓ token 已 DPAPI 加密落盘（昵称：{nickname or '未知'}）")

    try:
        info = yangjibao.get_accounts()
        print(f"账户: {info}")
    except Exception as exc:  # noqa: BLE001 - 取样失败不影响绑定
        print(f"账户读取失败: {type(exc).__name__}: {exc}")

    try:
        holdings = yangjibao.get_holdings()
        print(f"持仓 {len(holdings)} 条，前 3 条估值：")
        for item in holdings[:3]:
            nv = item.get("nv_info") or {}
            print(f"  {item.get('code')} {item.get('short_name')} "
                  f"gszzl={nv.get('gszzl')} zsgzzl={nv.get('zsgzzl')} vgszzl={nv.get('vgszzl')} "
                  f"dwjz={nv.get('dwjz')}")
    except Exception as exc:  # noqa: BLE001
        print(f"持仓读取失败: {type(exc).__name__}: {exc}")

    try:
        print(f"击败基民: {yangjibao.get_beat_rate()}")
    except Exception as exc:  # noqa: BLE001
        print(f"击败基民读取失败: {type(exc).__name__}: {exc}")

    PENDING.unlink(missing_ok=True)
    print("\n绑定完成。可执行 `python -m uvicorn ... --port 6900` 或双击 start.bat 使用正式界面。")
    return 0


def main() -> int:
    """入口：按子命令分发。

    Returns:
        int: 进程退出码。
    """
    action = (sys.argv[1] if len(sys.argv) > 1 else "new").lower()
    if action == "new":
        return cmd_new()
    if action == "poll":
        return cmd_poll()
    if action == "status":
        return cmd_status()
    print("用法: yjb_scan.py [new|poll|status]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

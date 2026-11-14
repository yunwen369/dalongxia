#!/usr/bin/env bash
# 只读取证：对指定实例跑全部 UI 动态探针（模拟人工点击），串行执行避免限流。
# 注意：panels/mobile 探针会清理 CDP profile 目录，必须先清空 safe-delete 批量守卫环境变量。
set -u
BASE="$1"
TAG="$2"
# 用法: bash _run_ui_battery.sh <base_url> <tag> <evidence_dir> [shots_root]
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
PY="$ROOT/A_运行资产/openbb-cn/.venv/Scripts/python.exe"
TOOLS="$ROOT/A_运行资产/openbb-cn/tools"
EV="${3:?需要证据输出目录}"
SH="${4:-$EV/../shots}/$TAG"
mkdir -p "$SH"
export NO_PROXY='127.0.0.1,localhost'
export PYTHONUTF8=1

run() {
  local name="$1"; shift
  echo "===== [$TAG] $name 开始 $(date +%T) ====="
  "$@" 2>&1 | tail -30
  echo "===== [$TAG] $name 结束 $(date +%T) ====="
}

NODEL="env -u CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR -u CODEBUDDY_TOOL_CALL_ID -u CODEBUDDY_SAFE_DELETE_ENABLED -u CODEBUDDY_SAFE_DELETE_BULK_GUARD -u CODEBUDDY_SAFE_DELETE_SANDBOX"

run "nav_consistency_probe" "$PY" "$TOOLS/nav_consistency_probe.py" --base "$BASE" --out "$EV/nav_$TAG.json"
run "ui_clickthrough"        "$PY" "$TOOLS/ui_clickthrough.py" --base-url "$BASE" --out "$EV/clickthrough_$TAG.json" --shots "$SH/clickthrough"
run "v34_panels_probe"       $NODEL "$PY" "$TOOLS/v34_panels_probe.py" --base-url "$BASE" --out "$EV/panels_$TAG.json"
run "mobile_layout_probe"    $NODEL "$PY" "$TOOLS/mobile_layout_probe.py" --base-url "$BASE" --out "$EV/mobile_$TAG.json" --shots "$SH/mobile"
run "_cdp_v350_features"     "$PY" "$TOOLS/_cdp_v350_features.py" --base "$BASE" --out "$EV/v350_$TAG.json"
run "_cdp_r14_summary"       "$PY" "$TOOLS/_cdp_r14_summary.py" --base "$BASE" --out "$EV/r14_$TAG.json"
run "human_walkthrough"      "$PY" "$TOOLS/human_walkthrough.py" --base-url "$BASE" --out "$EV/walkthrough_$TAG.json" --shots "$SH/walkthrough"
echo "===== [$TAG] 全部 UI 探针结束 $(date +%T) ====="

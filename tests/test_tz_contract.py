"""时区口径契约（TZ-06 / TZ-07）。

对应任务书验收要求「在把系统时区临时改成非 UTC+8 的对照测试中，接口 as_of 仍为北京时间」。

本机（以及绝大多数国内部署机）本身就是 UTC+8，直接断言 ``bj_now() == datetime.now()``
在两种时区下都成立，测不出问题。因此这里用「**伪造宿主时区**」的办法：
把 ``utils`` 模块内的 ``datetime`` 名字替换为一个 ``now(tz=None)`` 返回 UTC-6 本地时间的
假类，等价于把部署机搬到美洲中部——若业务代码哪天被改回 ``datetime.now()``，
返回值会立刻偏 14 小时，用例即失败。

同时用 AST 扫描守住「后端不再出现裸 ``datetime.now()`` / ``date.today()``」这条硬约束
（只扫代码节点，天然跳过注释与 docstring，不会误报说明文字）。
"""

from __future__ import annotations

import ast
from datetime import datetime as real_datetime
from datetime import timedelta
from pathlib import Path
from typing import List, Tuple
from zoneinfo import ZoneInfo

import pytest

from backend.app.datasource import utils

BEIJING = ZoneInfo("Asia/Shanghai")
HOST = ZoneInfo("Etc/GMT+6")  # 等价 UTC-6（Etc/GMT 的符号与直觉相反，+6 即西六区）

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"


def _make_fake_datetime(offset_hours: int = -14):
    """构造一个「宿主时区不是 UTC+8」的 datetime 替身。

    Args:
        offset_hours: 伪造宿主本地时间相对北京时间的偏移（小时）；默认 -14 即 UTC-6。

    Returns:
        type: 可直接替换模块内 ``datetime`` 名字的类。

    Notes:
        - ``now(tz)``：带时区时按真实 UTC 时刻换算，行为与标准库一致；
        - ``now()``：不带时区时返回「伪造宿主」的本地墙上时间。
          这正是 ``datetime.now()`` 的语义陷阱——它永远返回宿主本地时间。
    """

    class _FakeDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D102 - 类方法语义见 Notes
            instant = real_datetime.now(ZoneInfo("UTC"))
            if tz is not None:
                return instant.astimezone(tz)
            return instant.astimezone(BEIJING).replace(tzinfo=None) + timedelta(hours=offset_hours)

    return _FakeDatetime


def test_fake_datetime_actually_shifts_local_clock(monkeypatch) -> None:
    """前置自检：假的 datetime 必须真的偏 14 小时，否则后续用例是空转。

    Args:
        monkeypatch: pytest 夹具。
    """
    monkeypatch.setattr(utils, "datetime", _make_fake_datetime(-14))
    naive = utils.datetime.now()  # 伪造宿主的本地时间（西六区）
    aware = utils.datetime.now(BEIJING).replace(tzinfo=None)  # 真实北京墙上时间
    gap = (aware - naive).total_seconds() / 3600
    assert 13.9 < gap < 14.1, f"伪造宿主未生效，偏差 {gap:.2f}h"


def test_bj_now_immune_to_host_timezone(monkeypatch) -> None:
    """TZ-06：``bj_now()`` 必须是北京时间，不受宿主时区影响。

    Args:
        monkeypatch: pytest 夹具。

    Notes:
        若有人把 ``bj_now`` 改回 ``datetime.now().replace(...)``（不带 tz），
        本用例会拿到西六区时间，与真实北京时间相差 14 小时而失败。
    """
    expected = real_datetime.now(BEIJING).replace(tzinfo=None)
    monkeypatch.setattr(utils, "datetime", _make_fake_datetime(-14))
    got = utils.bj_now()
    assert abs((got - expected).total_seconds()) < 5, f"bj_now 偏移：{got} vs {expected}"


def test_text_helpers_use_beijing_under_foreign_host(monkeypatch) -> None:
    """TZ-06：``now_text`` / ``today_text`` 在非 UTC+8 宿主上仍是北京口径。

    Args:
        monkeypatch: pytest 夹具。
    """
    expected = real_datetime.now(BEIJING).replace(tzinfo=None)
    monkeypatch.setattr(utils, "datetime", _make_fake_datetime(-14))

    assert utils.now_text().startswith(expected.strftime("%Y-%m-%d %H:"))
    assert utils.today_text() == expected.date().isoformat()
    # 带偏移的版本：偏移量必须是 +08:00，不能跟随宿主
    assert utils.bj_now_aware().utcoffset().total_seconds() == 8 * 3600


def test_route_as_of_stays_beijing_under_foreign_host(monkeypatch) -> None:
    """TZ-06：路由层 ``as_of`` 在非 UTC+8 宿主上仍输出北京时间。

    Args:
        monkeypatch: pytest 夹具。

    Notes:
        取板块路由的 ``_status_now()`` 作为样本：它不触网、纯本地计算，
        且 as_of 走的是与多数接口相同的 ``utils.now_text()`` 通道。
    """
    from backend.app.routers import sector

    expected = real_datetime.now(BEIJING).replace(tzinfo=None)
    monkeypatch.setattr(utils, "datetime", _make_fake_datetime(-14))
    as_of, _status, _realtime = sector._status_now()  # noqa: SLF001 - 直接验证内部口径
    assert as_of.startswith(expected.strftime("%Y-%m-%d %H:")), f"as_of 偏移：{as_of}"


def _iter_naive_time_calls(root: Path) -> List[Tuple[str, int, str]]:
    """扫描后端源码中的「无参时间取值」调用。

    Args:
        root: 待扫描目录。

    Returns:
        List[Tuple[str, int, str]]: ``(文件相对路径, 行号, 调用名)`` 列表。

    Notes:
        只看 AST 的 ``Call`` 节点，注释与 docstring 不会被计入；
        带参数的 ``datetime.now(tz)`` 属显式时区，不算裸调用。
    """
    offenders: List[Tuple[str, int, str]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.args or node.func.attr not in ("now", "utcnow", "today"):
                continue
            base = node.func.value
            if isinstance(base, ast.Name) and base.id in ("datetime", "date"):
                offenders.append((str(path.relative_to(root)), node.lineno, f"{base.id}.{node.func.attr}()"))
    return offenders


def test_no_bare_datetime_now_in_backend() -> None:
    """TZ-06 硬约束：后端业务时间不得使用裸 ``datetime.now()`` / ``date.today()``。

    Notes:
        全部业务时间必须走 ``utils.bj_now()`` / ``utils.today_bj()`` /
        ``utils.now_text()`` / ``utils.bj_now_aware()`` 四个单一入口。
        UTC 场景（签名时间戳等）请显式写 ``datetime.now(timezone.utc)``，
        那种写法带参数，不会被本用例误判。
    """
    offenders = _iter_naive_time_calls(BACKEND_DIR)
    assert not offenders, "存在裸时间取值（应改用 utils 单一助手）：\n" + "\n".join(
        f"  {path}:{line} {name}" for path, line, name in offenders
    )


def test_beijing_tz_defined_once() -> None:
    """TZ-06 硬约束：北京时区常量只在 ``utils`` 里定义一次。

    Notes:
        多份 ``ZoneInfo("Asia/Shanghai")`` 会导致「改一处漏一处」，
        历史上前端/后端各有独立常量，切时区方案时极易漏改。
    """
    defs: List[Tuple[str, int]] = []
    for path in sorted(BACKEND_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name) and node.value.func.id == "ZoneInfo"):
                defs.append((str(path.relative_to(BACKEND_DIR)), node.lineno))
    assert len(defs) == 1, f"北京时区应只在一处定义，实际：{defs}"
    assert defs[0][0].endswith("datasource/utils.py") or defs[0][0].endswith("datasource\\utils.py")


@pytest.mark.parametrize("text, want_prefix", [
    ("2026-10-26 04:00:00", "2026-10-26 04:00"),
    ("2026-10-26 04:00", "2026-10-26 04:00"),
    ("20261026 04:00:00", None),  # 紧凑格式不在本契约内，交由 utils.parse_datetime 处理
])
def test_frontend_beijing_parse_contract(text: str, want_prefix) -> None:
    """TZ-07 契约说明：后端 ``as_of`` 一律为北京 naive 串（``YYYY-MM-DD HH:MM:SS``）。

    Args:
        text: 待校验的时间串。
        want_prefix: 期望前缀；``None`` 表示该格式不属于前端解析契约。

    Notes:
        前端 ``I18N.bjMs`` 用正则 ``(\\d{4})-(\\d{2})-(\\d{2})(?:[ T](...))?`` 匹配，
        因此「紧凑格式」必须由后端先转成标准串再下发；本用例把这条约定写成断言，
        避免有人为省事直接透传上游的紧凑格式导致前端算错报价年龄。
    """
    import re

    pattern = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")
    matched = pattern.match(text)
    if want_prefix is None:
        assert not matched, "紧凑格式不应命中前端正则（应由后端先规范化）"
        return
    assert matched, f"{text} 必须能被前端北京时间正则解析"
    assert text.startswith(want_prefix)

# -*- coding: utf-8 -*-
"""Google 风格 docstring 结构段补齐器（规范 §10.8）。

原则：
  1. **只追加、不重写**——原 docstring 的摘要与口径说明 100% 保留，仅在缺段时
     追加 ``Args:`` / ``Returns:`` / ``Raises:``；
  2. **不编造语义**——参数说明优先取源码内已有信息（FastAPI ``Query(description=...)``、
     类型注解、默认值），其次取参数语义词典（覆盖全部 115 个参数名）；
  3. **可复算**——``--dry-run`` 打印每个待改函数的追加内容，``--apply`` 才落盘。

用法：
    .venv\\Scripts\\python.exe tools\\docstring_fill.py --dry-run
    .venv\\Scripts\\python.exe tools\\docstring_fill.py --apply
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import textwrap
from typing import Dict, List, Optional, Tuple

CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 参数语义词典：覆盖 backend 全部参数名（115 个）。描述取自接口口径与类型注解，
# 不臆测业务含义；同一参数在不同模块语义一致（如 symbol 恒为证券代码）。
PARAM_DOC: Dict[str, str] = {
    "symbol": "证券代码（A股 6 位 / 港股 5 位 / 美股字母代码）",
    "market": "市场标识：a=A股 / hk=港股 / us=美股 / fund=场外基金",
    "code": "证券或基金代码",
    "limit": "返回条数上限（超出上限按上限截断）",
    "period": "周期：日k/周k/月k，或分钟数（1/5/15/30/60）",
    "source": "数据源标识（用于降级链溯源与徽章展示）",
    "value": "待处理数值",
    "board": "板块名称（与板块树中的名称一致）",
    "payload": "请求体（JSON 对象）",
    "data": "待包装或清洗的数据主体",
    "key": "缓存键 / 字典键",
    "func": "待执行的无参取数函数",
    "items": "记录列表",
    "path": "接口路径 / 文件路径",
    "message": "中文提示文案",
    "text": "待解析或清洗的文本",
    "as_of": "数据时间（北京时间，格式 YYYY-MM-DD HH:MM:SS）",
    "caliber": "口径说明（展示在口径徽章）",
    "ttl": "缓存有效期（秒）",
    "adjust": "复权方式：qfq=前复权 / hfq=后复权 / 空=不复权",
    "sort": "排序方式：time=按时间倒序 / relevance=按相关度",
    "page": "页码（从 1 开始）",
    "title": "标题",
    "loader": "缓存未命中时的加载函数（返回数据或 {data, error}）",
    "empty_value": "空数据时返回的同构空值（保持字段结构一致）",
    "name": "名称",
    "boards": "板块名列表（最多 6 个）",
    "token": "访问令牌（敏感信息，不落日志）",
    "market_status": "市场状态文案（盘中/盘前/盘后/休市/收盘快照）",
    "url": "请求地址",
    "frame": "源站原始表（pandas DataFrame）",
    "days": "统计天数",
    "params": "请求参数（查询串键值对）",
    "item_id": "条目主键",
    "kind": "类别：industry=行业 / concept=概念",
    "result": "上游返回结果",
    "is_realtime": "是否为盘中实时数据",
    "port": "端口号",
    "request": "HTTP 请求对象",
    "exc": "捕获到的异常",
    "accepted": "是否已确认合规告知书",
    "aliases": "别名列表",
    "allow_stale": "是否允许返回过期缓存",
    "api": "接口路径列表",
    "attempts": "已尝试次数",
    "backoff": "重试退避时长序列（秒）",
    "base": "基准值",
    "cache_key": "缓存键",
    "candidate": "候选数据源（名称/口径/函数的元组）",
    "candidates": "候选数据源列表（按优先级降级）",
    "channel": "通道/子来源名称",
    "codes": "代码列表",
    "content": "正文内容",
    "counter": "计数字典",
    "date_text": "日期文本",
    "default": "缺省值",
    "desc": "描述文本",
    "digits": "保留小数位数",
    "directory": "目录路径",
    "empty": "空数据时的返回值",
    "enabled": "是否启用",
    "encoding": "响应编码（如 gbk/utf-8）",
    "end": "结束日期（YYYY-MM-DD）",
    "entry": "组件条目（含 id 与 span）",
    "filter_text": "过滤文本（命令面板搜索词）",
    "fund_id": "基金代码",
    "grid": "网格配置",
    "high": "最高值",
    "host": "主机地址",
    "index": "序号 / 索引",
    "indicator": "指标名（如 预测年报每股收益）",
    "is_index": "是否为指数",
    "item": "单条记录",
    "jump": "跳转目标（路由或 URL）",
    "keyword": "搜索关键词",
    "keywords": "关键词列表",
    "line": "单行文本",
    "low": "最低值",
    "market_hint": "市场提示（由代码推断的 a/hk/us）",
    "method": "HTTP 方法",
    "need_rows": "是否要求返回非空行",
    "now": "当前时间（用于时段判定，可注入）",
    "param_code": "待校验的代码参数",
    "period_key": "周期键名",
    "periods": "报告期列表",
    "pids": "进程号列表",
    "platform_name": "平台名称",
    "protect": "保护配置映射",
    "q": "搜索关键词",
    "quote": "报价快照",
    "referer": "请求来源页（部分源站校验 Referer）",
    "region": "区域标识：cn=A股 / us=美股 / hk=港股",
    "report": "报告标识",
    "report_name": "报告名称",
    "retries": "最大重试次数",
    "rows": "数据行列表",
    "scope": "统计范围：market=大盘 / stock=个股 / board=板块",
    "secret": "签名密钥（敏感信息）",
    "sector": "板块/行业名称",
    "sectors": "板块名称集合（关键词 → 板块映射）",
    "session_id": "会话标识",
    "size": "条数 / 字节数",
    "start": "起始日期（YYYY-MM-DD）",
    "state": "状态对象",
    "statement": "财报类型（利润表/资产负债表/现金流量表）",
    "stocks": "股票代码列表",
    "time_text": "时间文本",
    "timeout": "超时秒数",
    "timestamp": "时间戳（秒）",
    "total": "总条数",
    "use_old": "是否使用旧密钥（密钥轮换期）",
    "validator": "结果校验函数（返回 False 视为该源不可用）",
    "values": "数值列表",
    "widget_id": "组件 id",
    "years": "统计年数",
    # ---- 按类型区分的同名参数（避免「page 一律当页码」这类语义误判）----
    "page|str": "页面标识（视图 id，如 home/sectors/news）",
    "page|int": "页码（从 1 开始）",
    "index|int": "序号（从 1 开始）",
    "index|str": "索引键",
    "size|int": "条数 / 字节数",
    "value|float": "待处理数值",
    "value|int": "待处理整数",
    "value|str": "待处理文本（可能含百分号等单位后缀）",
    "value|Any": "待处理值（类型由调用方约定）",
    "data|dict": "数据主体（字典）",
    "data|List[Dict[str, Any]]": "记录列表",
    "items|List[Dict[str, Any]]": "记录列表",
    "rows|List[Dict[str, Any]]": "记录行列表",
    "start|str": "起始日期（YYYY-MM-DD）",
    "end|str": "结束日期（YYYY-MM-DD）",
    "name|str": "名称",
    "key|str": "缓存键 / 字典键",
    "timeout|Optional[int]": "超时秒数（缺省取 config.DATASOURCE_TIMEOUT）",
    "timeout|int": "超时秒数",
    "secret|Any": "签名密钥（敏感信息，不落日志）",
    "state|Dict[str, Any]": "状态对象（读改写后原样返回）",
}

# 异常类型 → 原因描述（优先级低于 raise 语句自带的文案）
RAISE_DOC: Dict[str, str] = {
    "SystemExit": "启动前置检查失败需要终止进程时抛出",
    "NonRetryableError": "确定性失效（不重试）时抛出，交由降级链切换下一源",
    "ValueError": "参数不合法或上游数据不满足口径时抛出",
    "UpstreamTimeoutError": "上游请求超时时抛出（由 resilient 决定是否重试）",
    "KeyError": "必需字段缺失时抛出",
    "RuntimeError": "运行时前置条件不满足时抛出",
    "OSError": "文件或网络系统调用失败时抛出",
    "Exception": "不可恢复错误时抛出",
}

# 返回类型 → Returns 说明（按 ast.unparse 的注解文本匹配）
RETURN_DOC: List[Tuple[str, str]] = [
    ("Dict[str, Any]", "dict: 统一返回结构 `{data, error, error_code}`；data 内附 source/caliber/as_of/market_status/is_realtime。"),
    ("List[Dict[str, Any]]", "list[dict]: 记录列表；字段口径见函数内注释与《接口说明书》。"),
    ("Dict[str, float]", "dict: 指标名 → 数值。"),
    ("Dict[str, int]", "dict: 名称 → 计数。"),
    ("List[str]", "list[str]: 字符串列表。"),
    ("List[float]", "list[float]: 浮点序列。"),
    ("Optional[Dict[str, Any]]", "dict | None: 命中时返回记录，缺失返回 None。"),
    ("Optional[List[Dict[str, Any]]]", "list[dict] | None: 命中时返回列表，缺失返回 None。"),
    ("Optional[float]", "float | None: 数值；无法解析时返回 None（不补 0）。"),
    ("Optional[str]", "str | None: 文本；缺失返回 None。"),
    ("Optional[bool]", "bool | None: 判定结果；无法判断返回 None。"),
    ("Tuple[str, bool]", "tuple[str, bool]: (市场状态文案, 是否盘中实时)。"),
    ("Tuple[Optional[str], str, bool]", "tuple: (数据时间, 市场状态, 是否实时)。"),
    ("bool", "bool: 判定结果。"),
    ("float", "float: 数值结果。"),
    ("int", "int: 整数结果。"),
    ("str", "str: 文本结果。"),
    ("Any", "Any: 结果原样透传（类型由 loader/func 约定）。"),
    ("JSONResponse", "JSONResponse: FastAPI 响应对象（统一错误体，中文文案且不泄露堆栈）。"),
    ("Response", "Response: HTTP 响应对象。"),
    ("Path", "Path: 文件路径。"),
]


def _param_doc(func: ast.AST, param: str) -> Optional[str]:
    """参数说明：类型精确键 > 名字键（避免同名不同义被误判）。"""
    type_text = _param_type(func, param)
    if type_text:
        exact = PARAM_DOC.get(f"{param}|{type_text}")
        if exact:
            return exact
    return PARAM_DOC.get(param)


def _query_description(func: ast.AST, param: str) -> Optional[str]:
    """从 FastAPI ``param: type = Query(..., description="...")`` 取中文描述。"""
    args = func.args
    all_args = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
    defaults = [None] * (len(args.posonlyargs) + len(args.args) - len(args.defaults)) + list(args.defaults)
    defaults += list(args.kw_defaults)
    for node, default in zip(all_args, defaults):
        if node.arg != param or default is None or not isinstance(default, ast.Call):
            continue
        for kw in default.keywords:
            if kw.arg == "description" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                text = kw.value.value.strip()
                if CJK_RE.search(text):
                    return text.rstrip("。")
    return None


def _param_type(func: ast.AST, param: str) -> str:
    """取参数注解类型文本（无注解返回空串）。"""
    args = func.args
    all_args = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
    for node in all_args:
        if node.arg == param and node.annotation is not None:
            return ast.unparse(node.annotation)
    return ""


def _raises_entries(func: ast.AST) -> List[Tuple[str, str]]:
    """提取函数体内 raise 的 (异常类型, 原因描述)，去重保序。"""
    entries: List[Tuple[str, str]] = []
    seen = set()

    def _walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue  # 不含嵌套函数
            if isinstance(child, ast.Raise) and child.exc is not None:
                exc = child.exc
                if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
                    name = exc.func.id
                    reason = ""
                    if exc.args and isinstance(exc.args[0], ast.Constant) and isinstance(exc.args[0].value, str):
                        reason = exc.args[0].value.strip().rstrip("。")
                elif isinstance(exc, ast.Name):
                    name, reason = exc.id, ""
                else:
                    name, reason = "Exception", ""
                if name not in seen:
                    seen.add(name)
                    entries.append((name, reason))
            _walk(child)

    _walk(func)
    return entries


def _returns_line(func: ast.AST) -> Optional[str]:
    """按返回注解生成 Returns 段正文；无法判定时返回 None（不硬造）。"""
    if func.returns is not None:
        text = ast.unparse(func.returns)
        if text in ("None", "NoneType"):
            return None
        for prefix, doc in RETURN_DOC:
            if text == prefix:
                return doc
        if text.startswith("Dict"):
            return f"dict: {text} 结构的结果。"
        if text.startswith("List"):
            return f"list: {text} 结构的结果。"
        if text.startswith("Optional"):
            return f"{text}: 可能为 None。"
        return f"{text}: 计算结果。"
    # 无注解：按 return 表达式推断（仅 dict/list 字面量，避免臆断）
    for node in ast.walk(func):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            return "dict: 函数内构造的结果字典。"
        if isinstance(node, ast.Return) and isinstance(node.value, ast.List):
            return "list: 函数内构造的结果列表。"
    return None


def _normalize_doc(doc: str) -> str:
    """去掉多行 docstring 的公共缩进（首行除外），避免写回时重复叠加。

    Python docstring 的值会保留源码中的相对缩进（如 ``\n    实测列``），
    若写回时再前置函数缩进，就会多出 4 空格。统一 dedent 后再按函数缩进重排。
    """
    lines = doc.rstrip().split("\n")
    if len(lines) <= 1:
        return lines[0] if lines else ""
    rest = textwrap.dedent("\n".join(lines[1:]))
    return lines[0] + "\n" + rest


def _build_doc(func: ast.AST, original: str) -> Optional[str]:
    """在原文基础上追加缺失段；无需改动返回 None。"""
    # 关键顺序：先把**原文**规范化（此时公共缩进=4，可正确去除），再追加结构段；
    # 若先追加（Args 段无缩进）再 dedent，公共缩进变为 0，原有 4 格缩进将无法消除。
    body = _normalize_doc(original)
    if not body.strip():
        return None
    if not CJK_RE.search(body.splitlines()[0]):
        return None  # 摘要非中文交由人工处理，不自动改写

    additions: List[str] = []
    params = [a.arg for a in list(func.args.posonlyargs) + list(func.args.args)
              + list(func.args.kwonlyargs) if a.arg not in ("self", "cls")]

    # ---- Args ----
    if params and not re.search(r"^\s*Args:\s*$", body, re.MULTILINE):
        lines = []
        for param in params:
            desc = _query_description(func, param) or _param_doc(func, param)
            if not desc:
                desc = f"{_param_type(func, param) or '参数'}（见函数实现）"
            lines.append(f"    {param}: {desc}。")
        additions.append("Args:\n" + "\n".join(lines))
    elif params:
        # Args 段已存在：仅补漏列的参数
        absent = [p for p in params if not re.search(rf"^\s*{re.escape(p)}\s*[:(]", body, re.MULTILINE)]
        if absent:
            lines = []
            for param in absent:
                desc = _query_description(func, param) or _param_doc(func, param) or "见函数实现"
                lines.append(f"    {param}: {desc}。")
            body = re.sub(r"(^\s*Args:\s*$)", lambda m: m.group(1) + "\n" + "\n".join(lines),
                          body, count=1, flags=re.MULTILINE)

    # ---- Returns ----
    returns = _returns_line(func)
    if returns and not re.search(r"^\s*Returns:\s*$", body, re.MULTILINE):
        additions.append("Returns:\n    " + returns)

    # ---- Raises ----
    raises = _raises_entries(func)
    if raises and not re.search(r"^\s*Raises:\s*$", body, re.MULTILINE):
        lines = [f"    {name}: {reason or RAISE_DOC.get(name) or '不可恢复错误时抛出'}。"
                 for name, reason in raises]
        additions.append("Raises:\n" + "\n".join(lines))

    if not additions:
        return body
    return body + "\n\n" + "\n\n".join(additions)


def _replace_docstring(source: str, func: ast.AST, new_doc: str) -> Tuple[str, int]:
    """把新 docstring 写回源码（逐行补缩进，单行/多行分别处理）。

    注意：整行替换会丢掉原行的前导缩进，因此**每一行**都要显式加 indent；
    空行保持空（不留尾随空格）。
    """
    lines = source.split("\n")
    expr = func.body[0]
    start = expr.lineno - 1
    end = expr.end_lineno - 1
    indent = " " * expr.col_offset
    # docstring 值是「已解析」文本：写回源码前必须把反斜杠转义回字面量，
    # 否则 `\uXXXX`（腾讯 smartbox 说明）会被 Python 当作 unicode 转义而报错。
    safe = new_doc.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    parts = safe.split("\n")
    if len(parts) == 1:
        block = [indent + '"""' + parts[0] + '"""']
    else:
        block = [indent + '"""' + parts[0]]
        for extra in parts[1:]:
            block.append(indent + extra if extra else "")
        block.append(indent + '"""')
    return "\n".join(lines[:start] + block + lines[end + 1:]), start + 1


def process_file(path: str, apply: bool, verbose: bool) -> List[str]:
    """处理单个 Python 文件，返回改动说明列表。"""
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()
    original_source = source
    tree = ast.parse(source)
    changes: List[str] = []
    # 自底向上替换，避免行号漂移
    targets = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    targets.sort(key=lambda n: n.lineno, reverse=True)
    for func in targets:
        if not func.body or not isinstance(func.body[0], ast.Expr):
            continue
        doc = ast.get_docstring(func, clean=False)
        if doc is None:
            continue
        new_doc = _build_doc(func, doc)
        if new_doc is None or new_doc == doc.rstrip():
            continue
        source, lineno = _replace_docstring(source, func, new_doc)
        changes.append(f"{os.path.basename(path)}:{func.name}（原 {func.lineno} 行）")
        if verbose:
            added = new_doc[len(doc.rstrip()):].strip()
            print(f"\n--- {os.path.basename(path)}::{func.name} ---\n{added}")
    if apply and source != original_source:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(source)
    return changes


# =============================================================
# JS（JSDoc）补齐
# =============================================================
JS_FUNC_RE = re.compile(
    r"(?P<indent>[ \t]*)(?P<head>function\s+(?P<n1>[\w$]+)|(?P<n2>[\w$]+)\s*[:=]\s*function)"
    r"\s*\((?P<params>[^)]*)\)")

# 前端参数语义（与后端同名参数口径一致；补充 DOM 相关）
JS_PARAM_DOC: Dict[str, str] = {
    "value": "待格式化或处理的值",
    "body": "区块容器（.pg-body，四态 data-state 挂在此节点）",
    "container": "DOM 容器节点",
    "text": "文本内容",
    "data": "数据主体（接口 data 段）",
    "params": "查询参数对象",
    "items": "记录列表",
    "message": "提示文案（中文）",
    "payload": "渲染载荷（聚合后的数据）",
    "id": "元素 id / 组件 id",
    "row": "单行记录",
    "digits": "小数位数",
    "info": "信息对象",
    "panel": "数据面板（报价/新闻/舆情等聚合块）",
    "tag": "HTML 标签名",
    "cls": "CSS 类名",
    "key": "键名 / i18n key",
    "vars": "占位符变量表",
    "path": "接口路径",
    "rows": "记录行列表",
    "force": "是否强制刷新（true 时跳过本地缓存）",
    "widget": "组件元数据（id/title/span）",
    "columns": "列定义（字段名数组）",
    "card": "卡片 DOM 节点",
    "market": "市场标识：a/hk/us",
    "values": "数值列表",
    "state": "状态对象",
    "field": "字段名",
    "recipe": "渲染配方（kind/path/cols）",
    "entry": "列表项 / 组件条目",
    "event": "事件对象",
    "index": "序号（从 0 开始）",
    "node": "DOM 节点",
    "box": "图表容器节点",
    "chart": "ECharts 实例",
    "series": "序列数据",
    "option": "ECharts 配置项",
    "theme": "主题色对象",
    "target": "目标元素",
    "list": "列表数据",
    "name": "名称",
    "type": "类型标识",
    "url": "请求地址",
    "error": "错误对象",
    "response": "HTTP 响应对象",
    "filename": "文件名",
    "headers": "表头列表",
    "content": "正文内容",
    "dash": "仪表板对象",
    "meta": "元数据对象",
    "size": "条数 / 尺寸",
    "selector": "CSS 选择器",
    "defaultValue": "输入框默认值",
    "hint": "提示文案",
    # ---- 补齐剩余参数（按各模块真实用法，不做通用敷衍）----
    "board": "板块名称",
    "caliber": "口径说明文案（展示在口径徽章）",
    "callback": "回调函数",
    "cell": "表格单元格节点",
    "closes": "收盘价序列",
    "color": "颜色值（CSS 色值）",
    "cols": "列定义（字段名数组）",
    "containerId": "容器元素 id",
    "currency": "币种（CNY/HKD/USD）",
    "degrade": "是否降级命中（决定徽章样式）",
    "estimates": "一致预期数据（eps/profit/institutions/detail）",
    "extra": "附加配置项",
    "extraClass": "附加 CSS 类名",
    "fin": "财务数据块（摘要 + 趋势）",
    "gridClass": "栅格类名",
    "holders": "股东数据（十大股东/十大流通股东）",
    "input": "输入框节点",
    "kline": "K 线数据列表",
    "labels": "标签列表",
    "maxHeightClass": "最大高度类名",
    "method": "事件名或 HTTP 方法",
    "minute": "分时数据列表",
    "n": "数量 / 数值",
    "onRetry": "重试回调",
    "onRowClick": "行点击回调",
    "options": "可选项集合",
    "pairs": "键值对列表",
    "pane": "指标面板索引",
    "placeholder": "占位文案",
    "prefix": "代码前缀（如 sh/sz）",
    "profile": "公司概况数据",
    "quote": "报价数据",
    "role": "消息角色（user/assistant）",
    "root": "根节点",
    "secret": "签名密钥（敏感信息）",
    "silent": "是否静默（true 时不弹提示）",
    "source": "数据源名称",
    "sources": "数据源列表",
    "summary": "摘要数据",
    "title": "标题",
    "tree": "树形结构（板块树）",
    "valueKey": "取值字段名",
    "widgetId": "组件 id",
    "width": "宽度（栅格列数）",
    "window": "时间窗口（如 30 日）",
}

JS_TYPE_BY_NAME: Dict[str, str] = {
    "container": "HTMLElement", "body": "HTMLElement", "card": "HTMLElement",
    "node": "HTMLElement", "box": "HTMLElement", "target": "HTMLElement",
    "el": "HTMLElement", "panel": "Object", "data": "Object", "params": "Object",
    "recipe": "Object", "widget": "Object", "meta": "Object", "state": "Object",
    "vars": "Object", "theme": "Object", "option": "Object", "dash": "Object",
    "items": "Array", "rows": "Array", "columns": "Array", "values": "Array",
    "list": "Array", "series": "Array", "headers": "Array",
    "value": "*", "text": "string", "message": "string", "cls": "string",
    "tag": "string", "key": "string", "path": "string", "url": "string",
    "filename": "string", "selector": "string", "field": "string",
    "id": "string", "name": "string", "type": "string",
    "digits": "number", "index": "number", "size": "number",
    "force": "boolean", "event": "Event", "chart": "Object",
}


def _js_type(param: str, default: str) -> str:
    """参数类型：默认值字面量优先，其次按参数名推断，未知用 *。"""
    d = (default or "").strip()
    if re.match(r"^[\"']", d):
        return "string"
    if re.match(r"^-?\d", d):
        return "number"
    if d in ("true", "false"):
        return "boolean"
    if d.startswith("{}"):
        return "Object"
    if d.startswith("[") :
        return "Array"
    return JS_TYPE_BY_NAME.get(param, "*")


def _js_returns_type(body: str) -> Optional[str]:
    """按函数体内 return 表达式推断返回类型；无 return 返回 None。"""
    found = set()
    for match in re.finditer(r"^\s*return\b\s*([^;\n]*)", body, re.MULTILINE):
        expr = match.group(1).strip()
        if expr == "":
            continue  # 纯 `return;` 视为无返回值，不加 @returns
        if expr in ("null", "undefined"):
            found.add("*")
        elif expr in ("true", "false"):
            found.add("boolean")
        elif re.match(r"^-?\d", expr):
            found.add("number")
        elif re.match(r"^[\"'`]", expr):
            found.add("string")
        elif expr.startswith("el(") or "createElement" in expr:
            found.add("HTMLElement")
        elif expr.startswith("{"):
            found.add("Object")
        elif expr.startswith("["):
            found.add("Array")
        else:
            found.add("*")
    if not found:
        return None
    return found.pop() if len(found) == 1 else "*"


def _js_body(source: str, start: int) -> str:
    """按花括号配平截取函数体文本（避免 @returns 扫到相邻函数）。"""
    open_idx = source.find("{", start)
    if open_idx < 0:
        return ""
    depth = 0
    for idx in range(open_idx, len(source)):
        ch = source[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[open_idx:idx + 1]
    return source[open_idx:open_idx + 2000]


def _js_doc_block(lines: List[str], func_line: int) -> Optional[Tuple[int, int, str]]:
    """定位紧贴函数的注释块，返回 (起始行, 结束行, 形式)。

    形式为 ``block``（/** ... */）或 ``line``（// 逐行）；无注释块返回 None。
    只认**紧邻**（允许中间空一行）的注释，避免跨到上一个函数的注释块。
    """
    idx = func_line - 1
    if idx < 0:
        return None
    if lines[idx].strip() == "":
        idx -= 1
    if idx < 0:
        return None
    last = lines[idx].strip()
    if last.endswith("*/"):
        end = idx
        start = idx
        while start > 0 and not lines[start].lstrip().startswith("/**"):
            prev = lines[start - 1].strip()
            if prev.startswith("*") or prev.startswith("/**"):
                start -= 1
            else:
                break
        return start, end, "block"
    if last.startswith("//"):
        end = idx
        start = idx
        while start > 0 and lines[start - 1].strip().startswith("//"):
            start -= 1
        return start, end, "line"
    return None


def process_js_file(path: str, apply: bool, verbose: bool) -> List[str]:
    """为 JS 函数补齐 @param/@returns（仅处理已有中文注释块的函数）。

    实现要点：先按**原始源码**计算全部插入计划，再自底向上一次性应用，
    避免边改边算导致行号漂移、把 JSDoc 插到注释块之外。
    """
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read().replace("\r\n", "\n")
    original = source
    lines = source.split("\n")
    # (动作 replace/insert, 起始行, 结束行, 新行, 函数名)
    plans: List[Tuple[str, int, int, List[str], str]] = []
    for match in JS_FUNC_RE.finditer(source):
        name = match.group("n1") or match.group("n2")
        raw_params = match.group("params") or ""
        param_items: List[Tuple[str, str]] = []
        for part in raw_params.split(","):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                pname, default = part.split("=", 1)
                param_items.append((pname.strip(), default.strip()))
            else:
                param_items.append((part.strip(), ""))
        # 无参数函数也要处理：有返回值时需补 @returns
        line_idx = source[: match.start("head")].count("\n")
        found = _js_doc_block(lines, line_idx)
        if found is None:
            continue  # 无注释块：交由人工撰写摘要，不自动生成
        block_start, block_end, kind = found
        block = "\n".join(lines[block_start:block_end + 1])
        if not CJK_RE.search(block):
            continue
        additions: List[str] = []
        for pname, default in param_items:
            if re.search(rf"@param\s*\{{[^}}]*\}}\s*{re.escape(pname)}\b", block):
                continue
            ptype = _js_type(pname, default)
            desc = JS_PARAM_DOC.get(pname, f"参数 {pname}")
            additions.append(f"@param {{{ptype}}} {pname} {desc}")
        body = _js_body(source, match.end())
        ret_type = _js_returns_type(body)
        if ret_type and "@returns" not in block:
            additions.append(f"@returns {{{ret_type}}} 处理结果")
        if not additions:
            continue
        indent = match.group("indent")
        if kind == "line":
            plans.append(("insert", block_end + 1, block_end, [indent + "// " + a for a in additions], name))
            continue
        one_liner = (block_start == block_end
                     and lines[block_end].strip().startswith("/**")
                     and lines[block_end].strip().endswith("*/"))
        if one_liner:
            # 单行注释块必须拆成多行，否则 @param 会落到注释之外
            summary = lines[block_end].strip()[3:-2].strip()
            new_block = ([indent + "/** " + summary]
                         + [indent + " * " + a for a in additions]
                         + [indent + " */"])
            plans.append(("replace", block_end, block_end, new_block, name))
        else:
            plans.append(("insert", block_end, block_end, [indent + " * " + a for a in additions], name))

    changes: List[str] = []
    for kind, start, end, new_lines, name in sorted(plans, key=lambda p: -p[1]):
        if kind == "replace":
            lines = lines[:start] + new_lines + lines[end + 1:]
        else:
            lines = lines[:start] + new_lines + lines[start:]
        changes.append(f"{os.path.basename(path)}:{name}")
        if verbose:
            print(f"\n--- {os.path.basename(path)}::{name} ---\n" + "\n".join(new_lines))
    source = "\n".join(lines)
    if apply and source != original:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(source)
    return changes


def main() -> None:
    """入口：``--js`` 处理前端 JSDoc，缺省处理 backend/app 的 Python docstring。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), ".."))
    parser.add_argument("--apply", action="store_true", help="写入文件（缺省仅预览）")
    parser.add_argument("--verbose", action="store_true", help="打印每处追加内容")
    parser.add_argument("--js", action="store_true", help="处理 frontend/js 的 JSDoc")
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    total = 0
    if args.js:
        js_dir = os.path.join(root, "frontend", "js")
        for name in sorted(os.listdir(js_dir)):
            if not name.endswith(".js"):
                continue
            changes = process_js_file(os.path.join(js_dir, name), args.apply, args.verbose)
            if changes:
                print(f"[{'APPLY' if args.apply else 'DRY'}] frontend/js/{name}: {len(changes)} 处")
                total += len(changes)
        print(f"\n合计待改 {total} 处（{'已写入' if args.apply else '预览模式'}）")
        return
    for base, _dirs, names in os.walk(os.path.join(root, "backend", "app")):
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            changes = process_file(path, args.apply, args.verbose)
            if changes:
                print(f"[{'APPLY' if args.apply else 'DRY'}] {os.path.relpath(path, root)}: {len(changes)} 处")
                total += len(changes)
    print(f"\n合计待改 {total} 处（{'已写入' if args.apply else '预览模式'}）")


if __name__ == "__main__":
    main()

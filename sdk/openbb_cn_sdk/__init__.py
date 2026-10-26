# -*- coding: utf-8 -*-
"""OpenBB 中文投研工作台 · 最小可用 Python SDK（P2-10）。

定位：给自动化脚本/外部程序一个**只读**的轻客户端，直接调用本机服务端的
`/api/*` 统一信封接口，不引入额外依赖（仅标准库 urllib）。

设计约束：
1. 只读：不封装写接口（持仓增删、AI 配置保存等），避免误改本机数据；
2. 不重试、不隐藏错误：服务端返回 `error` 非空时抛 :class:`ApiError`，
   交由调用方决定重试策略（与工程内 resilient 分层一致）；
3. 统一返回 `data` 段；调用方可拿 `last_envelope` 查看完整信封。

用法::

    from openbb_cn_sdk import Client
    client = Client()                      # 默认 http://127.0.0.1:6900
    client.health()["build"]               # 构建标识
    client.index("a")["items"][0]["price"] # 上证指数点位
    client.quote("600519", "a")["quote"]["price"]
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_BASE_URL = "http://127.0.0.1:6900"


class ApiError(RuntimeError):
    """服务端返回业务错误时抛出（error 非空）。"""


class Client:
    """本机服务端只读客户端。

    Attributes:
        base_url: 服务基址（默认 http://127.0.0.1:6900）。
        timeout: 单次请求超时秒数。
        last_envelope: 最近一次完整响应信封，便于排查。
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 15.0) -> None:
        """初始化客户端。

        Args:
            base_url: 服务基址，需含 http(s):// 前缀。
            timeout: 单次请求超时秒数。
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.last_envelope: Dict[str, Any] = {}

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """发起 GET 请求并解包统一信封。

        Args:
            path: 以 /api 开头的接口路径。
            params: 查询参数（值为 None 的键会被忽略）。

        Returns:
            Any: 信封中的 data 段。

        Raises:
            ApiError: 服务端返回 error 非空（含中文原因与 error_code）。
            urllib.error.URLError: 服务未启动或网络不可达。
        """
        query = {k: v for k, v in (params or {}).items() if v is not None}
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            envelope = json.loads(response.read().decode("utf-8"))
        self.last_envelope = envelope
        if envelope.get("error"):
            raise ApiError(f"{envelope.get('error')}（{envelope.get('error_code')}）")
        return envelope.get("data")

    # ---------- 只读接口 ----------
    def health(self) -> Dict[str, Any]:
        """读取服务健康状态（构建号/数据源状态/缓存统计）。

        Returns:
            dict: /api/health 的 data 段。
        """
        return self._get("/api/health")

    def index(self, market: str = "all") -> Dict[str, Any]:
        """读取核心指数快照。

        Args:
            market: all/a/hk/us。

        Returns:
            dict: 含 items/source/caliber/as_of。
        """
        return self._get("/api/market/index", {"market": market})

    def quote(self, symbol: str, market: str = "a") -> Dict[str, Any]:
        """读取单只标的实时报价。

        Args:
            symbol: 股票代码（600519/00700/AAPL）。
            market: a/hk/us。

        Returns:
            dict: 含 quote（价格/涨跌/成交额等）与双徽章字段。
        """
        return self._get("/api/market/quote", {"symbol": symbol, "market": market})

    def kline(self, symbol: str, market: str = "a", period: str = "day",
              adjust: str = "qfq") -> Dict[str, Any]:
        """读取 K 线。

        Args:
            symbol: 股票代码。
            market: a/hk/us。
            period: day/week/month（按服务端枚举）。
            adjust: qfq/hfq/none。

        Returns:
            dict: 含 items（date/open/close/high/low/volume）。
        """
        return self._get("/api/market/kline", {
            "symbol": symbol, "market": market, "period": period, "adjust": adjust,
        })

    def news_flash(self, limit: int = 20) -> Dict[str, Any]:
        """读取财联社快讯（含情绪初评）。

        Args:
            limit: 返回条数。

        Returns:
            dict: 含 items（title/time/sentiment）。
        """
        return self._get("/api/news/flash", {"limit": limit})

    def widgets(self, page: str = "") -> List[Dict[str, Any]]:
        """读取组件注册表。

        Args:
            page: 页面路由（如 #/sectors）；留空返回全部。

        Returns:
            list[dict]: 组件元数据列表。
        """
        return self._get("/api/widgets", {"page": page or None}).get("items", [])

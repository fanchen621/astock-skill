"""
弹性数据层：自动故障转移 + 反限流
═══════════════════════════════════════════════════════════════════════════
多数据源链（新浪/腾讯/东方财富/网易），自动切换：
  - 健康分数管理（连续成功+1，失败-5）
  - 限流检测（403/429/空返回）→ 指数退避
  - UA轮换 + Referer伪装
  - 降级通知

替代 sina_realtime.py 作为主要数据获取入口。
"""
from __future__ import annotations

import json
import random
import time
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Callable, Tuple

from loguru import logger

from config import DATA_SOURCE_CONFIG

# ─── 数据源健康管理 ──────────────────────────────────────────────────────────

class SourceHealth:
    def __init__(self, name: str, init_score: int = 10):
        self.name = name
        self.score = init_score
        self.consecutive_fails = 0
        self.last_fail_time = 0.0
        self.last_success_time = 0.0
        self.total_calls = 0
        self.total_fails = 0
        self.backoff_until = 0.0    # 退避截止时间
        self.current_interval = DATA_SOURCE_CONFIG["base_interval"]

    def record_success(self):
        self.score = min(self.score + DATA_SOURCE_CONFIG["health_reward"], 20)
        self.consecutive_fails = 0
        self.last_success_time = time.time()
        self.total_calls += 1
        self.current_interval = DATA_SOURCE_CONFIG["base_interval"]

    def record_failure(self, is_rate_limit: bool = False):
        self.score += DATA_SOURCE_CONFIG["health_penalty"]
        self.consecutive_fails += 1
        self.last_fail_time = time.time()
        self.total_calls += 1
        self.total_fails += 1
        if is_rate_limit:
            backoff = min(
                self.current_interval * DATA_SOURCE_CONFIG["backoff_factor"],
                DATA_SOURCE_CONFIG["max_backoff"])
            self.current_interval = backoff
            self.backoff_until = time.time() + backoff
            logger.warning(f"[弹性] {self.name} 被限流，退避{backoff:.0f}秒")

    @property
    def is_available(self) -> bool:
        if time.time() < self.backoff_until:
            return False
        return self.score >= DATA_SOURCE_CONFIG["health_min_to_use"]

    @property
    def error_rate(self) -> float:
        return self.total_fails / self.total_calls if self.total_calls > 0 else 0

    def to_dict(self) -> dict:
        return {
            "name": self.name, "score": self.score,
            "available": self.is_available,
            "consecutive_fails": self.consecutive_fails,
            "error_rate": round(self.error_rate, 3),
            "backoff_until": self.backoff_until,
        }


# ─── 全局健康状态 ─────────────────────────────────────────────────────────────

_source_health: Dict[str, SourceHealth] = {}
_active_index_source = "sina"
_active_stock_source = "sina"


def get_source_health(name: str) -> SourceHealth:
    if name not in _source_health:
        _source_health[name] = SourceHealth(name, DATA_SOURCE_CONFIG["health_score_init"])
    return _source_health[name]


def get_all_source_status() -> List[dict]:
    return [h.to_dict() for h in _source_health.values()]


# ─── HTTP 请求工具 ────────────────────────────────────────────────────────────

def _get_ua() -> str:
    return random.choice(DATA_SOURCE_CONFIG["user_agents"])


def _http_get(url: str, referer: str = "", encoding: str = "utf-8",
              timeout: float = 3.0) -> str:
    """通用HTTP GET，带UA轮换和Referer伪装"""
    headers = {"User-Agent": _get_ua()}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode(encoding, errors="ignore")


def _is_rate_limited(error) -> bool:
    """判断是否被限流"""
    if isinstance(error, urllib.error.HTTPError):
        return error.code in (403, 429, 503)
    return False


# ════════════════════════════════════════════════════════════════════════════
# 指数数据源实现
# ════════════════════════════════════════════════════════════════════════════

def _fetch_indices_sina() -> List[Dict]:
    """新浪指数"""
    text = _http_get(
        "https://hq.sinajs.cn/list=sh000001,sz399001,sz399006",
        referer="https://finance.sina.com.cn", encoding="gbk")
    return _parse_sina_index_text(text)


def _fetch_indices_tencent() -> List[Dict]:
    """腾讯指数"""
    text = _http_get(
        "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006",
        referer="https://finance.qq.com", encoding="gbk")
    return _parse_tencent_index_text(text)


def _fetch_indices_eastmoney() -> List[Dict]:
    """东方财富指数"""
    url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?"
           "fields=f1,f2,f3,f4,f12,f14&secids=1.000001,0.399001,0.399006")
    text = _http_get(url, referer="https://www.eastmoney.com")
    return _parse_eastmoney_index_json(text)


def _fetch_indices_netease() -> List[Dict]:
    """网易指数"""
    text = _http_get(
        "https://api.money.126.net/data/feed/0000001,1399001,1399006",
        referer="https://money.163.com")
    return _parse_netease_index_json(text)


# ─── 指数解析器 ───────────────────────────────────────────────────────────────

INDEX_NAMES = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指"}


def _parse_sina_index_text(text: str) -> List[Dict]:
    out = []
    for ln in text.strip().splitlines():
        if "=" not in ln:
            continue
        try:
            left, right = ln.split("=", 1)
            code = left.strip().split("_")[-1]
            parts = right.strip().strip(";").strip('"').split(",")
            if len(parts) < 6:
                continue
            prev = float(parts[2] or 0)
            last = float(parts[3] or 0)
            pct = ((last - prev) / prev * 100) if prev else 0
            out.append({"code": code, "name": parts[0] or INDEX_NAMES.get(code, code),
                        "last": round(last, 3), "prev_close": round(prev, 3),
                        "pct": round(pct, 3)})
        except Exception:
            continue
    return out


def _parse_tencent_index_text(text: str) -> List[Dict]:
    code_map = {"sh000001": "sh000001", "sz399001": "sz399001", "sz399006": "sz399006"}
    out = []
    for ln in text.strip().splitlines():
        if "=" not in ln:
            continue
        try:
            left, right = ln.split("=", 1)
            parts = right.strip().strip(";").strip('"').split("~")
            if len(parts) < 45:
                continue
            name = parts[1]
            code_raw = parts[2]
            last = float(parts[3] or 0)
            prev = float(parts[4] or 0)
            pct = float(parts[32] or 0)
            code = f"sh{code_raw}" if code_raw.startswith("0") and len(code_raw) == 6 else f"sz{code_raw}"
            # 腾讯格式特殊处理
            if "000001" in left:
                code = "sh000001"
            elif "399001" in left:
                code = "sz399001"
            elif "399006" in left:
                code = "sz399006"
            out.append({"code": code, "name": name,
                        "last": round(last, 3), "prev_close": round(prev, 3),
                        "pct": round(pct, 3)})
        except Exception:
            continue
    return out


def _parse_eastmoney_index_json(text: str) -> List[Dict]:
    code_map = {"000001": "sh000001", "399001": "sz399001", "399006": "sz399006"}
    out = []
    try:
        data = json.loads(text)
        for item in (data.get("data", {}).get("diff", []) or []):
            code_raw = str(item.get("f12", ""))
            code = code_map.get(code_raw, code_raw)
            name = item.get("f14", INDEX_NAMES.get(code, code))
            last = float(item.get("f2", 0)) / 100 if item.get("f2") else 0
            pct = float(item.get("f3", 0)) / 100 if item.get("f3") else 0
            if last > 100:  # 东方财富有时返回未除100的值
                pass
            out.append({"code": code, "name": name,
                        "last": round(last, 3), "pct": round(pct, 3),
                        "prev_close": 0})
    except Exception:
        pass
    return out


def _parse_netease_index_json(text: str) -> List[Dict]:
    code_map = {"0000001": "sh000001", "1399001": "sz399001", "1399006": "sz399006"}
    out = []
    try:
        # 网易返回 _ntes_quote_callback({...});
        text = text.strip()
        if text.startswith("_ntes_quote_callback"):
            text = text[text.index("(") + 1:text.rindex(")")]
        data = json.loads(text)
        for key, item in data.items():
            code = code_map.get(key, key)
            name = item.get("name", INDEX_NAMES.get(code, ""))
            last = float(item.get("price", 0))
            prev = float(item.get("yestclose", 0))
            pct = float(item.get("percent", 0)) * 100
            out.append({"code": code, "name": name,
                        "last": round(last, 3), "prev_close": round(prev, 3),
                        "pct": round(pct, 3)})
    except Exception:
        pass
    return out


# ════════════════════════════════════════════════════════════════════════════
# 个股数据源实现
# ════════════════════════════════════════════════════════════════════════════

def _to_sina_code(code: str) -> str:
    code = code.strip().replace("sh", "").replace("sz", "")
    if code.startswith("6"):
        return f"sh{code}"
    return f"sz{code}"


def _fetch_stocks_sina(codes: List[str]) -> Dict[str, Dict]:
    sina_codes = [_to_sina_code(c) for c in codes]
    result = {}
    for i in range(0, len(sina_codes), 40):
        batch = sina_codes[i:i + 40]
        text = _http_get(
            f"https://hq.sinajs.cn/list={','.join(batch)}",
            referer="https://finance.sina.com.cn", encoding="gbk")
        for ln in text.strip().splitlines():
            if "=" not in ln:
                continue
            try:
                left, right = ln.split("=", 1)
                code_raw = left.strip().split("_")[-1]
                parts = right.strip().strip(";").strip('"').split(",")
                if len(parts) < 32:
                    continue
                code = code_raw.replace("sh", "").replace("sz", "")
                prev = float(parts[2] or 0)
                last = float(parts[3] or 0)
                if last <= 0:
                    continue
                pct = (last - prev) / prev * 100 if prev else 0
                result[code] = {
                    "code": code, "name": parts[0],
                    "price": round(last, 2), "prev_close": round(prev, 2),
                    "change_pct": round(pct, 2),
                    "volume": float(parts[8] or 0),
                    "amount": float(parts[9] or 0),
                    "amount_100m": round(float(parts[9] or 0) / 1e8, 2),
                }
            except Exception:
                continue
    return result


def _fetch_stocks_eastmoney(codes: List[str]) -> Dict[str, Dict]:
    secids = []
    for c in codes:
        c = c.strip().replace("sh", "").replace("sz", "")
        if c.startswith("6"):
            secids.append(f"1.{c}")
        else:
            secids.append(f"0.{c}")
    url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?"
           f"fields=f1,f2,f3,f4,f5,f6,f12,f14&secids={','.join(secids[:50])}")
    text = _http_get(url, referer="https://www.eastmoney.com")
    result = {}
    try:
        data = json.loads(text)
        for item in (data.get("data", {}).get("diff", []) or []):
            code = str(item.get("f12", ""))
            name = item.get("f14", "")
            last = float(item.get("f2", 0))
            pct = float(item.get("f3", 0))
            vol = float(item.get("f5", 0))
            amt = float(item.get("f6", 0))
            if last <= 0:
                continue
            # 东方财富push2有时数值需要/100
            if last > 10000:
                last /= 100
                pct /= 100
            result[code] = {
                "code": code, "name": name,
                "price": round(last, 2), "change_pct": round(pct, 2),
                "volume": vol, "amount": amt,
                "amount_100m": round(amt / 1e8, 2) if amt > 1e6 else round(amt, 2),
            }
    except Exception:
        pass
    return result


def _fetch_stocks_tencent(codes: List[str]) -> Dict[str, Dict]:
    qq_codes = []
    for c in codes:
        c = c.strip().replace("sh", "").replace("sz", "")
        if c.startswith("6"):
            qq_codes.append(f"sh{c}")
        else:
            qq_codes.append(f"sz{c}")
    result = {}
    for i in range(0, len(qq_codes), 30):
        batch = qq_codes[i:i + 30]
        text = _http_get(
            f"https://qt.gtimg.cn/q={','.join(batch)}",
            referer="https://finance.qq.com", encoding="gbk")
        for ln in text.strip().splitlines():
            if "=" not in ln:
                continue
            try:
                parts = ln.split("=", 1)[1].strip().strip(";").strip('"').split("~")
                if len(parts) < 45:
                    continue
                code = parts[2]
                last = float(parts[3] or 0)
                prev = float(parts[4] or 0)
                pct = float(parts[32] or 0)
                vol = float(parts[36] or 0)
                amt = float(parts[37] or 0)
                if last <= 0:
                    continue
                result[code] = {
                    "code": code, "name": parts[1],
                    "price": round(last, 2), "prev_close": round(prev, 2),
                    "change_pct": round(pct, 2),
                    "volume": vol, "amount": amt,
                    "amount_100m": round(amt / 1e8, 2) if amt > 1e6 else round(amt, 2),
                }
            except Exception:
                continue
    return result


# ════════════════════════════════════════════════════════════════════════════
# 弹性获取核心（自动故障转移）
# ════════════════════════════════════════════════════════════════════════════

_INDEX_FETCHERS: Dict[str, Callable] = {
    "sina": _fetch_indices_sina,
    "tencent": _fetch_indices_tencent,
    "eastmoney": _fetch_indices_eastmoney,
    "netease": _fetch_indices_netease,
}

_STOCK_FETCHERS: Dict[str, Callable] = {
    "sina": _fetch_stocks_sina,
    "eastmoney": _fetch_stocks_eastmoney,
    "tencent": _fetch_stocks_tencent,
}

_last_good_indices: List[Dict] = []
_last_good_stocks: Dict[str, Dict] = {}


def resilient_fetch_indices() -> List[Dict]:
    """
    弹性获取指数行情，自动在多数据源间切换。
    失败时返回上次成功数据。
    """
    global _last_good_indices, _active_index_source

    priority = DATA_SOURCE_CONFIG["index_priority"]
    for source_name in priority:
        health = get_source_health(f"index_{source_name}")
        if not health.is_available:
            continue

        fetcher = _INDEX_FETCHERS.get(source_name)
        if not fetcher:
            continue

        try:
            time.sleep(health.current_interval)
            result = fetcher()
            if result and len(result) > 0:
                health.record_success()
                _last_good_indices = result
                if _active_index_source != source_name:
                    logger.info(f"[弹性] 指数源切换: {_active_index_source} -> {source_name}")
                    _active_index_source = source_name
                return result
            else:
                health.record_failure()
        except Exception as e:
            rate_limited = _is_rate_limited(e)
            health.record_failure(is_rate_limit=rate_limited)
            logger.debug(f"[弹性] {source_name}指数失败: {e}")

    # 全部失败，返回缓存
    if _last_good_indices:
        logger.warning("[弹性] 所有指数源失败，使用缓存")
        return _last_good_indices
    raise RuntimeError("所有指数数据源不可用")


def resilient_fetch_stocks(codes: List[str]) -> Dict[str, Dict]:
    """弹性获取个股行情"""
    global _last_good_stocks, _active_stock_source

    if not codes:
        return {}

    priority = DATA_SOURCE_CONFIG["stock_priority"]
    for source_name in priority:
        health = get_source_health(f"stock_{source_name}")
        if not health.is_available:
            continue

        fetcher = _STOCK_FETCHERS.get(source_name)
        if not fetcher:
            continue

        try:
            time.sleep(health.current_interval)
            result = fetcher(codes)
            if result:
                health.record_success()
                _last_good_stocks.update(result)
                if _active_stock_source != source_name:
                    logger.info(f"[弹性] 个股源切换: {_active_stock_source} -> {source_name}")
                    _active_stock_source = source_name
                return result
            else:
                health.record_failure()
        except Exception as e:
            rate_limited = _is_rate_limited(e)
            health.record_failure(is_rate_limit=rate_limited)
            logger.debug(f"[弹性] {source_name}个股失败: {e}")

    # 全部失败返回缓存
    logger.warning("[弹性] 所有个股源失败，使用缓存")
    return {c: _last_good_stocks[c] for c in codes if c in _last_good_stocks}


def get_active_sources() -> Dict[str, str]:
    """获取当前活跃数据源"""
    return {"index": _active_index_source, "stock": _active_stock_source}


def reset_all_sources():
    """重置所有数据源健康状态"""
    for h in _source_health.values():
        h.score = DATA_SOURCE_CONFIG["health_score_init"]
        h.consecutive_fails = 0
        h.backoff_until = 0
        h.current_interval = DATA_SOURCE_CONFIG["base_interval"]
    logger.info("[弹性] 所有数据源健康状态已重置")

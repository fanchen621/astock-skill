"""
新浪实时行情 API（免费、<2秒延迟、零依赖）
═══════════════════════════════════════════════════════════════════════════
核心数据源，保证盘中 3 秒级实时性。

接口：
  - fetch_indices()         → 上证/深证/创业板实时行情
  - fetch_stocks(codes)     → 批量个股实时行情
  - fetch_stock_minute(code)→ 个股分时数据

基于 Codex feeds.py，增强为完整个股行情支持。
"""
from __future__ import annotations

import time
import urllib.request
from typing import Dict, List, Optional

from loguru import logger

# ─── 新浪行情 API 配置 ──────────────────────────────────────────────────────
_SINA_HQ_URL = "https://hq.sinajs.cn/list="
_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}
_TIMEOUT = 2.5  # 秒

# ─── 指数代码映射 ────────────────────────────────────────────────────────────
INDEX_MAP = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
}

# ─── 缓存（避免同一秒重复请求） ──────────────────────────────────────────────
_cache: Dict[str, tuple] = {}  # key -> (timestamp, data)
_CACHE_TTL = 2  # 秒


def _get_cached(key: str):
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return data
    return None


def _set_cached(key: str, data):
    _cache[key] = (time.time(), data)


# ════════════════════════════════════════════════════════════════════════════
# 原始请求
# ════════════════════════════════════════════════════════════════════════════

def _fetch_sina_raw(symbols: str) -> str:
    """请求新浪行情原始文本"""
    url = _SINA_HQ_URL + symbols
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read().decode("gbk", errors="ignore")


# ════════════════════════════════════════════════════════════════════════════
# 指数行情
# ════════════════════════════════════════════════════════════════════════════

def _parse_index_line(line: str) -> Optional[Dict]:
    """解析指数行情行"""
    try:
        left, right = line.split("=", 1)
        code = left.strip().split("_")[-1]
        body = right.strip().strip(";").strip('"')
        parts = body.split(",")
        if len(parts) < 4:
            return None
        name = parts[0] or INDEX_MAP.get(code, code)
        open_price = float(parts[1] or 0)
        prev_close = float(parts[2] or 0)
        last = float(parts[3] or 0)
        high = float(parts[4] or 0)
        low = float(parts[5] or 0)
        volume = float(parts[8] or 0)     # 成交量（手）
        amount = float(parts[9] or 0)     # 成交额（元）
        pct = ((last - prev_close) / prev_close * 100) if prev_close else 0.0
        return {
            "code": code,
            "name": name,
            "open": round(open_price, 3),
            "prev_close": round(prev_close, 3),
            "last": round(last, 3),
            "high": round(high, 3),
            "low": round(low, 3),
            "pct": round(pct, 3),
            "volume": volume,
            "amount": amount,
        }
    except Exception:
        return None


def fetch_indices() -> List[Dict]:
    """
    获取 A 股主要指数实时行情
    Returns: [{"code","name","last","pct","volume","amount",...}]
    Raises: RuntimeError if all indices fail
    """
    cached = _get_cached("indices")
    if cached is not None:
        return cached

    symbols = ",".join(INDEX_MAP.keys())
    text = _fetch_sina_raw(symbols)
    out = []
    for ln in text.strip().splitlines():
        ln = ln.strip()
        if not ln or "=" not in ln:
            continue
        parsed = _parse_index_line(ln)
        if parsed:
            out.append(parsed)

    if not out:
        raise RuntimeError("新浪指数行情返回为空")

    _set_cached("indices", out)
    logger.debug(f"[新浪] 指数行情获取成功，{len(out)} 条")
    return out


# ════════════════════════════════════════════════════════════════════════════
# 个股行情
# ════════════════════════════════════════════════════════════════════════════

def _parse_stock_line(line: str) -> Optional[Dict]:
    """
    解析个股行情行
    新浪格式（A股）：
    var hq_str_sh600519="贵州茅台,1849.00,1842.00,1855.88,1860.00,1838.01,
    1855.88,1855.90,12345678,2280000000,100,1855.88,200,1855.80,...,2026-03-31,15:00:03,00"
    字段顺序：名称,今开,昨收,最新,最高,最低,买一,卖一,成交量(股),成交额(元),
    买一量,买一价,买二量,买二价,...,日期,时间,状态
    """
    try:
        left, right = line.split("=", 1)
        code_raw = left.strip().split("_")[-1]
        body = right.strip().strip(";").strip('"')
        if not body:
            return None
        parts = body.split(",")
        if len(parts) < 32:
            return None

        name = parts[0]
        open_price = float(parts[1] or 0)
        prev_close = float(parts[2] or 0)
        last = float(parts[3] or 0)
        high = float(parts[4] or 0)
        low = float(parts[5] or 0)
        volume = float(parts[8] or 0)     # 成交量（股）
        amount = float(parts[9] or 0)     # 成交额（元）

        if last <= 0 or prev_close <= 0:
            return None

        pct = (last - prev_close) / prev_close * 100
        # 简易量比估算（当日成交量 / 前日参考，精确量比需要历史数据）
        # 这里仅返回原始数据，量比由 akshare 补充
        # 换手率同理

        # 代码标准化（去掉 sh/sz 前缀，只保留6位数字）
        code = code_raw.replace("sh", "").replace("sz", "")

        return {
            "code": code,
            "sina_code": code_raw,
            "name": name,
            "open": round(open_price, 2),
            "prev_close": round(prev_close, 2),
            "price": round(last, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "change_pct": round(pct, 2),
            "volume": volume,               # 股
            "amount": round(amount, 2),      # 元
            "amount_100m": round(amount / 1e8, 2),  # 亿
        }
    except Exception:
        return None


def _to_sina_code(code: str) -> str:
    """将6位股票代码转为新浪格式（sh/sz前缀）"""
    code = code.strip()
    if code.startswith(("sh", "sz")):
        return code
    if code.startswith(("6",)):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    return f"sh{code}"


def fetch_stocks(codes: List[str]) -> Dict[str, Dict]:
    """
    批量获取个股实时行情
    Args: codes - 6位股票代码列表 ["600519", "000001", ...]
    Returns: {code: {name,price,change_pct,volume,amount,...}}
    """
    if not codes:
        return {}

    # 检查缓存
    cache_key = "stocks_" + ",".join(sorted(codes[:20]))
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    # 新浪批量限制约40个，分批请求
    result = {}
    batch_size = 40
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        sina_codes = [_to_sina_code(c) for c in batch]
        try:
            text = _fetch_sina_raw(",".join(sina_codes))
            for ln in text.strip().splitlines():
                ln = ln.strip()
                if not ln or "=" not in ln:
                    continue
                parsed = _parse_stock_line(ln)
                if parsed:
                    result[parsed["code"]] = parsed
        except Exception as e:
            logger.warning(f"[新浪] 批量行情第{i//batch_size+1}批失败: {e}")

    if result:
        _set_cached(cache_key, result)
        logger.debug(f"[新浪] 个股行情获取成功，{len(result)}/{len(codes)} 条")

    return result


def fetch_stock_single(code: str) -> Optional[Dict]:
    """获取单只股票实时行情"""
    result = fetch_stocks([code])
    pure_code = code.replace("sh", "").replace("sz", "")
    return result.get(pure_code)


# ════════════════════════════════════════════════════════════════════════════
# 最近快照缓存（供 fallback 使用）
# ════════════════════════════════════════════════════════════════════════════

_last_good_indices: Optional[List[Dict]] = None
_last_good_stocks: Dict[str, Dict] = {}


def fetch_indices_safe() -> List[Dict]:
    """安全版指数获取，失败时返回上次成功数据"""
    global _last_good_indices
    try:
        data = fetch_indices()
        _last_good_indices = data
        return data
    except Exception as e:
        logger.warning(f"[新浪] 指数获取失败: {e}，使用上次缓存")
        if _last_good_indices:
            return _last_good_indices
        raise


def fetch_stocks_safe(codes: List[str]) -> Dict[str, Dict]:
    """安全版个股获取，失败时返回上次成功数据"""
    global _last_good_stocks
    try:
        data = fetch_stocks(codes)
        _last_good_stocks.update(data)
        return data
    except Exception as e:
        logger.warning(f"[新浪] 个股行情失败: {e}，使用上次缓存")
        return {c: _last_good_stocks[c] for c in codes if c in _last_good_stocks}

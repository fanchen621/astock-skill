"""
深度数据源（适配云端环境，绕过IP封禁）
═══════════════════════════════════════════════════════════════════════════
根因：OpenClaw 云端为腾讯云 IP，被东方财富/新浪等 IP 级封禁，
akshare 底层 requests 直连东方财富 API 必然 RemoteDisconnected。

解决方案：
  1. akshare 调用前注入 requests.Session + 浏览器级 Headers
  2. 关键接口失败时自动降级为直接 HTTP 请求（绕过 akshare）
  3. 个股历史数据使用腾讯/网易备选源（不经过东方财富）
  4. 全部请求加 Session 连接池 + Keep-Alive + 重试
"""
from __future__ import annotations

import datetime
import json
import random
import time
import functools
import urllib.request
from typing import Dict, List, Optional, Tuple

import pandas as pd
from loguru import logger

from config import DATA_SOURCE_CONFIG

# ─── 尝试安装 requests session 补丁 ──────────────────────────────────────────
_session = None
try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    _session = requests.Session()
    _session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    })
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
    _session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=5, pool_maxsize=10))
    _session.mount("http://", HTTPAdapter(max_retries=retry, pool_connections=5, pool_maxsize=10))
except ImportError:
    pass

try:
    import akshare as ak
    # 给 akshare 内部注入带重试的 session（核心补丁）
    if _session and ak:
        try:
            import akshare.utils as ak_utils
            if hasattr(ak_utils, 'requests'):
                ak_utils.requests = type('Module', (), {'get': _session.get, 'post': _session.post, 'Session': lambda: _session})()
        except Exception:
            pass
except ImportError:
    ak = None
    logger.warning("[数据] akshare 未安装")

# ─── TTL 缓存 ────────────────────────────────────────────────────────────────
_cache: Dict[str, Tuple[float, object]] = {}


def _cached(key: str, ttl: int = 60):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            now = time.time()
            if key in _cache:
                ts, val = _cache[key]
                if now - ts < ttl:
                    return val
            result = fn(*args, **kwargs)
            _cache[key] = (now, result)
            return result
        return wrapper
    return decorator


def clear_cache():
    _cache.clear()


def _get_ua() -> str:
    return random.choice(DATA_SOURCE_CONFIG["user_agents"])


def _http_get(url: str, referer: str = "", encoding: str = "utf-8",
              timeout: float = 10.0) -> str:
    """带浏览器伪装的 HTTP GET"""
    if _session:
        headers = {"Referer": referer} if referer else {}
        headers["User-Agent"] = _get_ua()
        resp = _session.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        if encoding == "auto":
            resp.encoding = resp.apparent_encoding
        else:
            resp.encoding = encoding
        return resp.text
    else:
        headers = {"User-Agent": _get_ua()}
        if referer:
            headers["Referer"] = referer
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode(encoding, errors="ignore")


def _safe_ak_call(fn, *args, **kwargs):
    """安全调用 akshare，带重试和超长 timeout"""
    for attempt in range(3):
        try:
            result = fn(*args, **kwargs)
            if result is not None:
                return result
        except Exception as e:
            err = str(e)
            if attempt < 2 and ("RemoteDisconnected" in err or "Connection aborted" in err
                                 or "ConnectionReset" in err):
                time.sleep(3 * (attempt + 1))
                # 重建 session
                if _session:
                    _session.close()
                    _session.mount("https://", HTTPAdapter(
                        max_retries=Retry(total=3, backoff_factor=2),
                        pool_connections=5, pool_maxsize=10))
                continue
            raise
    return None


# ════════════════════════════════════════════════════════════════════════════
# 涨停池（akshare 优先，失败走直接 HTTP）
# ════════════════════════════════════════════════════════════════════════════

@_cached("zt_pool", ttl=120)
def get_zt_pool(date: Optional[str] = None) -> pd.DataFrame:
    if not date:
        date = datetime.date.today().strftime("%Y%m%d")

    # 方式1: 同花顺涨停池（最稳定，不依赖东方财富）
    try:
        url = ("https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?"
               "page=1&limit=100&field=2024,199112,199113,199114"
               "&order_field=199112&order_type=0&ajax=1")
        text = _http_get(url, referer="https://data.10jqka.com.cn/")
        if text and "status_code" in text:
            import json as _json
            data = _json.loads(text)
            info = data.get("data", {}).get("info", [])
            if info:
                rows = []
                for item in info:
                    rows.append({
                        "代码": item.get("code", ""),
                        "名称": item.get("name", ""),
                        "涨跌幅": float(item.get("change_rate", 0)),
                        "最新价": 0,
                        "成交额": 0,
                        "连板数": 1 if item.get("is_again_limit") != 1 else 2,  # 同花顺只有是否再次涨停
                        "首板时间": "",
                        "板块": item.get("change_tag", ""),
                    })
                return pd.DataFrame(rows)
    except Exception as e:
        logger.debug(f"[数据] 同花顺涨停池失败: {e}")

    # 方式2: akshare
    if ak:
        try:
            df = _safe_ak_call(ak.stock_zt_pool_em, date=date)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logger.debug(f"[数据] akshare涨停池失败: {e}")

    # 方式3: 直接请求东方财富 API（绕过 akshare 的 session 问题）
    try:
        url = (f"https://push2ex.eastmoney.com/getTopicZTPool?"
               f"ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&"
               f"Ession=f&date={date}&_={int(time.time()*1000)}")
        text = _http_get(url, referer="https://quote.eastmoney.com")
        data = json.loads(text)
        pool = data.get("data", {}).get("pool", [])
        if pool:
            rows = []
            for item in pool:
                rows.append({
                    "代码": item.get("c", ""),
                    "名称": item.get("n", ""),
                    "涨跌幅": item.get("zdp", 0),
                    "最新价": item.get("p", 0) / 1000 if item.get("p") else 0,
                    "成交额": item.get("amount", 0),
                    "流通市值": item.get("ltsz", 0),
                    "连板数": item.get("days", 1),
                    "首次涨停时间": item.get("fbt", ""),
                })
            return pd.DataFrame(rows)
    except Exception as e:
        logger.debug(f"[数据] 直接HTTP涨停池失败: {e}")

    return pd.DataFrame()


def get_zt_stats(date: Optional[str] = None) -> Dict:
    df = get_zt_pool(date)
    if df.empty:
        return {"zt_count": 0, "highest_board": 0, "broken_rate": 0.0,
                "consecutive_ladder": {}}

    zt_count = len(df)
    board_col = None
    for c in ["连板数", "连续涨停天数", "连板天数", "days"]:
        if c in df.columns:
            board_col = c
            break
    highest_board = int(df[board_col].max()) if board_col else 1

    ladder: Dict[int, List[Dict]] = {}
    if board_col:
        for _, row in df.iterrows():
            days = int(row.get(board_col, 1) or 1)
            name = str(row.get("名称", row.get("name", "")))
            code = str(row.get("代码", row.get("code", "")))
            ladder.setdefault(days, []).append({"name": name, "code": code})

    # 炸板率
    broken_rate = 0.0
    try:
        url = (f"https://push2ex.eastmoney.com/getTopicZBPool?"
               f"ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&"
               f"date={date or datetime.date.today().strftime('%Y%m%d')}&_={int(time.time()*1000)}")
        text = _http_get(url, referer="https://quote.eastmoney.com")
        data = json.loads(text)
        zb_pool = data.get("data", {}).get("pool", [])
        broken_count = len(zb_pool)
        broken_rate = broken_count / (zt_count + broken_count) if (zt_count + broken_count) > 0 else 0
    except Exception:
        pass

    return {
        "zt_count": zt_count,
        "highest_board": highest_board,
        "broken_rate": round(broken_rate, 3),
        "consecutive_ladder": dict(sorted(ladder.items(), key=lambda x: x[0], reverse=True)),
    }


# ════════════════════════════════════════════════════════════════════════════
# 龙虎榜
# ════════════════════════════════════════════════════════════════════════════

@_cached("lhb", ttl=300)
def get_lhb(date: Optional[str] = None) -> pd.DataFrame:
    if not date:
        date = datetime.date.today().strftime("%Y%m%d")
    if ak:
        for fn_name in ["stock_lhb_detail_em", "stock_lhb_ggtj_dtl_em", "stock_lhb_jgmmtj_em"]:
            fn = getattr(ak, fn_name, None)
            if fn is None:
                continue
            try:
                df = _safe_ak_call(fn, start_date=date, end_date=date)
                if df is not None and not df.empty:
                    return df
            except Exception:
                pass
    logger.debug("[数据] 龙虎榜：所有方式失败")
    return pd.DataFrame()


# ════════════════════════════════════════════════════════════════════════════
# 板块资金流向（直接 HTTP，不依赖 akshare 参数）
# ════════════════════════════════════════════════════════════════════════════

@_cached("sector_flow", ttl=120)
def get_sector_flow() -> List[Dict]:
    """直接请求东方财富板块资金流 API"""
    # 方式1: 直接 HTTP（最可靠）
    try:
        url = ("https://push2.eastmoney.com/api/qt/clist/get?"
               "fid=f62&po=1&pz=20&np=1&fltt=2&invt=2&"
               "fs=m:90+t:2&fields=f12,f14,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124"
               f"&ut=b2884a393a59ad64002292a3e90d46a5&_={int(time.time()*1000)}")
        text = _http_get(url, referer="https://data.eastmoney.com")
        data = json.loads(text)
        result = []
        for item in (data.get("data", {}).get("diff", []) or [])[:15]:
            name = item.get("f14", "")
            pct = item.get("f3", 0)
            net_flow = item.get("f62", 0)
            if name:
                result.append({
                    "name": str(name),
                    "change_pct": round(float(pct), 2) if pct else 0,
                    "net_flow": round(float(net_flow), 2) if net_flow else 0,
                })
        if result:
            return result
    except Exception as e:
        logger.debug(f"[数据] 直接HTTP板块资金失败: {e}")

    # 方式2: akshare 多种参数尝试
    if ak:
        for fn_name, kwargs in [
            ("stock_sector_fund_flow_rank", {"indicator": "今日"}),
            ("stock_sector_fund_flow_rank", {"indicator": "今日", "sector_type": "行业资金流"}),
            ("stock_fund_flow_industry", {}),
        ]:
            fn = getattr(ak, fn_name, None)
            if fn is None:
                continue
            try:
                df = _safe_ak_call(fn, **kwargs)
                if df is not None and not df.empty:
                    cols = df.columns.tolist()
                    name_col = next((c for c in cols if "行业" in c or "名称" in c or "板块" in c), cols[0])
                    pct_col = next((c for c in cols if "涨跌" in c), None)
                    flow_col = next((c for c in cols if "主力" in c and "净" in c), None)
                    result = []
                    for _, row in df.head(15).iterrows():
                        result.append({
                            "name": str(row.get(name_col, "")),
                            "change_pct": float(row.get(pct_col, 0) or 0) if pct_col else 0,
                            "net_flow": float(row.get(flow_col, 0) or 0) if flow_col else 0,
                        })
                    if result:
                        return result
            except Exception:
                continue
    return []


def get_main_net_flow() -> float:
    sectors = get_sector_flow()
    if not sectors:
        return 0.0
    total = sum(s.get("net_flow", 0) for s in sectors)
    return round(total / 1e8, 2) if abs(total) > 1e6 else round(total, 2)


# ════════════════════════════════════════════════════════════════════════════
# 市场宽度（直接 HTTP 获取涨跌家数）
# ════════════════════════════════════════════════════════════════════════════

@_cached("market_breadth", ttl=60)
def get_market_breadth() -> Dict:
    result = {
        "up_count": 0, "down_count": 0, "flat_count": 0,
        "up_down_ratio": 1.0, "zt_count": 0, "dt_count": 0,
    }

    # 方式1: 东方财富大盘统计 API（轻量，不用拉全部股票）
    try:
        url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?"
               "fields=f1,f2,f3,f4,f12,f14&secids=1.000001&"
               f"ut=b2884a393a59ad64002292a3e90d46a5&_={int(time.time()*1000)}")
        text = _http_get(url, referer="https://quote.eastmoney.com")
        # 这个 API 返回的是指数，我们从涨停池推算
        zt_stats = get_zt_stats()
        result["zt_count"] = zt_stats.get("zt_count", 0)
    except Exception:
        pass

    # 方式2: akshare 全市场（如果不被封的话）
    if ak and result["up_count"] == 0:
        try:
            df = _safe_ak_call(ak.stock_zh_a_spot_em)
            if df is not None and not df.empty:
                pct_col = "涨跌幅"
                if pct_col in df.columns:
                    ups = len(df[df[pct_col] > 0])
                    downs = len(df[df[pct_col] < 0])
                    result["up_count"] = ups
                    result["down_count"] = downs
                    result["flat_count"] = len(df) - ups - downs
                    result["up_down_ratio"] = round(ups / downs, 2) if downs > 0 else 99.0
                    result["zt_count"] = len(df[df[pct_col] >= 9.8])
                    result["dt_count"] = len(df[df[pct_col] <= -9.8])
                    return result
        except Exception as e:
            logger.debug(f"[数据] akshare市场宽度失败: {e}")

    # 方式3: 从涨停池+指数涨跌幅估算
    if result["up_count"] == 0:
        try:
            from feeds.resilient import resilient_fetch_indices
            indices = resilient_fetch_indices()
            sh_pct = 0
            for idx in indices:
                if idx.get("code") == "sh000001":
                    sh_pct = idx.get("pct", 0)
                    break
            # 根据大盘涨跌幅粗估涨跌家数
            if sh_pct > 1:
                result["up_count"] = 3000
                result["down_count"] = 1500
            elif sh_pct > 0:
                result["up_count"] = 2500
                result["down_count"] = 2000
            elif sh_pct > -1:
                result["up_count"] = 2000
                result["down_count"] = 2500
            else:
                result["up_count"] = 1500
                result["down_count"] = 3000
            result["up_down_ratio"] = round(result["up_count"] / max(result["down_count"], 1), 2)
        except Exception:
            pass

    return result


# ════════════════════════════════════════════════════════════════════════════
# 北向资金
# ════════════════════════════════════════════════════════════════════════════

@_cached("north_flow", ttl=180)
def get_north_flow() -> float:
    """直接请求东方财富北向资金API（沪股通+深股通净买入）"""
    # 方式1: 直接 HTTP - kamt.get 可用
    try:
        url = ("https://push2.eastmoney.com/api/qt/kamt.get?"
               f"fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f55,f56&"
               f"ut=b2884a393a59ad64002292a3e90d46a5&_={int(time.time()*1000)}")
        text = _http_get(url, referer="https://data.eastmoney.com/")
        if text:
            data = json.loads(text)
            # 提取沪股通+深股通净买入
            hk2sh_net = 0.0  # 沪股通净买入（北上）
            sh2hk_net = 0.0  # 深股通净买入（北上）
            hk2sh = data.get("data", {}).get("hk2sh", {})
            sh2hk = data.get("data", {}).get("sh2hk", {})
            hk2sh_net = float(hk2sh.get("dayNetAmtIn", 0) or 0)
            sh2hk_net = float(sh2hk.get("dayNetAmtIn", 0) or 0)
            total = (hk2sh_net + sh2hk_net) / 1e8  # 转换为亿元
            if total != 0:
                return round(total, 2)
    except Exception as e:
        logger.debug(f"[数据] 直接HTTP北向资金失败: {e}")

    # 方式2: akshare 多 API
    if ak:
        for fn_name in ["stock_hsgt_north_net_flow_in_em", "stock_em_hsgt_north_net_flow_in",
                         "stock_hsgt_fund_flow_summary_em"]:
            fn = getattr(ak, fn_name, None)
            if fn is None:
                continue
            try:
                df = _safe_ak_call(fn, indicator="今日")
                if df is not None and not df.empty:
                    val_col = None
                    for c in df.columns:
                        if "value" in c.lower() or "净流入" in c or "净买入" in c:
                            val_col = c
                            break
                    if val_col is None:
                        val_col = df.columns[-1]
                    val = float(df.iloc[-1][val_col])
                    return round(val / 1e8, 2) if abs(val) > 1e6 else round(val, 2)
            except Exception:
                continue
    return 0.0


# ════════════════════════════════════════════════════════════════════════════
# 财经新闻
# ════════════════════════════════════════════════════════════════════════════

@_cached("news", ttl=180)
def get_financial_news(limit: int = 10) -> List[Dict]:
    # 方式1: 东方财富7x24快讯（直接HTTP）
    try:
        url = (f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?"
               f"client=web&biz=web_home_fl&column=102&order=1&needInteractData=0&"
               f"page_index=1&page_size={limit}&req_trace={int(time.time()*1000)}")
        text = _http_get(url, referer="https://kuaixun.eastmoney.com")
        data = json.loads(text)
        result = []
        for item in (data.get("data", {}).get("list", []) or [])[:limit]:
            title = item.get("title", "")
            if title:
                result.append({
                    "title": title,
                    "time": item.get("showTime", ""),
                    "source": "东方财富",
                    "importance": _judge_importance(title),
                })
        if result:
            return result
    except Exception as e:
        logger.debug(f"[数据] 直接HTTP新闻失败: {e}")

    # 方式2: akshare
    if ak:
        try:
            df = _safe_ak_call(ak.stock_news_em)
            if df is not None and not df.empty:
                result = []
                for _, row in df.head(limit).iterrows():
                    title = str(row.get("新闻标题", row.get("title", "")))
                    result.append({
                        "title": title,
                        "time": str(row.get("发布时间", "")),
                        "source": str(row.get("新闻来源", "")),
                        "importance": _judge_importance(title),
                    })
                return result
        except Exception:
            pass
    return []


def _judge_importance(title: str) -> str:
    from config import NEWS_KEYWORDS_HIGH, NEWS_KEYWORDS_MID
    for kw in NEWS_KEYWORDS_HIGH:
        if kw in title:
            return "high"
    for kw in NEWS_KEYWORDS_MID:
        if kw in title:
            return "medium"
    return "low"


# ════════════════════════════════════════════════════════════════════════════
# 个股历史数据（多源备选，绕过东方财富封禁）
# ════════════════════════════════════════════════════════════════════════════

def get_stock_history(code: str, days: int = 60) -> Optional[pd.DataFrame]:
    """个股日K线（优先腾讯源，不经过东方财富）"""
    # 方式1: 腾讯日K线 API（最稳定，不封云端IP）
    try:
        df = _fetch_history_tencent(code, days)
        if df is not None and len(df) >= 20:
            return df
    except Exception as e:
        logger.debug(f"[数据] {code} 腾讯K线失败: {e}")

    # 方式2: 网易日K线
    try:
        df = _fetch_history_netease(code, days)
        if df is not None and len(df) >= 20:
            return df
    except Exception as e:
        logger.debug(f"[数据] {code} 网易K线失败: {e}")

    # 方式3: akshare（大概率被封，但还是尝试）
    if ak:
        try:
            df = _safe_ak_call(
                ak.stock_zh_a_hist, symbol=code, period="daily",
                start_date=(datetime.date.today() - datetime.timedelta(days=days * 2)).strftime("%Y%m%d"),
                end_date=datetime.date.today().strftime("%Y%m%d"), adjust="qfq")
            if df is not None and not df.empty:
                df = df.rename(columns={
                    "日期": "date", "开盘": "open", "收盘": "close",
                    "最高": "high", "最低": "low", "成交量": "volume",
                    "成交额": "amount", "换手率": "turnover_pct",
                })
                return df.tail(days)
        except Exception as e:
            logger.debug(f"[数据] {code} akshare K线失败: {e}")

    return None


def _fetch_history_tencent(code: str, days: int = 60) -> Optional[pd.DataFrame]:
    """腾讯财经日K线 API"""
    code = code.strip()
    if code.startswith("6"):
        prefix = "sh"
    else:
        prefix = "sz"
    symbol = f"{prefix}{code}"

    end = datetime.date.today().strftime("%Y-%m-%d")
    start = (datetime.date.today() - datetime.timedelta(days=days * 2)).strftime("%Y-%m-%d")

    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={symbol},day,{start},{end},{days * 2},qfq")
    text = _http_get(url, referer="https://web.ifzq.gtimg.cn", encoding="utf-8")
    data = json.loads(text)

    klines = (data.get("data", {}).get(symbol, {}).get("day")
              or data.get("data", {}).get(symbol, {}).get("qfqday")
              or [])
    if not klines:
        return None

    rows = []
    for k in klines:
        if len(k) >= 6:
            rows.append({
                "date": k[0], "open": float(k[1]), "close": float(k[2]),
                "high": float(k[3]), "low": float(k[4]), "volume": float(k[5]),
            })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    return df.tail(days)


def _fetch_history_netease(code: str, days: int = 60) -> Optional[pd.DataFrame]:
    """网易财经日K线 API"""
    code = code.strip()
    if code.startswith("6"):
        prefix = "0"
    else:
        prefix = "1"
    symbol = f"{prefix}{code}"

    end = datetime.date.today().strftime("%Y%m%d")
    start = (datetime.date.today() - datetime.timedelta(days=days * 2)).strftime("%Y%m%d")

    url = (f"https://quotes.money.163.com/service/chddata.html?"
           f"code={symbol}&start={start}&end={end}&"
           f"fields=TCLOSE;HIGH;LOW;TOPEN;LCLOSE;CHG;PCHG;TURNOVER;VOTURNOVER;VATURNOVER")
    text = _http_get(url, referer="https://quotes.money.163.com", encoding="gbk")

    lines = text.strip().split("\n")
    if len(lines) < 3:
        return None

    rows = []
    for line in lines[1:]:  # 跳过表头
        parts = line.strip().split(",")
        if len(parts) < 10:
            continue
        try:
            rows.append({
                "date": parts[0].strip("'"),
                "close": float(parts[3]) if parts[3] and parts[3] != "None" else 0,
                "high": float(parts[4]) if parts[4] and parts[4] != "None" else 0,
                "low": float(parts[5]) if parts[5] and parts[5] != "None" else 0,
                "open": float(parts[6]) if parts[6] and parts[6] != "None" else 0,
                "volume": float(parts[9]) if parts[9] and parts[9] != "None" else 0,
                "turnover_pct": float(parts[8]) if parts[8] and parts[8] != "None" else 0,
            })
        except (ValueError, IndexError):
            continue

    if not rows:
        return None
    df = pd.DataFrame(rows)
    df = df[df["close"] > 0]
    df = df.sort_values("date").tail(days)
    return df


def compute_stock_features(code: str, current_price: float = 0,
                           current_change_pct: float = 0) -> Optional[Dict]:
    """计算选股特征"""
    hist = get_stock_history(code, 60)
    if hist is None or len(hist) < 20:
        return None

    try:
        close_series = hist["close"].astype(float)
        volume_series = hist["volume"].astype(float)

        high_20d = close_series.tail(20).max()
        low_60d = close_series.min()
        avg_vol_5d = volume_series.tail(5).mean()
        today_vol = volume_series.iloc[-1]
        ma5 = close_series.tail(5).mean()
        ma20 = close_series.tail(20).mean()

        price = current_price or float(close_series.iloc[-1])
        breakout_20d = 1 if price >= high_20d else 0
        from_60d_low_pct = ((price - low_60d) / low_60d * 100) if low_60d > 0 else 0
        volume_ratio_5d = (today_vol / avg_vol_5d) if avg_vol_5d > 0 else 1.0

        turnover = 0.0
        if "turnover_pct" in hist.columns:
            tp = hist["turnover_pct"].iloc[-1]
            turnover = float(tp) if pd.notna(tp) else 0.0

        return {
            "breakout_20d": breakout_20d,
            "from_60d_low_pct": round(from_60d_low_pct, 2),
            "volume_ratio_5d": round(volume_ratio_5d, 2),
            "turnover_pct": round(turnover, 2),
            "ma5": round(float(ma5), 2),
            "ma20": round(float(ma20), 2),
            "high_20d": round(float(high_20d), 2),
            "low_60d": round(float(low_60d), 2),
        }
    except Exception as e:
        logger.debug(f"[数据] {code} 特征计算失败: {e}")
        return None


# ════════════════════════════════════════════════════════════════════════════
# 全市场实时行情
# ════════════════════════════════════════════════════════════════════════════

@_cached("a_spot_em", ttl=30)
def get_all_stocks_spot() -> pd.DataFrame:
    """全市场行情（优先直接HTTP，备选akshare）"""
    # 方式1: 东方财富全市场 API（直接HTTP）
    try:
        url = ("https://push2.eastmoney.com/api/qt/clist/get?"
               "pn=1&pz=50&po=1&np=1&fltt=2&invt=2&fid=f3&"
               "fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&"
               "fields=f12,f14,f2,f3,f5,f6,f8,f10&"
               f"ut=b2884a393a59ad64002292a3e90d46a5&_={int(time.time()*1000)}")
        text = _http_get(url, referer="https://quote.eastmoney.com")
        data = json.loads(text)
        rows = []
        for item in (data.get("data", {}).get("diff", []) or []):
            rows.append({
                "code": str(item.get("f12", "")),
                "name": item.get("f14", ""),
                "price": float(item.get("f2", 0) or 0),
                "change_pct": float(item.get("f3", 0) or 0),
                "volume": float(item.get("f5", 0) or 0),
                "amount": float(item.get("f6", 0) or 0),
                "turnover_pct": float(item.get("f8", 0) or 0),
                "volume_ratio": float(item.get("f10", 0) or 0),
            })
        if rows:
            return pd.DataFrame(rows)
    except Exception as e:
        logger.debug(f"[数据] 直接HTTP全市场失败: {e}")

    # 方式2: akshare
    if ak:
        try:
            df = _safe_ak_call(ak.stock_zh_a_spot_em)
            if df is not None and not df.empty:
                df = df.rename(columns={
                    "代码": "code", "名称": "name",
                    "最新价": "price", "涨跌幅": "change_pct",
                    "成交量": "volume", "成交额": "amount",
                    "量比": "volume_ratio", "换手率": "turnover_pct",
                })
                for col in ["price", "change_pct", "volume", "amount", "volume_ratio", "turnover_pct"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
                return df
        except Exception as e:
            logger.debug(f"[数据] akshare全市场失败: {e}")

    return pd.DataFrame()


def enrich_stock_data(sina_data: Dict[str, Dict]) -> Dict[str, Dict]:
    spot_df = get_all_stocks_spot()
    if spot_df.empty:
        return sina_data
    for code, data in sina_data.items():
        row = spot_df[spot_df["code"] == code]
        if not row.empty:
            r = row.iloc[0]
            data["volume_ratio"] = float(r.get("volume_ratio", 0) or 0)
            data["turnover_pct"] = float(r.get("turnover_pct", 0) or 0)
    return sina_data

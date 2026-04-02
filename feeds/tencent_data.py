"""
tencent_data.py - 腾讯行情数据源（直连，绕过东方财富封禁）
支持：A股指数/个股、港股、美股、期货
格式：GBK编码，~分隔字段
"""
import re
import time
import json
import logging
from typing import Dict, List, Optional, Tuple
from functools import lru_cache

import requests

logger = logging.getLogger("tencent_data")

TENXUN_BASE = "https://qt.gtimg.cn/q="
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ── 常用指数代码 ──────────────────────────────────────────────────────────
INDICES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
    "sz399005": "中小100",
    "sh000016": "上证50",
    "sh000688": "科创50",
    "sz399300": "创业板综",
    "sz399673": "创业板50",
    "hkHSI": "恒生指数",
    "hkHSTECH": "恒生科技",
    "usDJI": "道琼斯",
    "usIXIC": "纳斯达克",
    "usSPX": "标普500",
}

# ── A股自选股 ─────────────────────────────────────────────────────────────
WATCHLIST = {
    "sh601318": "中国平安",
    "sh600519": "贵州茅台",
    "sz300750": "宁德时代",
    "sz002475": "立讯精密",
    "sh600036": "招商银行",
    "sz000001": "平安银行",
}

# ── 期货代码 ─────────────────────────────────────────────────────────────
FUTURES = {
    "hf_GC": "黄金期货",
    "hf_SI": "白银期货",
    "hf_CL": "原油期货",
    "hf_AU": "沪金主力",
    "hf_AG": "沪银主力",
    "hf_CU": "沪铜主力",
    "hf_AL": "沪铝主力",
    "hf_ZN": "沪锌主力",
    "hf_PB": "沪铅主力",
    "hf_RB": "螺纹钢",
    "hf_HC": "热卷",
    "hf_FG": "玻璃",
    "hf_MA": "甲醇",
    "hf_RU": "橡胶",
}

# ── 港股代码 ─────────────────────────────────────────────────────────────
HKSTOCKS = {
    "hk00700": "腾讯控股",
    "hk03690": "美团",
    "hk01810": "小米",
    "hk02382": "港交所",
    "hk09988": "阿里巴巴",
    "hk02020": "安踏体育",
    "hk01024": "快手",
    "hk09618": "京东",
    "hk06690": "海尔智家",
    "hk00941": "华为",
}

# ── 美股代码 ─────────────────────────────────────────────────────────────
USSTOCKS = {
    "usTSLA": "特斯拉",
    "usNVDA": "英伟达",
    "usAAPL": "苹果",
    "usMSFT": "微软",
    "usBABA": "阿里巴巴",
    "usGOOG": "谷歌",
    "usAMZN": "亚马逊",
    "usMETA": "Meta",
    "usNFLX": "Netflix",
    "usAMD": "AMD",
}


def _gbk_get(url: str) -> Optional[str]:
    """发送请求，自动处理GBK编码"""
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=8)
        resp.encoding = "gbk"
        return resp.text
    except Exception as e:
        logger.debug(f"[tencent] 请求失败 {url[-30:]}: {e}")
        return None


def _parse_tencent(text: str) -> Dict[str, Dict]:
    """解析腾讯行情数据，返回 {code: fields_dict}"""
    result = {}
    try:
        text = text.strip()
        if not text:
            return result
        # 匹配 v_code="..."
        pattern = r'v[_\w]+="([^"]+)"'
        for line in text.split(';'):
            line = line.strip()
            if not line:
                continue
            m = re.search(r'v[_\w]+="([^"]+)"', line)
            if not m:
                continue
            fields_str = m.group(1)
            # 期货使用逗号分隔，股票/指数使用~分隔
            if ',' in fields_str and fields_str.count('~') < 3:
                fields = fields_str.split(',')
            else:
                fields = fields_str.split('~')
            if len(fields) < 5:
                continue
            # 提取代码
            code_m = re.search(r'v[_\w]+', line)
            if not code_m:
                continue
            raw_code = code_m.group(0).replace('v_', '').replace('v_', '').replace('v', '')
            # s_ 前缀的是简化指数
            if raw_code.startswith('s_'):
                code = raw_code[2:]
            else:
                code = raw_code
            result[code] = fields
    except Exception as e:
        logger.debug(f"[tencent] 解析失败: {e}")
    return result


# ── 指数行情 ─────────────────────────────────────────────────────────────

def get_indices() -> Dict[str, Dict]:
    """获取所有指数实时行情"""
    codes = ",".join(INDICES.keys())
    url = f"{TENXUN_BASE}{codes}"
    text = _gbk_get(url)
    if not text:
        return {}
    data = _parse_tencent(text)
    result = {}
    for code in INDICES:
        fields = data.get(code) or data.get("s_" + code) or {}
        if not fields or len(fields) < 5:
            continue
        try:
            result[code] = {
                "name": fields[1],
                "code": fields[2],
                "price": float(fields[3]) if fields[3] else 0,
                "change": float(fields[4]) if fields[3] else 0,
                "pct": float(fields[32]) if len(fields) > 32 and fields[32] else 0,
                "volume": float(fields[6]) if len(fields) > 6 and fields[6] else 0,
                "time": fields[30] if len(fields) > 30 else "",
                "raw": fields,
            }
        except (ValueError, IndexError):
            continue
    return result


# ── 个股行情 ─────────────────────────────────────────────────────────────

def get_stocks(codes: List[str]) -> Dict[str, Dict]:
    """获取指定股票实时行情"""
    if not codes:
        return {}
    joined = ",".join(codes)
    url = f"{TENXUN_BASE}{joined}"
    text = _gbk_get(url)
    if not text:
        return {}
    data = _parse_tencent(text)
    result = {}
    for code in codes:
        raw_code = code.replace("sh", "").replace("sz", "").replace("bj", "")
        prefix = code[:2]
        # 尝试多种格式
        fields = data.get(code) or data.get(raw_code) or data.get(prefix + raw_code) or {}
        if not fields or len(fields) < 5:
            continue
        try:
            result[code] = {
                "name": fields[1],
                "code": fields[2],
                "price": float(fields[3]) if fields[3] else 0,
                "yesterday": float(fields[4]) if len(fields) > 4 and fields[4] else 0,
                "open": float(fields[5]) if len(fields) > 5 and fields[5] else 0,
                "volume": float(fields[6]) if len(fields) > 6 and fields[6] else 0,
                "bid1": float(fields[9]) if len(fields) > 9 and fields[9] else 0,
                "ask1": float(fields[19]) if len(fields) > 19 and fields[19] else 0,
                "pct": float(fields[32]) if len(fields) > 32 and fields[32] else 0,
                "turnover": float(fields[37]) if len(fields) > 37 and fields[37] else 0,
                "pe": float(fields[39]) if len(fields) > 39 and fields[39] else 0,
                "market_cap": float(fields[44]) if len(fields) > 44 and fields[44] else 0,
                "time": fields[30] if len(fields) > 30 else "",
            }
        except (ValueError, IndexError):
            continue
    return result


def get_watchlist() -> Dict[str, Dict]:
    """获取自选股实时行情"""
    return get_stocks(list(WATCHLIST.keys()))


# ── 期货行情 ─────────────────────────────────────────────────────────────

def get_futures() -> Dict[str, Dict]:
    """获取期货实时行情"""
    codes = list(FUTURES.keys())
    joined = ",".join(codes)
    url = f"{TENXUN_BASE}{joined}"
    text = _gbk_get(url)
    if not text:
        return {}
    data = _parse_tencent(text)
    result = {}
    for code in codes:
        fields = data.get(code) or {}
        if not fields or len(fields) < 5:
            continue
        try:
            result[code] = {
                "name": FUTURES.get(code, fields[0]),
                "price": float(fields[0]) if fields[0] else 0,
                "change": float(fields[1]) if fields[1] else 0,
                "open": float(fields[2]) if fields[2] else 0,
                "high": float(fields[3]) if fields[3] else 0,
                "low": float(fields[4]) if fields[4] else 0,
                "time": fields[6] if len(fields) > 6 else "",
            }
        except (ValueError, IndexError):
            continue
    return result


# ── 港股行情 ─────────────────────────────────────────────────────────────

def get_hk_stocks() -> Dict[str, Dict]:
    """获取港股实时行情"""
    codes = list(HKSTOCKS.keys())
    joined = ",".join(codes)
    url = f"{TENXUN_BASE}{joined}"
    text = _gbk_get(url)
    if not text:
        return {}
    data = _parse_tencent(text)
    result = {}
    for code in codes:
        fields = data.get(code) or {}
        if not fields or len(fields) < 5:
            continue
        try:
            result[code] = {
                "name": HKSTOCKS.get(code, fields[1]),
                "code": fields[2] if len(fields) > 2 else code,
                "price": float(fields[3]) if fields[3] else 0,
                "change": float(fields[31]) if len(fields) > 31 and fields[31] else 0,
                "pct": float(fields[32]) if len(fields) > 32 and fields[32] else 0,
                "time": fields[16] if len(fields) > 16 else "",
            }
        except (ValueError, IndexError):
            continue
    return result


# ── 美股行情 ─────────────────────────────────────────────────────────────

def get_us_stocks() -> Dict[str, Dict]:
    """获取美股实时行情"""
    codes = list(USSTOCKS.keys())
    joined = ",".join(codes)
    url = f"{TENXUN_BASE}{joined}"
    text = _gbk_get(url)
    if not text:
        return {}
    data = _parse_tencent(text)
    result = {}
    for code in codes:
        fields = data.get(code) or {}
        if not fields or len(fields) < 5:
            continue
        try:
            result[code] = {
                "name": USSTOCKS.get(code, fields[1]),
                "code": fields[2] if len(fields) > 2 else code,
                "price": float(fields[3]) if fields[3] else 0,
                "change": float(fields[31]) if len(fields) > 31 and fields[31] else 0,
                "pct": float(fields[32]) if len(fields) > 32 and fields[32] else 0,
                "time": fields[30] if len(fields) > 30 else "",
                "currency": "USD",
            }
        except (ValueError, IndexError):
            continue
    return result


# ── 全市场行情（批量） ──────────────────────────────────────────────────

def get_all_a_stocks(batch: int = 50) -> List[Dict]:
    """获取全市场A股行情（分批）"""
    # 主板：上海600/601/603/605开头，深圳000/001/002/003开头
    # 创业板：sz300开头
    # 科创板：sh688开头
    batches = [
        # 上证主板
        ["sh600000", "sh600001", "sh600004", "sh600009", "sh600010", "sh600011", "sh600015", "sh600016", "sh600018", "sh600019"],
        ["sh600028", "sh600030", "sh600031", "sh600036", "sh600048", "sh600050", "sh600104", "sh600109", "sh600111", "sh600115"],
        ["sh600150", "sh600176", "sh600183", "sh600196", "sh600309", "sh600406", "sh600519", "sh600547", "sh600570", "sh600585"],
        ["sh600588", "sh600690", "sh600703", "sh600745", "sh600809", "sh600837", "sh600887", "sh600893", "sh600909", "sh600918"],
        ["sh600926", "sh601006", "sh601012", "sh601066", "sh601088", "sh601111", "sh601138", "sh601166", "sh601169", "sh601186"],
        ["sh601211", "sh601229", "sh601288", "sh601318", "sh601336", "sh601390", "sh601398", "sh601601", "sh601628", "sh601668"],
        ["sh601688", "sh601698", "sh601728", "sh601766", "sh601800", "sh601816", "sh601818", "sh601857", "sh601888", "sh601939"],
        ["sh601985", "sh601988", "sh601989", "sh601995", "sh603259", "sh603288", "sh603501", "sh603799", "sh603986", "sh605500"],
        # 深证主板
        ["sz000001", "sz000002", "sz000063", "sz000066", "sz000100", "sz000333", "sz000338", "sz000425", "sz000568", "sz000651"],
        ["sz000661", "sz000725", "sz000768", "sz000858", "sz000876", "sz000895", "sz000938", "sz000001", "sz000002", "sz000004"],
        # 创业板
        ["sz300001", "sz300002", "sz300003", "sz300014", "sz300015", "sz300033", "sz300059", "sz300122", "sz300124", "sz300142"],
        ["sz300223", "sz300274", "sz300347", "sz300364", "sz300408", "sz300474", "sz300496", "sz300498", "sz300750", "sz300896"],
        # 科创板
        ["sh688001", "sh688008", "sh688012", "sh688036", "sh688111", "sh688126", "sh688169", "sh688185", "sh688223", "sh688981"],
    ]
    
    all_stocks = []
    for batch_codes in batches:
        stocks = get_stocks(batch_codes)
        all_stocks.extend(list(stocks.values()))
        time.sleep(0.2)  # 避免请求过快
    
    return all_stocks


# ── 统一快照 ─────────────────────────────────────────────────────────────

def build_full_snapshot() -> Dict:
    """构建完整市场快照（所有腾讯数据源聚合）"""
    snapshot = {
        "ts": int(time.time()),
        "source": "tencent",
        "indices": {},
        "watchlist": {},
        "futures": {},
        "hk": {},
        "us": {},
    }
    
    # 指数
    indices = get_indices()
    snapshot["indices"] = indices
    
    # 自选股
    watchlist = get_watchlist()
    snapshot["watchlist"] = watchlist
    
    # 期货
    futures = get_futures()
    snapshot["futures"] = futures
    
    # 港股
    hk = get_hk_stocks()
    snapshot["hk"] = hk
    
    # 美股
    us = get_us_stocks()
    snapshot["us"] = us
    
    return snapshot


# ── 入口测试 ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== 指数 ===")
    idx = get_indices()
    for k, v in idx.items():
        print(f"  {v['name']}: {v['price']} ({v['pct']:+.2f}%)")
    
    print("\n=== 自选股 ===")
    wl = get_watchlist()
    for k, v in wl.items():
        print(f"  {v['name']}: {v['price']} ({v['pct']:+.2f}%)")
    
    print("\n=== 期货 ===")
    fut = get_futures()
    for k, v in fut.items():
        print(f"  {v['name']}: {v['price']} ({v['change']:+.2f})")
    
    print("\n=== 港股 ===")
    hk = get_hk_stocks()
    for k, v in list(hk.items())[:5]:
        print(f"  {v['name']}: {v['price']} ({v['pct']:+.2f}%)")
    
    print("\n=== 美股 ===")
    us = get_us_stocks()
    for k, v in list(us.items())[:5]:
        print(f"  {v['name']}: \${v['price']} ({v['pct']:+.2f}%)")

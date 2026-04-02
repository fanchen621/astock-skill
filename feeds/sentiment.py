"""
舆情引擎：散户情绪反指标
═══════════════════════════════════════════════════════════════════════════
数据源：东方财富股吧 / 雪球 / 同花顺社区
核心逻辑：散户一致看多 = 危险信号

RSI (Retail Sentiment Index):
  > 0.85 → 极度乐观 → 仓位砍半
  0.7-0.85 → 偏乐观 → 仓位8折
  0.4-0.7 → 中性 → 正常
  0.25-0.4 → 偏悲观 → 正常
  < 0.25 → 极度恐慌 → 仓位7折（恐慌也要控制）
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.request
import urllib.error
import random
from typing import Dict, List, Optional, Tuple

from loguru import logger

from config import SENTIMENT_CONFIG, DATA_SOURCE_CONFIG

# ─── 缓存 ────────────────────────────────────────────────────────────────────
_sentiment_cache: Dict[str, Tuple[float, object]] = {}
_CACHE_TTL = 120  # 2分钟


def _get_ua() -> str:
    return random.choice(DATA_SOURCE_CONFIG["user_agents"])


def _http_get(url: str, referer: str = "", encoding: str = "utf-8",
              timeout: float = 5.0) -> str:
    headers = {
        "User-Agent": _get_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode(encoding, errors="ignore")


# ════════════════════════════════════════════════════════════════════════════
# 东方财富股吧（A股最大散户社区）
# ════════════════════════════════════════════════════════════════════════════

def _crawl_guba_market() -> List[Dict]:
    """爬取股吧热门帖子（大盘/沪深两市）"""
    posts = []
    try:
        # 上证指数吧
        url = "https://guba.eastmoney.com/list,zssh000001,0,f.html"
        html = _http_get(url, referer="https://guba.eastmoney.com", encoding="utf-8")
        posts.extend(_parse_guba_html(html, "guba_sh"))
    except Exception as e:
        logger.debug(f"[舆情] 股吧上证失败: {e}")

    try:
        # 创业板吧
        url = "https://guba.eastmoney.com/list,zssz399006,0,f.html"
        html = _http_get(url, referer="https://guba.eastmoney.com", encoding="utf-8")
        posts.extend(_parse_guba_html(html, "guba_cy"))
    except Exception as e:
        logger.debug(f"[舆情] 股吧创业板失败: {e}")

    return posts


def _crawl_guba_stock(code: str) -> List[Dict]:
    """爬取个股股吧"""
    try:
        url = f"https://guba.eastmoney.com/list,{code},0,f.html"
        html = _http_get(url, referer="https://guba.eastmoney.com", encoding="utf-8")
        return _parse_guba_html(html, f"guba_{code}")
    except Exception as e:
        logger.debug(f"[舆情] 股吧{code}失败: {e}")
        return []


def _parse_guba_html(html: str, source: str) -> List[Dict]:
    """从股吧HTML提取帖子标题"""
    posts = []
    # 匹配帖子标题（股吧页面结构）
    patterns = [
        re.compile(r'class="title[^"]*"[^>]*>\s*<a[^>]*title="([^"]+)"', re.S),
        re.compile(r'<span class="l3[^"]*"[^>]*><a[^>]*>([^<]+)</a>', re.S),
        re.compile(r'"post_title"\s*:\s*"([^"]+)"'),
    ]
    for pat in patterns:
        for m in pat.finditer(html):
            title = m.group(1).strip()
            if len(title) > 4:
                posts.append({"title": title, "source": source})
    # 也尝试JSON API方式
    try:
        json_pat = re.compile(r'"re":\s*(\d+).*?"post_title"\s*:\s*"([^"]+)"')
        for m in json_pat.finditer(html):
            replies = int(m.group(1))
            title = m.group(2).strip()
            posts.append({"title": title, "source": source, "replies": replies})
    except Exception:
        pass
    return posts[:30]  # 限制数量


# ════════════════════════════════════════════════════════════════════════════
# 雪球（专业投资者社区）
# ════════════════════════════════════════════════════════════════════════════

def _crawl_xueqiu_hot() -> List[Dict]:
    """爬取雪球热帖（多方式兼容）"""
    posts = []

    # 方式1：通过 HTML 页面提取（最稳定）
    try:
        html = _http_get("https://xueqiu.com/today", referer="https://xueqiu.com/",
                         timeout=8)
        patterns = [
            re.compile(r'"title"\s*:\s*"([^"]{8,200})"'),
            re.compile(r'"text"\s*:\s*"([^"]{10,150})"'),
            re.compile(r'"description"\s*:\s*"([^"]{10,150})"'),
        ]
        seen = set()
        for pat in patterns:
            for m in pat.finditer(html):
                title = re.sub(r'<[^>]+>', '', m.group(1)).strip()[:100]
                if len(title) > 6 and title not in seen:
                    seen.add(title)
                    posts.append({"title": title, "source": "xueqiu"})
    except Exception as e:
        logger.debug(f"[舆情] 雪球HTML失败: {e}")

    # 方式2：首页
    if not posts:
        try:
            html = _http_get("https://xueqiu.com/", referer="https://xueqiu.com/", timeout=6)
            pat = re.compile(r'"text"\s*:\s*"([^"]{10,100})"')
            for m in pat.finditer(html):
                posts.append({"title": m.group(1), "source": "xueqiu"})
        except Exception:
            pass

    return posts[:20]


# ════════════════════════════════════════════════════════════════════════════
# 同花顺社区
# ════════════════════════════════════════════════════════════════════════════

def _crawl_ths_hot() -> List[Dict]:
    """爬取同花顺热门话题（多URL兼容）"""
    posts = []

    urls = [
        ("https://t.10jqka.com.cn/", "https://www.10jqka.com.cn/", "gbk"),
        ("https://news.10jqka.com.cn/", "https://www.10jqka.com.cn/", "gbk"),
        ("https://stock.10jqka.com.cn/", "https://www.10jqka.com.cn/", "gbk"),
    ]

    for url, ref, enc in urls:
        if posts:
            break
        try:
            html = _http_get(url, referer=ref, encoding=enc, timeout=6)
            patterns = [
                re.compile(r'class="title[^"]*"[^>]*>([^<]{6,80})<'),
                re.compile(r'<a[^>]*>([^<]{8,60})</a>'),
                re.compile(r'"title"\s*:\s*"([^"]{6,80})"'),
            ]
            seen = set()
            for pat in patterns:
                for m in pat.finditer(html):
                    title = m.group(1).strip()
                    if (len(title) > 6 and title not in seen
                            and not title.startswith("http")
                            and "10jqka" not in title
                            and "javascript" not in title):
                        seen.add(title)
                        posts.append({"title": title, "source": "ths"})
        except Exception as e:
            logger.debug(f"[舆情] 同花顺 {url} 失败: {e}")

    return posts[:20]


# ════════════════════════════════════════════════════════════════════════════
# 情绪分析（关键词权重法，零API成本）
# ════════════════════════════════════════════════════════════════════════════

def _analyze_sentiment(posts: List[Dict]) -> Dict:
    """
    分析帖子列表的情绪
    Returns: {rsi, bullish_score, bearish_score, label, modifier, sample_count, hot_topics}
    """
    if not posts:
        return {"rsi": 0.5, "bullish_score": 0, "bearish_score": 0,
                "label": "中性", "modifier": 1.0, "sample_count": 0, "hot_topics": []}

    bull_kw = SENTIMENT_CONFIG["bullish_keywords"]
    bear_kw = SENTIMENT_CONFIG["bearish_keywords"]

    bull_total = 0.0
    bear_total = 0.0
    topic_freq: Dict[str, int] = {}

    for post in posts:
        title = post.get("title", "")
        for kw, weight in bull_kw.items():
            if kw in title:
                bull_total += weight
        for kw, weight in bear_kw.items():
            if kw in title:
                bear_total += weight
        # 话题统计
        for kw in list(bull_kw.keys()) + list(bear_kw.keys()):
            if kw in title:
                topic_freq[kw] = topic_freq.get(kw, 0) + 1

    total = bull_total + bear_total
    rsi = bull_total / total if total > 0 else 0.5

    # 确定标签和修正系数
    label = "中性"
    modifier = 1.0
    for lbl, cfg in SENTIMENT_CONFIG["rsi_thresholds"].items():
        if cfg["min"] <= rsi <= cfg["max"]:
            label = lbl
            modifier = cfg["modifier"]
            break

    hot_topics = sorted(topic_freq.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "rsi": round(rsi, 3),
        "bullish_score": round(bull_total, 1),
        "bearish_score": round(bear_total, 1),
        "label": label,
        "modifier": modifier,
        "sample_count": len(posts),
        "hot_topics": [{"keyword": k, "count": v} for k, v in hot_topics],
    }


# ════════════════════════════════════════════════════════════════════════════
# 公开接口
# ════════════════════════════════════════════════════════════════════════════

def get_market_sentiment() -> Dict:
    """
    获取市场整体舆情
    Returns: {rsi, label, modifier, bullish_score, bearish_score, ...}
    """
    cache_key = "market_sentiment"
    now = time.time()
    if cache_key in _sentiment_cache:
        ts, data = _sentiment_cache[cache_key]
        if now - ts < _CACHE_TTL:
            return data

    all_posts = []
    # 多源采集，部分失败不影响整体
    all_posts.extend(_crawl_guba_market())
    time.sleep(0.5)
    all_posts.extend(_crawl_xueqiu_hot())
    time.sleep(0.5)
    all_posts.extend(_crawl_ths_hot())

    result = _analyze_sentiment(all_posts)
    result["sources"] = {
        "guba": len([p for p in all_posts if "guba" in p.get("source", "")]),
        "xueqiu": len([p for p in all_posts if p.get("source") == "xueqiu"]),
        "ths": len([p for p in all_posts if p.get("source") == "ths"]),
    }

    _sentiment_cache[cache_key] = (now, result)
    logger.info(f"[舆情] 市场RSI={result['rsi']:.2f} [{result['label']}] "
                f"修正{result['modifier']} 样本{result['sample_count']}")
    return result


def get_stock_sentiment(code: str) -> Dict:
    """获取个股舆情"""
    cache_key = f"stock_{code}"
    now = time.time()
    if cache_key in _sentiment_cache:
        ts, data = _sentiment_cache[cache_key]
        if now - ts < _CACHE_TTL:
            return data

    posts = _crawl_guba_stock(code)
    result = _analyze_sentiment(posts)
    result["code"] = code

    # 个股评分调整建议
    rsi = result["rsi"]
    if rsi > 0.85:
        result["score_adj"] = SENTIMENT_CONFIG["stock_sentiment_score_adj"]["overhyped"]
        result["advice"] = "散户过度看多，降低评分"
    elif 0.55 < rsi < 0.75 and result["sample_count"] > 5:
        result["score_adj"] = SENTIMENT_CONFIG["stock_sentiment_score_adj"]["emerging"]
        result["advice"] = "讨论度上升但未过热，发酵期加分"
    elif rsi < 0.3 and result["sample_count"] > 10:
        result["score_adj"] = SENTIMENT_CONFIG["stock_sentiment_score_adj"]["feared"]
        result["advice"] = "恐慌抛售，可能是底部机会"
    else:
        result["score_adj"] = 0
        result["advice"] = "舆情中性"

    _sentiment_cache[cache_key] = (now, result)
    return result


def get_batch_stock_sentiment(codes: List[str]) -> Dict[str, Dict]:
    """批量获取个股舆情（带间隔防限流）"""
    result = {}
    for code in codes[:5]:  # 限制5只，避免大量请求
        result[code] = get_stock_sentiment(code)
        time.sleep(1.0)  # 间隔1秒
    return result


def save_sentiment_snapshot():
    """保存舆情快照到数据库"""
    from models import get_session, SentimentSnapshot
    import datetime

    market = get_market_sentiment()
    session = get_session()
    try:
        snap = SentimentSnapshot(
            timestamp=datetime.datetime.now(),
            date=datetime.date.today(),
            source="aggregate",
            rsi=market.get("rsi"),
            bullish_score=market.get("bullish_score"),
            bearish_score=market.get("bearish_score"),
            sentiment_label=market.get("label"),
            position_modifier=market.get("modifier"),
            sample_count=market.get("sample_count"),
            hot_topics=market.get("hot_topics"),
            raw_data=market,
        )
        session.add(snap)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"[舆情] 快照保存失败: {e}")
    finally:
        session.close()

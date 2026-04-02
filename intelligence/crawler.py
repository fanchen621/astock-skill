"""
24h知识爬虫：机构策略/量化文章/研报
═══════════════════════════════════════════════════════════════════════════
爬取目标（全免费公开资源）：
  - 东方财富研报摘要
  - 雪球大V帖子
  - 知乎量化专栏
  - 聚宽策略社区

接近XCrawl级别的爬虫能力：
  - 多层重试+指数退避
  - UA/Cookie/Referer全伪装
  - 自动编码检测
  - DOM多模式解析（正则+结构化）
  - 内容去重（hash）
  - 限速自适应
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
import urllib.request
import urllib.error
import urllib.parse
from typing import Dict, List, Optional

from loguru import logger
from config import DATA_SOURCE_CONFIG, INTELLIGENCE_CONFIG


def _get_ua() -> str:
    return random.choice(DATA_SOURCE_CONFIG["user_agents"])


def _smart_get(url: str, referer: str = "", encoding: str = "auto",
               timeout: float = 8.0, retries: int = 3) -> str:
    """
    增强HTTP GET：多层重试+编码检测+反限流
    """
    headers = {
        "User-Agent": _get_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
    }
    if referer:
        headers["Referer"] = referer

    last_err = None
    for attempt in range(retries):
        try:
            if attempt > 0:
                time.sleep(2 ** attempt + random.random())
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                # 自动编码检测
                if encoding == "auto":
                    ct = resp.headers.get("Content-Type", "")
                    if "gbk" in ct.lower() or "gb2312" in ct.lower():
                        return raw.decode("gbk", errors="ignore")
                    elif "utf-8" in ct.lower():
                        return raw.decode("utf-8", errors="ignore")
                    # 尝试utf-8，失败用gbk
                    try:
                        return raw.decode("utf-8")
                    except UnicodeDecodeError:
                        return raw.decode("gbk", errors="ignore")
                return raw.decode(encoding, errors="ignore")
        except Exception as e:
            last_err = e
            if isinstance(e, urllib.error.HTTPError) and e.code in (403, 429):
                time.sleep(5 * (attempt + 1))
    raise last_err or RuntimeError(f"请求失败: {url}")


def _content_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _clean_html(html: str) -> str:
    """去除HTML标签"""
    return re.sub(r'<[^>]+>', '', html).strip()


# ════════════════════════════════════════════════════════════════════════════
# 东方财富研报
# ════════════════════════════════════════════════════════════════════════════

def crawl_eastmoney_research() -> List[Dict]:
    """爬取东方财富研报摘要"""
    results = []
    try:
        url = "https://reportapi.eastmoney.com/report/list?industryCode=*&pageSize=20&industry=*&rating=&ratingChange=&beginTime=&endTime=&pageNo=1&fields=&qType=0&orgCode=&rcode=&_={}".format(int(time.time()*1000))
        text = _smart_get(url, referer="https://data.eastmoney.com")
        data = json.loads(text)
        for item in (data.get("data", []) or []):
            title = item.get("title", "")
            if not title:
                continue
            results.append({
                "source": "eastmoney_research",
                "title": title,
                "summary": item.get("stockName", "") + " " + item.get("indvInduName", ""),
                "url": f"https://data.eastmoney.com/report/zw/{item.get('infoCode', '')}.html",
                "tags": [item.get("indvInduName", ""), item.get("emRatingName", "")],
                "market_view": _extract_view(title),
                "content_hash": _content_hash(title),
            })
    except Exception as e:
        logger.debug(f"[知识] 东方财富研报失败: {e}")
    return results


# ════════════════════════════════════════════════════════════════════════════
# 雪球热帖+大V
# ════════════════════════════════════════════════════════════════════════════

def crawl_xueqiu_articles() -> List[Dict]:
    """爬取雪球热门文章"""
    results = []
    try:
        # 先访问首页获取cookie
        try:
            _smart_get("https://xueqiu.com/", timeout=3, retries=1)
        except Exception:
            pass
        time.sleep(0.5)

        url = "https://xueqiu.com/statuses/hot/listV2.json?since_id=-1&max_id=-1&size=30"
        headers = {
            "User-Agent": _get_ua(),
            "Referer": "https://xueqiu.com/",
            "Accept": "application/json",
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        for item in (data.get("items", []) or []):
            orig = item.get("original_status", {}) or item
            title = orig.get("title", "") or ""
            desc = orig.get("description", "") or ""
            text_content = _clean_html(title + " " + desc)[:500]
            if len(text_content) < 10:
                continue
            user = orig.get("user", {}) or {}
            results.append({
                "source": "xueqiu",
                "title": text_content[:100],
                "summary": text_content,
                "url": f"https://xueqiu.com{orig.get('target', '')}",
                "tags": _extract_tags(text_content),
                "market_view": _extract_view(text_content),
                "author_followers": user.get("followers_count", 0),
                "content_hash": _content_hash(text_content),
            })
    except Exception as e:
        logger.debug(f"[知识] 雪球文章失败: {e}")
    return results


# ════════════════════════════════════════════════════════════════════════════
# 知乎量化专栏
# ════════════════════════════════════════════════════════════════════════════

def crawl_zhihu_quant() -> List[Dict]:
    """爬取知乎量化交易相关文章"""
    results = []
    try:
        keywords = ["量化交易策略", "A股短线", "龙头战法", "涨停板策略"]
        kw = random.choice(keywords)
        url = f"https://www.zhihu.com/search?type=content&q={urllib.parse.quote(kw)}"
        html = _smart_get(url, referer="https://www.zhihu.com")

        # 提取搜索结果
        patterns = [
            re.compile(r'"title"\s*:\s*"([^"]{10,200})"'),
            re.compile(r'"excerpt"\s*:\s*"([^"]{20,500})"'),
            re.compile(r'<span class="Highlight[^"]*">([^<]+)</span>'),
        ]
        titles_seen = set()
        for pat in patterns:
            for m in pat.finditer(html):
                text = _clean_html(m.group(1))[:200]
                if len(text) > 10 and text not in titles_seen:
                    titles_seen.add(text)
                    results.append({
                        "source": "zhihu",
                        "title": text[:100],
                        "summary": text,
                        "tags": _extract_tags(text),
                        "market_view": _extract_view(text),
                        "content_hash": _content_hash(text),
                    })
    except Exception as e:
        logger.debug(f"[知识] 知乎失败: {e}")
    return results[:15]


# ════════════════════════════════════════════════════════════════════════════
# 财经快讯（东方财富/新浪）
# ════════════════════════════════════════════════════════════════════════════

def crawl_news_flash() -> List[Dict]:
    """爬取财经快讯"""
    results = []
    try:
        # 东方财富7x24快讯
        url = f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_home_fl&column=102&order=1&needInteractData=0&page_index=1&page_size=20&req_trace={int(time.time()*1000)}"
        text = _smart_get(url, referer="https://kuaixun.eastmoney.com")
        data = json.loads(text)
        for item in (data.get("data", {}).get("list", []) or []):
            title = item.get("title", "")
            content = item.get("digest", "") or item.get("content", "")
            if title:
                results.append({
                    "source": "eastmoney_flash",
                    "title": title,
                    "summary": _clean_html(content)[:300],
                    "tags": _extract_tags(title + " " + content),
                    "market_view": _extract_view(title),
                    "content_hash": _content_hash(title),
                    "time": item.get("showTime", ""),
                })
    except Exception as e:
        logger.debug(f"[知识] 快讯失败: {e}")
    return results[:20]


# ════════════════════════════════════════════════════════════════════════════
# 文本分析工具
# ════════════════════════════════════════════════════════════════════════════

def _extract_view(text: str) -> str:
    """提取市场观点"""
    bull_words = ["看多", "看涨", "利好", "突破", "主升", "新高", "龙头", "做多", "加仓"]
    bear_words = ["看空", "看跌", "利空", "风险", "泡沫", "见顶", "减仓", "回调", "破位"]
    bull = sum(1 for w in bull_words if w in text)
    bear = sum(1 for w in bear_words if w in text)
    if bull > bear + 1:
        return "bullish"
    if bear > bull + 1:
        return "bearish"
    return "neutral"


def _extract_tags(text: str) -> List[str]:
    """提取话题标签"""
    from config import HOT_THEMES
    tags = []
    for theme in HOT_THEMES:
        if theme in text:
            tags.append(theme)
    # 补充通用标签
    generic = {
        "短线": ["短线", "打板", "涨停", "龙头"],
        "趋势": ["趋势", "均线", "突破", "主升浪"],
        "价值": ["价值", "低估", "分红", "长期"],
        "量化": ["量化", "回测", "策略", "因子"],
    }
    for tag, keywords in generic.items():
        if any(kw in text for kw in keywords):
            tags.append(tag)
    return list(set(tags))[:10]


def extract_params(text: str) -> Dict:
    """从文本中提取量化参数"""
    params = {}
    for param_name, pattern in INTELLIGENCE_CONFIG["param_patterns"].items():
        m = re.search(pattern, text)
        if m:
            try:
                params[param_name] = float(m.group(1))
            except ValueError:
                pass
    return params


# ════════════════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════════════════

def run_crawl_cycle(deep: bool = False) -> List[Dict]:
    """
    执行一轮爬取
    Args:
        deep: True=深度模式（夜间，爬取策略文章），False=快速模式（日间，快讯为主）
    """
    all_items = []

    # 快讯（始终爬）
    all_items.extend(crawl_news_flash())
    time.sleep(1)

    # 研报
    all_items.extend(crawl_eastmoney_research())
    time.sleep(1)

    if deep:
        # 深度模式：爬取文章
        all_items.extend(crawl_xueqiu_articles())
        time.sleep(2)
        all_items.extend(crawl_zhihu_quant())
        time.sleep(1)

    # 提取参数
    for item in all_items:
        text = (item.get("title", "") + " " + item.get("summary", ""))
        item["extracted_params"] = extract_params(text)

    logger.info(f"[知识] 爬取完成: {len(all_items)}条 (深度={deep})")
    return all_items

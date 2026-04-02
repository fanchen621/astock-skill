"""
ProxyPool v2 - 高匿代理池 + IP自动轮换引擎
突破腾讯云IP段对东方财富/新浪等平台的封锁
"""
import asyncio
import aiohttp
import time
import random
import threading
import json
import os
import re
import logging
from typing import Optional
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger("ProxyPool")

TEST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
TEST_TIMEOUT = 10
MAX_PROXIES = 100
MIN_SCORE = 0.2
SCORE_FILE = "/root/.openclaw/workspace/quant/DragonFlow/DragonFlow/data/proxy_scores.json"

# 免费代理API源（稳定可访问）
PROXY_API_SOURCES = [
    ("https://api.proxyscrape.com/v2/", {"request": "get", "params": {"request": "displayproxies", "protocol": "https", "timeout": "10000", "country": "all", "ssl": "all", "anonymity": "all"}}),
    ("https://proxy.proxyscrape.com/resources/get proxies", {}),
    ("https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt", None),
    ("https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt", None),
    ("https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt", None),
    ("https://www.proxy-list.download/api/v1/get?type=http", None),
    ("https://www.proxy-list.download/api/v1/get?type=https", None),
    ("https://www.proxy-list.download/api/v1/get?type=socks4", None),
    ("https://www.proxy-list.download/api/v1/get?type=socks5", None),
]

# 从网页抓取代理
PROXY_PAGE_SOURCES = [
    "https://spys.me/proxy.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/https.txt",
    "https://raw.githubusercontent.com/a2source/proxy-list/main/http.txt",
    "https://raw.githubusercontent.com/a2source/proxy-list/main/https.txt",
]

@dataclass
class ProxyNode:
    ip: str
    port: int
    score: float
    fail_count: int
    last_used: float
    last_check: float
    protocol: str = "http"

    @property
    def url(self) -> str:
        return f"{self.protocol}://{self.ip}:{self.port}"

    @property
    def proxies(self) -> dict:
        return {self.protocol: self.url, "http": self.url}

    def to_dict(self):
        return asdict(self)


class ProxyPool:
    def __init__(self):
        self._lock = threading.RLock()
        self._proxies: dict[str, ProxyNode] = {}
        self._round_robin = 0
        self._ua = self._random_ua()
        self._load()

    def _random_ua(self) -> str:
        ua_list = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/2010 Firefox/121.0",
        ]
        return random.choice(ua_list)

    def _load(self):
        if os.path.exists(SCORE_FILE):
            try:
                with open(SCORE_FILE) as f:
                    data = json.load(f)
                for k, v in data.items():
                    node = ProxyNode(**v)
                    self._proxies[k] = node
                logger.info(f"[ProxyPool] 加载历史代理 {len(self._proxies)} 个")
            except Exception as e:
                logger.warning(f"[ProxyPool] 加载失败: {e}")

    def _save(self):
        os.makedirs(os.path.dirname(SCORE_FILE), exist_ok=True)
        with open(SCORE_FILE, 'w') as f:
            json.dump({k: v.to_dict() for k, v in self._proxies.items()}, f, indent=2)

    # ── 抓取代理 ────────────────────────────────────────────────────────────

    def _fetch_url(self, url: str, timeout: int = 15) -> str:
        import requests
        try:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": self._ua}, 
                               proxies={"http": None, "https": None})  # 直连
            return resp.text
        except Exception as e:
            logger.debug(f"[ProxyPool] 抓取失败 {url[-40:]}: {e}")
            return ""

    def _parse_proxy_list(self, text: str) -> list[tuple[str, int, str]]:
        """解析代理文本，返回 [(ip, port, protocol)]"""
        results = []
        lines = text.splitlines()
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('//'):
                continue
            # 格式1: IP:PORT
            m = re.match(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)$', line)
            if m:
                results.append((m.group(1), int(m.group(2)), "http"))
                continue
            # 格式2: protocol://IP:PORT
            m = re.match(r'^(https?|socks4|socks5)://(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)', line)
            if m:
                results.append((m.group(2), int(m.group(3)), m.group(1).lower()))
        return results

    def _parse_spys_me(self, text: str) -> list[tuple[str, int, str]]:
        """解析 spys.me 格式"""
        results = []
        for line in text.splitlines():
            m = re.match(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)\s+-.*?', line)
            if m:
                results.append((m.group(1), int(m.group(2)), "http"))
        return results

    def fetch_all(self) -> list[tuple[str, int, str]]:
        all_proxies = set()
        # 抓取API源
        for url, extra in PROXY_API_SOURCES:
            try:
                text = self._fetch_url(url)
                if not text:
                    continue
                if "spys" in url:
                    parsed = self._parse_spys_me(text)
                else:
                    parsed = self._parse_proxy_list(text)
                for ip, port, proto in parsed:
                    all_proxies.add((ip, port, proto))
                logger.info(f"[ProxyPool] {url[-40:]} → +{len(parsed)} 个")
            except Exception as e:
                logger.warning(f"[ProxyPool] 抓取 {url[-40:]} 失败: {e}")
            time.sleep(random.uniform(0.3, 0.8))

        # 抓取页面源
        for url in PROXY_PAGE_SOURCES:
            try:
                text = self._fetch_url(url)
                if not text:
                    continue
                parsed = self._parse_proxy_list(text)
                for ip, port, proto in parsed:
                    all_proxies.add((ip, port, proto))
                logger.info(f"[ProxyPool] {url[-40:]} → +{len(parsed)} 个")
            except Exception as e:
                logger.warning(f"[ProxyPool] 抓取 {url[-40:]} 失败: {e}")
            time.sleep(random.uniform(0.3, 0.8))

        logger.info(f"[ProxyPool] 共获取 {len(all_proxies)} 个不重复代理")
        return list(all_proxies)

    # ── 验证 ────────────────────────────────────────────────────────────────

    def verify_one(self, ip: str, port: int, protocol: str = "http") -> tuple[bool, float]:
        """验证单个代理，返回 (是否可用, 响应时间)"""
        proxy_url = f"{protocol}://{ip}:{port}"
        start = time.time()
        try:
            resp = requests.get(
                TEST_URL,
                params={"pn": 1, "pz": 5, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                        "fid": "f3", "fs": "m:0+t:6,m:0+t:80",
                        "fields": "f12,f14,f2,f3", "_": int(time.time() * 1000)},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Referer": "https://finance.eastmoney.com/",
                },
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=TEST_TIMEOUT,
            )
            elapsed = time.time() - start
            if resp.status_code == 200 and "data" in resp.text:
                return True, elapsed
            return False, elapsed
        except Exception as e:
            return False, time.time() - start

    def verify_batch(self, items: list[tuple[str, int, str]], max_workers: int = 30) -> list[ProxyNode]:
        """批量验证，返回可用节点"""
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(self.verify_one, ip, port, proto): (ip, port, proto) 
                      for ip, port, proto in items}
            done = 0
            for fut in as_completed(futures):
                ip, port, proto = futures[fut]
                done += 1
                if done % 50 == 0:
                    logger.info(f"[ProxyPool] 验证进度: {done}/{len(items)}")
                try:
                    ok, elapsed = fut.result()
                    if ok:
                        node = ProxyNode(ip=ip, port=port, protocol=proto,
                                       score=max(0.3, 1.0 - elapsed * 0.1),
                                       fail_count=0, last_used=time.time(),
                                       last_check=time.time())
                        results.append(node)
                        logger.info(f"[ProxyPool] ✅ {ip}:{port} ({proto}) {elapsed:.2f}s")
                except Exception as e:
                    logger.debug(f"[ProxyPool] 验证异常 {ip}:{port}: {e}")
        return results

    # ── 池管理 ─────────────────────────────────────────────────────────────

    def update(self, new_nodes: list[ProxyNode]):
        with self._lock:
            for node in new_nodes:
                key = f"{node.ip}:{node.port}"
                if key in self._proxies:
                    old = self._proxies[key]
                    old.score = old.score * 0.6 + node.score * 0.4
                    old.fail_count = 0 if node.score > 0.3 else old.fail_count + 1
                    old.last_check = time.time()
                else:
                    self._proxies[key] = node
            # 淘汰
            dead = [k for k, v in self._proxies.items()
                    if v.score < MIN_SCORE or v.fail_count >= 5]
            for k in dead:
                del self._proxies[k]
            # 限制数量
            sorted_proxies = sorted(self._proxies.items(), key=lambda x: x[1].score, reverse=True)
            self._proxies = dict(sorted_proxies[:MAX_PROXIES])
            self._save()
            alive = [v for v in self._proxies.values() if v.score >= MIN_SCORE]
            logger.info(f"[ProxyPool] 更新完成，可用 {len(alive)}/{len(self._proxies)} 个")

    def get(self) -> Optional[ProxyNode]:
        with self._lock:
            alive = sorted([v for v in self._proxies.values() if v.score >= MIN_SCORE],
                          key=lambda x: x.last_used)
            if not alive:
                return None
            node = alive[0]
            node.last_used = time.time()
            return node

    def report(self, node: ProxyNode, ok: bool):
        with self._lock:
            key = f"{node.ip}:{node.port}"
            if key not in self._proxies:
                return
            n = self._proxies[key]
            if ok:
                n.score = min(1.0, n.score + 0.08)
                n.fail_count = 0
            else:
                n.score = max(0, n.score - 0.2)
                n.fail_count += 1
                if n.fail_count >= 5:
                    n.score = 0
            self._save()

    # ── 主流程 ─────────────────────────────────────────────────────────────

    def refresh(self):
        """完整刷新：抓取 → 验证 → 入池"""
        logger.info("[ProxyPool] 开始刷新...")
        raw = self.fetch_all()
        if not raw:
            logger.warning("[ProxyPool] 抓取为空，使用历史代理")
            return
        verified = self.verify_batch(raw[:300])  # 最多验证300个
        if verified:
            self.update(verified)
        else:
            logger.warning("[ProxyPool] 没有任何代理通过验证（免费代理质量差，建议考虑付费代理）")

    def print_status(self):
        alive = [v for v in self._proxies.values() if v.score >= MIN_SCORE]
        logger.info(f"[ProxyPool] 可用:{len(alive)} 总:{len(self._proxies)}")


# ── 全局单例 ──────────────────────────────────────────────────────────────
_pool: Optional[ProxyPool] = None
_lock = threading.Lock()

def get_pool() -> ProxyPool:
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                _pool = ProxyPool()
    return _pool


# ── 入口测试 ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    pool = get_pool()
    pool.refresh()
    pool.print_status()
    print("\n=== 测试前5个代理 ===")
    for i in range(5):
        node = pool.get()
        if not node:
            print("池空")
            break
        ok, elapsed = pool.verify_one(node.ip, node.port, node.protocol)
        pool.report(node, ok)
        print(f"  {node.ip}:{node.port} ({node.protocol}) → {'✅' if ok else '❌'} ({elapsed:.2f}s)")

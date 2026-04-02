"""
DragonFlow 全局配置
═══════════════════════════════════════════════════════════════════════════
1万本金龙空龙专属参数体系
融合：astock_skill配置 + Codex PLAYBOOK + 文少策略阈值
"""
from __future__ import annotations

from pathlib import Path
import pytz

# ─── 基本路径 ─────────────────────────────────────────────────────────────────
SKILL_ROOT = Path(__file__).parent
TZ = pytz.timezone("Asia/Shanghai")

# ─── 调度时间（北京时间） ─────────────────────────────────────────────────────
SCHEDULE_MORNING = "08:50"
SCHEDULE_MIDDAY = "11:35"
SCHEDULE_CLOSING = "15:35"

# ─── 交易标的过滤（仅沪市主板 + 深市主板） ─────────────────────────────────────
ALLOWED_PREFIXES = ("600", "601", "603", "000", "001")

# ─── A 股主要指数 ─────────────────────────────────────────────────────────────
A_SHARE_INDEX = {
    "上证指数": "sh000001",
    "深证成指": "sz399001",
    "创业板指": "sz399006",
}

# ─── 热门题材关键词（动态更新） ───────────────────────────────────────────────
HOT_THEMES = [
    "商业航天", "卫星互联网", "低空经济", "人形机器人", "具身智能",
    "固态电池", "储能", "光伏", "风电",
    "算力", "大模型", "AI芯片", "存储芯片", "HBM",
    "量子计算", "脑机接口",
    "碳酸锂", "锂矿", "新能源车",
    "创新药", "GLP-1",
    "国产替代", "信创", "华为",
]

# ═══════════════════════════════════════════════════════════════════════════
# 4区间市场判断阈值（来自文少 + Codex PLAYBOOK）
# 所有条件必须同时满足（AND）
# ═══════════════════════════════════════════════════════════════════════════

ZONE_THRESHOLDS = {
    "super_attack": {
        "highest_board": 5,        # 最高连板 >= 5
        "limit_up_count": 60,      # 涨停 >= 60 家
        "broken_rate": 0.22,       # 炸板率 <= 22%
        "up_down_ratio": 1.8,      # 涨跌家数比 >= 1.8
        "main_flow_100m": 20.0,    # 主力净流入 >= 20 亿
    },
    "attack": {
        "highest_board": 4,
        "limit_up_count": 35,
        "broken_rate": 0.30,
        "up_down_ratio": 1.2,
        "main_flow_100m": 5.0,
    },
    "range": {
        "highest_board": 2,
        "limit_up_count": 15,
        "broken_rate": 0.42,
        "up_down_ratio": 0.8,
        "main_flow_100m": -15.0,
    },
    # 不满足以上任一 → risk（空仓）
}

# ═══════════════════════════════════════════════════════════════════════════
# 1万本金仓位表（zone × emotion 双重决定）
# ═══════════════════════════════════════════════════════════════════════════

ZONE_POSITION = {
    "super_attack": {
        "total_pct": 1.00,         # 总仓100%
        "max_single_pct": 0.60,    # 单票最多60%
        "max_holdings": 2,         # 最多2只
    },
    "attack": {
        "total_pct": 0.70,
        "max_single_pct": 0.40,
        "max_holdings": 2,
    },
    "range": {
        "total_pct": 0.30,
        "max_single_pct": 0.25,
        "max_holdings": 1,
    },
    "risk": {
        "total_pct": 0.00,
        "max_single_pct": 0.00,
        "max_holdings": 0,
    },
}

# ─── 情绪周期仓位修正系数 ─────────────────────────────────────────────────────
EMOTION_POSITION_MODIFIER = {
    "冰点": 0.80,     # 降仓等分歧
    "发酵": 1.00,     # 正常执行
    "高潮": 0.85,     # 降仓防一致
    "沸腾": 0.60,     # 大幅降仓，只做龙头
}

# ─── 情绪周期阈值（涨停家数） ──────────────────────────────────────────────────
EMOTION_THRESHOLDS = [
    (50,   "冰点"),    # < 50
    (100,  "发酵"),    # 50-100
    (150,  "高潮"),    # 100-150
    (9999, "沸腾"),    # > 150
]

# ═══════════════════════════════════════════════════════════════════════════
# 双龙破晓选股硬过滤条件（来自文少）
# ═══════════════════════════════════════════════════════════════════════════

DOUBLE_DRAGON_FILTER = {
    "theme_rank_max": 3,           # 题材排名前3（必须）
    "volume_ratio_min": 1.8,       # 量比 >= 1.8（必须）
    "breakout_20d": 1,             # 突破20日新高（必须）
    "from_60d_low_pct_min": 5.0,   # 距60日低点 >= 5%（必须）
    "from_60d_low_pct_max": 40.0,  # 距60日低点 <= 40%（必须）
    "close_change_min": 2.0,       # 涨幅 >= 2%（必须）
    "close_change_max": 9.5,       # 涨幅 <= 9.5%（非一字涨停）
    "is_limit_up_one_wall": 0,     # 不能是一字板（必须）
    "min_amount_100m": 8.0,        # 成交额 >= 8亿
}

# ═══════════════════════════════════════════════════════════════════════════
# 1万本金交易规则（来自Codex PLAYBOOK + 文少）
# ═══════════════════════════════════════════════════════════════════════════

TRADE_RULES = {
    # 资金
    "initial_capital": 10_000.0,

    # 止损（硬性，不可被进化调整）
    "stop_loss_pct": -0.018,           # -1.8%（约180元/笔）
    "next_day_fail_pct": -0.01,        # 次日10:00前跌破成本-1%清仓
    "next_day_low_vol_ratio": 1.0,     # 开盘30分钟量比<1.0清仓

    # 止盈（阶梯减仓）
    "take_profit_1_pct": 0.05,         # +5% 减仓50%
    "take_profit_2_pct": 0.08,         # +8% 再减仓50%
    "max_hold_days": 3,                # 第3天收盘无条件清仓

    # 加仓（仅进攻区）
    "add_trigger_pct": 0.02,           # 成本+2%后触发
    "add_ratio": 0.50,                 # 加仓量 = 原仓位50%

    # 买入过滤
    "max_chase_pct": 3.0,              # 已涨>3%不追
    "min_buy_volume_ratio": 1.8,       # 量比门槛

    # 手续费
    "buy_fee_rate": 0.00025,           # 万2.5
    "sell_fee_rate": 0.00125,          # 万2.5 + 千1印花税
}

# ═══════════════════════════════════════════════════════════════════════════
# 风控铁律（不可被进化引擎调整）
# ═══════════════════════════════════════════════════════════════════════════

RISK_CONTROL = {
    # 禁止场景
    "forbidden": [
        "risk区开仓",
        "高位连续加速后追板",
        "亏损单补仓摊低成本",
        "第4天仍死扛",
    ],

    # 惩罚规则
    "penalty_stop_loss_miss": 1,       # 单笔超1.8%止损未执行 → 次日禁开新仓
    "penalty_wrong_add_count": 2,      # 一周内N次逆势加仓 → 下周仓位减半
    "month_max_drawdown_pct": 8.0,     # 月回撤>8% → 当月停止实盘仅模拟
    "total_max_drawdown_pct": 15.0,    # 总回撤>15% → 系统强制停手

    # 回测达标线
    "backtest_min_win_rate": 0.55,
    "backtest_min_profit_ratio": 2.5,
    "backtest_max_month_dd": 8.0,
    "backtest_max_total_dd": 15.0,
    "backtest_min_samples": 100,
}

# ═══════════════════════════════════════════════════════════════════════════
# 进化引擎参数范围限制（防过拟合）
# ═══════════════════════════════════════════════════════════════════════════

EVOLUTION_BOUNDS = {
    "stop_loss_pct":         (-0.025, -0.012),    # 止损范围
    "take_profit_1_pct":     (0.04, 0.08),         # 止盈1范围
    "take_profit_2_pct":     (0.06, 0.12),         # 止盈2范围
    "min_buy_volume_ratio":  (1.2, 2.5),           # 量比门槛范围
    "max_chase_pct":         (2.0, 5.0),           # 追高上限范围
}

EVOLUTION_THRESHOLDS = {
    "win_rate_excellent": 0.60,
    "win_rate_poor": 0.52,
    "profit_ratio_excellent": 2.2,
    "profit_ratio_poor": 1.8,
    "max_dd_warn": 8.0,
    "max_dd_danger": 15.0,
    "lookback_days": 10,
    "max_param_changes_per_week": 1,    # 每周最多调1个参数
}

# ─── 新闻重要性关键词 ──────────────────────────────────────────────────────────
NEWS_KEYWORDS_HIGH = [
    "美联储", "降息", "加息", "政策", "央行", "监管", "制裁",
    "暴跌", "熔断", "贸易战", "重大利好",
]
NEWS_KEYWORDS_MID = [
    "业绩", "并购", "重组", "增持", "回购", "分红", "解禁",
]

# ═══════════════════════════════════════════════════════════════════════════
# 弹性数据源链（自动故障转移）
# ═══════════════════════════════════════════════════════════════════════════

DATA_SOURCE_CONFIG = {
    # 数据源优先级（按延迟排序）
    "index_priority": ["sina", "tencent", "eastmoney", "netease"],
    "stock_priority": ["sina", "eastmoney", "tencent"],

    # 反限流参数
    "base_interval": 0.3,          # 正常请求间隔（秒）
    "backoff_factor": 2.0,         # 指数退避因子
    "max_backoff": 30.0,           # 最大退避时间（秒）
    "health_score_init": 10,       # 数据源初始健康分
    "health_penalty": -5,          # 失败惩罚
    "health_reward": 1,            # 成功奖励
    "health_min_to_use": 0,        # 健康分低于此值跳过
    "cooldown_seconds": 60,        # 降级冷却时间

    # UA 池（真实浏览器指纹）
    "user_agents": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 OPR/108.0.0.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    ],
}

# ═══════════════════════════════════════════════════════════════════════════
# 舆情引擎配置
# ═══════════════════════════════════════════════════════════════════════════

SENTIMENT_CONFIG = {
    # 采集频率
    "trading_interval_min": 5,        # 交易时段每5分钟
    "idle_interval_min": 30,          # 非交易时段每30分钟

    # 看多关键词（权重）
    "bullish_keywords": {
        "涨停": 3, "起飞": 3, "牛": 2, "加仓": 2, "满仓": 3,
        "利好": 2, "暴涨": 3, "翻倍": 3, "入场": 2, "抄底": 2,
        "龙头": 2, "主升浪": 3, "突破": 2, "放量": 1, "强势": 1,
    },
    # 看空关键词（权重）
    "bearish_keywords": {
        "跑路": 3, "割肉": 3, "崩盘": 3, "套牢": 2, "暴跌": 3,
        "利空": 2, "减仓": 2, "清仓": 3, "风险": 1, "泡沫": 2,
        "见顶": 3, "出货": 3, "跌停": 3, "破位": 2, "缩量": 1,
    },

    # RSI阈值 → 仓位修正
    "rsi_thresholds": {
        "极度乐观": {"min": 0.85, "max": 1.0,  "modifier": 0.5},
        "偏乐观":   {"min": 0.70, "max": 0.85, "modifier": 0.8},
        "中性":     {"min": 0.40, "max": 0.70, "modifier": 1.0},
        "偏悲观":   {"min": 0.25, "max": 0.40, "modifier": 1.0},
        "极度恐慌": {"min": 0.0,  "max": 0.25, "modifier": 0.7},
    },

    # 个股舆情对评分的影响
    "stock_sentiment_score_adj": {
        "overhyped": -15,     # 散户过度看多 → 降分
        "emerging": 8,        # 刚开始讨论（发酵期） → 加分
        "feared": 5,          # 被恐慌抛售但基本面好 → 加分
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# 系统自愈配置
# ═══════════════════════════════════════════════════════════════════════════

SELFHEAL_CONFIG = {
    "health_check_interval": 60,      # 健康检查间隔（秒）
    "full_check_interval": 21600,     # 全面检查间隔（6小时）

    "data_freshness_warn": 30,        # 数据新鲜度警告阈值（秒）
    "data_freshness_critical": 120,   # 严重阈值
    "error_rate_warn": 0.3,           # 错误率警告
    "error_rate_critical": 0.7,       # 错误率严重
    "tick_latency_warn": 5000,        # tick延迟警告（ms）
    "tick_latency_critical": 10000,
    "db_size_warn_mb": 100,           # 数据库大小警告
    "memory_warn_mb": 500,

    # 自动修复开关
    "auto_repair": True,
    "max_repairs_per_hour": 5,        # 每小时最多修复次数（防循环）
}

# ═══════════════════════════════════════════════════════════════════════════
# 智能学习配置
# ═══════════════════════════════════════════════════════════════════════════

INTELLIGENCE_CONFIG = {
    # 爬虫频率
    "sentiment_crawl_interval": 300,   # 舆情5分钟
    "news_crawl_interval": 1800,       # 快讯30分钟
    "deep_crawl_interval": 7200,       # 深度策略2小时
    "nightly_learn_hour": 2,           # 凌晨2点深度学习

    # 爬取目标
    "crawl_sources": [
        {"name": "东方财富研报", "type": "research", "enabled": True},
        {"name": "雪球热帖", "type": "opinion", "enabled": True},
        {"name": "知乎量化", "type": "strategy", "enabled": True},
    ],

    # 知识库限制
    "max_knowledge_entries": 10000,
    "knowledge_expire_days": 90,       # 90天过期

    # 参数提取正则
    "param_patterns": {
        "stop_loss": r"止损[：:]\s*[-]?(\d+\.?\d*)\s*[%％]",
        "take_profit": r"止盈[：:]\s*(\d+\.?\d*)\s*[%％]",
        "win_rate": r"胜率[：:]\s*(\d+\.?\d*)\s*[%％]",
        "profit_ratio": r"盈亏比[：:]\s*(\d+\.?\d*)",
        "max_drawdown": r"最大回撤[：:]\s*(\d+\.?\d*)\s*[%％]",
    },
}

"""
本地知识库管理
═══════════════════════════════════════════════════════════════════════════
存储/检索/去重/过期清理
"""
from __future__ import annotations

import datetime
from typing import Dict, List, Optional

from loguru import logger

from config import INTELLIGENCE_CONFIG
from models import get_session, KnowledgeEntry


def save_entries(items: List[Dict]) -> int:
    """批量保存知识条目（自动去重）"""
    session = get_session()
    saved = 0
    try:
        for item in items:
            ch = item.get("content_hash", "")
            if not ch:
                continue
            # 去重
            exists = session.query(KnowledgeEntry).filter_by(content_hash=ch).first()
            if exists:
                continue

            entry = KnowledgeEntry(
                source=item.get("source", ""),
                source_url=item.get("url", ""),
                entry_type=item.get("entry_type", _infer_type(item)),
                title=item.get("title", "")[:256],
                content_hash=ch,
                summary=item.get("summary", "")[:2000],
                tags=item.get("tags", []),
                extracted_params=item.get("extracted_params", {}),
                market_view=item.get("market_view", "neutral"),
                sector_focus=item.get("sector_focus", []),
                quality_score=_score_quality(item),
                relevance_score=_score_relevance(item),
            )
            session.add(entry)
            saved += 1

        session.commit()
        if saved:
            logger.info(f"[知识库] 保存{saved}条新条目")
    except Exception as e:
        session.rollback()
        logger.error(f"[知识库] 保存失败: {e}")
    finally:
        session.close()
    return saved


def _infer_type(item: Dict) -> str:
    source = item.get("source", "")
    if "research" in source:
        return "research"
    if "flash" in source:
        return "news"
    if item.get("extracted_params"):
        return "strategy"
    return "opinion"


def _score_quality(item: Dict) -> float:
    """评估内容质量 0-100"""
    score = 30.0
    summary = item.get("summary", "")
    if len(summary) > 100:
        score += 15
    if len(summary) > 300:
        score += 10
    if item.get("extracted_params"):
        score += 20  # 有量化参数
    if item.get("tags") and len(item["tags"]) >= 2:
        score += 10
    followers = item.get("author_followers", 0)
    if followers > 100000:
        score += 15
    elif followers > 10000:
        score += 8
    return min(score, 100)


def _score_relevance(item: Dict) -> float:
    """与龙空龙策略的相关度 0-100"""
    score = 20.0
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    relevant_keywords = [
        "龙头", "涨停", "连板", "短线", "打板", "情绪", "龙空龙",
        "换手", "量比", "主升浪", "止损", "止盈", "仓位",
    ]
    for kw in relevant_keywords:
        if kw in text:
            score += 8
    params = item.get("extracted_params", {})
    if "stop_loss" in params or "win_rate" in params:
        score += 15
    return min(score, 100)


def get_recent_knowledge(entry_type: Optional[str] = None,
                          min_quality: float = 40,
                          limit: int = 50) -> List[Dict]:
    """检索知识库"""
    session = get_session()
    try:
        q = session.query(KnowledgeEntry).filter(
            KnowledgeEntry.quality_score >= min_quality)
        if entry_type:
            q = q.filter_by(entry_type=entry_type)
        entries = q.order_by(KnowledgeEntry.created_at.desc()).limit(limit).all()
        return [{
            "id": e.id, "source": e.source, "title": e.title,
            "summary": e.summary[:200] if e.summary else "",
            "tags": e.tags, "params": e.extracted_params,
            "market_view": e.market_view, "quality": e.quality_score,
            "relevance": e.relevance_score, "applied": e.applied,
            "date": str(e.created_at.date()) if e.created_at else "",
        } for e in entries]
    finally:
        session.close()


def get_param_references() -> Dict[str, List[float]]:
    """从知识库提取参数参考值（如"止损"平均多少%）"""
    session = get_session()
    try:
        entries = session.query(KnowledgeEntry).filter(
            KnowledgeEntry.extracted_params.isnot(None),
            KnowledgeEntry.quality_score >= 50,
        ).order_by(KnowledgeEntry.created_at.desc()).limit(200).all()

        param_values: Dict[str, List[float]] = {}
        for e in entries:
            params = e.extracted_params or {}
            for k, v in params.items():
                if isinstance(v, (int, float)):
                    param_values.setdefault(k, []).append(float(v))
        return param_values
    finally:
        session.close()


def cleanup_expired():
    """清理过期知识"""
    session = get_session()
    try:
        expire = INTELLIGENCE_CONFIG["knowledge_expire_days"]
        cutoff = datetime.datetime.now() - datetime.timedelta(days=expire)
        deleted = session.query(KnowledgeEntry).filter(
            KnowledgeEntry.created_at < cutoff).delete()
        # 超量清理
        total = session.query(KnowledgeEntry).count()
        max_entries = INTELLIGENCE_CONFIG["max_knowledge_entries"]
        if total > max_entries:
            # 删除质量最低的
            to_delete = total - max_entries
            low_quality = session.query(KnowledgeEntry).order_by(
                KnowledgeEntry.quality_score.asc()).limit(to_delete).all()
            for e in low_quality:
                session.delete(e)
        session.commit()
        if deleted:
            logger.info(f"[知识库] 清理{deleted}条过期")
    except Exception as e:
        session.rollback()
        logger.error(f"[知识库] 清理失败: {e}")
    finally:
        session.close()

"""
情绪周期判断（文少核心创新）
═══════════════════════════════════════════════════════════════════════════
根据涨停家数判断当前情绪阶段，并输出仓位修正系数。

情绪周期：
  冰点（<50涨停）  → 降仓等分歧
  发酵（50-100）   → 正常执行
  高潮（100-150）  → 降仓防一致
  沸腾（>150）     → 大幅降仓，只做龙头
"""
from __future__ import annotations

from typing import Tuple

from config import EMOTION_THRESHOLDS, EMOTION_POSITION_MODIFIER


def get_emotion_cycle(limit_up_count: int) -> str:
    """根据涨停家数判断情绪周期"""
    for threshold, cycle in EMOTION_THRESHOLDS:
        if limit_up_count < threshold:
            return cycle
    return "沸腾"


def get_emotion_modifier(emotion: str) -> float:
    """获取情绪仓位修正系数"""
    return EMOTION_POSITION_MODIFIER.get(emotion, 1.0)


def get_emotion_advice(emotion: str) -> str:
    """情绪操作建议"""
    advices = {
        "冰点": "市场冰点，降仓等分歧信号，只看不做或轻仓试探",
        "发酵": "市场发酵，正常执行策略，重点关注主线板块龙头",
        "高潮": "市场高潮，注意防范一致性预期后的分歧，适度降仓",
        "沸腾": "市场沸腾，极度谨慎！大幅降仓，只做最强龙头，拒绝杂毛",
    }
    return advices.get(emotion, "")

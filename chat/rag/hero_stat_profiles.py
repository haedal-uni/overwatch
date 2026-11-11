"""영웅별 스탯 기대치(hero_stat_profiles.json): 기본 무기 초당 피해·치유와 경감 핵심 기술.

정리 기준과 실측 근거는 chat_모듈_구조.md "rag/hero_stat_profiles.py" 절.
"""

import json
import logging
import os
from typing import Any, Dict, Optional

from chat.domain.heroes import normalize_hero_name

logger = logging.getLogger(__name__)

PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hero_stat_profiles.json")

_cache: Optional[Dict[str, Dict[str, Any]]] = None


def get_hero_stat_profiles() -> Dict[str, Dict[str, Any]]:
    """{영웅: 프로필}. 파일을 못 읽으면 빈 dict."""
    global _cache
    if _cache is None:
        try:
            with open(PROFILE_PATH, encoding="utf-8") as f:
                _cache = json.load(f).get("heroes") or {}
        except (OSError, ValueError) as exc:
            logger.warning("[STAT PROFILES] %s 읽기 실패: %s", PROFILE_PATH, exc)
            _cache = {}
    return _cache


def hero_stat_profile(hero: Optional[str]) -> Dict[str, Any]:
    return get_hero_stat_profiles().get(normalize_hero_name(hero) or "", {}) if hero else {}

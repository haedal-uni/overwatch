"""우선 타겟 순서에 쓰는 영웅별 "처리 우선" 원칙.

문서의 "처리 우선" 줄은 문장이라 순서에 바로 쓸 수 없어서, `build_target_priorities` 명령이
영웅 이름 목록(최우선/우선/피함)으로 정리해 JSON에 둔다. 저장 형식은 chat_모듈_구조.md 참고.
"""

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from chat.domain.heroes import HERO_TO_ROLE, normalize_hero_name
from chat.rag.doc_sections import _hero_section_bodies

logger = logging.getLogger(__name__)

PRIORITY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "target_priorities.json")

_RULE_LINE_RE = re.compile(r"^\s*-\s*처리 우선\s*:\s*(.+)$")
ROLE_KEYS = ("tank", "damage", "support")

# 순위 묶음. 숫자가 작을수록 먼저 노린다.
TIER_FIRST, TIER_PREFER, TIER_PREFER_ROLE, TIER_NEUTRAL, TIER_AVOID_ROLE, TIER_AVOID = range(6)
TIER_LABELS = {
    TIER_FIRST: "처리 우선: 최우선",
    TIER_PREFER: "처리 우선: 우선",
    TIER_PREFER_ROLE: "처리 우선: 우선(역할)",
    TIER_AVOID_ROLE: "처리 우선: 피함(역할)",
    TIER_AVOID: "처리 우선: 피함",
}

_cache: Optional[Dict[str, Dict[str, Any]]] = None


def target_rule_line(hero: Optional[str]) -> Optional[str]:
    """영웅 절의 "처리 우선" 줄 원문(앞의 "처리 우선:"은 뗀다)."""
    for line in _hero_section_bodies().get(normalize_hero_name(hero) or "", []):
        match = _RULE_LINE_RE.match(line)
        if match:
            return match.group(1).strip()
    return None


def source_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


# 최우선·피함은 근거 구절에 그 뜻의 말이 있어야 한다(없으면 최우선은 우선으로 내리고, 피함은 버린다).
_FIRST_CUE_RE = re.compile(r"최우선|1순위|첫 번째 목표|가장 먼저|최대한 일찍|가장 상대하기 쉽")
_AVOID_CUE_RE = re.compile(r"지나치|지나쳐|노리지 않|무시|무리하게|쫓기보다|쫓지|공격하지 않|상대하지 않|피한다|피해 ")
# 한 역할 영웅을 이만큼 늘어놓았으면 영웅 목록이 아니라 역할로 본다.
_ROLE_COLLAPSE_MIN = 5
_ROLE_COLLAPSE_SHARE = 0.6


def _collapse_roles(names: List[str], roles: List[str]) -> None:
    for role in ROLE_KEYS:
        in_role = [h for h in names if HERO_TO_ROLE.get(h) == role]
        total = sum(1 for r in HERO_TO_ROLE.values() if r == role)
        if len(in_role) >= _ROLE_COLLAPSE_MIN and len(in_role) >= total * _ROLE_COLLAPSE_SHARE:
            names[:] = [h for h in names if h not in in_role]
            if role not in roles:
                roles.append(role)


def clean_priority_entry(raw: Any, source: str) -> Dict[str, List[str]]:
    """LLM 출력에서 원문에 근거 구절이 있고 등록된 영웅·역할인 항목만 남긴다."""
    raw = raw if isinstance(raw, dict) else {}
    cleaned: Dict[str, List[str]] = {"first": [], "prefer": [], "avoid": []}
    for key in ("first", "prefer", "avoid"):
        for item in raw.get(key) or []:
            if not isinstance(item, dict):
                continue
            hero = normalize_hero_name(str(item.get("hero") or "").strip())
            quote = str(item.get("quote") or "").strip()
            if hero not in HERO_TO_ROLE or not quote or quote not in source:
                continue
            target = key
            if key == "first" and not _FIRST_CUE_RE.search(quote):
                target = "prefer"
            if key == "avoid" and not _AVOID_CUE_RE.search(quote):
                continue
            if hero not in cleaned[target]:
                cleaned[target].append(hero)
    for key in ("prefer_roles", "avoid_roles"):
        roles = []
        for item in raw.get(key) or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            quote = str(item.get("quote") or "").strip()
            if role in ROLE_KEYS and quote and quote in source and role not in roles:
                roles.append(role)
        cleaned[key] = roles
    _collapse_roles(cleaned["prefer"], cleaned["prefer_roles"])
    _collapse_roles(cleaned["avoid"], cleaned["avoid_roles"])
    # 같은 영웅이 여러 칸에 있으면 앞 칸(최우선 > 우선)만 남기고, 우선과 피함이 겹치면 둘 다 버린다.
    cleaned["prefer"] = [h for h in cleaned["prefer"] if h not in cleaned["first"]]
    conflict = set(cleaned["first"] + cleaned["prefer"]) & set(cleaned["avoid"])
    for key in ("first", "prefer", "avoid"):
        cleaned[key] = [h for h in cleaned[key] if h not in conflict]
    return cleaned


def _load() -> Dict[str, Dict[str, Any]]:
    global _cache
    if _cache is None:
        try:
            with open(PRIORITY_PATH, encoding="utf-8") as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            logger.warning("[TARGET PRIORITY] 처리 우선 정리 파일이 없어 상성 판정으로만 정렬한다: %s", PRIORITY_PATH)
            _cache = {}
    return _cache


def priority_tier(hero: Optional[str], opponent: Optional[str]) -> int:
    """내 영웅의 처리 우선 원칙에서 그 상대가 속한 묶음(TIER_*)."""
    entry = _load().get(normalize_hero_name(hero) or "") or {}
    opponent = normalize_hero_name(opponent)
    if opponent in entry.get("first", []):
        return TIER_FIRST
    if opponent in entry.get("avoid", []):
        return TIER_AVOID
    if opponent in entry.get("prefer", []):
        return TIER_PREFER
    role = HERO_TO_ROLE.get(opponent)
    if role in entry.get("avoid_roles", []):
        return TIER_AVOID_ROLE
    if role in entry.get("prefer_roles", []):
        return TIER_PREFER_ROLE
    return TIER_NEUTRAL

"""영웅 가이드(포지션·특전·조합) 화면 데이터.

문장은 `build_hero_guides` 명령이 원본 문서를 짧게 정리해 둔 JSON에서, 스킬 단축키는
문서에서 바로 읽는다. 저장 형식과 검사 규칙은 chat_모듈_구조.md 참고.
"""

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from chat.domain.answer_format import replace_dying_word, unify_role_labels
from chat.domain.heroes import HERO_TO_ROLE, has_final_consonant, josa_euro, normalize_hero_name, resolve_hero_name
from chat.domain.intent_rules import is_tank_pair, two_tanks_allowed
from chat.rag.doc_sections import _extract_block, _hero_section_bodies, get_hero_perk_section, get_hero_perks, get_skill_list
from chat.rag.matchup_tables import _question_for

logger = logging.getLogger(__name__)

GUIDE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hero_guides.json")

# 정리할 때 LLM에 넘기는 영웅 절 항목.
SOURCE_FIELDS = ["컨셉/부제", "기본 위치", "취약한 상대 판정/상황", "조합 내 담당"]

SKILL_KEY_ORDER = ["좌클릭", "우클릭", "shift", "e", "q"]
SKILL_KEY_LABELS = {"좌클릭": "좌클릭", "우클릭": "우클릭", "shift": "Shift", "e": "E", "q": "Q 궁"}

MAX_POSITION_STEPS = 3
MAX_PRIORITIES = 4
MAX_ALLY_GROUPS = 3
MAX_AVOID = 3

_guide_cache: Optional[Dict[str, Dict[str, Any]]] = None


def guide_source_text(hero: str) -> str:
    """정리에 쓰는 원문(영웅 절 항목 + 특전 데이터). 바뀌었는지 비교하는 기준이기도 하다."""
    body = _hero_section_bodies().get(hero) or []
    blocks = [block for block in (_extract_block(body, field) for field in SOURCE_FIELDS) if block]
    perk = get_hero_perk_section(hero)
    if perk:
        blocks.append(perk)
    return "\n".join(blocks)


def source_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _load_guides() -> Dict[str, Dict[str, Any]]:
    global _guide_cache
    if _guide_cache is None:
        try:
            with open(GUIDE_PATH, encoding="utf-8") as f:
                _guide_cache = json.load(f)
        except (OSError, ValueError):
            logger.warning("[HERO GUIDE] 가이드 파일이 없어 스킬만 보여준다: %s", GUIDE_PATH)
            _guide_cache = {}
    return _guide_cache


# unify_role_labels가 다루지 않는 문서 표기.
_GUIDE_TERM_FIXES = [
    ("비돌격군", "탱커가 아닌 영웅"), ("돌격군", "탱커"), ("지원군", "힐러"), ("공격군", "딜러"),
    ("돌격 탱커", "탱커"), ("돌격 또는", "탱커 또는"),
]
_NO_FINAL_JOSA = {"과": "와", "을": "를", "은": "는", "이": "가", "으로": "로"}


def _text(value: Any) -> str:
    """화면 문구로 다듬는다(문서의 돌격/공격/지원 표기를 탱커/딜러/힐러로, 끝 마침표 제거)."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    for old, new in _GUIDE_TERM_FIXES:
        if has_final_consonant(old) and not has_final_consonant(new):
            # 받침이 없어지면 바로 뒤 조사도 바꾼다("돌격군과" → "탱커와").
            text = re.sub(
                re.escape(old) + r"(과|을|은|이|으로)?(?![가-힣])|" + re.escape(old),
                lambda m: new + _NO_FINAL_JOSA.get(m.group(1) or "", m.group(1) or ""), text,
            )
        else:
            text = text.replace(old, new)
    return replace_dying_word(unify_role_labels(text)).rstrip(". ")


_ROLE_SUFFIX_RE = re.compile(r"\s*(?:이자|겸)\s*(?:메인|서브)?\s*(탱커|딜러|힐러)$")


def _concept(value: Any, hero: str) -> str:
    """"힐러이자 서브 딜러"처럼 자기 역할이 아닌 역할을 덧붙인 끝말을 뗀다."""
    text = _text(value)
    match = _ROLE_SUFFIX_RE.search(text)
    if match and _LABEL_ROLE_WORDS[match.group(1)] != HERO_TO_ROLE.get(hero):
        text = text[:match.start()]
    return text


def _short_list(values: Any, limit: int) -> List[str]:
    if not isinstance(values, list):
        return []
    return [text for text in (_text(v) for v in values) if text][:limit]


def _clean_perks(items: Any, doc_perks: List[Dict[str, Optional[str]]]) -> List[Dict[str, Any]]:
    """특전 목록은 문서에서 정하고, 정리본 요약은 이름이 같은 특전에만 붙인다(정리본이 특전을 빼먹어도 화면에는 다 나온다)."""
    summaries = {
        str(item.get("name") or "").strip(): item
        for item in (items if isinstance(items, list) else []) if isinstance(item, dict)
    }
    keys = [perk["key"] for perk in doc_perks]
    # 한 칸의 두 특전이 같은 키로 적혀 있으면 문서 오류라 키를 보이지 않는다.
    keys_ok = len(set(keys)) == len(keys)
    perks = []
    for perk in doc_perks:
        summary = summaries.get(perk["name"]) or {}
        perks.append({
            "name": perk["name"],
            "effect": _text(summary.get("effect")) or _text(perk["effect"]),
            "when": _text(summary.get("when")),
            "popular": bool(summary.get("popular")),
            "key": perk["key"] if keys_ok else None,
        })
    return perks


_LABEL_ROLE_WORDS = {"탱커": "tank", "딜러": "damage", "힐러": "support"}
# 묶음 이름이 역할로 끝날 때만 역할로 거른다("방벽 탱커"는 거르고 "탱커를 막아 줄 아군"은 거르지 않는다).
_LABEL_ENDS_WITH_ROLE_RE = re.compile(r"(탱커|딜러|힐러)(\s*영웅)?$")


def _clean_allies(groups: Any, hero: str, tank_pair_ok: bool = True) -> List[Dict[str, Any]]:
    """등록된 영웅 이름만, 묶음 이름에 역할이 있으면 그 역할 영웅만 남긴다. 탱커가 하나뿐인 규격이면 탱커에게 탱커 아군을 빼고."""
    cleaned = []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        label = _text(group.get("label"))
        label_roles = (
            {role for word, role in _LABEL_ROLE_WORDS.items() if word in label}
            if _LABEL_ENDS_WITH_ROLE_RE.search(label) else set()
        )
        names = []
        for raw in group.get("heroes") or []:
            name = resolve_hero_name(str(raw))
            if not name or name == hero or name in names:
                continue
            if label_roles and HERO_TO_ROLE.get(name) not in label_roles:
                continue
            if not tank_pair_ok and is_tank_pair(hero, name):
                continue
            names.append(name)
        if label and names:
            cleaned.append({"label": label, "heroes": names, "questions": [ally_question(hero, n) for n in names]})
    return cleaned[:MAX_ALLY_GROUPS]


def ally_question(hero: str, ally: str) -> str:
    """조합 탭 아군 얼굴을 눌렀을 때 보낼 질문."""
    with_josa = "과" if has_final_consonant(ally) else "와"
    return f"{hero}{josa_euro(hero)} 플레이할 때 우리팀 {ally}{with_josa} 어떻게 연계해야 해?"


def priority_question(hero: str, item: str) -> str:
    """"먼저 처리할 것" 칩을 눌렀을 때 보낼 질문. 영웅 이름이면 상성표 공략법과 같은 문장이다."""
    foe = resolve_hero_name(item)
    if foe and foe != hero:
        return _question_for(hero, foe, "hard")
    topic = "은" if has_final_consonant(item) else "는"
    return f"{hero}{josa_euro(hero)} 플레이할 때 {item}{topic} 어떻게 해야 해?"


def skill_rows(hero: str) -> List[Dict[str, str]]:
    """키가 둘인 스킬(생체 소총 좌클릭·우클릭)은 한 줄에 "좌클릭 · 우클릭"으로 보여 준다."""
    rows = []
    for name, keys in get_skill_list(hero):
        keys = [key for key in keys if key in SKILL_KEY_LABELS]
        if keys:
            rows.append({"key": keys[0], "label": " · ".join(SKILL_KEY_LABELS[key] for key in keys), "name": name})
    return rows


def get_hero_guide(hero: Optional[str]) -> Optional[Dict[str, Any]]:
    """영웅 가이드. 등록되지 않은 영웅이면 None, 정리본이 없으면 스킬만 채운다."""
    name = normalize_hero_name(hero)
    if not name or name not in HERO_TO_ROLE:
        return None

    entry = _load_guides().get(name) or {}
    doc_perks = get_hero_perks(name)
    perks = entry.get("perks") if isinstance(entry.get("perks"), dict) else {}
    priorities = _short_list(entry.get("priorities"), MAX_PRIORITIES)
    return {
        "hero": name,
        "concept": _concept(entry.get("concept"), name),
        "position": _short_list(entry.get("position"), MAX_POSITION_STEPS),
        "position_key": _text(entry.get("position_key")),
        "skills": skill_rows(name),
        "priorities": priorities,
        "priority_questions": [priority_question(name, item) for item in priorities],
        "perks": {
            "minor": _clean_perks(perks.get("minor"), doc_perks["minor"]),
            "major": _clean_perks(perks.get("major"), doc_perks["major"]),
        },
        "allies": _clean_allies(entry.get("allies"), name, two_tanks_allowed()),
        "avoid": _short_list(entry.get("avoid"), MAX_AVOID),
        "weak": _text(entry.get("weak")),
    }

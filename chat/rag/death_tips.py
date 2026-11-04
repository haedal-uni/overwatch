"""'죽었어요' 버튼의 답: 상성표 설명 한 줄을 게임 중에 읽을 두 줄(할 일 / 이유)로 줄인다.

검색·그래프를 타지 않고 LLM 한 번만 부른다. 같은 영웅 짝은 파일에 모아 두고,
상성표 설명이 바뀌면(원문이 다르면) 다시 만든다.
"""

import json
import logging
import os
import re
import threading
from typing import Any, Dict, Optional

from chat.domain.answer_format import replace_dying_word, unify_role_labels
from chat.domain.heroes import resolve_hero_name
from chat.rag import components as chatbot_service
from chat.rag.llm_utils import call_llm_text, safe_json_loads
from chat.rag.matchup_tables import get_hero_matchup

logger = logging.getLogger(__name__)

TIP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "death_tips.json")
_lock = threading.Lock()
_tips: Optional[Dict[str, Dict[str, str]]] = None

# 상성표 묶음 → 배지(보고 있는 영웅 입장).
BADGES = {"hard": "불리", "easy": "유리", "mixed": "상황에 따라 갈림", "even": "비슷"}

PROMPT = """오버워치 상성 설명 하나를 게임 중 10초 안에 읽을 두 줄로 줄여라.
상황: 사용자는 {hero}(으)로 플레이하다가 상대 {killer}에게 처치당했다.
설명(이 내용만 쓰고 없는 사실은 더하지 마라):
{note}

JSON으로만 답해라: {{"do": "...", "why": "..."}}
- do: 다음 교전에서 {hero}(이)가 할 행동 하나. 해요체, 40자 이내. 설명에 대응법이 없으면 설명 속 위험을 피하는 행동으로 쓴다.
- why: {killer}(이)가 {hero}에게 위험한 이유 하나. 해요체, 40자 이내.
- 궁·딜·힐 같은 줄임말은 쓰지 말고, 스킬 이름은 설명에 나온 그대로 쓴다."""


def _load() -> Dict[str, Dict[str, str]]:
    global _tips
    if _tips is None:
        try:
            with open(TIP_PATH, encoding="utf-8") as f:
                _tips = json.load(f)
        except FileNotFoundError:
            _tips = {}
        except Exception:
            logger.exception("[DEATH] 저장해 둔 답을 읽지 못했다: %s", TIP_PATH)
            _tips = {}
    return _tips


def _save() -> None:
    try:
        with open(TIP_PATH, "w", encoding="utf-8") as f:
            json.dump(_tips, f, ensure_ascii=False, indent=1, sort_keys=True)
    except Exception:
        logger.exception("[DEATH] 답을 저장하지 못했다: %s", TIP_PATH)


def find_pair(hero: str, killer: str) -> Optional[Dict[str, Any]]:
    """내 영웅 상성표에서 그 상대 줄을 찾는다(역방향 판정은 상성표가 이미 합쳐 둔다)."""
    chart = get_hero_matchup(hero)
    if not chart:
        return None
    for bucket in ("hard", "easy", "mixed", "even"):
        for entry in chart.get(bucket) or []:
            if entry.get("hero") == killer:
                return {"bucket": bucket, "score": entry.get("score"), "note": entry.get("note") or ""}
    return None


def _fallback(note: str) -> Dict[str, str]:
    """LLM이 안 될 때: 설명의 마지막 문장을 할 일, 첫 문장을 이유로 쓴다."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", note) if s.strip()]
    if not sentences:
        return {"do": "", "why": ""}
    return {"do": sentences[-1], "why": sentences[0] if len(sentences) > 1 else ""}


def _clean(text: Any) -> str:
    return replace_dying_word(unify_role_labels(str(text or "").strip()))


def get_death_tip(hero_raw: str, killer_raw: str) -> Optional[Dict[str, Any]]:
    """내 영웅·나를 처치한 상대로 답을 만든다. 상성표에 그 짝이 없으면 None."""
    hero = resolve_hero_name(hero_raw)
    killer = resolve_hero_name(killer_raw)
    if not hero or not killer or hero == killer:
        return None
    pair = find_pair(hero, killer)
    if not pair or not pair["note"]:
        return None

    key = f"{hero}|{killer}"
    with _lock:
        cached = _load().get(key)
    if cached and cached.get("source") == pair["note"]:
        tip = {"do": cached["do"], "why": cached["why"]}
    else:
        tip = None
        try:
            _bot, _retriever, llm = chatbot_service.get_chatbot_components()
            raw = call_llm_text(llm, PROMPT.format(hero=hero, killer=killer, note=pair["note"]))
            parsed = safe_json_loads(raw, {})
            if isinstance(parsed, dict) and parsed.get("do"):
                tip = {"do": _clean(parsed.get("do")), "why": _clean(parsed.get("why"))}
        except Exception:
            logger.exception("[DEATH] LLM 호출 실패 — 설명 문장으로 대신한다")
        if tip:
            with _lock:
                _load()[key] = {"source": pair["note"], **tip}
                _save()
        else:
            tip = _fallback(pair["note"])

    return {
        "hero": hero,
        "killer": killer,
        "bucket": pair["bucket"],
        "badge": BADGES[pair["bucket"]],
        "score": pair["score"],
        "do": tip["do"],
        "why": tip["why"],
    }

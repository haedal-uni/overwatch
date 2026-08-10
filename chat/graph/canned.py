"""웰컴 화면 예시 버튼 5개에 대한 캐시(canned) 응답.

이 5개 질문(파라 카운터 / 조합 추천 / 맵 운영 / 스탯 피드백 / 영웅 유지)은
LangGraph 파이프라인을 타지 않고 미리 저장해둔 결과를 즉시 돌려준다 —
데모에서 가장 많이 눌리는 버튼이라 응답 속도를 확보하기 위함이다.

중요: 새 캐시를 추가/수정할 때는 반드시 실제 파이프라인을 한 번 돌려 나온
결과를 그대로 넣어야 한다. 임의로 지어내면 RAG 문서에 근거가 없는 답이
나가고, 같은 질문을 조금 다르게 물었을 때의 실제 답변과 어긋난다.
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

from chat.domain.answer_format import sanitize_answer_for_user
from chat.domain.heroes import (
    HERO_TO_ROLE,
    find_all_heroes,
    find_first_hero,
    find_map,
    find_side,
)
from chat.domain.intent_rules import (
    detect_stat_input,
    extract_ally_team,
    extract_enemy_team,
    find_enemy_mentioned_hero,
    hero_mentioned_as_current_hero,
    is_perk_question,
)
from chat.graph.nodes_context import clarify_role_filter_node

logger = logging.getLogger(__name__)


CANNED_COUNTER_HERO = "겐지"
CANNED_COUNTER_TRIGGER_WORDS = [
    "카운터", "잡는 영웅", "상대하기 좋은", "상대하기 어려운", "상성",
]

CANNED_COMPOSITION_ENEMY_LIST = ["오리사", "리퍼", "파라", "루시우", "아나"]
CANNED_COMPOSITION_ALLY_LIST = ["라인하르트", "리퍼", "트레이서", "브리기테"]
CANNED_COMPOSITION_ENEMY_SET = set(CANNED_COMPOSITION_ENEMY_LIST)
CANNED_COMPOSITION_ALLY_SET = set(CANNED_COMPOSITION_ALLY_LIST)

CANNED_MAP_NAME = "왕의 길"
CANNED_MAP_SIDE = "defense"

CANNED_STAT_HERO = "솔저76"
CANNED_STAT_KILLS = 4
CANNED_STAT_DEATHS = 8
CANNED_STAT_DAMAGE = 6000

CANNED_STAY_HERO = "리퍼"
CANNED_STAY_ENEMY = "아나"

CANNED_GENJI_INTRO = {
    "detailed": (
        "겐지는 높은 기동성과 튕겨내기를 활용해 적의 후방을 교란하는 영웅입니다. "
        "하지만 광선 계열 공격이나 군중 제어기에는 대응이 어려워 이를 보유한 "
        "영웅들에게 취약한 모습을 보입니다."
    ),
    "simple": (
        "겐지는 기동성과 튕겨내기를 활용해 적 후방을 교란하는 영웅입니다. "
        "하지만 광선 공격이나 군중 제어기에 취약하므로 이를 보유한 영웅들을 "
        "상대할 때 주의가 필요합니다."
    ),
}
CANNED_GENJI_HARD_HEROES = [
    {"hero": "윈스턴", "note": "카운터 강도 높음"},
    {"hero": "시메트라", "note": "카운터 강도 높음"},
    {"hero": "브리기테", "note": "카운터 강도 중간"},
    {"hero": "모이라", "note": "카운터 강도 중간"},
]
CANNED_GENJI_EASY_HEROES = [
    {"hero": "위도우메이커", "note": "접근 시 암살 용이"},
    {"hero": "아나", "note": "생존기 빠지면 압박 가능"},
    {"hero": "젠야타", "note": "기동성 차이로 제압 가능"},
    {"hero": "한조", "note": "근접전에서 우위 점함"},
]
CANNED_GENJI_SUGGESTED_QUESTIONS = [
    "겐지 카운터 영웅 추천해줘",
    "겐지 상대할 때 팁 알려줘",
    "상대 겐지 대처법 더 자세히",
]

CANNED_COMPOSITION_INTRO = {
    "detailed": (
        "상대팀의 파라와 아나를 견제하면서 아군 라인하르트의 전진을 보조할 수 "
        "있는 지원가 영웅들을 추천합니다.\n"
        "안정적인 유지력과 원거리 대응 능력을 갖춘 영웅들로 조합의 균형을 "
        "맞춰보세요."
    ),
    "simple": (
        "상대 파라의 공중 견제와 아나의 힐밴을 고려하여 아군을 보호하고 "
        "안정적인 치유를 제공할 영웅을 추천합니다."
    ),
}
CANNED_COMPOSITION_HEROES = [
    {"hero": "바티스트", "note": "히트스캔으로 파라 견제 및 불사 장치로 생존 보조"},
    {"hero": "아나", "note": "원거리 힐과 생체 수류탄으로 상대 진영 압박 가능"},
    {"hero": "키리코", "note": "정화의 방울로 아군 보호 및 쿠나이로 파라 견제"},
    {"hero": "일리아리", "note": "히트스캔 공격으로 공중의 파라를 효과적으로 견제"},
]
CANNED_COMPOSITION_SUGGESTED_QUESTIONS = [
    "파라 잡기 좋은 영웅 추천해줘",
    "아나 견제는 어떻게 할까?",
    "우리 조합 운영법 알려줘",
]

CANNED_MAP_ANSWER = {
    "detailed": (
        "왕의 길 수비는 좁은 길목과 샛길을 활용해 적의 진입을 차단하고 고지대를 "
        "선점하는 것이 핵심입니다. 현재 역할 안에서 각 역할별로 추천하는 영웅과 "
        "운영법은 다음과 같습니다.\n\n"
        "1. 탱커: 레킹볼을 추천합니다. 좁은 길목과 샛길이 많아 갈고리 고정(우클)을 "
        "활용해 적의 뒷라인을 흔들고, 지뢰밭(q)으로 좁은 통로를 봉쇄하여 적의 진입을 "
        "강제로 지연시킬 수 있습니다.\n"
        "2. 딜러: 정크랫을 추천합니다. 화물 경로가 좁아 유탄과 충격 지뢰(shift)를 "
        "활용한 고지대 점령이 매우 효과적입니다. 덫(shift)으로 샛길을 봉쇄하고 "
        "유탄으로 입구에서 화력을 집중하십시오.\n"
        "3. 힐러: 아나를 추천합니다. 맵이 직선형으로 길게 뻗어 있어 저격 모드(우클)를 "
        "통해 안전한 거리에서 아군을 지원하기 좋습니다. 수면총(shift)과 생체 "
        "수류탄(e)으로 진입하는 적을 무력화하십시오.\n\n"
        "바로 적용할 것 3가지\n"
        "1. 정크랫을 선택했다면 좁은 골목과 샛길에 덫(shift)을 설치해 적의 우회 "
        "경로를 차단하십시오.\n"
        "2. 레킹볼을 선택했다면 갈고리 고정(우클)으로 고지대를 빠르게 선점하여 "
        "적의 시선을 분산시키십시오.\n"
        "3. 아나를 선택했다면 직선 지형을 활용해 아군 뒤편 고지대에서 생체 "
        "소총(좌클)으로 힐과 견제를 동시에 수행하십시오."
    ),
    "simple": (
        "왕의 길 수비는 좁은 길목과 고지대를 활용한 방어 전략이 핵심입니다.\n\n"
        "추천 영웅: 정크랫, 시그마, 아나\n"
        "- 정크랫: 좁은 골목과 샛길에 유탄과 덫을 배치해 진입로를 봉쇄하기 좋음\n"
        "- 시그마: 실험용 방벽(우클릭)으로 좁은 입구의 포킹을 차단하고 키네틱 "
        "손아귀(shift)로 화력을 흡수하기 좋음\n"
        "- 아나: 직선형 지형에서 생체 소총(좌클릭)으로 후방 지원이 용이하며 나노 "
        "강화제(q)로 아군 강화 가능\n\n"
        "바로 할 것 3가지\n"
        "1. 고지대를 선점하여 적의 진입 경로를 내려다보는 위치를 확보할 것\n"
        "2. 좁은 길목에 광역 피해 스킬을 집중하여 적의 진입 속도를 늦출 것\n"
        "3. 아군과 함께 리그룹하여 적의 기습적인 옆길 진입을 차단할 것"
    ),
}
CANNED_MAP_SUGGESTED_QUESTIONS = [
    "레킹볼 운영 팁 알려줘",
    "다른 탱커 추천해줘",
    "수비 시 좋은 자리 어디야?",
]

CANNED_STAT_ANSWER = {
    "detailed": (
        "현재 킬 4, 데스 8, 딜량 6000이라는 스탯은 무리한 진입으로 인해 생존력이 "
        "낮고, 교전 중 지속적인 화력을 투사하지 못하고 있음을 보여줍니다. 솔저: "
        "76은 고지대와 측면에서 중거리 지속 화력을 유지하는 것이 핵심입니다. "
        "데스가 많은 이유는 적의 시야에 너무 오래 노출되거나 생체장(e)을 적절한 "
        "위치에 깔지 못했기 때문일 가능성이 큽니다. 지금보다 생존에 집중하며 "
        "고지대를 선점하는 운영이 필요합니다.\n\n"
        "운영 개선 방안:\n"
        "1. 고지대와 측면 활용: 항상 지상보다는 고지대를 먼저 점령하여 적의 시야를 "
        "분산시키고, 적의 지원가나 딜러를 우선적으로 압박하십시오.\n"
        "2. 생체장(e)의 전략적 사용: 생체장(e)은 단순히 체력이 낮을 때 쓰는 것이 "
        "아니라, 교전 시작 전 고지대 거점이나 엄폐물 뒤에 미리 설치하여 유지력을 "
        "확보하는 용도로 사용하십시오.\n"
        "3. 질주(shift)를 통한 위치 선정: 질주(shift)는 단순히 이동용이 아니라, "
        "적의 공격을 피하거나 유리한 각도로 빠르게 재배치하는 용도로 활용하여 "
        "생존율을 높이십시오.\n\n"
        "바로 적용할 것 3가지:\n"
        "1. 교전 시 항상 생체장(e)을 깔 수 있는 엄폐물 근처에서 싸우기.\n"
        "2. 무리하게 적 본대로 들어가지 말고 고지대에서 중거리 사격 유지하기.\n"
        "3. 데스가 8회나 발생했으므로, 교전 중 체력이 절반 이하로 떨어지면 즉시 "
        "질주(shift)를 사용해 후퇴하고 재정비하기."
    ),
    "simple": (
        "현재 킬 4 데스 8은 생존력이 부족하고 교전 기여도가 낮음을 의미함. 딜량 "
        "6000은 지속 화력은 있으나 결정타가 부족한 상태임.\n\n"
        "고지대와 측면 중거리에서 지속 화력을 유지하며 적의 지원가와 딜러 헤드라인을 "
        "압박할 것.\n"
        "생체장(E)은 고지대 유지와 본대 버티기에 활용하고, 나선 로켓(우클)은 적의 "
        "체력이 낮을 때 마무리 용도로 사용할 것.\n"
        "질주(Shift)를 활용해 불리한 교전에서 빠르게 이탈하고, 고지대를 선점하여 "
        "시야를 확보할 것.\n\n"
        "바로 할 것 3가지\n"
        "1. 데스 수를 줄이기 위해 무리한 진입 대신 고지대 엄폐물 활용하기\n"
        "2. 나선 로켓(우클)을 쿨마다 쓰지 말고 적 체력이 낮을 때 마무리로 사용하기\n"
        "3. 생체장(E)을 아군과 함께 버티거나 고지대 유지용으로만 사용하기"
    ),
}
CANNED_STAT_SUGGESTED_QUESTIONS = [
    "생체장 활용법 알려줘",
    "포지션 잡는 법 알려줘",
    "어떻게 안 죽고 딜 넣지?",
]

CANNED_STAY_ANSWER = {
    "detailed": (
        "리퍼로 아나를 상대하는 것은 충분히 가능하며, 아나의 핵심 스킬을 무력화하는 "
        "운영이 중요합니다. 아나는 수면총(shift)과 생체 수류탄(e)을 보유하고 있어 "
        "리퍼에게 위협적이지만, 리퍼의 기동성과 무적기를 활용하면 충분히 제압할 수 "
        "있습니다.\n\n"
        "운영 팁:\n"
        "1. 망령화(shift)를 진입용으로 낭비하지 말고, 아나의 수면총(shift)이나 생체 "
        "수류탄(e)을 피하는 용도로 아껴두세요.\n"
        "2. 그림자 밟기(e)는 아나의 시야가 닿지 않는 고지대나 우회로로 이동하여 "
        "기습적인 근접 교전을 유도하는 데 사용하세요.\n"
        "3. 아나가 생체 수류탄(e)을 자신에게 사용하게 유도한 뒤, 헬파이어 "
        "샷건(좌클릭)으로 근접 폭딜을 넣으면 아나의 생존기를 강제로 뺄 수 "
        "있습니다.\n"
        "4. 아나의 수면총(shift)이 빠진 것을 확인한 후 죽음의 꽃(q)을 사용하면 "
        "훨씬 안전하게 다수의 적을 처치할 수 있습니다.\n\n"
        "바로 적용할 것 3가지:\n"
        "1. 아나의 수면총(shift)이 빠지기 전까지는 정면 진입을 자제하고 우회로를 "
        "이용하세요.\n"
        "2. 교전 중 아나가 생체 수류탄(e)을 던지는 모션을 취하면 즉시 "
        "망령화(shift)를 사용하여 효과를 무효화하세요.\n"
        "3. 아나에게 접근할 때는 항상 엄폐물을 끼고 이동하여 저격 각을 "
        "최소화하세요."
    ),
    "simple": (
        "아나의 수면총(Shift)과 생체 수류탄(E)을 망령화(Shift)로 회피하며 근접 "
        "거리까지 접근하기.\n"
        "그림자 밟기(E)는 아나의 시야가 닿지 않는 고지대나 우회로에서 사용하여 "
        "기습 각을 확보하기.\n"
        "아나가 생체 수류탄(E)을 자신에게 사용하거나 아군 탱커에게 던진 직후가 "
        "진입 최적기.\n\n"
        "운영 팁:\n"
        "- 아나의 수면총(Shift)이 빠지기 전까지는 정면 진입을 자제하고 코너를 "
        "활용해 압박하기.\n"
        "- 망령화(Shift)는 진입기보다 아나의 수면총(Shift)이나 생체 수류탄(E)을 "
        "무력화하는 탈출 및 생존기로 우선 사용하기.\n"
        "- 아나가 후방 엄폐물 뒤에 있다면 그림자 밟기(E)로 거리를 좁혀 헬파이어 "
        "샷건(좌클릭)의 근접 폭딜을 넣기.\n\n"
        "바로 할 것 3가지\n"
        "1. 아나의 수면총(Shift) 쿨타임 체크하기.\n"
        "2. 망령화(Shift)를 아나의 스킬 대응용으로 아껴두기.\n"
        "3. 우회로를 통해 아나의 후방 시야를 차단하며 접근하기."
    ),
}
CANNED_STAY_SUGGESTED_QUESTIONS = [
    "아나 수면총은 어떻게 피할까?",
    "망령화는 언제 쓰는 게 좋아?",
    "리퍼 운영 팁 더 알려줘",
]


def _find_stat_number(keywords: List[str], text: str) -> Optional[int]:
    for kw in keywords:
        match = re.search(rf"{re.escape(kw)}\s*(\d+)", text)
        if match:
            return int(match.group(1))
        match = re.search(rf"(\d+)\s*{re.escape(kw)}", text)
        if match:
            return int(match.group(1))
    return None


def match_canned_topic(message: str) -> Optional[str]:
    """이번 메시지가 5개 고정 버튼 중 하나(또는 그 비슷한 표현)와 같은 대상을
    가리키는지 판단한다. 대상이 다르면 None을 반환해 평소처럼 그래프를 태운다."""
    text = (message or "").strip()
    if not text:
        return None

    # 캐시된 5개 답변에는 특전 얘기가 없다. "나 솔저76 킬 4 데스 8 딜 6000인데
    # 특전 추천해줘"처럼 캐시 조건(영웅+수치)을 만족하면서 다른 것을 묻는 질문에
    # 캐시가 나가면, 특전은 한 글자도 없는 스탯 개선 답변이 그대로 돌아간다.
    if is_perk_question(text):
        return None

    # 영웅 유지(리퍼 유지 + 상대 아나) — 두 조건이 모두 맞을 때만 인정한다.
    if (
        hero_mentioned_as_current_hero(CANNED_STAY_HERO, text)
        and CANNED_STAY_ENEMY in find_all_heroes(text)
        and find_enemy_mentioned_hero(text) == CANNED_STAY_ENEMY
    ):
        return "stay_reaper_ana"

    # 스탯 피드백 — 수치까지 일치할 때만 캐시를 쓰고, 다르면 그래프로 보낸다.
    if find_first_hero(text) == CANNED_STAT_HERO and detect_stat_input(text):
        kills = _find_stat_number(["킬"], text)
        deaths = _find_stat_number(["데스", "사망"], text)
        damage = _find_stat_number(["딜량", "딜", "피해"], text)
        if (
            kills == CANNED_STAT_KILLS
            and deaths == CANNED_STAT_DEATHS
            and damage == CANNED_STAT_DAMAGE
        ):
            return "stat_soldier76"

    # 맵 운영(왕의 길 수비)
    if find_map(text) == CANNED_MAP_NAME and find_side(text) == CANNED_MAP_SIDE:
        return "map_kings_row_defense"

    # 조합 추천(고정 상대/아군 조합) — 두 조합이 정확히 일치할 때만 캐시를 쓴다.
    if (
        set(extract_enemy_team(text)) == CANNED_COMPOSITION_ENEMY_SET
        and set(extract_ally_team(text)) == CANNED_COMPOSITION_ALLY_SET
    ):
        return "composition_fixed"

    # 카운터(겐지) — 자기 영웅 선언이 아니라 카운터 대상으로 물을 때만 인정한다.
    if (
        CANNED_COUNTER_HERO in text
        and any(word in text for word in CANNED_COUNTER_TRIGGER_WORDS)
        and not hero_mentioned_as_current_hero(CANNED_COUNTER_HERO, text)
    ):
        return "counter_genji"

    return None


def _canned_base_context_patch(message: str, answer_style: str, intent: str) -> Dict[str, Any]:
    return {
        "last_user_message": message,
        "last_effective_message": message,
        "last_intent": intent,
        "role_filter": None,
        "no_enemy_turn_count": 0,
        "last_message_ts": time.time(),
        "answer_style": answer_style,
    }


def _canned_result(
    *,
    message: str,
    answer: str,
    intent: str,
    answer_style: str,
    context_patch: Dict[str, Any],
    recommendation_type: Optional[str] = None,
    recommended_heroes: Optional[List[str]] = None,
    suggested_questions: Optional[List[str]] = None,
    choice_buttons: Optional[List[Dict[str, str]]] = None,
    has_stats: bool = False,
    matchup_card: Optional[Dict[str, Any]] = None,
    recommend_card: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """format_response_node가 만드는 result와 동일한 모양으로 캐시 답변을
    돌려준다 — chat_api가 캐시/실제 그래프 어느 쪽 결과든 구분 없이 처리할 수
    있게 하기 위해서다."""
    return {
        "answer": sanitize_answer_for_user(answer, keep_dash_bullets=answer_style == "simple"),
        "message": message,
        "intent": intent,
        "recommendation_type": recommendation_type,
        "recommended_heroes": recommended_heroes or [],
        "suggested_questions": suggested_questions or [],
        "choice_buttons": choice_buttons or [],
        "context_patch": context_patch,
        "has_stats": has_stats,
        "answer_style": answer_style,
        "matchup_card": matchup_card,
        "recommend_card": recommend_card,
    }


def _build_canned_counter_genji_clarify(message: str, answer_style: str) -> Dict[str, Any]:
    """겐지 카운터 질문의 1턴째: 캐시 데이터는 "전체" 역할 답변만 있으므로,
    실제 clarify_role_filter_node를 그대로 재사용해 역할부터 물어본다."""
    clarify_state = {
        "message": message,
        "intent": "counter",
        "target_enemy": CANNED_COUNTER_HERO,
        "enemy_team": [],
        "target_enemy_narrowed": False,
        "enemy_role_focus": None,
        "context_patch": _canned_base_context_patch(message, answer_style, "counter"),
    }
    clarify_out = clarify_role_filter_node(clarify_state)
    context_patch = {
        **clarify_out["context_patch"],
        "pending_canned_topic": "counter_genji",
        "pending_canned_question": message,
    }
    return _canned_result(
        message=message,
        answer=clarify_out["answer"],
        intent="counter",
        answer_style=answer_style,
        context_patch=context_patch,
        choice_buttons=clarify_out["choice_buttons"],
    )


def _build_canned_counter_genji_card(message: str, answer_style: str) -> Dict[str, Any]:
    """겐지 카운터 질문의 2턴째("전체" 역할 선택 후): 미리 써둔 상성 카드를
    그대로 돌려준다."""
    context_patch = {
        **_canned_base_context_patch(message, answer_style, "counter"),
        "target_enemy": CANNED_COUNTER_HERO,
        "pending_canned_topic": None,
        "pending_canned_question": None,
        "focus_heroes": [h["hero"] for h in CANNED_GENJI_HARD_HEROES],
    }
    matchup_card = {
        "subject": CANNED_COUNTER_HERO,
        "subject_role": HERO_TO_ROLE.get(CANNED_COUNTER_HERO),
        "is_enemy": True,
        "hard_heroes": CANNED_GENJI_HARD_HEROES,
        "easy_heroes": CANNED_GENJI_EASY_HEROES,
    }
    return _canned_result(
        message=message,
        answer=CANNED_GENJI_INTRO[answer_style],
        intent="counter",
        answer_style=answer_style,
        context_patch=context_patch,
        recommendation_type="matchup_card",
        recommended_heroes=[h["hero"] for h in CANNED_GENJI_HARD_HEROES],
        suggested_questions=list(CANNED_GENJI_SUGGESTED_QUESTIONS),
        matchup_card=matchup_card,
    )


def _build_canned_composition(message: str, answer_style: str) -> Dict[str, Any]:
    context_patch = {
        **_canned_base_context_patch(message, answer_style, "composition"),
        "enemy_team": list(CANNED_COMPOSITION_ENEMY_LIST),
        # 아군 조합도 상대 조합과 똑같이 세션에 남긴다 — 실제 파이프라인이
        # merge_context_node에서 하는 일이다(ally_team/ally_team_ts). 이게 빠져
        # 있으면 "우리 조합 운영법 알려줘" 같은 후속 질문에서 아군만 비어 있어,
        # 세션에 남은 상대 조합이나 옛 값이 아군 자리로 흘러든다(2026-07-31).
        "ally_team": list(CANNED_COMPOSITION_ALLY_LIST),
        "ally_team_ts": time.time(),
        "focus_heroes": [h["hero"] for h in CANNED_COMPOSITION_HEROES],
    }
    recommend_card = {"mode": "composition", "heroes": CANNED_COMPOSITION_HEROES}
    return _canned_result(
        message=message,
        answer=CANNED_COMPOSITION_INTRO[answer_style],
        intent="composition",
        answer_style=answer_style,
        context_patch=context_patch,
        recommendation_type="recommend_card",
        recommended_heroes=[h["hero"] for h in CANNED_COMPOSITION_HEROES],
        suggested_questions=list(CANNED_COMPOSITION_SUGGESTED_QUESTIONS),
        recommend_card=recommend_card,
    )


def _build_canned_map(message: str, answer_style: str) -> Dict[str, Any]:
    context_patch = {
        **_canned_base_context_patch(message, answer_style, "map_strategy"),
        "map_name": CANNED_MAP_NAME,
        "side": CANNED_MAP_SIDE,
        "focus_heroes": [],
    }
    return _canned_result(
        message=message,
        answer=CANNED_MAP_ANSWER[answer_style],
        intent="map_strategy",
        answer_style=answer_style,
        context_patch=context_patch,
        suggested_questions=list(CANNED_MAP_SUGGESTED_QUESTIONS),
    )


def _build_canned_stat(message: str, answer_style: str) -> Dict[str, Any]:
    context_patch = {
        **_canned_base_context_patch(message, answer_style, "performance_improve"),
        "current_hero": CANNED_STAT_HERO,
        "current_hero_role": HERO_TO_ROLE.get(CANNED_STAT_HERO),
        "focus_heroes": [CANNED_STAT_HERO],
        "my_stats": {
            CANNED_STAT_HERO: {
                "kills": CANNED_STAT_KILLS,
                "assists": None,
                "deaths": CANNED_STAT_DEATHS,
                "damage": CANNED_STAT_DAMAGE,
                "healing": None,
            }
        },
    }
    return _canned_result(
        message=message,
        answer=CANNED_STAT_ANSWER[answer_style],
        intent="performance_improve",
        answer_style=answer_style,
        context_patch=context_patch,
        has_stats=True,
        suggested_questions=list(CANNED_STAT_SUGGESTED_QUESTIONS),
    )


def _build_canned_stay(message: str, answer_style: str) -> Dict[str, Any]:
    context_patch = {
        **_canned_base_context_patch(message, answer_style, "stay"),
        "current_hero": CANNED_STAY_HERO,
        "current_hero_role": HERO_TO_ROLE.get(CANNED_STAY_HERO),
        "target_enemy": CANNED_STAY_ENEMY,
        "focus_heroes": [CANNED_STAY_HERO],
    }
    return _canned_result(
        message=message,
        answer=CANNED_STAY_ANSWER[answer_style],
        intent="stay",
        answer_style=answer_style,
        context_patch=context_patch,
        suggested_questions=list(CANNED_STAY_SUGGESTED_QUESTIONS),
    )


_CANNED_BUILDERS = {
    "composition_fixed": _build_canned_composition,
    "map_kings_row_defense": _build_canned_map,
    "stat_soldier76": _build_canned_stat,
    "stay_reaper_ana": _build_canned_stay,
}


def try_canned_shortcut(
    message: str,
    role_filter: Optional[str],
    context: Dict[str, Any],
    answer_style: Optional[str],
) -> Dict[str, Any]:
    """5개 고정 버튼(과 비슷한 표현)에 대해 그래프를 실행하지 않고 미리 준비된
    답을 즉시 돌려준다. chat_api가 run_chatbot_graph보다 먼저 호출한다.

    반환값: result(매칭 시 응답 결과, 아니면 None), resume_message(캐시된
    역할 되묻기에 캐시 없는 역할로 답해 그래프를 태워야 할 때 넘길 원문,
    그 외 None), context_updates(세션에 즉시 반영할 정리용 값)."""
    style = answer_style or context.get("answer_style") or "detailed"
    pending_topic = context.get("pending_canned_topic")
    pending_question = context.get("pending_canned_question") or ""
    context_updates = (
        {"pending_canned_topic": None, "pending_canned_question": None}
        if pending_topic else {}
    )

    if not message and role_filter and pending_topic:
        if pending_topic == "counter_genji" and role_filter == "all":
            result = _build_canned_counter_genji_card(pending_question, style)
            return {"result": result, "resume_message": None, "context_updates": context_updates}
        return {"result": None, "resume_message": pending_question, "context_updates": context_updates}

    if message:
        topic = match_canned_topic(message)
        if topic == "counter_genji":
            result = _build_canned_counter_genji_clarify(message, style)
            return {"result": result, "resume_message": None, "context_updates": context_updates}
        builder = _CANNED_BUILDERS.get(topic)
        if builder:
            result = builder(message, style)
            return {"result": result, "resume_message": None, "context_updates": context_updates}

    return {"result": None, "resume_message": None, "context_updates": context_updates}

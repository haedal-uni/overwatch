"""그래프 앞단 노드 — 입력 검증, 컨텍스트 파악/병합, 되묻기.

흐름: validate_input → parse_stats_from_text → llm_parse_context →
merge_context → (필요하면) clarify_role_filter / clarify_focus_hero.
각 가드를 둔 배경은 chat_모듈_구조.md 참고.
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

from chat.rag import components as chatbot_service
from chat.graph.state import ChatbotGraphState
from chat.domain.heroes import (
    HERO_TO_ROLE,
    HEROES,
    ROLE_HEROES,
    ROLE_LABELS,
    find_all_heroes,
    find_map,
    find_side,
    get_hero_role,
    hero_mentioned_in_text,
    josa_eul_reul,
    make_role_filter,
    normalize_hero_name,
    role_filter_label,
)
from chat.domain.intent_rules import (
    ENEMY_ROLE_FOCUS_LABELS,
    ROLE_NARROWING_MAX_AGE_SECONDS,
    alternate_roster_size,
    can_be_roster_size,
    detect_roster_size,
    detect_stat_input,
    extract_ally_team,
    extract_enemy_team,
    find_ally_complaint_hero,
    find_enemy_mentioned_hero,
    find_enemy_role_focus,
    find_performance_comparison_heroes,
    find_self_comparison_heroes,
    find_synergy_ally_heroes,
    hero_listed_in_ally_comp,
    hero_mentioned_as_current_hero,
    hero_negated_as_self,
    analyze_team_comp,
    infer_current_hero,
    infer_intent_by_rule,
    infer_target_enemy,
    is_composition_reask,
    is_ellipsis_followup,
    is_performance_comparison_question,
    is_perk_question,
    is_target_priority_question,
    is_pure_role_correction,
    is_self_hero_negation_correction,
    mentions_self,
    resolve_roster_size,
    role_filter_from_text,
    roster_size_button_label,
    wants_composition_recommendation,
)
from chat.rag.llm_utils import call_llm_text, safe_json_loads
from chat.rag.matchup_tables import rank_opponents

logger = logging.getLogger(__name__)


def validate_input_node(state: ChatbotGraphState) -> ChatbotGraphState:
    message = state.get("message", "").strip()
    role_filter = state.get("role_filter")
    focus_hero_pick = state.get("focus_hero_pick")
    roster_size = state.get("roster_size")

    if not message and not role_filter and not focus_hero_pick and not roster_size:
        return {"error": "질문을 입력해주세요."}

    return {
        "message": message,
        "need_clarification": False,
    }


def parse_stats_from_text_node(state: ChatbotGraphState) -> ChatbotGraphState:
    if state.get("error"):
        return {}

    message = state.get("message", "")

    if not detect_stat_input(message):
        return {"has_stats": False}

    try:
        _chatbot, _retriever, llm = chatbot_service.get_chatbot_components()

        prompt = f"""
너는 오버워치2 게임 스탯 파서다.
사용자가 자유 형식으로 입력한 텍스트에서 영웅별 스탯을 추출해라.

사용자 입력:
\"\"\"{message}\"\"\"

추출 규칙:
1. 상대팀/적팀 스탯과 내(사용자) 스탯, 우리팀 스탯을 구분해라.
2. 킬(kills), 도움/어시(assists), 데스/사망(deaths), 딜량/피해(damage), 힐량/치유(healing) 항목을 파악해라.
3. 언급되지 않은 항목은 null로 둬라.
4. 영웅 이름은 한국어로 정규화해라 (예: "둠피" → "둠피스트", "솔저" → "솔저: 76").
5. 스탯이 전혀 없으면 모든 값을 빈 dict로 반환해라.

아래 JSON 형식으로만 답해라. 다른 텍스트는 출력하지 마라.

{{
  "enemy_stats": {{
    "영웅이름": {{
      "kills": 숫자 또는 null,
      "assists": 숫자 또는 null,
      "deaths": 숫자 또는 null,
      "damage": 숫자 또는 null,
      "healing": 숫자 또는 null
    }}
  }},
  "my_stats": {{
    "영웅이름": {{
      "kills": 숫자 또는 null,
      "assists": 숫자 또는 null,
      "deaths": 숫자 또는 null,
      "damage": 숫자 또는 null,
      "healing": 숫자 또는 null
    }}
  }},
  "my_team_stats": {{
    "영웅이름": {{
      "kills": 숫자 또는 null,
      "assists": 숫자 또는 null,
      "deaths": 숫자 또는 null,
      "damage": 숫자 또는 null,
      "healing": 숫자 또는 null
    }}
  }}
}}
"""
        raw_text = call_llm_text(llm, prompt)
        parsed = safe_json_loads(raw_text, default={})

        enemy_stats: Dict[str, Any] = parsed.get("enemy_stats") or {}
        my_stats: Dict[str, Any] = parsed.get("my_stats") or {}
        my_team_stats: Dict[str, Any] = parsed.get("my_team_stats") or {}

        high_threat_enemy: Optional[str] = None
        max_score = -1
        for hero, stats in enemy_stats.items():
            score = (stats.get("damage") or 0) + (stats.get("kills") or 0) * 500
            if score > max_score:
                max_score = score
                high_threat_enemy = hero

        has_stats = bool(enemy_stats or my_stats or my_team_stats)

        logger.info(
            "[스탯 파싱] enemy=%s my=%s team=%s high_threat=%s",
            list(enemy_stats.keys()),
            list(my_stats.keys()),
            list(my_team_stats.keys()),
            high_threat_enemy,
        )

        return {
            "has_stats": has_stats,
            "enemy_stats": enemy_stats,
            "my_stats": my_stats,
            "my_team_stats": my_team_stats,
            "high_threat_enemy": high_threat_enemy,
        }

    except Exception as exc:
        logger.exception("parse_stats_from_text_node 오류: %s", exc)
        return {"has_stats": False}


def should_reset_enemy_context(
    message: str,
    context: Dict[str, Any],
    incoming_map: Optional[str],
    new_current_hero: Optional[str],
) -> bool:
    prev_hero = context.get("current_hero")
    # 스탯창으로 영웅이 확정된 판에서는 영웅 교체로 보지 않는다.
    my_stats_hero = normalize_hero_name(
        next(iter((context.get("my_stats") or {}).keys()), None)
    )
    if prev_hero and new_current_hero and prev_hero != new_current_hero:
        if my_stats_hero and my_stats_hero in (prev_hero, new_current_hero):
            logger.info(
                "[CONTEXT RESET SKIPPED] 영웅 변경처럼 보이지만(%s → %s) 점수판 "
                "확정 영웅(%s)과 겹쳐 실제 교체로 보지 않음 — 컨텍스트 유지",
                prev_hero, new_current_hero, my_stats_hero,
            )
            return False
        logger.info(
            "[CONTEXT RESET] 영웅 변경 감지: %s → %s — 적팀 컨텍스트 초기화",
            prev_hero, new_current_hero,
        )
        return True

    prev_map = context.get("map_name")
    if incoming_map and prev_map and incoming_map != prev_map:
        logger.info(
            "[CONTEXT RESET] 맵 변경 감지: %s → %s — 적팀 컨텍스트 초기화",
            prev_map, incoming_map,
        )
        return True

    return False


def llm_parse_context_node(state: ChatbotGraphState) -> ChatbotGraphState:
    if state.get("error"):
        return {}

    message = state.get("message", "")

    # 버튼 클릭 턴(message='')은 LLM을 부르지 않고 규칙 기반 폴백에 맡긴다.
    if not message.strip():
        return {}

    try:
        _chatbot, _retriever, llm = chatbot_service.get_chatbot_components()

        context = state.get("conversation_context", {}) or {}

        hero_list = ", ".join(HEROES)
        role_map_text = "\n".join(
            f"- {role}: {', '.join(heroes)}"
            for role, heroes in ROLE_HEROES.items()
        )

        prompt = f"""
너는 오버워치2 코칭 챗봇의 문맥 파악 모듈이다.
사용자 메시지와 이전 대화 기록을 읽고 아래 항목을 JSON으로 추출해라.

이전 대화 컨텍스트:
- 이전 영웅: {context.get("current_hero", "없음")}
- 이전 intent: {context.get("last_intent", "없음")}
- 이전 target_enemy: {context.get("target_enemy", "없음")}
- 이전 enemy_team: {context.get("enemy_team", [])}
- 이전 아군 조합(ally_team): {context.get("ally_team", [])}

사용자 메시지:
\"\"\"{message}\"\"\"

영웅 목록:
{hero_list}

역할 분류:
{role_map_text}

아래 JSON 형식으로만 답해라. 다른 텍스트는 출력하지 마라.

{{
  "intent": "counter | stay | swap | composition | performance_improve | situation | map_strategy | general | off_topic 중 하나",
  "current_hero": "사용자가 지금 플레이 중인 영웅 이름 또는 null",
  "current_hero_role": "tank | damage | support 또는 null",
  "target_enemy": "카운터하거나 상대해야 할 적 영웅 이름 또는 null",
  "enemy_team": ["적팀 영웅1", "적팀 영웅2"],
  "ally_team": ["우리팀(아군) 영웅1", "우리팀(아군) 영웅2"]
}}

intent 분류 기준 (각 항목의 예시를 참고해라):

1. counter — 특정 영웅의 대표 카운터/상성 목록을 묻는 질문.
   예: "겐지 카운터 알려줘", "겐지 상성 알려줘", "겐지가 상대하기 어려운 영웅
   알려줘", "겐지가 상대하기 쉬운 영웅 알려줘".
2. stay — 사용자가 지금 영웅을 유지한 채로 상대 영웅을 어떻게 파훼할지 묻는 질문.
   예: "겐지로 윈스턴 상대법 알려줘", "파라로 솔저 어떻게 상대해?", "라인 안
   바꾸고 라마트라 어떻게 막아?".
3. swap — 지금 영웅이 힘들어서 다른 영웅으로 교체하거나 추천 영웅을 묻는 질문.
   예: "겐지 하는데 윈스턴 힘들어. 다른 영웅 추천해줘", "파라 말고 뭐 하면
   좋아?", "둠피 때문에 힘든데 바꿀 영웅 추천해줘".
4. composition — 상대팀 조합과 우리팀 조합을 함께 알려주면서 영웅 추천이나
   운영 방향을 묻는 질문.
   예: "상대 조합이 둠피, 겐지, 트레, 아나, 루시우고 우리팀이 윈스턴, 소전,
   키리코, 루시우야. 나는 뭐 하면 돼?".
5. performance_improve — 스탯, 운영, 실력 향상, 플레이 개선을 묻는 질문.
   예: "내 솔저 딜 6000 킬 4 데스 8이야", "겐지 운영법 알려줘", "아나 잘하는
   법 알려줘".
6. situation — 인게임에서 특정 상대나 상황 때문에 압박받고 있다는 것을 그대로
   토로하는 질문(추천이나 대표 카운터를 명시적으로 요청하는 게 아니라, 지금
   겪고 있는 어려움 자체를 말하는 문장).
   예: "파라가 계속 압박해", "둠피가 계속 힐러 물어", "트레이서 때문에 뒤가
   터져".
7. map_strategy — 맵, 공격, 수비, 거점, 화물 등 맵 전략을 묻는 질문.
   예: "왕의 길 수비 조합 추천해줘", "리장타워에서 뭐 하면 좋아?".
8. general — 위 항목에 속하지 않는 일반 질문.
9. off_topic — 오버워치2 게임과 전혀 무관한 메시지(인사말, 잡담, 다른 주제).

핵심 분류 규칙(가장 자주 틀리는 부분이니 반드시 지켜라):
- "상대법", "어떻게 상대", "어떻게 잡", "어떻게 막", "파훼", "대처", "견제"라는
  단어가 있다고 해서 무조건 counter로 분류하지 마라. counter는 "카운터",
  "상성", "상대하기 어려운", "상대하기 쉬운"처럼 대표 상성 목록 자체를
  요청하는 표현일 때만 해당한다.
- 사용자가 "X로 Y 상대법/어떻게 상대/파훼/대처/견제"처럼 자기 영웅(X)을 유지한
  채로 상대 영웅(Y)을 어떻게 다룰지 묻는다면 counter가 아니라 stay다.
  예: "겐지로 윈스턴 상대법 알려줘"는 counter가 아니라 stay다.
- 사용자가 "다른 영웅", "바꾸기", "교체", "말고", "픽 추천"처럼 교체 의도를
  보이면 swap이다. 단, "안 바꾸고", "바꾸지 않고", "그대로", "유지", "원챔"
  같은 표현이 있으면 swap이 아니라 stay다.
- 상대 조합과 우리팀 조합이 함께 나오면(예: "상대는 A B C, 우리팀은 D E F")
  intent는 반드시 composition이다. 이때 current_hero는 아직 정해지지 않은
  경우가 많으므로 무리하게 짐작해서 채우지 마라.
- 킬, 데스, 딜량, 힐량 같은 스탯이 나오거나 운영법/실력 향상을 물으면
  performance_improve다.
- "압박", "계속 물어", "계속 죽어", "힘들어", "못 막겠어", "뒤가 터져" 같은
  인게임 위기 표현은(다른 영웅 추천을 명시적으로 요청하지 않는 한) situation
  이다.

예시:
- "겐지 카운터 알려줘" → counter
- "겐지로 윈스턴 상대법 알려줘" → stay
- "겐지 하는데 윈스턴 힘들어. 다른 영웅 추천해줘" → swap
- "파라가 계속 압박해" → situation
- "상대 조합이 둠피, 겐지, 트레, 아나, 루시우고 우리팀이 윈스턴, 소전, 키리코,
  루시우야" → composition

추가 규칙:
0. 메시지가 오버워치2 게임(영웅, 전략, 상대 대처, 팀 조합, 맵, 스탯 등)과 전혀
   무관하면(예: 단순 인사말, 잡담, 다른 게임/주제 질문) intent를 "off_topic"으로
   해라. 이 경우 current_hero/current_hero_role/target_enemy는 null,
   enemy_team은 빈 배열로 둬라. 메시지가 오버워치와 조금이라도 관련이 있다면
   (영웅 이름, 게임 상황, 전략 관련 표현이 하나라도 있다면) off_topic으로
   분류하지 마라 — 애매하면 off_topic이 아니라 "general"로 분류해라.
1. current_hero는 사용자가 직접 플레이하는 영웅만. 상대 영웅은 target_enemy나 enemy_team에.
2. 힐/지원을 받지 못한다는 불만 표현(예: "힐을 못 받는다", "지원이 끊긴다", "케어가 안 된다" 등
   어떤 표현이든)은 사용자가 지원 부족 문제를 겪고 있다는 뜻이지, 사용자 본인이 힐러를
   플레이 중이라는 뜻이 아니다. 이런 표현만으로 current_hero_role을 "support"로 단정하지 마라.
   이 불만의 주체로 특정 영웅 이름이 등장하면(예: "메르시가 힐을 안 한다", "메르시가
   나를 안 봐준다", "X가 케어를 안 한다", "X가 탱킹/딜을 못 한다") 그 영웅은 아군이다
   — ally_team에 넣고, target_enemy/enemy_team에는 절대 넣지 마라. current_hero로도
   단정하지 마라(사용자 자신이 아니라 동료를 말하는 것이다).
3. swap일 때 current_hero는 바꾸기 전 영웅(이미 플레이 중이라고 말한 영웅)이고,
   교체 후보로 언급된 영웅은 current_hero도 target_enemy도 아니다. 따라서
   target_enemy는 반드시 null로 설정하라.
4. 영웅 이름이 메시지에 없으면 current_hero를 null로 둬라 — 이전 대화에서
   어떤 영웅 얘기가 있었든, 자동으로 이어받지 마라.
5. 새 판을 시작하는 맥락(다른 영웅 언급, "이번엔" 등)이면 이전 enemy_team/target_enemy는 무시해라.
6. current_hero_role은 역할 분류를 참고해서 current_hero의 역할을 반환해라.
7. target_enemy와 enemy_team은 사용자가 이번 메시지에서 영웅 이름을 직접 언급했을 때만
   채워라. 적의 역할이나 행동(예: "힐러가 자꾸 죽어서", "탱커가 먼저 빠져서")만 설명하고
   구체적인 영웅 이름을 말하지 않았다면, 그 역할에 해당하는 임의의 영웅을 짐작해서
   채우지 말고 target_enemy와 enemy_team을 반드시 null/[]로 둬라. 이전 대화에서 이미
   다른 상대를 언급했더라도, 이번 메시지에 영웅 이름이 없으면 절대 이어받지 마라.
8. 이 규칙들은 일반적인 패턴이다. 특정 영웅 이름이나 정확히 같은 문장 형태와
   일치할 때만 적용되는 것이 아니라, 같은 의미 구조를 가진 모든 메시지에 동일하게
   적용해야 한다.
9. 사용자가 "X를 하고 싶어", "X 쓸건데", "X 할건데", "X 고정", "X 원챔",
"X로 이기고 싶어", "X로 즐기고 싶어", "X 해도 돼?"처럼 말하면
current_hero는 X이고 intent는 "stay"로 판단해라.
이 경우 사용자는 영웅 교체 추천을 원하는 것이 아니라,
X를 유지한 상태에서 상대 조합을 이기는 운영법을 원하는 것이다.
10. ally_team은 사용자가 이번 메시지에서 아군으로 언급한 영웅을 채운다. 다음
    경우 모두 아군으로 본다 — 짐작으로 채우지 말고 아래 경우에만 채워라:
    - "우리팀"/"아군" 영웅으로 직접 언급("상대팀은 A B C D E, 우리팀은 C E F G").
    - 규칙 2의 동료 불만 표현("메르시가 힐을 안 한다" 등)의 주체.
    - "X랑 조합 어때?", "X와 같이 쓰면 어때?", "X랑 할 때 어떻게 운영해?"처럼
      영웅과의 시너지/조합을 묻는 문장에 언급된 영웅.
11. "A를 고르면/픽하면 B가 잘 나올까?", "A랑 B랑 둘 다 ~하잖아"처럼 자기 팀
    소속(이미 쓰고 있거나 고민 중인 아군 후보) 영웅끼리 비교/나열하는 문장은
    target_enemy/enemy_team이 아니다. "상대", "적", "카운터", "때문에"처럼
    적을 가리키는 표현이 없다면, 문장에 영웅 이름이 있어도 그 영웅을
    target_enemy로 짐작해서 채우지 마라.
12. 영웅 두 명 이상이 나오고 "조합", "시너지", "궁합", "같이 쓰면", "같이 하면"
    같은 표현이 있으며 "상대", "적", "카운터", "때문에" 같은 적대 표현이 없다면,
    언급된 영웅은 모두 ally_team이고 intent는 composition이다. current_hero는
    사용자가 자신이 그 영웅을 플레이한다고 직접 말하지 않았다면 null로 둬라.
13. "맵 이름 + 영웅 이름 + 활용법/운영법" 구조(예: "일리오스 메르시 활용법
    알려줘")는 그 맵에서 그 영웅을 어떻게 쓰는지 설명해달라는 뜻이지, 사용자가
    그 영웅을 플레이한다고 밝힌 것이 아니다. intent는 map_strategy이고
    current_hero는 null로 둬라(자기 선언 표현이 별도로 없는 한).
14. "A랑 B 중 누가 더 잘했어/잘하는지", "A 아니면 B, 누가 나아?"처럼 아군
    2명 이상의 실제 활약/기여도를 비교해달라는 질문은 규칙 12(조합/시너지
    평가)가 아니라 performance_improve다 — "조합", "시너지", "궁합", "같이
    쓰면" 같은 표현이 전혀 없고 "누가 (더) 잘했/잘하/못했/나은"류 비교·판단
    요청이 핵심이면, 영웅 이름이 2개 이상이어도 그 조합 자체를 평가해달라는
    질문으로 보지 마라. 이때도 ally_team은 규칙 10에 따라 채운다.
15. "이전 아군 조합"에 있는 영웅이 이번 메시지에 "상대", "적" 같은 적대 표현 없이
    다시 나오면 그 영웅은 여전히 아군이다 — ally_team에 넣고 target_enemy/
    enemy_team에는 넣지 마라("시온만 킬하고"는 아군 시온이 킬을 낸다는 뜻이다).

예시:
- "메르시가 힐을 안할건데 뭘로 힐을 많이 해야할까" → intent: performance_improve,
  current_hero: null, ally_team: ["메르시"], target_enemy: null, enemy_team: [].
- "둠피 메르시 조합 어때?" → intent: composition, ally_team: ["둠피스트", "메르시"],
  current_hero: null, target_enemy: null, enemy_team: [].
- "일리오스 메르시 활용법 알려줘" → intent: map_strategy, current_hero: null,
  target_enemy: null, enemy_team: [].
- "아나랑 바티스트중 누가 더 잘 했어" → intent: performance_improve,
  current_hero: null, ally_team: ["아나", "바티스트"], target_enemy: null, enemy_team: [].
"""

        raw = call_llm_text(llm, prompt)
        if isinstance(raw, list):
            raw = "\n".join(str(x) for x in raw)
        elif not isinstance(raw, str):
            raw = str(raw)

        parsed = safe_json_loads(raw, default={})
        if not isinstance(parsed, dict):
            logger.warning("[LLM_PARSE_CONTEXT] 파싱 실패, 규칙 기반으로 폴백")
            return {}

        result: Dict[str, Any] = {}

        intent = parsed.get("intent")
        if intent in [
            "counter", "stay", "swap", "composition", "performance_improve",
            "situation", "map_strategy", "general", "off_topic",
        ]:
            result["llm_intent"] = intent

        enemy_mentioned_in_message = find_enemy_mentioned_hero(message)
        current_hero = parsed.get("current_hero")
        current_hero_confirmed_in_message = False
        if current_hero and isinstance(current_hero, str):
            normalized = normalize_hero_name(current_hero.strip())
            if normalized in [normalize_hero_name(h) for h in HEROES]:
                if (
                    enemy_mentioned_in_message == normalized
                    and not hero_mentioned_as_current_hero(normalized, message)
                ):
                    logger.info(
                        "[LLM CURRENT HERO GUARD] '%s'는 이번 메시지에서 상대 영웅으로 "
                        "언급되어 current_hero 후보에서 제외함: %s",
                        normalized,
                        message,
                    )
                else:
                    result["llm_current_hero"] = normalized
                    role = HERO_TO_ROLE.get(normalized)
                    if role:
                        result["llm_current_hero_role"] = role
                    # 원문 등장 여부만 플래그로 남긴다(swap 가드용).
                    if hero_mentioned_as_current_hero(normalized, message):
                        current_hero_confirmed_in_message = True
        result["llm_current_hero_confirmed"] = current_hero_confirmed_in_message

        # swap인데 원문에 자기 영웅 언급이 없으면 오분류로 보고 general로 되돌린다.
        swap_guard_triggered = False
        if result.get("llm_intent") == "swap" and not current_hero_confirmed_in_message:
            logger.info(
                "[SWAP INTENT GUARD] intent=swap인데 메시지에 current_hero('%s')가 "
                "직접 언급되지 않아 general로 재분류함: %s",
                current_hero, message,
            )
            result["llm_intent"] = "general"
            swap_guard_triggered = True
        result["swap_guard_triggered"] = swap_guard_triggered

        # 아군이 상대로 채택되지 않도록 상대 검증보다 먼저 계산한다.
        ally_team = parsed.get("ally_team", [])
        verified_ally_team = []
        if isinstance(ally_team, list):
            for h in ally_team:
                n = normalize_hero_name(h)
                if n in [normalize_hero_name(x) for x in HEROES] and hero_mentioned_in_text(n, message):
                    verified_ally_team.append(n)
        if verified_ally_team:
            result["llm_ally_team"] = verified_ally_team
        message_complaint_hero = find_ally_complaint_hero(message)
        # 세션 아군 조합에 있는 영웅은 이번에 상대로 지목되지 않는 한 아군이다.
        session_allies = {
            normalize_hero_name(h) for h in (context.get("ally_team") or [])
            if normalize_hero_name(h) != enemy_mentioned_in_message
        }
        ally_excluded = (
            session_allies
            | set(verified_ally_team)
            | set(extract_ally_team(message))
            | set(find_self_comparison_heroes(message))
            | set(find_synergy_ally_heroes(message))
            | ({message_complaint_hero} if message_complaint_hero else set())
        )

        # 상대는 원문에 실제 등장하고 아군으로 분류되지 않았을 때만 채택한다.
        target_enemy = parsed.get("target_enemy")
        if target_enemy and isinstance(target_enemy, str):
            normalized_enemy = normalize_hero_name(target_enemy.strip())

            if normalized_enemy in ally_excluded:
                logger.info(
                    "[LLM_PARSE_CONTEXT] target_enemy='%s'가 이번 메시지에서 아군으로 "
                    "함께 언급되어 무시함: %s",
                    normalized_enemy, message,
                )
            elif (
                normalized_enemy in [normalize_hero_name(h) for h in HEROES]
                and hero_mentioned_in_text(normalized_enemy, message)
            ):
                result["llm_target_enemy"] = normalized_enemy

            elif normalized_enemy:
                logger.info(
                    "[LLM_PARSE_CONTEXT] target_enemy='%s'가 메시지 원문에 없어 무시함: %s",
                    normalized_enemy, message,
                )

        enemy_team = parsed.get("enemy_team", [])
        if isinstance(enemy_team, list) and enemy_team:
            verified_team = []
            for h in enemy_team:
                n = normalize_hero_name(h)
                if (
                    n in [normalize_hero_name(x) for x in HEROES]
                    and hero_mentioned_in_text(n, message)
                    and n not in ally_excluded
                ):
                    verified_team.append(n)
            if verified_team:
                result["llm_enemy_team"] = verified_team

        logger.info("[LLM_PARSE_CONTEXT] %s", result)
        return result

    except Exception as exc:
        logger.exception("llm_parse_context_node 오류: %s", exc)
        return {}


# 이만큼 지나면 새 판으로 보고 coach_context를 통째로 비운다.
SESSION_TIMEOUT_SECONDS = 10 * 60


def is_session_timed_out(context: Dict[str, Any], now_ts: Optional[float] = None) -> bool:
    """마지막 메시지로부터 SESSION_TIMEOUT_SECONDS가 지났는가.

    그래프를 우회하는 캐시 경로(chat_api)도 같은 판단을 쓴다.
    """
    last_message_ts = context.get("last_message_ts")
    if not last_message_ts:
        return False
    return ((now_ts if now_ts is not None else time.time()) - last_message_ts) > SESSION_TIMEOUT_SECONDS


def inheritable_self_hero(
    context: Dict[str, Any],
    effective_message: str,
    raw_message: str,
    now_ts: float,
    this_turn_ally_listed: bool,
    explicit_role_filter: Optional[str],
) -> Optional[str]:
    """최근(5분 이내)에 밝힌 자기 영웅을 이번 턴에 이어받을 수 있으면 그 영웅."""
    session_hero = normalize_hero_name(context.get("current_hero"))
    hero_ts = context.get("current_hero_ts")
    if not session_hero or not hero_ts or explicit_role_filter:
        return None
    if now_ts - hero_ts > ROLE_NARROWING_MAX_AGE_SECONDS:
        return None
    if not mentions_self(effective_message):
        return None
    if hero_negated_as_self(session_hero, raw_message):
        return None
    # 조합을 나열하고 픽을 묻는 질문은 새로 고르는 상황이라 이어받지 않는다.
    if this_turn_ally_listed and wants_composition_recommendation(effective_message):
        return None
    return session_hero


def merge_context_node(state: ChatbotGraphState) -> ChatbotGraphState:
    message = state.get("message", "").strip()
    context = state.get("conversation_context", {}) or {}

    now_ts = time.time()
    last_message_ts = context.get("last_message_ts")
    session_timed_out = is_session_timed_out(context, now_ts)
    if session_timed_out:
        logger.info(
            "[SESSION TIMEOUT] 마지막 메시지로부터 %.0f초 경과 — 컨텍스트 초기화 (새 게임으로 간주)",
            now_ts - last_message_ts,
        )
        context = {}

    explicit_role_filter = state.get("role_filter") or role_filter_from_text(message)
    awaiting_role_filter_reply = bool(context.get("pending_question"))
    role_filter_reply_consumed = bool(explicit_role_filter and awaiting_role_filter_reply)
    # 세션의 role_filter는 새 질문에 자동 재사용하지 않는다.
    role_filter = explicit_role_filter

    # 주제 영웅 되묻기에 응답한 턴인지.
    pending_intent = context.get("pending_intent")
    awaiting_focus_hero_reply = bool(context.get("pending_question")) and pending_intent == "clarify_focus_hero"
    focus_hero_pick = normalize_hero_name(state.get("focus_hero_pick")) if state.get("focus_hero_pick") else None
    focus_hero_reply_consumed = bool(awaiting_focus_hero_reply and focus_hero_pick)

    # 답변 스타일은 이번 턴 값 우선, 없으면 세션 값.
    requested_answer_style = state.get("answer_style")
    if requested_answer_style not in ("simple", "detailed"):
        requested_answer_style = None
    answer_style = requested_answer_style or context.get("answer_style") or "detailed"

    # 인원수는 추측하지 않는다(버튼이나 직접 선언이 있을 때만).
    declared_roster_size = state.get("roster_size") or detect_roster_size(message)

    effective_message = message
    # 직전 질문을 새 조건으로 다시 답하는 턴인지(LLM 추출값이 비어 아래에서 보완).
    resumed_previous_question = False
    if role_filter_reply_consumed or focus_hero_reply_consumed:
        effective_message = context.get("pending_question")
    elif explicit_role_filter or declared_roster_size:
        # 버튼 정정과 말로 한 정정을 똑같이 처리한다 — 직전 질문을 다시 태운다.
        previous_question = (
            context.get("last_effective_message") or context.get("last_user_message") or ""
        )
        if not message:
            effective_message = previous_question
            resumed_previous_question = bool(previous_question)
        elif previous_question and is_pure_role_correction(message):
            logger.info(
                "[SPOKEN ROLE CORRECTION] 역할/인원수 정정('%s') — 직전 질문('%s')을 다시 답함",
                message, previous_question,
            )
            effective_message = previous_question
            resumed_previous_question = True
    elif is_self_hero_negation_correction(message):
        previous_question = (
            context.get("last_effective_message") or context.get("last_user_message") or ""
        )
        if previous_question:
            logger.info(
                "[SELF HERO NEGATION] 자기 영웅 오해 정정('%s') — 직전 질문('%s')을 다시 답함",
                message, previous_question,
            )
            effective_message = previous_question
            resumed_previous_question = True

    llm_intent       = state.get("llm_intent")
    llm_current_hero = state.get("llm_current_hero")
    llm_hero_role    = state.get("llm_current_hero_role")
    llm_target_enemy = state.get("llm_target_enemy")
    llm_enemy_team   = state.get("llm_enemy_team")
    llm_current_hero_confirmed = state.get("llm_current_hero_confirmed", False)
    swap_guard_triggered = state.get("swap_guard_triggered", False)

    intent       = llm_intent or infer_intent_by_rule(effective_message, context)
    current_hero = llm_current_hero or infer_current_hero(effective_message, context, intent)
    if current_hero and hero_negated_as_self(current_hero, message):
        logger.info("[SELF HERO NEGATED] '%s'는 자기 영웅이 아니라고 밝혀 current_hero에서 제거함", current_hero)
        current_hero = None
        llm_hero_role = None
        llm_current_hero_confirmed = False

    # 최근에 밝힌 자기 영웅은 사용자가 자신을 가리키는 후속 질문에서 이어받는다.
    current_hero_inherited = False
    if not current_hero and not session_timed_out:
        inherited_hero = inheritable_self_hero(
            context, effective_message, message, now_ts,
            this_turn_ally_listed=len(
                state.get("llm_ally_team") or extract_ally_team(effective_message)
            ) >= 2,
            explicit_role_filter=explicit_role_filter,
        )
        if inherited_hero:
            logger.info(
                "[SELF HERO INHERITED] %d초 전에 밝힌 자기 영웅 '%s'를 이어받음: %s",
                int(now_ts - context.get("current_hero_ts", now_ts)), inherited_hero, effective_message,
            )
            current_hero = inherited_hero
            llm_hero_role = HERO_TO_ROLE.get(inherited_hero)
            current_hero_inherited = True

    enemy_mentioned_as_enemy = find_enemy_mentioned_hero(effective_message)
    if (
        current_hero
        and enemy_mentioned_as_enemy == current_hero
        and not hero_mentioned_as_current_hero(current_hero, effective_message)
    ):
        logger.info(
            "[CURRENT HERO ENEMY GUARD] '%s'는 이번 메시지에서 상대 영웅으로 "
            "언급되어 current_hero에서 제거함: %s",
            current_hero,
            effective_message,
        )
        current_hero = None
        llm_hero_role = None
        llm_current_hero_confirmed = False

    # 아래 여러 가드가 공유한다. 조합 나열 속 이름은 자기 선언으로 보지 않는다.
    current_hero_explicit_this_turn = bool(
        current_hero
        and hero_mentioned_as_current_hero(current_hero, effective_message)
        and not hero_listed_in_ally_comp(current_hero, effective_message)
    )

    # 자기 영웅을 밝힌 턴은 composition이 아니다.
    if intent == "composition" and (current_hero_explicit_this_turn or current_hero_inherited):
        corrected_intent = infer_intent_by_rule(effective_message, context)
        if corrected_intent != "composition":
            logger.info(
                "[COMPOSITION INTENT GUARD] 자기 영웅('%s')을 이미 밝혔는데 intent가 "
                "composition으로 잘못 분류되어 '%s'로 재분류함",
                current_hero, corrected_intent,
            )
            intent = corrected_intent

    # 불확실 판단의 근거는 swap_guard_triggered뿐이다.
    current_hero_uncertain = bool(
        current_hero
        and not llm_current_hero_confirmed
        and swap_guard_triggered
    )

    # 힐 수급 불만("힐" + 부정/부족 표현 근접).
    HEAL_COMPLAINT_PATTERN = re.compile(
        r"힐[^.!?\n]{0,6}(못|안|부족|없|끊)"
    )
    is_heal_complaint = bool(HEAL_COMPLAINT_PATTERN.search(effective_message))
    if llm_hero_role == "support" and is_heal_complaint:
        corrected = (
            context.get("current_hero_role")
            or (HERO_TO_ROLE.get(current_hero) if current_hero else None)
        )
        if corrected and corrected != "support":
            llm_hero_role = corrected
            logger.info(
                "[HEAL COMPLAINT FIX] '%s' 메시지에서 힐 수급 불만으로 판단, "
                "current_hero_role을 support→%s로 교정",
                effective_message, corrected,
            )

    # 영웅 표의 실제 역할이 LLM 판단보다 우선한다.
    if current_hero:
        true_role = HERO_TO_ROLE.get(current_hero)
        if true_role and true_role != llm_hero_role:
            logger.info(
                "[ROLE SAFETY] current_hero=%s의 실제 역할은 %s인데 llm_hero_role=%s로 "
                "잡혀 있어 교정함",
                current_hero, true_role, llm_hero_role,
            )
            llm_hero_role = true_role

    map_name = find_map(effective_message) or context.get("map_name") or None
    side = (
        find_side(effective_message)
        or state.get("side")
        or context.get("side")
    )

    incoming_map = state.get("map_name")
    context_was_reset = should_reset_enemy_context(effective_message, context, incoming_map, current_hero)
    if context_was_reset:
        context = {
            k: v for k, v in context.items()
            if k not in ("target_enemy", "enemy_team", "enemy_stats",
                         "high_threat_enemy", "my_stats", "my_team_stats", "has_stats",
                         "no_enemy_turn_count", "ally_team", "ally_team_ts",
                         "roster_size")
        }

    # 적 미언급 턴이 연속되면 적 정보를 비운다.
    mentioned_heroes_in_message_early = set(find_all_heroes(effective_message))
    enemy_mentioned_early = bool(
        mentioned_heroes_in_message_early & set(context.get("enemy_team", []))
        or (context.get("target_enemy") in mentioned_heroes_in_message_early)
        or extract_enemy_team(effective_message)
    )
    if not context_was_reset:
        prev_no_enemy_count = context.get("no_enemy_turn_count", 0)
        if enemy_mentioned_early:
            no_enemy_turn_count = 0
        else:
            no_enemy_turn_count = prev_no_enemy_count + 1
        STALE_ENEMY_TURN_LIMIT = 2
        if no_enemy_turn_count >= STALE_ENEMY_TURN_LIMIT and (context.get("target_enemy") or context.get("enemy_team")):
            logger.info(
                "[ENEMY CONTEXT DECAY] %d턴 연속 적 미언급 — target_enemy/enemy_team 세션에서 비움",
                no_enemy_turn_count,
            )
            # ally_team은 여기서 지우지 않는다(세션 타임아웃/새 판 감지에서만).
            context = {
                k: v for k, v in context.items()
                if k not in ("target_enemy", "enemy_team", "high_threat_enemy")
            }
            no_enemy_turn_count = 0
    else:
        no_enemy_turn_count = 0

    # 검증된 LLM 결과에 규칙 기반 추출을 합쳐 확정한다.
    mentioned_heroes_in_message = set(find_all_heroes(effective_message))
    rule_based_enemy_team = extract_enemy_team(effective_message)

    enemy_team = (
        llm_enemy_team
        or rule_based_enemy_team
        or (context.get("enemy_team", []) if not context_was_reset else [])
    )

    # 조합 제시 판정은 this_turn만, 역할 추론/답변 표시는 세션 값까지 쓴다.
    llm_ally_team = state.get("llm_ally_team")
    # 우선순위: 명시적 나열 → 시너지 질문 → 비교 질문 → 동료 불만 표현.
    effective_message_complaint_hero = find_ally_complaint_hero(effective_message)
    rule_based_ally_team = (
        extract_ally_team(effective_message)
        or find_synergy_ally_heroes(effective_message)
        or find_performance_comparison_heroes(effective_message)
        or ([effective_message_complaint_hero] if effective_message_complaint_hero else [])
    )
    ally_team_this_turn = llm_ally_team or rule_based_ally_team or []
    session_ally_team = context.get("ally_team", []) if not context_was_reset else []

    # 재답변 턴에는 세션의 아군 조합을 이번 턴 값으로 되살린다.
    if (
        resumed_previous_question
        and not ally_team_this_turn
        and len(session_ally_team) >= 2
        and context.get("ally_team_ts")
    ):
        logger.info("[RESUMED COMP] 세션 아군 조합 %s를 이번 턴 조합으로 복원", session_ally_team)
        ally_team_this_turn = list(session_ally_team)

    session_ally_ts = context.get("ally_team_ts") if not context_was_reset else None
    session_comp_fresh = bool(
        session_ally_team and session_ally_ts
        and (now_ts - session_ally_ts) <= ROLE_NARROWING_MAX_AGE_SECONDS
    )
    if (
        ally_team_this_turn
        and session_comp_fresh
        and set(ally_team_this_turn) < set(session_ally_team)
    ):
        # 조합 일부를 다시 말한 것이지 조합이 줄어든 게 아니다.
        logger.info(
            "[ALLY COMP KEPT] 이번 턴 아군 %s는 세션 조합 %s의 일부라 조합을 유지함",
            ally_team_this_turn, session_ally_team,
        )
        ally_team = list(session_ally_team)
    else:
        ally_team = ally_team_this_turn or session_ally_team

    # 조합을 마지막으로 들은 시각(오래되면 역할 좁히기에서만 제외한다).
    ally_team_ts = (
        now_ts if ally_team_this_turn
        else (context.get("ally_team_ts") if not context_was_reset else None)
    )
    ally_comp_fresh = bool(
        ally_team
        and ally_team_ts
        and (now_ts - ally_team_ts) <= ROLE_NARROWING_MAX_AGE_SECONDS
    )
    ally_comp_stale = bool(ally_team) and not ally_comp_fresh

    # 활약 비교 질문의 대상 영웅(2명 이상이 전제라 focus_heroes와 별도 필드다).
    performance_comparison_this_turn = is_performance_comparison_question(effective_message)
    # 최상급 표현이 있으면 팀 전체 대상의 새 순위 질문이라 이어받지 않는다.
    has_superlative_ranking_word = any(w in effective_message for w in ("제일", "가장"))
    if performance_comparison_this_turn and len(ally_team_this_turn) >= 2:
        compared_heroes = ally_team_this_turn
    elif (
        performance_comparison_this_turn
        and not mentioned_heroes_in_message
        and not has_superlative_ranking_word
        and not context_was_reset
    ):
        inherited_compared_heroes = context.get("compared_heroes", []) or []
        # 이번 판 로스터와 전혀 안 겹치면 판이 바뀐 것이므로 버린다.
        current_roster = {
            normalize_hero_name(h) for h in (context.get("my_team_stats") or {}).keys()
        }
        if current_roster and not any(
            normalize_hero_name(h) in current_roster for h in inherited_compared_heroes
        ):
            compared_heroes = []
        else:
            compared_heroes = inherited_compared_heroes
    else:
        compared_heroes = []

    # 아군 2명 이상 나열 = 조합 질문(자기 영웅 선언 턴과 활약 비교 질문은 제외).
    is_team_comp_question = (
        len(ally_team_this_turn) >= 2
        and not current_hero_explicit_this_turn
        and not current_hero_inherited
        and not performance_comparison_this_turn
    )
    # 아군 조합으로 사용자가 맡을 수 있는 역할을 좁힌다(되묻지 않는다).
    roster_size = declared_roster_size or (
        context.get("roster_size") if not context_was_reset else None
    )
    # 세션에는 사용자가 직접 밝힌 값만 남기고, 답변 기준은 이 값을 쓴다.
    effective_roster_size = resolve_roster_size(roster_size)
    team_comp_analysis = (
        analyze_team_comp(ally_team, effective_roster_size)
        if len(ally_team) >= 2 and ally_comp_fresh
        else None
    )
    # 정원이 찬 조합은 역할을 좁히지 않고 추천 카드 대신 조합 평가로 보낸다.
    roster_is_full = bool(team_comp_analysis and team_comp_analysis["is_full_roster"])
    team_comp_role_candidates = (
        list(team_comp_analysis["candidate_roles"])
        if team_comp_analysis and not roster_is_full
        else []
    )
    team_comp_inferred_role = (
        make_role_filter(team_comp_role_candidates) if team_comp_role_candidates else None
    )
    if team_comp_analysis and roster_is_full:
        logger.info(
            "[TEAM COMP FULL] %d인 정원이 아군 %s만으로 이미 찼음 — 사용자 자리가 없어 "
            "역할 좁히기/추천 카드 없이 조합 평가로 답함",
            team_comp_analysis["roster_size"], ally_team,
        )
    elif team_comp_analysis:
        logger.info(
            "[TEAM COMP ROLE] %d인 기준 아군 %s → 사용자 역할 후보 %s (마지막 자리=%s) "
            "→ role_filter=%s",
            team_comp_analysis["roster_size"], ally_team, team_comp_role_candidates,
            team_comp_analysis["is_last_slot"], team_comp_inferred_role,
        )
    elif ally_comp_stale:
        logger.info(
            "[TEAM COMP STALE] 아군 조합 %s를 마지막으로 들은 지 5분이 지나 역할 좁히기에는 "
            "쓰지 않고 답변 참고 자료로만 사용",
            ally_team,
        )
    # 조합을 다시 나열하지 않고 추천만 재요청한 턴(세션 조합이 유효할 때만).
    composition_reask = bool(
        not is_team_comp_question
        and not performance_comparison_this_turn
        and not mentioned_heroes_in_message
        and len(ally_team) >= 2
        and ally_comp_fresh
        and is_composition_reask(effective_message)
    )
    # 구조적 신호가 LLM의 intent 분류보다 우선한다.
    if is_team_comp_question or composition_reask:
        if composition_reask:
            logger.info("[COMPOSITION REASK] 세션 아군 조합 %s 기준 composition으로 처리", ally_team)
        intent = "composition"
    elif performance_comparison_this_turn and len(ally_team_this_turn) >= 2 and intent == "composition":
        # LLM이 비교 질문을 composition으로 분류해온 경우의 안전장치.
        intent = "performance_improve"

    # 특전 질문의 "추천"은 영웅 추천 요청이 아니라 개선 질문이다.
    perk_question = is_perk_question(effective_message)
    if perk_question and intent not in ("off_topic", "performance_improve"):
        logger.info(
            "[PERK QUESTION] '%s'는 특전 질문이라 intent %s → performance_improve로 교정 "
            "(영웅 추천 질문이 아님)",
            effective_message, intent,
        )
        intent = "performance_improve"

    # 우선 타겟 질문은 교체가 아니라 지금 영웅으로 상대하는 법을 묻는 질문이다.
    target_priority_question = bool(enemy_team) and is_target_priority_question(effective_message)
    if target_priority_question and intent not in ("off_topic", "stay"):
        logger.info("[TARGET PRIORITY] intent %s → stay로 교정 (상대 조합: %s)", intent, enemy_team)
        intent = "stay"

    context_for_enemy = {**context, "current_hero": current_hero, "ally_team_this_turn": ally_team_this_turn}
    rule_based_target_enemy = infer_target_enemy(effective_message, context_for_enemy, intent)
    target_enemy = llm_target_enemy or rule_based_target_enemy
    ally_set = {normalize_hero_name(h) for h in ally_team}
    enemy_set = {normalize_hero_name(h) for h in enemy_team}
    if target_enemy and normalize_hero_name(target_enemy) in ally_set - enemy_set:
        logger.info("[TARGET ENEMY IS ALLY] '%s'는 아군 조합 %s에 있어 target_enemy에서 제거함", target_enemy, ally_team)
        target_enemy = None
        llm_target_enemy = None
        rule_based_target_enemy = None

    # 자기 영웅과 같거나 자기 선언 패턴에 걸리면 상대에서 버린다.
    if target_enemy and (
        normalize_hero_name(target_enemy) == current_hero
        or hero_mentioned_as_current_hero(target_enemy, effective_message)
    ):
        logger.info(
            "[TARGET ENEMY SELF CONTRADICTION] '%s'는 이번 메시지에서 사용자 자신의 "
            "후보 영웅으로 언급되어 target_enemy에서 제거함: %s",
            target_enemy,
            effective_message,
        )
        target_enemy = None
        llm_target_enemy = None
        rule_based_target_enemy = None

    # 상대를 하나로 좁혔는지(되묻기 문구를 그 하나로 맞춘다).
    enemy_focus_hero = find_enemy_mentioned_hero(effective_message)
    target_enemy_narrowed = bool(
        enemy_focus_hero
        and target_enemy
        and normalize_hero_name(enemy_focus_hero) == normalize_hero_name(target_enemy)
    )

    # 역할로만 좁힌 경우. 그 역할 외 상대는 언급하지 않는다.
    enemy_role_focus = find_enemy_role_focus(effective_message)

    # 세션에서 이어받은 값은 이번 메시지에 없으면 "언급 아님"이다.
    enemy_named_this_turn = bool(
        llm_target_enemy
        or llm_enemy_team
        or rule_based_enemy_team
        or (rule_based_target_enemy and rule_based_target_enemy in mentioned_heroes_in_message)
    )

    high_threat_enemy = state.get("high_threat_enemy") or context.get("high_threat_enemy")
    if state.get("high_threat_enemy"):
        # 이번 턴 스탯에서 나온 위협 영웅은 언급된 것으로 본다.
        enemy_named_this_turn = True
    if high_threat_enemy and not target_enemy and intent in ["counter", "general", "map_strategy", "performance_improve"]:
        target_enemy = high_threat_enemy
        if intent == "general":
            intent = "counter"

    previous_target_enemy_for_role = normalize_hero_name(context.get("target_enemy"))
    # 아래 가드가 current_hero를 비우면 세션에도 반영해야 한다.
    current_hero_cleared_this_turn = False
    if explicit_role_filter and current_hero and not current_hero_explicit_this_turn:
        logger.info(
            "[EXPLICIT ROLE FILTER CLEARS STALE HERO] 사용자가 이번 턴에 역할 필터 '%s'를 "
            "명시했지만 현재 영웅 '%s'는 직접 언급하지 않아 이전 영웅 컨텍스트를 버림",
            explicit_role_filter,
            current_hero,
        )
        current_hero = None
        llm_hero_role = None
        current_hero_uncertain = False
        current_hero_explicit_this_turn = False
        current_hero_cleared_this_turn = True

    new_enemy_without_current_role = bool(
        target_enemy
        and enemy_named_this_turn
        and target_enemy != previous_target_enemy_for_role
        and current_hero
        and not current_hero_explicit_this_turn
        and not explicit_role_filter
    )
    if new_enemy_without_current_role:
        logger.info(
            "[CURRENT HERO STALE ON NEW ENEMY] 이번 턴에 새 상대 '%s'가 언급됐지만 "
            "사용자 영웅/역할은 직접 언급되지 않아 이전 current_hero='%s'를 버림",
            target_enemy,
            current_hero,
        )
        current_hero = None
        llm_hero_role = None
        current_hero_uncertain = False
        current_hero_cleared_this_turn = True

    # 조합 나열 문장에서 잡힌 영웅은 자기 선언이 아니다.
    if is_team_comp_question and current_hero and not current_hero_explicit_this_turn:
        logger.info(
            "[TEAM COMP QUESTION CLEARS UNCONFIRMED HERO] 아군 조합을 나열한 질문에서 "
            "current_hero='%s'가 자기 선언이 아니라 나열된 영웅 중 하나로 잘못 잡혀 비움",
            current_hero,
        )
        current_hero = None
        llm_hero_role = None
        current_hero_uncertain = False
        current_hero_cleared_this_turn = True

    current_hero_role = llm_hero_role or get_hero_role(current_hero)

    # 우선순위: 이번 턴 명시 필터 > current_hero_role > 조합 추론 > 세션 잔존값.
    if explicit_role_filter:
        effective_role_filter = explicit_role_filter
    elif current_hero_role:
        effective_role_filter = current_hero_role
    elif team_comp_inferred_role:
        effective_role_filter = team_comp_inferred_role
    else:
        effective_role_filter = role_filter

    # --- 되묻는 대신 답변에 붙이는 것들(판단 근거 한 줄 + 정정 버튼) ---
    role_basis_note = ""
    answer_choice_buttons: List[Dict[str, str]] = []
    role_narrowing_active = bool(
        team_comp_role_candidates and not explicit_role_filter and not current_hero_role
    )
    if role_narrowing_active:
        candidate_set = set(team_comp_role_candidates)
        ordered_candidates = [role for role in ROLE_HEROES if role in candidate_set]
        if len(ordered_candidates) < len(ROLE_HEROES):
            excluded_label = "/".join(
                ROLE_LABELS[role] for role in ROLE_HEROES if role not in candidate_set
            )
            candidate_label = "/".join(ROLE_LABELS[role] for role in ordered_candidates)
            role_basis_note = (
                f"{excluded_label}는 이미 명시되어 {candidate_label} 기준으로 추천드립니다."
            )
        # 역할 버튼은 후보가 2개 이상일 때만, 후보 역할만 보여준다.
        if len(ordered_candidates) >= 2:
            answer_choice_buttons.extend(
                {"label": ROLE_LABELS[role], "value": role, "type": "role_filter"}
                for role in ordered_candidates
            )
    elif ally_comp_stale and not explicit_role_filter and not current_hero_role:
        role_basis_note = "이전에 말씀하신 아군 조합 기준으로 답변드립니다."

    # 인원수 정정 버튼. 조합을 기준으로 답한 턴이면 역할 좁히기 여부와 무관하게
    # 반대쪽 규격 라벨로 붙이고, 그 규격으로 성립할 수 없는 조합이면 숨긴다.
    if team_comp_analysis:
        other_roster_size = alternate_roster_size(effective_roster_size)
        if can_be_roster_size(ally_team, other_roster_size):
            answer_choice_buttons.append({
                "label": roster_size_button_label(other_roster_size),
                "value": str(other_roster_size),
                "type": "roster_size",
            })

    if intent in ["counter", "stay", "performance_improve"] and not target_enemy:
        previous_target_enemy = context.get("target_enemy")

        # 유지 의사를 밝힌 경우 직전 상대를 이어받는다(아군이 된 영웅은 제외).
        if previous_target_enemy and normalize_hero_name(previous_target_enemy) not in ally_set - enemy_set:
            target_enemy = previous_target_enemy

            if intent == "counter":
                enemy_named_this_turn = True
            elif intent == "stay":
                # 이름을 다시 말하지 않아도 직전 상대 기준으로 운영법을 설명한다.
                enemy_named_this_turn = True

    # focus_heroes: 이번 질문이 다루는 주제 영웅(자기 영웅이 아니어도 된다).
    needs_focus_hero_clarify = False
    previous_focus_heroes = context.get("focus_heroes") or []
    if focus_hero_reply_consumed:
        # 되묻기에 사용자가 고른 영웅.
        focus_heroes = [focus_hero_pick]
    elif current_hero:
        # 자기 영웅을 선언했으면 그 영웅만 담는다.
        focus_heroes = [current_hero]
    else:
        # 아군으로 분류된 영웅은 설명 대상이 아니다.
        focus_heroes = [h for h in find_all_heroes(effective_message) if h not in ally_team]
        if not focus_heroes and is_ellipsis_followup(effective_message):
            if len(previous_focus_heroes) == 1:
                focus_heroes = list(previous_focus_heroes)
            else:
                # 0명이거나 2명 이상이면 되묻는다.
                needs_focus_hero_clarify = True

    game_state = {
        "raw_user_message": message,
        "effective_message": effective_message,
        "previous_context": context,
        "target_enemy": target_enemy,
        "current_hero": current_hero,
        "current_hero_role": current_hero_role,
        "map_name": map_name,
        "side": side,
        "enemy_team": enemy_team,
        "role_filter": effective_role_filter,
        "role_filter_explicit": bool(explicit_role_filter),
        "enemy_stats": state.get("enemy_stats") or context.get("enemy_stats"),
        "my_stats": state.get("my_stats") or context.get("my_stats"),
        "my_team_stats": state.get("my_team_stats") or context.get("my_team_stats"),
        "high_threat_enemy": high_threat_enemy,
        "has_stats": state.get("has_stats", False) or bool(
            context.get("my_stats") or context.get("enemy_stats") or context.get("my_team_stats")
        ),
        "enemy_named_this_turn": enemy_named_this_turn,
        "current_hero_uncertain": current_hero_uncertain,
    }

    context_patch: Dict[str, Any] = {
        "last_user_message": message,
        "last_effective_message": effective_message,
        "last_intent": intent,
        "role_filter": role_filter,
        "no_enemy_turn_count": no_enemy_turn_count,
        "last_message_ts": now_ts,
        "answer_style": answer_style,
    }
    if awaiting_role_filter_reply or awaiting_focus_hero_reply:
        context_patch["pending_question"] = None
        context_patch["pending_intent"] = None

    if target_enemy:
        context_patch["target_enemy"] = target_enemy
    elif normalize_hero_name(context.get("target_enemy")) in ally_set - enemy_set:
        # 아군으로 밝혀진 옛 상대는 세션에서도 지운다.
        context_patch["target_enemy"] = None
    if current_hero:
        context_patch["current_hero"] = current_hero
        context_patch["current_hero_ts"] = now_ts
    elif current_hero_cleared_this_turn:
        # 세션 값도 함께 비운다.
        context_patch["current_hero"] = None
        context_patch["current_hero_ts"] = None
    if current_hero_role:
        context_patch["current_hero_role"] = current_hero_role
    elif current_hero_cleared_this_turn:
        context_patch["current_hero_role"] = None
    if map_name:
        context_patch["map_name"] = map_name
    if side:
        context_patch["side"] = side
    if enemy_team:
        context_patch["enemy_team"] = enemy_team
    if ally_team:
        context_patch["ally_team"] = ally_team
    if ally_team_ts:
        context_patch["ally_team_ts"] = ally_team_ts
    if roster_size:
        context_patch["roster_size"] = roster_size
    if compared_heroes:
        context_patch["compared_heroes"] = compared_heroes
    elif performance_comparison_this_turn:
        # 비교 질문인데 대상이 없으면 옛 값을 비운다.
        context_patch["compared_heroes"] = []
    if high_threat_enemy:
        context_patch["high_threat_enemy"] = high_threat_enemy
    my_stats_now = state.get("my_stats") or {}
    if my_stats_now:
        context_patch["my_stats"] = my_stats_now
    enemy_stats_now = state.get("enemy_stats") or {}
    if enemy_stats_now:
        context_patch["enemy_stats"] = enemy_stats_now
    my_team_stats_now = state.get("my_team_stats") or {}
    if my_team_stats_now:
        context_patch["my_team_stats"] = my_team_stats_now

    logger.info("[CONTEXT MERGE] context=%s", context)
    logger.info("[CONTEXT PATCH] %s", context_patch)
    logger.info("[ENEMY NAMED THIS TURN] %s (target_enemy=%s, enemy_team=%s)", enemy_named_this_turn, target_enemy, enemy_team)
    if current_hero_uncertain:
        logger.info(
            "[CURRENT HERO UNCERTAIN] current_hero='%s'가 이번 메시지에 등장하지 않아 "
            "여전히 그 영웅을 플레이 중인지 불확실함",
            current_hero,
        )

    # 상성 카드는 순수 counter 질문에만 쓴다.
    matchup_subject: Optional[str] = None
    matchup_subject_is_enemy = False
    if (
        intent == "counter"
        and enemy_named_this_turn
        and target_enemy
        and not current_hero
    ):
        # 아직 영웅이 없는 픽 추천 질문만 상대를 카드 대상으로 삼는다.
        matchup_subject = target_enemy
        matchup_subject_is_enemy = True

    # 추천 영웅 카드 모드(swap과 composition이 같은 카드 형식을 공유한다).
    # composition은 추천 요청 표현이 있을 때만 카드고, 정원이 찬 조합과 특전
    # 질문은 추천 요청 표현이 있어도 카드를 만들지 않는다.
    recommend_card_mode: Optional[str] = None
    if (
        (is_team_comp_question or composition_reask)
        and wants_composition_recommendation(effective_message)
        and not roster_is_full
        and not perk_question
    ):
        recommend_card_mode = "composition"
    elif intent == "swap" and current_hero and not current_hero_uncertain:
        # 이미 쓰는 영웅이 있으면 같은 역할 안에서 대안을 추천한다.
        recommend_card_mode = "swap"

    return {
        "message": effective_message,
        "intent": intent,
        "target_enemy": target_enemy,
        "current_hero": current_hero,
        "current_hero_role": current_hero_role,
        "map_name": map_name,
        "side": side,
        "enemy_team": enemy_team,
        "role_filter": effective_role_filter,
        "role_filter_explicit": bool(explicit_role_filter),
        "game_state": game_state,
        # 뒤 노드들이 최상위 키를 직접 읽으므로 함께 꺼내준다.
        "has_stats": game_state["has_stats"],
        "my_stats": game_state["my_stats"],
        "my_team_stats": game_state["my_team_stats"],
        "enemy_stats": game_state["enemy_stats"],
        "high_threat_enemy": game_state["high_threat_enemy"],
        "context_patch": context_patch,
        "enemy_named_this_turn": enemy_named_this_turn,
        "target_enemy_narrowed": target_enemy_narrowed,
        "enemy_role_focus": enemy_role_focus,
        "current_hero_uncertain": current_hero_uncertain,
        "answer_style": answer_style,
        "matchup_subject": matchup_subject,
        "matchup_subject_is_enemy": matchup_subject_is_enemy,
        "recommend_card_mode": recommend_card_mode,
        "is_perk_question": perk_question,
        "is_target_priority_question": target_priority_question,
        "target_priority": rank_opponents(current_hero, enemy_team) if target_priority_question else [],
        "ally_team": ally_team,
        "compared_heroes": compared_heroes,
        "is_team_comp_question": is_team_comp_question,
        # 역할 후보와 그 조합이 최근 것인지. fresh면 되묻지 않는다.
        "role_candidates": team_comp_role_candidates,
        "role_candidates_fresh": bool(team_comp_role_candidates),
        # roster_size는 사용자가 직접 밝힌 값, effective는 이번 답변에 적용한 값.
        "roster_size": roster_size,
        "roster_size_effective": effective_roster_size,
        "roster_is_full": roster_is_full,
        # 판단 근거 한 줄과 정정 버튼.
        "role_basis_note": role_basis_note,
        "choice_buttons": answer_choice_buttons,
        "focus_heroes": focus_heroes,
        "needs_focus_hero_clarify": needs_focus_hero_clarify,
        "previous_focus_heroes": previous_focus_heroes,
        # 짧은 후속 질문의 배경으로 쓰는 직전 턴 메시지.
        "previous_user_message": context.get("last_user_message") or context.get("last_effective_message"),
    }


def build_role_choice_buttons(role_candidates: List[str]) -> List[Dict[str, str]]:
    """역할 되묻기 버튼 목록을 만든다.

    후보가 좁혀졌으면 그 후보만(2개면 "둘 다 보기"를 더해서), 아니면
    전체/탱커/딜러/힐러 4개.
    """
    candidates = [role for role in ROLE_HEROES if role in set(role_candidates or [])]

    if not candidates or len(candidates) >= len(ROLE_HEROES):
        return [
            {"label": "전체", "value": "all", "type": "role_filter"},
            {"label": "탱커", "value": "tank", "type": "role_filter"},
            {"label": "딜러", "value": "damage", "type": "role_filter"},
            {"label": "힐러", "value": "support", "type": "role_filter"},
        ]

    buttons = [
        {"label": ROLE_LABELS[role], "value": role, "type": "role_filter"}
        for role in candidates
    ]
    if len(candidates) == 2:
        combined = make_role_filter(candidates)
        buttons.append(
            {
                "label": role_filter_label(combined),
                "value": combined,
                "type": "role_filter",
            }
        )
    return buttons


def clarify_role_filter_node(state: ChatbotGraphState) -> ChatbotGraphState:
    target_enemy = state.get("target_enemy")
    message = state.get("message", "")
    role_candidates = state.get("role_candidates") or []
    role_candidates_narrowed = 0 < len(role_candidates) < len(ROLE_HEROES)

    # 표시 우선순위: 역할로만 좁힘 > 하나로 좁힘 > 상대 조합 전체.
    enemy_team = state.get("enemy_team") or []
    target_enemy_narrowed = state.get("target_enemy_narrowed", False)
    enemy_role_focus = state.get("enemy_role_focus")
    if enemy_role_focus:
        enemy_names = [ENEMY_ROLE_FOCUS_LABELS[enemy_role_focus]]
    elif target_enemy_narrowed:
        enemy_names = [target_enemy] if target_enemy else []
    else:
        enemy_names = enemy_team if enemy_team else ([target_enemy] if target_enemy else [])

    if enemy_names:
        enemy_label = ", ".join(enemy_names)
        if state.get("intent") == "counter":
            answer = (
                f"{enemy_label}{josa_eul_reul(enemy_names[-1])} 카운터하는 영웅을 어떤 역할 기준으로 볼까요?\n\n"
                "원하는 역할을 선택하면 그 역할의 영웅만 골라서 추천해드릴게요."
            )
        else:
            # counter가 아니면 상대 조합이 있어도 카운터 질문으로 보지 않는다.
            answer = (
                "지금 어떤 역할(탱커/딜러/힐러)로 플레이 중이신가요?\n\n"
                f"상대 조합({enemy_label})을 고려해서 답변드릴게요."
            )
    else:
        # 상대를 특정하지 않은 대처법 질문도 역할부터 묻는다.
        answer = (
            "지금 어떤 역할(탱커/딜러/힐러)로 플레이 중이신가요?\n\n"
            "역할을 알려주시면 그 역할 기준으로 상황에 맞게 답변드릴게요."
        )

    # 질문 문구도 버튼과 같은 후보만 언급한다.
    if role_candidates_narrowed:
        candidate_label = " 또는 ".join(
            ROLE_LABELS[role] for role in ROLE_HEROES if role in set(role_candidates)
        )
        answer = (
            f"말씀하신 아군 조합이면 남은 자리는 {candidate_label}예요. "
            "어떤 역할로 플레이하실 건가요?\n\n"
            "아직 정하지 않았다면 둘 다 고려한 추천도 받아보실 수 있어요."
        )

    choice_buttons = build_role_choice_buttons(role_candidates)

    context_patch = {
        **state.get("context_patch", {}),
        "pending_question": message,
        "pending_intent": "counter",
    }
    if target_enemy:
        context_patch["target_enemy"] = target_enemy

    return {
        "answer": answer,
        "choice_buttons": choice_buttons,
        # 되묻는 답변에는 판단 근거 문구를 붙이지 않는다.
        "role_basis_note": "",
        "context_patch": context_patch,
        "result": {
            "answer": answer,
            "type": "role_filter",
            "choice_buttons": choice_buttons,
            "suggested_questions": [],
            "context_patch": context_patch,
        },
    }


def clarify_focus_hero_node(state: ChatbotGraphState) -> ChatbotGraphState:
    """생략형 후속 질문의 주제 영웅이 확정되지 않을 때 어떤 영웅인지 되묻는다."""
    message = state.get("message", "")
    candidates = state.get("previous_focus_heroes") or []

    if len(candidates) >= 2:
        candidate_label = ", ".join(candidates)
        answer = (
            f"{candidate_label} 중 어떤 영웅 기준으로 답변드릴까요?\n\n"
            "원하는 영웅을 선택해주세요."
        )
        choice_buttons = [
            {"label": hero, "value": hero, "type": "focus_hero"} for hero in candidates
        ]
    else:
        answer = "어떤 영웅에 대해 궁금하신가요? 영웅 이름을 알려주시면 그 기준으로 답변드릴게요."
        choice_buttons = []

    context_patch = {
        **state.get("context_patch", {}),
        "pending_question": message,
        "pending_intent": "clarify_focus_hero",
    }

    return {
        "answer": answer,
        "choice_buttons": choice_buttons,
        "role_basis_note": "",
        "context_patch": context_patch,
        "result": {
            "answer": answer,
            "type": "focus_hero",
            "choice_buttons": choice_buttons,
            "suggested_questions": [],
            "context_patch": context_patch,
        },
    }


"""그래프 끝단 노드 — 실제 사용자에게 나가는 답변/카드를 만든다.

세 가지 출력 형식이 여기서 갈린다:
- generate_matchup_answer_node : 상성 카드(counter)
- generate_recommend_card_node : 추천 영웅 카드(swap, 추천을 요청한 composition)
- generate_answer_node         : 그 외 전부(일반 텍스트 답변)

generate_answer_node는 답변을 만든 뒤 "역할 고정" 검사도 수행한다 — 사용자
역할이 확정됐는데 답변이 다른 역할 영웅을 추천하면 그 이름을 "다른 영웅"으로
치환한다(실제 매치에 있던 팀원/상대 이름은 예외).
"""

import logging
import re
from typing import Any, Dict, List, Optional

from chat.domain.answer_format import (
    _format_stat_text,
    extract_inline_suggested_questions,
    format_perk_answer,
    sanitize_answer_for_user,
)
from chat.rag import components as chatbot_service
from chat.graph.state import ChatbotGraphState
from chat.domain.heroes import (
    HERO_TO_ROLE,
    HEROES,
    ROLE_HEROES,
    ROLE_LABELS,
    find_all_heroes,
    get_skill_shortcut_text,
    heroes_for_role_filter,
    normalize_hero_name,
    parse_role_filter,
    role_filter_label,
)
from chat.domain.intent_rules import (
    is_performance_comparison_question,
    resolve_roster_size,
    roster_role_quota_text,
    roster_size_label,
)
from chat.rag.llm_utils import call_llm_text, call_llm_text_creative, safe_json_loads
from chat.domain.prompts import (
    SUGGESTED_QUESTIONS_INLINE_RULES,
    SUGGESTED_QUESTIONS_INLINE_SCHEMA_LINE,
    SUPPORT_DAMAGE_CONTRIBUTION_RULE,
    stat_judgement_rules,
)

logger = logging.getLogger(__name__)


def generate_answer_node(state: ChatbotGraphState) -> ChatbotGraphState:
    if state.get("error"):
        return state

    try:
        _chatbot, _retriever, llm = chatbot_service.get_chatbot_components()

        skill_shortcut_text = get_skill_shortcut_text()
        role_filter = state.get("role_filter") or "all"
        role_filter_explicit = state.get("role_filter_explicit", False)
        current_hero = state.get("current_hero")
        current_hero_role = state.get("current_hero_role")
        has_stats = state.get("has_stats", False)
        compared_heroes = state.get("compared_heroes") or []
        # 특정 2명 비교와 팀 전체 순위 질문 둘 다 판단을 원하는 질문이라
        # 운영 팁("바로 적용할 것 3가지")을 만들지 않는다.
        is_hero_comparison_question = len(compared_heroes) >= 2 or is_performance_comparison_question(
            state.get("message") or ""
        )
        # 간단히/자세히 토글.
        answer_style = state.get("answer_style") or "detailed"
        is_simple_style = answer_style == "simple"
        # "간단히"는 별도 LLM 호출 없이 이번 호출에서 추천 질문까지 함께 받는다.
        suggested_questions_schema_line = SUGGESTED_QUESTIONS_INLINE_SCHEMA_LINE if is_simple_style else ""
        suggested_questions_rules = SUGGESTED_QUESTIONS_INLINE_RULES if is_simple_style else ""
        # 이번 턴에 적이 실제로 언급되지 않았다면 답변에서도 "확정된 상대"로 다루지 않는다.
        enemy_named_this_turn = state.get("enemy_named_this_turn", False)
        # 이번 메시지에서 확인되지 않은 current_hero. 이 상태로 역할을 제한하면
        # 옛 영웅 기준으로 답이 좁혀진다.
        current_hero_uncertain = state.get("current_hero_uncertain", False)

        # 명시 역할 필터가 최우선이다(덮어쓰면 버튼 선택과 다른 역할이 나간다).
        if (
            not role_filter_explicit
            and current_hero_role
            and current_hero_role in ROLE_HEROES
            and role_filter != current_hero_role
        ):
            if role_filter in ROLE_HEROES:
                logger.info(
                    "[ROLE FILTER OVERRIDE] role_filter='%s'가 current_hero_role='%s'와 달라 "
                    "current_hero_role을 우선함 (current_hero=%s)",
                    role_filter, current_hero_role, current_hero,
                )
            role_filter = current_hero_role

        if current_hero_uncertain:
            allowed_heroes_text = (
                "사용자가 지금 어떤 영웅을 플레이 중인지 이번 메시지만으로는 확실하지 않다. "
                "이전 대화에서 다른 영웅 얘기가 있었더라도, 이번 질문은 그 영웅과 무관한 "
                "일반적인 팀 조합/전략 질문일 수 있다. 특정 영웅을 계속 플레이 중이라고 "
                "단정해서 '현재의 OO보다는' 같은 식으로 말하지 말고, 역할 제한 없이 "
                "상황에 맞는 영웅이나 조합을 자유롭게 제안해라."
            )
            answer_allowed_hero_set: Optional[set] = None
        elif parse_role_filter(role_filter):
            # 복합 필터면 두 역할 영웅이 함께 허용된다.
            filter_roles = parse_role_filter(role_filter)
            allowed_heroes_text = (
                f"영웅 교체 추천은 반드시 {role_filter_label(role_filter)} 역할만:\n"
                f"{', '.join(heroes_for_role_filter(role_filter))}"
            )
            if len(filter_roles) > 1:
                allowed_heroes_text += (
                    f"\n사용자는 아직 {role_filter_label(role_filter)} 중 무엇을 할지 정하지 않았다. "
                    f"두 역할 영웅을 골고루 섞어서 추천하고, 각 추천이 어느 역할인지 밝혀라."
                )
            answer_allowed_hero_set = set(heroes_for_role_filter(role_filter))
        elif current_hero_role and current_hero_role in ROLE_HEROES:
            allowed_heroes_text = (
                f"사용자는 현재 {ROLE_LABELS.get(current_hero_role)}({current_hero})를 플레이 중이다.\n"
                f"영웅 교체를 추천할 때는 반드시 같은 {ROLE_LABELS.get(current_hero_role)} 역할 영웅만 추천해라:\n"
                f"{', '.join(ROLE_HEROES[current_hero_role])}\n"
                f"힐러 교체, 탱커 교체 등 다른 역할 영웅 추천은 절대 하지 마라."
            )
            answer_allowed_hero_set = set(ROLE_HEROES[current_hero_role])
        elif role_filter == "all" and (
            role_filter_explicit
            or len(state.get("role_candidates") or []) >= len(ROLE_HEROES)
        ):
            # "전체"를 직접 골랐거나 후보가 세 역할 전부일 때만 여기 온다.
            # 정보가 없어 기본값이 "all"인 경우까지 걸리면 영웅 추천이 필요 없는
            # 질문에도 역할별 추천이 붙는다.
            allowed_heroes_text = (
                "사용자의 역할이 아직 하나로 정해지지 않았다. 특정 역할로 제한하지 말고, "
                "탱커/딜러/힐러 각 역할에서 이 상황에 대응할 수 있는 영웅을 "
                "역할당 1~2명씩 골고루 골라 역할별로 균형 있게 제안해라. "
                "한 역할에만 치우친 추천은 하지 마라."
            )
            answer_allowed_hero_set = None
        else:
            allowed_heroes_text = "역할 제한 없음. 상황에 맞는 영웅을 자유롭게 추천해도 된다."
            answer_allowed_hero_set = None

        enemy_stats = state.get("enemy_stats") or {}
        my_stats = state.get("my_stats") or {}
        my_team_stats = state.get("my_team_stats") or {}

        # 이번 턴 조합이 점수판 로스터의 부분집합이 아니면 그 판의 조합이 아니므로
        # 스탯 컨텍스트를 뺀다. 남겨두면 답변이 점수판 영웅을 그 조합의 일부처럼
        # 섞어서 설명한다.
        composition_ally_this_turn = {normalize_hero_name(h) for h in (state.get("ally_team") or [])}
        match_roster = {normalize_hero_name(h) for h in my_team_stats.keys()}
        composition_unrelated_to_match = bool(
            state.get("intent") == "composition"
            and not state.get("recommend_card_mode")
            and my_team_stats
            and composition_ally_this_turn
            and not composition_ally_this_turn.issubset(match_roster)
        )
        if composition_unrelated_to_match:
            enemy_stats = {}
            my_stats = {}
            my_team_stats = {}

        enemy_stat_text = _format_stat_text(enemy_stats, "상대팀")
        my_stat_text = _format_stat_text(my_stats, "나")
        team_stat_text = _format_stat_text(my_team_stats, "우리팀")
        stat_summary = "\n".join(filter(None, [enemy_stat_text, my_stat_text, team_stat_text]))

        stat_analysis_instruction = ""
        if has_stats and not composition_unrelated_to_match:
            # 가운데 판단 기준은 스탯창 카드 피드백과 같아야 하므로 공용 규칙
            # (chat/domain/prompts.py)을 끼워 넣는다.
            stat_analysis_instruction = """
스탯 분석 지시:
- 사용자가 입력한 스탯을 바탕으로 현재 상황을 구체적으로 짚어줘라.
- 내 스탯이 있으면: 딜량/킬/데스 수치를 언급하며 잘한 점과 개선할 점을 말해라.
- 상대 스탯이 있으면: 딜량/킬이 높은 상대를 먼저 언급하고 어떻게 대처할지 설명해라.
- 수치가 낮은 항목(예: 딜량 낮음, 데스 많음)의 원인과 해결책을 알려줘라.
""" + stat_judgement_rules() + "\n" + SUPPORT_DAMAGE_CONTRIBUTION_RULE + """
- 사용자가 팀원 중 누가 잘했는지/못했는지 순위를 묻는다면 전략 조언으로
  화제를 돌리며 회피하지 말고, 위에 주어진 실제 스탯을 근거로 직접 답해라.
  순위만 나열하고 끝내지 말고, 각 순위마다 왜 그 순서인지 근거가 되는
  구체적 수치(킬/데스/딜량/힐량/경감량 등)를 함께 짚어라. 이때도 위 스킬/
  역할 판단 기준은 그대로 적용해라. 순위를 나열할 때는 "1위 ○○는 ...",
  "2위 ○○는 ..."처럼 순위마다 줄을 바꿔 한 문단씩 써라 — 여러 순위를
  한 문단에 이어 붙이지 마라.
- 순위를 매길 때 킬/데스/도움 숫자만으로 판단하지 마라. 딜량/힐량/경감량도
  반드시 함께 비교해서 실제 기여도를 판단해라. 킬 수가 가장 많다고 자동으로
  최상위가 아니다 — 같은 역할군의 다른 딜러(아군이든 상대든)와 딜량을
  비교해서, 킬은 많아도 딜량 자체는 다른 딜러와 비슷하거나 낮다면 화력
  기여를 과대평가하지 마라. 반대로 킬 수는 비슷해도 딜량이나 힐량이 더
  높은 쪽이 있다면 그 기여를 킬 수만으로 저평가하지 마라. 힐러의 딜량을
  "힐러 항목이니까" 순위 판단에서 제외하지 마라 — 메르시가 아닌 힐러가
  탱커/딜러급 딜량을 힐량 저하 없이 냈다면, 역할이 다르더라도 그 딜량을
  탱커/딜러의 딜량과 직접 맞대어 비교해 순위에 반영해라.
- 사용자가 팀 전체 순위나 팀원 전체 평가를 물었다면(본인 스탯 개선을
  물은 게 아니라면), 답변 전체가 그 팀 전체 순위/평가만 다뤄야 한다.
  "현재 [본인 영웅]의 스탯은 훌륭하며 ~하는 것이 좋습니다" 같은 본인 개인
  운영 문단을 별도로 추가하지 마라 — 사용자가 묻지 않았다. 순위 질문에는
  순위와 그 근거만 답하고 운영 팁이나 본인 얘기로 끝맺지 마라.
"""
            if len(compared_heroes) >= 2:
                # 특정 2명 비교는 "팀 전체 순위" 지시와 범위가 다르다.
                compared_list = ", ".join(compared_heroes)
                compared_roles = {
                    h: HERO_TO_ROLE.get(normalize_hero_name(h)) for h in compared_heroes
                }
                role_values = list(compared_roles.values())
                same_role = len(set(role_values)) == 1 and all(role_values)
                if same_role:
                    comparison_method = (
                        f"{compared_list}는 같은 역할이니 서로의 실제 스탯을 직접 비교해라."
                    )
                else:
                    # 역할이 다르면 스탯 종류 자체가 달라 결론을 회피하는 답이
                    # 나온다. 비교 방법을 구체적인 절차로 못박는다.
                    comparison_method = (
                        f"{compared_list}는 역할이 달라 곧바로 비교할 수 없다는 이유로 "
                        "회피하지 마라. 절차: 1) 각 영웅을 상대팀에서 같은 역할인 "
                        "영웅과 먼저 비교해라(예: 딜러는 상대 딜러와 딜량/킬을, 힐러는 "
                        "상대 힐러와 힐량/도움을 — 단, 힐러가 메르시가 아니고 딜량도 "
                        "탱커/딜러에 준할 만큼 높다면 그 딜량도 반드시 함께 비교 근거에 "
                        "포함해라, 힐량/도움만 보고 딜량을 빼면 안 된다) — 상대와 비교해 "
                        "더 앞섰는지 판단해라. "
                        "2) 아군 중 같은 역할이 더 있다면 그 아군과도 비교해라. 비교 대상의 "
                        "역할이 서로 다르면(예: 탱커 vs 힐러), 딜량처럼 두 역할 모두에 있는 "
                        "공통 지표는 역할에 상관없이 직접 맞대어 비교해라. "
                        f"3) 두 비교 결과를 종합해 {compared_list} 중 누가 더 낫다고 "
                        "볼 수 있는지 결론을 내려라."
                    )
                stat_analysis_instruction += f"""
- 지금 사용자가 실제로 비교해달라고 한 대상은 정확히 {compared_list}뿐이다.
  위 순위/평가 지시보다 이 지시를 우선해라 — 팀 전체 순위를 매기거나 다른
  팀원, 상대팀 얘기로 범위를 넓히지 말고 "누가 더 낫다"는 명확한 결론을
  내려라. {comparison_method} 반드시 한쪽의 손을 들어주는 결론으로 답을
  끝내라 — "우열을 가리기 어렵다", "각자 역할을 충분히 수행했다", "판단
  하기보다는 ~가 중요하다"처럼 결론을 회피하는 문장으로 답을 마무리하지
  마라. 답변 전체에서 상대팀의 위협 요소(비교 근거로 쓴 상대 동일 역할
  영웅 제외)나 사용자 본인 영웅(current_hero)의 스킬 활용법 얘기를 아예
  꺼내지 마라 — {compared_list} 둘 다 사용자 본인이 아니라면 사용자 본인
  얘기는 답변에 전혀 나오면 안 된다. 운영 팁이나 "바로 적용할 것 3가지"
  없이 비교 결론과 그 근거만 답해라 — 사용자는 운영법이 아니라 판단을
  원했다.
"""

        # 이번 턴에 언급되지 않은 상대 정보는 답변 프롬프트에서도 "없음"으로 표시한다.
        display_target_enemy = state.get("target_enemy") if enemy_named_this_turn else None
        display_high_threat = state.get("high_threat_enemy") if enemy_named_this_turn else None
        display_enemy_team = state.get("enemy_team", []) if enemy_named_this_turn else []
        # 아군 조합은 카드 경로가 아닌 운영법 질문에도 나올 수 있어 함께 표시한다.
        display_ally_team = state.get("ally_team") or []

        enemy_naming_instruction = ""
        if not enemy_named_this_turn:
            enemy_naming_instruction = (
                "\n6. 이번 질문에서 사용자는 특정 상대 영웅 이름을 말하지 않았다. 검색된 문서에 "
                "영웅 이름이 등장하더라도 그것을 사용자가 실제로 마주한 상대인 것처럼 단정해서 "
                "'상대 ○○가', '○○를 상대할 때' 식으로 쓰지 마라. 상대를 지칭해야 한다면 "
                "'상대 탱커', '다이브해오는 적'처럼 일반적 표현만 사용해라."
            )

        swap_decision_instruction = ""
        if state.get("intent") == "swap":
            recommended = state.get("recommended_heroes", [])
            swap_decision_instruction = (
                "\n7. 사용자는 같은 역할 안에서의 영웅 교체(예: "
                f"{current_hero} → 다른 {ROLE_LABELS.get(current_hero_role, '')} 영웅) 여부를 묻고 있다.\n"
                "   운영 팁만 늘어놓고 끝내지 마라. 답변의 첫 문장에서 반드시 "
                "'바꾸는 게 낫다 / 유지하는 게 낫다 / 둘 다 가능하지만 ○○가 더 낫다' 식으로 "
                "명확한 결론을 먼저 제시한 뒤, 그 이유와 운영 팁을 이어서 설명해라.\n"
                f"   추천 영웅 후보({recommended})가 있다면 그 영웅으로 교체할 때의 장점과, "
                f"{current_hero}를 유지할 때의 장점을 비교해서 판단 근거를 명확히 제시해라."
            )

        if current_hero:
            current_hero_context_line = (
                f"현재 사용자 영웅: {current_hero} "
                f"(역할: {ROLE_LABELS.get(current_hero_role, '알 수 없음') if current_hero_role else '알 수 없음'})"
            )
        else:
            current_hero_context_line = "현재 사용자 영웅: 명확히 확인되지 않음"
            # focus_heroes: 이번 질문이 다루는 주제 영웅. 영웅 이름이 없는 후속
            # 질문에서 LLM이 기준 영웅을 알 수 있는 유일한 통로다.
            focus_heroes_for_prompt = state.get("focus_heroes") or []
            if focus_heroes_for_prompt:
                current_hero_context_line += (
                    f"\n질문 주제 영웅: {', '.join(focus_heroes_for_prompt)} — 사용자가 "
                    "플레이 중이라고 밝힌 영웅은 아니지만, 이번 질문이 다루는 대상이다. "
                    "이 영웅을 기준으로 답해라."
                )

        selected_role_context_line = ""
        if role_filter_explicit and role_filter in ROLE_LABELS:
            selected_role_context_line = f"\n사용자가 선택한 기준 역할: {ROLE_LABELS.get(role_filter)}"

        if current_hero_uncertain:
            role_lock_block = f"""=== 영웅 정보 불확실 (주의) ===
사용자가 지금 어떤 영웅을 플레이 중인지 이번 메시지만으로는 확실하지 않다.
이전 대화에서는 {current_hero or "어떤 영웅"} 얘기가 있었지만, 이번 메시지에는 그 이름이
등장하지 않는다. 이번 질문은 그 영웅과 무관한 일반적인 팀 조합/전략 질문일 수 있다.

따라서:
- "현재의 {current_hero}보다는" 같은 식으로 특정 영웅을 계속 플레이 중이라고 단정하지 마라.
- 역할 제한 없이, 상황(상대 조합, 맵, 문제 상황)에 맞는 영웅이나 조합을 자유롭게 제안해라.
- 다만 오버워치는 역할 고정(Role Lock) 모드이므로, 영웅을 추천할 때는 어떤 역할의
  영웅인지 명확히 밝혀서 사용자가 본인 역할에 맞게 골라볼 수 있게 하라.
================================="""
        else:
            role_lock_block = f"""=== 최우선 규칙 (절대 위반 금지) ===
오버워치는 역할 고정(Role Lock) 모드를 운영한다.
딜러로 입장하면 그 판에서는 딜러 영웅만 선택 가능하다. 탱커·힐러로 변경 불가.
탱커·힐러도 동일하다. 역할을 넘나드는 교체는 게임 시스템상 불가능하다.

{current_hero_context_line}{selected_role_context_line}
{allowed_heroes_text}

따라서:
- 사용자가 힐을 못받는다고 해도 → 힐러 교체 제안 금지 (역할 고정으로 불가능)
- 사용자가 팀 딜이 부족하다고 해도 → 탱커·힐러 교체 제안 금지
- 어떤 상황·이유가 있어도 허용 목록 밖의 영웅 추천 금지
- "탱커나 힐러 교체를 원하시면 상대 조합을 알려주세요" 같은 우회적 안내도 절대 금지.
  다른 역할 교체 가능성 자체를 언급하지 마라. 사용자는 이번 판에서 다른 역할로 절대 갈 수 없다.
- 대신 현재 역할 안에서 해결책을 찾아라

주의: 위 규칙은 "다른 역할로의 교체"만 금지하는 것이다.
같은 역할 안에서의 교체(예: 딜러 {current_hero} → 다른 딜러)는 전혀 다른 문제이며,
사용자가 그런 질문을 했다면 절대 회피하지 말고 명확히 판단해서 답해야 한다.
======================================="""

        if is_simple_style:
            style_rules_1to5 = (
                "1. 문단 대신 핵심 아이디어당 한 줄, \\n으로 구분해라. 격식체 종결(~입니다 등) "
                "대신 \"~하기 좋음\", \"~가능\" 같은 짧은 구로 끝내고, 질문에 나온 영웅/상대를 "
                "되짚는 서두 없이 바로 본론부터 써라. 같은 섹션 안 항목은 빈 줄 없이 \\n으로만 "
                "구분하고, 빈 줄은 섹션 사이에만 써라.\n"
                "2. 추천 영웅이 있으면 \"추천 영웅: 영웅1, 영웅2\" 한 줄 뒤 영웅마다 이름 줄과 "
                "\"- \"로 시작하는 짧은 이유 1~2개를 적어라. 이 블록은 답변 전체에 한 번만 "
                "만들어라 — 여러 역할을 함께 추천하더라도 블록을 역할마다 따로 만들지 말고 "
                "한 블록에 모아 적고, 이름 옆 괄호로 역할을 밝혀라(예: 윈스턴(탱커)). "
                "없으면(운영 개선/유지 등) 이 블록 없이 서론 없이 바로 4번만 적어라.\n"
                "3. 스킬은 단축키를 괄호로 붙여라(예: 투창(우클릭))."
            )
            if is_hero_comparison_question:
                # 비교 질문은 판단을 원하는 질문이라 "3가지"를 강제하지 않는다.
                style_rules_1to5 += (
                    "\n4. \"바로 할 것 3가지\"는 만들지 마라 — 사용자는 운영 팁이 "
                    "아니라 비교 결론을 원했다."
                )
            else:
                style_rules_1to5 += (
                    "\n4. 마지막에 \"바로 할 것 3가지\" 아래 1~3개 항목을 \"1. \", \"2. \" "
                    "숫자 목록으로 적어라."
                )
            stay_preference_instruction = (
                "\"추천 영웅\" 블록과 서두 결론 문장 없이 핵심 운영 아이디어부터 한 줄씩 "
                "적어라. 그 아래 \"운영 팁:\" 한 줄과 구체적 팁을 줄마다 하나씩 적어라."
            )
            markdown_ban_text = (
                "answer에는 마크다운을 쓰지 마라. 단, 영웅별 이유 나열의 \"- \"만 허용한다. "
                "줄바꿈은 \\n으로."
            )
            markdown_forbidden_line = (
                "- 마크다운 문법(**, #, > 등). 단, 영웅별 이유 나열의 \"- \"만 예외로 허용."
            )
        else:
            style_rules_1to5 = (
                "1. 첫 문장은 이번 질문의 핵심에 바로 답해라. 질문이 예/아니오나 선택을 묻는 "
                "것이라면\n"
                "   운영 팁부터 늘어놓지 말고, 먼저 그 질문에 직접 답한 뒤 이유와 팁을 "
                "설명해라.\n"
                "2. 영웅 교체를 추천할 때는 위 허용 목록 안에서만 골라라.\n"
                "   추천 영웅 목록은 답변 전체에 한 번만 만들어라 — 여러 역할을 함께 "
                "추천하더라도 역할마다 목록을 따로 만들지 말고 한 곳에 모아 적고,\n"
                "   이름 옆 괄호로 역할을 밝혀라(예: 윈스턴(탱커)).\n"
                "3. 힐 부족·팀 문제처럼 현재 역할로 해결하기 어려운 상황이라면,\n"
                "   역할 변경 대신 \"현재 영웅으로 생존력을 높이는 법\" 또는 \"힐팩 활용\" 등 "
                "대안을 제시해라.\n"
                "4. 스킬명에 단축키를 같이 써라. 예: 다이너마이트(shift), 코치건(e)."
            )
            if is_hero_comparison_question:
                style_rules_1to5 += (
                    "\n5. \"바로 적용할 것 3가지\"는 만들지 마라 — 사용자는 운영 팁이 "
                    "아니라 비교 결론을 원했다."
                )
            else:
                style_rules_1to5 += "\n5. 마지막에 \"바로 적용할 것 3가지\"를 적어라."
            stay_preference_instruction = (
                "첫 문장은 반드시 \"그 영웅을 유지해도 된다\" 또는 \"불리하지만 운영으로 풀 수 "
                "있다\"처럼\n"
                "   사용자의 선택을 존중하는 방향으로 답해라.\n"
                "   이후 그 영웅으로 상대 조합을 상대하는 구체적인 운영법을 제시해라."
            )
            markdown_ban_text = (
                "answer 안에서는 마크다운 문법(**볼드**, *   리스트, # 제목 등)을 쓰지 마라. "
                "줄바꿈은 \\n으로,\n"
                "목록은 \"1. \", \"2. \" 같은 일반 숫자/기호로만 표현해라. 마크다운 기호는 "
                "JSON 문자열 파싱을\n"
                "깨뜨릴 수 있으므로 절대 사용하지 마라."
            )
            markdown_forbidden_line = "- 마크다운 문법(**, *, #, - 등)"

        # "그 영웅을 유지해도 된다"류 안내는 intent가 stay일 때만 프롬프트에 넣는다.
        stay_intent_block = ""
        if state.get("intent") == "stay":
            stay_intent_block = f"""
6. 사용자가 특정 영웅을 하고 싶다, 쓸 것이다, 고정으로 한다, 원챔이다, 해도 되냐고 말한 경우
   다른 영웅 추천을 먼저 하지 마라.
   {stay_preference_instruction}"""

        # performance_improve는 교체 의도가 아니므로 명시해둔다. 단 비교 질문
        # (compared_heroes)에는 넣지 않는다 — 비교 대상이 current_hero와 다를 수
        # 있어 "지금 영웅 중심" 지시와 충돌한다.
        performance_improve_instruction = ""
        if state.get("intent") == "performance_improve" and len(state.get("compared_heroes") or []) < 2:
            performance_improve_instruction = """
9. 사용자는 지금 영웅을 계속 플레이하면서 스탯/실력/운영을 개선하고 싶어하는
   것이지, 다른 영웅으로 바꾸고 싶어하는 게 아니다. "추천 영웅" 블록을 만들거나
   다른 영웅으로 바꾸라고 제안하지 말고, 지금 영웅으로 무엇을 다르게 하면
   좋을지만 답해라. 단, 사용자가 본인이 아니라 팀원 전체나 다른 특정 팀원의
   활약/순위를 물었다면 이 지시를 따르지 말고 위 "스탯 분석 지시"의 순위
   관련 규칙을 따라라 — 그 팀원(들) 얘기만 답하고 본인 얘기는 꺼내지 마라."""

        # 맵 운영 질문은 추천을 직접 요청한 게 아니면 위치·타이밍 설명을 우선한다.
        map_strategy_instruction = ""
        if state.get("intent") == "map_strategy":
            map_strategy_instruction = """
10. 이 질문은 맵 운영에 대한 질문이다. 사용자가 영웅 추천이나 조합을 직접
    요청한 게 아니라면(예: 좋은 자리, 타이밍, 시야 확보를 묻는 질문), 영웅별
    추천 목록 형식으로 답하지 말고 질문에 직접 답하는 문단으로 설명해라.
    영웅 이름은 예시가 필요할 때만 짧게 언급해도 된다."""

        # composition인데 카드 모드가 아니면 평가 질문이다. 답변이 영웅 추천으로
        # 새지 않게 막는다(추천은 카드나 후속 질문 버튼의 몫).
        composition_evaluation_instruction = ""
        if state.get("intent") == "composition" and not state.get("recommend_card_mode"):
            comp_roster_size = resolve_roster_size(state.get("roster_size_effective"))
            roster_line = (
                f"\n    이번 판은 {roster_size_label(comp_roster_size)}이고 역할 정원은 "
                f"{roster_role_quota_text(comp_roster_size)}이다. 이 규격을 기준으로 "
                "조합이 균형 잡혔는지 판단해라."
            )
            # 정원이 이미 찬 조합은 "내 자리"라는 개념 자체가 없다.
            if state.get("roster_is_full"):
                roster_line += (
                    f"\n    아군 {comp_roster_size}명이 모두 정해진 완성된 조합이라 "
                    "사용자가 채울 빈자리가 없다. 사용자 개인이 무엇을 골라야 할지는 "
                    "다루지 말고, 팀 조합 전체가 어떤지를 평가해라."
                )
            composition_evaluation_instruction = f"""
11. 사용자는 이미 정한 아군 조합(위 "아군 조합")이 어떤지 평가해달라고 묻고
    있다(무엇을 더 뽑을지 추천해달라는 질문이 아니다). 그 조합의 강점/약점과
    함께 쓸 때의 운영 방식을 답변의 중심 내용으로 다뤄라. 부족한 역할이나
    약점이 있다면 "이런 부분이 아쉽다" 정도로 짧게만 짚고, 그걸 보완할 구체적인
    영웅을 여러 명 나열해서 추천하지는 마라 — 그건 이 답변이 할 일이 아니다.{roster_line}"""

        # 특전 질문 전용 지시. 세 가지를 한꺼번에 막는다 — (1) 같은 칸의 특전 두
        # 개를 한 조합으로 묶어 게임에서 불가능한 선택을 알려주는 것(실제로 주요
        # 특전인 "전투자극제 + 전속력"을 묶어 답한 사례가 있었다), (2) 상황 정보도
        # 없이 하나가 정답인 것처럼 단정하는 것, (3) 특전 이름과 설명을 " - "로 한
        # 줄에 붙여 설명 속 기본 스킬 이름이 또 하나의 특전처럼 읽히는 것.
        # 간단히/자세히 두 스타일에 같은 형식을 쓰려고 style_rules 밖에 따로 둔다.
        perk_instruction = ""
        if state.get("is_perk_question"):
            # 스탯·상대 조합·맵처럼 판단 근거가 이미 있으면 선택지만 늘어놓고
            # 끝내지 않는다 — 앞에서 "데스가 많다"까지 분석해놓고 "상황 보고
            # 고르세요"로 끝나면 분석과 답이 따로 논다.
            perk_situation_known = bool(
                has_stats
                or (state.get("enemy_team") and enemy_named_this_turn)
                or state.get("map_name")
            )
            if perk_situation_known:
                perk_choice_rule = (
                    "- 이번 대화에서 확인된 상황(스탯 / 상대 조합 / 맵)이 있으니 선택지만\n"
                    "  늘어놓고 끝내지 마라. 조합들을 모두 소개한 뒤 마지막에 \"추천 운용: "
                    "OO 운용\"\n  한 줄을 적고, 다음 줄에 왜 그 조합인지를 확인된 정보와 "
                    "직접 연결해 설명해라\n  (예: 데스가 많으니 포지션 전환과 이탈을 "
                    "강화하는 쪽). 나머지 조합은 위에 조건과\n  함께 그대로 남겨둔다."
                )
            else:
                perk_choice_rule = (
                    "- 상대 조합·맵·스탯처럼 판단 근거가 될 상황 정보가 이번 대화에 없다.\n"
                    "  어느 하나를 정답처럼 단정하지 말고 조합마다 \"언제 고르는지\"만 적어\n"
                    "  사용자가 상황을 보고 고르게 해라."
                )
            perk_instruction = f"""

특전(퍼크) 답변 규칙:
- 특전은 보조 특전 칸에서 1개, 주요 특전 칸에서 1개를 고르는 것이다. 같은 칸의
  특전 두 개를 한 조합으로 묶지 마라 — 게임에서 불가능한 선택이다. 어떤 특전이
  보조이고 어떤 특전이 주요인지, 선택 방향이 좌클인지 우클인지는 검색된 문서의
  특전 데이터에 있는 그대로만 따르고, 문서에 없는 특전 이름이나 조합을 지어내지
  마라. 문서에 그 영웅의 특전 데이터가 없으면 추측하지 말고 확인이 필요하다고
  밝혀라.
- 문서에 운용 조합(기본 운용/안정 운용/공격 운용 등)이 있으면 그 조합들을 각각
  언제 고르는지 판단 기준과 함께 제시해라.
{perk_choice_rule}
- 조합 하나는 정확히 아래 형식으로 적어라. "- "로 시작하는 설명 줄은 이 블록에
  한해 허용한다.
  기본 운용 : (나선 추진(보조 특전, 좌클) + 전속력(주요 특전, 좌클))
  - 맵과 상대 조합이 불확실할 때 가장 안정적인 선택입니다.
  - 나선 로켓(우클릭)의 명중률을 높여 킬 결정력을 보완합니다.
- 첫 줄에는 조합 이름과 특전 두 개까지만 적고 설명은 반드시 다음 줄부터 적어라.
  특전 이름과 설명을 " - "나 ":"로 같은 줄에 이어 붙이지 마라 — 설명 줄에는 그
  특전이 강화하는 기본 스킬 이름(예: 나선 로켓(우클릭))이 나오는데, 특전 이름과
  한 줄에 붙으면 특전을 두 개 고르는 것처럼 읽힌다.
- 같은 조합 안(제목 줄과 설명 줄들 사이, 설명 줄들끼리)에는 빈 줄을 넣지 말고
  줄을 붙여 써라. 빈 줄은 조합과 조합 사이에만 하나 넣는다."""

        prompt = f"""
너는 오버워치 코칭 RAG 챗봇이다. 사용자에게 한국어로 답변해라.

{role_lock_block}

이번 사용자 질문: {state.get("message")}
{f"(참고: 직전 질문은 '{state.get('previous_user_message')}' 였고, 이번 질문은 그 후속이다. 이번 답변도 그 맥락—왜 교체를 고민하게 됐는지—을 이어받아 일관되게 답해라.)" if state.get("previous_user_message") and not current_hero_uncertain else ""}
스탯 입력 여부: {"있음" if has_stats else "없음"}

현재 컨텍스트:
- 카운터 대상: {display_target_enemy or "없음 (이번 질문에서 특정 상대를 언급하지 않음)"}
- 맵: {state.get("map_name")}
- 공격/수비: {state.get("side")}
- 상대 조합: {display_enemy_team}
- 아군 조합: {', '.join(display_ally_team) if display_ally_team else "없음"}
- 질문 의도: {state.get("intent")}
- 전략 판단: {state.get("recommendation_type") or "없음(직접 판단해라)"}
- 추천 영웅 후보: {state.get("recommended_heroes", [])}
- 판단 근거: {state.get("strategy_reason", "")}

스탯 정보:
{stat_summary or "없음"}
가장 위협적인 적: {display_high_threat or "없음"}

{stat_analysis_instruction}

스킬 단축키 참고:
{skill_shortcut_text}

검색된 문서:
{state.get("retrieval_text", "")}

아래 JSON 형식으로만 답해라. 다른 텍스트, 설명, 코드 펜스(```) 없이 JSON 객체 하나만 출력해라.
answer 값 안에 JSON을 다시 넣지 마라. answer는 사용자에게 보여줄 순수 텍스트만 담아라.
{markdown_ban_text}

{{
  "answer": "사용자에게 보여줄 최종 답변 (줄바꿈은 \\n으로 표현, 마크다운 금지)",
  "used_doc_ids": [1, 2]{suggested_questions_schema_line}
}}

답변 작성 규칙:
{style_rules_1to5}{enemy_naming_instruction}{swap_decision_instruction}{stay_intent_block}{performance_improve_instruction}{map_strategy_instruction}{composition_evaluation_instruction}{suggested_questions_rules}
{perk_instruction}

절대 금지:
- 허용 목록 밖 역할의 영웅 추천 (역할 고정으로 게임 내 선택 불가)
- "[문서 1]", "(문서 1)" 같은 어떤 형태의 출처 표시도 금지
- "문서에 따르면", "~에서 언급했듯이" 같은 자료 인용 표현
{markdown_forbidden_line}
- 사용자가 말하지 않은 상황을 단정적으로 지어내서 답변 서두에 전제로 깔지 마라.
  "이번 사용자 질문" 원문에 없는 상대의 의도나 행동을 사실처럼 서술하지 말고,
  사용자가 실제로 말한 내용과 위 컨텍스트에 있는 정보만 근거로 답해라.
"""

        raw_text = call_llm_text(llm, prompt)

        if isinstance(raw_text, list):
            raw_text = "\n".join(str(item) for item in raw_text)
        elif not isinstance(raw_text, str):
            raw_text = str(raw_text)

        parsed = safe_json_loads(raw_text, default={})

        if isinstance(parsed, dict) and parsed.get("answer"):
            raw_answer = parsed["answer"].replace("\\n", "\n")
            used_doc_ids = parsed.get("used_doc_ids", [])
        else:
            answer_match = re.search(
                r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)"',
                raw_text, re.DOTALL
            )
            if answer_match:
                raw_answer = answer_match.group(1).replace("\\n", "\n").replace('\\"', '"')
                logger.info("[FALLBACK] answer 필드 정규식 추출 성공")
            else:
                # 토큰 한도로 JSON이 잘린 경우. 껍데기가 노출되지 않게 answer만 뽑는다.
                truncated_match = re.search(
                    r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)\\?$',
                    raw_text, re.DOTALL
                )
                if truncated_match:
                    raw_answer = truncated_match.group(1).replace("\\n", "\n").replace('\\"', '"')
                    raw_answer = re.sub(r'\\+$', '', raw_answer).rstrip()
                    logger.warning("[FALLBACK] answer 필드가 중간에 잘림(max_output_tokens 의심), 잘린 내용으로 대체")
                else:
                    cleaned = re.sub(r'```(?:json)?\s*[\s\S]*?```', '', raw_text, flags=re.IGNORECASE)
                    cleaned = re.sub(r'\{[\s\S]*"answer"[\s\S]*\}', '', cleaned)
                    raw_answer = cleaned.strip() or raw_text
                    logger.warning("[FALLBACK] answer 필드 추출 실패, raw_text 정제본 사용")
            used_doc_ids = []

        if not isinstance(used_doc_ids, list):
            used_doc_ids = []
        used_doc_ids = [int(d) for d in used_doc_ids if str(d).isdigit()]

        # 특전 답변의 운용 조합 부분은 형식을 LLM에 맡기지 않고 여기서 확정한다
        # (제목 줄 + "- " 설명 줄, 블록 안 빈 줄 없음, 추천 조합은 제목에 표시).
        # sanitize가 "- "를 지우므로(자세히 스타일) 그 뒤에 다시 붙인다.
        is_perk_answer = bool(state.get("is_perk_question"))
        answer = sanitize_answer_for_user(
            raw_answer, keep_dash_bullets=is_simple_style or is_perk_answer
        )
        if is_perk_answer:
            answer = format_perk_answer(answer)

        if answer_allowed_hero_set is not None:
            # 사용자가 원문에서 언급한 영웅은 추천이 아니라 인용이므로 제외한다.
            user_mentioned_heroes = set(find_all_heroes(state.get("message", "")))

            enemy_context_heroes = set()

            target_enemy = state.get("target_enemy")
            if target_enemy:
                enemy_context_heroes.add(normalize_hero_name(target_enemy))

            high_threat_enemy = state.get("high_threat_enemy")
            if high_threat_enemy:
                enemy_context_heroes.add(normalize_hero_name(high_threat_enemy))

            for h in state.get("enemy_team", []) or []:
                normalized = normalize_hero_name(h)
                if normalized:
                    enemy_context_heroes.add(normalized)

            # enemy_stats는 enemy_team과 별도 경로로 세션에 남으므로 함께 본다.
            for h in (state.get("enemy_stats") or {}).keys():
                normalized = normalize_hero_name(h)
                if normalized:
                    enemy_context_heroes.add(normalized)

            # 아군 영웅을 언급한 문장이 "역할 밖 추천"으로 오인돼 치환되면 안 된다.
            # ally_team이 좁혀져도 팀원 이름이 남도록 my_team_stats도 함께 본다.
            ally_context_heroes = {
                normalize_hero_name(h) for h in (state.get("ally_team") or []) if h
            }
            for h in (state.get("my_team_stats") or {}).keys():
                normalized = normalize_hero_name(h)
                if normalized:
                    ally_context_heroes.add(normalized)

            forbidden_in_answer = [
                h for h in find_all_heroes(answer)
                if (
                    h not in answer_allowed_hero_set
                    and h not in user_mentioned_heroes
                    and h not in enemy_context_heroes
                    and h not in ally_context_heroes
                )
            ]

            if forbidden_in_answer:
                logger.warning(
                    "[ROLE VIOLATION] 답변에 허용 범위 밖 영웅 등장: %s (current_hero=%s role=%s, "
                    "user_mentioned=%s) — 단어만 치환",
                    forbidden_in_answer, current_hero, current_hero_role, user_mentioned_heroes,
                )
                role_label_kor = (
                    role_filter_label(role_filter)
                    if parse_role_filter(role_filter)
                    else ROLE_LABELS.get(current_hero_role, "현재 역할")
                )

                # 줄 전체가 아니라 위반 영웅 이름만 치환해 문장 구조를 보존한다.
                forbidden_hero_names = set(forbidden_in_answer)
                # 별칭과 원본 표기를 모두 치환 대상으로 잡는다.
                surface_forms = set()
                for h in HEROES:
                    if normalize_hero_name(h) in forbidden_hero_names:
                        surface_forms.add(h)
                surface_forms |= forbidden_hero_names

                for surface in sorted(surface_forms, key=len, reverse=True):
                    if surface in answer:
                        answer = answer.replace(surface, "다른 영웅")

                # 같은 표현이 연달아 중복되며 어색해지는 것만 가볍게 정리
                answer = re.sub(r"(다른 영웅)(,?\s*\1)+", r"\1", answer)

                notice = (
                    f"\n\n⚠ 역할 고정 모드에서는 {role_label_kor} 역할만 선택 가능합니다. "
                    f"위 추천은 {role_label_kor} 영웅 기준으로 제한되었습니다."
                )
                answer = answer + notice

        retrieved_docs = state.get("retrieved_docs", [])
        used_docs = [doc for doc in retrieved_docs if int(doc.get("doc_id", -1)) in used_doc_ids]
        used_doc_metadata = []

        if used_docs:
            logger.info("========== AI 답변 참고 문서 ==========")
        for doc in used_docs:
            metadata = doc.get("metadata", {})
            preview = doc.get("content", "")[:300].replace("\n", " ")
            used_doc_metadata.append({
                "doc_id": doc.get("doc_id"),
                "query": doc.get("query"),
                "metadata": metadata,
                "preview": preview,
            })
            logger.info("[AI USED DOC %s] query=%s metadata=%s preview=%s",
                        doc.get("doc_id"), doc.get("query"), metadata, preview)
        if used_docs:
            logger.info("======================================")

        result = {"answer": answer, "used_doc_ids": used_doc_ids, "used_doc_metadata": used_doc_metadata}
        if is_simple_style:
            result["suggested_questions"] = extract_inline_suggested_questions(parsed)
        return result

    except Exception as exc:
        logger.exception("generate_answer_node 오류: %s", exc)
        return {"error": str(exc)}


def _clean_matchup_hero_list(
    items: Any, exclude: Optional[Any], allowed_set: Optional[set]
) -> List[Dict[str, str]]:
    """LLM이 만든 카드(상성 카드/추천 영웅 카드)의 hero/note 목록을 검증한다.
    실제 영웅명이 아니거나, 제외 대상(exclude — 분석 대상 자신, 이미 정해진
    아군 등 하나 또는 여러 명)이거나, 역할 제한(allowed_set)을 벗어나면 뺀다."""
    valid_heroes = {normalize_hero_name(h) for h in HEROES}
    if exclude is None:
        exclude_set: set = set()
    elif isinstance(exclude, str):
        exclude_set = {exclude}
    else:
        exclude_set = set(exclude)
    cleaned: List[Dict[str, str]] = []
    seen = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        hero_name = normalize_hero_name(str(item.get("hero", "")).strip())
        if not hero_name or hero_name in exclude_set or hero_name in seen:
            continue
        if hero_name not in valid_heroes:
            continue
        if allowed_set is not None and hero_name not in allowed_set:
            continue
        note = str(item.get("note", "")).strip()
        cleaned.append({"hero": hero_name, "note": note})
        seen.add(hero_name)
    return cleaned


def generate_matchup_answer_node(state: ChatbotGraphState) -> ChatbotGraphState:
    """아직 영웅을 안 고른 순수 카운터 추천 질문, 또는 지금 영웅의 교체를 고민하는
    질문(merge_context_node의 matchup_subject 계산 참고)은 문단 서술 대신,
    말풍선에 짧은 설명 + "상성 카드"(상대하기 어려운 영웅 / 쉬운 영웅)로 답한다."""
    if state.get("error"):
        return state

    try:
        _chatbot, _retriever, llm = chatbot_service.get_chatbot_components()

        subject = state.get("matchup_subject")
        is_subject_enemy = state.get("matchup_subject_is_enemy", False)
        subject_role = HERO_TO_ROLE.get(subject) if subject else None
        subject_role_label = ROLE_LABELS.get(subject_role, "알 수 없음")

        answer_style = state.get("answer_style") or "detailed"
        is_simple_style = answer_style == "simple"

        role_filter = state.get("role_filter") or "all"
        role_filter_explicit = state.get("role_filter_explicit", False)
        current_hero_role = state.get("current_hero_role")
        # 명시 선택이 없고 현재 영웅 역할과 role_filter가 다르면(세션 잔존값 등)
        # 현재 영웅 역할을 우선한다 — generate_answer_node와 동일한 안전장치.
        if (
            not role_filter_explicit
            and current_hero_role
            and current_hero_role in ROLE_HEROES
            and role_filter != current_hero_role
        ):
            role_filter = current_hero_role

        # hard_heroes는 사용자가 픽할 후보라 역할 제한을 받고, easy_heroes는
        # 정보 제공용이라 제한하지 않는다.
        if parse_role_filter(role_filter):
            hard_role_constraint = (
                f"hard_heroes는 반드시 {role_filter_label(role_filter)} 역할만: "
                f"{', '.join(heroes_for_role_filter(role_filter))}\n"
                "이 목록 밖의 영웅은 어떤 이유로도 넣지 마라."
            )
            if len(parse_role_filter(role_filter)) > 1:
                # 남은 자리가 두 역할 중 하나일 때는 양쪽을 함께 제시한다.
                hard_role_constraint += (
                    f"\n{role_filter_label(role_filter)} 두 역할을 골고루 섞어서 넣어라."
                )
            hard_allowed_set: Optional[set] = {
                normalize_hero_name(h) for h in heroes_for_role_filter(role_filter)
            }
        elif role_filter == "all":
            hard_role_constraint = (
                "hard_heroes는 특정 역할로 제한하지 말고, 탱커/딜러/힐러 각 역할에서 "
                "최소 1명씩 골고루 포함해라. 한 역할에만 치우치지 마라."
            )
            hard_allowed_set = None
        else:
            hard_role_constraint = "hard_heroes 역할 제한 없음."
            hard_allowed_set = None

        intro_length_instruction = (
            "1~2문장으로 아주 짧게" if is_simple_style else "2~3문장으로 자연스럽게 풀어서"
        )
        subject_context = (
            f"{subject}는 상대팀이 사용 중인(또는 카운터하려는) 영웅이다."
            if is_subject_enemy
            else f"{subject}는 사용자가 현재 플레이 중인 영웅이다."
        )

        prompt = f"""
너는 오버워치2 코칭 RAG 챗봇의 상성표(매치업 카드) 생성 모듈이다.

분석 대상 영웅: {subject} (역할: {subject_role_label})
{subject_context}

사용자 질문: {state.get("message")}
질문 의도: {state.get("intent")}

역할 제한 규칙:
{hard_role_constraint}

검색된 문서:
{state.get("retrieval_text", "")}

스킬 단축키 참고:
{get_skill_shortcut_text()}

아래 JSON 형식으로만 답해라. 다른 텍스트, 설명, 코드 펜스(```) 없이 JSON 객체 하나만
출력해라. 마크다운 문법(**, *, #, - 등)을 쓰지 마라.

{{
  "intro": "채팅에 보여줄 짧은 설명 ({intro_length_instruction}, 마크다운 금지, 줄바꿈은 \\n으로)",
  "hard_heroes": [{{"hero": "영웅 이름", "note": "10자 내외 짧은 설명"}}],
  "easy_heroes": [{{"hero": "영웅 이름", "note": "10자 내외 짧은 설명"}}]{SUGGESTED_QUESTIONS_INLINE_SCHEMA_LINE if is_simple_style else ""}
}}

규칙:
1. hard_heroes: {subject}를 상대하기 어렵게 만드는(즉 {subject}의 카운터가 되는) 영웅을
   3~4명 골라라. note는 카운터 강도를 "카운터 강도 높음", "카운터 강도 중간",
   "카운터 강도 낮음" 중 하나로 표현해라.
2. easy_heroes: {subject}가 상대하기 쉬운(즉 {subject}에게 유리한) 영웅을 3~4명 골라라.
   이쪽은 추천 픽이 아니라 정보 제공용이므로 역할 제한 없이 자유롭게 골라도 된다.
   note는 왜 유리한지 짧게 표현해라(예: "압박하기 쉬움", "진입 성공 시 유리",
   "스킬이 빠지면 압박 가능").
3. hard_heroes/easy_heroes 어디에도 {subject} 자신은 넣지 마라.
4. intro는 {subject}의 특징과 왜 이런 상성이 생기는지 자연스러운 한국어 문장으로
   설명해라. 목록이나 영웅 이름을 나열하는 형태로 쓰지 마라 — 그건 카드가 이미 보여준다.
5. "[문서 1]" 같은 출처 표시나 "문서에 따르면" 같은 인용 표현은 절대 쓰지 마라.
{SUGGESTED_QUESTIONS_INLINE_RULES if is_simple_style else ""}
"""

        raw_text = call_llm_text(llm, prompt)
        if isinstance(raw_text, list):
            raw_text = "\n".join(str(item) for item in raw_text)
        elif not isinstance(raw_text, str):
            raw_text = str(raw_text)

        parsed = safe_json_loads(raw_text, default={})
        if not isinstance(parsed, dict):
            parsed = {}

        intro = sanitize_answer_for_user(str(parsed.get("intro") or ""), keep_dash_bullets=False)
        hard_heroes = _clean_matchup_hero_list(parsed.get("hard_heroes"), subject, hard_allowed_set)
        easy_heroes = _clean_matchup_hero_list(parsed.get("easy_heroes"), subject, None)

        if not intro and not hard_heroes and not easy_heroes:
            # 응답 파싱이 완전히 실패한 경우: 카드 없이 최소한의 안내만 남긴다.
            logger.warning("[MATCHUP CARD] 파싱 실패, 안내 문구로 대체. raw=%s", raw_text)
            intro = "상성 정보를 불러오지 못했습니다. 다시 질문해 주세요."

        matchup_card = None
        if hard_heroes or easy_heroes:
            matchup_card = {
                "subject": subject,
                "subject_role": subject_role,
                "is_enemy": is_subject_enemy,
                "hard_heroes": hard_heroes,
                "easy_heroes": easy_heroes,
            }

        result = {
            "answer": intro,
            "matchup_card": matchup_card,
            "recommendation_type": "matchup_card",
            "recommended_heroes": [h["hero"] for h in hard_heroes],
        }
        if is_simple_style:
            result["suggested_questions"] = extract_inline_suggested_questions(parsed)
        return result

    except Exception as exc:
        logger.exception("generate_matchup_answer_node 오류: %s", exc)
        return {"error": str(exc)}


def generate_recommend_card_node(state: ChatbotGraphState) -> ChatbotGraphState:
    """swap(교체 고민) 또는 composition(팀 조합 분석) 질문은 상성 카드(상대하기
    어려운/쉬운 두 목록)가 아니라 "추천 영웅 카드"(단일 목록 + 이유)로 답한다.
    recommend_card_mode로 두 모드를 구분한다 — swap은 지금 영웅과 같은 역할의
    대안을, composition은 아직 안 채워진 역할의 시너지 픽을 추천한다."""
    if state.get("error"):
        return state

    try:
        _chatbot, _retriever, llm = chatbot_service.get_chatbot_components()

        mode = state.get("recommend_card_mode")
        answer_style = state.get("answer_style") or "detailed"
        is_simple_style = answer_style == "simple"
        intro_length_instruction = (
            "1~2문장으로 아주 짧게" if is_simple_style else "2~3문장으로 자연스럽게 풀어서"
        )

        role_filter = state.get("role_filter") or "all"
        role_filter_explicit = state.get("role_filter_explicit", False)
        current_hero = state.get("current_hero")
        current_hero_role = state.get("current_hero_role")
        # 명시 선택이 없고 현재 영웅 역할과 role_filter가 다르면(세션 잔존값 등)
        # 현재 영웅 역할을 우선한다 — generate_answer_node와 동일한 안전장치.
        if (
            not role_filter_explicit
            and current_hero_role
            and current_hero_role in ROLE_HEROES
            and role_filter != current_hero_role
        ):
            role_filter = current_hero_role

        if parse_role_filter(role_filter):
            role_constraint = (
                f"추천 영웅은 반드시 {role_filter_label(role_filter)} 역할만: "
                f"{', '.join(heroes_for_role_filter(role_filter))}\n"
                "이 목록 밖의 영웅은 어떤 이유로도 넣지 마라."
            )
            if len(parse_role_filter(role_filter)) > 1:
                # 아군 조합상 남은 자리가 두 역할 중 하나로 좁혀진 경우(6vs6).
                # 어느 쪽을 고를지는 사용자가 정하므로 양쪽을 함께 제시한다.
                role_constraint += (
                    f"\n사용자는 {role_filter_label(role_filter)} 중 무엇을 할지 아직 정하지 않았다. "
                    "두 역할 영웅을 골고루 섞어서 추천하고, 추천 이유에 어느 역할인지 밝혀라."
                )
            allowed_set: Optional[set] = {
                normalize_hero_name(h) for h in heroes_for_role_filter(role_filter)
            }
        elif role_filter == "all":
            role_constraint = (
                "추천 영웅은 특정 역할로 제한하지 말고, 탱커/딜러/힐러 각 역할에서 "
                "최소 1명씩 골고루 포함해라. 한 역할에만 치우치지 마라."
            )
            allowed_set = None
        else:
            role_constraint = "역할 제한 없음. 상황에 맞는 영웅을 자유롭게 추천해라."
            allowed_set = None

        exclude_heroes: set = set()
        if current_hero:
            exclude_heroes.add(current_hero)
        ally_team = state.get("ally_team") or []
        exclude_heroes.update(ally_team)

        enemy_team = state.get("enemy_team") or []
        target_enemy = state.get("target_enemy")
        enemy_named_this_turn = state.get("enemy_named_this_turn", False)
        display_enemy_team = enemy_team if enemy_named_this_turn else []
        display_target_enemy = target_enemy if enemy_named_this_turn else None

        if mode == "composition":
            ally_display = ", ".join(
                f"{h}({ROLE_LABELS.get(HERO_TO_ROLE.get(h), '?')})" for h in ally_team
            ) or "없음"
            # 인원수는 판마다/패치마다 달라지므로 규격을 프롬프트에 명시한다.
            roster_size = resolve_roster_size(state.get("roster_size_effective"))
            open_slots = max(1, roster_size - len(ally_team))
            context_block = f"""
이번 판은 {roster_size_label(roster_size)}이다(한 팀 {roster_size}명, 역할 정원은
{roster_role_quota_text(roster_size)}).
아군 조합(이미 정해진 인원): {ally_display}
상대 조합: {', '.join(display_enemy_team) if display_enemy_team else '없음'}
사용자를 포함해 아직 {open_slots}자리가 비어 있다. 사용자는 아직 영웅을 고르지
않았고, 그중 자기 자리를 채울 영웅을 고르는 상황이다. 남은 역할은 이미 위 역할
제한에 반영돼 있다."""
            task_instruction = """
분석 순서:
1. 상대팀에서 가장 위협적인 영웅 1~2명을 찾고, 왜 위협적인지 설명해라.
2. 아군 조합의 강점과 약점을 판단해라.
3. 위 역할 제한 안에서, 상대 위협을 줄이면서 아군 조합과 시너지가 나는 영웅을
   추천해라."""
        else:  # swap
            context_block = f"""
현재 영웅: {current_hero or '알 수 없음'}(역할: {ROLE_LABELS.get(current_hero_role, '알 수 없음')})
카운터 대상/상대 조합: {display_target_enemy or (', '.join(display_enemy_team) if display_enemy_team else '없음')}
사용자는 지금 영웅이 힘들어서 같은 역할 안에서 교체를 고민하고 있다."""
            task_instruction = """
분석 순서:
1. 왜 지금 영웅이 힘든지 사용자 질문과 상대 조합을 근거로 짧게 짚어라.
2. 위 역할 제한 안에서, 그 어려움을 해결할 수 있는 대안 영웅을 추천해라."""

        prompt = f"""
너는 오버워치2 코칭 RAG 챗봇의 "추천 영웅 카드" 생성 모듈이다.
{context_block}

사용자 질문: {state.get("message")}
질문 의도: {state.get("intent")}

역할 제한 규칙:
{role_constraint}
{task_instruction}

검색된 문서:
{state.get("retrieval_text", "")}

스킬 단축키 참고:
{get_skill_shortcut_text()}

아래 JSON 형식으로만 답해라. 다른 텍스트, 설명, 코드 펜스(```) 없이 JSON 객체 하나만
출력해라. 마크다운 문법(**, *, #, - 등)을 쓰지 마라.

{{
  "intro": "채팅에 보여줄 짧은 설명 ({intro_length_instruction}, 마크다운 금지, 줄바꿈은 \\n으로)",
  "recommended_heroes": [{{"hero": "영웅 이름", "note": "10자 내외 짧은 이유"}}]{SUGGESTED_QUESTIONS_INLINE_SCHEMA_LINE if is_simple_style else ""}
}}

규칙:
1. recommended_heroes는 위 역할 제한 목록 안에서 3~4명 골라라. 현재 영웅이나
   이미 정해진 아군 인원은 다시 추천하지 마라.
2. note는 왜 추천하는지 짧게 표현해라(예: "진입 저지에 강함", "시너지 좋음",
   "생존력이 좋아 안정적").
3. 검색된 문서에 없는 상성이나 수치는 지어내지 마라. 퍼센트, 승률, 티어
   통계는 절대 언급하지 마라. 확실하지 않으면 추측하지 말고 "문서 기준으로는
   확인되지 않는다"고 답해라.
4. intro는 자연스러운 한국어 문장으로 설명해라. 영웅 이름을 나열하는 형태로
   쓰지 마라 — 그건 카드가 이미 보여준다.
5. "[문서 1]" 같은 출처 표시나 "문서에 따르면" 같은 인용 표현은 절대 쓰지 마라.
{SUGGESTED_QUESTIONS_INLINE_RULES if is_simple_style else ""}
"""

        raw_text = call_llm_text(llm, prompt)
        if isinstance(raw_text, list):
            raw_text = "\n".join(str(item) for item in raw_text)
        elif not isinstance(raw_text, str):
            raw_text = str(raw_text)

        parsed = safe_json_loads(raw_text, default={})
        if not isinstance(parsed, dict):
            parsed = {}

        intro = sanitize_answer_for_user(str(parsed.get("intro") or ""), keep_dash_bullets=False)
        recommended = _clean_matchup_hero_list(
            parsed.get("recommended_heroes"), exclude_heroes, allowed_set
        )

        if not intro and not recommended:
            logger.warning("[RECOMMEND CARD] 파싱 실패, 안내 문구로 대체. raw=%s", raw_text)
            intro = "추천 정보를 불러오지 못했습니다. 다시 질문해 주세요."

        recommend_card = None
        if recommended:
            recommend_card = {
                "mode": mode,
                "heroes": recommended,
            }

        result = {
            "answer": intro,
            "recommend_card": recommend_card,
            "recommendation_type": "recommend_card",
            "recommended_heroes": [h["hero"] for h in recommended],
        }
        if is_simple_style:
            result["suggested_questions"] = extract_inline_suggested_questions(parsed)
        return result

    except Exception as exc:
        logger.exception("generate_recommend_card_node 오류: %s", exc)
        return {"error": str(exc)}


def generate_suggested_questions_node(state: ChatbotGraphState) -> ChatbotGraphState:
    if state.get("error"):
        return state

    try:
        _chatbot, _retriever, llm = chatbot_service.get_chatbot_components()

        # 사용자가 플레이할 수 있는 역할 범위. 없으면 답변에 등장한 아군 팀원을
        # 사용자가 플레이하는 것처럼 질문을 만든다.
        role_filter = state.get("role_filter")
        current_hero_role = state.get("current_hero_role")
        role_scope = role_filter if parse_role_filter(role_filter) else current_hero_role
        if parse_role_filter(role_scope):
            role_scope_line = (
                f"- 사용자가 플레이할 역할: {role_filter_label(role_scope)} "
                f"(이 역할 영웅: {', '.join(heroes_for_role_filter(role_scope))})"
            )
            role_scope_rule = (
                f"- 사용자는 {role_filter_label(role_scope)}다. "
                "\"OO으로 ~하는 법\"처럼 사용자가 직접 그 영웅을 플레이하는 형태의 질문은 "
                "반드시 위 역할 영웅으로만 만들어라. 아군 팀원(다른 역할) 영웅은 "
                "\"그 팀원과 어떻게 맞출지\" 같은 협력 형태로만 쓸 수 있고, 사용자가 그 "
                "영웅을 플레이하는 것처럼 쓰면 안 된다."
            )
        else:
            role_scope_line = "- 사용자가 플레이할 역할: 아직 모름"
            role_scope_rule = (
                "- 사용자의 역할이 아직 정해지지 않았으므로, 특정 영웅을 사용자가 "
                "플레이 중인 것처럼 단정하는 질문은 만들지 마라."
            )

        prompt = f"""
너는 오버워치 코칭 챗봇 UI의 "빠른 질문 버튼" 생성기다.

역할 정의:
- 사용자가 AI의 답변을 읽은 뒤 다음으로 보낼 법한 메시지를 예측해서 버튼 텍스트로 만든다.
- 버튼을 클릭하면 그 텍스트가 그대로 사용자 입력창에 입력된다.
- 즉, 생성하는 문장은 반드시 "사용자가 AI에게 보내는 질문/요청" 형태여야 한다.

현재 컨텍스트:
- 현재 영웅: {state.get("current_hero")}
{role_scope_line}
- 아군 팀원(사용자가 플레이하는 영웅이 아님): {', '.join(state.get("ally_team") or []) or "없음"}
- 카운터 대상: {state.get("target_enemy")}
- 맵: {state.get("map_name")}
- 공격/수비: {state.get("side")}
- 스탯 입력 여부: {"있음" if state.get("has_stats") else "없음"}
- 사용자가 보낸 메시지: {state.get("message")}
- AI 답변 요약: {(state.get("answer") or "")[:200]}

JSON 형식으로만 답해라.

{{
  "suggested_questions": [
    "버튼 텍스트 1",
    "버튼 텍스트 2",
    "버튼 텍스트 3"
  ]
}}

규칙:
- 반드시 사용자 1인칭 시점의 짧은 질문/요청문으로 작성.
- AI가 사용자에게 묻는 형태 절대 금지.
- AI가 추가 설명하는 형태 절대 금지.
- 이번 답변 내용과 자연스럽게 이어지는 흐름으로 작성.
- 버튼 라벨이므로 15자 이내의 짧은 문장.
- 문서, 출처, 내부 시스템 용어 금지.
- 카운터 대상이 있으면, 추천 질문 3개 중 최소 1개는 반드시 그 카운터 대상과 관련된 질문으로 작성해라.
- 사용자가 언급하지 않은 상대 영웅 이름을 새로 만들지 마라.
- 예를 들어 카운터 대상이 둠피스트라면 겐지, 트레이서, 윈스턴 같은 다른 영웅을 임의로 넣지 마라.
{role_scope_rule}
"""

        text = call_llm_text_creative(llm, prompt)
        parsed = safe_json_loads(text, default={})
        questions = parsed.get("suggested_questions", [])

        if not isinstance(questions, list):
            questions = []
        questions = [str(q).strip() for q in questions if str(q).strip()]

        if len(questions) < 3:
            questions = build_fallback_suggested_questions(state)

        return {"suggested_questions": questions[:3]}

    except Exception as exc:
        logger.exception("generate_suggested_questions_node 오류: %s", exc)
        return {"suggested_questions": build_fallback_suggested_questions(state)}


def build_fallback_suggested_questions(state: ChatbotGraphState) -> List[str]:
    target_enemy = state.get("target_enemy")
    current_hero = state.get("current_hero")
    map_name = state.get("map_name")

    if target_enemy and current_hero:
        return [
            f"{target_enemy} 진입 막는 법은?",
            f"{current_hero} 위치 잡는 법은?",
            f"{target_enemy} 상대로 궁 타이밍은?",
        ]

    if state.get("has_stats"):
        return [
            f"{current_hero or '현재 영웅'} 데스 줄이는 법 알려줘",
            "딜량 더 올리는 방법은?",
            "이 스탯이면 영웅 바꿔야 해?",
        ]

    if state.get("intent") == "performance_improve" and current_hero:
        return [
            f"{current_hero} 덜 죽는 운영법 알려줘",
            f"{current_hero} 딜각 잡는 위치는?",
            f"{current_hero} 스킬 순서 알려줘",
        ]

    if state.get("intent") == "swap":
        return [
            "이 조합에서 가장 좋은 픽은?",
            "상대 조합 대응 픽 나눠줘",
            f"{map_name or '이 맵'}에서 딜러 추천해줘",
        ]

    return [
        "지금 먼저 할 일은?",
        "스킬 순서 알려줘",
        "포지션 잡는 법은?",
    ]


def compute_final_focus_heroes(state: ChatbotGraphState) -> List[str]:
    """이번 답변이 실제로 다룬 영웅을 다음 턴 focus_heroes로 남긴다. 추천 카드/
    전략 판단이 만든 recommended_heroes(여러 영웅 추천)를 최우선으로 삼고,
    그 다음은 자기 영웅(단일 설명), 그 다음은 질문 자체의 주제 영웅 순이며,
    영웅 중심 답변이 아니면 빈 배열로 남긴다."""
    recommended = state.get("recommended_heroes") or []
    if recommended:
        return list(dict.fromkeys(recommended))
    current_hero = state.get("current_hero")
    if current_hero:
        return [current_hero]
    return list(state.get("focus_heroes") or [])


def format_response_node(state: ChatbotGraphState) -> ChatbotGraphState:
    if state.get("error"):
        return {"result": {"error": state["error"]}}

    answer_style = state.get("answer_style") or "detailed"
    # 특전 답변은 "- " 설명 줄이 형식의 일부라 스타일과 무관하게 보존한다
    # (generate_answer_node의 format_perk_answer가 붙여둔 줄이다).
    answer = sanitize_answer_for_user(
        state.get("answer", ""),
        keep_dash_bullets=answer_style == "simple" or bool(state.get("is_perk_question")),
    )

    # 되묻지 않고 조합만 보고 답했으면 판단 근거를 한 줄 밝혀 정정받을 수 있게 한다.
    role_basis_note = (state.get("role_basis_note") or "").strip()
    if role_basis_note and answer:
        answer = f"{answer}\n\n{role_basis_note}"

    # 이번 답변이 다룬 영웅을 다음 턴의 focus_heroes로 남긴다. 영웅 중심 답변이
    # 아니면 빈 배열로 덮어써 이전 주제가 눌어붙지 않게 한다.
    context_patch = {
        **state.get("context_patch", {}),
        "focus_heroes": compute_final_focus_heroes(state),
    }

    # 버튼이 붙는 답변에는 추천 질문을 내보내지 않는다. "간단히"는 답변 노드가
    # 함께 받아오므로 여기서 한 번 더 걸러준다.
    choice_buttons = state.get("choice_buttons", [])
    suggested_questions = [] if choice_buttons else state.get("suggested_questions", [])

    return {
        "result": {
            "answer": answer,
            # 원문 메시지가 비어 있는 턴에도 복원된 실제 질문을 로그용으로 내려준다.
            "message": state.get("message"),
            "intent": state.get("intent"),
            "recommendation_type": state.get("recommendation_type"),
            "recommended_heroes": state.get("recommended_heroes", []),
            "suggested_questions": suggested_questions,
            "choice_buttons": choice_buttons,
            "context_patch": context_patch,
            "has_stats": state.get("has_stats", False),
            "answer_style": answer_style,
            "matchup_card": state.get("matchup_card"),
            "recommend_card": state.get("recommend_card"),
        }
    }

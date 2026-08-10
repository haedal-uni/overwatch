"""그래프 중단 노드 — 잡담 응답, 검색 질의 생성, 문서 검색, 전략 판단.

off_topic_response_node는 오버워치와 무관한 잡담을 LLM 호출 없이 고정 문구로
끊어내고, build_retrieval_queries_node가 확정된 컨텍스트로 여러 개의 검색
질의를 만들면 retrieve_docs_node가 그것들을 병렬로 검색한다.
judge_strategy_node는 검색 결과를 바탕으로 "영웅을 바꿔야 하는지"를 먼저
판단해 답변 노드가 쓸 재료를 만든다("간단히" 스타일에서는 생략된다).
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from chat.domain.answer_format import _format_stat_text
from chat.rag import components as chatbot_service
from chat.graph.state import ChatbotGraphState
from chat.domain.heroes import (
    ROLE_HEROES,
    ROLE_LABELS,
)
from chat.rag.doc_sections import get_hero_perk_section
from chat.rag.llm_utils import (
    call_llm_text,
    document_to_dict,
    retrieve_documents,
    safe_json_loads,
)

logger = logging.getLogger(__name__)


OFF_TOPIC_ANSWER = (
    "저는 오버워치2 게임 코칭만 도와드릴 수 있어요. "
    "상대 영웅 대처법, 팀 조합, 맵 운영, 개인 플레이 개선처럼 "
    "오버워치2 게임 상황과 관련된 질문을 해주세요."
)


def off_topic_response_node(state: ChatbotGraphState) -> ChatbotGraphState:
    """오버워치2와 무관한 메시지에는 LLM을 호출하지 않고 항상 같은 고정
    문구로 응답한다 — 관련 없는 주제에 LLM이 그럴듯하게 답을 지어내는 것을
    막기 위함이다."""
    context_patch = {
        **state.get("context_patch", {}),
    }

    return {
        "answer": OFF_TOPIC_ANSWER,
        "recommendation_type": "off_topic",
        "recommended_heroes": [],
        "choice_buttons": [],
        "suggested_questions": [],
        # 고정 문구 응답에는 판단 근거 문구를 붙이지 않는다.
        "role_basis_note": "",
        "context_patch": context_patch,
        "result": {
            "answer": OFF_TOPIC_ANSWER,
            "intent": "off_topic",
            "recommendation_type": "off_topic",
            "recommended_heroes": [],
            "suggested_questions": [],
            "choice_buttons": [],
            "context_patch": context_patch,
            "has_stats": False,
        },
    }


def resolve_perk_hero(state: ChatbotGraphState) -> Any:
    """특전 질문이 다루는 영웅. 자기 영웅이 있으면 그 영웅, 아니면 주제 영웅."""
    focus_heroes = state.get("focus_heroes") or []
    return state.get("current_hero") or (focus_heroes[0] if focus_heroes else None)


def build_retrieval_queries_node(state: ChatbotGraphState) -> ChatbotGraphState:
    message = state.get("message", "")
    intent = state.get("intent") or "general"
    target_enemy = state.get("target_enemy")
    current_hero = state.get("current_hero")
    map_name = state.get("map_name")
    side = state.get("side")
    enemy_team = state.get("enemy_team", [])
    role_filter = state.get("role_filter")
    high_threat_enemy = state.get("high_threat_enemy")
    has_stats = state.get("has_stats", False)
    # 이번 턴에 적이 언급되지 않았으면 적 기반 쿼리를 만들지 않는다.
    enemy_named_this_turn = state.get("enemy_named_this_turn", False)

    side_text = "공격" if side == "attack" else "수비" if side == "defense" else ""
    queries = [message]

    if high_threat_enemy and enemy_named_this_turn:
        queries.append(f"{high_threat_enemy} 카운터 영웅 상대법 견제")
        queries.append(f"{high_threat_enemy} 약점 스킬 무력화")

    if target_enemy and target_enemy != high_threat_enemy and enemy_named_this_turn:
        queries.append(f"{target_enemy} 카운터 상대법 견제 방법")
        queries.append(f"{target_enemy} 약점 스킬 무력화")

    if current_hero:
        queries.append(f"{current_hero} 운영법 스킬 사용법 딜 넣는 법 생존법")

    if map_name:
        queries.append(f"{map_name} {side_text} 맵 운영법 포지션 영웅")
        if current_hero:
            queries.append(f"{map_name} {side_text} {current_hero} 포지션 운영")
        if target_enemy and enemy_named_this_turn:
            queries.append(f"{map_name} {side_text} {target_enemy} 대응법")

    if enemy_team and enemy_named_this_turn:
        queries.append(f"상대 조합 {' '.join(enemy_team)} 대응 영웅 추천")

    if role_filter and role_filter != "all":
        queries.append(f"{ROLE_LABELS.get(role_filter)} 역할 카운터 영웅 추천")

    ally_team = state.get("ally_team", [])
    if state.get("is_team_comp_question") and ally_team:
        queries.append(f"{' '.join(ally_team)} 조합 시너지 영웅 추천")
        if enemy_team and enemy_named_this_turn:
            queries.append(f"{' '.join(ally_team)} 조합으로 {' '.join(enemy_team)} 상대하기")

    if intent == "performance_improve":
        queries.append(f"{current_hero or ''} 딜량 올리는 방법 포지션 타이밍 스킬")
        queries.append(f"{current_hero or ''} 생존법 죽지 않는 운영")
        if has_stats:
            queries.append(f"{current_hero or ''} 데스 줄이기 생존 운영")
    elif intent == "swap":
        if enemy_named_this_turn and enemy_team:
            queries.append(
                f"{map_name or ''} {side_text} 상대 {' '.join(enemy_team)} 상대로 "
                f"{current_hero or ''} 말고 추천 영웅"
            )
        else:
            queries.append(f"{map_name or ''} {side_text} {current_hero or ''} 말고 추천 영웅")
    elif intent == "counter" and enemy_named_this_turn:
        queries.append(f"{target_enemy or high_threat_enemy or ''} 카운터 픽 대응법")
    elif intent == "map_strategy":
        queries.append(f"{map_name or ''} {side_text} 거점 수비 포지션 운영")
    elif intent == "situation":
        # 압박 상황은 현재 영웅으로 버티는 법이 핵심이라 상대와 함께 엮는다.
        if enemy_named_this_turn and (target_enemy or high_threat_enemy):
            queries.append(f"{target_enemy or high_threat_enemy} 압박 대처법 운영 {current_hero or ''}")
        else:
            queries.append(f"{current_hero or ''} 위기 상황 대처법 생존 운영")

    # 특전 질문은 그 영웅의 특전 절이 반드시 있어야 답을 만들 수 있다. 실제
    # 절 원문은 retrieve_docs_node가 문서에서 직접 꺼내 얹지만(perk_hero_section),
    # 특전과 함께 볼 운영 맥락을 위해 검색도 같이 한다.
    if state.get("is_perk_question"):
        perk_hero = resolve_perk_hero(state)
        if perk_hero:
            queries.append(f"{perk_hero} 특전 보조 특전 주요 특전")

    unique_queries = [q.strip() for q in dict.fromkeys(queries) if q.strip()]
    logger.info("[RAG 검색 쿼리] %s", unique_queries)
    return {"retrieval_queries": unique_queries}


def retrieve_docs_node(state: ChatbotGraphState) -> ChatbotGraphState:
    if state.get("error"):
        return state

    t0 = time.time()
    try:
        _chatbot, retriever, _llm = chatbot_service.get_chatbot_components()

        queries = state.get("retrieval_queries", []) or []

        # 검색어마다 로컬 임베딩 계산이 필요해 순차 실행하면 지연이 쌓인다.
        # 스레드로 동시 실행하되, 결과는 queries 순서대로 병합해 순차 실행과
        # 동일한 dedup/절단 결과를 유지한다.
        results_by_query: List[List[Any]] = [[] for _ in queries]
        if len(queries) > 1:
            with ThreadPoolExecutor(max_workers=min(6, len(queries))) as executor:
                future_to_idx = {
                    executor.submit(retrieve_documents, retriever, query): idx
                    for idx, query in enumerate(queries)
                }
                for future in as_completed(future_to_idx):
                    results_by_query[future_to_idx[future]] = future.result()
        elif queries:
            results_by_query[0] = retrieve_documents(retriever, queries[0])

        all_docs: List[Dict[str, Any]] = []
        seen_contents: set = set()

        for query, docs in zip(queries, results_by_query):
            for doc in docs:
                doc_dict = document_to_dict(doc)
                content = doc_dict.get("content", "").strip()
                if not content:
                    continue
                content_key = content[:500]
                if content_key in seen_contents:
                    continue
                seen_contents.add(content_key)
                doc_dict["query"] = query
                all_docs.append(doc_dict)

        # 특전 질문에는 그 영웅의 특전 절을 문서에서 직접 꺼내 맨 앞에 얹는다.
        # 벡터 검색만으로는 이 절이 상위 k에 못 드는 일이 흔하다 — 절 본문에
        # 영웅 이름이 없고 51명의 특전 절이 서로 거의 같은 문장이라 다른 영웅의
        # 특전이나 같은 영웅의 다른 절에 밀린다(실측: "오리사 특전"은 상위 10개
        # 안에 오리사 특전 절이 없었다). 그 결과 "특전 데이터가 없는 영웅"이라는
        # 엉뚱한 답이 나갔다.
        if state.get("is_perk_question"):
            perk_hero = resolve_perk_hero(state)
            perk_section = get_hero_perk_section(perk_hero)
            if perk_section:
                all_docs.insert(0, {
                    "content": f"## {perk_hero}\n{perk_section}",
                    "metadata": {"H2": perk_hero, "H3": "특전 데이터"},
                    "query": f"{perk_hero} 특전 데이터(원문 직접 조회)",
                })
                logger.info("[PERK SECTION] %s 특전 절을 검색 결과 맨 앞에 추가", perk_hero)
            else:
                logger.info("[PERK SECTION] %s의 특전 절이 문서에 없음", perk_hero)

        all_docs = all_docs[:12]
        numbered_docs = [{**doc, "doc_id": idx} for idx, doc in enumerate(all_docs, start=1)]

        retrieval_text = "\n\n".join(
            f"[문서 {doc['doc_id']}]\n"
            f"검색어: {doc.get('query')}\n"
            f"metadata: {json.dumps(doc.get('metadata', {}), ensure_ascii=False)}\n"
            f"내용:\n{doc.get('content')}"
            for doc in numbered_docs
        )

        logger.info(
            "[TIMING] retrieve_docs_node: %.2fs (queries=%d)",
            time.time() - t0, len(queries),
        )
        return {"retrieved_docs": numbered_docs, "retrieval_text": retrieval_text}

    except Exception as exc:
        logger.exception("retrieve_docs_node 오류: %s", exc)
        return {"error": str(exc)}


def judge_strategy_node(state: ChatbotGraphState) -> ChatbotGraphState:
    if state.get("error"):
        return state

    try:
        _chatbot, _retriever, llm = chatbot_service.get_chatbot_components()

        intent = state.get("intent") or "general"
        role_filter = state.get("role_filter") or "all"
        role_filter_explicit = state.get("role_filter_explicit", False)
        current_hero = state.get("current_hero")
        current_hero_role = state.get("current_hero_role")
        target_enemy = state.get("target_enemy")
        high_threat_enemy = state.get("high_threat_enemy")
        map_name = state.get("map_name")
        side = state.get("side")
        enemy_team = state.get("enemy_team", [])
        has_stats = state.get("has_stats", False)
        # 이번 턴에 적이 실제로 언급되지 않았다면 프롬프트에 "확정된 상대"로 넘기지 않는다.
        enemy_named_this_turn = state.get("enemy_named_this_turn", False)
        # 이번 메시지에서 확인되지 않은 current_hero. 역할을 제한하면 옛 영웅
        # 기준으로 답이 좁혀진다.
        current_hero_uncertain = state.get("current_hero_uncertain", False)

        # 이번 턴에 명시된 역할 필터가 최우선이다(덮어쓰면 선택과 다른 역할이 나간다).
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
        role_label = ROLE_LABELS.get(role_filter, "전체")

        enemy_stats = state.get("enemy_stats") or {}
        my_stats = state.get("my_stats") or {}
        my_team_stats = state.get("my_team_stats") or {}

        enemy_stat_text = _format_stat_text(enemy_stats, "상대팀")
        my_stat_text = _format_stat_text(my_stats, "나")
        team_stat_text = _format_stat_text(my_team_stats, "우리팀")
        stat_summary = "\n".join(filter(None, [enemy_stat_text, my_stat_text, team_stat_text]))

        if current_hero_uncertain:
            # 영웅이 불확실하면 역할로 영웅 풀을 제한하지 않는다.
            role_constraint = (
                "사용자가 지금 어떤 영웅을 플레이 중인지 이번 메시지만으로는 확실하지 않다. "
                "이전 대화에서 다른 영웅 얘기가 있었더라도, 이번 질문은 그 영웅과 무관한 "
                "일반적인 팀 조합/전략 질문일 수 있다. 특정 영웅을 계속 플레이 중이라고 "
                "단정하지 말고, 상황에 맞는 영웅이나 조합을 역할 제한 없이 자유롭게 제안해라."
            )
        elif role_filter in ROLE_HEROES:
            role_constraint = (
                f"추천 영웅은 반드시 {role_label} 역할만: {', '.join(ROLE_HEROES[role_filter])}\n"
                f"이 목록 밖의 영웅은 어떤 이유로도 추천 불가."
            )
        elif current_hero_role and current_hero_role in ROLE_HEROES:
            role_constraint = (
                f"사용자는 {ROLE_LABELS.get(current_hero_role)} 역할({current_hero})을 플레이 중이다.\n"
                f"추천 영웅은 반드시 같은 {ROLE_LABELS.get(current_hero_role)} 역할만:\n"
                f"{', '.join(ROLE_HEROES[current_hero_role])}\n"
                f"팀 문제·힐 부족·어떤 이유가 있어도 이 목록 밖의 영웅은 절대 추천 불가."
            )
        elif role_filter == "all" and role_filter_explicit:
            # "전체"를 직접 골랐을 때만 이 분기를 탄다. 기본값 "all"까지 걸리면
            # 영웅 추천이 필요 없는 질문에도 역할별 추천이 붙는다.
            role_constraint = (
                "사용자가 '전체' 역할을 선택했다. 특정 역할로 제한하지 말고, "
                "탱커/딜러/힐러 각 역할에서 이 상황에 대응할 수 있는 영웅을 "
                "최소 1명씩 골고루 골라 역할별로 균형 있게 추천해라. "
                "한 역할에만 치우친 추천은 하지 마라."
            )
        else:
            role_constraint = "역할 제한 없음. 상황에 맞는 영웅을 자유롭게 고려해라."

        if current_hero_uncertain:
            allowed_hero_set = None
        elif role_filter in ROLE_HEROES:
            allowed_hero_set = set(ROLE_HEROES[role_filter])
        elif current_hero_role and current_hero_role in ROLE_HEROES:
            allowed_hero_set = set(ROLE_HEROES[current_hero_role])
        else:
            allowed_hero_set = None

        side_text = "공격" if side == "attack" else "수비" if side == "defense" else "알 수 없음"

        # 이번 턴에 언급되지 않은 상대 정보는 LLM에게 "없음"으로 보여준다.
        display_target_enemy = target_enemy if enemy_named_this_turn else None
        display_high_threat = high_threat_enemy if enemy_named_this_turn else None
        display_enemy_team = enemy_team if enemy_named_this_turn else []

        prompt = f"""
너는 오버워치2 코칭 RAG 챗봇의 전략 판단 모듈이다.
아래 상황을 보고 이번 질문에 대한 전략적 판단을 내려서 JSON으로만 답해라.

=== 절대 규칙 ===
{role_constraint}
=================

사용자 질문: {state.get("message")}
질문 의도(intent): {intent}
스탯 입력 여부: {"있음" if has_stats else "없음"}

현재 영웅: {f"{current_hero} (이전 대화 기준 — 이번 질문에서 다시 언급되지 않아 지금도 이 영웅인지 불확실함)" if current_hero_uncertain else f"{current_hero or '없음'} (역할: {ROLE_LABELS.get(current_hero_role, '알 수 없음') if current_hero_role else '알 수 없음'})"}
카운터 대상: {display_target_enemy or "없음 (이번 질문에서 특정 상대를 언급하지 않음)"}
가장 위협적인 적: {display_high_threat or "없음"}
맵: {map_name or "없음"} / 공격-수비: {side_text}
상대 조합: {', '.join(display_enemy_team) if display_enemy_team else "없음"}
아군 조합: {', '.join(state.get("ally_team") or []) or "없음"}

스탯 정보:
{stat_summary or "없음"}

참고 문서:
{state.get("retrieval_text", "")}

아래 JSON 형식으로만 답해라.

{{
  "recommendation_type": "counter_pick | hero_swap | stay_and_adapt | map_strategy | performance_tips | stat_analysis | general 중 하나",
  "recommended_heroes": ["영웅1", "영웅2"],
  "strategy_reason": "이런 영웅/방향을 추천하는 이유를 1~2문장으로"
}}

규칙:
1. recommended_heroes는 위 절대 규칙의 목록 안에서만 골라라.
2. intent가 "stay" 또는 "performance_improve"이면 recommended_heroes는 빈 배열,
   recommendation_type은 각각 "stay_and_adapt", "performance_tips"로 해라.
3. 스탯이 있고 intent가 "performance_improve"이면 recommendation_type을 "stat_analysis"로 해도 된다.
4. intent가 "map_strategy"이면 recommendation_type을 "map_strategy"로 해라.
5. intent가 "swap"이면 recommendation_type을 "hero_swap"으로 하고, recommended_heroes를
   빈 배열로 두지 마라. 사용자가 같은 역할 안에서 교체를 고민하고 있으므로, 상황에 맞는
   같은 역할의 다른 영웅을 최소 1명 이상 골라 추천하고, strategy_reason에 "교체가 나은지
   유지가 나은지"에 대한 명확한 판단과 근거를 적어라. 정보가 부족해도 일반적인 운영
   지식을 바탕으로 판단을 내려라.
6. 그 외에 정보가 부족하면 recommended_heroes를 빈 배열, recommendation_type을 "general"로 해라.
7. recommended_heroes에는 한국어 영웅 이름만 적어라.
8. "카운터 대상"이 "없음"으로 표시된 경우, 참고 문서에 등장하는 영웅 이름을 사용자가 실제로
   마주한 상대인 것처럼 strategy_reason에 단정해서 쓰지 마라. 문서는 일반 자료일 뿐이다.
"""

        text = call_llm_text(llm, prompt)
        parsed = safe_json_loads(text, default={})

        if not isinstance(parsed, dict):
            parsed = {}

        recommended_heroes = parsed.get("recommended_heroes", [])
        if not isinstance(recommended_heroes, list):
            recommended_heroes = []
        recommended_heroes = [str(h).strip() for h in recommended_heroes if str(h).strip()]

        if allowed_hero_set is not None:
            filtered = [h for h in recommended_heroes if h in allowed_hero_set]
            if len(filtered) < len(recommended_heroes):
                logger.warning(
                    "[ROLE FILTER] LLM이 허용 범위 밖 영웅을 추천함 — 제거: %s",
                    set(recommended_heroes) - allowed_hero_set,
                )
            recommended_heroes = filtered

        recommendation_type = parsed.get("recommendation_type") or intent
        strategy_reason = parsed.get("strategy_reason", "")

        return {
            "recommendation_type": recommendation_type,
            "recommended_heroes": recommended_heroes,
            "strategy_reason": strategy_reason,
        }

    except Exception as exc:
        logger.exception("judge_strategy_node 오류: %s", exc)
        return {
            "recommendation_type": state.get("intent") or "general",
            "recommended_heroes": [],
            "strategy_reason": "",
            "error": str(exc),
        }

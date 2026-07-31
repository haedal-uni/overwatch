"""LangGraph 그래프 조립과 실행 진입점(`chat/chatbot_graph.py`의 후신).

이 파일은 원래 4,400줄짜리 단일 모듈이었다 — 영웅 데이터, 규칙 기반 분류,
캐시 응답, 모든 노드, 프롬프트가 한 곳에 있었다. 지금은 주제별 패키지로
나뉘어 있고 여기에는 "노드를 어떤 순서로 잇는가"와 실행 함수만 남아 있다.
패키지 구성 설명은 저장소 루트의 `chat_모듈_구조.md` 참고.

    chat/domain (영웅 데이터·규칙·프롬프트)   chat/rag (검색·LLM)
        ↑                                          ↑
    chat/graph/state · nodes_context → nodes_retrieval → nodes_answer
        ↑
    chat/graph/pipeline (이 파일: 그래프 배선)
    chat/graph/canned   (그래프를 우회하는 캐시 응답)

아래 재수출(re-export)은 노드/규칙/영웅 데이터의 주요 이름을 이 모듈 하나로
모아준다 — `views.py`처럼 파이프라인 바깥에서 부르는 쪽이 내부 모듈 경로를
일일이 알 필요가 없게 하려는 것이다.
"""

import logging
import time
from typing import Any, Dict, List, Optional

from langgraph.graph import END, START, StateGraph

# --- 재수출: 파이프라인 바깥(views 등)에서 한 곳으로 가져다 쓰기 위한 창구 ---
from chat.domain.answer_format import (  # noqa: F401
    _format_stat_text,
    extract_inline_suggested_questions,
    sanitize_answer_for_user,
)
from chat.graph.canned import (  # noqa: F401
    CANNED_COMPOSITION_ALLY_LIST,
    CANNED_COMPOSITION_ENEMY_LIST,
    CANNED_COUNTER_HERO,
    CANNED_MAP_NAME,
    CANNED_MAP_SIDE,
    CANNED_STAT_DAMAGE,
    CANNED_STAT_DEATHS,
    CANNED_STAT_HERO,
    CANNED_STAT_KILLS,
    CANNED_STAY_ENEMY,
    CANNED_STAY_HERO,
    match_canned_topic,
    try_canned_shortcut,
)
from chat.graph.state import ChatbotGraphState  # noqa: F401
from chat.domain.heroes import (  # noqa: F401
    HERO_ALIASES,
    HERO_NAME_TO_CANONICAL,
    HERO_TO_ROLE,
    HEROES,
    MAPS,
    OVERWATCH_SKILL_SHORTCUTS,
    ROLE_HEROES,
    ROLE_LABELS,
    _validate_hero_tables,
    find_all_heroes,
    find_first_hero,
    find_map,
    find_side,
    get_hero_role,
    get_skill_shortcut_text,
    hero_mentioned_in_text,
    heroes_for_role_filter,
    josa_eul_reul,
    make_role_filter,
    normalize_hero_name,
    parse_role_filter,
    role_filter_label,
)
from chat.domain.intent_rules import (  # noqa: F401
    ENEMY_MENTION_PATTERNS,
    ENEMY_ROLE_FOCUS_LABELS,
    ENEMY_ROLE_FOCUS_WORDS,
    CURRENT_META_ROSTER_SIZE,
    ROLE_CLARIFICATION_INTENTS,
    ROLE_NARROWING_MAX_AGE_SECONDS,
    ROSTER_ROLE_RANGES,
    TEAM_COMP_ROLE_QUOTA,
    alternate_roster_size,
    analyze_team_comp,
    can_be_roster_size,
    detect_roster_size,
    detect_situation,
    detect_stat_input,
    detect_stay_with_named_hero,
    detect_wants_to_keep_hero,
    extract_ally_team,
    extract_enemy_team,
    find_ally_complaint_hero,
    find_enemy_mentioned_hero,
    find_enemy_role_focus,
    find_performance_comparison_heroes,
    find_self_comparison_heroes,
    find_synergy_ally_heroes,
    hero_mentioned_as_current_hero,
    infer_current_hero,
    infer_intent_by_rule,
    infer_missing_role_from_team_comp,
    infer_target_enemy,
    is_ellipsis_followup,
    is_hero_usage_guide_question,
    is_performance_comparison_question,
    normalize_hero_candidate,
    resolve_roster_size,
    role_filter_from_text,
    roster_role_quota_text,
    roster_size_button_label,
    roster_size_label,
    should_ask_role_filter,
    wants_composition_recommendation,
)
from chat.rag.llm_utils import (  # noqa: F401
    call_llm_text,
    call_llm_text_creative,
    document_to_dict,
    retrieve_documents,
    safe_json_loads,
)
from chat.graph.nodes_answer import (
    build_fallback_suggested_questions,  # noqa: F401
    compute_final_focus_heroes,  # noqa: F401
    format_response_node,
    generate_answer_node,
    generate_matchup_answer_node,
    generate_recommend_card_node,
    generate_suggested_questions_node,
)
from chat.graph.nodes_context import (
    clarify_focus_hero_node,
    clarify_role_filter_node,
    llm_parse_context_node,
    merge_context_node,
    parse_stats_from_text_node,
    should_reset_enemy_context,  # noqa: F401
    validate_input_node,
)
from chat.graph.nodes_retrieval import (
    OFF_TOPIC_ANSWER,  # noqa: F401
    build_retrieval_queries_node,
    judge_strategy_node,
    off_topic_response_node,
    retrieve_docs_node,
)
from chat.domain.prompts import (  # noqa: F401
    SUGGESTED_QUESTIONS_INLINE_RULES,
    SUGGESTED_QUESTIONS_INLINE_SCHEMA_LINE,
)

logger = logging.getLogger(__name__)


def route_after_validation(state: ChatbotGraphState) -> str:
    return "format_response" if state.get("error") else "parse_stats"

def route_after_parse_stats(state: ChatbotGraphState) -> str:
    return "format_response" if state.get("error") else "llm_parse_context"

def route_after_context_merge(state: ChatbotGraphState) -> str:
    if state.get("intent") == "off_topic":
        return "off_topic_response"
    # 기준 영웅 확정이 역할 확인보다 우선이다 — 영웅을 모르면 역할을 알아도
    # 답을 만들 수 없다.
    if state.get("needs_focus_hero_clarify"):
        return "clarify_focus_hero"
    if should_ask_role_filter(state):
        return "clarify_role_filter"
    return "build_retrieval_queries"

def route_after_retrieve(state: ChatbotGraphState) -> str:
    if state.get("error"):
        return "format_response"
    # 카드 노드는 목록을 직접 만들므로 judge_strategy 호출을 건너뛴다.
    if state.get("matchup_subject"):
        return "generate_matchup_answer"
    if state.get("recommend_card_mode"):
        return "generate_recommend_card"
    # "간단히"는 호출 수를 줄인다 — generate_answer가 state만으로 같은 판단을
    # 내릴 수 있다. "자세히"는 기존 방식을 유지한다.
    if state.get("answer_style") == "simple":
        return "generate_answer"
    return "judge_strategy"

def route_after_judge(state: ChatbotGraphState) -> str:
    return "format_response" if state.get("error") else "generate_answer"

def route_after_generate(state: ChatbotGraphState) -> str:
    if state.get("error"):
        return "format_response"
    # 정정 버튼이 붙는 턴은 추천 질문을 생략한다(다음 답변에는 정상 표시된다).
    if state.get("choice_buttons"):
        return "format_response"
    # "간단히"는 답변 노드가 추천 질문을 함께 받아오므로, 3개가 확보됐으면
    # 별도 호출 없이 끝낸다.
    if state.get("answer_style") == "simple" and len(state.get("suggested_questions") or []) >= 3:
        return "format_response"
    return "generate_suggested_questions"


def build_chatbot_graph():
    graph = StateGraph(ChatbotGraphState)

    graph.add_node("validate_input", validate_input_node)
    graph.add_node("parse_stats", parse_stats_from_text_node)
    graph.add_node("llm_parse_context", llm_parse_context_node)
    graph.add_node("merge_context", merge_context_node)
    graph.add_node("clarify_role_filter", clarify_role_filter_node)
    graph.add_node("clarify_focus_hero", clarify_focus_hero_node)
    graph.add_node("off_topic_response", off_topic_response_node)
    graph.add_node("build_retrieval_queries", build_retrieval_queries_node)
    graph.add_node("retrieve_docs", retrieve_docs_node)
    graph.add_node("judge_strategy", judge_strategy_node)
    graph.add_node("generate_answer", generate_answer_node)
    graph.add_node("generate_matchup_answer", generate_matchup_answer_node)
    graph.add_node("generate_recommend_card", generate_recommend_card_node)
    graph.add_node("generate_suggested_questions", generate_suggested_questions_node)
    graph.add_node("format_response", format_response_node)

    graph.add_edge(START, "validate_input")
    graph.add_conditional_edges("validate_input", route_after_validation,
        {"parse_stats": "parse_stats", "format_response": "format_response"})
    graph.add_conditional_edges("parse_stats", route_after_parse_stats,
        {"llm_parse_context": "llm_parse_context", "format_response": "format_response"})
    graph.add_edge("llm_parse_context", "merge_context")
    graph.add_conditional_edges("merge_context", route_after_context_merge,
        {
            "clarify_role_filter": "clarify_role_filter",
            "clarify_focus_hero": "clarify_focus_hero",
            "off_topic_response": "off_topic_response",
            "build_retrieval_queries": "build_retrieval_queries",
        })
    graph.add_edge("clarify_role_filter", "format_response")
    graph.add_edge("clarify_focus_hero", "format_response")
    graph.add_edge("off_topic_response", "format_response")
    graph.add_edge("build_retrieval_queries", "retrieve_docs")
    graph.add_conditional_edges("retrieve_docs", route_after_retrieve,
        {
            "judge_strategy": "judge_strategy",
            "generate_answer": "generate_answer",
            "generate_matchup_answer": "generate_matchup_answer",
            "generate_recommend_card": "generate_recommend_card",
            "format_response": "format_response",
        })
    graph.add_conditional_edges("judge_strategy", route_after_judge,
        {"generate_answer": "generate_answer", "format_response": "format_response"})
    graph.add_conditional_edges("generate_answer", route_after_generate,
        {"generate_suggested_questions": "generate_suggested_questions", "format_response": "format_response"})
    graph.add_conditional_edges("generate_matchup_answer", route_after_generate,
        {"generate_suggested_questions": "generate_suggested_questions", "format_response": "format_response"})
    graph.add_conditional_edges("generate_recommend_card", route_after_generate,
        {"generate_suggested_questions": "generate_suggested_questions", "format_response": "format_response"})
    graph.add_edge("generate_suggested_questions", "format_response")
    graph.add_edge("format_response", END)

    return graph.compile()


_chatbot_graph = None

def get_chatbot_graph():
    global _chatbot_graph
    if _chatbot_graph is None:
        _chatbot_graph = build_chatbot_graph()
    return _chatbot_graph


def run_chatbot_graph(
    message: str,
    conversation_context: Optional[Dict[str, Any]] = None,
    role_filter: Optional[str] = None,
    answer_style: Optional[str] = None,
    focus_hero_pick: Optional[str] = None,
    roster_size: Optional[int] = None,
) -> Dict[str, Any]:
    logger.info(
        "[GRAPH START] message=%s role_filter=%s answer_style=%s focus_hero_pick=%s "
        "roster_size=%s context=%s",
        message, role_filter, answer_style, focus_hero_pick, roster_size, conversation_context,
    )
    t0 = time.time()

    graph = get_chatbot_graph()
    final_state = graph.invoke({
        "message": message,
        "conversation_context": conversation_context or {},
        "role_filter": role_filter,
        "answer_style": answer_style,
        "focus_hero_pick": focus_hero_pick,
        "roster_size": roster_size,
    })

    logger.info("[TIMING] run_chatbot_graph 전체: %.2fs (answer_style=%s)", time.time() - t0, answer_style)
    logger.info(
        "[GRAPH RESULT] intent=%s recommendation_type=%s current_hero=%s "
        "current_hero_role=%s target_enemy=%s has_stats=%s",
        final_state.get("intent"),
        final_state.get("recommendation_type"),
        final_state.get("current_hero"),
        final_state.get("current_hero_role"),
        final_state.get("target_enemy"),
        final_state.get("has_stats"),
    )

    return final_state.get("result", {"error": "응답 생성에 실패했습니다."})


def save_graph_png(compiled_graph, output_path="chatbot_graph.png"):
    try:
        from pathlib import Path
        png_bytes = compiled_graph.get_graph().draw_mermaid_png()
        Path(output_path).write_bytes(png_bytes)
        logger.info("[LangGraph PNG 저장 완료] %s", output_path)
    except Exception as exc:
        logger.exception("[LangGraph PNG 저장 실패] %s", exc)
        from pathlib import Path
        mermaid_text = compiled_graph.get_graph().draw_mermaid()
        fallback_path = Path(output_path).with_suffix(".mmd")
        fallback_path.write_text(mermaid_text, encoding="utf-8")
        logger.info("[LangGraph Mermaid 저장 완료] %s", fallback_path)

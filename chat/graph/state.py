"""LangGraph 파이프라인이 노드 사이로 주고받는 상태(state) 정의.

한 요청이 그래프를 한 바퀴 도는 동안 이 dict 하나가 계속 갱신되며 흘러간다.
각 노드는 자기가 바꾼 키만 담은 dict를 반환하고 LangGraph가 병합한다.
"""

from typing import Any, Dict, List, Optional, TypedDict


class ChatbotGraphState(TypedDict, total=False):
    message: str
    conversation_context: Dict[str, Any]
    context_patch: Dict[str, Any]
    role_filter: Optional[str]
    role_filter_explicit: bool
    intent: Optional[str]
    target_enemy: Optional[str]
    current_hero: Optional[str]
    current_hero_role: Optional[str]
    map_name: Optional[str]
    side: Optional[str]
    enemy_team: List[str]
    llm_intent: Optional[str]
    llm_current_hero: Optional[str]
    llm_current_hero_role: Optional[str]
    llm_target_enemy: Optional[str]
    llm_enemy_team: List[str]
    llm_current_hero_confirmed: bool
    swap_guard_triggered: bool
    enemy_stats: Optional[Dict[str, Any]]
    my_stats: Optional[Dict[str, Any]]
    my_team_stats: Optional[Dict[str, Any]]
    high_threat_enemy: Optional[str]
    has_stats: bool
    can_swap_hero: Optional[bool]
    wants_to_stay: Optional[bool]
    game_state: Dict[str, Any]
    retrieval_queries: List[str]
    retrieved_docs: List[Dict[str, Any]]
    retrieval_text: str
    used_doc_ids: List[int]
    used_doc_metadata: List[Dict[str, Any]]
    recommendation_type: Optional[str]
    recommended_heroes: List[str]
    strategy_reason: str
    answer: str
    suggested_questions: List[str]
    choice_buttons: List[Dict[str, str]]
    result: Dict[str, Any]
    error: Optional[str]
    need_clarification: bool
    clarification_question: Optional[str]
    previous_user_message: Optional[str]
    enemy_named_this_turn: bool
    target_enemy_narrowed: bool
    enemy_role_focus: Optional[str]
    current_hero_uncertain: bool
    answer_style: Optional[str]
    matchup_subject: Optional[str]
    matchup_subject_is_enemy: bool
    matchup_card: Optional[Dict[str, Any]]
    recommend_card_mode: Optional[str]
    recommend_card: Optional[Dict[str, Any]]
    ally_team: List[str]
    llm_ally_team: List[str]
    # 아군 조합으로 좁힌 사용자 역할 후보와, 그 조합이 역할 좁히기에 쓸 만큼
    # 최근인지(5분 규칙). roster_size는 사용자가 직접 알려준 팀 인원수(5/6).
    role_candidates: List[str]
    role_candidates_fresh: bool
    roster_size: Optional[int]
    # 되묻지 않고 답할 때 답변 끝에 붙이는 판단 근거 한 줄.
    role_basis_note: str
    compared_heroes: List[str]
    is_team_comp_question: bool
    focus_heroes: List[str]
    focus_hero_pick: Optional[str]
    needs_focus_hero_clarify: bool
    previous_focus_heroes: List[str]

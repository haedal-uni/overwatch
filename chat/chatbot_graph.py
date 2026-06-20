import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .chatbot_service import get_chatbot_components

logger = logging.getLogger(__name__)


class ChatbotGraphState(TypedDict, total=False):
    message: str
    conversation_context: Dict[str, Any]
    context_patch: Dict[str, Any]
    role_filter: Optional[str]
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
    current_hero_uncertain: bool


HEROES = [
    "겐지", "트레이서", "솜브라", "리퍼", "캐서디", "애쉬", "위도우메이커", "한조",
    "소전", "솔저76", "파라", "에코", "메이", "토르비욘", "정크랫", "바스티온",
    "시메트라", "벤처", "벤데타", "안란", "엠레", "프레야", "시에라",
    "라인하르트", "윈스턴", "디바", "자리야", "오리사", "시그마", "라마트라",
    "레킹볼", "둠피스트", "로드호그", "정커퀸", "마우가", "해저드", "도미나",
    "아나", "키리코", "모이라", "루시우", "브리기테", "젠야타", "바티스트",
    "메르시", "일리아리", "라이프위버", "주노", "제트팩 캣", "우양", "미즈키"
]

MAPS = [
    "남극 반도", "네팔", "리장 타워", "부산", "사모아", "오아시스", "일리오스",
    "66번 국도", "감시 기지: 지브롤터", "도라도", "리알토", "샴발리 수도원",
    "서킷 로얄", "쓰레기촌", "하바나",
    "눔바니", "미드타운", "블리자드 월드", "아이헨발데", "왕의 길", "파라이수", "할리우드",
    "뉴 퀸 스트리트", "이스페란사", "콜로세오", "루나사피",
    "뉴 정크 시티", "수라바사", "아틀리스"
]

ROLE_LABELS = {
    "all": "전체",
    "tank": "탱커",
    "damage": "딜러",
    "support": "힐러",
}

ROLE_HEROES: Dict[str, List[str]] = {
    "tank": [
        "라인하르트", "윈스턴", "디바", "자리야", "오리사", "시그마",
        "라마트라", "레킹볼", "둠피스트", "로드호그", "정커퀸", "마우가",
        "도미나", "해저드"
    ],
    "damage": [
        "겐지", "트레이서", "솜브라", "리퍼", "캐서디", "애쉬", "위도우메이커", "한조",
        "소전", "솔저: 76", "파라", "에코", "메이", "토르비욘", "정크랫",
        "바스티온", "시메트라", "벤처", "벤데타", "시에라", "안란", "엠레", "프레야"
    ],
    "support": [
        "아나", "키리코", "모이라", "루시우", "브리기테", "젠야타",
        "바티스트", "메르시", "일리아리", "라이프위버", "주노", "미즈키", "우양",
        "제트팩 캣"
    ],
}

HERO_TO_ROLE: Dict[str, str] = {}
for _role, _heroes in ROLE_HEROES.items():
    for _hero in _heroes:
        HERO_TO_ROLE[_hero] = _role

OVERWATCH_SKILL_SHORTCUTS = {
    "디바": {"융합포": "좌클릭", "부스터": "shift", "방어 매트릭스": "우클릭", "마이크로 미사일": "e", "자폭": "q"},
    "도미나": {"광자 매그넘": "좌클릭", "방벽 배열": "우클릭", "소닉 리펄서": "shift", "수정 발사": "e", "판옵티콘": "q"},
    "둠피스트": {"철권포": "좌클릭", "로켓 펀치": "우클릭", "지진 강타": "shift", "파워 블락": "e", "파멸의 일격": "q"},
    "라마트라": {"공허 가속기": "좌클릭", "공허 방벽": "우클릭", "네메시스 형태": "shift", "탐식의 소용돌이": "e", "절멸": "q"},
    "라인하르트": {"로켓 해머": "좌클릭", "방벽 방패": "우클릭", "돌진": "shift", "화염 강타": "e", "대지분쇄": "q"},
    "레킹볼": {"4연장 기관총": "좌클릭", "갈고리 고정": "우클릭", "구르기": "shift", "적응형 보호막": "e", "파일드라이버": "ctrl", "지뢰밭": "q"},
    "로드호그": {"고철총": "좌클릭", "고철총(폭발)": "우클릭", "사슬 갈고리": "shift", "숨 돌리기": "e", "돼재앙": "q"},
    "마우가": {"화염 및 촉발 기관포": "좌클릭", "돌파": "shift", "터질 듯한 심장": "e", "케이지 혈투": "q"},
    "시그마": {"초구체": "좌클릭", "실험용 방벽": "우클릭", "키네틱 손아귀": "shift", "강착": "e", "중력붕괴": "q"},
    "오리사": {"개량형 융합 기관포": "좌클릭", "투창": "우클릭", "방어 강화": "shift", "수호의 창": "e", "대지의 창": "q"},
    "윈스턴": {"테슬라 캐논": "좌클릭", "점프 팩": "shift", "방벽 생성기": "e", "원시의 분노": "q"},
    "자리야": {"입자포": "좌클릭", "입자탄": "우클릭", "입자 방벽": "shift", "방벽 씌우기": "e", "중력자탄": "q"},
    "정커퀸": {"산탄총": "좌클릭", "톱니칼": "우클릭", "지휘의 외침": "shift", "도륙": "e", "살육": "q"},
    "해저드": {"본스퍼": "좌클릭", "날카로운 저항": "우클릭", "덤벼들기": "shift", "가시벽": "e", "가시 소나기": "q"},
    "벤데타": {"팔라틴 팽": "좌클릭", "수호 태세": "우클릭", "소용돌이 질주": "shift", "치솟는 베기": "e", "갈라내는 칼날": "q"},
    "시에라": {"헬릭스 소총": "좌클릭", "추적 사격": "우클릭", "앵커 드론": "shift", "진동 폭약": "e", "개척자": "q"},
    "안란": {"주작의 부채": "좌클릭", "불난 데 부채질": "우클릭", "맹염 질주": "shift", "춤추는 불꽃": "e", "불사조 승천/부활": "q"},
    "엠레": {"합성 점사 소총": "좌클릭", "저격 모드": "우클릭", "사이펀 블라스터": "shift", "사이버 파편 수류탄": "e", "오버라이드 프로토콜": "q"},
    "아나": {"생체 소총": "좌클릭", "저격 모드": "우클릭", "수면총": "shift", "생체 수류탄": "e", "나노 강화제": "q"},
    "우양": {"현무 지팡이": "좌클릭", "회복의 물결": "우클릭", "격류": "shift", "수호의 파도": "e", "해일 폭발": "q"},
    "제트팩 캣": {"생체 냥냥탄": "좌클릭", "정신 없는 비행": "우클릭", "생명줄": "shift", "골골대기": "e", "납치한다냥": "q"},
    "젠야타": {"파괴의 구슬": "좌클릭", "구슬 연사": "우클릭", "조화의 구슬": "shift", "부조화의 구슬": "e", "초월": "q"},
    "주노": {"메디블라스터": "좌클릭", "펄사 어뢰": "우클릭", "글라이드 부스터": "shift", "하이퍼 링": "e", "궤도 광선": "q"},
    "키리코": {"치유의 부적": "좌클릭", "쿠나이": "우클릭", "순보": "shift", "정화의 방울": "e", "여우길": "q"},
    "미즈키": {"영혼 수리검": "좌클릭", "치유의 삿갓": "우클릭", "종이 인형 분신술": "shift", "속박 사슬": "e", "결계 성역": "q"},
}


def call_llm_text(llm: Any, prompt: str) -> str:
    response = llm.invoke(prompt)
    if hasattr(response, "content"):
        content = response.content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(item.get("text", str(item)))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(content)
    return str(response)


def call_llm_text_creative(llm: Any, prompt: str) -> str:
    creative_llm = llm
    if hasattr(llm, "bind"):
        try:
            creative_llm = llm.bind(temperature=0.7)
        except Exception:
            creative_llm = llm
    return call_llm_text(creative_llm, prompt)


def safe_json_loads(text: str, default: Any) -> Any:
    try:
        cleaned = str(text or "").strip()
        fence_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned)
        if fence_match:
            cleaned = fence_match.group(1).strip()
        return json.loads(cleaned)
    except Exception:
        logger.warning("JSON 파싱 실패. raw=%s", text)
        return default


def retrieve_documents(retriever: Any, query: str) -> List[Any]:
    if hasattr(retriever, "invoke"):
        return retriever.invoke(query)
    if hasattr(retriever, "get_relevant_documents"):
        return retriever.get_relevant_documents(query)
    raise TypeError("retriever는 invoke 또는 get_relevant_documents 메서드를 가져야 합니다.")


def document_to_dict(doc: Any) -> Dict[str, Any]:
    if hasattr(doc, "page_content"):
        return {"content": doc.page_content, "metadata": getattr(doc, "metadata", {})}
    return {"content": str(doc), "metadata": {}}


def get_skill_shortcut_text() -> str:
    lines = []
    for hero, skills in OVERWATCH_SKILL_SHORTCUTS.items():
        skill_items = [f"{skill}({key})" for skill, key in skills.items() if skill.strip()]
        if not skill_items:
            continue
        lines.append(f"- {hero}: {', '.join(skill_items)}")
    return "\n".join(lines)


def normalize_hero_name(hero: Optional[str]) -> Optional[str]:
    if not hero:
        return None
    hero = hero.strip()
    if hero == "솔저: 76":
        return "솔저"
    if hero == "D.Va":
        return "디바"
    return hero


def find_first_hero(text: str) -> Optional[str]:
    for hero in HEROES:
        if hero in text:
            return normalize_hero_name(hero)
    return None


def find_all_heroes(text: str) -> List[str]:
    found = []
    for hero in HEROES:
        if hero in text:
            normalized = normalize_hero_name(hero)
            if normalized and normalized not in found:
                found.append(normalized)
    return found


def find_map(text: str) -> Optional[str]:
    for map_name in MAPS:
        if map_name in text:
            return map_name
    return None


def find_side(text: str) -> Optional[str]:
    if "공격" in text:
        return "attack"
    if "수비" in text:
        return "defense"
    return None


def get_hero_role(hero: Optional[str]) -> Optional[str]:
    if not hero:
        return None
    return HERO_TO_ROLE.get(hero)


def extract_enemy_team(text: str) -> List[str]:
    patterns = [
        r"상대는\s*([가-힣A-Za-z0-9\.,\s]+)",
        r"상대\s*조합은\s*([가-힣A-Za-z0-9\.,\s]+)",
        r"적은\s*([가-힣A-Za-z0-9\.,\s]+)",
        r"적팀은\s*([가-힣A-Za-z0-9\.,\s]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        chunk = match.group(1)
        heroes = find_all_heroes(chunk)
        if heroes:
            return heroes
    return []


def detect_stat_input(message: str) -> bool:
    stat_keywords = ["킬", "데스", "딜", "힐", "도움", "어시", "사망", "피해", "치유"]
    has_keyword = any(kw in message for kw in stat_keywords)
    has_number = bool(re.search(r"\d{2,}", message))
    return has_keyword and has_number


def infer_intent_by_rule(message: str, context: Dict[str, Any]) -> str:
    text = message.strip()

    if any(word in text for word in ["카운터", "견제", "잡는", "막는"]):
        return "counter"
    if any(word in text for word in ["말고", "다른 영웅", "바꾸", "변경", "픽 추천"]):
        return "swap"
    if any(word in text for word in ["계속 쓰고", "유지", "그 영웅", "현재 영웅", "내가 계속"]):
        return "stay"
    if any(word in text for word in ["딜량", "데스", "킬", "스탯", "어떻게 플레이", "어떻게 해야"]):
        return "performance_improve"
    if any(word in text for word in ["맵", "공격", "수비", "거점"] + MAPS):
        return "map_strategy"

    if detect_stat_input(text):
        return "performance_improve"

    previous_intent = context.get("last_intent")
    if previous_intent in ["counter", "stay", "swap", "performance_improve", "map_strategy"]:
        return previous_intent

    return "general"


def infer_target_enemy(message: str, context: Dict[str, Any], intent: str) -> Optional[str]:
    text = message.strip()
    current_hero = normalize_hero_name(context.get("current_hero"))

    counter_patterns = [
        r"([가-힣A-Za-z0-9\.]+)[이가]\s*(?:우리\s*팀|아군|힐러|팀원)",
        r"([가-힣A-Za-z0-9\.]+)\s*때문에",
        r"([가-힣A-Za-z0-9\.]+)[을를]?\s*카운터",
        r"([가-힣A-Za-z0-9\.]+)[을를]?\s*견제",
        r"([가-힣A-Za-z0-9\.]+)[을를]?\s*잡",
        r"([가-힣A-Za-z0-9\.]+)[을를]?\s*막",
        r"상대\s*([가-힣A-Za-z0-9\.]+)",
    ]

    for pattern in counter_patterns:
        match = re.search(pattern, text)
        if match:
            candidate = normalize_hero_name(match.group(1))
            if candidate in [normalize_hero_name(h) for h in HEROES]:
                if candidate != current_hero:
                    return candidate

    if intent == "swap":
        new_situation = bool(find_map(text) or find_side(text) or extract_enemy_team(text))
        return None if new_situation else context.get("target_enemy")

    heroes_in_text = find_all_heroes(text)
    heroes_in_text = [h for h in heroes_in_text if h != current_hero]

    if heroes_in_text:
        return heroes_in_text[0]

    new_situation = bool(find_map(text) or find_side(text) or extract_enemy_team(text))
    if not new_situation:
        return context.get("target_enemy")

    return None


def infer_current_hero(message: str, context: Dict[str, Any], intent: str) -> Optional[str]:
    text = message.strip()

    my_stats_heroes = list((context.get("my_stats") or {}).keys())
    if my_stats_heroes:
        return normalize_hero_name(my_stats_heroes[0])

    enemy_heroes: set = set()
    for h in context.get("enemy_team", []):
        n = normalize_hero_name(h)
        if n:
            enemy_heroes.add(n)
    for h in (context.get("enemy_stats") or {}).keys():
        n = normalize_hero_name(h)
        if n:
            enemy_heroes.add(n)
    te = normalize_hero_name(context.get("target_enemy"))
    if te:
        enemy_heroes.add(te)

    if "말고" in text:
        before = text.split("말고")[0]
        hero = find_first_hero(before)
        if hero and hero not in enemy_heroes:
            return hero

    if any(word in text for word in ["계속 쓰고", "계속 하고", "현재", "플레이", "하고 있"]):
        hero = find_first_hero(text)
        if hero and hero not in enemy_heroes:
            return hero

    if intent in ["stay", "performance_improve", "swap", "map_strategy"]:
        for hero in find_all_heroes(text):
            if hero not in enemy_heroes:
                return hero

    return context.get("current_hero")


def role_filter_from_text(message: str) -> Optional[str]:
    if any(word in message for word in ["탱커", "탱커로", "탱커 추천"]):
        return "tank"
    if any(word in message for word in ["딜러로", "딜러 추천", "딜러가 잡혔", "딜러 해야"]):
        return "damage"
    if any(word in message for word in ["힐러로", "힐러 추천", "힐러가 잡혔", "힐러 해야", "지원가로", "지원가 추천"]):
        return "support"
    if any(word in message for word in ["전체", "전부", "다 알려"]):
        return "all"
    return None


def should_ask_role_filter(state: ChatbotGraphState) -> bool:
    intent = state.get("intent")
    target_enemy = state.get("target_enemy")
    role_filter = state.get("role_filter")

    if intent != "counter":
        return False
    if not target_enemy:
        return False
    if role_filter:
        return False

    message = state.get("message", "")
    if role_filter_from_text(message):
        return False

    broad_counter_words = [
        "카운터 하는 영웅",
        "카운터치는 영웅",
        "카운터 영웅",
        "상대하기 좋은 영웅",
        "막는 영웅",
    ]
    return any(word in message for word in broad_counter_words)


def sanitize_answer_for_user(answer: str) -> str:
    if not answer:
        return ""
    sanitized = answer

    sanitized = sanitized.replace("\\n", "\n")

    sanitized = re.sub(r'\n*```json[\s\S]*?```\s*$', '', sanitized).strip()
    sanitized = re.sub(r'\n*\{\s*"answer"\s*:[\s\S]*\}\s*$', '', sanitized).strip()
    sanitized = re.sub(r'\n*"used_doc_ids"\s*:\s*\[.*?\]\s*\}?\s*$', '', sanitized).strip()

    sanitized = re.sub(r"\s*\(문서\s*\d+\)", "", sanitized)
    # "[문서 1]에서 언급했듯이" 처럼 대괄호 출처 표시 뒤에 붙는 어구까지 함께 제거
    # 대괄호만 지우면 "에서 언급했듯이"라는 어색한 잔여 문구가 발생.
    sanitized = re.sub(r"\s*\[문서\s*\d+\][^,.\n]{0,12}(?:듯이|면서)?,?", "", sanitized)
    sanitized = re.sub(r"\s*\[문서\s*\d+\]", "", sanitized)
    banned_phrases = [
        "문서에 따르면,", "문서에 따르면",
        "검색된 문서에 따르면,", "검색된 문서에 따르면",
        "제공된 문서에 따르면,", "제공된 문서에 따르면",
        "참고 문서에 따르면,", "참고 문서에 따르면",
        "자료에 따르면,", "자료에 따르면",
        "컨텍스트에 따르면,", "컨텍스트에 따르면",
    ]
    for phrase in banned_phrases:
        sanitized = sanitized.replace(phrase, "")
    map_warning_patterns = [
        r"현재 플레이 중인 맵 정보가 없어[^.\n]*(\.|\n)?",
        r"맵 정보가 없어[^.\n]*(\.|\n)?",
        r"특정 맵 운영법에 대한 조언은 어렵습니다[^.\n]*(\.|\n)?",
    ]
    for pattern in map_warning_patterns:
        sanitized = re.sub(pattern, "", sanitized)

    # LLM이 마크다운 문법을 지시 위반으로 섞어 쓴 경우를 위한 안전망.
    # JSON 파싱이 깨지는 가장 흔한 원인이 **볼드**나 *   리스트 같은 마크다운
    # 기호이므로, 최종 사용자 노출 전에 한 번 더 청소한다.
    sanitized = re.sub(r"\*\*(.+?)\*\*", r"\1", sanitized)        # **볼드** → 볼드
    sanitized = re.sub(r"^\s*[\*\-]\s+", "", sanitized, flags=re.MULTILINE)  # 글머리 기호 제거

    sanitized = re.sub(r"[ \t]+", " ", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    return sanitized.strip()


def validate_input_node(state: ChatbotGraphState) -> ChatbotGraphState:
    message = state.get("message", "").strip()
    role_filter = state.get("role_filter")

    if not message and not role_filter:
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
        _chatbot, _retriever, llm = get_chatbot_components()

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
    if prev_hero and new_current_hero and prev_hero != new_current_hero:
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

    try:
        _chatbot, _retriever, llm = get_chatbot_components()

        message = state.get("message", "")
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

사용자 메시지:
\"\"\"{message}\"\"\"

영웅 목록:
{hero_list}

역할 분류:
{role_map_text}

아래 JSON 형식으로만 답해라. 다른 텍스트는 출력하지 마라.

{{
  "intent": "counter | swap | stay | performance_improve | map_strategy | general 중 하나",
  "current_hero": "사용자가 지금 플레이 중인 영웅 이름 또는 null",
  "current_hero_role": "tank | damage | support 또는 null",
  "target_enemy": "카운터하거나 상대해야 할 적 영웅 이름 또는 null",
  "enemy_team": ["적팀 영웅1", "적팀 영웅2"]
}}

추론 규칙:
1. current_hero는 사용자가 직접 플레이하는 영웅만. 상대 영웅은 target_enemy나 enemy_team에.
2. 힐/지원을 받지 못한다는 불만 표현(예: "힐을 못 받는다", "지원이 끊긴다", "케어가 안 된다" 등
   어떤 표현이든)은 사용자가 지원 부족 문제를 겪고 있다는 뜻이지, 사용자 본인이 힐러를
   플레이 중이라는 뜻이 아니다. 이런 표현만으로 current_hero_role을 "support"로 단정하지 마라.
3. 같은 역할 안에서의 교체를 묻는 문장(예: "X 말고 Y로 바꿀까?", "X 대신 Y 어때?",
   "Y로 바꾸는 게 나을까?")이 나오면 intent는 "swap"이다. 이때 current_hero는 바꾸기
   전 영웅(이미 플레이 중이라고 말한 영웅)이고, 교체 후보로 언급된 영웅(Y)은
   current_hero도 target_enemy도 아니다. 따라서 target_enemy는 반드시 null로 설정하라.
4. 영웅 이름이 메시지에 없으면 이전 컨텍스트의 current_hero를 이어받아라.
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
        if intent in ["counter", "swap", "stay", "performance_improve", "map_strategy", "general"]:
            result["llm_intent"] = intent

        current_hero = parsed.get("current_hero")
        current_hero_confirmed_in_message = False
        if current_hero and isinstance(current_hero, str):
            normalized = normalize_hero_name(current_hero.strip())
            if normalized in [normalize_hero_name(h) for h in HEROES]:
                result["llm_current_hero"] = normalized
                role = HERO_TO_ROLE.get(normalized)
                if role:
                    result["llm_current_hero_role"] = role
                # current_hero는 "이전 영웅을 이어받는 것" 자체는 정당한 경우가 많아
                # (예: "딜 더 올리는 법은?" 같은 후속 질문) target_enemy처럼 무조건
                # 버리지는 않는다. 다만 메시지 원문에 실제로 등장했는지는 별도로
                # 표시해, swap처럼 "교체"를 다루는 민감한 intent와 결합됐을 때
                # 안전장치가 작동할 수 있게 한다.
                if normalized in message:
                    current_hero_confirmed_in_message = True
        result["llm_current_hero_confirmed"] = current_hero_confirmed_in_message

        # 안전장치: intent가 "swap"(영웅 교체 여부 판단)인데 정작 메시지 원문에
        # current_hero 이름이 전혀 없다면, 이건 "내 영웅을 바꿀지" 묻는 질문이
        # 아니라 "팀 조합을 어떻게 짤지" 같은 일반적인 질문일 가능성이 높다.
        # 실제 사례: "상대가 바스티온, 토르비욘으로 압박해서 팀 조합을 어떻게
        # 맞추는 게 좋을까"라는, 본인 영웅 언급이 전혀 없는 조합 질문을 LLM이
        # "디바를 교체할지" 묻는 질문으로 오인해 엉뚱한 답을 한 사고가 있었다.
        #
        # 이 가드가 실제로 발동했다는 사실 자체를 별도 플래그(swap_guard_triggered)로
        # 남긴다. merge_context_node에서 "현재 영웅이 불확실하다"고 판단할 근거는
        # 반드시 이 플래그여야 한다 — 단순히 llm_intent가 "general"이라는 것만으로는
        # 부족하다. "딜러들 케어 우선순위 알려줘"처럼 직전 답변에 대한 순수 후속
        # 질문도 영웅 이름 없이 intent=general로 분류되는데, 이런 경우까지 "영웅이
        # 불확실하다"고 처리하면 정상적인 후속 대화의 맥락(예: 모이라 얘기)이
        # 통째로 사라지는 사고가 난다(실제 발생: 모이라 스탯 질문 다음 "케어
        # 우선순위" 후속 질문에 모이라 얘기가 전혀 없는 범용 답변이 나옴).
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

        # target_enemy/enemy_team은 LLM이 짐작으로 채울 수 있으므로,
        # 사용자 메시지 원문에 그 영웅 이름이 실제로 등장할 때만 신뢰한다.
        # (이전 대화의 적이 다음 질문에 단정적으로 이어붙는 문제 방지 — 윈스턴 버그 수정)
        target_enemy = parsed.get("target_enemy")
        if target_enemy and isinstance(target_enemy, str):
            normalized_enemy = normalize_hero_name(target_enemy.strip())
            if (
                normalized_enemy in [normalize_hero_name(h) for h in HEROES]
                and normalized_enemy in message
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
                if n in [normalize_hero_name(x) for x in HEROES] and n in message:
                    verified_team.append(n)
            if verified_team:
                result["llm_enemy_team"] = verified_team

        logger.info("[LLM_PARSE_CONTEXT] %s", result)
        return result

    except Exception as exc:
        logger.exception("llm_parse_context_node 오류: %s", exc)
        return {}


def merge_context_node(state: ChatbotGraphState) -> ChatbotGraphState:
    message = state.get("message", "").strip()
    context = state.get("conversation_context", {}) or {}

    # ── 세션 타임아웃: 마지막 메시지로부터 일정 시간이 지나면 새 게임으로 간주 ──
    # 텍스트만으로 "이전 대화 이어가기"와 "새 게임 시작"을 구분하는 데는 한계가
    # 있다(예: "팀 조합 어떻게 짤까"는 직전 영웅의 후속 질문일 수도, 완전히 새로운
    # 판의 질문일 수도 있다). 가장 확실한 신호는 경과 시간이다. 오버워치 한 판은
    # 보통 10~20분 정도 걸리므로, 10분 이상 메시지가 없었다면 직전 판이 끝나고
    # 새 판이 시작됐을 가능성이 높다고 보고 컨텍스트를 초기화한다.
    SESSION_TIMEOUT_SECONDS = 10 * 60
    now_ts = time.time()
    last_message_ts = context.get("last_message_ts")
    session_timed_out = bool(
        last_message_ts and (now_ts - last_message_ts) > SESSION_TIMEOUT_SECONDS
    )
    if session_timed_out:
        logger.info(
            "[SESSION TIMEOUT] 마지막 메시지로부터 %.0f초 경과 — 컨텍스트 초기화 (새 게임으로 간주)",
            now_ts - last_message_ts,
        )
        context = {}

    explicit_role_filter = state.get("role_filter") or role_filter_from_text(message)
    # 세션에 남아있는 role_filter는 "카운터 역할 필터 질문(예: 탱커로 볼까요?)"에
    # 대한 응답 흐름(pending_question)에서만 의미가 있다. 그 흐름이 아니면(=일반
    # 대화로 넘어가면) 세션 잔존값을 그대로 이어받지 않는다. 그렇지 않으면 한 번
    # "탱커로 추천해줘"라고 물었던 필터가 평생 세션에 박혀, 전혀 다른 영웅으로
    # 갈아탄 뒤에도 답변 허용 목록을 계속 탱커로 고정시키는 사고가 난다.
    stale_role_filter = context.get("role_filter") if context.get("pending_question") else None
    role_filter = explicit_role_filter or stale_role_filter

    effective_message = message
    if explicit_role_filter and context.get("pending_question"):
        effective_message = context.get("pending_question")

    llm_intent       = state.get("llm_intent")
    llm_current_hero = state.get("llm_current_hero")
    llm_hero_role    = state.get("llm_current_hero_role")
    llm_target_enemy = state.get("llm_target_enemy")
    llm_enemy_team   = state.get("llm_enemy_team")
    llm_current_hero_confirmed = state.get("llm_current_hero_confirmed", False)
    swap_guard_triggered = state.get("swap_guard_triggered", False)

    intent       = llm_intent or infer_intent_by_rule(effective_message, context)
    current_hero = llm_current_hero or infer_current_hero(effective_message, context, intent)

    # current_hero가 이번 메시지에 직접 등장하지 않았는데, llm_parse_context_node의
    # SWAP INTENT GUARD가 실제로 발동했다면(swap_guard_triggered=True) — 즉 LLM이
    # 원래 "교체 여부 판단" 질문으로 잘못 분류했을 만큼 새로운 상황 설명(상대 조합,
    # 압박 상황 등)이 담겨 있었는데 본인 영웅 언급이 없었다면 — "지금도 정말 그
    # 영웅을 플레이 중인지" 자체가 불확실한 상태다.
    #
    # 반드시 swap_guard_triggered를 기준으로 삼아야 한다. 단순히 llm_intent가
    # "general"이라는 것만으로 판단하면, "케어 우선순위 알려줘"처럼 새로운 정보
    # 없이 직전 답변을 더 풀어달라는 순수 후속 질문까지 "영웅이 불확실하다"고
    # 오판해서, 멀쩡히 이어지던 대화 맥락(예: 모이라 스탯 분석)이 끊기는 사고가
    # 난다(실제 발생 사례).
    current_hero_uncertain = bool(
        current_hero
        and not llm_current_hero_confirmed
        and swap_guard_triggered
    )

    # "힐을 못받아", "힐이 없어", "힐을 못줘", "힐 부족" 등 힐 수급 문제를 토로하는
    # 표현은 화이트리스트로 일일이 나열하면 누락되기 쉽다("힐을 못줘"가 빠져있던
    # 실제 버그 사례). "힐"이라는 단어와 부정/부족을 뜻하는 표현이 근접해서
    # 함께 등장하면 힐 수급 불만으로 간주하는 정규식으로 더 견고하게 잡는다.
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

    # 추가 안전장치: current_hero가 이미 명확히 특정 영웅으로 잡혔다면,
    # 그 영웅의 실제 역할(HERO_TO_ROLE)을 LLM 판단보다 우선한다.
    # current_hero가 무엇인지는 find_first_hero 등으로 메시지에서 직접
    # 추출되므로, 역할을 잘못 분류해 허용 영웅 목록이 통째로 어긋나는
    # 사고(예: 에코를 플레이 중인데 role이 support로 잘못 잡혀 답변의
    # "애쉬"·"에코" 언급이 전부 위반 처리되는 문제)를 막는다.
    if current_hero:
        true_role = HERO_TO_ROLE.get(current_hero)
        if true_role and true_role != llm_hero_role:
            logger.info(
                "[ROLE SAFETY] current_hero=%s의 실제 역할은 %s인데 llm_hero_role=%s로 "
                "잡혀 있어 교정함",
                current_hero, true_role, llm_hero_role,
            )
            llm_hero_role = true_role

    map_name = find_map(effective_message) or state.get("map_name") or None
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
                         "no_enemy_turn_count")
        }

    # 적 정보가 무한정 세션에 눌어붙는 것을 막는다. current_hero/map_name이 그대로
    # 유지된 채 대화가 이어지면 should_reset_enemy_context는 리셋을 트리거하지 않으므로,
    # "이번 턴에 적이 언급되지 않은" 상태가 연속되면 별도로 카운트해서 일정 턴 후
    # 자동으로 비운다. (실제 사례: 윈스턴을 언급한 적 없는데 여러 턴째 target_enemy에
    # 남아있던 버그)
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
            context = {
                k: v for k, v in context.items()
                if k not in ("target_enemy", "enemy_team", "high_threat_enemy")
            }
            no_enemy_turn_count = 0
    else:
        no_enemy_turn_count = 0

    # ── 이번 메시지에 적 영웅 이름이 실제로 등장했는지 판단 ──
    # (LLM 결과는 이미 llm_parse_context_node에서 원문 검증을 거쳤으므로 신뢰 가능.
    #  여기서는 규칙 기반 추출 결과까지 포함해 "이번 턴 한정" 여부를 최종 결정한다.)
    mentioned_heroes_in_message = set(find_all_heroes(effective_message))
    rule_based_enemy_team = extract_enemy_team(effective_message)

    enemy_team = (
        llm_enemy_team
        or rule_based_enemy_team
        or (context.get("enemy_team", []) if not context_was_reset else [])
    )

    context_for_enemy = {**context, "current_hero": current_hero}
    rule_based_target_enemy = infer_target_enemy(effective_message, context_for_enemy, intent)
    target_enemy = llm_target_enemy or rule_based_target_enemy

    # 이번 턴에 실제로 메시지에 등장한 적 이름이 있는지 확인.
    # llm_target_enemy/llm_enemy_team은 이미 원문 검증됨. rule_based 값도 메시지에서 직접
    # 추출된 것이므로 신뢰 가능. 반면 컨텍스트에서 그대로 이어받은 값(이전 대화 잔존)은
    # mentioned_heroes_in_message와 교집합이 없으면 "이번 턴 언급 아님"으로 처리한다.
    enemy_named_this_turn = bool(
        llm_target_enemy
        or llm_enemy_team
        or rule_based_enemy_team
        or (rule_based_target_enemy and rule_based_target_enemy in mentioned_heroes_in_message)
    )

    high_threat_enemy = state.get("high_threat_enemy") or context.get("high_threat_enemy")
    if state.get("high_threat_enemy"):
        # 이번 턴에 직접 입력된 스탯에서 나온 위협 영웅이면 "이번 턴에 언급됨"으로 간주
        enemy_named_this_turn = True
    if high_threat_enemy and not target_enemy and intent in ["counter", "general", "map_strategy", "performance_improve"]:
        target_enemy = high_threat_enemy
        if intent == "general":
            intent = "counter"

    current_hero_role = llm_hero_role or get_hero_role(current_hero)

    # effective_role_filter 결정 우선순위:
    # 1순위: 이번 메시지에서 사용자가 명시적으로 요청한 필터(explicit_role_filter) —
    #        예: "탱커로 추천해줘" 같은 직접적인 요청
    # 2순위: current_hero_role — 실제로 지금 플레이 중인 영웅의 역할. 가장 신뢰도 높음.
    # 3순위: 세션에 남아있는 이전 role_filter — 둘 다 없을 때만 최후 수단으로 사용.
    #        (이 값을 2순위보다 위에 두면, 예전에 "탱커 추천해줘"라고 물었던 잔존값이
    #         계속 세션에 남아 정작 지금 플레이 중인 영웅의 역할과 무관하게 답변
    #         허용 목록을 고정시켜버리는 사고가 난다 — 실제로 발생했던 버그)
    if explicit_role_filter:
        effective_role_filter = explicit_role_filter
    elif current_hero_role:
        effective_role_filter = current_hero_role
    else:
        effective_role_filter = role_filter

    if intent == "performance_improve" and not current_hero:
        current_hero = context.get("current_hero")
    if intent == "swap" and not current_hero:
        current_hero = context.get("current_hero")
    if intent == "counter" and not target_enemy:
        target_enemy = context.get("target_enemy")
        if target_enemy:
            # 사용자가 명시적으로 "카운터" 의도를 다시 표현한 경우는
            # 이전 대화의 적을 이어받는 것이 자연스러우므로 언급된 것으로 처리
            enemy_named_this_turn = True

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
        "enemy_stats": state.get("enemy_stats") or context.get("enemy_stats"),
        "my_stats": state.get("my_stats") or context.get("my_stats"),
        "my_team_stats": state.get("my_team_stats") or context.get("my_team_stats"),
        "high_threat_enemy": high_threat_enemy,
        "has_stats": state.get("has_stats", False) or bool(context.get("my_stats") or context.get("enemy_stats")),
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
    }

    if target_enemy:
        context_patch["target_enemy"] = target_enemy
    if current_hero:
        context_patch["current_hero"] = current_hero
    if current_hero_role:
        context_patch["current_hero_role"] = current_hero_role
    if map_name:
        context_patch["map_name"] = map_name
    if side:
        context_patch["side"] = side
    if enemy_team:
        context_patch["enemy_team"] = enemy_team
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
        "game_state": game_state,
        "context_patch": context_patch,
        "enemy_named_this_turn": enemy_named_this_turn,
        "current_hero_uncertain": current_hero_uncertain,
        # 직전 턴의 사용자 메시지(있다면). 이번 질문이 "애쉬로 바꾸는 게 나을까?"처럼
        # 짧고 맥락 의존적인 후속 질문일 때, 답변 생성 단계가 원래 상황(왜 교체를
        # 고민하게 됐는지)을 잃지 않도록 전달한다.
        "previous_user_message": context.get("last_user_message") or context.get("last_effective_message"),
    }


def clarify_role_filter_node(state: ChatbotGraphState) -> ChatbotGraphState:
    target_enemy = state.get("target_enemy") or "상대 영웅"
    message = state.get("message", "")

    answer = (
        f"{target_enemy}를 카운터하는 영웅을 어떤 역할 기준으로 볼까요?\n\n"
        "원하는 역할을 선택하면 그 역할의 영웅만 골라서 추천해드릴게요."
    )

    choice_buttons = [
        {"label": "전체", "value": "all", "type": "role_filter"},
        {"label": "탱커", "value": "tank", "type": "role_filter"},
        {"label": "딜러", "value": "damage", "type": "role_filter"},
        {"label": "힐러", "value": "support", "type": "role_filter"},
    ]

    context_patch = {
        **state.get("context_patch", {}),
        "pending_question": message,
        "pending_intent": "counter",
        "target_enemy": target_enemy,
    }

    return {
        "answer": answer,
        "choice_buttons": choice_buttons,
        "context_patch": context_patch,
        "result": {
            "answer": answer,
            "type": "role_filter",
            "choice_buttons": choice_buttons,
            "suggested_questions": [],
            "context_patch": context_patch,
        },
    }


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
    # 이번 턴에 적 영웅이 실제로 언급되지 않았다면 target_enemy/enemy_team 기반
    # 검색 쿼리를 만들지 않는다. (이전 대화의 적이 이번 질문의 검색 결과를 오염시켜
    # 답변에 엉뚱한 영웅이 단정적으로 등장하는 문제를 막기 위함 — 윈스턴 버그 수정)
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

    unique_queries = [q.strip() for q in dict.fromkeys(queries) if q.strip()]
    logger.info("[RAG 검색 쿼리] %s", unique_queries)
    return {"retrieval_queries": unique_queries}


def retrieve_docs_node(state: ChatbotGraphState) -> ChatbotGraphState:
    if state.get("error"):
        return state

    try:
        _chatbot, retriever, _llm = get_chatbot_components()

        all_docs: List[Dict[str, Any]] = []
        seen_contents: set = set()

        for query in state.get("retrieval_queries", []):
            docs = retrieve_documents(retriever, query)
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

        all_docs = all_docs[:12]
        numbered_docs = [{**doc, "doc_id": idx} for idx, doc in enumerate(all_docs, start=1)]

        retrieval_text = "\n\n".join(
            f"[문서 {doc['doc_id']}]\n"
            f"검색어: {doc.get('query')}\n"
            f"metadata: {json.dumps(doc.get('metadata', {}), ensure_ascii=False)}\n"
            f"내용:\n{doc.get('content')}"
            for doc in numbered_docs
        )

        return {"retrieved_docs": numbered_docs, "retrieval_text": retrieval_text}

    except Exception as exc:
        logger.exception("retrieve_docs_node 오류: %s", exc)
        return {"error": str(exc)}


def judge_strategy_node(state: ChatbotGraphState) -> ChatbotGraphState:
    if state.get("error"):
        return state

    try:
        _chatbot, _retriever, llm = get_chatbot_components()

        intent = state.get("intent") or "general"
        role_filter = state.get("role_filter") or "all"
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
        # current_hero가 이전 턴에서 이어받은 값일 뿐, 이번 메시지에서 그 영웅을
        # 계속 플레이 중이라고 확인된 적이 없는 경우. 이런 상태에서 역할을 강제로
        # 제한하면, 실제로는 새로운 상황(다른 영웅이거나 일반적인 조합 질문)인데도
        # 옛 영웅 기준으로 답이 좁혀지는 사고가 난다.
        current_hero_uncertain = state.get("current_hero_uncertain", False)

        # current_hero_role(실제 플레이 중인 영웅의 역할)을 role_filter보다 우선한다.
        # role_filter는 세션에 걸쳐 누적되므로 이전 턴의 잔존값일 수 있다.
        if current_hero_role and current_hero_role in ROLE_HEROES and role_filter != current_hero_role:
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
            # 영웅이 불확실하면 역할로 영웅 풀을 제한하지 않는다. 이번 질문이
            # 특정 영웅과 무관한 일반 조합 질문일 가능성이 있기 때문이다.
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


def generate_answer_node(state: ChatbotGraphState) -> ChatbotGraphState:
    if state.get("error"):
        return state

    try:
        _chatbot, _retriever, llm = get_chatbot_components()

        skill_shortcut_text = get_skill_shortcut_text()
        role_filter = state.get("role_filter") or "all"
        current_hero = state.get("current_hero")
        current_hero_role = state.get("current_hero_role")
        has_stats = state.get("has_stats", False)
        # 이번 턴에 적이 실제로 언급되지 않았다면 답변에서도 "확정된 상대"로 다루지 않는다.
        enemy_named_this_turn = state.get("enemy_named_this_turn", False)
        # current_hero가 이전 턴에서 이어받은 값일 뿐, 이번 메시지에서 다시 확인되지
        # 않은 상태. 이럴 때 역할을 강제로 제한하면 실제로는 영웅과 무관한 일반
        # 질문(예: 팀 조합 질문)인데도 옛 영웅 기준으로 답이 좁혀지는 사고가 난다.
        current_hero_uncertain = state.get("current_hero_uncertain", False)

        # current_hero_role(실제로 지금 플레이 중인 영웅의 역할)이 가장 신뢰도 높은
        # 정보다. role_filter는 세션에 걸쳐 누적되는 값이라 이전 턴의 잔존값이 남아있을
        # 수 있다(예: 예전에 "탱커 추천해줘"라고 물어봤던 role_filter='tank'가 그대로
        # 남아, 정작 지금은 에코를 플레이 중인데 탱커 목록으로 답변이 제한되는 사고).
        # 따라서 current_hero_role을 항상 최우선으로 하고, role_filter는 current_hero_role이
        # 아예 없을 때(역할이 불명확한 일반 질문)만 보조적으로 사용한다.
        if current_hero_role and current_hero_role in ROLE_HEROES and role_filter != current_hero_role:
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
        elif role_filter in ROLE_HEROES:
            allowed_heroes_text = (
                f"영웅 교체 추천은 반드시 {ROLE_LABELS.get(role_filter)} 역할만:\n"
                f"{', '.join(ROLE_HEROES[role_filter])}"
            )
            answer_allowed_hero_set = set(ROLE_HEROES[role_filter])
        elif current_hero_role and current_hero_role in ROLE_HEROES:
            allowed_heroes_text = (
                f"사용자는 현재 {ROLE_LABELS.get(current_hero_role)}({current_hero})를 플레이 중이다.\n"
                f"영웅 교체를 추천할 때는 반드시 같은 {ROLE_LABELS.get(current_hero_role)} 역할 영웅만 추천해라:\n"
                f"{', '.join(ROLE_HEROES[current_hero_role])}\n"
                f"힐러 교체, 탱커 교체 등 다른 역할 영웅 추천은 절대 하지 마라."
            )
            answer_allowed_hero_set = set(ROLE_HEROES[current_hero_role])
        else:
            allowed_heroes_text = "역할 제한 없음. 상황에 맞는 영웅을 자유롭게 추천해도 된다."
            answer_allowed_hero_set = None

        enemy_stats = state.get("enemy_stats") or {}
        my_stats = state.get("my_stats") or {}
        my_team_stats = state.get("my_team_stats") or {}

        enemy_stat_text = _format_stat_text(enemy_stats, "상대팀")
        my_stat_text = _format_stat_text(my_stats, "나")
        team_stat_text = _format_stat_text(my_team_stats, "우리팀")
        stat_summary = "\n".join(filter(None, [enemy_stat_text, my_stat_text, team_stat_text]))

        stat_analysis_instruction = ""
        if has_stats:
            stat_analysis_instruction = """
스탯 분석 지시:
- 사용자가 입력한 스탯을 바탕으로 현재 상황을 구체적으로 짚어줘라.
- 내 스탯이 있으면: 딜량/킬/데스 수치를 언급하며 잘한 점과 개선할 점을 말해라.
- 상대 스탯이 있으면: 딜량/킬이 높은 상대를 먼저 언급하고 어떻게 대처할지 설명해라.
- 수치가 낮은 항목(예: 딜량 낮음, 데스 많음)의 원인과 해결책을 알려줘라.
"""

        # 이번 턴에 언급되지 않은 상대 정보는 답변 프롬프트에서도 "없음"으로 표시한다.
        display_target_enemy = state.get("target_enemy") if enemy_named_this_turn else None
        display_high_threat = state.get("high_threat_enemy") if enemy_named_this_turn else None
        display_enemy_team = state.get("enemy_team", []) if enemy_named_this_turn else []

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

현재 사용자 영웅: {current_hero} (역할: {ROLE_LABELS.get(current_hero_role, "알 수 없음") if current_hero_role else "알 수 없음"})
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
- 질문 의도: {state.get("intent")}
- 전략 판단: {state.get("recommendation_type")}
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
answer 안에서는 마크다운 문법(**볼드**, *   리스트, # 제목 등)을 쓰지 마라. 줄바꿈은 \\n으로,
목록은 "1. ", "2. " 같은 일반 숫자/기호로만 표현해라. 마크다운 기호는 JSON 문자열 파싱을
깨뜨릴 수 있으므로 절대 사용하지 마라.

{{
  "answer": "사용자에게 보여줄 최종 답변 (줄바꿈은 \\n으로 표현, 마크다운 금지)",
  "used_doc_ids": [1, 2]
}}

답변 작성 규칙:
1. 첫 문장은 이번 질문의 핵심에 바로 답해라. 질문이 예/아니오나 선택을 묻는 것이라면
   운영 팁부터 늘어놓지 말고, 먼저 그 질문에 직접 답한 뒤 이유와 팁을 설명해라.
2. 영웅 교체를 추천할 때는 위 허용 목록 안에서만 골라라.
3. 힐 부족·팀 문제처럼 현재 역할로 해결하기 어려운 상황이라면,
   역할 변경 대신 "현재 영웅으로 생존력을 높이는 법" 또는 "힐팩 활용" 등 대안을 제시해라.
4. 스킬명에 단축키를 같이 써라. 예: 다이너마이트(shift), 코치건(e).
5. 마지막에 "바로 적용할 것 3가지"를 적어라.{enemy_naming_instruction}{swap_decision_instruction}

절대 금지:
- 허용 목록 밖 역할의 영웅 추천 (역할 고정으로 게임 내 선택 불가)
- "[문서 1]", "(문서 1)" 같은 어떤 형태의 출처 표시도 금지
- "문서에 따르면", "~에서 언급했듯이" 같은 자료 인용 표현
- 마크다운 문법(**, *, #, - 등)
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
                cleaned = re.sub(r'```(?:json)?\s*[\s\S]*?```', '', raw_text, flags=re.IGNORECASE)
                cleaned = re.sub(r'\{[\s\S]*"answer"[\s\S]*\}', '', cleaned)
                raw_answer = cleaned.strip() or raw_text
                logger.warning("[FALLBACK] answer 필드 추출 실패, raw_text 정제본 사용")
            used_doc_ids = []

        if not isinstance(used_doc_ids, list):
            used_doc_ids = []
        used_doc_ids = [int(d) for d in used_doc_ids if str(d).isdigit()]

        answer = sanitize_answer_for_user(raw_answer)

        if answer_allowed_hero_set is not None:
            # 사용자가 메시지 원문에서 이미 언급한 영웅 이름(예: "윈스턴이랑 같이
            # 진입하는데"처럼 같은 편 동료를 가리키는 경우)은 위반 검사에서 제외한다.
            # 이런 이름은 LLM이 새로 "추천"한 게 아니라 사용자의 말을 그대로
            # 인용/응답한 것일 뿐이므로, 다른 역할이라는 이유로 치환해버리면
            # "윈스턴이랑 같이 가는데"가 "다른 영웅이랑 같이 가는데"로 바뀌는
            # 식의 엉뚱한 결과가 나온다(실제 발생 사례).
            user_mentioned_heroes = set(find_all_heroes(state.get("message", "")))
            forbidden_in_answer = [
                h for h in find_all_heroes(answer)
                if h not in answer_allowed_hero_set and h not in user_mentioned_heroes
            ]
            if forbidden_in_answer:
                logger.warning(
                    "[ROLE VIOLATION] 답변에 허용 범위 밖 영웅 등장: %s (current_hero=%s role=%s, "
                    "user_mentioned=%s) — 단어만 치환",
                    forbidden_in_answer, current_hero, current_hero_role, user_mentioned_heroes,
                )
                role_label_kor = ROLE_LABELS.get(current_hero_role, "현재 역할")

                # 줄 전체를 삭제하면 "교체할지 유지할지"같은 핵심 판단 문장까지
                # 통째로 날아갈 수 있다 (실제 사례: "애쉬로 바꾸는 게 낫습니다"가
                # 같은 줄의 다른 위반 단어 때문에 삭제되어 판단 자체가 사라짐).
                # 대신 위반 영웅 이름만 정밀하게 치환하고, 문장 구조는 보존한다.
                forbidden_hero_names = set(forbidden_in_answer)
                # HEROES 원본 표기(예: "솔저: 76")까지 포함해 실제 텍스트에 등장하는
                # 모든 표기 형태를 치환 대상으로 잡는다.
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

        return {"answer": answer, "used_doc_ids": used_doc_ids, "used_doc_metadata": used_doc_metadata}

    except Exception as exc:
        logger.exception("generate_answer_node 오류: %s", exc)
        return {"error": str(exc)}


def generate_suggested_questions_node(state: ChatbotGraphState) -> ChatbotGraphState:
    if state.get("error"):
        return state

    try:
        _chatbot, _retriever, llm = get_chatbot_components()

        prompt = f"""
너는 오버워치 코칭 챗봇 UI의 "빠른 질문 버튼" 생성기다.

역할 정의:
- 사용자가 AI의 답변을 읽은 뒤 다음으로 보낼 법한 메시지를 예측해서 버튼 텍스트로 만든다.
- 버튼을 클릭하면 그 텍스트가 그대로 사용자 입력창에 입력된다.
- 즉, 생성하는 문장은 반드시 "사용자가 AI에게 보내는 질문/요청" 형태여야 한다.

현재 컨텍스트:
- 현재 영웅: {state.get("current_hero")}
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
- 반드시 사용자 1인칭 시점의 짧은 질문/요청문으로 작성. 예: "더 자세히 알려줘", "영웅 바꿔야 해?", "이 상황에서 딜 더 올리는 법은?"
- AI가 사용자에게 묻는 형태 절대 금지. 예: "어떤 영웅을 사용했나요?" (X)
- AI가 추가 설명하는 형태 절대 금지. 예: "궁극기 활용법을 알아보세요" (X)
- 이번 답변 내용과 자연스럽게 이어지는 흐름으로 작성.
- 버튼 라벨이므로 15자 이내의 짧은 문장.
- 문서, 출처, 내부 시스템 용어 금지.
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
    if target_enemy:
        return [
            f"{target_enemy} 상대할 때 피해야 할 행동은?",
            f"{target_enemy} 카운터 영웅 알려줘",
            f"{target_enemy} 잡는 스킬 순서는?",
        ]
    return [
        "지금 상황에서 먼저 할 일 알려줘",
        "스킬 순서 어떻게 써야 해?",
        "영웅 바꿔야 하는 타이밍은?",
    ]


def format_response_node(state: ChatbotGraphState) -> ChatbotGraphState:
    if state.get("error"):
        return {"result": {"error": state["error"]}}

    answer = sanitize_answer_for_user(state.get("answer", ""))

    return {
        "result": {
            "answer": answer,
            "intent": state.get("intent"),
            "recommendation_type": state.get("recommendation_type"),
            "recommended_heroes": state.get("recommended_heroes", []),
            "suggested_questions": state.get("suggested_questions", []),
            "choice_buttons": state.get("choice_buttons", []),
            "context_patch": state.get("context_patch", {}),
            "has_stats": state.get("has_stats", False),
        }
    }


def _format_stat_text(stats: Dict[str, Any], label: str = "") -> str:
    if not stats:
        return ""
    lines = [f"[{label}]"] if label else []
    for hero, s in stats.items():
        parts = []
        if s.get("kills") is not None:
            parts.append(f"킬 {s['kills']}")
        if s.get("assists") is not None:
            parts.append(f"도움 {s['assists']}")
        if s.get("deaths") is not None:
            parts.append(f"데스 {s['deaths']}")
        if s.get("damage") is not None:
            parts.append(f"딜량 {s['damage']}")
        if s.get("healing") is not None:
            parts.append(f"힐량 {s['healing']}")
        lines.append(f"- {hero}: {', '.join(parts)}")
    return "\n".join(lines)


def route_after_validation(state: ChatbotGraphState) -> str:
    return "format_response" if state.get("error") else "parse_stats"

def route_after_parse_stats(state: ChatbotGraphState) -> str:
    return "format_response" if state.get("error") else "llm_parse_context"

def route_after_context_merge(state: ChatbotGraphState) -> str:
    return "clarify_role_filter" if should_ask_role_filter(state) else "build_retrieval_queries"

def route_after_retrieve(state: ChatbotGraphState) -> str:
    return "format_response" if state.get("error") else "judge_strategy"

def route_after_judge(state: ChatbotGraphState) -> str:
    return "format_response" if state.get("error") else "generate_answer"

def route_after_generate(state: ChatbotGraphState) -> str:
    return "format_response" if state.get("error") else "generate_suggested_questions"


def build_chatbot_graph():
    graph = StateGraph(ChatbotGraphState)

    graph.add_node("validate_input", validate_input_node)
    graph.add_node("parse_stats", parse_stats_from_text_node)
    graph.add_node("llm_parse_context", llm_parse_context_node)
    graph.add_node("merge_context", merge_context_node)
    graph.add_node("clarify_role_filter", clarify_role_filter_node)
    graph.add_node("build_retrieval_queries", build_retrieval_queries_node)
    graph.add_node("retrieve_docs", retrieve_docs_node)
    graph.add_node("judge_strategy", judge_strategy_node)
    graph.add_node("generate_answer", generate_answer_node)
    graph.add_node("generate_suggested_questions", generate_suggested_questions_node)
    graph.add_node("format_response", format_response_node)

    graph.add_edge(START, "validate_input")
    graph.add_conditional_edges("validate_input", route_after_validation,
        {"parse_stats": "parse_stats", "format_response": "format_response"})
    graph.add_conditional_edges("parse_stats", route_after_parse_stats,
        {"llm_parse_context": "llm_parse_context", "format_response": "format_response"})
    graph.add_edge("llm_parse_context", "merge_context")
    graph.add_conditional_edges("merge_context", route_after_context_merge,
        {"clarify_role_filter": "clarify_role_filter", "build_retrieval_queries": "build_retrieval_queries"})
    graph.add_edge("clarify_role_filter", "format_response")
    graph.add_edge("build_retrieval_queries", "retrieve_docs")
    graph.add_conditional_edges("retrieve_docs", route_after_retrieve,
        {"judge_strategy": "judge_strategy", "format_response": "format_response"})
    graph.add_conditional_edges("judge_strategy", route_after_judge,
        {"generate_answer": "generate_answer", "format_response": "format_response"})
    graph.add_conditional_edges("generate_answer", route_after_generate,
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
) -> Dict[str, Any]:
    logger.info(
        "[GRAPH START] message=%s role_filter=%s context=%s",
        message, role_filter, conversation_context,
    )

    graph = get_chatbot_graph()
    final_state = graph.invoke({
        "message": message,
        "conversation_context": conversation_context or {},
        "role_filter": role_filter,
    })

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
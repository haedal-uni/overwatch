import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .chatbot_service import get_chatbot_components

logger = logging.getLogger(__name__)


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
    is_team_comp_question: bool


HEROES = [
    "겐지", "트레이서", "솜브라", "리퍼", "캐서디", "애쉬", "위도우메이커", "한조",
    "소전", "솔저76", "파라", "에코", "메이", "토르비욘", "정크랫", "바스티온",
    "시메트라", "벤처", "벤데타", "안란", "엠레", "프레야", "시에라",
    "라인하르트", "윈스턴", "디바", "자리야", "오리사", "시그마", "라마트라",
    "레킹볼", "둠피스트", "로드호그", "정커퀸", "마우가", "해저드", "도미나",
    "아나", "키리코", "모이라", "루시우", "브리기테", "젠야타", "바티스트",
    "메르시", "일리아리", "라이프위버", "주노", "제트팩 캣", "우양", "미즈키"
]

HERO_ALIASES = {
    "둠피": "둠피스트",
    "솔저": "솔저76",
    "솔져": "솔저76",
    "솔저: 76": "솔저76",
    "D.Va": "디바",
    "디바": "디바",
    "바스" : "바스티온",
    "시메": "시메트라",
    "라인": "라인하르트",
    "정크" : "정크랫",
    "브리" : "브리기테",
    "위도우" : "위도우메이커", 
    "호그" : "로드호그",

}

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
    t0 = time.time()
    response = llm.invoke(prompt)
    logger.info("[TIMING] LLM 호출: %.2fs (prompt_len=%d)", time.time() - t0, len(prompt))
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

    if hero in HERO_ALIASES:
        return HERO_ALIASES[hero]

    if hero == "솔저: 76":
        return "솔저76"

    if hero == "D.Va":
        return "디바"

    return hero


def find_first_hero(text: str) -> Optional[str]:
    # 정식 영웅명 우선
    for hero in HEROES:
        if hero in text:
            return normalize_hero_name(hero)

    # 별칭 처리
    for alias, canonical in HERO_ALIASES.items():
        if alias in text:
            return canonical

    return None


def hero_mentioned_in_text(hero: Optional[str], text: str) -> bool:
    """
    정규화된 영웅명이 원문에 직접 또는 별칭으로 언급되었는지 확인한다.
    예: hero='둠피스트', text='둠피가 나만 노려' -> True
    """
    if not hero or not text:
        return False

    normalized = normalize_hero_name(hero)

    # 정식 이름 그대로 등장
    if normalized and normalized in text:
        return True

    # HEROES 원본 표기 확인
    for h in HEROES:
        if normalize_hero_name(h) == normalized and h in text:
            return True

    # 별칭 확인
    for alias, canonical in HERO_ALIASES.items():
        if canonical == normalized and alias in text:
            return True

    return False


def hero_mentioned_as_current_hero(hero: Optional[str], text: str) -> bool:
    """
    영웅 이름이 문장에 등장했더라도 "상대 겐지"처럼 적으로 언급된 경우와
    "겐지로 할게"처럼 사용자가 직접 플레이한다고 말한 경우를 구분한다.
    """
    normalized = normalize_hero_name(hero)
    if not normalized or not text:
        return False

    names = set()
    for h in HEROES:
        if normalize_hero_name(h) == normalized:
            names.add(h)
    for alias, canonical in HERO_ALIASES.items():
        if canonical == normalized:
            names.add(alias)
    names.add(normalized)

    for name in names:
        escaped = re.escape(name)
        if re.search(rf"(?:난|나는|나|저는|제가|내가)\s*{escaped}", text):
            return True
        if re.search(rf"{escaped}\s*(?:로|으로)\s*(?:플레이|하고|하는|할|가|갈|쓰|쓸|이기|즐기)", text):
            return True
        # "윈스턴으로 수비하는데"처럼 "로/으로"와 활용형(하고/하는/할 등) 사이에
        # 수비·공격·포지션 같은 역할 명사가 끼어 있는 경우도 자기 영웅 선언으로
        # 인정한다. 명사 없이 바로 붙는 위 패턴만으로는 이런 문장을 놓친다.
        if re.search(
            rf"{escaped}\s*(?:로|으로)\s*[가-힣]{{0,4}}\s*"
            rf"(?:하고|하는|할|해서|하면서|하는데|하다가)",
            text,
        ):
            return True
        # "파라를 하고 싶은데"처럼 영웅 이름과 활용형 사이에 목적격 조사(을/를)가
        # 끼는 경우도 인정한다 — 조사 없이 바로 붙는 경우도 \s*가 0개를 허용하므로
        # 기존 매칭에는 영향이 없다.
        if re.search(
            rf"{escaped}\s*(?:을|를)?\s*(?:하고\s*있|하는\s*중|하고\s*싶|하고싶|할\s*거|할건데|"
            rf"쓰고\s*싶|쓰고싶|쓸건데|계속|유지|고정|원챔)",
            text,
        ):
            return True
        # "시그마인데 둠피가 날뛰어", "겐지인데 상대 아나..."처럼 영웅 이름 바로 뒤에
        # "인데/이야/임/입니다" 같은 서술격 조사만 붙여 지금 플레이 중인 영웅을 짧게
        # 밝히는 표현도 자기 영웅 선언으로 인정한다. 위 "로/으로" 계열 패턴들은
        # 이런 구문을 놓친다.
        if re.search(rf"{escaped}\s*(?:인데요|인데|이야|이거든|임|입니다|이에요|예요)", text):
            return True
        # "겐지 하는데 윈스턴 힘들어"처럼 조사 없이 "하다" 활용형만 바로 붙여
        # 지금 플레이 중인 영웅을 밝히는 표현("X 하는데/할 때/하다가")도 인정한다.
        if re.search(rf"{escaped}\s*(?:하는데요|하는데|할\s*때|하다가)", text):
            return True
        # "겐지로 윈스턴 상대법 알려줘"처럼 "로/으로" 뒤에 다른 영웅 이름이 먼저
        # 나오고 그 뒤에 상대법류 표현이 붙는 경우, 위의 근접 패턴들은 "로"와
        # 활용형 사이에 다른 영웅 이름이 끼어 있어 놓친다. 문장에 상대법류
        # 표현이 있고 이 영웅이 "로/으로"로 이어진다면 자기 영웅 선언으로 본다.
        if (
            any(word in text for word in _STAY_OPERATION_WORDS)
            and re.search(rf"{escaped}\s*(?:로|으로)", text)
        ):
            return True
        # "트레이서를 고르면", "이걸로 픽할까", "브리기테 선택하면"처럼 아직
        # 확정은 아니지만 자신이 그 영웅을 고를지 고려/선언하는 표현도 자기
        # 영웅 선언으로 인정한다. 이게 없으면 "X를 고르면 Y가 잘 나올까?" 같은
        # 질문에서 X가 자기 후보 영웅으로 인식되지 않아, current_hero_role로
        # 역할이 자동으로 정해지지 못하고 불필요하게 역할을 되묻게 된다.
        if re.search(
            rf"{escaped}\s*(?:을|를)?\s*(?:고르면|고를까|고르는\s*게|고르는게|"
            rf"골라도|픽하면|픽할까|선택하면|선택할까)",
            text,
        ):
            return True

    return False


def find_all_heroes(text: str) -> List[str]:
    found = []

    for hero in HEROES:
        if hero in text:
            normalized = normalize_hero_name(hero)
            if normalized and normalized not in found:
                found.append(normalized)

    for alias, canonical in HERO_ALIASES.items():
        if alias in text and canonical not in found:
            found.append(canonical)

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


# 상대팀/아군팀을 한 문장에서 함께 나열하는 질문(예: "상대팀은 A B C D E 조합이고
# 우리팀 조합은 C E F G일때...")에서, 각 팀의 정규식 캡처 그룹(`[가-힣A-Za-z0-9\.,\s]+`)
# 은 한글 조사까지 넓게 허용하다 보니 상대 쪽 캡처가 "우리팀"/"아군" 마커까지 그대로
# 삼켜버려 상대팀 목록에 아군 영웅이 섞여 들어가는 문제가 생긴다(그 반대도 마찬가지).
# 캡처된 조각 안에 상대편 팀을 가리키는 마커가 나오면 그 앞까지만 잘라 교차 오염을 막는다.
_ALLY_TEAM_MARKERS = ["우리팀", "아군", "우리는", "우리가"]
_ENEMY_TEAM_MARKERS = ["상대팀", "상대는", "상대가", "상대", "적은", "적팀은"]


def _truncate_before_markers(chunk: str, markers: List[str]) -> str:
    cut = len(chunk)
    for marker in markers:
        idx = chunk.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return chunk[:cut]


def extract_enemy_team(text: str) -> List[str]:
    patterns = [
        r"상대팀\s*조합은\s*([가-힣A-Za-z0-9\.,\s]+)",
        r"상대팀은\s*([가-힣A-Za-z0-9\.,\s]+)",
        r"상대가\s*([가-힣A-Za-z0-9\.,\s]+)",
        r"상대는\s*([가-힣A-Za-z0-9\.,\s]+)",
        r"상대\s*조합은\s*([가-힣A-Za-z0-9\.,\s]+)",
        r"적은\s*([가-힣A-Za-z0-9\.,\s]+)",
        r"적팀은\s*([가-힣A-Za-z0-9\.,\s]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        chunk = _truncate_before_markers(match.group(1), _ALLY_TEAM_MARKERS)
        heroes = find_all_heroes(chunk)
        if heroes:
            return heroes
    return []


def extract_ally_team(text: str) -> List[str]:
    patterns = [
        r"우리팀\s*조합은\s*([가-힣A-Za-z0-9\.,\s]+)",
        r"우리팀은\s*([가-힣A-Za-z0-9\.,\s]+)",
        r"우리\s*조합은\s*([가-힣A-Za-z0-9\.,\s]+)",
        r"아군\s*조합은\s*([가-힣A-Za-z0-9\.,\s]+)",
        r"아군은\s*([가-힣A-Za-z0-9\.,\s]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        chunk = _truncate_before_markers(match.group(1), _ENEMY_TEAM_MARKERS)
        heroes = find_all_heroes(chunk)
        if heroes:
            return heroes
    return []


# 표준 팀 구성(탱커 1 / 딜러 2 / 힐러 2)을 기준으로, 이미 정해진 아군 인원의 역할을
# 보고 남은 한 자리가 어떤 역할인지 추론한다. 정확히 한 역할만 자리가 비어 있을 때만
# (즉 나머지 두 역할은 이미 꽉 찼을 때만) 그 역할을 확정하고, 그렇지 않으면(아직
# 아군 정보가 부족해 여러 역할이 비어 있을 수 있으면) None을 반환해 기존처럼
# 역할을 되묻게 한다.
TEAM_COMP_ROLE_QUOTA = {"tank": 1, "damage": 2, "support": 2}


def infer_missing_role_from_team_comp(ally_heroes: List[str]) -> Optional[str]:
    role_counts = {"tank": 0, "damage": 0, "support": 0}
    for hero in ally_heroes:
        role = HERO_TO_ROLE.get(normalize_hero_name(hero))
        if role in role_counts:
            role_counts[role] += 1

    open_roles = [
        role for role, quota in TEAM_COMP_ROLE_QUOTA.items()
        if role_counts[role] < quota
    ]
    if len(open_roles) == 1:
        return open_roles[0]
    return None


def detect_stat_input(message: str) -> bool:
    stat_keywords = ["킬", "데스", "딜", "힐", "도움", "어시", "사망", "피해", "치유"]
    has_keyword = any(kw in message for kw in stat_keywords)
    has_number = bool(re.search(r"\d{2,}", message))
    return has_keyword and has_number

def detect_wants_to_keep_hero(text: str) -> bool:
    """
    사용자가 특정 영웅을 바꾸기보다 계속 하거나,
    그 영웅으로 이기고 싶어하는 표현인지 판단한다.
    """

    # 영웅 이름이 아예 없으면 이 함수에서는 판단하지 않음
    hero = find_first_hero(text)
    if not hero:
        return False
    if hero == find_enemy_mentioned_hero(text):
        return False

    # 붙여 쓰기 대응: "파라쓸건데", "파라할건데", "파라하고싶어"
    compact = re.sub(r"\s+", "", text)

    keep_patterns = [
        r"(하고싶|하고싶어|해보고싶|쓰고싶|쓸거|쓸건데|할거|할건데)",
        r"(계속|유지|고정|원챔|포기안|안바꾸|바꾸지않)",
        r"(이기고싶|이기면서|즐기고싶|즐기면서)",
        r"(해도돼|해도될까|가능할까|괜찮을까)",
        # "트레이서를 고르면 우리팀 딜이 잘 나올까?"처럼 아직 확정은 아니지만
        # 자신이 그 영웅을 고를지 고려/선언하는 표현. hero_mentioned_as_current_hero
        # 에도 같은 패턴이 있는데, 그건 "LLM이 이미 current_hero로 준 값을
        # 검증"할 때만 쓰이고, LLM이 애초에 current_hero를 null로 잘못 판단해
        # 이 규칙 기반 폴백(infer_current_hero)까지 내려온 경우에는 여기도
        # 함께 인식해야 실제로 current_hero가 채워진다.
        r"(고르면|고를까|고르는게|골라도|픽하면|픽할까|선택하면|선택할까)",
    ]

    return any(re.search(pattern, compact) for pattern in keep_patterns)


# "상대법", "어떻게 상대", "어떻게 잡", "어떻게 막", "파훼", "대처", "견제"는 그 자체로는
# counter를 뜻하지 않는다 — "겐지로 윈스턴 상대법 알려줘"처럼 자기 영웅을 유지한 채
# 상대법을 묻는 stay 질문에도 똑같이 등장하기 때문이다. counter는 "카운터/상성 목록을
# 대표적으로 알려달라"는 명확한 요청일 때만 해당한다.
_COUNTER_LIST_WORDS = ["카운터", "상성", "상대하기 어려운", "상대하기 쉬운"]
_STAY_OPERATION_WORDS = ["상대법", "어떻게 상대", "어떻게 잡", "어떻게 막", "파훼", "대처", "견제"]

_SITUATION_PATTERNS = [
    re.compile(r"압박"),
    re.compile(r"계속.{0,10}(물어|물고|죽어|죽는|터져|터지)"),
    re.compile(r"뒤가\s*터"),
    re.compile(r"못\s*막겠"),
    re.compile(r"힘들어"),
]


def detect_stay_with_named_hero(text: str) -> bool:
    """"겐지로 윈스턴 상대법 알려줘", "파라로 솔저 어떻게 상대해?"처럼 (자기
    영웅)로 + 상대법/파훼/대처류 표현이 함께 있으면, 그 영웅을 유지한 채 상대법을
    묻는 stay 질문으로 본다."""
    if not any(word in text for word in _STAY_OPERATION_WORDS):
        return False

    names = list(HEROES) + list(HERO_ALIASES.keys())
    for name in names:
        if re.search(rf"{re.escape(name)}\s*(?:로|으로)", text):
            return True
    return False


def detect_situation(text: str) -> bool:
    """"파라가 계속 압박해", "둠피가 계속 힐러 물어"처럼 인게임에서 겪고 있는
    위기/압박 상황을 그대로 토로하는 표현인지 판단한다."""
    return any(pattern.search(text) for pattern in _SITUATION_PATTERNS)


_SWAP_TRIGGER_PATTERN = re.compile(r"말고|다른\s*영웅|바꾸|바꿀|바꿔|교체|변경|픽\s*추천")


def infer_intent_by_rule(message: str, context: Dict[str, Any]) -> str:
    text = message.strip()

    # 1. 영웅을 바꾸겠다는 표현이 명확하면 swap. "계속"처럼 stay 신호로도 흔히
    # 쓰이는 일반 단어와 겹치지 않는, 가장 구체적인 신호이므로 최우선으로 본다.
    # 단, "안 바꾸고", "바꾸지 않고"는 stay로 처리
    if _SWAP_TRIGGER_PATTERN.search(text):
        compact = re.sub(r"\s+", "", text)

        if any(word in compact for word in ["안바꾸", "바꾸지않", "그대로", "유지", "고정"]):
            return "stay"

        return "swap"

    # 2. 인게임 위기/압박 상황을 토로하는 situation
    # 예: "파라가 계속 압박해", "둠피가 계속 힐러 물어", "트레이서 때문에 뒤가 터져"
    # detect_wants_to_keep_hero(아래 3번)보다 먼저 확인한다 — "계속"이라는 흔한
    # 단어 하나만으로 상황 토로 문장까지 "영웅을 유지하고 싶다"는 뜻으로 잘못
    # 묶이는 것을 막기 위해서다.
    if detect_situation(text):
        return "situation"

    # 3. 사용자가 특정 영웅을 유지하고 싶어하는 경우
    # 예: "파라 하고싶어", "파라쓸건데", "파라 원챔인데", "파라로 이기고 싶어"
    if detect_wants_to_keep_hero(text):
        return "stay"

    # 4. 특정 영웅을 유지한 채 상대법을 묻는 stay
    # 예: "겐지로 윈스턴 상대법 알려줘", "파라로 솔저 어떻게 상대해?"
    if detect_stay_with_named_hero(text):
        return "stay"

    # 5. 대표 카운터/상성 목록을 묻는 counter
    # 예: "겐지 카운터 알려줘", "겐지 상성 알려줘", "겐지가 상대하기 어려운 영웅 알려줘"
    if any(word in text for word in _COUNTER_LIST_WORDS):
        return "counter"

    # 6. 유지 의도
    if any(word in text for word in ["계속 쓰고", "계속 하고", "유지", "그 영웅", "현재 영웅", "내가 계속"]):
        return "stay"

    # 7. 플레이 개선 의도
    if any(word in text for word in ["딜량", "데스", "킬", "스탯", "어떻게 플레이", "어떻게 해야", "운영", "잘하는 법"]):
        return "performance_improve"

    # 8. 맵 전략 의도
    if any(word in text for word in ["맵", "공격", "수비", "거점"] + MAPS):
        return "map_strategy"

    if detect_stat_input(text):
        return "performance_improve"

    previous_intent = context.get("last_intent")
    if previous_intent in ["counter", "stay", "swap", "performance_improve", "map_strategy", "situation"]:
        return previous_intent

    return "general"


# "OO 때문에", "상대 OO", "OO를 카운터" 처럼 문장에서 '상대(적) 영웅'을 가리키는
# 패턴. infer_target_enemy와 infer_current_hero가 공유한다 — current_hero
# 추론이 이 패턴에 걸리는 영웅(즉, 명백히 "상대"로 언급된 영웅)을 실수로
# "지금 플레이 중인 영웅"으로 잘못 집어가지 않도록 막기 위해서다.
# 조사와 동사 사이에 흔히 끼는 부사(예: "OO를 일단 막아야겠어"). \s*만으로는
# 이 부사를 건너뛸 수 없어 좁히기 패턴이 매칭에 실패하므로 명시적으로 허용한다.
_NARROWING_FILLER = r"(?:\s*(?:일단|우선|먼저|이번엔|반드시|꼭|그냥))?"

ENEMY_MENTION_PATTERNS = [
    r"([가-힣A-Za-z0-9\.]+)[이가]\s*(?:우리\s*팀|아군|힐러|팀원)",
    r"([가-힣A-Za-z0-9\.]+)\s*때문에",
    rf"([가-힣A-Za-z0-9\.]+)[을를]?{_NARROWING_FILLER}\s*카운터",
    rf"([가-힣A-Za-z0-9\.]+)[을를]?{_NARROWING_FILLER}\s*견제",
    rf"([가-힣A-Za-z0-9\.]+)[을를]?{_NARROWING_FILLER}\s*잡",
    rf"([가-힣A-Za-z0-9\.]+)[을를]?{_NARROWING_FILLER}\s*막",
    rf"([가-힣A-Za-z0-9\.]+)[을를]?{_NARROWING_FILLER}\s*처리",
    r"상대[가은는]?\s*([가-힣A-Za-z0-9\.]+)",
    r"상대\s*([가-힣A-Za-z0-9\.]+)",
]

# 사용자가 특정 영웅 이름 대신 "상대 힐러/딜러/탱커부터 처리하려고" 처럼 역할로만
# 카운터 우선순위를 좁히는 경우. 이때는 상대 조합에 어떤 영웅이 있든, 그 역할
# 하나에 집중하겠다는 의미이므로 다른 상대 영웅을 같이 언급하면 안 된다.
ENEMY_ROLE_FOCUS_WORDS = {"탱커": "tank", "딜러": "damage", "힐러": "support"}
ENEMY_ROLE_FOCUS_LABELS = {"tank": "상대 탱커", "damage": "상대 딜러", "support": "상대 힐러"}
ENEMY_ROLE_FOCUS_PATTERN = re.compile(
    rf"상대\s*(탱커|딜러|힐러){_NARROWING_FILLER}\s*(?:카운터|견제|잡|막|처리)"
)


def find_enemy_role_focus(text: str) -> Optional[str]:
    match = ENEMY_ROLE_FOCUS_PATTERN.search(text)
    if not match:
        return None
    return ENEMY_ROLE_FOCUS_WORDS.get(match.group(1))


def normalize_hero_candidate(candidate: Optional[str]) -> Optional[str]:
    if not candidate:
        return None

    valid_heroes = {normalize_hero_name(h) for h in HEROES}
    cleaned = candidate.strip()
    normalized = normalize_hero_name(cleaned)
    if normalized in valid_heroes:
        return normalized

    # "상대 겐지가"처럼 자유 캡처 패턴이 이름 뒤 조사까지 먹는 경우 보정.
    # "이랑"/"과"/"와"는 "겐지랑 리퍼랑 둘 다"처럼 나열형 문장에서 자유 캡처가
    # 뒤에 붙는 접속 조사까지 삼키는 경우를 보정한다("랑"보다 길게 먼저 검사해야
    # "이랑"이 "랑"만 잘리고 "이"가 남는 일이 없다).
    for suffix in ["이랑", "랑", "과", "와", "이", "가", "은", "는", "을", "를", "도", "만"]:
        if cleaned.endswith(suffix):
            normalized = normalize_hero_name(cleaned[:-len(suffix)].strip())
            if normalized in valid_heroes:
                return normalized

    return None


# "솜브라랑 트레이서랑 둘 다 짤짤이딜이잖아"처럼 자기 후보 영웅 두 개를 나란히
# 비교/나열하는 문장("A랑 B랑 둘 다") 안에 등장하는 영웅은 상대(적)가 아니라
# 사용자 자신이 고려 중인 후보다. "우리팀은 A, B, C"처럼 마커가 있는 아군 나열
# (extract_ally_team)과 달리, 이런 비교문에는 그런 마커가 없어서 기존 아군
# 배제 로직이 놓친다. 다만 "상대"/"카운터"/"때문에" 같은 적대 신호가 함께
# 있으면(예: "겐지랑 리퍼 둘 다 상대하기 힘들어") 실제로 상대 조합을 가리키는
# 것일 수 있으므로 이 경우엔 적용하지 않는다.
ADVERSARIAL_SIGNAL_WORDS = ["상대", "카운터", "견제", "때문에"]

_SELF_COMPARISON_PATTERN = re.compile(
    r"([가-힣A-Za-z0-9\.]+)(?:이랑|랑|과|와)\s*"
    r"([가-힣A-Za-z0-9\.]+)(?:이랑|랑|과|와)?\s*둘\s*다"
)


def find_self_comparison_heroes(text: str) -> List[str]:
    if any(word in text for word in ADVERSARIAL_SIGNAL_WORDS):
        return []
    match = _SELF_COMPARISON_PATTERN.search(text)
    if not match:
        return []
    heroes = []
    for raw in match.groups():
        normalized = normalize_hero_candidate(raw)
        if normalized and normalized not in heroes:
            heroes.append(normalized)
    return heroes


def find_enemy_mentioned_hero(text: str) -> Optional[str]:
    for pattern in ENEMY_MENTION_PATTERNS:
        match = re.search(pattern, text)
        if match:
            candidate = normalize_hero_candidate(match.group(1))
            if candidate:
                return candidate
    return None


def infer_target_enemy(message: str, context: Dict[str, Any], intent: str) -> Optional[str]:
    text = message.strip()
    current_hero = normalize_hero_name(context.get("current_hero"))

    enemy_mentioned = find_enemy_mentioned_hero(text)
    if enemy_mentioned and enemy_mentioned != current_hero:
        return enemy_mentioned

    if intent == "swap":
        new_situation = bool(find_map(text) or find_side(text) or extract_enemy_team(text))
        return None if new_situation else context.get("target_enemy")

    # "메시지에 등장한 첫 영웅"을 상대로 보는 이 폴백은 적/아군을 구분하는
    # 명시적 신호(카운터/견제/잡/막/처리/때문에/상대)가 전혀 없을 때만 쓰는
    # 최후 수단이다. current_hero뿐 아니라, 이번 메시지에서 "우리팀은/아군은"
    # 같은 마커로 아군으로 언급된 영웅도 제외해야 한다 — 그렇지 않으면 아군
    # 조합을 나열하는 문장에서 첫 번째로 언급된 아군 영웅이 엉뚱하게 카운터
    # 대상으로 잘못 채택된다.
    heroes_in_text = find_all_heroes(text)
    ally_named_this_turn = set(extract_ally_team(text)) | set(find_self_comparison_heroes(text))
    heroes_in_text = [
        h for h in heroes_in_text
        if h != current_hero and h not in ally_named_this_turn
    ]

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

    # 이번 메시지 안에서 "상대 OO", "OO 때문에" 처럼 적으로 언급된 영웅도
    # 제외 대상에 추가한다. 그렇지 않으면 적 영웅 이름과 "계속" 같은 트리거
    # 단어가 같은 문장에 우연히 있을 때, 적 영웅을 사용자 본인이 플레이 중인
    # 영웅으로 잘못 인식하는 문제가 생긴다.
    enemy_mentioned = find_enemy_mentioned_hero(text)
    if enemy_mentioned:
        enemy_heroes.add(enemy_mentioned)

    if "말고" in text:
        before = text.split("말고")[0]
        hero = find_first_hero(before)
        if hero and hero not in enemy_heroes:
            return hero

    if detect_wants_to_keep_hero(text) or any(word in text for word in [
        "계속 쓰고",
        "계속 하고",
        "현재",
        "플레이",
        "하고 있",
        "하고 싶어",
        "하고싶어",
        "쓰고 싶어",
        "쓸건데",
        "쓸 거",
        "고정",
        "원챔",
    ]):
        hero = find_first_hero(text)
        if hero and hero not in enemy_heroes:
            return hero

    if intent in ["stay", "performance_improve", "swap", "map_strategy"]:
        for hero in find_all_heroes(text):
            if hero not in enemy_heroes:
                return hero

    return context.get("current_hero")


def role_filter_from_text(message: str) -> Optional[str]:
    # 역할 단어가 문장 전체(공백/조사 제거 후)와 사실상 같을 때만 "사용자 스스로의
    # 역할 선언"으로 본다. 문장 중간에 역할 단어가 섞여 있으면(예: "탱커 OO 견제
    # 하려고") 그건 상대 영웅의 역할을 설명하는 것일 수 있어, 그 경우까지 자기
    # 역할 선언으로 오인하면 안 된다.
    stripped = re.sub(r"[\s,.!?~]+", "", message)
    short_role_replies = {
        "tank": {"탱커", "탱커요", "탱커임", "탱커에요", "탱커입니다"},
        "damage": {"딜러", "딜러요", "딜러임", "딜러에요", "딜러입니다"},
        "support": {"힐러", "힐러요", "힐러임", "힐러에요", "힐러입니다", "지원가"},
        "all": {"전체", "전부"},
    }
    for role, words in short_role_replies.items():
        if stripped in words:
            return role

    if any(word in message for word in ["탱커로", "탱커 추천", "탱커가 잡혔", "탱커 해야"]):
        return "tank"
    if any(word in message for word in ["딜러로", "딜러 추천", "딜러가 잡혔", "딜러 해야"]):
        return "damage"
    if any(word in message for word in ["힐러로", "힐러 추천", "힐러가 잡혔", "힐러 해야", "지원가로", "지원가 추천"]):
        return "support"
    if any(word in message for word in ["전체로", "전체 추천", "전부 알려", "다 알려"]):
        return "all"
    return None


# current_hero가 전혀 파악되지 않은 채로 들어오면 역할을 반드시 되물어야 하는
# intent. "map_strategy"는 특정 영웅과 무관한 맵 운영 질문일 수 있어 제외한다.
ROLE_CLARIFICATION_INTENTS = {
    "performance_improve", "stay", "swap", "general", "counter", "situation", "composition",
}

# 답변을 문단 서술 대신 "상성 카드"(상대하기 어려운 영웅 / 쉬운 영웅)로 보여줘야 하는
# 경우. 카드는 "지금 어떤 영웅을 고를지" 추천하는 화면이므로, 사용자가 이미
# 플레이 중인 영웅을 밝힌 상태에서는 맞지 않는다 — 예: "키리코인데 상대 트레이서
# 어떻게 해?"는 키리코로 트레이서를 어떻게 상대할지 묻는 운영 질문이지, 트레이서를
# 카운터할 다른 영웅을 추천해달라는 게 아니다. 그래서 실제로 카드가 나가는 경우는
# 두 가지로 좁힌다(merge_context_node의 matchup_subject 계산 참고):
#   1) intent=="counter"이면서 아직 플레이 중인 영웅이 없는 경우(순수 픽 추천 질문).
#   2) intent=="swap"이면서 이미 쓰는 영웅이 있는 경우(그 영웅의 상성표를 보고
#      교체 여부를 판단하려는 질문).
# intent=="stay"(상성이 불리해도 지금 영웅을 유지하겠다는 질문)는 애초에 다른 영웅
# 추천이 목적이 아니므로 카드 대상에서 제외한다 — 기존처럼 stay_intent_block이
# 붙은 운영 조언 텍스트로 답한다. "performance_improve"(운영법·포지션·스킬 사용
# 시점 등)와 "general"/"map_strategy"도 상성 질문이 아니므로 제외.


def should_ask_role_filter(state: ChatbotGraphState) -> bool:
    """
    사용자가 지금 어떤 역할(탱커/딜러/힐러)로 플레이 중인지 전혀 알 수 없을 때
    역할을 먼저 물어봐야 하는지 판단한다. 두 가지 경우를 모두 포함한다.

    1) 상대 영웅을 카운터하려는 상황(intent=="counter")인데 상대는 지목했지만
       내 역할을 알 수 없는 경우 — 예전부터 있던 흐름.
    2) 상대 영웅을 지목했는지 여부와 무관하게, 지금 무슨 영웅으로 플레이
       중인지조차 전혀 모르는 상태에서 개인화된 답이 필요한 질문이 들어온 경우
       (상대 영웅 이름조차 없는 일반적인 대처법 질문도 포함). 이때도 역할을
       모르는 채로 바로 답하면 엉뚱한 역할 기준으로 추천하게 되므로, 2)번도
       반드시 역할부터 물어야 한다.

    role_filter(effective_role_filter)는 merge_context_node에서 이미
    explicit_role_filter → current_hero_role → 세션 잔존값 순으로 채워지므로,
    여기서 role_filter가 비어 있다는 것은 곧 "현재 역할을 어떤 방법으로도
    알아낼 수 없었다"는 뜻이다.
    """
    role_filter = state.get("role_filter")
    if role_filter:
        return False

    message = state.get("message", "")
    if role_filter_from_text(message):
        return False

    intent = state.get("intent")
    target_enemy = state.get("target_enemy")

    if intent == "counter" and target_enemy:
        return True

    if not state.get("current_hero") and intent in ROLE_CLARIFICATION_INTENTS:
        return True

    return False


def sanitize_answer_for_user(answer: str, keep_dash_bullets: bool = False) -> str:
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
    if keep_dash_bullets:
        # 간단 모드는 "- "를 의도된 목록 기호로 쓰므로 보존한다. LLM이 실수로 섞어 쓸 수 있는
        # "*" 글머리 기호만 안전망으로 제거한다(프론트의 simpleMarkdown이 "- "를 <li>로 렌더링함).
        sanitized = re.sub(r"^\s*\*\s+", "", sanitized, flags=re.MULTILINE)
    else:
        sanitized = re.sub(r"^\s*[\*\-]\s+", "", sanitized, flags=re.MULTILINE)  # 글머리 기호 제거

    sanitized = re.sub(r"[ \t]+", " ", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    return sanitized.strip()


# ── "간단히" 스타일 전용: 예측 질문(suggested_questions)을 답변 생성 LLM 호출에
# 끼워 넣어, generate_suggested_questions_node의 별도 LLM 호출을 생략한다. ──
# "간단히"는 인게임 중 빠른 응답이 목적이라 LLM 호출 수 자체를 줄여야 하는데,
# 기존에는 답변 생성(judge_strategy 생략 후에도 1회) + 예측 질문(1회)로 최소
# 2번의 순차 호출이 필요했다. 답변 텍스트와 예측 질문을 한 번의 JSON 응답으로
# 함께 받으면 이 중 하나를 없앨 수 있다. "자세히" 스타일은 답변 품질을 우선해
# 기존처럼 별도 노드(generate_suggested_questions_node)를 그대로 거친다.
SUGGESTED_QUESTIONS_INLINE_SCHEMA_LINE = ',\n  "suggested_questions": ["질문1", "질문2", "질문3"]'
SUGGESTED_QUESTIONS_INLINE_RULES = """
suggested_questions 작성 규칙:
- 사용자가 이 답변을 본 뒤 다음으로 보낼 법한 1인칭 질문/요청문 3개, 각 15자 이내로.
- AI가 되묻거나 설명하는 형태 절대 금지 — 반드시 사용자가 보내는 문장이어야 한다.
- 카운터 대상/분석 대상이 있으면 최소 1개는 그 대상과 관련된 질문으로 써라.
- 사용자가 언급하지 않은 상대 영웅 이름을 새로 만들지 마라."""


def extract_inline_suggested_questions(parsed: Any) -> List[str]:
    if not isinstance(parsed, dict):
        return []
    raw = parsed.get("suggested_questions")
    if not isinstance(raw, list):
        return []
    questions = [str(q).strip() for q in raw if str(q).strip()]
    return questions[:3]


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

    message = state.get("message", "")

    # message가 비어 있는 턴은 실제 사용자 발화가 아니라 role_filter 버튼 클릭
    # 같은 순수 선택 신호다(chat.html의 sendRoleFilter가 message: ''로 보냄).
    # 이런 턴을 LLM에 그대로 넘기면 "오버워치와 무관해 보이는 빈 메시지"를
    # intent="off_topic"으로 잘못 분류해버려, merge_context_node가 원래
    # 질문(pending_question)을 이어받기도 전에 고정 오프토픽 답변으로 새버리는
    # 문제가 생긴다. 뽑아낼 정보 자체가 없으므로 LLM 호출 없이 그대로 규칙
    # 기반 폴백에 맡긴다.
    if not message.strip():
        return {}

    try:
        _chatbot, _retriever, llm = get_chatbot_components()

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
3. swap일 때 current_hero는 바꾸기 전 영웅(이미 플레이 중이라고 말한 영웅)이고,
   교체 후보로 언급된 영웅은 current_hero도 target_enemy도 아니다. 따라서
   target_enemy는 반드시 null로 설정하라.
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
9. 사용자가 "X를 하고 싶어", "X 쓸건데", "X 할건데", "X 고정", "X 원챔",
"X로 이기고 싶어", "X로 즐기고 싶어", "X 해도 돼?"처럼 말하면
current_hero는 X이고 intent는 "stay"로 판단해라.
이 경우 사용자는 영웅 교체 추천을 원하는 것이 아니라,
X를 유지한 상태에서 상대 조합을 이기는 운영법을 원하는 것이다.
10. ally_team은 사용자가 이번 메시지에서 "우리팀"/"아군" 영웅으로 직접 언급한
    경우에만 채워라("상대팀은 A B C D E, 우리팀은 C E F G"처럼 두 팀을 함께
    나열하는 질문에서 흔하다). 언급이 없으면 빈 배열로 둬라. enemy_team과 마찬가지로
    짐작으로 채우지 마라.
11. "A를 고르면/픽하면 B가 잘 나올까?", "A랑 B랑 둘 다 ~하잖아"처럼 자기 팀
    소속(이미 쓰고 있거나 고민 중인 아군 후보) 영웅끼리 비교/나열하는 문장은
    target_enemy/enemy_team이 아니다. "상대", "적", "카운터", "때문에"처럼
    적을 가리키는 표현이 없다면, 문장에 영웅 이름이 있어도 그 영웅을
    target_enemy로 짐작해서 채우지 마라.
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
                    # current_hero는 "이전 영웅을 이어받는 것" 자체는 정당한 경우가 많아
                    # (예: "딜 더 올리는 법은?" 같은 후속 질문) target_enemy처럼 무조건
                    # 버리지는 않는다. 다만 메시지 원문에 실제로 등장했는지는 별도로
                    # 표시해, swap처럼 "교체"를 다루는 민감한 intent와 결합됐을 때
                    # 안전장치가 작동할 수 있게 한다.
                    if hero_mentioned_as_current_hero(normalized, message):
                        current_hero_confirmed_in_message = True
        result["llm_current_hero_confirmed"] = current_hero_confirmed_in_message

        # 안전장치: intent가 "swap"(영웅 교체 여부 판단)인데 정작 메시지 원문에
        # current_hero 이름이 전혀 없다면, 이건 "내 영웅을 바꿀지" 묻는 질문이
        # 아니라 "팀 조합을 어떻게 짤지" 같은 일반적인 질문일 가능성이 높다.
        # 본인 영웅 언급이 전혀 없는 조합 질문을 LLM이 "지금 영웅을 교체할지"
        # 묻는 질문으로 오인하는 경우를 막기 위한 안전장치다.
        #
        # 이 가드가 실제로 발동했다는 사실 자체를 별도 플래그(swap_guard_triggered)로
        # 남긴다. merge_context_node에서 "현재 영웅이 불확실하다"고 판단할 근거는
        # 반드시 이 플래그여야 한다 — 단순히 llm_intent가 "general"이라는 것만으로는
        # 부족하다. 직전 답변에 대한 순수 후속 질문도 영웅 이름 없이 intent=general로
        # 분류될 수 있는데, 이런 경우까지 "영웅이 불확실하다"고 처리하면 정상적인
        # 후속 대화의 맥락(직전에 다루던 영웅)이 통째로 사라지는 문제가 생긴다.
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

        # 아군(ally_team)을 target_enemy/enemy_team 검증보다 먼저 계산해, 이번
        # 메시지에서 이미 우리 편으로 언급된 영웅이 동시에 상대(적)로도 잘못
        # 채택되는 것을 막는다. LLM이 준 ally_team뿐 아니라 "우리팀은/아군은"
        # 같은 명시적 마커로 규칙 기반으로 뽑히는 이름도 함께 배제 대상에
        # 넣는다 — LLM이 ally_team 자체를 놓쳤더라도 이 마커가 있으면 그
        # 영웅은 확실히 아군이기 때문이다.
        ally_team = parsed.get("ally_team", [])
        verified_ally_team = []
        if isinstance(ally_team, list):
            for h in ally_team:
                n = normalize_hero_name(h)
                if n in [normalize_hero_name(x) for x in HEROES] and hero_mentioned_in_text(n, message):
                    verified_ally_team.append(n)
        if verified_ally_team:
            result["llm_ally_team"] = verified_ally_team
        ally_excluded = (
            set(verified_ally_team)
            | set(extract_ally_team(message))
            | set(find_self_comparison_heroes(message))
        )

        # target_enemy/enemy_team은 LLM이 짐작으로 채울 수 있으므로,
        # 사용자 메시지 원문에 그 영웅 이름이 실제로 등장할 때만 신뢰한다.
        # (이전 대화의 적이 다음 질문에 근거 없이 단정적으로 이어붙는 문제 방지)
        # 추가로, 이번 메시지에서 이미 아군으로 언급된 영웅은 target_enemy/
        # enemy_team으로 절대 채택하지 않는다 — "솜브라랑 트레이서랑 둘 다
        # 짤짤이딜이잖아"처럼 아군끼리 비교하는 문장에서 아군 영웅(솜브라)이
        # "카운터해야 할 상대"로 둔갑해 엉뚱하게 역할을 되묻는 사고를 막는다.
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
    awaiting_role_filter_reply = bool(context.get("pending_question"))
    role_filter_reply_consumed = bool(explicit_role_filter and awaiting_role_filter_reply)
    # 세션에 남아있는 role_filter를 새 질문에 자동 재사용하지 않는다. 역할 필터는
    # 사용자가 이번 턴에 버튼/텍스트로 명시했을 때만 적용한다. 그렇지 않으면
    # 이전 역할 버튼 답변이 다음 새 질문까지 새어 들어가, 현재 역할을 모르는
    # 상황에서도 되묻지 않고 바로 답하는 문제가 생긴다.
    role_filter = explicit_role_filter

    # 답변 스타일(간단히/자세히)은 이번 턴에 명시적으로 온 값을 우선하고, 없으면 세션에
    # 남아있는 이전 선택을 이어받는다 — 역할 버튼을 누르는 후속 턴(message='')에도 원래
    # 질문에서 고른 스타일이 그대로 유지되어야 하기 때문이다.
    requested_answer_style = state.get("answer_style")
    if requested_answer_style not in ("simple", "detailed"):
        requested_answer_style = None
    answer_style = requested_answer_style or context.get("answer_style") or "detailed"

    effective_message = message
    if role_filter_reply_consumed:
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

    # 이번 턴에 사용자가 current_hero를 직접 선언했는지 여부. is_team_comp_question
    # 판단(아래)과 나중의 stale-hero 정리 가드 둘 다 이 값을 공유해서 써야
    # 일관되게 동작한다 — current_hero는 위 가드 이후로 바뀌지 않으므로 여기서
    # 한 번만 계산해도 안전하다.
    current_hero_explicit_this_turn = bool(
        current_hero and hero_mentioned_as_current_hero(current_hero, effective_message)
    )

    # "나는 파라인데 킬3 데스10 딜4000이야 ... 우리팀은 윈스턴/솜브라/루시우/
    # 브리기테야, 상대방은 ..."처럼 자기 영웅을 이미 밝혔는데도, 아군/상대
    # 조합을 함께 나열했다는 이유만으로 LLM이 intent를 "composition"(아직 영웅을
    # 못 골라 추천이 필요한 질문)으로 잘못 분류하는 경우가 있다. composition은
    # 정의상 "아직 영웅을 고르지 않은" 상태를 전제하므로, 이번 턴에 자기 영웅을
    # 이미 밝혔다면 그 전제 자체가 성립하지 않는다 — 규칙 기반 폴백으로 다시
    # 분류한다(위 is_team_comp_question 가드와 같은 이유, 다른 채널의 오분류를 막음).
    if intent == "composition" and current_hero_explicit_this_turn:
        corrected_intent = infer_intent_by_rule(effective_message, context)
        if corrected_intent != "composition":
            logger.info(
                "[COMPOSITION INTENT GUARD] 자기 영웅('%s')을 이미 밝혔는데 intent가 "
                "composition으로 잘못 분류되어 '%s'로 재분류함",
                current_hero, corrected_intent,
            )
            intent = corrected_intent

    # current_hero가 이번 메시지에 직접 등장하지 않았는데, llm_parse_context_node의
    # SWAP INTENT GUARD가 실제로 발동했다면(swap_guard_triggered=True) — 즉 LLM이
    # 원래 "교체 여부 판단" 질문으로 잘못 분류했을 만큼 새로운 상황 설명(상대 조합,
    # 압박 상황 등)이 담겨 있었는데 본인 영웅 언급이 없었다면 — "지금도 정말 그
    # 영웅을 플레이 중인지" 자체가 불확실한 상태다.
    #
    # 반드시 swap_guard_triggered를 기준으로 삼아야 한다. 단순히 llm_intent가
    # "general"이라는 것만으로 판단하면, 새로운 정보 없이 직전 답변을 더
    # 풀어달라는 순수 후속 질문까지 "영웅이 불확실하다"고 오판해서, 멀쩡히
    # 이어지던 대화 맥락이 끊기는 문제가 생긴다.
    current_hero_uncertain = bool(
        current_hero
        and not llm_current_hero_confirmed
        and swap_guard_triggered
    )

    # 힐 수급 문제를 토로하는 표현(예: "힐을 못받아", "힐이 없어", "힐 부족")은
    # 화이트리스트로 일일이 나열하면 누락되기 쉽다. "힐"이라는 단어와 부정/부족을
    # 뜻하는 표현이 근접해서 함께 등장하면 힐 수급 불만으로 간주하는 정규식으로
    # 더 견고하게 잡는다.
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
    # 추출되므로, 역할을 잘못 분류해 허용 영웅 목록이 통째로 어긋나는 문제
    # (예: 딜러 영웅을 플레이 중인데 역할이 힐러로 잘못 잡혀, 답변에 등장하는
    # 정상적인 영웅 언급까지 전부 위반으로 처리되는 문제)를 막는다.
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
    # 자동으로 비운다.
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

    # 아군 조합("우리팀은 A B C D")은 상대 조합과 달리 매 판 새로 짜이는 정보라
    # 세션에 이어붙이지 않고, 이번 턴에 실제로 언급된 경우에만 사용한다.
    llm_ally_team = state.get("llm_ally_team")
    rule_based_ally_team = extract_ally_team(effective_message)
    ally_team = llm_ally_team or rule_based_ally_team or []

    # 아군 인원을 2명 이상 직접 나열한 질문은 "지금 조합에서 어떤 영웅을 고를지"를
    # 묻는 팀 조합 분석 질문으로 본다. 표준 구성(탱1/딜2/힐2) 기준으로 이미 채워진
    # 인원의 역할을 보고 남은 자리가 정확히 한 역할일 때만 그 역할을 확정한다.
    #
    # 단, 이번 턴에 사용자가 이미 자기 영웅을 직접 선언했다면(예: "나는
    # 파라인데 ... 우리팀은 윈스턴, 솜브라, 루시우, 브리기테야") 이건 "아직
    # 영웅을 못 골라서 추천이 필요한" 조합 질문이 아니다 — 이미 골랐다.
    # 이 경우를 걸러내지 않으면, 스탯 피드백처럼 전혀 다른 질문에도 아군 인원이
    # 4명 나열됐다는 이유만으로 intent가 "composition"으로 강제 전환되어
    # 엉뚱하게 추천 카드가 나가는 문제가 생긴다.
    is_team_comp_question = len(ally_team) >= 2 and not current_hero_explicit_this_turn
    team_comp_inferred_role = (
        infer_missing_role_from_team_comp(ally_team) if is_team_comp_question else None
    )
    if is_team_comp_question:
        # 아군 조합이 실제로 나열된 것은 LLM의 intent 분류보다 신뢰도 높은 구조적
        # 신호이므로, 그 결과와 무관하게 "composition"으로 확정한다.
        intent = "composition"

    context_for_enemy = {**context, "current_hero": current_hero}
    rule_based_target_enemy = infer_target_enemy(effective_message, context_for_enemy, intent)
    target_enemy = llm_target_enemy or rule_based_target_enemy

    # 한 영웅이 이번 턴에 동시에 "내가 고르는 영웅"이자 "카운터해야 할 상대"일
    # 수는 없다 — 이는 자기모순이다. "트레이서를 고르면 우리팀 딜이 잘
    # 나올까?"처럼 사용자가 자기 후보로 언급한 영웅을, LLM이 애매한 문장
    # 구조 때문에 target_enemy로 잘못 판단하는 경우가 있었다(예: current_hero는
    # null로 판단했지만 target_enemy만 그 영웅으로 채워버림 — current_hero가
    # 비어 있어서 위 CURRENT HERO ENEMY GUARD로도 못 잡는 케이스). target_enemy가
    # current_hero와 같거나, 이번 메시지에서 자기 영웅 선언 패턴으로 직접
    # 인식되면(hero_mentioned_as_current_hero) 무조건 버린다.
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

    # 사용자가 여러 상대를 언급했더라도 그중 하나를 콕 집어 카운터 대상으로
    # 좁혔다면, 역할 되묻기 문구가 나머지까지 같이 언급하면 안 된다.
    # find_enemy_mentioned_hero는 "OO를 막/카운터/견제/잡/때문에"류 패턴에서만
    # 안정적으로 단일 영웅을 뽑아내므로("A랑 B랑"처럼 조사가 붙은 나열형은 정규화에
    # 실패해 자연히 걸러진다), 이 결과가 최종 target_enemy와 일치할 때만 "명시적으로
    # 하나로 좁혔다"고 판단한다.
    enemy_focus_hero = find_enemy_mentioned_hero(effective_message)
    target_enemy_narrowed = bool(
        enemy_focus_hero
        and target_enemy
        and normalize_hero_name(enemy_focus_hero) == normalize_hero_name(target_enemy)
    )

    # 사용자가 영웅 이름 대신 "상대 힐러부터 처리하려고"처럼 역할로만 카운터
    # 우선순위를 좁히는 경우도 있다. 이때는 그 역할이 실제로 어떤 영웅인지 몰라도
    # 상대 조합에 있는 다른 영웅들을 같이 언급하면 안 되므로 별도로 표시해둔다.
    enemy_role_focus = find_enemy_role_focus(effective_message)

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

    previous_target_enemy_for_role = normalize_hero_name(context.get("target_enemy"))
    # current_hero_explicit_this_turn은 위(ENEMY GUARD 직후)에서 이미 계산해뒀다 —
    # current_hero는 그 이후로 바뀌지 않으므로 여기서 다시 계산할 필요가 없다.
    # 아래 두 가드 중 하나라도 current_hero를 비우면, 그 사실을 세션에도 반영해야
    # 한다. 그렇지 않으면 세션에 남아있는 예전 current_hero가 사라지지 않고,
    # intent가 performance_improve/swap일 때 곧바로 다시 끌어와지거나(아래
    # 폴백 로직 참고), 이번엔 같은 상대가 그대로 유지된다는 이유로(target_enemy가
    # "새 상대"가 아니게 되어) 다음 턴에 새 상대 가드가 재작동하지 않아 그대로
    # 되살아나는 문제가 생긴다.
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

    # 팀 조합 분석 질문("상대팀은 A~E, 우리팀은 C E F G")은 상대/아군 양쪽에 걸쳐
    # 여러 영웅 이름이 한 문장에 나열된다. infer_current_hero의 intent 기반 폴백
    # ("stay/performance_improve/swap/map_strategy면 메시지에 등장한 첫 영웅을
    # current_hero로 본다")은 원래 "그 영웅 하나만 언급된" 문장을 염두에 둔 것이라,
    # 이런 나열형 문장에서는 사용자가 아직 고르지 않은 아군/적 영웅을 자기 영웅으로
    # 잘못 집어가 버린다. 자기 선언("나는 트레이서인데")이 아니라면 비운다.
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

    # effective_role_filter 결정 우선순위:
    # 1순위: 이번 메시지에서 사용자가 명시적으로 요청한 필터(explicit_role_filter) —
    #        예: "탱커로 추천해줘" 같은 직접적인 요청
    # 2순위: current_hero_role — 실제로 지금 플레이 중인 영웅의 역할. 가장 신뢰도 높음.
    # 3순위: team_comp_inferred_role — 아직 영웅을 고르지 않았지만, 이미 정해진
    #        아군 조합으로 보아 남은 자리(=사용자 역할)가 정확히 하나로 확정되는 경우.
    # 4순위: 세션에 남아있는 이전 role_filter — 아무 것도 없을 때만 최후 수단으로 사용.
    #        (이 값을 2순위보다 위에 두면, 예전에 명시했던 역할 필터 잔존값이
    #         계속 세션에 남아 정작 지금 플레이 중인 영웅의 역할과 무관하게 답변
    #         허용 목록을 고정시켜버리는 문제가 생긴다)
    if explicit_role_filter:
        effective_role_filter = explicit_role_filter
    elif current_hero_role:
        effective_role_filter = current_hero_role
    elif team_comp_inferred_role:
        effective_role_filter = team_comp_inferred_role
    else:
        effective_role_filter = role_filter

    # current_hero_cleared_this_turn(바로 위 가드들)이 이번 턴에 현재 영웅을
    # 모른다고 이미 판단했다면, 여기서 세션의 예전 값을 곧바로 다시 끌어와
    # 그 판단을 무효화하면 안 된다.
    if intent == "performance_improve" and not current_hero and not explicit_role_filter and not current_hero_cleared_this_turn:
        current_hero = context.get("current_hero")
    if intent == "swap" and not current_hero and not explicit_role_filter and not current_hero_cleared_this_turn:
        current_hero = context.get("current_hero")


    if intent in ["counter", "stay", "performance_improve"] and not target_enemy:
        previous_target_enemy = context.get("target_enemy")

        # 사용자가 지금 영웅을 계속 쓰겠다는 유지 의사를 말한 경우 직전 상대를
        # 이어받는다.
        if previous_target_enemy:
            target_enemy = previous_target_enemy

            if intent == "counter":
                enemy_named_this_turn = True
            elif intent == "stay":
                # 이번 턴에 이름을 다시 말하지 않았지만,
                # 직전 질문의 상대를 이어받아 운영법을 설명해야 하는 흐름이다.
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
        "role_filter_explicit": bool(explicit_role_filter),
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
        "answer_style": answer_style,
    }
    if awaiting_role_filter_reply:
        context_patch["pending_question"] = None
        context_patch["pending_intent"] = None

    if target_enemy:
        context_patch["target_enemy"] = target_enemy
    if current_hero:
        context_patch["current_hero"] = current_hero
    elif current_hero_cleared_this_turn:
        # 이번 턴에 명시적으로 "현재 영웅을 모른다"고 판단했다면, 세션에 남아있는
        # 예전 값도 함께 비워야 한다. 그렇지 않으면 같은 상대를 다시 물어보는
        # 다음 턴에서(더 이상 "새 상대"가 아니라는 이유로 위 가드가 재작동하지
        # 않아) 이 예전 값이 그대로 되살아난다.
        context_patch["current_hero"] = None
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

    # "상성 카드"(상대하기 어려운/쉬운 영웅 두 목록)의 분석 대상. 이 카드는 오직
    # "특정 영웅의 대표 카운터/상성 목록"을 묻는 순수 counter 질문에만 쓴다 —
    # 이미 플레이 중인 영웅이 있는 상태에서 상대를 어떻게 다룰지 묻는 질문(예:
    # "키리코인데 상대 트레이서 어떻게 해?")은 "지금 영웅으로 어떻게 운영할지"를
    # 묻는 stay 질문이지 "다른 영웅을 추천해달라"는 게 아니므로 카드 대상이 아니다.
    matchup_subject: Optional[str] = None
    matchup_subject_is_enemy = False
    if (
        intent == "counter"
        and enemy_named_this_turn
        and target_enemy
        and not current_hero
    ):
        # 아직 플레이 중인 영웅이 없는 순수 픽 추천 질문("겐지 카운터 알려줘",
        # "겐지가 상대하기 어려운 영웅 알려줘")만 상대 영웅을 대상으로 카드를 만든다.
        matchup_subject = target_enemy
        matchup_subject_is_enemy = True

    # "추천 영웅 카드"(단일 목록 + 이유)의 모드. swap(교체 고민)과
    # composition(팀 조합 분석)은 서로 다른 질문이지만 둘 다 "지금 상황에 맞는
    # 새 영웅을 추천해달라"는 목적은 같아서 같은 카드 형식을 쓴다.
    recommend_card_mode: Optional[str] = None
    if is_team_comp_question:
        recommend_card_mode = "composition"
    elif intent == "swap" and current_hero and not current_hero_uncertain:
        # 이미 쓰는 영웅이 있지만 상성이 안 좋아 교체를 고민하는 질문
        # ("윈스턴이 힘든데 누구로 바꿀까?")은 같은 역할 안에서 대안을 추천한다.
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
        "context_patch": context_patch,
        "enemy_named_this_turn": enemy_named_this_turn,
        "target_enemy_narrowed": target_enemy_narrowed,
        "enemy_role_focus": enemy_role_focus,
        "current_hero_uncertain": current_hero_uncertain,
        "answer_style": answer_style,
        "matchup_subject": matchup_subject,
        "matchup_subject_is_enemy": matchup_subject_is_enemy,
        "recommend_card_mode": recommend_card_mode,
        "ally_team": ally_team,
        "is_team_comp_question": is_team_comp_question,
        # 직전 턴의 사용자 메시지(있다면). 이번 질문이 짧고 맥락 의존적인 후속
        # 질문일 때, 답변 생성 단계가 원래 상황(왜 교체를 고민하게 됐는지)을
        # 잃지 않도록 전달한다.
        "previous_user_message": context.get("last_user_message") or context.get("last_effective_message"),
    }


def josa_eul_reul(word: Optional[str]) -> str:
    """받침 유무에 따라 '을'/'를' 조사를 고른다. 영웅 이름이 여러 개일 때도
    마지막 이름 기준으로 자연스럽게 이어붙일 수 있도록 쓴다."""
    if not word:
        return "를"
    last_char = word[-1]
    code = ord(last_char)
    if 0xAC00 <= code <= 0xD7A3 and (code - 0xAC00) % 28 != 0:
        return "을"
    return "를"


def clarify_role_filter_node(state: ChatbotGraphState) -> ChatbotGraphState:
    target_enemy = state.get("target_enemy")
    message = state.get("message", "")

    # target_enemy는 "카운터 대상 1명"만 담는 필드라, 여러 상대를 동시에 언급한
    # 질문에서는 이걸로만 답하면 사용자가 언급한 영웅 중 일부가 누락된 것처럼
    # 보인다. enemy_team(상대 조합 전체)이 있으면 그쪽을 우선 사용해 사용자가
    # 말한 영웅을 전부 언급한다.
    #
    # 다만 여러 상대를 나열해놓고도 그중 하나만 콕 집어 카운터하겠다고 명시했다면
    # (target_enemy_narrowed=True), 나머지는 언급하지 말고 그 하나만 말해야 한다.
    #
    # 영웅 이름이 아니라 역할로만 좁힌 경우(enemy_role_focus)도 있다. 이때는
    # 어떤 영웅인지 특정할 수 없으므로 target_enemy/enemy_team에 잡힌 다른
    # 상대(역할과 무관하게 원문에 등장했던 영웅들)를 언급하면 안 되고, 사용자가
    # 말한 역할 그대로("상대 힐러" 등)를 카운터 대상으로 삼아야 한다.
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
        answer = (
            f"{enemy_label}{josa_eul_reul(enemy_names[-1])} 카운터하는 영웅을 어떤 역할 기준으로 볼까요?\n\n"
            "원하는 역할을 선택하면 그 역할의 영웅만 골라서 추천해드릴게요."
        )
    else:
        # 상대 영웅을 특정하지 않은 일반적인 대처법 질문(예: "돌진 때문에 계속
        # 죽어, 어떻게 해야해?")도 current_hero를 모르면 역할부터 물어야 한다.
        answer = (
            "지금 어떤 역할(탱커/딜러/힐러)로 플레이 중이신가요?\n\n"
            "역할을 알려주시면 그 역할 기준으로 상황에 맞게 답변드릴게요."
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
    }
    if target_enemy:
        context_patch["target_enemy"] = target_enemy

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


# ============================================================
# 고정 버튼(카운터/조합 추천/맵 운영/스탯 피드백/영웅 유지) 캐시 답변
# ============================================================
# chat.html 웰컴 화면의 5개 예시 버튼(.sample-chips)은 각각 정해진 질문 하나를
# 입력창에 채운다. 이 버튼 질문, 그리고 같은 대상(영웅/맵/스탯/조합)을 가리키는
# 비슷한 표현(예: "겐지 카운터 알려줘"/"겐지 카운터"/"겐지 잡는 영웅"/"겐지
# 상대하기 좋은 영웅")은 그래프 전체(검색 + LLM 호출 여러 번)를 매번 실행하지
# 않고 미리 써둔 답을 그대로 돌려준다 — 버튼을 누른 직후 로딩 없이 바로 답을
# 보여주기 위해서다. 문장을 정확히 일치시키는 대신, 이 5개가 다루는 "고정된
# 대상"이 메시지에 들어있는지만 판단한다. 대상이 다르면(예: "리퍼 카운터
# 알려줘") 캐시를 쓰지 않고 평소대로 run_chatbot_graph를 실행한다.
#
# 카운터(겐지)만 유일하게 역할(전체/탱커/딜러/힐러)을 먼저 물어야 한다. 캐시
# 데이터는 "전체" 역할 답변만 준비돼 있으므로, 탱커/딜러/힐러를 고르면 원래
# 질문 그대로 실제 그래프를 태워 정상적으로(LLM 호출) 답한다 — try_canned_shortcut의
# resume_message가 이 경우를 위한 것이다.

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
        "겐지는 기동성을 활용해 적의 후방을 교란하는 영웅이지만, 튕겨내기로 막을 "
        "수 없는 광선 공격이나 군중 제어기에 매우 취약합니다. 따라서 적의 공격을 "
        "무력화하거나 겐지의 진입을 강제로 차단할 수 있는 영웅들이 주요 카운터로 "
        "꼽힙니다."
    ),
    "simple": (
        "겐지는 광선 공격·군중 제어기에 약함\n"
        "진입을 강제로 막을 수 있는 영웅이 주요 카운터"
    ),
}
CANNED_GENJI_HARD_HEROES = [
    {"hero": "로드호그", "note": "카운터 강도 매우 높음"},
    {"hero": "윈스턴", "note": "카운터 강도 매우 높음"},
    {"hero": "자리야", "note": "카운터 강도 매우 높음"},
    
    {"hero": "시메트라", "note": "카운터 강도 높음"},
    {"hero": "제트팩 캣", "note": "카운터 강도 높음"},
    {"hero": "캐서디", "note": "카운터 강도 중간"},
]
CANNED_GENJI_EASY_HEROES = [
    {"hero": "D.Va", "note": "디바의 모든 공격을 튕겨낼 수 있는 스킬 및 궁극기 게이지 공급원"},
    {"hero": "바스티온", "note": "디바의 모든 공격을 튕겨낼 수 있는 스킬 및 궁극기 게이지 공급원"},
    {"hero": "위도우메이커", "note": "기동성으로 접근해 처치 용이"},
    {"hero": "메이", "note": "근접전에서 압박하기 쉬움"},
    {"hero": "아나", "note": "생존기 빠지면 진입 성공 시 유리"},
    {"hero": "브리기테", "note": "거리 조절 시 압박 가능"},
]
CANNED_GENJI_SUGGESTED_QUESTIONS = [
    "겐지 카운터 영웅 추천해줘",
    "겐지 상대할 때 팁 알려줘",
    "겐지 튕겨내기 대처법은?",
]

CANNED_COMPOSITION_INTRO = {
    "detailed": (
        "상대 팀의 파라와 아나는 우리 팀 라인하르트에게 큰 위협이 됩니다.\n"
        "공중 견제와 아군 케어를 동시에 수행하며 조합의 안정성을 높여줄 지원가를 "
        "추천해 드립니다."
    ),
    "simple": (
        "파라·아나가 라인하르트에게 위협적\n"
        "공중 견제와 치유를 겸할 지원가 추천"
    ),
}
CANNED_COMPOSITION_HEROES = [
    {"hero": "바티스트", "note": "파라 견제와 광역 치유에 능함"},
    {"hero": "키리코", "note": "정화의 방울로 아나 힐밴 무효화"},
    {"hero": "일리아리", "note": "태양 포탑으로 파라 압박 가능"},
    {"hero": "젠야타", "note": "부조화로 오리사 처치 속도 향상"},
]
CANNED_COMPOSITION_SUGGESTED_QUESTIONS = [
    "추천하는 지원가 영웅은?",
    "오리사 상대하기 좋은 딜러는?",
    "파라 대응법을 더 자세히 알려줘",
]

CANNED_MAP_ANSWER = {
    "detailed": (
        "왕의 길 수비는 1포인트 진입로를 좁게 막아 상대의 초반 교전 이득을 "
        "최소화하는 것이 핵심입니다.\n\n"
        "1포인트는 정문과 측면 골목으로 나뉘어 있어, 탱커가 정문 시야를 막고 "
        "딜러가 측면 골목을 견제하는 형태로 자리를 잡아야 합니다. 1포인트가 "
        "뚫리면 2포인트의 좁은 길목에서 다시 자리를 잡을 수 있으니, 무리하게 "
        "1포인트를 사수하려다 전멸하지 않는 것이 중요합니다. 궁극기는 상대가 "
        "좁은 골목에 뭉치는 타이밍(포인트 진입 직전)에 맞춰 광역 궁극기로 "
        "끊어내는 것이 효율적입니다.\n\n"
        "바로 적용할 것 3가지:\n"
        "1. 1포인트 정문과 측면 골목을 동시에 볼 수 있는 위치를 선점한다.\n"
        "2. 무리한 사수보다 2포인트 좁은 길목에서 다시 자리를 잡는 것을 "
        "우선한다.\n"
        "3. 상대가 좁은 골목에 뭉치는 타이밍에 광역 궁극기를 맞춘다."
    ),
    "simple": (
        "1포인트 정문·측면 골목 동시에 보는 위치 선점\n"
        "무리한 사수보다 2포인트 좁은 길목에서 재정비\n"
        "상대가 좁은 골목에 뭉칠 때 광역 궁 사용\n\n"
        "바로 할 것 3가지\n"
        "1. 정문·측면 시야 확보\n"
        "2. 1포인트 무리하게 사수 안 함\n"
        "3. 뭉치는 타이밍에 광역 궁 사용"
    ),
}
CANNED_MAP_SUGGESTED_QUESTIONS = [
    "왕의 길 공격 조합 추천해줘",
    "왕의 길 2포인트 운영법은?",
    "수비 궁극기 타이밍 알려줘",
]

CANNED_STAT_ANSWER = {
    "detailed": (
        "킬 4에 데스 8은 딜량 6000에 비해 데스가 많아 딜러치고 생존력이 아쉬운 "
        "수치입니다.\n\n"
        "솔저76은 안정적인 딜을 넣을 수 있는 영웅이지만, 극딜 타이밍에 너무 "
        "전진해서 죽는 경우가 많으면 데스가 쌓이기 쉽습니다. 아군 탱커 뒤에서 "
        "사격하다가 원거리 딜을 넣고, 궁극기 택티컬 바이저(Q)는 반드시 아군과 "
        "함께 있을 때만 사용해 혼자 이니시에이팅하는 상황을 피하는 것이 "
        "중요합니다. 스프린트(Shift)를 아껴뒀다가 체력이 50% 이하로 떨어지고 "
        "상대 궁이 예상되는 위험 신호가 오면 즉시 후퇴하는 습관을 들이면 데스를 "
        "줄일 수 있습니다.\n\n"
        "바로 적용할 것 3가지:\n"
        "1. 탱커 뒤에서 사격하고 무리한 전진을 자제한다.\n"
        "2. 택티컬 바이저(Q)는 아군과 함께 있을 때만 사용한다.\n"
        "3. 체력 50% 이하에서는 스프린트(Shift)로 즉시 후퇴한다."
    ),
    "simple": (
        "킬4/데스8은 딜량 6000 대비 데스 많음\n"
        "탱커 뒤에서 사격, 무리한 전진 자제\n"
        "택티컬 바이저(Q)는 아군과 함께 있을 때만 사용\n"
        "체력 50% 이하는 스프린트(Shift)로 즉시 후퇴\n\n"
        "바로 할 것 3가지\n"
        "1. 무리한 전진 자제\n"
        "2. 바이저는 아군과 함께\n"
        "3. 체력 낮으면 즉시 후퇴"
    ),
}
CANNED_STAT_SUGGESTED_QUESTIONS = [
    "딜량 더 올리는 방법은?",
    "데스 줄이는 법 알려줘",
    "이 스탯이면 영웅 바꿔야 해?",
]

CANNED_STAY_ANSWER = {
    "detailed": (
        "리퍼는 유지해도 됩니다. 아나의 수면총과 생체 소총 견제 범위만 피해서 "
        "접근하면 리퍼가 유리한 근접전으로 끌고 갈 수 있습니다.\n\n"
        "아나는 정면에서 수면총(Shift)으로 리퍼의 진입을 끊을 수 있으므로, "
        "정면으로 바로 들어가기보다 벽이나 구조물을 낀 측면 경로로 우회해 "
        "접근하는 것이 안전합니다. 유령 형태(Shift)로 아나의 견제 사거리를 "
        "좁히며 접근한 뒤, 사거리 안에 들어오면 지옥의 산탄(좌클릭)으로 순식간에 "
        "처치할 수 있습니다. 궁극기 죽음의 꽃(Q)은 아나가 수면총을 사용한 직후"
        "(쿨타임 중)이거나 생체 소총 재장전 중인 타이밍에 맞춰 사용하면 무력화 "
        "없이 확정 처치를 노릴 수 있습니다.\n\n"
        "바로 적용할 것 3가지:\n"
        "1. 정면 대신 벽·구조물을 낀 측면 경로로 접근한다.\n"
        "2. 유령 형태(Shift)로 사거리를 좁히며 접근한다.\n"
        "3. 아나의 수면총 쿨타임 타이밍에 죽음의 꽃(Q)을 사용한다."
    ),
    "simple": (
        "아나 수면총(Shift)·생체 소총 사거리 피해 접근\n"
        "정면 대신 벽 낀 측면 경로로 우회\n"
        "유령 형태(Shift)로 거리 좁히기\n"
        "수면총 쿨타임 타이밍에 죽음의 꽃(Q) 사용\n\n"
        "바로 할 것 3가지\n"
        "1. 측면 경로로 접근\n"
        "2. 유령 형태로 거리 좁히기\n"
        "3. 수면총 쿨타임에 궁극기 사용"
    ),
}
CANNED_STAY_SUGGESTED_QUESTIONS = [
    "리퍼 그림자 밟기 활용법은?",
    "아나 나노 강화제 대처법은?",
    "리퍼 다른 상대법도 알려줘",
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

    # 영웅 유지(리퍼 유지 + 상대 아나) — 자기 영웅 선언 표현으로 리퍼를 밝히고,
    # 실제로 "상대 아나"로 언급한 경우만 인정한다.
    if (
        hero_mentioned_as_current_hero(CANNED_STAY_HERO, text)
        and CANNED_STAY_ENEMY in find_all_heroes(text)
        and find_enemy_mentioned_hero(text) == CANNED_STAY_ENEMY
    ):
        return "stay_reaper_ana"

    # 스탯 피드백(솔저76, 킬4/데스8/딜6000) — 수치까지 정확히 일치할 때만 캐시를
    # 쓴다. 수치가 다르면 실제 스탯 분석이 필요하므로 그래프로 흘려보낸다.
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

    # 카운터(겐지) — "겐지 하는데" 같은 자기 영웅 선언이 아니라, 겐지를 카운터
    # 대상으로 묻는 경우만 인정한다.
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
    답을 즉시 돌려준다. chat_api가 run_chatbot_graph를 호출하기 전에 먼저
    호출한다.

    반환값:
    - result: 매칭됐다면 그대로 응답에 쓸 결과(run_chatbot_graph 반환값과 동일한
      모양). 매칭 안 됐으면 None.
    - resume_message: 캐시된 역할 되묻기(겐지 카운터)에 답했지만 캐시 데이터가
      없는 역할(탱커/딜러/힐러)을 골라 실제 그래프를 태워야 하는 경우, 그래프에
      넘길 원래 질문 원문. 그 외에는 None.
    - context_updates: 세션에 즉시 반영해야 하는 정리용 값(캐시 pending 정리).
    """
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


OFF_TOPIC_ANSWER = (
    "저는 오버워치2 게임 코칭만 도와드릴 수 있어요. "
    "상대 영웅 대처법, 팀 조합, 맵 운영, 개인 플레이 개선처럼 "
    "오버워치2 게임 상황과 관련된 질문을 해주세요."
)


def off_topic_response_node(state: ChatbotGraphState) -> ChatbotGraphState:
    """
    오버워치2와 무관한 메시지(인사, 잡담, 전혀 다른 주제 등)에는 LLM이 매번
    다른 문구를 즉석에서 지어내지 않고, 항상 같은 고정 문구로만 응답한다.
    이 노드는 LLM을 호출하지 않는다 — intent 분류(llm_parse_context_node)만
    LLM이 하고, 실제로 사용자에게 보여줄 답변은 고정값이라 관련 없는 주제에
    대해 그럴듯하게 대답해버리는 것을 원천 차단한다.
    """
    context_patch = {
        **state.get("context_patch", {}),
    }

    return {
        "answer": OFF_TOPIC_ANSWER,
        "recommendation_type": "off_topic",
        "recommended_heroes": [],
        "choice_buttons": [],
        "suggested_questions": [],
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
    # 답변에 엉뚱한 영웅이 단정적으로 등장하는 문제를 막기 위함)
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
        # 인게임 압박 상황("파라가 계속 압박해" 등)은 현재 영웅으로 어떻게 버티고
        # 대응할지가 핵심이라, 상대(있다면)와 현재 영웅을 함께 엮은 쿼리를 만든다.
        if enemy_named_this_turn and (target_enemy or high_threat_enemy):
            queries.append(f"{target_enemy or high_threat_enemy} 압박 대처법 운영 {current_hero or ''}")
        else:
            queries.append(f"{current_hero or ''} 위기 상황 대처법 생존 운영")

    unique_queries = [q.strip() for q in dict.fromkeys(queries) if q.strip()]
    logger.info("[RAG 검색 쿼리] %s", unique_queries)
    return {"retrieval_queries": unique_queries}


def retrieve_docs_node(state: ChatbotGraphState) -> ChatbotGraphState:
    if state.get("error"):
        return state

    t0 = time.time()
    try:
        _chatbot, retriever, _llm = get_chatbot_components()

        queries = state.get("retrieval_queries", []) or []

        # 검색어마다 로컬 임베딩 모델(BAAI/bge-m3, CPU) 계산이 새로 필요해서,
        # 순차 실행하면 검색어 개수만큼 지연이 그대로 쌓인다(실측 기준 검색어당
        # 약 0.3~1.6초). 검색어끼리는 서로 결과에 의존하지 않으므로 스레드로
        # 동시에 실행해 벽시계 시간을 줄인다 — torch/HF 임베딩 연산은 GIL을
        # 풀어주는 네이티브 연산이 대부분이라 스레드 병렬화 효과가 있다.
        # 결과 순서는 기존과 동일하게 queries 순서를 그대로 유지해, 뒤이은
        # dedup(같은 문서가 여러 검색어에서 나올 때 먼저 나온 검색어 기준으로
        # 채택)과 all_docs[:12] 절단 동작이 병렬화 전과 완전히 동일하게 유지된다.
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
        _chatbot, _retriever, llm = get_chatbot_components()

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
        # current_hero가 이전 턴에서 이어받은 값일 뿐, 이번 메시지에서 그 영웅을
        # 계속 플레이 중이라고 확인된 적이 없는 경우. 이런 상태에서 역할을 강제로
        # 제한하면, 실제로는 새로운 상황(다른 영웅이거나 일반적인 조합 질문)인데도
        # 옛 영웅 기준으로 답이 좁혀지는 사고가 난다.
        current_hero_uncertain = state.get("current_hero_uncertain", False)

        # 버튼/텍스트로 이번 턴에 명시된 역할 필터가 있으면 그것이 최우선이다.
        # current_hero_role은 이전 대화에서 이어진 값일 수 있어, 명시 선택 역할을
        # 덮어쓰면 "힐러" 버튼을 눌렀는데 딜러 추천이 나오는 사고가 난다.
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
        elif role_filter == "all":
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


def generate_answer_node(state: ChatbotGraphState) -> ChatbotGraphState:
    if state.get("error"):
        return state

    try:
        _chatbot, _retriever, llm = get_chatbot_components()

        skill_shortcut_text = get_skill_shortcut_text()
        role_filter = state.get("role_filter") or "all"
        role_filter_explicit = state.get("role_filter_explicit", False)
        current_hero = state.get("current_hero")
        current_hero_role = state.get("current_hero_role")
        has_stats = state.get("has_stats", False)
        # 간단히/자세히 토글. "자세히"는 기존 서술형 답변을 그대로 유지하고,
        # "간단히"는 인게임 중 한눈에 훑어볼 수 있도록 영웅별 짧은 불릿 + 실행 목록만 남긴다.
        answer_style = state.get("answer_style") or "detailed"
        is_simple_style = answer_style == "simple"
        # "간단히"는 judge_strategy(route_after_retrieve 참고)뿐 아니라
        # generate_suggested_questions의 별도 LLM 호출도 생략한다 — 이번
        # 호출 하나에서 answer와 suggested_questions를 함께 받는다.
        suggested_questions_schema_line = SUGGESTED_QUESTIONS_INLINE_SCHEMA_LINE if is_simple_style else ""
        suggested_questions_rules = SUGGESTED_QUESTIONS_INLINE_RULES if is_simple_style else ""
        # 이번 턴에 적이 실제로 언급되지 않았다면 답변에서도 "확정된 상대"로 다루지 않는다.
        enemy_named_this_turn = state.get("enemy_named_this_turn", False)
        # current_hero가 이전 턴에서 이어받은 값일 뿐, 이번 메시지에서 다시 확인되지
        # 않은 상태. 이럴 때 역할을 강제로 제한하면 실제로는 영웅과 무관한 일반
        # 질문(예: 팀 조합 질문)인데도 옛 영웅 기준으로 답이 좁혀지는 사고가 난다.
        current_hero_uncertain = state.get("current_hero_uncertain", False)

        # 버튼/텍스트로 이번 턴에 명시된 역할 필터가 있으면 그것이 최우선이다.
        # current_hero_role은 이전 대화에서 이어진 값일 수 있어, 명시 선택 역할을
        # 덮어쓰면 "힐러" 버튼을 눌렀는데 딜러 추천이 나오는 사고가 난다.
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
        elif role_filter == "all":
            allowed_heroes_text = (
                "사용자가 '전체' 역할을 선택했다. 특정 역할로 제한하지 말고, "
                "탱커/딜러/힐러 각 역할에서 이 상황에 대응할 수 있는 영웅을 "
                "최소 1명씩 골고루 골라 역할별로 균형 있게 제안해라. "
                "한 역할에만 치우친 추천은 하지 마라."
            )
            answer_allowed_hero_set = None
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
        # 아군 조합("우리팀은 A B C D")은 팀 조합 추천 카드(recommend_card)로만
        # 가는 게 아니라, 이미 자기 영웅을 확정한 채로 운영법을 묻는 질문
        # (예: "나는 파라인데 ... 우리팀은 윈스턴/솜브라/루시우/브리기테야")
        # 에도 함께 나올 수 있다. 이 경우 아군 조합 정보를 답변 프롬프트에서
        # 완전히 빼버리면 시너지를 고려한 조언을 할 수 없으므로 표시해준다.
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
                "\"- \"로 시작하는 짧은 이유 1~2개를 적어라. 없으면(운영 개선/유지 등) 이 블록 "
                "없이 서론 없이 바로 4번만 적어라.\n"
                "3. 스킬은 단축키를 괄호로 붙여라(예: 투창(우클릭)).\n"
                "4. 마지막에 \"바로 할 것 3가지\" 아래 1~3개 항목을 \"1. \", \"2. \" 숫자 "
                "목록으로 적어라."
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
                "3. 힐 부족·팀 문제처럼 현재 역할로 해결하기 어려운 상황이라면,\n"
                "   역할 변경 대신 \"현재 영웅으로 생존력을 높이는 법\" 또는 \"힐팩 활용\" 등 "
                "대안을 제시해라.\n"
                "4. 스킬명에 단축키를 같이 써라. 예: 다이너마이트(shift), 코치건(e).\n"
                "5. 마지막에 \"바로 적용할 것 3가지\"를 적어라."
            )
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

        # "그 영웅을 유지해도 된다"류 안내는 intent가 실제로 "stay"(유지 의사를
        # 밝힌 질문)일 때만 넣는다. 예전에는 이 블록이 항상 프롬프트에 고정으로
        # 들어가 있었는데, 그 결과 "겐지인데 상대 아나 어떻게 킬할 수 있을까"처럼
        # 그냥 지금 플레이 중인 영웅을 언급했을 뿐인 counter성 질문에도 LLM이
        # "겐지를 유지해도 좋습니다" 같은 불필요한 서두를 붙이는 문제가 있었다.
        stay_intent_block = ""
        if state.get("intent") == "stay":
            stay_intent_block = f"""
6. 사용자가 특정 영웅을 하고 싶다, 쓸 것이다, 고정으로 한다, 원챔이다, 해도 되냐고 말한 경우
   다른 영웅 추천을 먼저 하지 마라.
   {stay_preference_instruction}"""

        # performance_improve(스탯 피드백/운영 개선)는 지금 영웅을 계속 플레이하며
        # 실력을 늘리고 싶다는 질문이지, 다른 영웅으로 바꾸고 싶다는 게 아니다.
        # 이 구분이 없으면 아군/상대 조합 정보가 함께 있을 때 모델이 스스로
        # "추천 영웅" 블록(교체 후보)을 만들어버려, 정작 사용자가 묻지 않은
        # 영웅 교체 얘기로 새는 문제가 있었다.
        performance_improve_instruction = ""
        if state.get("intent") == "performance_improve":
            performance_improve_instruction = """
9. 사용자는 지금 영웅을 계속 플레이하면서 스탯/실력/운영을 개선하고 싶어하는
   것이지, 다른 영웅으로 바꾸고 싶어하는 게 아니다. "추천 영웅" 블록을 만들거나
   다른 영웅으로 바꾸라고 제안하지 말고, 지금 영웅으로 무엇을 다르게 하면
   좋을지만 답해라."""

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
{style_rules_1to5}{enemy_naming_instruction}{swap_decision_instruction}{stay_intent_block}{performance_improve_instruction}{suggested_questions_rules}

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
                # 응답이 max_output_tokens에 걸려 JSON이 닫히기 전에 잘린 경우,
                # 위 정규식은 종료 큰따옴표가 없어 매칭되지 않는다. 이때 "answer"
                # 필드 시작부터 끝까지(닫는 따옴표 없이)라도 추출해 JSON 껍데기
                # ({, "answer": " 등)가 그대로 사용자에게 노출되는 것을 막는다.
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

        answer = sanitize_answer_for_user(raw_answer, keep_dash_bullets=is_simple_style)

        if answer_allowed_hero_set is not None:
            # 사용자가 메시지 원문에서 같은 편 동료를 가리키며 이미 언급한 영웅
            # 이름은 위반 검사에서 제외한다. 이런 이름은 LLM이 새로 "추천"한 게
            # 아니라 사용자의 말을 그대로 인용/응답한 것일 뿐이므로, 다른 역할
            # 이라는 이유로 치환해버리면 사용자가 언급한 동료 영웅이 엉뚱한
            # 영웅으로 바뀌는 결과가 나온다.


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

            forbidden_in_answer = [
                h for h in find_all_heroes(answer)
                if (
                    h not in answer_allowed_hero_set
                    and h not in user_mentioned_heroes
                    and h not in enemy_context_heroes
                )
            ]

            if forbidden_in_answer:
                logger.warning(
                    "[ROLE VIOLATION] 답변에 허용 범위 밖 영웅 등장: %s (current_hero=%s role=%s, "
                    "user_mentioned=%s) — 단어만 치환",
                    forbidden_in_answer, current_hero, current_hero_role, user_mentioned_heroes,
                )
                role_label_kor = ROLE_LABELS.get(
                    role_filter if role_filter in ROLE_LABELS else current_hero_role,
                    "현재 역할",
                )

                # 줄 전체를 삭제하면 "교체할지 유지할지"같은 핵심 판단 문장까지
                # 같은 줄의 다른 위반 단어 때문에 통째로 날아갈 수 있다.
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
        _chatbot, _retriever, llm = get_chatbot_components()

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

        # hard_heroes(=상대하기 어려운 영웅)는 사용자가 실제로 픽할 수 있는 후보이므로
        # 역할 고정 규칙을 그대로 적용한다. easy_heroes(=상대하기 쉬운 영웅)는 상대팀이
        # 어떤 영웅을 쓰든 상관없는 정보 제공용이라 역할 제한을 두지 않는다.
        if role_filter in ROLE_HEROES:
            hard_role_constraint = (
                f"hard_heroes는 반드시 {ROLE_LABELS.get(role_filter)} 역할만: "
                f"{', '.join(ROLE_HEROES[role_filter])}\n"
                "이 목록 밖의 영웅은 어떤 이유로도 넣지 마라."
            )
            # ROLE_HEROES 원본 표기("솔저: 76")와 HEROES/normalize_hero_name 결과
            # ("솔저76")가 다른 경우가 있어, 정규화한 이름으로 허용 집합을 만든다.
            hard_allowed_set: Optional[set] = {normalize_hero_name(h) for h in ROLE_HEROES[role_filter]}
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
        _chatbot, _retriever, llm = get_chatbot_components()

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

        if role_filter in ROLE_HEROES:
            role_constraint = (
                f"추천 영웅은 반드시 {ROLE_LABELS.get(role_filter)} 역할만: "
                f"{', '.join(ROLE_HEROES[role_filter])}\n"
                "이 목록 밖의 영웅은 어떤 이유로도 넣지 마라."
            )
            allowed_set: Optional[set] = {normalize_hero_name(h) for h in ROLE_HEROES[role_filter]}
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
            context_block = f"""
아군 조합(이미 정해진 인원): {ally_display}
상대 조합: {', '.join(display_enemy_team) if display_enemy_team else '없음'}
사용자는 아직 영웅을 고르지 않았고, 위 인원 외에 남은 한 자리를 채울 영웅을
고르는 상황이다. 남은 역할은 이미 위 역할 제한에 반영돼 있다."""
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
- 반드시 사용자 1인칭 시점의 짧은 질문/요청문으로 작성.
- AI가 사용자에게 묻는 형태 절대 금지.
- AI가 추가 설명하는 형태 절대 금지.
- 이번 답변 내용과 자연스럽게 이어지는 흐름으로 작성.
- 버튼 라벨이므로 15자 이내의 짧은 문장.
- 문서, 출처, 내부 시스템 용어 금지.
- 카운터 대상이 있으면, 추천 질문 3개 중 최소 1개는 반드시 그 카운터 대상과 관련된 질문으로 작성해라.
- 사용자가 언급하지 않은 상대 영웅 이름을 새로 만들지 마라.
- 예를 들어 카운터 대상이 둠피스트라면 겐지, 트레이서, 윈스턴 같은 다른 영웅을 임의로 넣지 마라.
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


def format_response_node(state: ChatbotGraphState) -> ChatbotGraphState:
    if state.get("error"):
        return {"result": {"error": state["error"]}}

    answer_style = state.get("answer_style") or "detailed"
    answer = sanitize_answer_for_user(state.get("answer", ""), keep_dash_bullets=answer_style == "simple")

    return {
        "result": {
            "answer": answer,
            # 역할 버튼 클릭처럼 이번 턴의 원문 메시지가 빈 문자열일 때도,
            # merge_context_node가 pending_question에서 복원한 실제 질문을
            # 로그 저장용으로 알 수 있도록 함께 내려준다.
            "message": state.get("message"),
            "intent": state.get("intent"),
            "recommendation_type": state.get("recommendation_type"),
            "recommended_heroes": state.get("recommended_heroes", []),
            "suggested_questions": state.get("suggested_questions", []),
            "choice_buttons": state.get("choice_buttons", []),
            "context_patch": state.get("context_patch", {}),
            "has_stats": state.get("has_stats", False),
            "answer_style": answer_style,
            "matchup_card": state.get("matchup_card"),
            "recommend_card": state.get("recommend_card"),
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
    if state.get("intent") == "off_topic":
        return "off_topic_response"
    if should_ask_role_filter(state):
        return "clarify_role_filter"
    return "build_retrieval_queries"

def route_after_retrieve(state: ChatbotGraphState) -> str:
    if state.get("error"):
        return "format_response"
    # 카드 질문(상성 카드/추천 영웅 카드)은 judge_strategy가 만드는
    # recommendation_type/recommended_heroes를 쓰지 않으므로(카드 노드가 직접
    # 목록을 생성한다), 불필요한 LLM 호출을 건너뛴다.
    if state.get("matchup_subject"):
        return "generate_matchup_answer"
    if state.get("recommend_card_mode"):
        return "generate_recommend_card"
    # "간단히" 스타일은 인게임 중 빠른 응답이 목적이라 LLM 호출 수 자체를
    # 줄여야 한다. judge_strategy는 recommendation_type/recommended_heroes/
    # strategy_reason을 만들어 generate_answer의 프롬프트에 "참고 정보"로
    # 실어주는 별도 LLM 호출인데, generate_answer는 이 정보 없이도 role_filter/
    # current_hero/target_enemy/stats 등 이미 가진 컨텍스트만으로 같은 판단을
    # 스스로 내릴 수 있다. "자세히" 스타일은 답변 품질(전략 판단 근거를 먼저
    # 잡아두는 것)을 우선해 기존처럼 judge_strategy를 그대로 거친다.
    if state.get("answer_style") == "simple":
        return "generate_answer"
    return "judge_strategy"

def route_after_judge(state: ChatbotGraphState) -> str:
    return "format_response" if state.get("error") else "generate_answer"

def route_after_generate(state: ChatbotGraphState) -> str:
    if state.get("error"):
        return "format_response"
    # "간단히" 스타일은 답변 생성 노드(generate_answer/generate_matchup_answer/
    # generate_recommend_card) 안에서 이미 suggested_questions를 함께 받아왔다
    # (SUGGESTED_QUESTIONS_INLINE_* 참고). 유효한 질문 3개를 확보했다면 별도
    # LLM 호출(generate_suggested_questions_node)을 또 하지 않고 바로 끝낸다.
    # 인라인 요청이 실패했거나(개수 부족) "자세히" 스타일이면 기존처럼 진행한다.
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
            "off_topic_response": "off_topic_response",
            "build_retrieval_queries": "build_retrieval_queries",
        })
    graph.add_edge("clarify_role_filter", "format_response")
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
) -> Dict[str, Any]:
    logger.info(
        "[GRAPH START] message=%s role_filter=%s answer_style=%s context=%s",
        message, role_filter, answer_style, conversation_context,
    )
    t0 = time.time()

    graph = get_chatbot_graph()
    final_state = graph.invoke({
        "message": message,
        "conversation_context": conversation_context or {},
        "role_filter": role_filter,
        "answer_style": answer_style,
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

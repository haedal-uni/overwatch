"""LLM 없이 메시지 문장만 보고 판단하는 규칙 기반 추출/분류 계층.

의도(intent), 자기 영웅 선언, 아군/상대 조합 나열, 비교 질문 여부, 역할
되묻기 필요 여부 등을 정규식과 단어 목록으로 판단한다.

문장 구조로 확실히 알 수 있는 신호는 LLM 분류보다 우선하므로, 여기 함수
대부분은 merge_context_node에서 LLM 결과를 덮어쓰는 안전장치로 쓰인다.
LLM/DB/Django에 의존하지 않는 순수 함수다.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from chat.graph.state import ChatbotGraphState
from chat.domain.heroes import (
    HERO_ALIASES,
    HERO_TO_ROLE,
    ROLE_HEROES,
    ROLE_LABELS,
    HEROES,
    MAPS,
    find_all_heroes,
    find_first_hero,
    find_map,
    find_side,
    make_role_filter,
    normalize_hero_name,
)

logger = logging.getLogger(__name__)


def _hero_name_variants(hero: Optional[str]) -> set:
    """영웅의 표준 이름과 사용자가 쓸 법한 별칭 표기를 모두 모은다."""
    normalized = normalize_hero_name(hero)
    if not normalized:
        return set()
    names = {normalized}
    for h in HEROES:
        if normalize_hero_name(h) == normalized:
            names.add(h)
    for alias, canonical in HERO_ALIASES.items():
        if canonical == normalized:
            names.add(alias)
    return names


# 조합 나열에서 이름 사이에 들어가는 구분자(공백만인 경우 포함).
_COMP_LIST_SEPARATOR = r"\s*(?:,|/|랑|이랑|와|과|하고|그리고|\+)?\s*"


def hero_listed_in_ally_comp(hero: Optional[str], text: str) -> bool:
    """그 영웅 이름이 조합 나열의 일부로 등장했는지.

    나열 맨 뒤 이름에 "인데"가 붙으면 hero_mentioned_as_current_hero가 자기
    선언으로 오인하므로, 그 앞에서 걸러내는 용도다. 판정은 인접성으로만 한다 —
    바로 앞에 구분자만 두고 다른 영웅 이름이 붙어 있으면 나열로 본다.
    """
    normalized = normalize_hero_name(hero)
    if not normalized or not text:
        return False
    mentioned = find_all_heroes(text)
    if len(mentioned) < 2 or normalized not in mentioned:
        return False

    names = _hero_name_variants(normalized)
    # 1인칭 표지가 바로 앞에 붙어 있으면 나열이 아니라 자기 선언이다.
    for name in names:
        if re.search(rf"(?<![가-힣])(?:난|나는|나|저는|제가|내가)\s*{re.escape(name)}", text):
            return False

    other_names = [
        n for other in mentioned if other != normalized
        for n in _hero_name_variants(other)
    ]

    # 같은 영웅이 나열에도 따로도 나올 수 있으므로 등장 위치마다 본다.
    # 나열이 아닌 등장이 하나라도 있으면 자기 선언 판정을 막지 않는다.
    found_any = False
    for name in names:
        for match in re.finditer(re.escape(name), text):
            found_any = True
            prefix = re.sub(rf"{_COMP_LIST_SEPARATOR}$", "", text[: match.start()])
            if not any(prefix.endswith(other) for other in other_names):
                return False
    return found_any


def hero_mentioned_as_current_hero(hero: Optional[str], text: str) -> bool:
    """영웅 이름이 "상대 겐지"처럼 적으로 언급된 경우와 "겐지로 할게"처럼
    사용자가 직접 플레이한다고 말한 경우를 구분한다."""
    normalized = normalize_hero_name(hero)
    if not normalized or not text:
        return False

    names = _hero_name_variants(normalized)

    for name in names:
        escaped = re.escape(name)
        # 앞에 한글 음절이 없을 때만 — 영웅 이름 속 음절이 1인칭 표지로 잡히면 안 된다.
        if re.search(rf"(?<![가-힣])(?:난|나는|나|저는|제가|내가)\s*{escaped}", text):
            return True
        if re.search(rf"{escaped}\s*(?:로|으로)\s*(?:플레이|하고|하는|할|가|갈|쓰|쓸|이기|즐기)", text):
            return True
        # "윈스턴으로 수비하는데"처럼 로/으로와 활용형 사이에 역할 명사가 낄 때도 인정한다.
        if re.search(
            rf"{escaped}\s*(?:로|으로)\s*[가-힣]{{0,4}}\s*"
            rf"(?:하고|하는|할|해서|하면서|하는데|하다가)",
            text,
        ):
            return True
        # "파라를 하고 싶은데"처럼 목적격 조사(을/를)가 낀 경우도 인정한다.
        if re.search(
            rf"{escaped}\s*(?:을|를)?\s*(?:하고\s*있|하는\s*중|하고\s*싶|하고싶|할\s*거|할건데|"
            rf"쓰고\s*싶|쓰고싶|쓸건데|계속|유지|고정|원챔)",
            text,
        ):
            return True
        # "시그마인데"처럼 서술격 조사만 붙는 표현도 인정한다 (로/으로 패턴으로는 못 잡음).
        if re.search(rf"{escaped}\s*(?:인데요|인데|이야|이거든|임|입니다|이에요|예요)", text):
            return True
        # "겐지 하는데"처럼 조사 없이 "하다" 활용형만 붙는 표현도 인정한다.
        if re.search(rf"{escaped}\s*(?:하는데요|하는데|할\s*때|하다가)", text):
            return True
        # "겐지로 윈스턴 상대법 알려줘"처럼 로/으로 뒤에 다른 영웅명이 끼면 위 패턴들이
        # 놓치므로, 상대법류 표현과 "로/으로"가 함께 있으면 자기 선언으로 본다.
        if (
            any(word in text for word in _STAY_OPERATION_WORDS)
            and re.search(rf"{escaped}\s*(?:로|으로)", text)
        ):
            return True
        # "트레이서를 고르면"처럼 확정 전 고려 표현도 인정한다(없으면 후보 영웅이
        # 인식되지 않아 불필요하게 역할을 되묻는다).
        if re.search(
            rf"{escaped}\s*(?:을|를)?\s*(?:고르면|고를까|고르는\s*게|고르는게|"
            rf"골라도|픽하면|픽할까|선택하면|선택할까)",
            text,
        ):
            return True

    return False


# 양 팀을 한 문장에 나열하면 한쪽 캡처가 반대 팀 마커까지 삼킬 수 있어,
# 캡처 조각을 반대 팀 마커 앞에서 자른다.
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


# 조합 평가 질문과 추천 요청 질문을 가르는 표현. 후자만 추천 영웅 카드로 보낸다.
_COMPOSITION_RECOMMEND_REQUEST_WORDS = (
    "추천", "뭐 하면", "뭘 하면", "뭘 해야", "뭐 해야", "뭐가 좋을까", "뭐로 하면",
    "골라야", "고를까", "고르면", "선택하는", "선택하면", "선택해야",
    "누구를 고르", "누구 고르", "누구를 선택", "누구 선택", "누구를 뽑", "누구 뽑",
)

# 표기를 하나씩 추가하면 계속 누락되므로 "의문사 + (조사) + 고르는 동사"
# 구조를 정규식으로 잡는다.
_COMPOSITION_RECOMMEND_REQUEST_PATTERN = re.compile(
    r"(뭐|뭘|무엇|누구|누굴|어떤\s*영웅|무슨\s*영웅|어떤\s*거|어느\s*영웅)"
    r"\s*(을|를|로|으로|가)?\s*"
    r"(하면|할까|해야|하지|하는\s*게|골라|고르|고를|선택|뽑|픽|가면|갈까|잡으면"
    # 교체 표현("뭘로 바꿔야 할지")도 추천 요청이다.
    r"|바꾸|바꿔|바꿀|교체|갈아)"
)


def wants_composition_recommendation(message: str) -> bool:
    if any(w in message for w in _COMPOSITION_RECOMMEND_REQUEST_WORDS):
        return True
    return bool(_COMPOSITION_RECOMMEND_REQUEST_PATTERN.search(message))


# 조합을 다시 나열하지 않고 추천만 재요청하는 질문. 이 턴에는
# is_team_comp_question이 False라 별도로 잡아 세션의 아군 조합을 쓴다.
def is_composition_reask(message: str) -> bool:
    if "조합" not in message:
        return False
    return wants_composition_recommendation(message)


# 아군의 실제 활약을 비교해달라는 질문. is_team_comp_question보다 우선해야
# 조합 평가가 아니라 스탯 비교로 답이 간다.
_PERFORMANCE_COMPARISON_PATTERN = re.compile(
    r"누(가|구)\s*(더|제일|가장)?\s*(잘\s*(했|하|한)|못\s*(했|하|한)|나은|나아|잘함|못함)"
)


def is_performance_comparison_question(message: str) -> bool:
    return bool(_PERFORMANCE_COMPARISON_PATTERN.search(message))


def find_performance_comparison_heroes(text: str) -> List[str]:
    """비교 질문에 등장한 영웅들을 ally_team 후보로 뽑는다.

    llm_ally_team이 비어도 compared_heroes가 채워지도록 하는 규칙 기반 폴백.
    적대 신호가 있으면 상대와의 비교일 수 있어 적용하지 않는다."""
    if any(word in text for word in ADVERSARIAL_SIGNAL_WORDS):
        return []
    if not is_performance_comparison_question(text):
        return []
    return find_all_heroes(text)


# 아군 조합으로 사용자가 맡을 수 있는 역할을 좁히는 계층.
# 5vs5: 탱1/딜2/힐2 고정, 6vs6: 탱1~2/딜2~3/힐2(남는 한 자리가 판마다 다름).
ROSTER_ROLE_RANGES = {
    5: {"tank": (1, 1), "damage": (2, 2), "support": (2, 2)},
    6: {"tank": (1, 2), "damage": (2, 3), "support": (2, 2)},
}

# ─────────────────────────────────────────────────────────────────────────
# 현재 패치 메타의 팀 인원수. 5vs5 ↔ 6vs6를 바꾸는 **유일한 고정점**이다.
# 패치가 바뀌면 이 값만 5 또는 6으로 고치면 역할 좁히기, 답변 프롬프트에
# 들어가는 규격 설명, 답변 하단 정정 버튼("5대5예요"/"6대6이에요")이 전부
# 따라 바뀐다. 다른 곳에 5나 6을 직접 쓰지 마라.
#
# 인원수는 추측하지 않는다 — 사용자가 직접 말하거나("5대5야") 답변 하단
# 버튼을 누르기 전까지는 항상 이 값을 쓴다.
# ─────────────────────────────────────────────────────────────────────────
CURRENT_META_ROSTER_SIZE = 6

# 아군 조합을 역할 좁히기에 쓸 수 있는 유효 기간. 이보다 오래되면 답변 참고
# 자료로만 쓴다.
ROLE_NARROWING_MAX_AGE_SECONDS = 5 * 60

# "5대5야", "6대6인데" 같은 인원수 선언을 읽는다. 숫자 사이에 대/vs/v/: 를 허용.
_ROSTER_SIZE_PATTERN = re.compile(r"(?<![0-9])([56])\s*(?:대|vs|VS|v|V|:)\s*([56])(?![0-9])")


def detect_roster_size(text: str) -> Optional[int]:
    """사용자가 직접 밝힌 팀 인원수(5 또는 6). 없으면 None.

    "5대5야"처럼 양쪽 숫자가 같을 때만 인정한다 — "5대6" 같은 표기는 오타이거나
    인원수 선언이 아닐 가능성이 높아 추측하지 않는다.
    """
    if not text:
        return None
    match = _ROSTER_SIZE_PATTERN.search(text)
    if not match or match.group(1) != match.group(2):
        return None
    return int(match.group(1))


def resolve_roster_size(declared: Optional[int]) -> int:
    """이번 답변에 실제로 적용할 인원수. 사용자가 밝힌 값이 있으면 그 값,
    없으면 현재 메타(CURRENT_META_ROSTER_SIZE)."""
    if declared in ROSTER_ROLE_RANGES:
        return declared
    return CURRENT_META_ROSTER_SIZE


def alternate_roster_size(roster_size: Optional[int] = None) -> int:
    """지금 적용 중인 인원수의 반대쪽(5↔6). 답변 하단 정정 버튼용 —
    6대6으로 답했으면 "5대5예요", 5대5로 답했으면 "6대6이에요"가 붙는다."""
    return 5 if resolve_roster_size(roster_size) == 6 else 6


def roster_size_label(roster_size: int) -> str:
    """5 → "5대5"."""
    return f"{roster_size}대{roster_size}"


def roster_size_button_label(roster_size: int) -> str:
    """정정 버튼 라벨. 받침 유무에 따라 조사가 달라진다
    (5="오"→"5대5예요", 6="육"→"6대6이에요")."""
    suffix = "예요" if roster_size == 5 else "이에요"
    return f"{roster_size_label(roster_size)}{suffix}"


def roster_role_quota_text(roster_size: int) -> str:
    """프롬프트에 넣는 역할 정원 설명. 예: "탱커 1명, 딜러 2명, 힐러 2명"
    (6인은 "탱커 1~2명, 딜러 2~3명, 힐러 2명")."""
    ranges = ROSTER_ROLE_RANGES.get(roster_size) or ROSTER_ROLE_RANGES[CURRENT_META_ROSTER_SIZE]
    parts = []
    for role in ROLE_HEROES:
        low, high = ranges[role]
        count = f"{low}명" if low == high else f"{low}~{high}명"
        parts.append(f"{ROLE_LABELS[role]} {count}")
    return ", ".join(parts)

# 예전 이름(표준 구성 쿼터). 5vs5 기준 값이라 그대로 두되, 새 코드는
# ROSTER_ROLE_RANGES를 쓴다.
TEAM_COMP_ROLE_QUOTA = {"tank": 1, "damage": 2, "support": 2}


def count_roles(heroes: List[str]) -> Dict[str, int]:
    """영웅 목록을 역할별 인원수로 센다(역할을 모르는 이름은 무시)."""
    counts = {"tank": 0, "damage": 0, "support": 0}
    for hero in heroes or []:
        role = HERO_TO_ROLE.get(normalize_hero_name(hero))
        if role in counts:
            counts[role] += 1
    return counts


def _fits_roster(counts: Dict[str, int], roster_size: int) -> bool:
    """이미 확정된 인원수가 그 인원수 규격의 상한을 넘지 않는지."""
    ranges = ROSTER_ROLE_RANGES[roster_size]
    return all(counts[role] <= ranges[role][1] for role in counts)


def _can_complete(counts: Dict[str, int], unknown: int, roster_size: int) -> bool:
    """확정 인원 + 미지의 팀원 `unknown`명으로 유효한 조합을 만들 수 있는가.

    각 역할의 남은 여유(max-현재)의 합이 unknown 이상이어야 하고, 아직 최소
    인원을 못 채운 역할들의 부족분 합이 unknown 이하여야 한다.
    """
    if unknown < 0:
        return False
    ranges = ROSTER_ROLE_RANGES[roster_size]
    room = sum(max(0, ranges[role][1] - counts[role]) for role in counts)
    shortage = sum(max(0, ranges[role][0] - counts[role]) for role in counts)
    return shortage <= unknown <= room


def analyze_team_comp(
    ally_heroes: List[str], roster_size: Optional[int] = None
) -> Dict[str, Any]:
    """사용자가 말한 아군 조합으로 "사용자가 맡을 수 있는 역할"을 좁힌다.

    반환:
        roster_size      팀 인원수(5 또는 6)
        counts           역할별 확정 인원수
        known_count      역할을 알아낸 아군 수
        candidate_roles  사용자가 맡을 수 있는 역할 목록(좁히지 못하면 3개 전부)
        is_last_slot     사용자 자리가 마지막 한 자리인지(미지의 팀원이 없음)
        is_full_roster   말한 아군만으로 이미 정원이 찬 조합인지(사용자 자리 없음)

    인원수는 추측하지 않는다 — 사용자가 직접 알려준 값(roster_size 인자)이
    없으면 항상 현재 메타(CURRENT_META_ROSTER_SIZE)로 본다.
    """
    counts = count_roles(ally_heroes)
    known_count = sum(counts.values())

    roster_size = resolve_roster_size(roster_size)

    # 말한 아군만으로 정원이 다 찼으면 사용자가 채울 자리가 없다 — "내가 뭘
    # 고를까"가 아니라 완성된 팀 조합 자체를 평가해달라는 질문이다.
    full_roster = len(ally_heroes or []) >= roster_size

    # 사용자 자신을 뺀 나머지 자리 중 아직 정체를 모르는 팀원 수.
    unknown_teammates = roster_size - 1 - known_count

    candidate_roles = []
    for role in ROLE_HEROES:
        assumed = dict(counts)
        assumed[role] += 1
        if not _fits_roster(assumed, roster_size):
            continue
        if not _can_complete(assumed, unknown_teammates, roster_size):
            continue
        candidate_roles.append(role)

    if not candidate_roles:
        # 규격에 안 맞는 조합이면 좁히지 않는다.
        candidate_roles = list(ROLE_HEROES)

    return {
        "roster_size": roster_size,
        "counts": counts,
        "known_count": known_count,
        "candidate_roles": candidate_roles,
        "is_last_slot": unknown_teammates <= 0,
        "is_full_roster": full_roster,
    }


def can_be_roster_size(ally_heroes: List[str], roster_size: int) -> bool:
    """지금까지 말한 아군 조합이 그 인원수 규격으로도 성립할 수 있는가.

    인원수 정정 버튼("5대5예요"/"6대6이에요")을 보여줄지 결정하는 데 쓴다 —
    사용자 자리를 포함해 정원을 이미 넘었거나(아군을 roster_size명 이상 말함),
    어느 역할이든 그 규격의 상한을 넘었으면(5vs5인데 탱커 2명/딜러 3명 등)
    그 인원수로는 성립할 수 없으므로 버튼을 숨긴다. 힐러는 두 규격 모두 2명
    이라 판별에 쓰이지 않는다.
    """
    if roster_size not in ROSTER_ROLE_RANGES:
        return False
    if len(ally_heroes or []) >= roster_size:
        return False
    return _fits_roster(count_roles(ally_heroes), roster_size)


def infer_missing_role_from_team_comp(ally_heroes: List[str]) -> Optional[str]:
    """아군 조합으로 사용자 역할이 **하나로** 확정될 때만 그 역할을 돌려준다.

    (예: 6vs6에서 딜러 3 + 힐러 2를 나열 → 남은 자리는 탱커뿐)
    두 개 이상으로 좁혀지거나 전혀 좁혀지지 않으면 None — 호출부가 되묻거나
    복합 역할 필터를 만든다.
    """
    if not ally_heroes:
        return None

    analysis = analyze_team_comp(ally_heroes)
    candidates = analysis["candidate_roles"]
    return candidates[0] if len(candidates) == 1 else None


def detect_stat_input(message: str) -> bool:
    stat_keywords = ["킬", "데스", "딜", "힐", "도움", "어시", "사망", "피해", "치유"]
    has_keyword = any(kw in message for kw in stat_keywords)
    has_number = bool(re.search(r"\d{2,}", message))
    return has_keyword and has_number

def detect_wants_to_keep_hero(text: str) -> bool:
    """사용자가 특정 영웅을 바꾸기보다 계속 하고 싶어하는 표현인지 판단한다."""

    hero = find_first_hero(text)
    if not hero:
        return False
    if hero == find_enemy_mentioned_hero(text):
        return False

    # 붙여 쓰기 대응: 예) "파라쓸건데"
    compact = re.sub(r"\s+", "", text)

    keep_patterns = [
        r"(하고싶|하고싶어|해보고싶|쓰고싶|쓸거|쓸건데|할거|할건데)",
        r"(계속|유지|고정|원챔|포기안|안바꾸|바꾸지않)",
        r"(이기고싶|이기면서|즐기고싶|즐기면서)",
        r"(해도돼|해도될까|가능할까|괜찮을까)",
        # hero_mentioned_as_current_hero의 같은 패턴은 검증용이라, LLM이 null을
        # 준 경우의 규칙 기반 폴백으로 여기서도 본다.
        r"(고르면|고를까|고르는게|골라도|픽하면|픽할까|선택하면|선택할까)",
    ]

    return any(re.search(pattern, compact) for pattern in keep_patterns)


# "상대법/파훼/대처"는 stay 질문에도 쓰이므로 counter로 바로 분류하지 않는다.
# counter는 "카운터/상성 목록" 요청일 때만 해당한다.
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

# 영웅 이름을 생략한 후속 질문("E 스킬은?"). 이런 질문만 이전
# intent/focus_heroes를 이어받는다. "특전 추천해줘"도 앞 대화의 영웅을 그대로
# 두고 묻는 후속 질문이라 여기 포함한다.
_ELLIPSIS_FOLLOWUP_WORDS = [
    "플레이", "운영", "스킬", "포지션", "타이밍", "궁", "굴리", "굴려", "특전", "퍼크",
]
_ELLIPSIS_FOLLOWUP_MAP_PATTERN = re.compile(r"어떤\s*맵|맵에서")


# 특전(퍼크)을 묻는 질문. "특전 추천해줘"의 "추천"이 영웅 추천 요청으로 읽혀
# 추천 영웅 카드가 나가던 문제를 막고, 답변 프롬프트에 특전 전용 지시를 붙이는
# 데 쓴다. 특전/퍼크는 오버워치에서 이 뜻으로만 쓰이는 단어라 단어 등장만으로
# 판단해도 오탐이 없다.
_PERK_QUESTION_PATTERN = re.compile(r"특전|퍼크|perk", re.IGNORECASE)


def is_perk_question(message: str) -> bool:
    if not message:
        return False
    return bool(_PERK_QUESTION_PATTERN.search(message))


def is_ellipsis_followup(text: str) -> bool:
    if find_all_heroes(text):
        return False
    # 맵을 언급한 질문은 그 자체로 완결된 질문이라 이전 턴을 이어받지
    # 생략형 후속 질문으로 보면 안 된다.
    if find_map(text):
        return False
    if any(word in text for word in _ELLIPSIS_FOLLOWUP_WORDS):
        return True
    return bool(_ELLIPSIS_FOLLOWUP_MAP_PATTERN.search(text))


def infer_intent_by_rule(message: str, context: Dict[str, Any]) -> str:
    text = message.strip()

    # 1. swap: 교체 의도가 명확한 표현. stay 신호와 안 겹치는 가장 구체적인
    # 신호라 최우선으로 본다. 단 "안 바꾸고"류는 stay로 처리.
    if _SWAP_TRIGGER_PATTERN.search(text):
        compact = re.sub(r"\s+", "", text)

        if any(word in compact for word in ["안바꾸", "바꾸지않", "그대로", "유지", "고정"]):
            return "stay"

        return "swap"

    # 2. situation: 위기/압박 토로. 예: "파라가 계속 압박해"
    # detect_wants_to_keep_hero(3번)보다 먼저 검사해야 "계속"이라는 흔한 단어
    # 때문에 stay로 잘못 묶이지 않는다.
    if detect_situation(text):
        return "situation"

    # 3. stay: 영웅 유지 의사. 예: "파라 하고싶어"
    if detect_wants_to_keep_hero(text):
        return "stay"

    # 4. stay: 영웅을 유지한 채 상대법 문의. 예: "겐지로 윈스턴 상대법 알려줘"
    if detect_stay_with_named_hero(text):
        return "stay"

    # 5. counter: 대표 카운터/상성 목록 요청. 예: "겐지 카운터 알려줘"
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

    # 이전 intent는 생략형 후속 질문일 때만 참고한다.
    if is_ellipsis_followup(text):
        previous_intent = context.get("last_intent")
        if previous_intent in ["counter", "stay", "swap", "performance_improve", "map_strategy", "situation"]:
            return previous_intent

    return "general"


# 상대 영웅을 가리키는 패턴. infer_current_hero도 공유해 상대를 자기 영웅으로
# 인식하지 않게 막는다. _NARROWING_FILLER는 조사와 동사 사이 부사를 허용한다.
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

# 역할로만 카운터 대상을 좁히는 경우 — 다른 상대 영웅은 함께 언급하지 않는다.
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

    # 자유 캡처가 이름 뒤 조사까지 먹는 경우를 보정한다. 긴 조사부터 검사한다.
    for suffix in ["이랑", "랑", "과", "와", "이", "가", "은", "는", "을", "를", "도", "만"]:
        if cleaned.endswith(suffix):
            normalized = normalize_hero_name(cleaned[:-len(suffix)].strip())
            if normalized in valid_heroes:
                return normalized

    return None


# 후보 영웅을 나란히 비교하는 문장은 마커가 없어 extract_ally_team이 놓친다.
# 적대 신호가 있으면 적용하지 않는다.
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


# 동료의 역할 수행을 불만하는 문장의 영웅은 아군이다. 적대 마커가 바로 앞에
# 있으면 적용하지 않는다.
_ALLY_COMPLAINT_PATTERN = re.compile(
    r"([가-힣A-Za-z0-9\.]+)[이가]\s*(?:힐|케어|탱킹|딜|나를)[^.!?\n]{0,10}(?:안|못|않)"
)


def find_ally_complaint_hero(text: str) -> Optional[str]:
    match = _ALLY_COMPLAINT_PATTERN.search(text)
    if not match:
        return None
    candidate = normalize_hero_candidate(match.group(1))
    if not candidate:
        return None
    if re.search(r"(?:상대|적)\s*$", text[:match.start(1)]):
        return None
    return candidate


# 시너지를 묻는 문장의 영웅은 아군이다. 적대 신호가 있으면 적용하지 않는다.
_SYNERGY_WORDS = [
    "조합", "시너지", "궁합", "같이 쓰면", "같이 하면", "같이 할 때",
    "함께 쓰면", "함께 하면", "랑 할 때", "이랑 할 때",
]


def find_synergy_ally_heroes(text: str) -> List[str]:
    if any(word in text for word in ADVERSARIAL_SIGNAL_WORDS):
        return []
    if not any(word in text for word in _SYNERGY_WORDS):
        return []
    return find_all_heroes(text)


def find_enemy_mentioned_hero(text: str) -> Optional[str]:
    for pattern in ENEMY_MENTION_PATTERNS:
        match = re.search(pattern, text)
        if match:
            candidate = normalize_hero_candidate(match.group(1))
            if candidate:
                return candidate
    return None


# 특정 영웅의 사용법을 묻는 질문의 영웅은 상대가 아니라 설명 대상이다.
_HERO_USAGE_GUIDE_WORDS = ["활용법", "운영법", "사용법", "쓰는 법", "다루는 법", "활용"]


def is_hero_usage_guide_question(text: str) -> bool:
    return bool(
        find_map(text)
        and any(word in text for word in _HERO_USAGE_GUIDE_WORDS)
        and find_all_heroes(text)
    )


def infer_target_enemy(message: str, context: Dict[str, Any], intent: str) -> Optional[str]:
    text = message.strip()
    current_hero = normalize_hero_name(context.get("current_hero"))

    enemy_mentioned = find_enemy_mentioned_hero(text)
    if enemy_mentioned and enemy_mentioned != current_hero:
        return enemy_mentioned

    if intent == "swap":
        new_situation = bool(find_map(text) or find_side(text) or extract_enemy_team(text))
        return None if new_situation else context.get("target_enemy")

    if is_hero_usage_guide_question(text):
        return None

    # 최후 수단: 메시지의 첫 영웅을 상대로 본다. 아군으로 분류된 영웅은
    # 제외하며, 규칙 기반 탐지가 놓치는 경우를 위해 ally_team_this_turn도 본다.
    complaint_hero = find_ally_complaint_hero(text)
    ally_named_this_turn = (
        set(extract_ally_team(text))
        | set(find_self_comparison_heroes(text))
        | set(find_synergy_ally_heroes(text))
        | ({complaint_hero} if complaint_hero else set())
        | set(context.get("ally_team_this_turn") or [])
    )
    heroes_in_text = find_all_heroes(text)
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

    # 적으로 언급된 영웅은 제외한다(트리거 단어와 우연히 겹칠 수 있다).
    enemy_mentioned = find_enemy_mentioned_hero(text)
    if enemy_mentioned:
        enemy_heroes.add(enemy_mentioned)

    if "말고" in text:
        before = text.split("말고")[0]
        hero = find_first_hero(before)
        if hero and hero not in enemy_heroes:
            return hero

    # 검증된 자기 선언만 채택한다. 주제로만 언급된 영웅은 focus_heroes의 몫이고,
    # 세션에 남은 이전 current_hero를 자동으로 되살리지도 않는다.
    for hero in find_all_heroes(text):
        if hero in enemy_heroes:
            continue
        # 조합 나열의 일부로 불린 이름은 자기 영웅이 아니다.
        if hero_listed_in_ally_comp(hero, text):
            continue
        if hero_mentioned_as_current_hero(hero, text):
            return hero

    return None


# 앵커링된 자기 역할 선언. hero_mentioned_as_current_hero와 같은 수준으로 좁게
# 간다 — 남의 역할을 말하는 문장이 걸리면 안 된다.
_ROLE_WORDS = {
    "탱커": "tank", "탱": "tank",
    "딜러": "damage", "딜": "damage",
    "힐러": "support", "지원가": "support", "힐": "support",
}
_SELF_ROLE_PATTERN = re.compile(
    r"(?:^|[^가-힣])(?:난|나는|나|내가|저는|제가)\s*"
    r"(탱커|딜러|힐러|지원가)\s*"
    r"(?:야|이야|입니다|이에요|예요|임|인데|인데요|고|이고|할게|할래|로|으로|$|[.!?\s,])"
)

# "탱커랑 딜러로만 알려줘", "탱커/딜러 기준으로"처럼 여러 역할을 함께 고르는 표현.
_MULTI_ROLE_PATTERN = re.compile(
    r"(탱커|딜러|힐러|지원가)\s*(?:랑|이랑|나|이나|와|과|하고|,|/|\+)\s*"
    r"(탱커|딜러|힐러|지원가)\s*"
    r"(?:로만|으로만|만|로|으로|기준|중에서|중)"
)


def role_filter_from_text(message: str) -> Optional[str]:
    # 역할 단어가 문장 전체와 사실상 같을 때만 자기 역할 선언으로 본다.
    stripped = re.sub(r"[\s,.!?~]+", "", message)
    # 역할 단어 하나에 서술격 어미만 붙은 짧은 대답. 어미를 나열하면 누락이
    # 잦아 정규식으로 받는다.
    short_reply = re.match(
        r"^(탱커|탱|딜러|딜|힐러|지원가|힐|전체|전부)"
        r"(요|임|야|이야|이에요|에요|예요|입니다|이야요|이요|다|입니당)?$",
        stripped,
    )
    if short_reply:
        word = short_reply.group(1)
        if word in ("전체", "전부"):
            return "all"
        return _ROLE_WORDS[word]

    # 두 역할을 함께 고른 경우 — 한 역할만 돌려주면 나머지가 통째로 빠진다.
    multi = _MULTI_ROLE_PATTERN.search(message)
    if multi:
        roles = {_ROLE_WORDS[g] for g in multi.groups() if g in _ROLE_WORDS}
        if len(roles) >= 2:
            return make_role_filter(list(roles))

    # "나는 힐러야"처럼 앵커링된 자기 역할 선언.
    self_role = _SELF_ROLE_PATTERN.search(message)
    if self_role:
        return _ROLE_WORDS.get(self_role.group(1))

    if any(word in message for word in ["탱커로", "탱커 추천", "탱커가 잡혔", "탱커 해야"]):
        return "tank"
    if any(word in message for word in ["딜러로", "딜러 추천", "딜러가 잡혔", "딜러 해야"]):
        return "damage"
    if any(word in message for word in ["힐러로", "힐러 추천", "힐러가 잡혔", "힐러 해야", "지원가로", "지원가 추천"]):
        return "support"
    if any(word in message for word in ["전체로", "전체 추천", "전부 알려", "다 알려"]):
        return "all"
    return None


# 정정만 있고 질문은 없는 메시지. 여기 걸리면 merge_context_node가 정정 버튼과
# 똑같이 직전 질문을 다시 태운다.
# 판정은 좁게 간다 — 역할/인원수 표현과 상투적인 조사·요청 표현을 지운 뒤에도
# 내용이 남거나 영웅 이름이 섞여 있으면 그 자체로 답할 내용이 있는 새 질문이다.
_ROLE_CORRECTION_FILLER_PATTERN = re.compile(
    r"(탱커|딜러|힐러|지원가|전체|전부|탱|딜|힐"
    r"|\d\s*대\s*\d|\d\s*v\s*s?\s*\d"
    r"|나는|난|내가|저는|제가|나|저"
    r"|알려주세요|알려줘|추천해주세요|추천해줘|추천|해주세요|해줘|주세요"
    r"|기준으로|기준|중에서|중|으로만|로만|만"
    r"|입니다|이에요|에요|예요|이야|였어|이고|인데요|인데|임|이라|야|고|요"
    r"|할게요|할래요|할게|할래|맞아요|맞아|입장|플레이"
    r"|은|는|이|가|을|를|로|으로|랑|이랑|하고|와|과)"
)


def is_pure_role_correction(message: str) -> bool:
    """역할/인원수 정정 그 자체만 담긴 메시지인지."""
    if not message:
        return False
    if not (role_filter_from_text(message) or detect_roster_size(message)):
        return False
    if find_all_heroes(message):
        return False
    remainder = _ROLE_CORRECTION_FILLER_PATTERN.sub("", message)
    remainder = re.sub(r"[\s,.!?~]+", "", remainder)
    return not remainder


# current_hero를 모를 때 역할을 되물어야 하는 intent.
# map_strategy(영웅과 무관할 수 있음)와 composition(역할 없이도 평가 가능하고
# role_filter="all" 폴백이 있음)은 제외한다.
ROLE_CLARIFICATION_INTENTS = {
    "performance_improve", "stay", "swap", "general", "counter", "situation",
}

# 카드가 나가는 경우는 merge_context_node의 matchup_subject 기준 두 가지뿐이다:
# counter이면서 영웅 미확정, 또는 swap이면서 이미 쓰는 영웅이 있는 경우.


def should_ask_role_filter(state: ChatbotGraphState) -> bool:
    """현재 역할을 전혀 알 수 없을 때 역할을 먼저 물어야 하는지 판단한다:
    1) counter이고 상대는 지목했지만 내 역할을 모르는 경우, 2) 상대 지목
    여부와 무관하게 지금 영웅 자체를 몰라 개인화된 답이 불가능한 경우.
    role_filter는 merge_context_node가 이미 explicit_role_filter →
    current_hero_role → 세션 잔존값 순으로 채우므로, 여기서 비어 있다는 것은
    어떤 방법으로도 역할을 알아낼 수 없었다는 뜻이다."""
    role_filter = state.get("role_filter")
    if role_filter:
        return False

    message = state.get("message", "")
    if role_filter_from_text(message):
        return False

    intent = state.get("intent")
    target_enemy = state.get("target_enemy")

    if intent == "counter" and target_enemy:
        # 조합이 최근 것이면 그 후보 기준으로 바로 답하고, 오래됐으면 되묻는다.
        return not state.get("role_candidates_fresh")

    # 팀 전체 스탯이 있으면 그 데이터가 답변 근거가 되므로 역할을 몰라도 답한다
    # (counter는 위에서 이미 처리됐다).
    if state.get("my_team_stats"):
        return False

    # 역할 후보가 좁혀졌으면 되묻지 않고 답한 뒤 버튼/말로 정정받는다.
    if state.get("role_candidates_fresh"):
        return False

    # focus_heroes가 곧 설명 대상인 intent(performance_improve/stay)면 역할을
    # 몰라도 그 영웅 기준으로 답할 수 있다. general/situation은 제외 — 상대만
    # 언급된 상황이라 여전히 사용자 역할이 필요하다.
    focus_hero_sufficient = intent in ("performance_improve", "stay") and bool(state.get("focus_heroes"))
    if not state.get("current_hero") and not focus_hero_sufficient and intent in ROLE_CLARIFICATION_INTENTS:
        return True

    return False

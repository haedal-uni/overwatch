"""오버워치2 영웅/맵 기초 데이터와 이름 해석 함수.

영웅 추가·별칭 수정은 이 파일에서만 한다. 표준 이름 규칙과 추가 체크리스트는
chat_모듈_구조.md 참고.
"""

import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


HEROES = [
    "겐지", "트레이서", "솜브라", "리퍼", "캐서디", "애쉬", "위도우메이커", "한조",
    "소전", "솔저76", "파라", "에코", "메이", "토르비욘", "정크랫", "바스티온",
    "시메트라", "벤처", "벤데타", "안란", "엠레", "프레야", "시에라", "시온",
    "라인하르트", "윈스턴", "디바", "자리야", "오리사", "시그마", "라마트라",
    "레킹볼", "둠피스트", "로드호그", "정커퀸", "마우가", "해저드", "도미나",
    "아나", "키리코", "모이라", "루시우", "브리기테", "젠야타", "바티스트",
    "메르시", "일리아리", "라이프위버", "주노", "제트팩 캣", "우양", "미즈키", "디몬"
]

HERO_ALIASES = {
    "둠피": "둠피스트",
    "둠": "둠피스트",
    "솔저": "솔저76",
    "솔져": "솔저76",
    "솔저: 76": "솔저76",
    "솔저:76": "솔저76",
    "솔저 76": "솔저76",
    "솔져: 76": "솔저76",
    "솔져:76": "솔저76",
    "솔져 76": "솔저76",
    "솔져76": "솔저76",
    "D.Va": "디바",
    "디바": "디바",
    "바스" : "바스티온",
    "시메": "시메트라",
    "라인": "라인하르트",
    "정크" : "정크랫",
    "정크렛": "정크랫",
    "브리" : "브리기테",
    "위도우" : "위도우메이커", 
    "호그" : "로드호그",
    "제트팩" : "제트팩 캣",
    "캣" : "제트팩 캣",
    "트레" : "트레이서",
    "일리야리" : "일리아리",
    "젠" : "젠야타",
    "해자드" : "해저드",
    "D.Mon" : "디몬",
    "디몬(D.Mon)" : "디몬",
    "디먼" : "디몬",
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
        "도미나", "해저드", "디몬"
    ],
    "damage": [
        "겐지", "트레이서", "솜브라", "리퍼", "캐서디", "애쉬", "위도우메이커", "한조",
        "소전", "솔저76", "파라", "에코", "메이", "토르비욘", "정크랫",
        "바스티온", "시메트라", "벤처", "벤데타", "시에라", "안란", "엠레", "프레야", "시온"
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


def _validate_hero_tables() -> List[str]:
    """HEROES와 ROLE_HEROES가 같은 영웅 집합을 같은 표기로 담고 있는지 검사한다.

    import 시점에 돌려 경고만 남긴다(raise하지 않는다).
    """
    problems = []

    role_heroes = [h for heroes in ROLE_HEROES.values() for h in heroes]
    hero_set, role_set = set(HEROES), set(role_heroes)

    if len(role_heroes) != len(role_set):
        dupes = sorted({h for h in role_heroes if role_heroes.count(h) > 1})
        problems.append(f"ROLE_HEROES에 중복된 영웅: {dupes}")
    if role_set - hero_set:
        problems.append(f"ROLE_HEROES에만 있는 영웅(HEROES 누락): {sorted(role_set - hero_set)}")
    if hero_set - role_set:
        problems.append(f"HEROES에만 있는 영웅(역할 미지정): {sorted(hero_set - role_set)}")

    # 별칭이 가리키는 영웅이 없으면 정규화 결과가 어디에도 매칭되지 않는다.
    unknown_alias_targets = sorted(
        {target for target in HERO_ALIASES.values() if target not in hero_set}
    )
    if unknown_alias_targets:
        problems.append(f"HERO_ALIASES가 가리키는 미등록 영웅: {unknown_alias_targets}")

    return problems


_HERO_TABLE_PROBLEMS = _validate_hero_tables()
if _HERO_TABLE_PROBLEMS:
    for _problem in _HERO_TABLE_PROBLEMS:
        logger.error("[HERO TABLE] %s", _problem)

def normalize_hero_name(hero: Optional[str]) -> Optional[str]:
    if not hero:
        return None

    hero = hero.strip()

    if hero in HERO_ALIASES:
        return HERO_ALIASES[hero]

    return hero


# 문서 절 제목/표 칸에 붙는 괄호 설명.
_PAREN_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")


def resolve_hero_name(raw: Optional[str]) -> Optional[str]:
    """문서 표기를 등록된 영웅의 표준 이름으로. 영웅이 아니면 None.

    모르는 이름을 그대로 돌려주는 normalize_hero_name과 달리 영웅만 통과시킨다.
    """
    if not raw:
        return None
    normalized = normalize_hero_name(_PAREN_SUFFIX_RE.sub("", raw.strip()))
    return normalized if normalized in HERO_TO_ROLE else None


# 텍스트 탐색용 "표기 → 표준 이름" 사전.
HERO_NAME_TO_CANONICAL: Dict[str, str] = {}
for _name in HEROES:
    HERO_NAME_TO_CANONICAL[_name] = _name
for _alias, _canonical in HERO_ALIASES.items():
    HERO_NAME_TO_CANONICAL.setdefault(_alias, _canonical)

# 긴 표기 우선(짧은 이름이 긴 이름 안에서 잡히는 오탐 방지). 같은 길이는 사전 순.
_HERO_NAMES_LONGEST_FIRST = sorted(
    HERO_NAME_TO_CANONICAL, key=lambda n: (-len(n), n)
)


def _scan_hero_mentions(text: str) -> List[tuple]:
    """텍스트에서 영웅 표기를 전부 찾아 (등장 위치, 표준 이름) 목록으로 돌려준다."""
    if not text:
        return []

    # 매치 구간을 같은 길이로 마스킹해 두 번 잡히지 않게 한다.
    masked = text
    mentions = []

    for name in _HERO_NAMES_LONGEST_FIRST:
        start = 0
        while True:
            idx = masked.find(name, start)
            if idx == -1:
                break
            mentions.append((idx, HERO_NAME_TO_CANONICAL[name]))
            masked = masked[:idx] + ("\x00" * len(name)) + masked[idx + len(name):]
            start = idx + len(name)

    mentions.sort(key=lambda item: item[0])
    return mentions


def find_first_hero(text: str) -> Optional[str]:
    """텍스트에 가장 먼저 등장하는 영웅의 표준 이름."""
    mentions = _scan_hero_mentions(text)
    return mentions[0][1] if mentions else None


def hero_mentioned_in_text(hero: Optional[str], text: str) -> bool:
    """영웅명이 정식 명칭 또는 별칭으로 텍스트에 등장했는지 확인한다."""
    if not hero or not text:
        return False

    normalized = normalize_hero_name(hero)

    if normalized and normalized in text:
        return True

    for h in HEROES:
        if normalize_hero_name(h) == normalized and h in text:
            return True

    for alias, canonical in HERO_ALIASES.items():
        if canonical == normalized and alias in text:
            return True


def find_all_heroes(text: str) -> List[str]:
    """텍스트에 등장한 영웅 표준 이름을 등장 순서대로(중복 없이) 돌려준다."""
    found = []

    for _, canonical in _scan_hero_mentions(text):
        if canonical not in found:
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


# --- 역할 필터(role_filter) ---------------------------------------------
# "tank"/"damage"/"support"/"all" 외에 "tank+damage" 같은 복합 필터를 허용한다.
ROLE_FILTER_SEPARATOR = "+"


def parse_role_filter(role_filter: Optional[str]) -> List[str]:
    """role_filter를 역할 코드 목록으로 편다. 빈 목록은 "제한 없음"이다."""
    if not role_filter or role_filter == "all":
        return []

    roles = [
        part.strip() for part in str(role_filter).split(ROLE_FILTER_SEPARATOR)
    ]
    # 중복 제거 + 역할 정의 순서로 정렬해 표기를 고정한다.
    return [role for role in ROLE_HEROES if role in roles]


def make_role_filter(roles: List[str]) -> Optional[str]:
    """역할 코드 목록을 role_filter 문자열로 만든다(세 역할 전부면 "all")."""
    ordered = [role for role in ROLE_HEROES if role in set(roles or [])]
    if not ordered:
        return None
    if len(ordered) == len(ROLE_HEROES):
        return "all"
    return ROLE_FILTER_SEPARATOR.join(ordered)


def heroes_for_role_filter(role_filter: Optional[str]) -> List[str]:
    """role_filter가 허용하는 영웅 목록(복합 필터면 두 역할을 합쳐서)."""
    heroes: List[str] = []
    for role in parse_role_filter(role_filter):
        heroes.extend(ROLE_HEROES[role])
    return heroes


def role_filter_label(role_filter: Optional[str]) -> str:
    """사용자/프롬프트에 보여줄 역할 이름("탱커", "탱커+딜러", "전체")."""
    roles = parse_role_filter(role_filter)
    if not roles:
        return ROLE_LABELS["all"]
    return ROLE_FILTER_SEPARATOR.join(ROLE_LABELS[role] for role in roles)


# 숫자로 끝나는 이름은 읽는 소리의 받침을 본다.
_DIGIT_HAS_FINAL_CONSONANT = {
    "0": True,   # 영
    "1": True,   # 일
    "2": False,  # 이
    "3": True,   # 삼
    "4": False,  # 사
    "5": False,  # 오
    "6": True,   # 육
    "7": True,   # 칠
    "8": True,   # 팔
    "9": False,  # 구
}


def has_final_consonant(word: Optional[str]) -> bool:
    """마지막 글자에 받침이 있는지. 조사 선택(을/를, 이/가)의 공용 판단."""
    if not word:
        return False
    last_char = word[-1]
    if last_char in _DIGIT_HAS_FINAL_CONSONANT:
        return _DIGIT_HAS_FINAL_CONSONANT[last_char]
    code = ord(last_char)
    return 0xAC00 <= code <= 0xD7A3 and (code - 0xAC00) % 28 != 0


def josa_eul_reul(word: Optional[str]) -> str:
    """받침 유무에 따라 '을'/'를' 조사를 고른다."""
    return "을" if has_final_consonant(word) else "를"

"""오버워치2 영웅/맵 기초 데이터와 이름 해석 함수.

이 모듈은 다른 chat 모듈에 의존하지 않는 최하위 계층이다(순수 데이터 + 문자열
처리). 영웅 목록을 손보는 작업은 전부 여기서 끝난다.

주의:
- 내부 표준 이름(canonical)은 chat/vision/hero_icons/{이름}.png 파일명과 프론트 카드의
  아이콘 URL이 그대로 쓰는 값이다. 공식 표기가 달라도(예: 공식 "솔저: 76",
  내부 "솔저76") 표준 이름을 바꾸면 아이콘이 깨지므로, 표기 차이는 전부
  HERO_ALIASES로 흡수한다.
- HEROES와 ROLE_HEROES는 같은 영웅 집합을 같은 표기로 담아야 한다
  (_validate_hero_tables가 import 시점에 검사한다).
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


HEROES = [
    "겐지", "트레이서", "솜브라", "리퍼", "캐서디", "애쉬", "위도우메이커", "한조",
    "소전", "솔저76", "파라", "에코", "메이", "토르비욘", "정크랫", "바스티온",
    "시메트라", "벤처", "벤데타", "안란", "엠레", "프레야", "시에라", "시온",
    "라인하르트", "윈스턴", "디바", "자리야", "오리사", "시그마", "라마트라",
    "레킹볼", "둠피스트", "로드호그", "정커퀸", "마우가", "해저드", "도미나",
    "아나", "키리코", "모이라", "루시우", "브리기테", "젠야타", "바티스트",
    "메르시", "일리아리", "라이프위버", "주노", "제트팩 캣", "우양", "미즈키"
]

HERO_ALIASES = {
    "둠피": "둠피스트",
    "둠": "둠피스트",
    # 표준 이름은 "솔저76"이다(공식 표기와 다름). 아이콘 파일명과 카드 이미지
    # URL이 이 이름을 쓰므로 바꾸면 아이콘이 깨진다 — 표기 차이는 별칭으로 흡수한다.
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

    두 목록을 손으로 맞추는 구조라 실제로 어긋난 적이 있다 — ROLE_HEROES에만
    "솔저: 76"(정규화 전 표기)이 들어 있어 HERO_TO_ROLE["솔저76"]이 없었고,
    그 결과 (1) 솔저76의 역할이 None이라 역할 교정/역할 고정 분기를 타지 못했고
    (2) generate_answer_node의 역할 위반 검사(answer_allowed_hero_set)에서
    "솔저76"이 항상 허용 목록 밖으로 잡혀 답변의 솔저76이 "다른 영웅"으로
    치환됐다. 영웅을 추가할 때 같은 사고가 반복되지 않도록 import 시점에
    검사해 경고를 남긴다(서비스가 죽으면 안 되므로 raise하지 않는다).
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

    # 별칭 대상이 실존하지 않으면 정규화 결과가 HERO_TO_ROLE/아이콘과 매칭되지 않는다.
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

    return hero


# 텍스트 탐색용 "표기 → 표준 이름" 사전. 짧은 이름이 긴 이름 안에 포함되는
# 경우를 오탐하지 않도록 긴 표기부터 검사한다.
HERO_NAME_TO_CANONICAL: Dict[str, str] = {}
for _name in HEROES:
    HERO_NAME_TO_CANONICAL[_name] = _name
for _alias, _canonical in HERO_ALIASES.items():
    HERO_NAME_TO_CANONICAL.setdefault(_alias, _canonical)

# 긴 표기 우선. 같은 길이면 사전 순으로 고정해 결과가 항상 결정론적이게 한다.
_HERO_NAMES_LONGEST_FIRST = sorted(
    HERO_NAME_TO_CANONICAL, key=lambda n: (-len(n), n)
)


def _scan_hero_mentions(text: str) -> List[tuple]:
    """텍스트에서 영웅 표기를 전부 찾아 (등장 위치, 표준 이름) 목록으로 돌려준다.

    긴 표기부터 찾고 매치된 구간을 마스킹해 다시 검사하지 않는다 — 그래서
    "위도우메이커"를 찾은 뒤 그 자리에서 "메이"가 또 잡히지 않는다. 이 처리가
    없던 시절에는 "위도우메이커 상대법"이 ["위도우메이커", "메이"] 두 명으로
    인식돼, 영웅이 2명이라는 이유로 불필요하게 "어떤 영웅 기준으로?"를 되묻고
    ally_team/compared_heroes까지 오염되는 문제가 있었다.
    """
    if not text:
        return []

    # 매치 구간을 마스킹한다. 길이를 유지해야 나머지 이름의 위치가 원문 기준으로 남는다.
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
# "tank"/"damage"/"support"/"all" 외에 "tank+damage"처럼 이어 붙인 복합 필터를
# 허용한다(역할이 둘로만 좁혀질 때). 순서는 ROLE_HEROES 정의 순서로 정규화한다.
ROLE_FILTER_SEPARATOR = "+"


def parse_role_filter(role_filter: Optional[str]) -> List[str]:
    """role_filter를 실제 역할 코드 목록으로 편다.

    "tank" → ["tank"], "tank+damage" → ["tank", "damage"].
    "all" / None / 알 수 없는 값 → [] (역할 제한 없음을 뜻한다 — 호출부는
    빈 목록을 "제한 없음"으로 다뤄야 한다).
    """
    if not role_filter or role_filter == "all":
        return []

    roles = [
        part.strip() for part in str(role_filter).split(ROLE_FILTER_SEPARATOR)
    ]
    # 중복 제거 + ROLE_HEROES 정의 순서(탱커→딜러→힐러)로 정렬해 표기를 고정한다.
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

"""원본 문서에서 특정 절을 이름으로 직접 꺼내오는 유틸(특전 데이터, 영웅 프로필).

벡터 검색을 우회하는 이유는 chat_모듈_구조.md 참고.
"""

import logging
import os
import re
from typing import Dict, List, Optional, Tuple

from chat.domain.heroes import HERO_TO_ROLE, ROLE_LABELS, normalize_hero_name, resolve_hero_name
from chat.rag.vectorstore import MD_PATH

logger = logging.getLogger(__name__)

_HERO_HEADING_RE = re.compile(r"^##\s+(?!#)(.+?)\s*$")
_ANY_H1_H2_RE = re.compile(r"^#{1,2}(?!#)\s")
_NEXT_H3_RE = re.compile(r"^###\s")

# 우선 타겟 답변이 영웅마다 참고하는 항목.
PROFILE_FIELDS = ["기본 위치", "취약한 상대 판정/상황", "조합 내 담당"]

# 원본 문서는 실행 중 바뀌지 않으므로 최초 1회만 파싱한다.
_perk_sections_cache: Optional[Dict[str, str]] = None
_profile_cache: Optional[Dict[str, str]] = None


def read_source_markdown() -> Optional[str]:
    # MD_PATH가 프로젝트 루트 기준 상대 경로다.
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for path in (MD_PATH, os.path.join(here, MD_PATH)):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read()

    logger.warning("[DOC SECTION] 원본 문서를 찾을 수 없음: %s", MD_PATH)
    return None


def _extract_block(lines: List[str], field: str) -> Optional[str]:
    """영웅 절 본문에서 한 항목만 잘라낸다(소제목 형태와 목록 형태 둘 다)."""
    escaped = re.escape(field).replace(r"\ ", r"\s*")
    heading_re = re.compile(r"^###\s+" + escaped + r"\s*$")
    bullet_re = re.compile(r"^(\s*)-\s*" + escaped + r"\s*:")

    for idx, line in enumerate(lines):
        if heading_re.match(line):
            body = [line]
            for next_line in lines[idx + 1:]:
                if _NEXT_H3_RE.match(next_line):
                    break
                body.append(next_line)
            return "\n".join(body).strip()

    for idx, line in enumerate(lines):
        bullet = bullet_re.match(line)
        if not bullet:
            continue
        indent = len(bullet.group(1))
        body = [line]
        for next_line in lines[idx + 1:]:
            if not next_line.strip():
                body.append(next_line)
                continue
            # 목록에는 끝을 알리는 표시가 없어 들여쓰기 깊이로 판단한다.
            if len(next_line) - len(next_line.lstrip()) <= indent:
                break
            body.append(next_line)
        return "\n".join(body).strip()

    return None


def _hero_section_bodies() -> Dict[str, List[str]]:
    """{영웅: 그 영웅 절의 본문 줄들}. 영웅이 아닌 H2 절은 빠진다."""
    text = read_source_markdown()
    if not text:
        return {}

    bodies: Dict[str, List[str]] = {}
    lines = text.splitlines()
    for start in [i for i, line in enumerate(lines) if _HERO_HEADING_RE.match(line)]:
        hero = resolve_hero_name(_HERO_HEADING_RE.match(lines[start]).group(1))
        if not hero:
            continue
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if _ANY_H1_H2_RE.match(lines[i]):
                end = i
                break
        bodies[hero] = lines[start + 1:end]
    return bodies


def _build_perk_sections() -> Dict[str, str]:
    sections = {}
    for hero, body in _hero_section_bodies().items():
        block = _extract_block(body, "특전 데이터")
        if block:
            sections[hero] = block

    logger.info("[PERK SECTION] 특전 데이터 절 %d개 로드", len(sections))
    return sections


def _build_profiles() -> Dict[str, str]:
    profiles = {}
    for hero, body in _hero_section_bodies().items():
        blocks = [
            block for block in
            (_extract_block(body, field) for field in PROFILE_FIELDS)
            if block
        ]
        if blocks:
            profiles[hero] = "\n".join(blocks)

    logger.info("[HERO PROFILE] 영웅 %d명 프로필 로드", len(profiles))
    return profiles


def get_hero_perk_section(hero: Optional[str]) -> Optional[str]:
    """영웅의 "특전 데이터" 절 원문. 문서에 없으면 None."""
    global _perk_sections_cache

    if not hero:
        return None
    if _perk_sections_cache is None:
        _perk_sections_cache = _build_perk_sections()

    return _perk_sections_cache.get(normalize_hero_name(hero))


def get_hero_profile(hero: Optional[str]) -> Optional[str]:
    """우선 타겟 답변이 쓰는 영웅 정보(기본 위치 / 취약한 상대 / 조합 내 담당)."""
    global _profile_cache

    if not hero:
        return None
    if _profile_cache is None:
        _profile_cache = _build_profiles()

    return _profile_cache.get(normalize_hero_name(hero))


_TEAMPLAY_LINE_RE = re.compile(r"^\s*-\s*(기본 위치|협업):")
_POSITION_LINE_RE = re.compile(r"^\s*-\s*기본 위치:")


def get_hero_position(hero: Optional[str]) -> Optional[str]:
    """영웅 프로필의 "기본 위치" 줄(포지션 질문의 근거)."""
    profile = get_hero_profile(hero)
    if not profile:
        return None
    lines = [line.strip() for line in profile.splitlines() if _POSITION_LINE_RE.match(line)]
    return "\n".join(lines) or None


def get_hero_teamplay(hero: Optional[str]) -> Optional[str]:
    """영웅 프로필 중 "기본 위치"와 "협업" 줄만(합이 맞는 영웅을 고를 근거)."""
    profile = get_hero_profile(hero)
    if not profile:
        return None
    lines = [line.strip() for line in profile.splitlines() if _TEAMPLAY_LINE_RE.match(line)]
    return "\n".join(lines) or None


# 스킬 데이터 줄의 괄호 속 첫 항목 → 표준 단축키.
_SKILL_KEY_WORDS = {
    "좌클": "좌클릭", "우클": "우클릭", "좌클릭": "좌클릭", "우클릭": "우클릭",
    "Shift": "shift", "좌Shift": "shift", "E": "e", "Q": "q",
}
# 단축키를 괄호가 아니라 이름 끝에 붙인 줄("펄스 소총 좌클릭(기본 발사)").
_NAME_SUFFIX_KEY_RE = re.compile(r"^(.+?)\s+(좌클릭|우클릭)$")
_SKILL_LINE_RE = re.compile(r"^\s+-\s+(.+?)\(([^)]*)\)\s*:")

_skill_keys_cache: Optional[Dict[str, Dict[str, str]]] = None


def _skill_key_from(paren: str) -> Optional[str]:
    """"좌클, 메카 기본무기" / "조종사 Q, 궁극기" → 표준 단축키. 단축키가 아니면 None."""
    first = paren.split(",")[0].strip()
    if "/" in first:
        return None
    return _SKILL_KEY_WORDS.get(first.split()[-1]) if first else None


# 소제목 형태("### E — 생체장", "#### 기본 무기 — 펄스 소총")의 스킬 줄.
_SKILL_HEADING_RE = re.compile(r"^#{3,4}\s+(.+?)\s+[—–]\s+(.+?)\s*$")
_HEADING_KEY_RE = re.compile(r"(Shift|좌클릭|우클릭|(?<![A-Za-z])[EQ](?![A-Za-z]))")
_HEADING_KEY_WORDS = {"Shift": "shift", "좌클릭": "좌클릭", "우클릭": "우클릭", "E": "e", "Q": "q"}


def _heading_skill_key(label: str, following: List[str]) -> Optional[str]:
    """소제목 왼쪽 표기 → 표준 단축키. 조합 입력(+)은 단축키 하나로 못 정한다."""
    if "+" in label:
        return None
    if "기본 무기" in label:
        # 기본 무기가 둘인 영웅은 바로 아래 "- 좌클릭:"/"- 우클릭:"으로 구분한다.
        for line in following[:4]:
            if line.strip().startswith("- 우클릭"):
                return "우클릭"
        return "좌클릭"
    match = _HEADING_KEY_RE.search(label.split("/")[0])
    return _HEADING_KEY_WORDS[match.group(1)] if match else None


def _build_skill_keys() -> Dict[str, Dict[str, str]]:
    table: Dict[str, Dict[str, str]] = {}
    for hero, body in _hero_section_bodies().items():
        for idx, line in enumerate(body):
            heading = _SKILL_HEADING_RE.match(line)
            if heading:
                key = _heading_skill_key(heading.group(1), body[idx + 1:])
                if key:
                    table.setdefault(hero, {})[heading.group(2).strip()] = key
        in_block = False
        found: Dict[str, set] = {}
        for line in body:
            if line.startswith("- "):
                in_block = line.strip().rstrip(":") == "- 스킬 데이터"
                continue
            if not in_block:
                continue
            match = _SKILL_LINE_RE.match(line)
            if not match:
                continue
            name = match.group(1).strip()
            key = _skill_key_from(match.group(2))
            suffix = _NAME_SUFFIX_KEY_RE.match(name)
            if not key and suffix:
                name, key = suffix.group(1), _SKILL_KEY_WORDS[suffix.group(2)]
            if key:
                found.setdefault(name, set()).add(key)
        # 이름 하나에 키가 둘이면(수리검 좌클릭/우클릭) 바로잡을 기준이 없어 뺀다.
        for name, keys in found.items():
            if len(keys) == 1:
                table.setdefault(hero, {})[name] = next(iter(keys))

    logger.info("[SKILL KEYS] 영웅 %d명 스킬 단축키 로드", len(table))
    return table


_DISPLAY_SKILL_LINE_RE = re.compile(r"^\s+-\s+(?:설정:\s*)?([^:()]+?)\s*(?:\(([^)]*)\))?\s*:")
_DISPLAY_KEY_TOKEN_RE = re.compile(r"(좌클릭|우클릭|좌클|우클|좌Shift|Shift|(?<![A-Za-z])[EQ](?![A-Za-z]))")
_DISPLAY_KEY_ORDER = ["좌클릭", "우클릭", "shift", "e", "q"]

_skill_list_cache: Optional[Dict[str, List[Tuple[str, List[str]]]]] = None


def _build_skill_list() -> Dict[str, List[Tuple[str, List[str]]]]:
    table: Dict[str, List[Tuple[str, List[str]]]] = {}
    keys_by_name = get_skill_keys()
    for hero, body in _hero_section_bodies().items():
        found: Dict[str, List[str]] = {}
        in_block = False
        for line in body:
            if line.startswith("- "):
                in_block = line.strip().rstrip(":") == "- 스킬 데이터"
                continue
            if not in_block:
                continue
            match = _DISPLAY_SKILL_LINE_RE.match(line)
            if not match:
                continue
            name = match.group(1).strip()
            keys = []
            suffix = _NAME_SUFFIX_KEY_RE.match(name)
            if suffix:
                name, keys = suffix.group(1), [_SKILL_KEY_WORDS[suffix.group(2)]]
            elif match.group(2):
                keys = [_SKILL_KEY_WORDS[token] for token in _DISPLAY_KEY_TOKEN_RE.findall(match.group(2))]
            for key in keys:
                if key not in found.setdefault(name, []):
                    found[name].append(key)
        # 소제목 형태로 적힌 영웅은 단축키 표에만 있다.
        for name, key in (keys_by_name.get(hero) or {}).items():
            name = re.sub(r"^설정:\s*", "", name)
            if not found.get(name):
                found[name] = [key]
        rows = [(name, keys) for name, keys in found.items() if keys]
        rows.sort(key=lambda row: min(_DISPLAY_KEY_ORDER.index(key) for key in row[1]))
        if rows:
            table[hero] = rows
    return table


def get_skill_list(hero: Optional[str]) -> List[Tuple[str, List[str]]]:
    """화면에 보여 줄 스킬 목록 [(이름, [단축키…])]. 답변 단축키 교정용 표(get_skill_keys)와 달리 키가 둘인 스킬도 남긴다."""
    global _skill_list_cache
    if _skill_list_cache is None:
        _skill_list_cache = _build_skill_list()
    return _skill_list_cache.get(normalize_hero_name(hero) or "", [])


def get_skill_keys() -> Dict[str, Dict[str, str]]:
    """{영웅: {스킬 이름: 단축키}} — 원본 문서의 "스킬 데이터" 절 기준."""
    global _skill_keys_cache
    if _skill_keys_cache is None:
        _skill_keys_cache = _build_skill_keys()
    return _skill_keys_cache


# 스킬 줄의 이름과 태그 부분("- 다이너마이트(E): 광역 | 자가 피해; 재사용 …").
_SKILL_TAG_LINE_RE = re.compile(r"^\s*-\s+([^:;|]{1,40}?)\s*:\s*([^;]+);")
_TAG_DETAIL_RE = re.compile(r"\(.*?\)")
_skill_tag_cache: Optional[Dict[str, Dict[str, List[str]]]] = None


def _build_skill_tag_index() -> Dict[str, Dict[str, List[str]]]:
    index: Dict[str, Dict[str, List[str]]] = {}
    for hero, body in _hero_section_bodies().items():
        for line in body:
            match = _SKILL_TAG_LINE_RE.match(line)
            # 스킬 줄은 이름에 단축키 괄호가 있거나 태그가 둘 이상이다.
            if not match or ("(" not in match.group(1) and "|" not in match.group(2)):
                continue
            for raw in re.split(r"[|·,/]", _TAG_DETAIL_RE.sub("", match.group(2))):
                # "투사체(낙차 있음, 폭발형, 자가 피해 없음)"의 괄호는 태그가 아니라 세부 설명이다.
                tag = raw.strip()
                if not tag or len(tag) > 12 or re.search(r"[\d:]", tag):
                    continue
                skills = index.setdefault(tag, {}).setdefault(hero, [])
                if match.group(1) not in skills:
                    skills.append(match.group(1))
    return index


# 이보다 적은 영웅에게 붙은 태그는 문서에 드물게만 붙어 있어(밀쳐내기는 267번 나오지만 태그는 1개) 목록으로 쓰지 않는다.
SKILL_TAG_MIN_HEROES = 3


def listable_skill_tags() -> List[str]:
    """영웅 목록 질문에 쓸 태그(붙은 영웅이 많은 순)."""
    index = get_skill_tag_index()
    tags = [tag for tag, heroes in index.items() if len(heroes) >= SKILL_TAG_MIN_HEROES]
    return sorted(tags, key=lambda t: -len(index[t]))


def get_skill_tag_index() -> Dict[str, Dict[str, List[str]]]:
    """{태그: {영웅: [스킬 이름]}} — 원본 문서 스킬 줄의 태그 기준."""
    global _skill_tag_cache
    if _skill_tag_cache is None:
        _skill_tag_cache = _build_skill_tag_index()
    return _skill_tag_cache


def skill_tag_section(tag: Optional[str]) -> Optional[str]:
    """그 태그가 붙은 스킬을 영웅별로 나열한 글(검색 결과 앞에 넣는다)."""
    heroes = get_skill_tag_index().get(tag or "")
    if not heroes:
        return None
    lines = [
        f"- {hero}({ROLE_LABELS.get(HERO_TO_ROLE.get(hero), '')}): {', '.join(skills)}"
        for hero, skills in heroes.items()
    ]
    return f"'{tag}' 태그가 붙은 스킬(원본 문서 전체, {len(heroes)}명)\n" + "\n".join(lines)


_source_lines_cache: Optional[List[str]] = None
# 목록 항목의 이름 부분("- 마이크로 미사일(E): ..."의 "마이크로 미사일(E)").
_LIST_ITEM_LABEL_RE = re.compile(r"^\s*-\s+([^:;|\n]{1,40}?)\s*:")


def continued_line_label(chunk_body: str) -> Optional[str]:
    """줄 중간부터 시작하는 chunk면 그 줄의 항목 이름(chunk 경계에서 잘려 이름이 앞 조각에 남은 경우)."""
    global _source_lines_cache
    if _source_lines_cache is None:
        _source_lines_cache = (read_source_markdown() or "").split("\n")
    first = (chunk_body or "").lstrip().split("\n", 1)[0].strip()[:60]
    if len(first) < 10:
        return None
    containing = [line for line in _source_lines_cache if first in line]
    if not containing or any(line.lstrip().startswith(first) for line in containing):
        return None
    match = _LIST_ITEM_LABEL_RE.match(containing[0])
    return match.group(1).strip() if match else None


# 영웅을 가리킨다고 볼 스킬 이름의 최소 길이(공백 제외, "돌진" 같은 일반어 제외).
_SKILL_NAME_MIN_LENGTH = 3


def find_heroes_by_skill_name(text: str) -> List[str]:
    """텍스트에 나온 스킬 이름의 주인 영웅들(여러 영웅이 같이 쓰는 이름은 뺀다)."""
    if not text:
        return []
    owners: Dict[str, set] = {}
    for hero, skills in get_skill_keys().items():
        for name in skills:
            owners.setdefault(name, set()).add(hero)
    found: List[tuple] = []
    compact = text.replace(" ", "")
    for name, heroes in owners.items():
        key = name.replace(" ", "")
        if len(heroes) != 1 or len(key) < _SKILL_NAME_MIN_LENGTH:
            continue
        position = compact.find(key)
        if position != -1:
            found.append((position, next(iter(heroes))))
    ordered: List[str] = []
    for _, hero in sorted(found):
        if hero not in ordered:
            ordered.append(hero)
    return ordered


# 탱커 스킬 태그 중 피해를 막아 경감량을 쌓는 것.
_ABSORB_SKILL_TAGS = {"방벽", "방울", "투사체 무효화"}
_STAT_TYPE_LINE_RE = re.compile(r"^\*\*(메인|서브) (딜러|힐러)\*\*\s*:\s*(.+)$")
_STAT_TYPE_KEYS = {
    ("메인", "딜러"): "main_damage", ("서브", "딜러"): "sub_damage",
    ("메인", "힐러"): "main_support", ("서브", "힐러"): "sub_support",
}

_stat_types_cache: Optional[Dict[str, str]] = None


def _is_ultimate_skill(paren: str) -> bool:
    """스킬 줄 괄호("Q", "Q, 궁극기", "조종사 Q, 궁극기")가 궁극기인지."""
    first = paren.split(",")[0].strip()
    return "궁극기" in paren or (bool(first) and first.split()[-1] == "Q")


def _tank_absorbs_damage(body: List[str]) -> bool:
    """궁극기를 뺀 스킬 태그에 방벽·방울·투사체 무효화가 있는지."""
    in_block = False
    for line in body:
        if line.startswith("- "):
            in_block = line.strip().rstrip(":") == "- 스킬 데이터"
            continue
        if not in_block:
            continue
        match = _SKILL_LINE_RE.match(line)
        if not match or _is_ultimate_skill(match.group(2)):
            continue
        tag_text = line[match.end():].split(";")[0]
        tags = {tag.strip() for tag in re.split(r"[|,]", tag_text)}
        if tags & _ABSORB_SKILL_TAGS:
            return True
    return False


def _build_stat_types() -> Dict[str, str]:
    types: Dict[str, str] = {}
    text = read_source_markdown() or ""
    for line in text.splitlines():
        match = _STAT_TYPE_LINE_RE.match(line.strip())
        if not match:
            continue
        type_key = _STAT_TYPE_KEYS[(match.group(1), match.group(2))]
        for raw in match.group(3).split(","):
            hero = resolve_hero_name(raw.strip())
            if hero:
                types[hero] = type_key

    for hero, body in _hero_section_bodies().items():
        if HERO_TO_ROLE.get(hero) == "tank":
            types[hero] = "absorb_tank" if _tank_absorbs_damage(body) else "plain_tank"

    logger.info("[STAT TYPES] 영웅 %d명 스탯 판정 유형 로드", len(types))
    return types


def get_hero_stat_types() -> Dict[str, str]:
    """{영웅: 스탯 판정 유형} — 딜러·힐러는 메인·서브 이론 절, 탱커는 스킬 태그 기준."""
    global _stat_types_cache
    if _stat_types_cache is None:
        _stat_types_cache = _build_stat_types()
    return _stat_types_cache


def skill_key_reference(heroes) -> str:
    """프롬프트에 넣을 영웅별 스킬 줄("- 안란: 맹염 질주(shift), ..."). 무기처럼 키가 둘인 스킬도 넣는다."""
    lines = []
    seen = set()
    for hero in heroes:
        name = normalize_hero_name(hero)
        if not name or name in seen:
            continue
        skills = get_skill_list(name)
        if not skills:
            continue
        seen.add(name)
        lines.append(f"- {name}: " + ", ".join(f"{skill}({'/'.join(keys)})" for skill, keys in skills))
    return "\n".join(lines)

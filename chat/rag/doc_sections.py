"""원본 문서에서 특정 절을 이름으로 직접 꺼내오는 유틸(특전 데이터, 영웅 프로필).

벡터 검색을 우회하는 이유는 chat_모듈_구조.md 참고.
"""

import logging
import os
import re
from typing import Dict, List, Optional

from chat.domain.heroes import normalize_hero_name, resolve_hero_name
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


# 스킬 데이터 줄의 괄호 속 첫 항목 → 표준 단축키.
_SKILL_KEY_WORDS = {"좌클": "좌클릭", "우클": "우클릭", "Shift": "shift", "E": "e", "Q": "q"}
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
        for line in body:
            if line.startswith("- "):
                in_block = line.strip().rstrip(":") == "- 스킬 데이터"
                continue
            if not in_block:
                continue
            match = _SKILL_LINE_RE.match(line)
            if not match:
                continue
            key = _skill_key_from(match.group(2))
            if key:
                table.setdefault(hero, {})[match.group(1).strip()] = key

    logger.info("[SKILL KEYS] 영웅 %d명 스킬 단축키 로드", len(table))
    return table


def get_skill_keys() -> Dict[str, Dict[str, str]]:
    """{영웅: {스킬 이름: 단축키}} — 원본 문서의 "스킬 데이터" 절 기준."""
    global _skill_keys_cache
    if _skill_keys_cache is None:
        _skill_keys_cache = _build_skill_keys()
    return _skill_keys_cache


def skill_key_reference(heroes) -> str:
    """프롬프트에 넣을 영웅별 스킬 단축키 줄("- 안란: 맹염 질주(shift), ...")."""
    table = get_skill_keys()
    lines = []
    seen = set()
    for hero in heroes:
        name = normalize_hero_name(hero)
        if not name or name in seen or name not in table:
            continue
        seen.add(name)
        skills = ", ".join(f"{skill}({key})" for skill, key in table[name].items())
        lines.append(f"- {name}: {skills}")
    return "\n".join(lines)

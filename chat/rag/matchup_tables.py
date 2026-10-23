"""원본 문서의 상성 표를 영웅별 상성표로 읽는다.

상성표 모달과 우선 타겟 답변의 데이터 출처. 역방향 채움·배지 점수 같은
규칙은 chat_모듈_구조.md 참고.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from chat.domain.answer_format import replace_dying_word
from chat.domain.heroes import (
    HERO_TO_ROLE,
    ROLE_HEROES,
    ROLE_LABELS,
    josa_eul_reul,
    josa_euro,
    normalize_hero_name,
    resolve_hero_name,
)
from chat.rag.doc_sections import read_source_markdown
from chat.rag.target_priorities import TIER_LABELS, priority_tier

logger = logging.getLogger(__name__)

SUMMARY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "matchup_summaries.json")

_HERO_HEADING_RE = re.compile(r"^##\s+(?!#)(.+?)\s*$")
_ANY_H1_H2_RE = re.compile(r"^#{1,2}(?!#)\s")
_MATCHUP_HEADING_RE = re.compile(r"^####\s*상성\s*[—–-]\s*(탱커|딜러|힐러)\s*$")
_ANY_HEADING_RE = re.compile(r"^#{1,6}\s")
_TABLE_SEPARATOR_CHARS = set("-: |")

# 부호가 유불리 방향, 절대값이 세기.
_VERDICT_SCORES = {
    "매우 유리": 3,
    "유리": 2,
    "약간 유리": 1,
    "중립": 0,
    "유동적": 0,
    "약간 불리": -1,
    "불리": -2,
    "매우 불리": -3,
}
# "매우 유리" ⊃ "유리"라 긴 표기부터 검사한다.
_VERDICT_LABELS_LONGEST_FIRST = sorted(_VERDICT_SCORES, key=lambda label: -len(label))

_VERDICT_SEGMENT_SEPARATOR = "/"
_INVERT_PLACEHOLDER = "\x00"

# 역할이 바뀌어 다른 영웅 표의 판정이 옛 킷 기준인 영웅. 자기 표에 적힌 판정만 화면에 띄운다.
SELF_TABLE_ONLY_HEROES = frozenset({"솜브라"})

_chart_cache: Optional[Dict[str, Dict[str, Any]]] = None
_summary_cache: Optional[Dict[str, str]] = None


def _load_summaries() -> Dict[str, str]:
    """`{"영웅|상대": 짧은 설명}`. 파일에는 대조용 원문(`source`)도 함께 들어 있다."""
    global _summary_cache
    if _summary_cache is None:
        try:
            with open(SUMMARY_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            _summary_cache = {
                key: (value.get("summary") if isinstance(value, dict) else value) or ""
                for key, value in raw.items()
            }
        except (OSError, ValueError):
            logger.warning("[MATCHUP] 요약 파일이 없어 문서 원문을 그대로 쓴다: %s", SUMMARY_PATH)
            _summary_cache = {}
    return _summary_cache


# 문서 작성자용 메모는 사용자에게 보여줄 내용이 아니다.
_EDITOR_NOTE_RE = re.compile(r"\([^)]*(?:문서|역방향)[^)]*\)")
_DASH_RE = re.compile(r"\s*[—–]\s*")
# 이유가 없다는 자리표시 문장은 설명이 아니라 빈칸이다(요약이 표현을 바꿔 써서 문장 단위로 뺀다).
_NO_REASON_SENTENCE_RE = re.compile(r"[^.]*자료가\s*없[^.]*\.?")


def clean_note(text: str) -> str:
    """화면에 띄울 설명에서 편집 메모와 줄표, 이유 없음 문장을 걷어낸다."""
    text = _NO_REASON_SENTENCE_RE.sub(" ", text or "")
    text = _EDITOR_NOTE_RE.sub(" ", text)
    text = replace_dying_word(_DASH_RE.sub(", ", text))
    return re.sub(r"\s{2,}", " ", text).strip(" ,")


def _parse_table_rows(lines: List[str]) -> List[Tuple[str, str, str]]:
    """마크다운 표에서 (상대, 판정, 이유) 줄만 뽑는다. 이유 칸이 없는 표도 있다."""
    rows: List[Tuple[str, str, str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("상대", "영웅"):
            continue
        if set("".join(cells)) <= _TABLE_SEPARATOR_CHARS:
            continue
        rows.append((cells[0], cells[1], cells[2] if len(cells) > 2 else ""))
    return rows


def _segment_score(segment: str) -> Optional[int]:
    """판정 한 조각의 점수. 판정 표기가 없으면("다른 문서 필요") None."""
    # 괄호 안은 조건 설명이라 뺀다.
    text = re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", " ", segment)).strip()
    for label in _VERDICT_LABELS_LONGEST_FIRST:
        if label in text:
            return _VERDICT_SCORES[label]
    return None


def _verdict_scores(verdict: str) -> List[int]:
    """한 칸에 "유리(1대1) / 불리(궁)"처럼 조건별 판정이 함께 적히기도 한다."""
    scores = []
    for segment in (verdict or "").split(_VERDICT_SEGMENT_SEPARATOR):
        score = _segment_score(segment)
        if score is not None:
            scores.append(score)
    return scores


def _invert_verdict(verdict: str) -> str:
    """판정을 반대 영웅 시점으로 뒤집는다("매우 불리" → "매우 유리")."""
    return (
        (verdict or "")
        .replace("유리", _INVERT_PLACEHOLDER)
        .replace("불리", "유리")
        .replace(_INVERT_PLACEHOLDER, "불리")
    )


def _parse_hero_tables(text: str, heading_re: re.Pattern = _MATCHUP_HEADING_RE) -> Dict[str, Dict[str, Tuple[str, str]]]:
    """문서를 훑어 `{영웅: {상대: (판정, 이유)}}`로 만든다(직접 판정만)."""
    lines = text.splitlines()
    heading_indexes = [i for i, line in enumerate(lines) if _HERO_HEADING_RE.match(line)]

    tables: Dict[str, Dict[str, Tuple[str, str]]] = {}
    for start in heading_indexes:
        hero = resolve_hero_name(_HERO_HEADING_RE.match(lines[start]).group(1))
        if not hero:
            continue

        end = len(lines)
        for i in range(start + 1, len(lines)):
            if _ANY_H1_H2_RE.match(lines[i]):
                end = i
                break

        body = lines[start + 1:end]
        rows: Dict[str, Tuple[str, str]] = tables.setdefault(hero, {})
        for idx, line in enumerate(body):
            if not heading_re.match(line):
                continue
            table_lines = []
            for next_line in body[idx + 1:]:
                if _ANY_HEADING_RE.match(next_line):
                    break
                table_lines.append(next_line)
            for opponent_raw, verdict, reason in _parse_table_rows(table_lines):
                opponent = resolve_hero_name(opponent_raw)
                if not opponent or opponent == hero:  # 미러전은 보여줄 내용이 없다
                    continue
                rows.setdefault(opponent, (verdict, reason))

    return {hero: rows for hero, rows in tables.items() if rows}


def _bucket_for(scores: List[int]) -> Optional[str]:
    """`hard`=이 영웅이 불리한 상대, `easy`=유리한 상대, `mixed`=조건에 따라 갈림."""
    if not scores:
        return None
    if max(scores) > 0 and min(scores) < 0:
        return "mixed"
    if max(scores) > 0:
        return "easy"
    if min(scores) < 0:
        return "hard"
    return "even"


def _strength_score(scores: List[int]) -> int:
    """세기를 5점 만점으로. 약간=3, 기본=4, 매우=5."""
    average = sum(abs(score) for score in scores) / len(scores)
    return min(5, max(1, round(average) + 2))


def _question_for(hero: str, opponent: str, bucket: str) -> str:
    """상성표 항목을 눌렀을 때 챗봇에 보낼 질문.

    문구를 바꾸면 stay intent로 잡히지 않을 수 있다(chat_모듈_구조.md 참고).
    """
    josa = josa_eul_reul(opponent)
    if bucket == "easy":
        return (
            f"{hero}{josa_euro(hero)} 플레이하는데 상대 {opponent}{josa} 어떻게 상대하면 "
            f"유리하게 굴릴 수 있을까? 스킬 활용법 위주로 알려줘"
        )
    return (
        f"{hero}{josa_euro(hero)} 플레이하는데 상대 {opponent}{josa} 어떻게 상대해야 할까? "
        f"스킬 활용법 위주로 알려줘"
    )


def _build_chart() -> Dict[str, Dict[str, Any]]:
    text = read_source_markdown()
    if not text:
        logger.warning("[MATCHUP] 원본 문서를 읽지 못해 상성표가 비어 있습니다.")
        return {}

    direct = _parse_hero_tables(text)
    summaries = _load_summaries()

    chart: Dict[str, Dict[str, Any]] = {}
    for hero in HERO_TO_ROLE:
        # 직접 판정이 역방향 판정을 이긴다. 자기 표만 믿는 영웅은 그 표가 양쪽 판정의 유일한 출처다.
        merged: Dict[str, Tuple[str, str, str, Optional[str]]] = {}
        for other, rows in direct.items():
            if other == hero or hero in SELF_TABLE_ONLY_HEROES:
                continue
            row = rows.get(hero)
            if row:
                merged[other] = (_invert_verdict(row[0]), row[1], f"{other}|{hero}", other)
        for opponent, (verdict, reason) in direct.get(hero, {}).items():
            if opponent in SELF_TABLE_ONLY_HEROES:
                continue
            merged[opponent] = (verdict, reason, f"{hero}|{opponent}", None)

        # 중립은 화면에 안 띄우지만 우선 타겟 정렬에 쓴다.
        buckets: Dict[str, List[Dict[str, Any]]] = {"hard": [], "easy": [], "mixed": [], "even": []}
        for opponent, (verdict, reason, key, source_hero) in merged.items():
            scores = _verdict_scores(verdict)
            bucket = _bucket_for(scores)
            if bucket not in buckets:
                continue
            buckets[bucket].append({
                "hero": opponent,
                "role": HERO_TO_ROLE.get(opponent),
                "note": clean_note(summaries.get(key) or reason),
                "source_hero": source_hero,
                "score": _strength_score(scores),
                "signed": sum(scores) / len(scores),
                "question": _question_for(hero, opponent, bucket),
            })

        # 역할이 1순위, 그 안에서 센 상대부터(화면이 역할별로 묶어 보여준다).
        role_order = list(ROLE_HEROES)
        for items in buckets.values():
            items.sort(key=lambda item: (
                role_order.index(item["role"]) if item["role"] in role_order else 99,
                -item["score"],
                item["hero"],
            ))

        chart[hero] = {
            "hero": hero,
            "role": HERO_TO_ROLE[hero],
            "role_label": ROLE_LABELS[HERO_TO_ROLE[hero]],
            **buckets,
        }

    logger.info(
        "[MATCHUP] 상성표 로드 — 문서에 표가 있는 영웅 %d명, 요약 %d줄",
        len(direct), len(summaries),
    )
    return chart


def _has_entries(data: Dict[str, Any]) -> bool:
    return bool(data["hard"] or data["easy"] or data["mixed"])


def _get_chart() -> Dict[str, Dict[str, Any]]:
    global _chart_cache
    if _chart_cache is None:
        _chart_cache = _build_chart()
    return _chart_cache


def get_matchup_roster() -> List[Dict[str, Any]]:
    """역할별 영웅 목록(상성표 모달 첫 화면)."""
    chart = _get_chart()
    return [
        {
            "code": role,
            "label": ROLE_LABELS[role],
            "heroes": [
                {"name": hero, "has_data": _has_entries(chart[hero]) if hero in chart else False}
                for hero in heroes
            ],
        }
        for role, heroes in ROLE_HEROES.items()
    ]


def get_hero_matchup(hero: Optional[str]) -> Optional[Dict[str, Any]]:
    """영웅 한 명의 상성표. 등록되지 않은 영웅이면 None."""
    return _get_chart().get(normalize_hero_name(hero))


def rank_opponents(hero: Optional[str], opponents: List[str]) -> List[Dict[str, Any]]:
    """내 영웅 기준으로 상대 목록을 정렬한다(우선 타겟 답변용).

    내 영웅 문서의 처리 우선 원칙(최우선 > 우선 > 역할 우선 > 언급 없음 > 피함)이 먼저이고,
    같은 묶음 안에서는 상성 판정이 유리한 상대부터 둔다.
    """
    data = get_hero_matchup(hero)
    if not data:
        return []

    by_opponent = {
        item["hero"]: (bucket, item)
        for bucket in ("hard", "easy", "mixed", "even")
        for item in data[bucket]
    }

    ranked = []
    for raw in opponents:
        opponent = normalize_hero_name(raw)
        # 내 영웅과 같은 상대(미러전)도 남긴다. 상성 판정은 없고 처리 우선 원칙만 적용된다.
        if not opponent or opponent not in HERO_TO_ROLE:
            continue
        bucket, item = by_opponent.get(opponent, (None, None))
        tier = priority_tier(data["hero"], opponent)
        ranked.append({
            "tier": tier,
            "tier_label": TIER_LABELS.get(tier, ""),
            "hero": opponent,
            "role": HERO_TO_ROLE[opponent],
            "bucket": bucket,
            "signed": item["signed"] if item else None,
            "score": item["score"] if item else None,
            "note": item["note"] if item else "",
        })

    ranked.sort(key=lambda row: (row["tier"], row["signed"] is None, -(row["signed"] or 0), row["hero"]))
    return ranked


_SYNERGY_HEADING_RE = re.compile(r"^####\s*궁합\s*[—–-]\s*(탱커|딜러|힐러)\s*$")
_synergy_cache: Optional[Dict[str, Dict[str, Tuple[str, str]]]] = None


def get_synergy_note(hero: Optional[str], ally: Optional[str]) -> Optional[str]:
    """두 영웅의 궁합 표 줄(양쪽 영웅 표 모두). 연계 질문의 근거."""
    global _synergy_cache
    if _synergy_cache is None:
        text = read_source_markdown()
        _synergy_cache = _parse_hero_tables(text, _SYNERGY_HEADING_RE) if text else {}
    hero, ally = normalize_hero_name(hero), normalize_hero_name(ally)
    lines = []
    for owner, other in ((hero, ally), (ally, hero)):
        row = (_synergy_cache.get(owner) or {}).get(other)
        if row:
            verdict, reason = row
            lines.append(f"- {owner} 궁합표의 {other}: {verdict}. {clean_note(reason)}".rstrip())
    return "\n".join(lines) or None


_tank_pair_lines: Optional[List[str]] = None


def tank_pair_synergy_lines() -> List[str]:
    """탱커 영웅 궁합 표의 탱커 아군 줄(원문 그대로). 탱커가 하나뿐인 규격에서 근거에서 뺀다."""
    global _tank_pair_lines
    if _tank_pair_lines is None:
        _tank_pair_lines = []
        owner, in_table = None, False
        for line in (read_source_markdown() or "").splitlines():
            heading = _HERO_HEADING_RE.match(line)
            if heading or _ANY_H1_H2_RE.match(line):
                owner = resolve_hero_name(heading.group(1)) if heading else None
                in_table = False
            elif _ANY_HEADING_RE.match(line):
                match = _SYNERGY_HEADING_RE.match(line)
                in_table = bool(match and match.group(1) == "탱커")
            elif in_table and HERO_TO_ROLE.get(owner) == "tank":
                rows = _parse_table_rows([line])
                if rows and HERO_TO_ROLE.get(resolve_hero_name(rows[0][0])) == "tank":
                    _tank_pair_lines.append(line.strip())
    return _tank_pair_lines


def strip_tank_pair_synergy(text: str) -> str:
    """검색 조각에서 탱커끼리 궁합 줄과 그 소제목을 지운다(조각 경계에서 잘린 줄도)."""
    banned = tank_pair_synergy_lines()
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        match = _SYNERGY_HEADING_RE.match(stripped)
        if match and match.group(1) == "탱커":
            continue
        if len(stripped) > 20 and stripped.startswith("|") and any(stripped in row for row in banned):
            continue
        if len(stripped) > 20 and not stripped.startswith("|") and stripped.endswith("|") and any(row.endswith(stripped) for row in banned):
            continue
        kept.append(line)
    return "\n".join(kept)


# 궁합 표 평가 점수(첫 표기, 없으면 괄호 속 역방향 표기).
_SYNERGY_SCORES = [("매우 좋음", 3), ("매우 적합", 3), ("약간 좋음", 1), ("약간 적합", 1), ("좋음", 2), ("적합", 2)]


def _synergy_score(verdict: str) -> int:
    for part in (verdict.split("(")[0], verdict):
        head = re.split(r"\s*(?:/|↔)\s*", part.strip())[0]
        for label, score in _SYNERGY_SCORES:
            if label in head and "좋지 않" not in head and "부적합" not in head:
                return score
    return 0


def top_synergy_rows(hero: Optional[str], roles: List[str], limit: int = 8) -> Optional[str]:
    """그 영웅 궁합 표에서 roles 역할 아군 중 평가가 "좋음" 이상인 줄(같이 할 영웅을 묻는 질문의 근거)."""
    get_synergy_note(hero, hero)  # 표를 읽어 둔다
    hero = normalize_hero_name(hero)
    rows = [
        (ally, verdict, reason) for ally, (verdict, reason) in (_synergy_cache.get(hero) or {}).items()
        if HERO_TO_ROLE.get(ally) in roles and _synergy_score(verdict) >= 2
    ]
    rows.sort(key=lambda row: -_synergy_score(row[1]))
    lines = [
        f"- {ally}({ROLE_LABELS[HERO_TO_ROLE[ally]]}): {clean_note(verdict)}. {clean_note(reason)}"
        for ally, verdict, reason in rows[:limit]
    ]
    return "\n".join(lines) or None

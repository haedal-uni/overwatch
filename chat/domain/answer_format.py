"""LLM이 만든 답변 문자열을 사용자에게 내보내기 전에 다듬는 함수 모음.

마크다운 잔재/내부 표기 제거(sanitize_answer_for_user), 답변 JSON에 함께 실려
오는 후속 질문 추출, 프롬프트에 넣을 스탯 요약 문자열 포맷이 여기 있다.
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def sanitize_answer_for_user(answer: str, keep_dash_bullets: bool = False) -> str:
    if not answer:
        return ""
    sanitized = answer

    sanitized = sanitized.replace("\\n", "\n")

    sanitized = re.sub(r'\n*```json[\s\S]*?```\s*$', '', sanitized).strip()
    sanitized = re.sub(r'\n*\{\s*"answer"\s*:[\s\S]*\}\s*$', '', sanitized).strip()
    sanitized = re.sub(r'\n*"used_doc_ids"\s*:\s*\[.*?\]\s*\}?\s*$', '', sanitized).strip()

    sanitized = re.sub(r"\s*\(문서\s*\d+\)", "", sanitized)
    # 대괄호만 지우면 "에서 언급했듯이"같은 어색한 잔여 문구가 남아 뒤 어구까지 제거.
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

    # LLM이 지시를 어기고 마크다운을 섞어 쓸 때를 위한 안전망(JSON 파싱 깨짐의 흔한 원인).
    sanitized = re.sub(r"\*\*(.+?)\*\*", r"\1", sanitized)
    if keep_dash_bullets:
        # 간단 모드는 "- "를 의도된 목록 기호로 써서 보존하고, "*"만 안전망으로 제거한다.
        sanitized = re.sub(r"^\s*\*\s+", "", sanitized, flags=re.MULTILINE)
    else:
        sanitized = re.sub(r"^\s*[\*\-]\s+", "", sanitized, flags=re.MULTILINE)

    sanitized = re.sub(r"[ \t]+", " ", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    return sanitized.strip()


_DASH_LINE_RE = re.compile(r"^\s*-\s+\S")


def tighten_bullet_blocks(answer: str) -> str:
    """설명 줄("- ")이 자기 제목 줄에서 빈 줄로 떨어지는 것을 붙여준다.

    프롬프트로 "같은 항목 안에는 빈 줄을 넣지 마라"고 지시해도 LLM이 항목마다
    빈 줄을 끼워 넣어, 한 묶음이어야 할 제목+설명이 서로 다른 문단처럼 벌어지는
    일이 잦다. 빈 줄 다음에 오는 첫 내용 줄이 "- "로 시작하면 그 빈 줄을 지운다
    — 목록 항목은 앞 줄에 붙어 있어야 한 묶음으로 읽힌다. 새 묶음은 항상 제목
    줄로 시작하므로 묶음 사이의 빈 줄은 그대로 남는다.
    """
    if not answer:
        return answer

    lines = answer.split("\n")
    kept: List[str] = []
    for idx, line in enumerate(lines):
        if not line.strip():
            following = next(
                (later for later in lines[idx + 1:] if later.strip()), ""
            )
            if _DASH_LINE_RE.match(following):
                continue
        kept.append(line)
    return "\n".join(kept)


# 특전 답변의 운용 조합 제목 줄:
#   "기본 운용 (나선 추진(보조 특전, 좌클) + 전속력(주요 특전, 좌클))"
# 괄호가 중첩돼 있어 마지막 ")"까지 통째로 잡는다.
_PERK_TITLE_RE = re.compile(r"^\s*([^:：(]*운용)\s*[:：]?\s*(\(.+\))\s*(?:추천\s*⭐?)?\s*$")
# "추천 운용: 안정 운용" — 답변 끝에 따로 나오는 추천 문단.
_PERK_RECOMMEND_RE = re.compile(r"^\s*\**\s*추천\s*운용\s*[:：]\s*(.+?)\s*\**\s*$")
# 조합 목록이 끝났다는 신호(마무리 섹션/번호 목록).
_PERK_SECTION_BREAK_RE = re.compile(
    r"^\s*(?:바로\s*할\s*것|바로\s*적용할\s*것|추천\s*영웅|운영\s*핵심|운영\s*개선|\d+\.\s)"
)


def _strip_blank_edges(lines: List[str]) -> List[str]:
    result = list(lines)
    while result and not result[0].strip():
        result.pop(0)
    while result and not result[-1].strip():
        result.pop()
    return result


def _same_combo_name(left: str, right: str) -> bool:
    left_key = re.sub(r"\s+", "", left)
    right_key = re.sub(r"\s+", "", right)
    return left_key in right_key or right_key in left_key


def format_perk_answer(answer: str) -> str:
    """특전 답변의 운용 조합 부분을 정해진 모양으로 다시 조립한다.

    프롬프트로 형식을 지시해도 LLM이 매번 다르게 쓴다(설명 줄의 "- "를 빼거나,
    항목마다 빈 줄을 끼우거나, 추천 이유를 맨 아래 따로 떼어 놓는다). 구조가
    "제목 줄 + 설명 줄"로 뚜렷하니 여기서 확정적으로 맞춘다:

        기본 운용 : (나선 추진(보조 특전, 좌클) + 전속력(주요 특전, 좌클))
        - 설명 줄
        - 설명 줄

        안정 운용 : (전술 일제사격(보조 특전, 우클) + 전속력(주요 특전, 좌클)) 추천⭐
        - 설명 줄
        *추천 이유

    조합 제목을 하나도 못 찾으면 손대지 않고 빈 줄만 정리한다.
    """
    if not answer:
        return answer

    lines = [line.rstrip() for line in answer.split("\n")]

    # 1) 맨 아래 "추천 운용: OO" 문단을 떼어낸다 — 해당 조합 블록 안으로 옮긴다.
    recommended: Optional[str] = None
    reason_lines: List[str] = []
    rest: List[str] = []
    idx = 0
    while idx < len(lines):
        match = _PERK_RECOMMEND_RE.match(lines[idx])
        if not match:
            rest.append(lines[idx])
            idx += 1
            continue

        recommended = match.group(1)
        idx += 1
        while idx < len(lines):
            following = lines[idx]
            if _PERK_SECTION_BREAK_RE.match(following) or _PERK_TITLE_RE.match(following):
                break
            if following.strip():
                reason_lines.append(following.strip().lstrip("*").strip())
            idx += 1

    # 2) 조합 블록으로 나눈다.
    preamble: List[str] = []
    blocks: List[Dict[str, Any]] = []
    trailing: List[str] = []
    current: Optional[Dict[str, Any]] = None
    for line in rest:
        title = _PERK_TITLE_RE.match(line)
        if title:
            current = {"name": title.group(1).strip(), "combo": title.group(2).strip(), "desc": []}
            blocks.append(current)
            continue
        if _PERK_SECTION_BREAK_RE.match(line):
            current = None
            trailing.append(line)
            continue
        if current is not None:
            if line.strip():
                current["desc"].append(line.strip())
            continue
        (trailing if blocks else preamble).append(line)

    if not blocks:
        return tighten_bullet_blocks(answer)

    # 3) 다시 조립한다.
    rendered: List[str] = _strip_blank_edges(preamble)
    reason_used = False
    for block in blocks:
        if rendered:
            rendered.append("")
        header = f"{block['name']} : {block['combo']}"
        is_recommended = bool(recommended and _same_combo_name(block["name"], recommended))
        if is_recommended:
            header += " 추천⭐"
        rendered.append(header)
        for desc in block["desc"]:
            body = desc.lstrip("-").strip() if desc.startswith("-") else desc
            rendered.append(f"- {body}")
        if is_recommended and reason_lines:
            rendered.append(f"*{reason_lines[0]}")
            rendered.extend(reason_lines[1:])
            reason_used = True

    # 추천 조합 이름이 어느 블록과도 안 맞으면 정보를 잃지 않게 따로 남긴다.
    if recommended and not reason_used:
        rendered.append("")
        rendered.append(f"추천 운용: {recommended}")
        rendered.extend(reason_lines)

    tail = _strip_blank_edges(trailing)
    if tail:
        rendered.append("")
        rendered.extend(tail)

    return "\n".join(rendered)


# "간단히" 전용: 추천 질문을 답변 생성 호출에서 함께 받아 별도 LLM 호출을
# 생략한다(응답 속도 우선). "자세히"는 기존 방식을 유지한다.
def extract_inline_suggested_questions(parsed: Any) -> List[str]:
    if not isinstance(parsed, dict):
        return []
    raw = parsed.get("suggested_questions")
    if not isinstance(raw, list):
        return []
    questions = [str(q).strip() for q in raw if str(q).strip()]
    return questions[:3]


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

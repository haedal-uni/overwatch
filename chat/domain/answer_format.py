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

    # 줄 앞 들여쓰기는 중첩 목록의 깊이라 건드리지 않는다.
    sanitized = re.sub(
        r"(?m)^([ \t]*)(.*)$",
        lambda m: m.group(1) + re.sub(r"[ \t]+", " ", m.group(2)),
        sanitized,
    )
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    return sanitized.strip()


_DASH_LINE_RE = re.compile(r"^\s*-\s+\S")


def tighten_bullet_blocks(answer: str) -> str:
    """목록 항목을 앞 줄에 붙여 한 묶음으로 읽히게 한다.

    묶음은 항상 제목 줄로 시작하므로 묶음 사이 빈 줄은 유지된다.
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


# 조합 이름 뒤 구분자와 괄호 유무가 LLM 출력마다 달라 느슨하게 잡고,
# "+ 와 괄호가 있는 나머지"인지로 제목 줄 여부를 가른다.
_PERK_TITLE_RE = re.compile(
    r"^\s*(?:-\s+)?([^:：(]*운용)\s*[:：]?\s*(.+?)\s*(?:추천\s*⭐?)?\s*$"
)
# 답변의 줄바꿈이 화면에 그대로 보이므로 한 줄이 이보다 길면 나눈다.
_PERK_LINE_LIMIT = 60
_PERK_RECOMMEND_RE = re.compile(r"^\s*(?:-\s+)?\**\s*추천\s*운용\s*[:：]\s*(.+?)\s*\**\s*$")
# 조합 목록이 끝나는 지점(마무리 섹션/번호 목록).
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


def _strip_outer_parens(text: str) -> str:
    """조합 전체를 감싼 괄호만 벗긴다(특전 이름 안의 괄호는 남긴다)."""
    if not (text.startswith("(") and text.endswith(")")):
        return text

    depth = 0
    for idx, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                # 첫 "("의 짝이 마지막 문자여야 전체를 감싼 괄호다.
                return text[1:-1].strip() if idx == len(text) - 1 else text
    return text


def _perk_title_parts(line: str) -> Optional[Dict[str, Any]]:
    """운용 조합 제목 줄이면 {이름, 조합, 추천 여부}로 돌려주고 아니면 None."""
    match = _PERK_TITLE_RE.match(line)
    if not match:
        return None
    combo = _strip_outer_parens(match.group(2).strip())
    if "+" not in combo or "(" not in combo:
        return None
    return {
        "name": match.group(1).strip(),
        "combo": combo,
        # 조립된 답변을 다시 넣어도 결과가 같아야 한다(멱등).
        "recommended": "추천" in line[match.end(2):],
    }


def _wrap_sentences(text: str) -> List[str]:
    """긴 문단을 문장 경계에서, 그래도 길면 쉼표에서 나눈다."""
    lines: List[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= _PERK_LINE_LIMIT:
            lines.append(sentence)
            continue

        current = ""
        for chunk in re.findall(r"[^,]+,?\s*", sentence):
            chunk = chunk.strip()
            if not chunk:
                continue
            if current and len(current) + 1 + len(chunk) > _PERK_LINE_LIMIT:
                lines.append(current)
                current = chunk
            else:
                current = f"{current} {chunk}".strip()
        if current:
            lines.append(current)
    return lines


# "간단히" 스타일의 격식체 종결을 짧은 구로 바꾼다. 프롬프트로 여러 번 지시해도
# LLM이 "~합니다"로 되돌아가, 스타일 차이가 형식에만 남고 문장에는 안 남았다.
_POLITE_ENDING_RULES = [
    (re.compile(r"([가-힣]+)세요\.?$"), r"\1기"),
    (re.compile(r"([가-힣]+)십시오\.?$"), r"\1기"),
    (re.compile(r"있습니다\.?$"), "있음"),
    (re.compile(r"없습니다\.?$"), "없음"),
    (re.compile(r"좋습니다\.?$"), "좋음"),
    (re.compile(r"됩니다\.?$"), "됨"),
    (re.compile(r"[가-힣]*합니다\.?$"), lambda m: m.group(0).replace("합니다", "").rstrip(".")),
    # 명사 뒤 "입니다"만 뗀다. 앞이 한 글자면 "높입니다"류 동사라 건드리면 깨진다.
    (re.compile(r"([가-힣]{2,})입니다\.?$"), r"\1"),
]


def shorten_polite_endings(answer: str) -> str:
    """줄 끝의 격식체 종결을 짧은 구로 줄인다.

    추천 이유("*" 줄)는 판단을 설명하는 자리라 문장 그대로 둔다.
    """
    if not answer:
        return answer

    shortened: List[str] = []
    for line in answer.split("\n"):
        body = line.strip()
        if not body or body.startswith("*"):
            shortened.append(line)
            continue
        for pattern, replacement in _POLITE_ENDING_RULES:
            new_line, count = pattern.subn(replacement, line)
            if count:
                line = new_line.rstrip()
                break
        shortened.append(line)
    return "\n".join(shortened)


def format_perk_answer(answer: str) -> str:
    """특전 답변의 운용 조합 부분을 정해진 모양으로 다시 조립한다.

    형식은 프롬프트로 지시해도 LLM 출력이 매번 달라 여기서 확정한다. 조합 제목을
    하나도 못 찾으면 손대지 않는다 — 특전과 무관한 답변을 망가뜨리지 않기 위함.
    """
    if not answer:
        return answer

    lines = [line.rstrip() for line in answer.split("\n")]

    # 1) 따로 떨어진 추천 문단을 떼어낸다(해당 조합 블록 안으로 옮기려고).
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
            if _PERK_SECTION_BREAK_RE.match(following) or _perk_title_parts(following):
                break
            if following.strip():
                # 앞머리 기호는 LLM 출력마다 달라 떼고 아래에서 "*"로 통일한다.
                reason_lines.append(following.strip().lstrip("*-").strip())
            idx += 1

    # 2) 조합 블록으로 나눈다.
    preamble: List[str] = []
    blocks: List[Dict[str, Any]] = []
    trailing: List[str] = []
    current: Optional[Dict[str, Any]] = None
    for line in rest:
        title = _perk_title_parts(line)
        if title:
            current = {**title, "desc": [], "reason": [], "in_reason": False}
            blocks.append(current)
            continue
        if _PERK_SECTION_BREAK_RE.match(line):
            current = None
            trailing.append(line)
            continue
        if current is not None:
            body = line.strip()
            if not body:
                continue
            bullet = body.startswith("-")
            content = body.lstrip("-").strip() if bullet else body
            # "*" 줄은 설명이 아니라 그 조합을 고른 이유이고, 줄바꿈으로 이어진
            # 뒷줄도 같은 이유다.
            if content.startswith("*"):
                current["in_reason"] = True
                current["reason"].append(content.lstrip("*").strip())
            elif current["in_reason"] and not bullet:
                current["reason"].append(content)
            else:
                current["in_reason"] = False
                current["desc"].append(content)
            continue
        (trailing if blocks else preamble).append(line)

    if not blocks:
        return tighten_bullet_blocks(answer)

    # 3) 다시 조립한다.
    rendered: List[str] = []
    for line in _strip_blank_edges(preamble):
        rendered.extend(_wrap_sentences(line) if line.strip() else [line])
    reason_used = False
    for block in blocks:
        if rendered:
            rendered.append("")
        matches_recommendation = bool(
            recommended and _same_combo_name(block["name"], recommended)
        )
        is_recommended = matches_recommendation or block["recommended"]
        header = f"- {block['name']} : {block['combo']}"
        if is_recommended:
            header += " 추천⭐"
        rendered.append(header)
        for desc in block["desc"]:
            rendered.append(f"  - {desc}")

        reason = reason_lines if matches_recommendation else block["reason"]
        if is_recommended and reason:
            # 추천 이유는 조합의 특징이 아니라 이번 판단이라 목록에 넣지 않는다.
            rendered.append(f"*{' '.join(reason)}")
            if matches_recommendation:
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

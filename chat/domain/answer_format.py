"""답변 문자열 다듬기와 프롬프트용 스탯 요약 포맷.

각 후처리를 둔 배경은 chat_모듈_구조.md 참고.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from chat.domain.heroes import HERO_NAME_TO_CANONICAL, HERO_TO_ROLE

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
    # 대괄호만 지우면 어색한 잔여 문구가 남아 뒤 어구까지 함께 지운다.
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

    # LLM이 지시를 어기고 마크다운을 섞어 쓸 때를 위한 안전망.
    sanitized = re.sub(r"\*\*(.+?)\*\*", r"\1", sanitized)
    if keep_dash_bullets:
        # "- "가 형식의 일부인 답변은 보존하고 "*"만 제거한다.
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
    """목록 항목을 앞 줄에 붙여 한 묶음으로 읽히게 한다."""
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


# 운용 조합 제목 줄. 구분자·괄호가 LLM 출력마다 달라 느슨하게 잡는다.
_PERK_TITLE_RE = re.compile(
    r"^\s*(?:-\s+)?([^:：(]*운용)\s*[:：]?\s*(.+?)\s*(?:추천\s*⭐?)?\s*$"
)
# 도입부 한 줄이 이보다 길면 나눈다.
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
        # 이미 조립된 답변을 다시 넣어도 결과가 같아야 한다.
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


# "간단히" 스타일의 격식체 종결 → 짧은 구 치환 규칙.
_POLITE_ENDING_RULES = [
    (re.compile(r"([가-힣]+)세요\.?$"), r"\1기"),
    (re.compile(r"([가-힣]+)십시오\.?$"), r"\1기"),
    (re.compile(r"있습니다\.?$"), "있음"),
    (re.compile(r"없습니다\.?$"), "없음"),
    (re.compile(r"좋습니다\.?$"), "좋음"),
    (re.compile(r"됩니다\.?$"), "됨"),
    # 띄어 쓴 보조 용언("해야 합니다")은 지우면 문장이 끊기므로 "함"으로 줄인다.
    (re.compile(r"(?<![가-힣])합니다\.?$"), "함"),
    (re.compile(r"[가-힣]*합니다\.?$"), lambda m: m.group(0).replace("합니다", "").rstrip(".")),
    # 명사 뒤 "입니다"만 뗀다(앞이 한 글자면 동사 어미라 건드리지 않는다).
    (re.compile(r"([가-힣]{2,})입니다\.?$"), r"\1"),
]


def shorten_polite_endings(answer: str) -> str:
    """줄 끝의 격식체 종결을 짧은 구로 줄인다(추천 이유 줄은 그대로 둔다)."""
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

    조합 제목을 하나도 못 찾으면 손대지 않는다.
    """
    if not answer:
        return answer

    lines = [line.rstrip() for line in answer.split("\n")]

    # 1) 따로 떨어진 추천 문단을 떼어낸다.
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
                # 앞머리 기호를 떼고 아래에서 "*"로 통일한다.
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
            # "*" 줄은 설명이 아니라 그 조합을 고른 이유다(이어지는 줄 포함).
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
            # 추천 이유는 목록이 아니라 평문 줄로 넣는다.
            rendered.append(f"*{' '.join(reason)}")
            if matches_recommendation:
                reason_used = True

    # 추천 조합 이름이 어느 블록과도 안 맞으면 따로 남긴다.
    if recommended and not reason_used:
        rendered.append("")
        rendered.append(f"추천 운용: {recommended}")
        rendered.extend(reason_lines)

    tail = _strip_blank_edges(trailing)
    if tail:
        rendered.append("")
        rendered.extend(tail)

    return "\n".join(rendered)


# "간단히" 전용: 답변 JSON에 함께 실려 온 추천 질문을 꺼낸다.
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
        if s.get("mitigation") is not None:
            parts.append(f"경감량 {s['mitigation']}")
        lines.append(f"- {hero}: {', '.join(parts)}")
    return "\n".join(lines)


# 답변에 붙은 단축키 표기 → 표준 단축키.
_KEY_TOKENS = {
    "좌클릭": "좌클릭", "좌클": "좌클릭", "우클릭": "우클릭", "우클": "우클릭",
    "shift": "shift", "좌shift": "shift", "좌 shift": "shift", "e": "e", "q": "q",
}
_UPPER_KEYS = {"shift": "Shift", "e": "E", "q": "Q"}


def fix_skill_keys(text: str, skill_keys: Dict[str, Dict[str, str]]) -> str:
    """"스킬명(단축키)"의 단축키를 원본 문서 표({영웅: {스킬: 키}}) 기준으로 바로잡는다."""
    if not text or not skill_keys:
        return text
    name_to_keys: Dict[str, set] = {}
    for skills in skill_keys.values():
        for skill, key in skills.items():
            name_to_keys.setdefault(skill, set()).add(key)
    # 영웅마다 키가 다른 같은 이름은 어느 쪽인지 몰라 건드리지 않는다.
    name_to_key = {name: next(iter(keys)) for name, keys in name_to_keys.items() if len(keys) == 1}
    if not name_to_key:
        return text
    names = sorted(name_to_key, key=len, reverse=True)
    pattern = re.compile(
        r"(?<![가-힣A-Za-z])(" + "|".join(re.escape(n) for n in names) + r")\s*\(([^()]{1,10})\)"
    )

    def _replace(match):
        name, written = match.group(1), match.group(2).strip()
        if written.lower() not in _KEY_TOKENS:
            return match.group(0)
        correct = name_to_key[name]
        if _KEY_TOKENS[written.lower()] == correct:
            return match.group(0)
        # 원래 표기가 대문자였으면 대문자로 맞춘다.
        shown = _UPPER_KEYS.get(correct, correct) if written[:1].isupper() else correct
        return f"{name}({shown})"

    return pattern.sub(_replace, text)


# 원본 문서의 역할 표기(돌격/공격/지원)를 화면 용어(탱커/딜러/힐러)에 맞춘다.
# 괄호 표기와 "OO 영웅"만 바꾸고 "공격하다"·"지원하다" 같은 동사는 건드리지 않는다.
_DOC_ROLE_TO_LABEL = {"돌격": "탱커", "공격": "딜러", "지원": "힐러", "지원가": "힐러"}
_DOC_ROLE_PAREN_RE = re.compile(r"\(\s*(돌격|공격|지원가|지원)(?:\s*영웅)?\s*(,[^)]*)?\)")
_DOC_ROLE_HERO_WORD_RE = re.compile(r"(?<![가-힣])(돌격|공격|지원)\s*영웅")
_DOC_ROLE_GROUP_WORD_RE = re.compile(r"(?<![가-힣])(돌격|공격|지원)군(?![가-힣])")


def unify_role_labels(text: str) -> str:
    """"(돌격)"·"공격 영웅"·"지원군"·"지원가" 같은 문서 역할 표기를 탱커/딜러/힐러로 바꾼다."""
    if not text:
        return text
    text = _DOC_ROLE_PAREN_RE.sub(lambda m: f"({_DOC_ROLE_TO_LABEL[m.group(1)]}{m.group(2) or ''})", text)
    text = _DOC_ROLE_HERO_WORD_RE.sub(lambda m: f"{_DOC_ROLE_TO_LABEL[m.group(1)]} 영웅", text)
    text = _DOC_ROLE_GROUP_WORD_RE.sub(lambda m: _DOC_ROLE_TO_LABEL[m.group(1)], text)
    return text.replace("지원가", "힐러")


_ROLE_LABEL_TO_KEY = {"탱커": "tank", "딜러": "damage", "힐러": "support"}
_HERO_ROLE_LABEL_RE = re.compile(
    r"(?<![가-힣A-Za-z])("
    + "|".join(re.escape(n) for n in sorted(HERO_NAME_TO_CANONICAL, key=len, reverse=True))
    + r")\s*\((탱커|딜러|힐러)\)"
)


def drop_single_role_labels(answer: str) -> str:
    """영웅 이름 뒤 "(역할)" 표기가 모두 같은 역할이면 괄호를 지운다."""
    if not answer:
        return answer
    matches = [
        m for m in _HERO_ROLE_LABEL_RE.finditer(answer)
        if HERO_TO_ROLE.get(HERO_NAME_TO_CANONICAL[m.group(1)]) == _ROLE_LABEL_TO_KEY[m.group(2)]
    ]
    if not matches or len({_ROLE_LABEL_TO_KEY[m.group(2)] for m in matches}) > 1:
        return answer
    for m in reversed(matches):
        answer = answer[:m.start()] + m.group(1) + answer[m.end():]
    return answer


# 영웅을 추천·교체 대상으로 드는 줄의 표지.
_RECOMMEND_LINE_WORDS = ("추천", "바꾼다면", "바꾸", "바꿔", "교체", "픽", "고르", "골라", "선택")


def _is_recommendation_line(line: str, hero_surfaces: List[str]) -> bool:
    if any(w in line for w in _RECOMMEND_LINE_WORDS):
        return True
    # "영웅 — 이유", "영웅: ~", "- 영웅(딜러)" 처럼 줄 첫머리에 영웅을 세운 목록 줄.
    head = re.sub(r"^\s*(?:[-*•]|\d+[.)])?\s*", "", line)
    return any(
        head.startswith(s) and re.match(r"\s*(?:\(|—|-|:|,|$)", head[len(s):])
        for s in hero_surfaces
    )


def recommended_heroes_in_answer(answer: str) -> List[str]:
    """추천·교체 대상으로 등장한 영웅(등장 순서, 표준 이름)."""
    from chat.domain.heroes import find_all_heroes

    result: List[str] = []
    for line in (answer or "").split("\n"):
        heroes = find_all_heroes(line)
        if heroes and _is_recommendation_line(line, _surfaces_for(heroes)):
            result.extend(heroes)
    return result


def _surfaces_for(heroes: List[str]) -> List[str]:
    wanted = set(heroes)
    surfaces = [s for s, c in HERO_NAME_TO_CANONICAL.items() if c in wanted]
    return sorted(set(surfaces) | wanted, key=len, reverse=True)


def _list_head(line: str) -> str:
    return re.sub(r"^\s*(?:[-*•]|\d+[.)])?\s*", "", line)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def remove_from_recommendation_lines(answer: str, heroes: List[str]) -> str:
    """추천 줄에서 heroes(표준 이름)를 뺀다.

    그 영웅이 이끄는 목록 줄은 하위 설명 줄과 함께 지우고, 쉼표로 나열한 줄에서는 그 항목만 지운다.
    문장 속에 섞여 항목만 떼어낼 수 없으면 줄을 지운다.
    """
    from chat.domain.heroes import find_all_heroes

    removed = set(heroes)
    if not removed:
        return answer
    surfaces = _surfaces_for(list(removed))
    item_re = re.compile(
        r"(?:\s*[,/·]\s*)?(?:" + "|".join(re.escape(s) for s in surfaces) + r")"
        r"(?:\s*\([^)]*\))?(?=\s*(?:[,/·]|$))"
    )
    out: List[str] = []
    emptied_after: set = set()
    skip_deeper_than: Optional[int] = None
    for line in (answer or "").split("\n"):
        if skip_deeper_than is not None:
            if line.strip() and _indent(line) > skip_deeper_than:
                continue
            skip_deeper_than = None
        if not removed & set(find_all_heroes(line)) or not _is_recommendation_line(line, surfaces):
            out.append(line)
            continue
        head = _list_head(line)
        led_by_removed = any(
            head.startswith(s) and re.match(r"\s*(?:\(|—|-|:|,|$)", head[len(s):]) for s in surfaces
        )
        # 그 영웅이 이끄는 목록 줄은 하위 설명과 함께 지운다(여러 영웅을 쉼표로 나열한 줄은 항목만 뺀다).
        if led_by_removed and not re.match(r"[^—:\n]*,", head):
            skip_deeper_than = _indent(line)
            emptied_after.add(len(out) - 1)
            continue
        stripped = item_re.sub("", line)
        stripped = re.sub(r"^(\s*(?:[^:\n]*:\s*)?)[,/·]\s*", r"\1", stripped)
        remaining = set(find_all_heroes(stripped))
        if removed & remaining or not remaining:
            emptied_after.add(len(out) - 1)
            continue
        out.append(stripped)
    # 아래 항목이 모두 지워져 홀로 남은 "…:" 제목 줄도 지운다.
    out = [
        line for i, line in enumerate(out)
        if not (
            i in emptied_after and line.rstrip().endswith(":")
            and (i + 1 >= len(out) or not out[i + 1].strip())
        )
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n")


_RANK_LINE_RE = re.compile(r"^\s*(\d+)\s*위\s*")


def enforce_ranking_order(answer: str, ranked_heroes: List[str]) -> str:
    """"N위 영웅…" 문단들을 코드가 계산한 순위 순서로 다시 놓고 번호를 고친다.

    순위 문단 앞에서 영웅을 둘 이상 늘어놓은 요약 줄은 순서가 어긋날 수 있어 지운다.
    순위 문단의 영웅을 하나라도 못 알아보면 손대지 않는다.
    """
    from chat.domain.heroes import find_all_heroes, normalize_hero_name

    if not answer or not ranked_heroes:
        return answer
    order = {normalize_hero_name(h) or h: i for i, h in enumerate(ranked_heroes)}
    lines = answer.split("\n")
    rank_starts = [i for i, line in enumerate(lines) if _RANK_LINE_RE.match(line)]
    if len(rank_starts) < 2:
        return answer

    def _ranked_names(text):
        names = [h for h in find_all_heroes(text) if h in order]
        names += [n for n in re.findall(r"미확인 \S+?\d", text) if n in order]
        return names

    # 순위 문단 = 순위 줄 + 뒤따르는 내용 줄(빈 줄은 문단 사이 구분으로 따로 본다).
    blocks = []
    separator_blank = False
    for n, start in enumerate(rank_starts):
        limit = rank_starts[n + 1] if n + 1 < len(rank_starts) else len(lines)
        end = start + 1
        while end < limit and lines[end].strip():
            # 마지막 순위 문단 뒤의 다른 내용("다음 판에 해볼 것…")은 들여쓰기·목록 줄만 이어 붙인다.
            if n + 1 == len(rank_starts) and not lines[end].startswith((" ", "-", "\t")):
                break
            end += 1
        if n + 1 < len(rank_starts):
            between = lines[end:limit]
            if any(line.strip() for line in between):
                return answer
            separator_blank = separator_blank or bool(between)
        blocks.append((start, end))

    keys = []
    for start, _ in blocks:
        names = _ranked_names(_RANK_LINE_RE.sub("", lines[start]))
        if not names:
            return answer
        keys.append(order[names[0]])

    head = [
        line for line in lines[:rank_starts[0]]
        if len(set(_ranked_names(line))) < 2
    ]
    while head and not head[0].strip():
        head.pop(0)
    while head and not head[-1].strip():
        head.pop()
    if head:
        head.append("")

    ordered = [b for _, b in sorted(zip(keys, blocks), key=lambda kb: kb[0])]
    rebuilt: List[str] = []
    for rank, (start, end) in enumerate(ordered, 1):
        block_lines = lines[start:end]
        block_lines[0] = _RANK_LINE_RE.sub(lambda m: m.group(0).replace(m.group(1), str(rank), 1), block_lines[0])
        if rebuilt and separator_blank:
            rebuilt.append("")
        rebuilt.extend(block_lines)
    return "\n".join(head + rebuilt + lines[blocks[-1][1]:])


_QUICK_ACTIONS_HEADER_RE = re.compile(r"^(\s*)바로\s*(?:적용)?할\s*것\s*3가지", re.MULTILINE)


def relabel_quick_actions_for_review(answer: str) -> str:
    """스탯 복기 답변의 "바로 할 것 3가지" 머리줄을 "다음 판에 해볼 것 3가지"로 바꾼다."""
    if not answer:
        return answer
    return _QUICK_ACTIONS_HEADER_RE.sub(r"\1다음 판에 해볼 것 3가지", answer)

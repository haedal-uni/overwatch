"""LLM이 만든 답변 문자열을 사용자에게 내보내기 전에 다듬는 함수 모음.

마크다운 잔재/내부 표기 제거(sanitize_answer_for_user), 답변 JSON에 함께 실려
오는 후속 질문 추출, 프롬프트에 넣을 스탯 요약 문자열 포맷이 여기 있다.
"""

import logging
import re
from typing import Any, Dict, List

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

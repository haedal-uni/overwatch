"""스탯창 판 수치를 익명으로 모은다(나중에 일반 경향을 AI 답변에 쓰려는 용도).

같은 판은 한 줄만 둔다: 양 팀 모든 줄의 영웅·숫자를 해시한 값(내 줄 표시는 뺀다)이 키라서,
같은 판을 다시 올리거나 듀오가 각자 올려도 중복으로 쌓이지 않는다.
"""
import hashlib
import logging

from chat.models import MatchStat

logger = logging.getLogger(__name__)

FIELDS = ("hero", "kills", "deaths", "assists", "damage", "healing", "mitigation")


def _rank_rows(team, self_hero):
    return [
        {
            "hero": p.get("hero") or "unknown", "role_code": p.get("role"),
            "kda": {"kill": p.get("kills"), "death": p.get("deaths"), "assist": p.get("assists")},
            "damage": p.get("damage"), "healing": p.get("healing"), "mitigation": p.get("mitigation"),
            "is_me": bool(self_hero) and p.get("hero") == self_hero,
        }
        for p in team or []
    ]


def recompute_rank(stat):
    """저장된 줄로 지금 비중 기준 내 순위를 다시 구한다. 영웅을 못 알아본 줄이 있으면(역할이 없어 같은 계산이 안 된다) None."""
    from chat.domain import stat_verdicts as sv

    if not stat.self_hero or any(not p.get("hero") and not p.get("role") for p in (stat.ally or []) + (stat.enemy or [])):
        return None
    entries = sv.entries_from_scoreboard_rows(_rank_rows(stat.ally, stat.self_hero), _rank_rows(stat.enemy, ""))
    return next((r["rank"] for r in sv.score_report_rows(entries) if r["is_me"]), None)


def _row(p):
    return {**{k: p.get(k) for k in FIELDS}, "role": p.get("role")}


def stat_key(match):
    rows = [",".join(str(p.get(k)) for k in FIELDS) for p in (match.get("ally") or []) + [{}] + (match.get("enemy") or [])]
    return hashlib.sha256("|".join(rows).encode("utf-8")).hexdigest()[:40]


def has_numbers(match):
    return any(p.get("kills") is not None or p.get("damage") is not None for p in (match or {}).get("ally") or [])


def record_match(match):
    """숫자를 읽은 판만 저장하고 식별값을 돌려준다(저장에 실패해도 분석은 계속된다)."""
    if not has_numbers(match):
        return None
    key = stat_key(match)
    try:
        MatchStat.objects.get_or_create(stat_key=key, defaults={
            "ally": [_row(p) for p in match.get("ally") or []],
            "enemy": [_row(p) for p in match.get("enemy") or []],
            "self_hero": match.get("self_hero") or "",
            "self_rank": match.get("self_rank"),
        })
    except Exception:
        logger.exception("판 통계 저장 실패")
        return None
    return key


def set_result(key, result):
    """승패는 처음 받은 값만 남긴다(듀오가 같은 판을 각자 올려도 같은 팀이라 같은 값이다)."""
    if result not in ("win", "loss", "draw") or not key:
        return False
    return bool(MatchStat.objects.filter(stat_key=key, result="").update(result=result))


def unknown_flags(admin_log):
    """분석 로그 metadata에 넣을 "영웅 미확인" 표시(관리자 목록이 이 값으로 거른다)."""
    rows = [r for r in (admin_log or {}).get("hero_rows") or [] if r.get("hero") == "unknown"]
    return {"has_unknown": bool(rows), "unknown_count": len(rows)}

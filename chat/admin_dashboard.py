"""관리자 홈의 챗봇 분석 요약(질문 수·의도·불만족·오류·되묻기·응답 시간·스탯창 인식)."""

import math
from collections import Counter
from datetime import timedelta
from statistics import median

from django.core.cache import cache
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from chat.models import ChatLog

INTENT_LABELS = {
    "counter": "카운터",
    "stay": "유지·상대법",
    "swap": "교체",
    "composition": "조합",
    "performance_improve": "운영·스탯 개선",
    "situation": "위기 상황",
    "map_strategy": "맵 운영",
    "general": "일반",
    "off_topic": "잡담",
    "scoreboard_analysis": "스탯창 분석",
    "death_tip": "죽었어요",
}
DAYS = 14
CACHE_SECONDS = 60

# 다른 앱이 홈 대시보드에 붙이는 요약 칸(chat은 다른 앱을 모른다). 함수는
# {"title", "cards": [{"value", "unit", "label"}], "link", "link_label"}을 돌려준다.
EXTRA_SECTIONS = []


def register_section(fn):
    EXTRA_SECTIONS.append(fn)
    return fn


def _pct(part, whole):
    return round(100 * part / whole, 1) if whole else 0


def _daily(qs, field="id", distinct=False):
    rows = qs.annotate(d=TruncDate("created_at")).values("d").annotate(n=Count(field, distinct=distinct))
    return {r["d"]: r["n"] for r in rows}


def _top(qs, field, n=8):
    rows = qs.exclude(**{f"{field}__isnull": True}).exclude(**{field: ""}).values(field).annotate(n=Count("id")).order_by("-n")[:n]
    total = max((r["n"] for r in rows), default=0)
    return [{"label": r[field], "n": r["n"], "w": _pct(r["n"], total)} for r in rows]


def _latency(values):
    ms = sorted(v for v in values if isinstance(v, (int, float)))
    if not ms:
        return None
    p90 = ms[min(len(ms) - 1, math.ceil(len(ms) * 0.9) - 1)]
    return {"n": len(ms), "median": round(median(ms) / 1000, 1), "p90": round(p90 / 1000, 1)}


def build_dashboard():
    today = timezone.now().date()
    since = lambda days: timezone.now() - timedelta(days=days)
    logs = ChatLog.objects.all()
    user = logs.filter(role="USER")
    ai = logs.filter(role="AI")
    err = logs.filter(role="ERROR")

    week_user = user.filter(created_at__gte=since(7))
    week_ai = ai.filter(created_at__gte=since(7))
    month_user = user.filter(created_at__gte=since(30))

    # 되물은 턴: 답이 원래 질문을 pending_question으로 남긴 턴.
    pending = list(week_ai.exclude(intent="scoreboard_analysis").values_list("metadata__context_patch__pending_question", flat=True))
    asked_back = sum(1 for p in pending if p)

    elapsed = list(week_ai.exclude(intent="scoreboard_analysis").values_list("metadata__elapsed_ms", flat=True))
    scoreboard_elapsed = list(week_ai.filter(intent="scoreboard_analysis").values_list("metadata__elapsed_ms", flat=True))

    # 스탯창 인식: 30일 동안 분석한 행 중 영웅을 못 알아본 행.
    rows_total = rows_unknown = 0
    for hero_rows in ai.filter(intent="scoreboard_analysis", created_at__gte=since(30)).values_list("metadata__admin_log__hero_rows", flat=True):
        for r in hero_rows or []:
            rows_total += 1
            rows_unknown += (r or {}).get("hero") in (None, "", "unknown")

    q_day = _daily(user.filter(created_at__gte=since(DAYS)))
    s_day = _daily(user.filter(created_at__gte=since(DAYS)), "log_session_id", distinct=True)
    ai_day = _daily(ai.filter(created_at__gte=since(DAYS)))
    bad_day = _daily(ai.filter(created_at__gte=since(DAYS), is_unsatisfied=True))
    err_day = _daily(err.filter(created_at__gte=since(DAYS)))
    peak = max(q_day.values(), default=0)
    daily = []
    for i in range(DAYS - 1, -1, -1):
        d = today - timedelta(days=i)
        q = q_day.get(d, 0)
        daily.append({
            "date": d,
            "questions": q,
            "sessions": s_day.get(d, 0),
            "h": _pct(q, peak),
            "bad": _pct(bad_day.get(d, 0), ai_day.get(d, 0)),
            "err": _pct(err_day.get(d, 0), q),
        })

    intents = Counter(i or "(없음)" for i in month_user.values_list("intent", flat=True))
    total_intents = sum(intents.values())
    intent_rows = [
        {"label": INTENT_LABELS.get(k, k), "code": k, "n": n, "pct": _pct(n, total_intents)}
        for k, n in intents.most_common()
    ]

    week_q = week_user.count()
    week_ai_n = week_ai.count()
    return {
        "today": user.filter(created_at__date=today).count(),
        "week": week_q,
        "week_sessions": week_user.values("log_session_id").distinct().count(),
        "bad_rate": _pct(week_ai.filter(is_unsatisfied=True).count(), week_ai_n),
        "err_rate": _pct(err.filter(created_at__gte=since(7)).count(), week_q),
        "ask_rate": _pct(asked_back, len(pending)),
        "latency": _latency(elapsed),
        "scoreboard_latency": _latency(scoreboard_elapsed),
        "unknown_rate": _pct(rows_unknown, rows_total),
        "unknown_rows": rows_unknown,
        "rows_total": rows_total,
        "daily": daily,
        "intents": intent_rows,
        "heroes": _top(month_user, "current_hero"),
        "enemies": _top(month_user, "target_enemy"),
    }


def _build_all():
    data = build_dashboard()
    data["extra"] = [fn() for fn in EXTRA_SECTIONS]
    return data


def get_dashboard():
    return cache.get_or_set("chat.admin_dashboard", _build_all, CACHE_SECONDS)

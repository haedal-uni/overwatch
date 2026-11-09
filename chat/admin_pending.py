"""관리자 메뉴·홈에 띄우는 개수. 새 글(그 목록을 마지막으로 연 뒤 생긴 것)과 미반영(반영함 체크가 안 된 것 전부)을 따로 센다."""

from datetime import datetime, timedelta

from django.contrib import admin
from django.core.cache import cache
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

CACHE_SECONDS = 30
SEEN_SESSION_KEY = "admin_seen"

# (changelist URL 이름, 함수) — 함수는 개수만 돌려준다. chat은 다른 앱을 모르므로 game이 직접 등록한다.
SHARED = []
# 관리자마다 다른 값(마지막으로 연 시각 뒤에 생긴 것)이라 캐시하지 않는다.
PER_USER = []


def register_pending(url_name, kind=None):
    """fn은 처리 안 한 쿼리셋을 돌려준다. kind가 있으면 AdminSeen으로 미확인을 가르고, 없으면 개수만 돌려주는 함수로 본다."""
    def deco(fn):
        SHARED.append((url_name, fn, kind))
        return fn
    return deco


STATE_BADGES = {
    "new": ("새 글", "new"),
    "open": ("미반영", "todo"),
    "done": ("반영함", "done"),
}


class MarkSeenMixin:
    """목록 화면을 여는 것만으로 "새 글"을 확인한 것으로 친다(항목마다 누를 필요 없음).
    새 글 = 이 관리자가 이 목록을 마지막으로 연 뒤에 생긴 것, 미반영 = 반영함 체크가 안 된 것(열어 봤어도 계속 센다)."""

    seen_kind = ""
    _since = None

    def changelist_view(self, request, extra_context=None):
        if self.seen_kind:
            # 이번에 연 시각은 화면을 다 그린 뒤가 아니라 지금 적는다. 줄 표시(새 글)는 이전에 연 시각 기준이다.
            self._since = seen_since(request, self.seen_kind)
            mark_seen(request, self.seen_kind)
        return super().changelist_view(request, extra_context)

    @admin.display(description="처리 상태")
    def process_state(self, obj):
        from django.utils.html import format_html
        if getattr(obj, "is_resolved", False):
            key = "done"
        elif self._since is not None and getattr(obj, "created_at", None) and obj.created_at > self._since:
            key = "new"
        else:
            key = "open"
        label, css = STATE_BADGES[key]
        return format_html('<span class="fb-badge {}">{}</span>', css, label)


def register_pending_since(url_name):
    def deco(fn):
        PER_USER.append((url_name, fn))
        return fn
    return deco


def mark_seen(request, key):
    """이 목록을 지금 열었다고 세션에 적는다(다음부터 이 시각 뒤에 생긴 것만 "새 글")."""
    seen = dict(request.session.get(SEEN_SESSION_KEY) or {})
    seen[key] = timezone.now().isoformat()
    request.session[SEEN_SESSION_KEY] = seen


def seen_since(request, key, default_days=7):
    raw = (request.session.get(SEEN_SESSION_KEY) or {}).get(key)
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return timezone.now() - timedelta(days=default_days)


def _shared_counts():
    """개수만 돌려주는 등록(kind 없음)은 모든 관리자가 같은 값이라 캐시한다."""
    out = {}
    for url_name, fn, kind in SHARED:
        if kind:
            continue
        try:
            n = fn()
            out[url_name] = {"unseen": n, "open": n}
        except Exception:
            out[url_name] = {"unseen": 0, "open": 0}
    return out


def pending_counts(request):
    """{changelist URL: {"unseen": 새 글, "open": 미반영}}. 둘 다 0인 것은 뺀다."""
    counts = dict(cache.get_or_set("chat.admin_pending", _shared_counts, CACHE_SECONDS))
    for url_name, fn, kind in SHARED:
        if not kind:
            continue
        try:
            qs = fn()
            counts[url_name] = {
                "unseen": qs.filter(created_at__gt=seen_since(request, kind)).count(),
                "open": qs.count(),
            }
        except Exception:
            counts[url_name] = {"unseen": 0, "open": 0}
    for url_name, fn in PER_USER:
        try:
            n = fn(request)
        except Exception:
            n = 0
        counts[url_name] = {"unseen": n, "open": n}
    out = {}
    for url_name, c in counts.items():
        if not c["open"] and not c["unseen"]:
            continue
        try:
            out[reverse(url_name)] = c
        except NoReverseMatch:
            continue
    return out

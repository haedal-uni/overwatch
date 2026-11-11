"""PC·폰 기록 동기화: 6자리 코드로 기기를 묶고 내 기록·이벤트 횟수·저장한 대화를 합친다(숫자 기록만, 3개월 미사용은 삭제)."""

import json
import re
import secrets
from datetime import timedelta
from typing import Any, Dict, List, Optional

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from chat.models import SyncCode, SyncDevice, SyncGroup

STALE_DAYS = 90
CODE_MINUTES = 10
MAX_RECORDS = 100
MAX_RECORD_BYTES = 8_000
MAX_SAVED_CHATS = 2
MAX_SAVED_CHATS_BYTES = 300_000
MAX_SHOTS = 300
# 기기마다 더해서 합치는 이벤트 횟수(스탯창은 같은 판을 한 번만 세려고 따로 합친다).
EVENT_COUNTS = ("chat", "chat1103", "chat1104", "chat11", "cards", "cardWins", "cardPerfect")
# 연속 질문 이벤트용 날짜별 질문 수("2026-11-4" 같은 키)를 남기는 날 수.
MAX_DAYS = 20
_DAY_KEY = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")


def _day_sort(key: str):
    return tuple(int(x) for x in key.split("-"))


def _clean_days(raw: Any) -> Dict[str, int]:
    days: Dict[str, int] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(k, str) and _DAY_KEY.match(k):
                try:
                    days[k] = max(0, min(int(v or 0), 10_000))
                except (TypeError, ValueError):
                    pass
    keep = sorted(days, key=_day_sort, reverse=True)[:MAX_DAYS]
    return {k: days[k] for k in keep}
# 코드 입력 실패는 기기마다 10분에 10번까지(6자리 숫자를 맞혀 보는 것을 막는다).
MAX_LINK_FAILS = 10


def cleanup_stale(days: int = STALE_DAYS) -> int:
    """오래 안 쓴 묶음(기기·기록 포함)과 지난 코드를 지운다. 지운 묶음 수를 돌려준다."""
    now = timezone.now()
    SyncCode.objects.filter(expires_at__lt=now).delete()
    deleted, _ = SyncGroup.objects.filter(last_seen__lt=now - timedelta(days=days)).delete()
    return deleted


def _device_label(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    return "폰" if any(k in ua for k in ("iphone", "android", "mobile")) else "PC"


def device_of(device_id: str) -> Optional[SyncDevice]:
    if not device_id:
        return None
    return SyncDevice.objects.select_related("group").filter(device_id=device_id).first()


def make_code(device_id: str, user_agent: str = "") -> str:
    """이 기기의 묶음(없으면 새로 만든다)에 다른 기기를 붙일 6자리 코드를 만든다."""
    cleanup_stale()
    device = device_of(device_id)
    if device is None:
        group = SyncGroup.objects.create()
        device = SyncDevice.objects.create(device_id=device_id, group=group, label=_device_label(user_agent))
    active = set(SyncCode.objects.filter(used=False, expires_at__gte=timezone.now()).values_list("code", flat=True))
    code = f"{secrets.randbelow(1_000_000):06d}"
    while code in active:
        code = f"{secrets.randbelow(1_000_000):06d}"
    SyncCode.objects.create(code=code, group=device.group, expires_at=timezone.now() + timedelta(minutes=CODE_MINUTES))
    return code


def link(device_id: str, code: str, user_agent: str = "") -> Dict[str, Any]:
    """코드로 이 기기를 그 묶음에 붙인다. 다른 묶음에 있던 기기면 옮기고, 남은 기기가 없는 옛 묶음은 지운다."""
    fail_key = f"sync-link-fail:{device_id}"
    if (cache.get(fail_key) or 0) >= MAX_LINK_FAILS:
        return {"ok": False, "error": "코드를 너무 많이 틀렸어요. 10분 뒤에 다시 해 주세요."}
    with transaction.atomic():
        row = (
            SyncCode.objects.select_for_update()
            .filter(code=str(code).strip(), used=False, expires_at__gte=timezone.now())
            .select_related("group").first()
        )
        if row is None:
            cache.set(fail_key, (cache.get(fail_key) or 0) + 1, CODE_MINUTES * 60)
            return {"ok": False, "error": "코드가 맞지 않거나 10분이 지났어요."}
        row.used = True
        row.save(update_fields=["used"])
        device = device_of(device_id)
        old_group = None
        if device is None:
            SyncDevice.objects.create(device_id=device_id, group=row.group, label=_device_label(user_agent))
        elif device.group_id != row.group_id:
            old_group = device.group
            # 옛 묶음의 기록은 새 묶음에 합친다(다음 동기화에서 이 기기의 기록도 함께 올라온다).
            row.group.records = merge_records(row.group.records, old_group.records)
            row.group.save(update_fields=["records", "last_seen"])
            device.group = row.group
            device.save(update_fields=["group", "last_seen"])
        if old_group is not None and not old_group.devices.exists():
            old_group.delete()
    return {"ok": True}


def unlink(device_id: str, wipe: bool = False) -> None:
    """이 기기를 묶음에서 뺀다. wipe면 묶음(다른 기기 연결과 서버 기록 전부)을 지운다."""
    device = device_of(device_id)
    if device is None:
        return
    group = device.group
    if wipe:
        group.delete()
        return
    device.delete()
    if not group.devices.exists():
        group.delete()


def _clean_records(items: Any) -> List[Dict[str, Any]]:
    out = []
    for r in items if isinstance(items, list) else []:
        if not isinstance(r, dict) or not r.get("id") or not isinstance(r.get("match"), dict):
            continue
        if len(json.dumps(r, ensure_ascii=False)) > MAX_RECORD_BYTES:
            continue
        out.append(r)
    return out


def merge_records(a: Any, b: Any) -> List[Dict[str, Any]]:
    """두 기록 목록을 판 id로 합친다(같은 판이면 승패가 적힌 쪽을 남긴다). 최근 100판."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for r in _clean_records(a) + _clean_records(b):
        key = str(r["id"])
        if key not in by_id or (r.get("result") and not by_id[key].get("result")):
            by_id[key] = r
    merged = sorted(by_id.values(), key=lambda r: r.get("ts") or 0, reverse=True)
    return merged[:MAX_RECORDS]


def _clean_events(ev: Any) -> Dict[str, Any]:
    ev = ev if isinstance(ev, dict) else {}

    def count(v):
        try:
            return max(0, min(int(v or 0), 100_000))
        except (TypeError, ValueError):
            return 0

    shots = [str(s)[:40] for s in ev.get("shots") or [] if s][-MAX_SHOTS:]
    shots1104 = [str(s)[:40] for s in ev.get("shots1104") or [] if s][-MAX_SHOTS:]
    shots11 = [str(s)[:40] for s in ev.get("shots11") or [] if s][-MAX_SHOTS:]
    return {**{k: count(ev.get(k)) for k in EVENT_COUNTS}, "shots": shots, "shots1104": shots1104, "shots11": shots11, "days": _clean_days(ev.get("days"))}


def sync(device_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """이 기기의 기록을 묶음에 합치고, 묶음 전체 값을 돌려준다. 연결되지 않은 기기면 linked=False."""
    # 정리 명령을 돌리지 않아도 오래된 묶음이 남지 않게 하루 한 번 정리한다.
    if not cache.get("sync-cleanup-done"):
        cache.set("sync-cleanup-done", True, 24 * 3600)
        cleanup_stale()
    device = device_of(device_id)
    if device is None:
        return {"linked": False}
    group = device.group

    group.records = merge_records(group.records, payload.get("records"))
    saved = payload.get("saved_chats")
    try:
        saved_ts = int(payload.get("saved_chats_ts") or 0)
    except (TypeError, ValueError):
        saved_ts = 0
    if isinstance(saved, list) and saved_ts > group.saved_chats_ts:
        saved = saved[:MAX_SAVED_CHATS]
        if len(json.dumps(saved, ensure_ascii=False)) <= MAX_SAVED_CHATS_BYTES:
            group.saved_chats, group.saved_chats_ts = saved, saved_ts
    group.save()

    device.events = _clean_events(payload.get("events_self"))
    device.save(update_fields=["events", "last_seen"])

    devices = list(group.devices.all())
    shots: List[str] = []
    for d in devices:
        for s in (d.events or {}).get("shots") or []:
            if s not in shots:
                shots.append(s)
    total = {k: sum((d.events or {}).get(k, 0) for d in devices) for k in EVENT_COUNTS}
    total["shots"] = shots[-MAX_SHOTS:]
    shots1104: List[str] = []
    for d in devices:
        for s in (d.events or {}).get("shots1104") or []:
            if s not in shots1104:
                shots1104.append(s)
    total["shots1104"] = shots1104[-MAX_SHOTS:]
    shots11: List[str] = []
    for d in devices:
        for s in (d.events or {}).get("shots11") or []:
            if s not in shots11:
                shots11.append(s)
    total["shots11"] = shots11[-MAX_SHOTS:]
    days: Dict[str, int] = {}
    for d in devices:
        for k, v in _clean_days((d.events or {}).get("days")).items():
            days[k] = days.get(k, 0) + v
    total["days"] = _clean_days(days)
    return {
        "linked": True,
        "devices": [{"label": d.label or "기기", "me": d.device_id == device_id} for d in devices],
        "records": group.records,
        "saved_chats": group.saved_chats,
        "saved_chats_ts": group.saved_chats_ts,
        "events_total": total,
    }

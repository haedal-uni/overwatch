from pathlib import Path

from django.apps import apps
from django.http import FileResponse, Http404
from django.shortcuts import render


def home(request):
    services = [{
        "name": "오버워치 챗봇",
        "tone": "chat",
        "url": "/chat/",
    }]
    if apps.is_installed("game"):
        services.append({
            "name": "미니 게임",
            "tone": "game",
            "url": "/game/",
        })
    return render(request, "home.html", {"services": services, "is_staff": request.user.is_staff, **_notices(), "patch_notes": _patch_notes()})


def _notices():
    """관리자 "공지사항" 중 켜져 있고 기간 안인 것을 위쪽·아래쪽으로 나눠 준다(chat 앱이 없으면 비운다)."""
    if not apps.is_installed("chat"):
        return {"notices_top": [], "notices_bottom": [], "notices_popup": []}
    from django.db.models import Q
    from django.utils import timezone

    Notice = apps.get_model("chat", "Notice")
    now = timezone.now()
    try:
        rows = list(
            Notice.objects.filter(is_active=True)
            .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
        )
    except Exception:
        # 마이그레이션 전이라 표가 없어도 홈은 열린다.
        rows = []
    import hashlib

    return {
        "notices_top": [n for n in rows if n.position == "top"],
        "notices_bottom": [n for n in rows if n.position == "bottom"],
        # 팝업은 글이 바뀌면 다시 뜨도록 번호와 글 해시를 합쳐 키로 쓴다.
        "notices_popup": [{"key": f"{n.pk}:{hashlib.md5(n.message.encode()).hexdigest()[:8]}", "message": n.message} for n in reversed(rows) if n.position == "popup"],
    }


def transfer(request):
    return render(request, "transfer.html")


PWA_DIR = Path(__file__).resolve().parent / "pwa"
PWA_FILES = {
    "manifest.json": "application/manifest+json",
    "sw.js": "application/javascript",
    "inset.js": "application/javascript",
    "device.js": "text/javascript",
    "icon-180.png": "image/png",
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
}


def pwa_file(request, name):
    if name not in PWA_FILES:
        raise Http404
    resp = FileResponse(open(PWA_DIR / name, "rb"), content_type=PWA_FILES[name])
    resp["Cache-Control"] = "no-cache"
    return resp


def _patch_notes():
    """공개한 패치 노트(최근 40개). chat 앱이 없거나 표가 없으면 비운다."""
    if not apps.is_installed("chat"):
        return []
    try:
        PatchNote = apps.get_model("chat", "PatchNote")
        return [
            {"id": n.pk, "title": n.title, "body": n.body, "date": n.published_at.strftime("%Y.%m.%d")}
            for n in PatchNote.objects.filter(is_published=True)[:40]
        ]
    except Exception:
        return []

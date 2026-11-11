"""스탯창 일괄 올리기: 이미지를 임시로 저장해 두고 서버가 한 장씩 천천히 분석한다.

Gemini 무료 한도에 걸리지 않게 호출 사이에 간격을 두고, 한도 오류가 나면 기다렸다가 다시 시도한다.
분석이 끝난 이미지는 바로 지운다. 영웅을 못 알아본 판의 원본은 분석 쪽(analyze_scoreboard_image)이
logs/scoreboard_debug/{turn_id}/original.png로 따로 남긴다.
"""
import logging
import os
import shutil
import threading
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from chat.match_stats import record_match, set_result, unknown_flags
from chat.models import BatchImage, BatchJob

logger = logging.getLogger(__name__)

MAX_IMAGES_PER_JOB = 30
MAX_PENDING_TOTAL = 200
JOB_KEEP_DAYS = 3
STALE_PROCESSING = timedelta(minutes=10)
MAX_ATTEMPTS = 4
GAP_SECONDS = 8  # 이미지 한 장 분석이 끝난 뒤 다음 장까지 쉬는 시간
QUOTA_WAIT_SECONDS = 60
IDLE_POLLS = 24  # 일이 없는 채로 이만큼(5초씩) 지나면 일꾼 스레드를 끝낸다

EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}

_lock = threading.Lock()
_thread = None


def upload_root():
    return os.path.join(str(getattr(settings, "BASE_DIR", os.getcwd())), "logs", "batch_uploads")


def pending_total():
    return BatchImage.objects.filter(status__in=["pending", "processing"]).count()


def create_job(result, files):
    """files: [(바이트, mime_type)]. 임시 파일을 저장하고 작업을 만든다."""
    purge_old()
    job = BatchJob.objects.create(id=uuid.uuid4(), result=result)
    folder = os.path.join(upload_root(), str(job.id))
    os.makedirs(folder, exist_ok=True)
    rows = []
    for idx, (data, mime) in enumerate(files):
        path = os.path.join(folder, f"{idx}.{EXTENSIONS.get(mime, 'png')}")
        with open(path, "wb") as fh:
            fh.write(data)
        rows.append(BatchImage(job=job, idx=idx, path=path, mime_type=mime))
    BatchImage.objects.bulk_create(rows)
    ensure_worker()
    return job


def purge_old():
    """오래된 작업(이미지·파일 포함)을 지운다."""
    old = BatchJob.objects.filter(created_at__lt=timezone.now() - timedelta(days=JOB_KEEP_DAYS))
    for job in list(old):
        shutil.rmtree(os.path.join(upload_root(), str(job.id)), ignore_errors=True)
    old.delete()


def ensure_worker():
    """일꾼 스레드가 없으면 띄운다(서버를 다시 켠 뒤 상태 조회가 오면 이어서 돈다)."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_run, name="batch-scoreboard", daemon=True)
        _thread.start()


def _claim():
    """대기 중인 가장 오래된 이미지 하나를 분석 중으로 바꿔 돌려준다(다른 프로세스와 겹치지 않게 조건부 갱신)."""
    BatchImage.objects.filter(status="processing", updated_at__lt=timezone.now() - STALE_PROCESSING).update(status="pending")
    for img in BatchImage.objects.filter(status="pending").select_related("job").order_by("job__created_at", "idx")[:5]:
        if BatchImage.objects.filter(pk=img.pk, status="pending").update(status="processing"):
            img.status = "processing"
            return img
    return None


def _is_quota_error(exc):
    text = f"{type(exc).__name__} {exc}".lower()
    return any(w in text for w in ("429", "quota", "resource_exhausted", "rate limit", "exhausted"))


def _remove_file(img):
    if not img.path:
        return
    try:
        os.remove(img.path)
    except OSError:
        pass
    try:
        os.rmdir(os.path.dirname(img.path))
    except OSError:
        pass


def _finish(img, status, error="", match=None, turn_id=""):
    _remove_file(img)
    BatchImage.objects.filter(pk=img.pk).update(status=status, error=error, match=match, turn_id=turn_id, path="")


def process_one(img):
    """이미지 한 장을 분석해 결과를 저장한다. 한도 오류면 True(잠시 쉬어야 함)를 돌려준다."""
    from chat.views import save_chat_log
    from chat.vision.scoreboard import ScoreboardAnalysisError, analyze_scoreboard_image

    turn_id = str(uuid.uuid4())
    try:
        with open(img.path, "rb") as fh:
            data = fh.read()
        result = analyze_scoreboard_image(data, mime_type=img.mime_type, turn_id=turn_id, with_feedback=False)
    except ScoreboardAnalysisError:
        _finish(img, "failed", error="스탯창 화면으로 보이지 않아요")
        return False
    except Exception as exc:
        if _is_quota_error(exc) and img.attempts + 1 < MAX_ATTEMPTS:
            BatchImage.objects.filter(pk=img.pk).update(status="pending", attempts=img.attempts + 1)
            logger.warning("일괄 올리기: 한도 오류, 잠시 뒤 다시 시도 (%s)", exc)
            return True
        logger.exception("일괄 올리기 분석 오류")
        _finish(img, "failed", error="분석 중 오류가 났어요")
        return False

    match = result.get("match")
    admin_log = result.get("admin_log", {})
    try:
        save_chat_log(
            log_session_id=f"batch-{img.job_id}", turn_id=turn_id, role="AI", message=result["report"],
            intent="scoreboard_analysis", current_hero=admin_log.get("self_hero"),
            metadata={"source": "chat_scoreboard_batch", "admin_log": admin_log, **unknown_flags(admin_log)},
        )
    except Exception:
        logger.exception("일괄 올리기 로그 저장 실패")
    key = record_match(match) if match else None
    if not key:
        _finish(img, "failed", error="숫자를 읽지 못했어요", turn_id=turn_id)
        return False
    match["stat_key"] = key
    set_result(key, img.job.result)
    _finish(img, "done", match=match, turn_id=turn_id)
    return False


def _run():
    idle = 0
    try:
        while idle < IDLE_POLLS:
            close_old_connections()
            try:
                img = _claim()
            except Exception:
                logger.exception("일괄 올리기 대기열 조회 실패")
                img = None
            if img is None:
                idle += 1
                time.sleep(5)
                continue
            idle = 0
            try:
                quota = process_one(img)
            except Exception:
                logger.exception("일괄 올리기 처리 실패")
                BatchImage.objects.filter(pk=img.pk, status="processing").update(status="failed", error="분석 중 오류가 났어요")
                quota = False
            time.sleep(QUOTA_WAIT_SECONDS if quota else GAP_SECONDS)
    finally:
        close_old_connections()


def job_status(job):
    images = list(job.images.all())
    return {
        "result": job.result,
        "total": len(images),
        "done": sum(i.status == "done" for i in images),
        "failed": sum(i.status == "failed" for i in images),
        "images": [
            {"i": i.idx, "status": i.status, "error": i.error, "turn_id": i.turn_id, "match": i.match if i.status == "done" else None}
            for i in images
        ],
    }

"""관리자 페이지 설정(SiteSetting)을 읽는 곳. 요청마다 DB를 읽지 않게 잠깐 기억해 둔다."""
import logging
import time

logger = logging.getLogger(__name__)

# 다른 서버 프로세스는 이 시간 안에 새 값을 읽는다(저장한 프로세스는 바로).
CACHE_SECONDS = 30

_cache = {"value": None, "at": 0.0}


def clear_cache() -> None:
    _cache["at"] = 0.0


def meta_roster_size():
    """관리자가 정한 현재 메타 인원수. 설정이 없거나 읽지 못하면 None."""
    now = time.monotonic()
    if _cache["at"] and now - _cache["at"] < CACHE_SECONDS:
        return _cache["value"]
    from chat.models import SiteSetting

    try:
        value = SiteSetting.objects.filter(pk=1).values_list("meta_roster_size", flat=True).first()
    except Exception as exc:
        logger.warning("챗봇 설정을 읽지 못해 기본 인원수를 씁니다: %s", exc)
        value = None
    _cache.update(value=value, at=now)
    return value

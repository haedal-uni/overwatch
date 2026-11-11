"""정적 파일 주소 뒤에 파일 수정 시각(?v=)을 붙여, 배포 뒤 폰·설치 앱이 옛 CSS·JS를 계속 쓰지 않게 한다."""
import os
from functools import lru_cache

from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import StaticFilesStorage


@lru_cache(maxsize=None)
def _version(name):
    path = finders.find(name)
    return int(os.path.getmtime(path)) if path and os.path.isfile(path) else None


class VersionedStaticStorage(StaticFilesStorage):
    def url(self, name):
        url = super().url(name)
        v = _version(name)
        return f"{url}{'&' if '?' in url else '?'}v={v}" if v else url

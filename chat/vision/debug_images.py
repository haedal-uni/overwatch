"""점수판 디버그 이미지(logs/scoreboard_debug/{turn_id}/) 정리 유틸.

원래 `chat/admin.py`에만 있던 함수인데, 오래된 로그를 일괄 정리하는
`python manage.py cleanup_chatlogs`도 같은 삭제 규칙(경로 검증 + 권한 실패
보고)을 그대로 써야 해서 중립 모듈로 옮겼다. admin은 여기서 import한다.
"""

import logging
import os
import re
import shutil

from django.conf import settings

logger = logging.getLogger(__name__)

# 경로 조작 방지용 turn_id 화이트리스트(UUID 형태만 허용).
SCOREBOARD_DEBUG_TURN_ID_RE = re.compile(r"[A-Za-z0-9\-]+")


def scoreboard_debug_root() -> str:
    base_dir = str(getattr(settings, "BASE_DIR", os.getcwd()))
    return os.path.normpath(os.path.join(base_dir, "logs", "scoreboard_debug"))


def delete_scoreboard_debug_dirs(turn_ids):
    """주어진 turn_id들의 디버그 이미지 폴더를 지운다.

    대부분의 turn_id는 일반 채팅이라 폴더가 없으며 이 경우는 건너뛴다. 삭제
    실패(주로 서버 파일 권한 문제) turn_id 목록을 반환해 호출자가 관리자에게
    알릴 수 있게 한다 — `ignore_errors=True`로 조용히 삼키면 디스크에 고아
    폴더가 쌓이는 걸 아무도 모른다.
    """
    debug_root = scoreboard_debug_root()
    failed_turn_ids = []

    for turn_id in turn_ids:
        if not turn_id or not SCOREBOARD_DEBUG_TURN_ID_RE.fullmatch(turn_id):
            continue
        target = os.path.normpath(os.path.join(debug_root, turn_id))
        if os.path.commonpath([debug_root, target]) != debug_root:
            continue
        if not os.path.exists(target):
            continue
        try:
            shutil.rmtree(target)
        except OSError:
            logger.warning(
                "[SCOREBOARD] 디버그 폴더 삭제 실패(권한 문제 의심): %s", target, exc_info=True
            )
            failed_turn_ids.append(turn_id)

    return failed_turn_ids


def list_debug_turn_ids():
    """디스크에 남아 있는 디버그 폴더의 turn_id 목록."""
    debug_root = scoreboard_debug_root()
    if not os.path.isdir(debug_root):
        return []
    return [
        name for name in os.listdir(debug_root)
        if os.path.isdir(os.path.join(debug_root, name))
    ]

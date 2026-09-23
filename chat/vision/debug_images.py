"""스탯창 디버그 이미지(logs/scoreboard_debug/{turn_id}/) 정리 유틸.

admin의 삭제 액션과 cleanup_chatlogs 명령이 함께 쓴다.
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

    삭제에 실패한 turn_id 목록을 돌려준다(조용히 삼키지 않는다).
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

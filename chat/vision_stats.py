import base64
import glob
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .chatbot_graph import ROLE_HEROES, call_llm_text, normalize_hero_name, safe_json_loads
from .chatbot_service import get_chatbot_components

logger = logging.getLogger(__name__)

# False면 Gemini로 하는 숫자 인식과 코치/개인 피드백 생성을 건너뛴다 —
# get_chatbot_components()도 호출하지 않아 CV 인식 로직만 테스트할 때 더
# 빠르다. 꺼진 동안 표의 수치 칸은 "확인 필요", 피드백은 고정 안내 문구로
# 나간다. 다시 끄고 켤 때는 benchmark_matching.py로 회귀 여부를 확인할 것.
ENABLE_GEMINI_STATS_AND_FEEDBACK = True

HERO_ICON_DIR = os.path.join(os.path.dirname(__file__), "hero_icons")

SCOREBOARD_DEBUG_DIR_NAME = "scoreboard_debug"

PLAYERS_PER_TEAM = 5  # 오버워치2 5vs5 기준 — 인식 성공 여부와 무관하게 항상 5슬롯을 만든다.

# 행 순서는 항상 고정되어 있다고 전제한다(1행 탱커, 2~3행 딜러, 4~5행 힐러).
# ROW_ROLES는 사용자 표시용(한글 라벨), ROW_ROLE_CODES는 관리자 로그 + 영웅
# 아이콘 역할 제한용(tank/damage/support, 다른 곳의 ROLE_HEROES/ROLE_LABELS
# 키와 동일한 코드 체계)이다.
ROW_ROLES = ["탱커", "딜러", "딜러", "힐러", "힐러"]
ROW_ROLE_CODES = ["tank", "damage", "damage", "support", "support"]

HERO_ICON_METHOD_LABEL = "hero_icons 폴더 유사도 매칭 (OpenCV 템플릿 매칭, 역할별 후보 제한)"

# 행의 고정 역할에 해당하는 영웅 이름 집합. chatbot_graph.py의 ROLE_HEROES를
# 그대로 재사용해 영웅 별칭/표기 목록이 두 파일에서 어긋나지 않게 한다.
ROLE_HERO_NAME_SETS: Dict[str, set] = {
    role: {normalize_hero_name(h) or h for h in heroes}
    for role, heroes in ROLE_HEROES.items()
}

# --- HSV 기준 팀 배경색 판정 (OpenCV의 H는 0~179 스케일) ---
# 카메라로 찍은 사진은 채도/노출 차이로 상대팀(빨강)이 마젠타·크림슨에 가까운
# hue로도 나타날 수 있어 RED_HUE_RANGES 하한을 낮춰뒀다.
BLUE_HUE_RANGE = (95, 135)
RED_HUE_RANGES = [(0, 10), (115, 179)]
TEAM_COLOR_MIN_SATURATION = 60

# 팀별 밝기 차이를 반영한 HSV 명도(V) 하한값. 같은 사진 안에서도 파란팀
# 패널은 밝게, 빨간팀 패널은 상대적으로 어둡게 찍히는 경향이 있어 분리했다.
BLUE_TEAM_COLOR_MIN_VALUE = 160
RED_TEAM_COLOR_MIN_VALUE = 60

# 한쪽 팀만 검출됐을 때, 또는 두 팀의 행 높이가 서로 비정상적으로 다를 때
# 반대 팀/작은 쪽 팀을 인접 위치에서 재탐색할 때만 쓰는 완화 기준값.
RELAXED_TEAM_COLOR_MIN_SATURATION = 25
RELAXED_TEAM_COLOR_MIN_VALUE = 30

# 두 팀의 expected_row_height는 같은 점수판 안에서 비슷해야 정상이다. 본인
# 강조 행처럼 배경이 유독 밝은 행만 파란팀 명도 임계값을 통과하면, contour가
# 강조 행 1개 크기로만 잡혀 종횡비/최소 크기 검증은 통과해버릴 수 있다. 한쪽
# 팀의 expected_row_height가 반대쪽보다 이 비율 이상 작으면, 그 팀만 완화된
# 색 기준으로 인접 위치를 다시 탐색한다(_relaxed_search_adjacent 재사용).
ROW_HEIGHT_MISMATCH_RATIO = 0.4

# 행 높이 불일치 재탐색 전용 명도 하한. RELAXED_TEAM_COLOR_MIN_VALUE(30)까지
# 낮추면 배경의 다른 UI 요소까지 하나의 영역으로 붙어버려 오히려 잘못된
# 후보를 고르게 된다 — 일반(비강조) 행 배경의 명도보다 살짝 낮은 수준까지만
# 완화해 강조 행+일반 행 5개를 하나의 contour로 합치되 VS 구분선/배경은
# 제외한다.
BLUE_ROW_HEIGHT_RETRY_MIN_VALUE = 120

# 이미지 높이 대비 expected_row_height 비율이 이보다 작으면 "일부 행만 잡힌"
# 것으로 의심한다. ROW_HEIGHT_MISMATCH_RATIO의 교차 비교는 반대 팀도 유효해야
# 작동하므로, 반대 팀이 아예 검출되지 않은 경우까지 구제하려면 이미지 높이
# 대비 절대 비율 기준이 별도로 필요하다 — 반대 팀 유무와 무관하게 자체
# 재탐색을 트리거한다.
EXPECTED_ROW_HEIGHT_MIN_TRUST_RATIO = 0.045

# 상대팀(빨강)이 아예 검출되지 않을 때(team_box=None) 쓰는 재탐색 기준. 이미
# 확정된 아군 team_box의 x범위 안에서, 아군 바로 아래부터 행별 마스크
# 커버리지가 이 threshold 이상으로 견고하게 이어지는 가장 긴 연속 구간을
# 찾으면 배경 노이즈/성긴 브리지 픽셀은 걸러지고 진짜 5행 패널만 남는다.
# 아군 x범위 밖의 배경 오염과는 애초에 무관하다.
SOLID_ROW_COVERAGE_THRESHOLD = 0.5
# 찾은 구간이 최소 이 배수(아군 행 높이 기준) 이상이어야 "5행 패널"로
# 인정한다 — 우연한 작은 조각을 상대팀으로 오인하지 않기 위한 안전장치.
ENEMY_MASKED_RETRY_MIN_ROW_MULT = 3.0

# 팀색 마스크에 2차원 morphology close를 적용해 아이콘/닉네임 글자, 본인 강조
# 행처럼 채도가 낮아지는 부분 등 "내부의 작은 구멍"만 메운다. 커널 크기는
# 이미지 크기 비례라 해상도가 달라져도 동일하게 동작한다. 값을 너무 키우면
# 배경 노이즈까지 패널과 이어붙어 하나의 거대한 영역으로 합쳐진다.
TEAM_MASK_CLOSE_KERNEL_HEIGHT_RATIO = 0.01
TEAM_MASK_CLOSE_KERNEL_WIDTH_RATIO = 0.01

# 팀 패널 후보(contour bounding box) 검증 기준 — 전부 이미지 크기 대비 비율.
MIN_TEAM_BLOCK_WIDTH_RATIO = 0.08
MIN_TEAM_BLOCK_HEIGHT_RATIO = 0.05
CANDIDATE_MAX_HEIGHT_RATIO = 0.6   # 이보다 크면 배경 오검출로 의심한다.
CANDIDATE_MAX_AREA_RATIO = 0.5     # 화면 면적의 이 비율보다 크면 배경으로 의심한다.
CANDIDATE_MIN_ASPECT_RATIO = 2.0   # 폭/높이가 이보다 작으면 "가로로 긴 패널"이 아니다.

# --- 1단계(coarse) 전용 상수 — 전체화면 캡처 대응 ---
# 위 MIN_TEAM_BLOCK_*_RATIO/CANDIDATE_MAX_*_RATIO 등은 전부 "이미지 전체
# 크기 대비" 비율이라, 전체화면 캡처처럼 점수판이 화면 일부만 차지하면
# 통과하지 못한다. 정밀 파이프라인(_compute_team_layout)의 임계값 자체는
# 건드리지 않고, 그 앞에 완화된 색 조건으로 점수판 위치만 대략 찾아
# sub-image로 잘라내는 1단계를 둔다(_compute_team_layout_with_coarse_crop).
COARSE_MIN_AREA_RATIO = 0.001  # 이보다 작은 연결 영역은 노이즈로 무시하고 후보에서 제외한다.
# 완화된 색 조건으로 찾은 파란+빨간 bounding box에 상하좌우로 붙이는 여유
# 마진(그 bounding box의 폭/높이 대비 비율). 너무 작으면 완화 조건에도 안
# 걸리는 헤더/맨 위·아래 행 가장자리가 sub-image 밖으로 잘려나갈 수 있고,
# 너무 크면 sub-image 안에서 점수판이 차지하는 비중이 다시 작아져 coarse
# 단계를 두는 의미가 옅어진다.
COARSE_CROP_MARGIN_RATIO = 0.15

# 파란/빨간 후보 쌍 평가 기준 — 전부 실패하면 그 쌍은 후보에서 제외한다.
PAIR_MIN_X_OVERLAP_RATIO = 0.5
PAIR_MAX_WIDTH_DIFF_RATIO = 0.35
PAIR_MAX_HEIGHT_DIFF_RATIO = 0.5
PAIR_MAX_LEFT_X_DIFF_RATIO = 0.25
PAIR_MAX_CENTER_X_DIFF_RATIO = 0.25
PAIR_MAX_VERTICAL_GAP_RATIO = 0.3       # (red.y0 - blue.y1) / image_height 상한.
PAIR_MAX_VERTICAL_OVERLAP_RATIO = 0.15  # 위 값이 음수(겹침)일 때 허용하는 최대 겹침 비율.

# team_box 검증 — 헤더 제외 후 예상 행 높이가 비정상적이면 거부한다.
MIN_ROW_HEIGHT_RATIO = 0.015
MAX_ROW_HEIGHT_RATIO = 0.25
ROW_HEIGHT_TOLERANCE_PX = 2  # 5등분 결과 행 높이 차이 허용 오차(반올림 오차 수준).

# 헤더(칼럼 제목 바) 처리. 상대팀(빨강) 패널은 TAB 점수판 구조상 칼럼 제목을
# 반복하지 않으므로 헤더가 없다고 못박는다. 아군(파랑) 패널은 그 헤더가
# 색상 기반 contour 검출 결과에 포함되는지가 헤더 배경색에 따라 사진마다
# 달라 고정 비율로 판정할 수 없다 — 실제로 헤더가 team_box 안에 남아있는지를
# 이미지에서 직접 판별한다(_detect_ally_header_height).
ENEMY_HEADER_HEIGHT_RATIO = 0.0

# 헤더 존재 여부 판별: team_box 맨 위 1행 높이만큼과 맨 아래(row5, 항상 실제
# 플레이어 행) 1행 높이만큼의 평균 채도(HSV S)를 비교한다. 헤더가 남아있으면
# 칼럼 제목 바는 일반 행 배경보다 채도가 뚜렷하게 낮고, 이미 제외된 경우엔
# 위/아래 둘 다 실제 행 배경이라 채도가 비슷하다.
HEADER_DETECT_SATURATION_DIFF_THRESHOLD = 35
# 위/아래 채도를 샘플링할 밴드 높이(team_box_height 비율). 실제 헤더 유무와
# 무관하게 "행 하나 높이 정도"를 표본으로 삼는다.
HEADER_DETECT_SAMPLE_BAND_RATIO = 1 / 6

# hero crop은 row_box 그대로 쓰지 않고 세로 중앙부만 쓴다 — 위/아래 경계는
# 옆 행과의 정렬 오차에 가장 취약한 영역이라, 일부 제외하면 그런 오차가
# 있어도 옆 행 픽셀이 섞일 여지가 줄어든다. 너무 크게 잡으면 얼굴 아랫부분이
# 잘리는 초상화 포즈가 있어 절충한 값이다.
HERO_CROP_VERTICAL_TRIM = 0.08

# 영웅 아이콘은 역할 아이콘 바로 다음(team_box 왼쪽 끝에서 얼마 떨어지지 않은
# 곳)에 있다. hero_crop_relative_x = (hero_crop_box.x0 - team_box.x0) /
# team_box_width가 이 값보다 크면 좌표 계산 자체가 잘못된 것으로 보고 매칭을
# 시도하지 않는다.
HERO_CROP_MAX_RELATIVE_X = 0.2

# 역할 아이콘 폭 대비 영웅 아이콘의 x 오프셋/크기 비율(행 높이 기준).
ROLE_ICON_WIDTH_RATIO = 0.65
HERO_ICON_SIZE_RATIO = 1.15
HERO_ICON_X_OFFSET_RATIO = 0.0

ICON_TEMPLATE_SIZE = (64, 64)
CLAHE_CLIP_LIMIT = 2.0
CLAHE_TILE_GRID_SIZE = (8, 8)
PREPROCESS_BLUR_KERNEL = (3, 3)
EDGE_CANNY_LOW = 50
EDGE_CANNY_HIGH = 150

FLAT_REGION_STD_MIN = 6.0  # 이보다 낮으면(사실상 단색) 매칭 자체를 시도하지 않는다.
MIN_HERO_CROP_PIXELS = 6   # 가로/세로가 이보다 작으면 무의미한 crop으로 본다.
HERO_CROP_ASPECT_MIN = 0.4
HERO_CROP_ASPECT_MAX = 2.5

# 전체화면 캡처처럼 점수판 패널의 절대 픽셀 크기 자체가 작으면 hero_crop_box의
# 네이티브 크기가 매우 작아질 수 있다. cv2.resize의 기본 보간(INTER_LINEAR)은
# 이런 작은 원본을 크게 늘릴 때 블록/흐림이 두드러지므로, 네이티브 크기의
# 작은 쪽 변이 이 값 미만이면 _match_hero_icon에 넘기기 전에 먼저
# cv2.INTER_CUBIC(확대에 더 적합한 보간)으로 업스케일한다.
MIN_ICON_CROP_FOR_UPSCALE = 40

# 색상 히스토그램 1차 필터(HSV Hue+Saturation 2차원 히스토그램,
# cv2.compareHist HISTCMP_CORREL) — 역할로 좁힌 후보 중 색상이 가장 비슷한
# 상위 HISTOGRAM_PREFILTER_TOP_K명만 최종 비교 대상으로 남긴다.
#
# ENABLE_COLOR_HISTOGRAM_PREFILTER=False(기본값): TAB 점수판의 영웅 초상화에는
# 팀 색(아군=파랑/상대=빨강) 틴트가 오버레이되는데, hero_icons/ 템플릿은 틴트
# 없는 원본이라 크롭의 HSV 색상이 캐릭터 고유색이 아니라 팀 색 위주로 나온다.
# 그 결과 그레이스케일로는 정답인 영웅이 색상 유사도로는 낮게 나와 하드 컷
# (top-K) 밖으로 밀려날 수 있어, 최종 순위는 role_scores(색상 미반영)를 그대로
# 쓴다. 히스토그램 계산 자체(_hsv_hist, color_shortlist)는 항상 계산해
# admin_log에 진단용으로 남기므로, 팀 틴트 문제가 해결되면 플래그만 켜서
# 다시 비교할 수 있다.
ENABLE_COLOR_HISTOGRAM_PREFILTER = False
HISTOGRAM_HUE_BINS = 30
HISTOGRAM_SAT_BINS = 32
HISTOGRAM_PREFILTER_TOP_K = 8

# 영웅 인식 확정 기준 — best_score만 보지 않고 1위/2위 차이도 함께 본다.
# 역할 제한은 후보를 좁힐 뿐 확정하는 것은 아니므로 이 기준은 역할 제한
# 여부와 무관하게 항상 적용된다.
BEST_SCORE_MIN_THRESHOLD = 0.65
MIN_SCORE_GAP = 0.08
HIGH_CONFIDENCE_THRESHOLD = 0.78
TOP_MATCHES_COUNT = 3

SELF_ROW_BRIGHTNESS_MARGIN = 8
HIGHLIGHT_BORDER_MIN = 0.15
HIGHLIGHT_BORDER_MARGIN = 0.05

LOW_HERO_RECOGNITION_RATIO = 0.5


class ScoreboardAnalysisError(Exception):
    pass


def _fmt_num(value: Any) -> str:
    return str(value) if value is not None else "확인 필요"


# ============================================================
# 0단계: 공용 유틸 (지연 import, 이미지 IO, 디버그 저장)
# ============================================================

def _cv2_np():
    """cv2/numpy는 이 모듈이 import되는 시점이 아니라 실제로 분석을 요청받았을
    때만 불러온다 — 설치 문제가 있어도 챗봇의 나머지 기능이 죽지 않게 하기 위함."""
    import cv2
    import numpy as np
    return cv2, np


def _imread_unicode(cv2, np, path: str):
    """cv2.imread(path)는 Windows에서 비-ASCII 경로를 조용히 실패시킨다
    (hero_icons/ 파일명이 한글이라 필요). np.fromfile + cv2.imdecode로 우회한다."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _imwrite_unicode(cv2, path: str, image) -> bool:
    """cv2.imwrite도 imread와 동일하게 Windows 비-ASCII 경로에서 조용히
    실패할 수 있어 cv2.imencode + tofile()로 우회한다."""
    try:
        ext = os.path.splitext(path)[1] or ".png"
        ok, buf = cv2.imencode(ext, image)
        if not ok:
            return False
        buf.tofile(path)
        return True
    except Exception:
        logger.warning("[SCOREBOARD] 디버그 이미지 저장 실패: %s", path)
        return False


def _scoreboard_debug_dir(turn_id: str) -> str:
    from django.conf import settings
    base_dir = str(getattr(settings, "BASE_DIR", os.getcwd()))
    return os.path.join(base_dir, "logs", SCOREBOARD_DEBUG_DIR_NAME, turn_id)


def _save_debug_images(
    cv2, turn_id: str,
    ally_row_crops: List[Optional[Any]], ally_hero_crops: List[Optional[Any]],
    enemy_row_crops: List[Optional[Any]], enemy_hero_crops: List[Optional[Any]],
) -> Dict[str, str]:
    """turn_id별 디버그 폴더에 행별 row crop, hero crop만 저장한다. 원본
    이미지/1단계(coarse) sub-image는 저장하지 않는다 — 인식 문제 진단에는
    행별 crop만으로 충분하고, 원본까지 남기면 디스크 사용량과 개인정보
    보관 범위만 늘어난다. 저장 실패(디스크 권한 등)는 예외를 삼켜 분석
    자체가 죽지 않게 한다."""
    debug_dir = _scoreboard_debug_dir(turn_id)
    try:
        os.makedirs(debug_dir, exist_ok=True)
    except Exception:
        logger.warning("[SCOREBOARD] 디버그 폴더 생성 실패: %s", debug_dir)
        return {}

    paths: Dict[str, str] = {}

    def _rel(filename: str) -> str:
        return f"logs/{SCOREBOARD_DEBUG_DIR_NAME}/{turn_id}/{filename}"

    sources = (
        ("ally", "row", ally_row_crops), ("ally", "hero", ally_hero_crops),
        ("enemy", "row", enemy_row_crops), ("enemy", "hero", enemy_hero_crops),
    )
    for team, kind, crops in sources:
        for i, crop in enumerate(crops):
            if crop is None or crop.size == 0:
                continue
            filename = f"{team}_row_{i + 1}_{kind}_crop.png"
            if _imwrite_unicode(cv2, os.path.join(debug_dir, filename), crop):
                paths[f"{team}_{i + 1}_{kind}"] = _rel(filename)

    return paths


# ============================================================
# 1단계: 영웅 아이콘 전처리 / 템플릿 로딩 / 매칭
# ============================================================

def _upscale_icon_if_small(cv2, np, icon_region_bgr) -> Tuple[Any, bool, Optional[Tuple[int, int]]]:
    """작은 쪽 변이 MIN_ICON_CROP_FOR_UPSCALE 미만이면 cv2.INTER_CUBIC으로
    업스케일해 반환한다. 이미 충분히 크면 그대로 반환한다(다운스케일은
    다루지 않는다). (이미지, 업스케일 여부, 업스케일 전 native 크기)를 반환한다."""
    if icon_region_bgr is None or icon_region_bgr.size == 0:
        return icon_region_bgr, False, None
    h, w = icon_region_bgr.shape[:2]
    native_size = (w, h)
    if min(h, w) >= MIN_ICON_CROP_FOR_UPSCALE or h == 0 or w == 0:
        return icon_region_bgr, False, native_size

    scale = MIN_ICON_CROP_FOR_UPSCALE / min(h, w)
    new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    upscaled = cv2.resize(icon_region_bgr, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
    return upscaled, True, native_size


def _preprocess_icon_variants(cv2, gray_img) -> Dict[str, Any]:
    """템플릿과 crop이 동일하게 거치는 전처리 3종(raw/blurred/clahe)을 만든다
    — 각각 유사도를 비교해 최댓값을 최종 점수로 쓴다(_match_hero_icon). edge는
    최종 점수에는 쓰지 않고 진단용으로만 별도 계산한다."""
    resized = cv2.resize(gray_img, ICON_TEMPLATE_SIZE)
    blurred = cv2.GaussianBlur(resized, PREPROCESS_BLUR_KERNEL, 0)
    clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP_LIMIT, tileGridSize=CLAHE_TILE_GRID_SIZE)
    clahe_img = clahe.apply(resized)
    edge = cv2.Canny(cv2.GaussianBlur(clahe_img, PREPROCESS_BLUR_KERNEL, 0), EDGE_CANNY_LOW, EDGE_CANNY_HIGH)
    return {"raw": resized, "blurred": blurred, "clahe": clahe_img, "edge": edge}


def _hsv_hist(cv2, bgr_img):
    """H·S 2차원 컬러 히스토그램(정규화). 그레이스케일 상관계수가 놓치는
    캐릭터별 배색 차이를 색상 히스토그램 유사도(1차 필터용)로 보완한다."""
    hsv = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [HISTOGRAM_HUE_BINS, HISTOGRAM_SAT_BINS], [0, 180, 0, 256])
    cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
    return hist


def _load_hero_icon_templates(cv2, np) -> Dict[str, List[Dict[str, Any]]]:
    """hero_icons/ 폴더를 영웅 이름 -> 전처리된 참조 이미지 "리스트"로
    불러온다. 파일명이 "{영웅명}.png" 또는 "{영웅명}__아무개.png"(예:
    "모이라__promo.png") 형태면 모두 같은 영웅의 참조 이미지로 모은다 —
    영웅당 여러 참조 이미지를 두면 매칭 시점(_match_hero_icon)에 그중 가장
    점수가 높은 것을 채택할 수 있어, 조건(구도/조명/화질)이 다른 스크린샷에도
    더 안정적으로 대응한다. 색상 히스토그램(hist)도 함께 미리 계산해둔다
    (_match_hero_icon의 1차 필터용, 기본은 비활성화)."""
    grouped_paths: Dict[str, List[str]] = {}
    for path in glob.glob(os.path.join(HERO_ICON_DIR, "*")):
        if not os.path.isfile(path):
            continue
        raw_name = os.path.splitext(os.path.basename(path))[0]
        base_name = re.sub(r"__.*$", "", raw_name)  # "모이라__promo" -> "모이라"
        name = normalize_hero_name(base_name) or base_name
        grouped_paths.setdefault(name, []).append(path)

    templates: Dict[str, List[Dict[str, Any]]] = {}
    for name, paths in grouped_paths.items():
        variants: List[Dict[str, Any]] = []
        for path in sorted(paths):  # 정렬해 매 실행마다 순서가 안정적이게 함.
            img = _imread_unicode(cv2, np, path)
            if img is None:
                continue
            img_resized = cv2.resize(img, ICON_TEMPLATE_SIZE)
            gray = cv2.cvtColor(img_resized, cv2.COLOR_BGR2GRAY)
            preprocessed = _preprocess_icon_variants(cv2, gray)
            variants.append({
                **preprocessed, "hist": _hsv_hist(cv2, img_resized),
                "source": f"hero_icons/{os.path.basename(path)}",
            })
        if variants:
            templates[name] = variants
    if not templates:
        logger.warning(
            "[SCOREBOARD] hero_icons 폴더(%s)에 템플릿 이미지가 없어 영웅 인식이 "
            "항상 'unknown'으로 처리됩니다.", HERO_ICON_DIR,
        )
    return templates


def _empty_hero_result(role_code: Optional[str], reason: Optional[str] = None) -> Dict[str, Any]:
    """영웅 인식이 아예 시도되지 못했을 때 쓰는 공용 기본값."""
    return {
        "hero": "unknown", "score": 0.0, "second_score": 0.0, "source": None,
        "reason": reason, "confidence_label": "낮음", "top_matches": [],
        "role_code": role_code,
        "pre_role_top_matches": [], "post_role_top_matches": [],
        "pre_role_best_score": 0.0, "post_role_best_score": 0.0,
        "rank1_hero": None, "rank2_hero": None,
        "raw_gray_score": 0.0, "blurred_gray_score": 0.0, "clahe_gray_score": 0.0,
        "edge_score": 0.0, "baseline_score": 0.0, "final_score": 0.0,
        "template_path": None,
        "crop_size_before": None, "crop_size_after": list(ICON_TEMPLATE_SIZE),
        "role_fallback_used": False,
        "color_shortlist": [],
        "color_prefilter_applied": False,
        "upscaled": False,
        "native_size": None,
    }


def _match_hero_icon(
    cv2, np, search_region_bgr, templates: Dict[str, List[Dict[str, Any]]], role_code: Optional[str],
) -> Dict[str, Any]:
    """search_region_bgr을 role_code(그 행의 고정 역할)에 해당하는 영웅
    템플릿만 최종 비교 후보로 삼아 인식한다. 최종 순위는 role_scores 기준
    best_score/1·2위 차이로 확정한다(각 템플릿의 raw/blurred/clahe 유사도 중
    최댓값). 색상 히스토그램 유사도는 항상 계산해 color_shortlist로 반환하되,
    ENABLE_COLOR_HISTOGRAM_PREFILTER가 True일 때만 실제 후보 축소에 쓴다
    (기본 False — 팀 색 틴트 때문에 색상 필터가 정답을 걸러낼 수 있다)."""
    empty = _empty_hero_result(role_code)

    if not templates:
        return {**empty, "reason": "hero_icons 템플릿 없음(폴더가 비어있거나 로드 실패)"}
    if search_region_bgr is None or search_region_bgr.size == 0:
        return {**empty, "reason": "crop 영역 오류 의심(인식 영역 없음)"}

    region_h, region_w = search_region_bgr.shape[:2]
    empty = {**empty, "crop_size_before": [region_w, region_h]}
    if region_h < MIN_HERO_CROP_PIXELS or region_w < MIN_HERO_CROP_PIXELS:
        return {**empty, "reason": "crop 영역 오류 의심(인식 영역이 너무 작음)"}

    aspect = region_w / region_h
    if aspect < HERO_CROP_ASPECT_MIN or aspect > HERO_CROP_ASPECT_MAX:
        return {**empty, "reason": "crop 영역 오류 의심(가로세로 비율이 비정상적)"}

    gray_region = cv2.cvtColor(search_region_bgr, cv2.COLOR_BGR2GRAY)
    if float(gray_region.std()) < FLAT_REGION_STD_MIN:
        return {**empty, "reason": "crop 영역 오류 의심(단색 배경, team_box x좌표 또는 hero_icon_column 확인 필요)"}

    crop_variants = _preprocess_icon_variants(cv2, gray_region)

    def _match(a, b) -> float:
        try:
            score = float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0][0])
        except Exception:
            return 0.0
        return 0.0 if np.isnan(score) else score

    scores: List[Tuple[str, float, float, float, float, float, float, str]] = []
    for name, variant_list in templates.items():
        # 영웅당 참조 이미지가 여러 개일 수 있어, 그중 가장 점수가 높은
        # 것을 대표 점수로 채택한다.
        best_for_hero: Optional[Tuple[str, float, float, float, float, float, float, str]] = None
        for tmpl in variant_list:
            raw_s = _match(crop_variants["raw"], tmpl["raw"])
            blurred_s = _match(crop_variants["blurred"], tmpl["blurred"])
            clahe_s = _match(crop_variants["clahe"], tmpl["clahe"])
            edge_s = _match(crop_variants["edge"], tmpl["edge"])
            baseline_s = max(raw_s, blurred_s)
            final_s = max(baseline_s, clahe_s)
            if best_for_hero is None or final_s > best_for_hero[1]:
                best_for_hero = (name, final_s, raw_s, blurred_s, clahe_s, edge_s, baseline_s, tmpl["source"])
        if best_for_hero is not None:
            scores.append(best_for_hero)

    if not scores:
        return {**empty, "reason": "crop 영역 오류 의심(템플릿 비교 실패)"}

    scores.sort(key=lambda t: t[1], reverse=True)
    pre_role_top = [{"hero": n, "score": round(s, 3)} for n, s, *_ in scores[:TOP_MATCHES_COUNT]]
    pre_role_best = scores[0][1]

    role_name_set = ROLE_HERO_NAME_SETS.get(role_code, set()) if role_code else set()
    role_scores = [t for t in scores if t[0] in role_name_set]

    if not role_scores:
        # 해당 역할 템플릿이 없으면 전체 후보로 폴백하지 않는다 — 탱커 행이
        # 힐러/딜러로 확정되는 것을 막기 위해 무조건 unknown 처리.
        return {
            **empty,
            "reason": "해당 역할의 hero_icons 템플릿 없음",
            "pre_role_top_matches": pre_role_top,
            "pre_role_best_score": round(pre_role_best, 3),
            "role_fallback_used": True,
        }

    post_role_top = [{"hero": n, "score": round(s, 3)} for n, s, *_ in role_scores[:TOP_MATCHES_COUNT]]

    # 색상 히스토그램은 플래그와 무관하게 항상 계산한다 — 진단용
    # (hero_color_shortlist)으로 남겨 나중에 비교/재검토할 수 있게 하기 위함.
    crop_hist = _hsv_hist(cv2, cv2.resize(search_region_bgr, ICON_TEMPLATE_SIZE))
    color_sims = []
    for name, *_rest in role_scores:
        sim = 0.0
        for tmpl in templates.get(name) or []:
            tmpl_hist = tmpl.get("hist")
            if tmpl_hist is None:
                continue
            s = float(cv2.compareHist(crop_hist, tmpl_hist, cv2.HISTCMP_CORREL))
            sim = max(sim, s)
        color_sims.append((name, sim))
    color_sims.sort(key=lambda t: -t[1])
    color_shortlist_names = {n for n, _ in color_sims[:HISTOGRAM_PREFILTER_TOP_K]}
    color_shortlist = [{"hero": n, "color_similarity": round(s, 3)} for n, s in color_sims[:HISTOGRAM_PREFILTER_TOP_K]]

    # 최종 순위 결정 대상. 플래그가 꺼져 있으면(기본값) 색상 히스토그램은
    # 위에서 계산만 하고 실제 순위에는 반영하지 않는다 — role_scores는 이미
    # final_score 내림차순으로 정렬돼 있으므로 그대로 쓰면 된다.
    if ENABLE_COLOR_HISTOGRAM_PREFILTER:
        final_candidate_scores = [t for t in role_scores if t[0] in color_shortlist_names] or role_scores
        color_prefilter_applied = True
    else:
        final_candidate_scores = role_scores
        color_prefilter_applied = False

    best_name, best_score, best_raw, best_blurred, best_clahe, best_edge, best_baseline, best_source = final_candidate_scores[0]
    second_score = final_candidate_scores[1][1] if len(final_candidate_scores) > 1 else 0.0
    rank2_hero = final_candidate_scores[1][0] if len(final_candidate_scores) > 1 else None

    if best_score < BEST_SCORE_MIN_THRESHOLD:
        hero, confidence_label, reason = "unknown", "낮음", "best_score가 낮음"
    elif best_score - second_score < MIN_SCORE_GAP:
        hero, confidence_label, reason = "unknown", "낮음", "1위와 2위 유사도 차이가 작음"
    elif best_score < HIGH_CONFIDENCE_THRESHOLD:
        hero, confidence_label, reason = best_name, "보통", None
    else:
        hero, confidence_label, reason = best_name, "높음", None

    return {
        "hero": hero, "score": round(best_score, 3), "second_score": round(second_score, 3),
        "source": best_source if hero != "unknown" else None,
        "reason": reason, "confidence_label": confidence_label,
        "top_matches": post_role_top,
        "role_code": role_code,
        "pre_role_top_matches": pre_role_top,
        "post_role_top_matches": post_role_top,
        "pre_role_best_score": round(pre_role_best, 3),
        "post_role_best_score": round(best_score, 3),
        "rank1_hero": best_name, "rank2_hero": rank2_hero,
        "raw_gray_score": round(best_raw, 3), "blurred_gray_score": round(best_blurred, 3),
        "clahe_gray_score": round(best_clahe, 3), "edge_score": round(best_edge, 3),
        "baseline_score": round(best_baseline, 3), "final_score": round(best_score, 3),
        # hero=="unknown"일 때는 None으로 둔다(source와 동일한 규칙) — 그렇지
        # 않으면 확정되지 않은 최상위 후보가 실제로 쓰인 것처럼 보일 수 있다.
        "template_path": best_source if hero != "unknown" else None,
        "crop_size_before": [region_w, region_h],
        "crop_size_after": list(ICON_TEMPLATE_SIZE),
        "role_fallback_used": False,
        "color_shortlist": color_shortlist,
        "color_prefilter_applied": color_prefilter_applied,
    }


# ============================================================
# 2단계: 팀 패널 검출(contour) / 쌍 선택 / 검증
# ============================================================

def _pixel_team_color_mask(
    cv2, np, hsv, hue_ranges: List[Tuple[int, int]],
    min_saturation: int, min_value: int,
):
    """hue_ranges + 채도/명도 조건을 만족하는 픽셀만 팀색으로 본다. 밝다는
    이유만으로 포함시키는 예외는 두지 않는다."""
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    mask = np.zeros(hue.shape, dtype=bool)
    for lo, hi in hue_ranges:
        mask |= (hue >= lo) & (hue <= hi)
    return mask & (sat >= min_saturation) & (val >= min_value)


def _contour_candidates(cv2, np, mask, image_shape: Tuple[int, int]) -> List[Dict[str, Any]]:
    """boolean 마스크에 2차원 morphology close(내부 구멍만 메움)를 적용한 뒤
    연결된 영역(contour)의 bounding box를 후보로 뽑고, 크기/비율로 검증한다."""
    h, w = image_shape[:2]
    kernel_h = max(1, int(round(h * TEAM_MASK_CLOSE_KERNEL_HEIGHT_RATIO)))
    kernel_w = max(1, int(round(w * TEAM_MASK_CLOSE_KERNEL_WIDTH_RATIO)))
    closed = cv2.morphologyEx(
        mask.astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((kernel_h, kernel_w), dtype=np.uint8),
    )
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    image_area = float(h * w)
    candidates: List[Dict[str, Any]] = []
    for contour in contours:
        x, y, cw, ch = cv2.boundingRect(contour)
        if cw <= 0 or ch <= 0:
            continue
        width_ratio, height_ratio = cw / w, ch / h
        area_ratio = (cw * ch) / image_area
        aspect_ratio = cw / ch
        reasons = []
        if width_ratio < MIN_TEAM_BLOCK_WIDTH_RATIO:
            reasons.append("폭이 최소 기준 미만")
        if height_ratio < MIN_TEAM_BLOCK_HEIGHT_RATIO:
            reasons.append("높이가 최소 기준 미만")
        if height_ratio > CANDIDATE_MAX_HEIGHT_RATIO:
            reasons.append("높이가 이미지 대비 지나치게 큼(배경 의심)")
        if area_ratio > CANDIDATE_MAX_AREA_RATIO:
            reasons.append("면적이 이미지 대비 지나치게 커 배경으로 의심됨")
        if aspect_ratio < CANDIDATE_MIN_ASPECT_RATIO:
            reasons.append("가로세로 비율이 점수판 패널 형태가 아님(폭이 충분히 길지 않음)")
        candidates.append({
            "box": {"x0": x, "y0": y, "x1": x + cw, "y1": y + ch},
            "width": cw, "height": ch,
            "aspect_ratio": round(aspect_ratio, 3),
            "area_ratio": round(area_ratio, 4),
            "score": round(area_ratio, 4),  # 유효 후보 중 순위/폴백에만 쓰는 보조 점수(면적이 클수록 실제 패널일 가능성이 높다고 본다).
            "valid": not reasons,
            "rejection_reasons": reasons,
        })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def _find_team_color_candidates(
    cv2, np, image, hue_ranges: List[Tuple[int, int]], min_value: int,
) -> List[Dict[str, Any]]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = _pixel_team_color_mask(cv2, np, hsv, hue_ranges, TEAM_COLOR_MIN_SATURATION, min_value)
    return _contour_candidates(cv2, np, mask, image.shape)


def _relaxed_search_adjacent(
    cv2, np, image, hue_ranges: List[Tuple[int, int]], known_box, side: str,
    min_saturation: int = RELAXED_TEAM_COLOR_MIN_SATURATION,
    min_value: int = RELAXED_TEAM_COLOR_MIN_VALUE,
) -> Optional[Dict[str, Any]]:
    """완화된 색 조건으로 known_box의 위(side="above")/아래(side="below")에서만
    다시 찾는다. known_box와 x범위가 충분히 겹치는 후보만 채택해 임의 위치에
    team_box를 만들지 않는다. min_saturation/min_value 기본값은 "한 팀이
    아예 검출되지 않았을 때" 쓰는 완화 기준이며, 호출부가 다른 시나리오(예:
    행 높이 불일치 재탐색)에 맞는 값을 넘길 수 있다."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = _pixel_team_color_mask(cv2, np, hsv, hue_ranges, min_saturation, min_value)
    if side == "below":
        mask[:known_box["y1"], :] = False
    else:
        mask[known_box["y0"]:, :] = False

    candidates = _contour_candidates(cv2, np, mask, image.shape)
    known_width = known_box["x1"] - known_box["x0"]

    best = None
    best_overlap = -1.0
    for cand in candidates:
        if not cand["valid"]:
            continue
        box = cand["box"]
        if side == "below" and box["y0"] < known_box["y1"]:
            continue
        if side == "above" and box["y1"] > known_box["y0"]:
            continue
        overlap = min(box["x1"], known_box["x1"]) - max(box["x0"], known_box["x0"])
        overlap_ratio = overlap / max(1, min(cand["width"], known_width))
        if overlap_ratio < PAIR_MIN_X_OVERLAP_RATIO:
            continue
        if overlap_ratio > best_overlap:
            best, best_overlap = cand, overlap_ratio
    return best


def _evaluate_pair(blue_box, red_box, image_shape) -> Tuple[bool, float, Dict[str, Any], List[str]]:
    """파란/빨간 후보 쌍이 "같은 점수판의 두 팀"으로 자연스러운지 평가한다.
    하나라도 기준을 벗어나면 이 쌍은 탈락시키고, 통과한 쌍끼리는
    pair_score(작을수록 좋음)로 순위를 매긴다."""
    h = image_shape[0]
    blue_w, blue_h = blue_box["x1"] - blue_box["x0"], blue_box["y1"] - blue_box["y0"]
    red_w, red_h = red_box["x1"] - red_box["x0"], red_box["y1"] - red_box["y0"]

    order_ok = blue_box["y0"] < red_box["y0"]
    overlap = min(blue_box["x1"], red_box["x1"]) - max(blue_box["x0"], red_box["x0"])
    overlap_ratio = overlap / max(1, min(blue_w, red_w))
    width_diff_ratio = abs(blue_w - red_w) / max(blue_w, red_w)
    height_diff_ratio = abs(blue_h - red_h) / max(blue_h, red_h)
    left_x_diff_ratio = abs(blue_box["x0"] - red_box["x0"]) / max(blue_w, red_w)
    blue_center = (blue_box["x0"] + blue_box["x1"]) / 2
    red_center = (red_box["x0"] + red_box["x1"]) / 2
    center_x_diff_ratio = abs(blue_center - red_center) / max(blue_w, red_w)
    vertical_gap_ratio = (red_box["y0"] - blue_box["y1"]) / h

    details = {
        "order_ok": order_ok, "overlap_ratio": round(overlap_ratio, 3),
        "width_diff_ratio": round(width_diff_ratio, 3), "height_diff_ratio": round(height_diff_ratio, 3),
        "left_x_diff_ratio": round(left_x_diff_ratio, 3), "center_x_diff_ratio": round(center_x_diff_ratio, 3),
        "vertical_gap_ratio": round(vertical_gap_ratio, 3),
    }
    reasons = []
    if not order_ok:
        reasons.append("파란팀이 빨간팀보다 위에 있지 않음")
    if overlap_ratio < PAIR_MIN_X_OVERLAP_RATIO:
        reasons.append("x범위 겹침 부족")
    if width_diff_ratio > PAIR_MAX_WIDTH_DIFF_RATIO:
        reasons.append("폭 차이가 큼")
    if height_diff_ratio > PAIR_MAX_HEIGHT_DIFF_RATIO:
        reasons.append("높이 차이가 큼")
    if left_x_diff_ratio > PAIR_MAX_LEFT_X_DIFF_RATIO:
        reasons.append("왼쪽 시작점 차이가 큼")
    if center_x_diff_ratio > PAIR_MAX_CENTER_X_DIFF_RATIO:
        reasons.append("중심 x좌표 차이가 큼")
    if vertical_gap_ratio > PAIR_MAX_VERTICAL_GAP_RATIO or vertical_gap_ratio < -PAIR_MAX_VERTICAL_OVERLAP_RATIO:
        reasons.append("두 패널 사이 수직 간격이 비정상적")

    pair_score = round(
        width_diff_ratio + height_diff_ratio + left_x_diff_ratio
        + center_x_diff_ratio + max(0.0, vertical_gap_ratio), 4,
    )
    return not reasons, pair_score, details, reasons


def _select_team_boxes(cv2, np, image, blue_candidates, red_candidates):
    """파란/빨간 후보를 각각 독립적으로 하나씩 뽑지 않고, 가능한 쌍을 전부
    평가해 통과하는 쌍 중 pair_score가 최소인 쌍을 선택한다. 유효한 쌍이
    없으면 각 색상의 최선 후보를 독립적으로 쓰거나(둘 다 후보가 있을 때),
    한쪽만 후보가 있으면 반대쪽은 인접 위치에서 완화 탐색한다."""
    valid_blue = [c for c in blue_candidates if c["valid"]]
    valid_red = [c for c in red_candidates if c["valid"]]

    pair_evaluations: List[Dict[str, Any]] = []
    best_pair = None  # (blue_cand, red_cand, score, details)
    for bc in valid_blue:
        for rc in valid_red:
            passes, score, details, reasons = _evaluate_pair(bc["box"], rc["box"], image.shape)
            pair_evaluations.append({
                "blue_box": bc["box"], "red_box": rc["box"],
                "passes": passes, "pair_score": score,
                "details": details, "rejection_reasons": reasons,
            })
            if passes and (best_pair is None or score < best_pair[2]):
                best_pair = (bc, rc, score, details)

    ally_candidate = enemy_candidate = None
    selected_by = {"ally": "none", "enemy": "none"}
    y_fallback_used = {"ally": False, "enemy": False}
    pair_score = pair_details = None

    if best_pair:
        bc, rc, score, details = best_pair
        ally_candidate, enemy_candidate = bc, rc
        selected_by["ally"] = selected_by["enemy"] = "pair"
        pair_score, pair_details = score, details
    elif valid_blue and valid_red:
        # 후보는 둘 다 있지만 자연스러운 쌍이 없다 — 임의로 결합하는 대신
        # 각 색상에서 가장 면적이 큰(=패널일 가능성이 높은) 후보를 독립적으로 쓴다.
        ally_candidate, enemy_candidate = valid_blue[0], valid_red[0]
        selected_by["ally"] = selected_by["enemy"] = "single_best_no_valid_pair"
    elif valid_blue:
        ally_candidate = valid_blue[0]
        selected_by["ally"] = "single_best"
        relaxed = _relaxed_search_adjacent(cv2, np, image, RED_HUE_RANGES, ally_candidate["box"], "below")
        if relaxed:
            enemy_candidate = relaxed
            selected_by["enemy"] = "relaxed_fallback"
            y_fallback_used["enemy"] = True
    elif valid_red:
        enemy_candidate = valid_red[0]
        selected_by["enemy"] = "single_best"
        relaxed = _relaxed_search_adjacent(cv2, np, image, [BLUE_HUE_RANGE], enemy_candidate["box"], "above")
        if relaxed:
            ally_candidate = relaxed
            selected_by["ally"] = "relaxed_fallback"
            y_fallback_used["ally"] = True
    # 둘 다 없으면 ally_candidate/enemy_candidate 모두 None으로 남는다.

    return ally_candidate, enemy_candidate, selected_by, pair_score, pair_details, pair_evaluations, y_fallback_used


def _resolve_x_range(own_box, paired_box, image_width: int) -> Tuple[Optional[Tuple[int, int]], str]:
    """team_box의 x범위 결정 우선순위: 1) 자기 자신의 contour bounding
    box, 2) 같은 쌍으로 선택된 반대 팀 박스의 x범위, 3) 두 박스의 평균 x범위.
    셋 다 유효하지 않으면 None을 반환한다 — 이미지 전체 폭 폴백은 쓰지 않는다."""
    def _valid(b) -> bool:
        return bool(b) and (b["x1"] - b["x0"]) >= image_width * MIN_TEAM_BLOCK_WIDTH_RATIO

    if _valid(own_box):
        return (own_box["x0"], own_box["x1"]), "own_candidate"
    if _valid(paired_box):
        return (paired_box["x0"], paired_box["x1"]), "paired_team"
    if own_box and paired_box:
        x0 = (own_box["x0"] + paired_box["x0"]) // 2
        x1 = (own_box["x1"] + paired_box["x1"]) // 2
        if (x1 - x0) >= image_width * MIN_TEAM_BLOCK_WIDTH_RATIO:
            return (x0, x1), "average_of_both"
    return None, "none"


def _slice_bounds(bounds: Tuple[int, int], count: int) -> List[Optional[Tuple[int, int]]]:
    """[top, bottom) 구간을 count개의 균등한 행으로 나눈다."""
    top, bottom = bounds
    total = bottom - top
    if total <= 0:
        return [None] * count
    step = total / count
    bands: List[Optional[Tuple[int, int]]] = []
    for i in range(count):
        y0 = top + int(round(step * i))
        y1 = top + int(round(step * (i + 1)))
        if y1 <= y0:
            y1 = y0 + 1
        bands.append((y0, y1))
    return bands


def _compute_player_area(y_range: Tuple[int, int], header_height: int) -> Tuple[Tuple[int, int], int]:
    top, bottom = y_range
    header_height = max(0, min(header_height, bottom - top))
    player_top = min(top + header_height, bottom)
    return (player_top, bottom), header_height


def _detect_ally_header_height(cv2, np, image, team_box: Dict[str, int]) -> Dict[str, Any]:
    """아군 team_box 안에 실제로 헤더(칼럼 제목 바)가 남아있는지 직접 판별한다.
    team_box 맨 위 1행 높이만큼과 맨 아래(row5, 항상 실제 플레이어 행) 1행
    높이만큼의 평균 채도(HSV S)를 비교해, 차이가 크면(위쪽이 칼럼 제목 바라서
    채도가 낮으면) 헤더가 남아있다고 보고 1행 높이만큼 제외한다. 차이가
    작으면(색상 검출 단계에서 헤더가 이미 제외된 경우) 0을 반환한다."""
    x0, x1, y0, y1 = team_box["x0"], team_box["x1"], team_box["y0"], team_box["y1"]
    total_height = y1 - y0
    band_h = max(1, int(round(total_height * HEADER_DETECT_SAMPLE_BAND_RATIO)))
    info = {
        "sample_band_px": band_h, "top_mean_saturation": None, "bottom_mean_saturation": None,
        "saturation_diff": None, "threshold": HEADER_DETECT_SATURATION_DIFF_THRESHOLD,
        "header_detected": False, "header_height": 0,
    }
    if band_h * 2 > total_height:
        return info

    top_band = image[y0:y0 + band_h, x0:x1]
    bottom_band = image[max(y0, y1 - band_h):y1, x0:x1]
    if top_band.size == 0 or bottom_band.size == 0:
        return info

    top_sat = float(cv2.cvtColor(top_band, cv2.COLOR_BGR2HSV)[:, :, 1].mean())
    bottom_sat = float(cv2.cvtColor(bottom_band, cv2.COLOR_BGR2HSV)[:, :, 1].mean())
    diff = abs(top_sat - bottom_sat)
    detected = diff >= HEADER_DETECT_SATURATION_DIFF_THRESHOLD
    info.update({
        "top_mean_saturation": round(top_sat, 1), "bottom_mean_saturation": round(bottom_sat, 1),
        "saturation_diff": round(diff, 1), "header_detected": detected,
        "header_height": band_h if detected else 0,
    })
    return info


def _resolve_header_height(cv2, np, image, team: str, team_box: Dict[str, int]) -> Dict[str, Any]:
    """팀별 header_height를 결정한다. 상대팀은 항상 0(TAB 점수판은 상대팀
    패널 위에 칼럼 제목을 반복하지 않음). 아군은 _detect_ally_header_height()
    로 실제 이미지에서 판별한다."""
    if team == "enemy":
        return {
            "sample_band_px": 0, "top_mean_saturation": None, "bottom_mean_saturation": None,
            "saturation_diff": None, "threshold": None,
            "header_detected": False, "header_height": int(ENEMY_HEADER_HEIGHT_RATIO),
            "method": "enemy_never_has_header",
        }
    info = _detect_ally_header_height(cv2, np, image, team_box)
    info["method"] = "saturation_top_vs_bottom_band"
    return info


def _validate_team_box(
    box: Dict[str, int], image_shape: Tuple[int, int], header_height: int,
) -> Tuple[bool, Optional[str], Optional[float], List[int]]:
    """team_box를 그대로 5등분하기 전에 검증한다 — 잘못 검출된 team_box가
    그대로 row_boxes로 이어지면 hero crop이 엉뚱한 영역을 가리키게 된다.
    실패하면 (False, 사유, None/예상 행 높이, [])를 반환해 호출부가
    row_boxes를 전부 None으로 두게 한다."""
    h, w = image_shape[:2]
    width, height = box["x1"] - box["x0"], box["y1"] - box["y0"]
    if width <= 0 or height <= 0:
        return False, "team_box 크기가 0 이하", None, []

    width_ratio, height_ratio = width / w, height / h
    aspect_ratio = width / height
    if aspect_ratio < CANDIDATE_MIN_ASPECT_RATIO:
        return False, "가로로 긴 패널 형태가 아님", None, []
    if width_ratio < MIN_TEAM_BLOCK_WIDTH_RATIO or height_ratio < MIN_TEAM_BLOCK_HEIGHT_RATIO:
        return False, "폭 또는 높이가 최소 기준 미만", None, []
    if height_ratio > CANDIDATE_MAX_HEIGHT_RATIO:
        return False, "높이가 이미지 대비 지나치게 큼(배경 의심)", None, []

    header_height = max(0, min(header_height, height))
    player_height = height - header_height
    if player_height <= 0:
        return False, "헤더 제외 후 플레이어 영역이 없음", None, []
    expected_row_height = player_height / PLAYERS_PER_TEAM
    row_height_ratio = expected_row_height / h
    if row_height_ratio < MIN_ROW_HEIGHT_RATIO:
        return False, "예상 행 높이가 지나치게 작음", expected_row_height, []
    if row_height_ratio > MAX_ROW_HEIGHT_RATIO:
        return False, "예상 행 높이가 지나치게 큼", expected_row_height, []

    player_top = min(box["y0"] + header_height, box["y1"])
    row_bounds = _slice_bounds((player_top, box["y1"]), PLAYERS_PER_TEAM)
    row_heights = [b[1] - b[0] for b in row_bounds if b]
    if len(row_heights) < PLAYERS_PER_TEAM:
        return False, "5개 행으로 나눌 수 없음", expected_row_height, row_heights
    if row_heights and (max(row_heights) - min(row_heights)) > ROW_HEIGHT_TOLERANCE_PX:
        return False, "생성된 행 높이가 서로 다름", expected_row_height, row_heights
    return True, None, expected_row_height, row_heights


def _empty_team_layout(mask_candidates: List[Dict[str, Any]], selected_by: str, reason: Optional[str] = None) -> Dict[str, Any]:
    return {
        "team_box": None, "header_height": 0, "header_detection": None, "player_area_box": None,
        "row_boxes": [None] * PLAYERS_PER_TEAM,
        "mask_candidate_boxes": mask_candidates,
        "selected_candidate_box": None, "selected_candidate_score": None,
        "pair_score": None, "pair_details": None,
        "selected_by": selected_by,
        "x_fallback_used": False, "y_fallback_used": False,
        "layout_validation_ok": False,
        "layout_validation_reason": reason or "팀 패널을 검출하지 못함",
        "expected_row_height": None, "row_heights": [],
    }


def _build_team_layout_entry(
    cv2, np, image, team: str, candidate: Optional[Dict[str, Any]], mask_candidates: List[Dict[str, Any]],
    other_box: Optional[Dict[str, int]], selected_by_label: str,
    pair_score: Optional[float], pair_details: Optional[Dict[str, Any]],
    y_fallback_used: bool, width_img: int,
) -> Dict[str, Any]:
    """색 후보 하나(candidate)로부터 이 팀의 team_box/header/row_boxes를
    조립한다. _compute_team_layout의 1차 검출 결과뿐 아니라, 행 높이 불일치로
    재탐색한 완화 후보에도 동일한 방식으로 재사용한다."""
    if not candidate:
        return _empty_team_layout(mask_candidates, selected_by_label)

    own_box = candidate["box"]
    x_range, x_source = _resolve_x_range(own_box, other_box, width_img)
    if not x_range:
        return _empty_team_layout(
            mask_candidates, selected_by_label, reason="x범위를 확정할 수 없음(이미지 전체 폭 폴백 금지)",
        )

    team_box = {"x0": x_range[0], "y0": own_box["y0"], "x1": x_range[1], "y1": own_box["y1"]}
    header_info = _resolve_header_height(cv2, np, image, team, team_box)
    header_height_guess = header_info["header_height"]
    valid_ok, valid_reason, expected_row_height, row_heights = _validate_team_box(
        team_box, image.shape, header_height_guess
    )

    base = {
        "team_box": team_box,
        "header_detection": header_info,
        "mask_candidate_boxes": mask_candidates,
        "selected_candidate_box": own_box,
        "selected_candidate_score": candidate.get("score"),
        "pair_score": pair_score if selected_by_label == "pair" else None,
        "pair_details": pair_details if selected_by_label == "pair" else None,
        "selected_by": selected_by_label,
        "x_fallback_used": x_source != "own_candidate",
        "y_fallback_used": y_fallback_used,
        "expected_row_height": expected_row_height,
        "row_heights": row_heights,
    }

    if not valid_ok:
        return {
            **base, "header_height": 0, "player_area_box": None,
            "row_boxes": [None] * PLAYERS_PER_TEAM,
            "layout_validation_ok": False, "layout_validation_reason": valid_reason,
        }

    player_y_range, header_height = _compute_player_area((team_box["y0"], team_box["y1"]), header_height_guess)
    player_area_box = {"x0": team_box["x0"], "y0": player_y_range[0], "x1": team_box["x1"], "y1": player_y_range[1]}
    row_y_bounds = _slice_bounds(player_y_range, PLAYERS_PER_TEAM)
    row_boxes = [
        {"x0": team_box["x0"], "y0": ry[0], "x1": team_box["x1"], "y1": ry[1]} if ry else None
        for ry in row_y_bounds
    ]
    return {
        **base, "header_height": header_height, "player_area_box": player_area_box,
        "row_boxes": row_boxes,
        "layout_validation_ok": True, "layout_validation_reason": None,
    }


def _largest_solid_run(coverage: List[float], threshold: float) -> Optional[Tuple[int, int]]:
    """coverage(행별 마스크 커버리지 비율 배열)에서 threshold 이상인 인덱스의
    가장 긴 연속 구간을 [start, end) 형태로 반환한다. 없으면 None."""
    best: Optional[Tuple[int, int]] = None
    start: Optional[int] = None
    for i, v in enumerate(coverage):
        if v >= threshold:
            if start is None:
                start = i
        elif start is not None:
            if best is None or (i - start) > (best[1] - best[0]):
                best = (start, i)
            start = None
    if start is not None:
        end = len(coverage)
        if best is None or (end - start) > (best[1] - best[0]):
            best = (start, end)
    return best


def _retry_enemy_masked_by_ally(
    cv2, np, image, layout: Dict[str, Any], candidates_by_team: Dict[str, List[Dict[str, Any]]],
    width_img: int, diagnostics: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """상대팀(빨강)이 완전히 검출 실패했을 때(team_box=None), 이미 확정된
    아군 team_box의 x범위 안에서 행별 마스크 커버리지가 threshold 이상으로
    견고하게 이어지는 가장 긴 연속 구간을 상대팀 패널로 본다. 아군 바로
    아래부터 탐색하므로 배경 노이즈나 아군 x범위 밖의 오염과는 무관하다."""
    ally_entry = layout["ally"]
    ally_box = ally_entry.get("team_box")
    ally_row_h = ally_entry.get("expected_row_height")
    diagnostics["enemy_masked_retry_attempted"] = bool(ally_box and ally_row_h and ally_entry.get("layout_validation_ok"))
    if not diagnostics["enemy_masked_retry_attempted"]:
        return layout, diagnostics

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = _pixel_team_color_mask(cv2, np, hsv, RED_HUE_RANGES, TEAM_COLOR_MIN_SATURATION, RED_TEAM_COLOR_MIN_VALUE)
    search_top = ally_box["y1"]
    coverage = mask[search_top:, ally_box["x0"]:ally_box["x1"]].mean(axis=1)
    run = _largest_solid_run(list(coverage), SOLID_ROW_COVERAGE_THRESHOLD)
    diagnostics["enemy_masked_retry_run"] = run

    if not run or (run[1] - run[0]) < ally_row_h * ENEMY_MASKED_RETRY_MIN_ROW_MULT:
        diagnostics["enemy_masked_retry_found"] = False
        return layout, diagnostics

    candidate_box = {"x0": ally_box["x0"], "y0": search_top + run[0], "x1": ally_box["x1"], "y1": search_top + run[1]}
    diagnostics["enemy_masked_retry_found"] = True
    diagnostics["enemy_masked_retry_box"] = candidate_box
    new_entry = _build_team_layout_entry(
        cv2, np, image, "enemy", {"box": candidate_box, "score": run[1] - run[0]}, candidates_by_team["enemy"],
        ally_box, "masked_retry_excluding_ally_row_coverage",
        None, None, False, width_img,
    )
    layout = {**layout, "enemy": new_entry}
    return layout, diagnostics


def _self_relaxed_retry_no_cross_team(
    cv2, np, image, layout: Dict[str, Any], picked_by_team: Dict[str, Optional[Dict[str, Any]]],
    candidates_by_team: Dict[str, List[Dict[str, Any]]], width_img: int, diagnostics: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """반대 팀이 아예 검출되지 않아 _resolve_row_height_mismatch의 교차 비교가
    불가능할 때 쓰는 대체 경로. candidate는 있지만(완전 실패가 아님)
    expected_row_height가 이미지 높이 대비 비정상적으로 작은 팀만, 완화된 값
    임계값으로 전체 이미지에서 다시 색 후보를 찾아(방향 anchor 없이) 기존
    candidate와 x범위가 겹치면서 더 큰 후보로 교체를 시도한다. 아군(파랑)만
    지원한다 — 상대팀(빨강)이 "candidate는 있는데 비정상적으로 작은" 경우는
    이 함수가 다루지 않는다(원인이 다를 수 있음). 상대팀이 아예 완전히
    실패한 경우(team_box=None)는 이 함수 끝에서 _retry_enemy_masked_by_ally로
    별도 처리한다."""
    img_h = image.shape[0]
    for team in ("ally", "enemy"):
        h = layout[team].get("expected_row_height")
        if not h:
            continue
        ratio = h / img_h
        diagnostics[f"{team}_row_height_ratio"] = round(ratio, 4)
        if ratio >= EXPECTED_ROW_HEIGHT_MIN_TRUST_RATIO:
            continue
        if team != "ally":
            continue  # 상대팀은 위 docstring 이유로 이번 재탐색 대상에서 제외.
        own_candidate = picked_by_team[team]
        if not own_candidate:
            continue

        diagnostics.update({
            "triggered": True, "small_team": team, "trigger_reason": "absolute_ratio_no_cross_team",
        })
        wider = _find_team_color_candidates(cv2, np, image, [BLUE_HUE_RANGE], BLUE_ROW_HEIGHT_RETRY_MIN_VALUE)
        own_box = own_candidate["box"]
        own_width = own_box["x1"] - own_box["x0"]
        own_height = own_box["y1"] - own_box["y0"]
        best = None
        for c in wider:
            if not c["valid"]:
                continue
            b = c["box"]
            overlap = min(b["x1"], own_box["x1"]) - max(b["x0"], own_box["x0"])
            overlap_ratio = overlap / max(1, min(b["x1"] - b["x0"], own_width))
            if overlap_ratio < PAIR_MIN_X_OVERLAP_RATIO:
                continue  # 기존 candidate와 x범위가 거의 안 겹치면 무관한 위치의 후보 — 제외.
            if (b["y1"] - b["y0"]) <= own_height:
                continue  # 기존보다 커진 경우만(작은 행 일부만 잡던 문제를 해결하는 방향).
            if best is None or c["score"] > best["score"]:
                best = c
        diagnostics["replacement_found"] = bool(best)
        if not best:
            continue

        new_entry = _build_team_layout_entry(
            cv2, np, image, team, best, candidates_by_team[team],
            None, "relaxed_self_retry_no_cross_team",
            None, None, True, width_img,
        )
        layout = {**layout, team: new_entry}

    if layout["enemy"].get("team_box") is None:
        layout, diagnostics = _retry_enemy_masked_by_ally(
            cv2, np, image, layout, candidates_by_team, width_img, diagnostics,
        )

    return layout, diagnostics


def _resolve_row_height_mismatch(
    cv2, np, image, layout: Dict[str, Any], picked_by_team: Dict[str, Optional[Dict[str, Any]]],
    candidates_by_team: Dict[str, List[Dict[str, Any]]], width_img: int,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """두 팀의 expected_row_height가 크게 다르면(ROW_HEIGHT_MISMATCH_RATIO
    이상) 작은 쪽만 완화된 색 기준으로 인접 위치를 재탐색한다 — 본인 강조
    행만 명도 임계값을 통과해 team_box가 그 행 1개 크기로 잘못 잡히는
    경우를 구제한다. 교차 비교가 불가능하면(한쪽 검출 실패) 이미지 높이
    대비 절대 비율로 자체 재탐색하되(_self_relaxed_retry_no_cross_team),
    candidate 자체가 없는 팀은 원인이 다를 수 있어 대상에서 제외한다."""
    ally_h = layout["ally"].get("expected_row_height")
    enemy_h = layout["enemy"].get("expected_row_height")
    diagnostics = {
        "ally_expected_row_height": ally_h, "enemy_expected_row_height": enemy_h,
        "triggered": False, "small_team": None, "replacement_found": False,
    }
    if not ally_h or not enemy_h:
        return _self_relaxed_retry_no_cross_team(
            cv2, np, image, layout, picked_by_team, candidates_by_team, width_img, diagnostics,
        )

    diff_ratio = abs(ally_h - enemy_h) / max(ally_h, enemy_h)
    diagnostics["diff_ratio"] = round(diff_ratio, 3)
    if diff_ratio < ROW_HEIGHT_MISMATCH_RATIO:
        return layout, diagnostics

    small_team = "ally" if ally_h < enemy_h else "enemy"
    large_team = "enemy" if small_team == "ally" else "ally"
    anchor_candidate = picked_by_team[large_team]
    diagnostics.update({"triggered": True, "small_team": small_team})
    if not anchor_candidate:
        return layout, diagnostics

    # 작은 쪽이 파랑이면 큰 쪽(빨강) 위쪽에서 파랑을, 작은 쪽이 빨강이면
    # 큰 쪽(파랑) 아래쪽에서 빨강을 다시 찾는다 — _select_team_boxes가
    # "한쪽만 검출됐을 때" 쓰는 것과 같은 방향이다. 파랑은
    # RELAXED_TEAM_COLOR_MIN_VALUE(30)까지 완화하면 배경까지 붙어버려
    # BLUE_ROW_HEIGHT_RETRY_MIN_VALUE로 덜 완화한 기준을 쓴다.
    if small_team == "ally":
        relaxed = _relaxed_search_adjacent(
            cv2, np, image, [BLUE_HUE_RANGE], anchor_candidate["box"], "above",
            min_value=BLUE_ROW_HEIGHT_RETRY_MIN_VALUE,
        )
    else:
        relaxed = _relaxed_search_adjacent(cv2, np, image, RED_HUE_RANGES, anchor_candidate["box"], "below")
    diagnostics["replacement_found"] = bool(relaxed)
    if not relaxed:
        return layout, diagnostics

    new_entry = _build_team_layout_entry(
        cv2, np, image, small_team, relaxed, candidates_by_team[small_team],
        anchor_candidate["box"], "relaxed_row_height_mismatch",
        None, None, True, width_img,
    )
    layout = {**layout, small_team: new_entry}
    return layout, diagnostics


# ------------------------------------------------------------
# 1단계(coarse): 전체화면 캡처 대응 — 점수판 대략적 위치만 찾아 sub-image로
# 잘라낸다. 아래 두 함수는 정밀 검증(_contour_candidates의 크기/비율 기준)을
# 하지 않는다 — "점수판이 대충 어디쯤 있는지"만 찾는 게 목적이며, 여기서 나온
# 박스는 최종 team_box로 쓰이지 않는다.
# ------------------------------------------------------------

def _largest_color_contour_bbox(cv2, np, mask, image_shape) -> Optional[Dict[str, int]]:
    """mask에 morphology close를 적용한 뒤 연결된 영역 중 면적이 가장 큰
    것의 bounding box만 반환한다. _contour_candidates()와 달리 크기/비율
    검증(MIN_TEAM_BLOCK_*_RATIO 등)은 하지 않는다 — 그 검증은 sub-image로
    좁힌 뒤 2단계(_compute_team_layout)가 그대로 수행한다. 노이즈로 생기는
    아주 작은 연결 영역만 COARSE_MIN_AREA_RATIO로 걸러낸다."""
    h, w = image_shape[:2]
    kernel_h = max(1, int(round(h * TEAM_MASK_CLOSE_KERNEL_HEIGHT_RATIO)))
    kernel_w = max(1, int(round(w * TEAM_MASK_CLOSE_KERNEL_WIDTH_RATIO)))
    closed = cv2.morphologyEx(
        mask.astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((kernel_h, kernel_w), dtype=np.uint8),
    )
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    image_area = float(h * w)
    best_box: Optional[Dict[str, int]] = None
    best_area = 0
    for contour in contours:
        x, y, cw, ch = cv2.boundingRect(contour)
        if cw <= 0 or ch <= 0:
            continue
        area = cw * ch
        if area / image_area < COARSE_MIN_AREA_RATIO:
            continue
        if area > best_area:
            best_area = area
            best_box = {"x0": x, "y0": y, "x1": x + cw, "y1": y + ch}
    return best_box


def _detect_coarse_scoreboard_box(cv2, np, image) -> Tuple[Optional[Dict[str, int]], Optional[str]]:
    """완화된 색 조건(RELAXED_TEAM_COLOR_MIN_SATURATION/VALUE — 한쪽 팀만
    검출됐을 때 인접 위치를 재탐색하는 데 쓰던 것과 동일한 완화 기준을
    재사용)으로 파란/빨간 영역의 대략적인 위치만 찾는다. 정교한 크기/비율
    검증 없이 "점수판이 대충 어디쯤 있는지"만 찾는 것이 목적이라, 여기서
    나온 박스는 최종 team_box로 쓰지 않는다.

    파란/빨간 대략적 후보를 합쳐 점수판 전체를 포함할 여유 있는 사각 영역
    (COARSE_CROP_MARGIN_RATIO만큼 마진 포함)을 원본 이미지 기준 좌표로
    반환한다. 완화 조건으로도 후보가 전혀 없으면 (None, 실패 사유)를 반환해
    호출부가 크롭 없이 원본 이미지를 그대로 쓰게 한다 — 크롭 자체가 잘못되어
    점수판을 아예 잘라먹는 사고를 방지하는 안전한 폴백이다."""
    h, w = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    blue_mask = _pixel_team_color_mask(
        cv2, np, hsv, [BLUE_HUE_RANGE], RELAXED_TEAM_COLOR_MIN_SATURATION, RELAXED_TEAM_COLOR_MIN_VALUE,
    )
    red_mask = _pixel_team_color_mask(
        cv2, np, hsv, RED_HUE_RANGES, RELAXED_TEAM_COLOR_MIN_SATURATION, RELAXED_TEAM_COLOR_MIN_VALUE,
    )
    blue_box = _largest_color_contour_bbox(cv2, np, blue_mask, image.shape)
    red_box = _largest_color_contour_bbox(cv2, np, red_mask, image.shape)

    boxes = [b for b in (blue_box, red_box) if b]
    if not boxes:
        return None, "완화된 색 조건으로도 파란/빨간 영역을 전혀 찾지 못함"

    x0 = min(b["x0"] for b in boxes)
    y0 = min(b["y0"] for b in boxes)
    x1 = max(b["x1"] for b in boxes)
    y1 = max(b["y1"] for b in boxes)

    margin_x = int(round((x1 - x0) * COARSE_CROP_MARGIN_RATIO))
    margin_y = int(round((y1 - y0) * COARSE_CROP_MARGIN_RATIO))
    x0 = max(0, x0 - margin_x)
    y0 = max(0, y0 - margin_y)
    x1 = min(w, x1 + margin_x)
    y1 = min(h, y1 + margin_y)

    if x1 <= x0 or y1 <= y0:
        return None, "coarse crop 좌표 계산 결과가 비정상적(폭 또는 높이가 0 이하)"

    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}, None


def _translate_box(box: Optional[Dict[str, int]], offset_x: int, offset_y: int) -> Optional[Dict[str, int]]:
    """sub-image 기준 로컬 좌표 box를 원본 이미지 기준 절대 좌표로 옮긴다."""
    if not box:
        return None
    return {
        "x0": box["x0"] + offset_x, "y0": box["y0"] + offset_y,
        "x1": box["x1"] + offset_x, "y1": box["y1"] + offset_y,
    }


def _translate_layout_coordinates(layout: Dict[str, Any], offset_x: int, offset_y: int) -> Dict[str, Any]:
    """_compute_team_layout()이 sub-image 기준으로 계산한 모든 좌표(team_box,
    player_area_box, row_boxes, mask_candidate_boxes, pair_evaluations의
    blue_box/red_box 등)를 원본 이미지 기준 절대 좌표로 옮긴다. 이 변환이
    누락되면 analyze_scoreboard_image()의 image[y0:y1, x0:x1] 픽셀 접근이
    sub-image가 아니라 원본 이미지 기준으로 이뤄지기 때문에 크롭 위치가
    완전히 어긋난다. offset이 (0,0)이면(coarse crop을 쓰지 않은 경우) 변환할
    필요가 없어 그대로 반환한다. header_detection/pair_details/
    row_height_mismatch 등 좌표가 아닌 값(비율·불리언·통계)은 그대로 둔다."""
    if offset_x == 0 and offset_y == 0:
        return layout

    translated: Dict[str, Any] = {}
    for team in ("ally", "enemy"):
        data = dict(layout.get(team) or {})
        data["team_box"] = _translate_box(data.get("team_box"), offset_x, offset_y)
        data["player_area_box"] = _translate_box(data.get("player_area_box"), offset_x, offset_y)
        data["row_boxes"] = [_translate_box(b, offset_x, offset_y) for b in (data.get("row_boxes") or [])]
        data["selected_candidate_box"] = _translate_box(data.get("selected_candidate_box"), offset_x, offset_y)
        data["mask_candidate_boxes"] = [
            {**c, "box": _translate_box(c.get("box"), offset_x, offset_y)}
            for c in (data.get("mask_candidate_boxes") or [])
        ]
        translated[team] = data

    meta = dict(layout.get("_meta") or {})
    meta["pair_evaluations"] = [
        {
            **ev,
            "blue_box": _translate_box(ev.get("blue_box"), offset_x, offset_y),
            "red_box": _translate_box(ev.get("red_box"), offset_x, offset_y),
        }
        for ev in (meta.get("pair_evaluations") or [])
    ]
    translated["_meta"] = meta
    return translated


def _compute_team_layout_with_coarse_crop(cv2, np, image) -> Dict[str, Any]:
    """coarse-to-fine 2단계 검출의 진입점. 1단계(_detect_coarse_scoreboard_box)로
    점수판 대략적 위치를 찾아 sub-image로 자르고, 2단계는 _compute_team_layout()
    (정밀 파이프라인, 무수정)을 그 sub-image에 재실행한다 — 정밀 파이프라인의
    크기/비율 임계값이 "이미지 전체 대비"라서, 점수판만 캡처한 경우엔 coarse
    박스가 이미지 경계에 clamp되어 크롭이 사실상 no-op이 되고, 전체화면
    캡처처럼 점수판이 일부만 차지하는 경우엔 sub-image로 좁혀 비중을 키운다.

    2단계 결과 좌표는 sub-image 기준이므로 반드시 원본 이미지 기준으로
    변환한다(_translate_layout_coordinates). coarse 단계가 아무것도 못 찾으면
    크롭 없이 원본 이미지를 그대로 정밀 파이프라인에 넘긴다.

    반환값은 _compute_team_layout()과 동일한 구조에 "_meta.coarse_crop"
    (진단·디버그 이미지 저장용) 정보만 추가한 것이다."""
    coarse_box, coarse_reason = _detect_coarse_scoreboard_box(cv2, np, image)

    sub_image = None
    if coarse_box is not None:
        candidate = image[coarse_box["y0"]:coarse_box["y1"], coarse_box["x0"]:coarse_box["x1"]]
        if candidate.size == 0:
            coarse_box, coarse_reason = None, "coarse crop 결과가 빈 이미지라 원본으로 폴백"
        else:
            sub_image = candidate

    if coarse_box is None:
        layout = _compute_team_layout(cv2, np, image)
        offset_x = offset_y = 0
    else:
        layout = _compute_team_layout(cv2, np, sub_image)
        offset_x, offset_y = coarse_box["x0"], coarse_box["y0"]
        layout = _translate_layout_coordinates(layout, offset_x, offset_y)

    layout.setdefault("_meta", {})["coarse_crop"] = {
        "coarse_crop_box": coarse_box,
        "coarse_crop_used": coarse_box is not None,
        "coarse_crop_reason": coarse_reason,
        "sub_image": sub_image,
    }
    return layout


def _compute_team_layout(cv2, np, image) -> Dict[str, Any]:
    """우리팀(파란)/상대팀(빨간) 패널을 contour로 검출하고 쌍을 선택한 뒤,
    각 팀의 team_box를 검증해 header_height 제외 후 5등분한 row_boxes를
    만든다. 두 팀의 행 높이가 서로 크게 다르면 작은 쪽만 완화 조건으로
    재탐색한다(_resolve_row_height_mismatch). 반환값은
    {"ally": {...}, "enemy": {...}, "_meta": {...}}이고, "_meta"는 사용자
    화면과 무관한 진단 전용 값(pair_evaluations)이라 analyze_scoreboard_image()
    가 admin_log를 만들기 전에 꺼내 쓴다."""
    width_img = image.shape[1]
    blue_candidates = _find_team_color_candidates(cv2, np, image, [BLUE_HUE_RANGE], BLUE_TEAM_COLOR_MIN_VALUE)
    red_candidates = _find_team_color_candidates(cv2, np, image, RED_HUE_RANGES, RED_TEAM_COLOR_MIN_VALUE)

    ally_candidate, enemy_candidate, selected_by, pair_score, pair_details, pair_evaluations, y_fallback_used = (
        _select_team_boxes(cv2, np, image, blue_candidates, red_candidates)
    )

    candidates_by_team = {"ally": blue_candidates, "enemy": red_candidates}
    picked_by_team = {"ally": ally_candidate, "enemy": enemy_candidate}
    other_by_team = {"ally": enemy_candidate, "enemy": ally_candidate}

    layout: Dict[str, Any] = {}
    for team in ("ally", "enemy"):
        other = other_by_team[team]
        layout[team] = _build_team_layout_entry(
            cv2, np, image, team, picked_by_team[team], candidates_by_team[team],
            other["box"] if other else None, selected_by[team],
            pair_score, pair_details, y_fallback_used.get(team, False), width_img,
        )

    layout, row_height_mismatch = _resolve_row_height_mismatch(
        cv2, np, image, layout, picked_by_team, candidates_by_team, width_img,
    )

    layout["_meta"] = {"pair_evaluations": pair_evaluations, "row_height_mismatch": row_height_mismatch}
    return layout


# ============================================================
# 3단계: 본인 판별 / 행별 영웅 아이콘 crop 빌드
# ============================================================

def _highlight_border_score(cv2, np, row_bgr) -> float:
    """행의 상/하단 가장자리에서 흰색에 가까운(밝고 채도 낮은) 픽셀 비율을
    구한다 — 닉네임/테두리 하이라이트를 감지하는 본인 판별 2순위 신호."""
    if row_bgr is None or row_bgr.size == 0:
        return 0.0
    hsv = cv2.cvtColor(row_bgr, cv2.COLOR_BGR2HSV)
    h = hsv.shape[0]
    if h >= 4:
        edges = np.concatenate([hsv[0:2, :, :].reshape(-1, 3), hsv[h - 2:h, :, :].reshape(-1, 3)], axis=0)
    else:
        edges = hsv.reshape(-1, 3)
    if edges.size == 0:
        return 0.0
    val = edges[:, 2].astype("float32")
    sat = edges[:, 1].astype("float32")
    near_white = (val >= 220) & (sat <= 30)
    return float(near_white.mean())


def _determine_self_row(cv2, np, row_images: List[Optional[Any]]) -> Tuple[Optional[int], str, List[Any], List[Optional[str]]]:
    """우리팀 5행 중 본인 행을 우선순위대로 판별한다. 애매하면 억지로 하나를
    고르지 않고 None(개인 피드백 생략)을 반환한다.
    1순위: 배경이 가장 밝게 강조된 행 (2등과의 밝기 차이가 충분히 클 때만 확정)
    2순위: 닉네임/테두리 하이라이트가 뚜렷한 행
    3순위: 그래도 불확실하면 "확인 필요" — 절대 첫 번째 행으로 단정하지 않는다."""
    valid_indices = [i for i, img in enumerate(row_images) if img is not None]
    is_me_values: List[Any] = [False] * len(row_images)
    is_me_reasons: List[Optional[str]] = [None] * len(row_images)

    if not valid_indices:
        return None, "확인 필요 (우리팀 행을 인식하지 못함)", is_me_values, is_me_reasons

    brightness = {i: float(cv2.cvtColor(row_images[i], cv2.COLOR_BGR2GRAY).mean()) for i in valid_indices}
    sorted_brightness = sorted(brightness.values(), reverse=True)
    best_i = max(valid_indices, key=lambda i: brightness[i])
    margin = sorted_brightness[0] - (sorted_brightness[1] if len(sorted_brightness) > 1 else 0)

    if margin >= SELF_ROW_BRIGHTNESS_MARGIN:
        is_me_values[best_i] = True
        is_me_reasons[best_i] = "밝은 행 강조"
        return best_i, "밝은 행 강조", is_me_values, is_me_reasons

    highlight = {i: _highlight_border_score(cv2, np, row_images[i]) for i in valid_indices}
    sorted_highlight = sorted(highlight.values(), reverse=True)
    h_best_i = max(valid_indices, key=lambda i: highlight[i])
    h_margin = sorted_highlight[0] - (sorted_highlight[1] if len(sorted_highlight) > 1 else 0)

    if highlight[h_best_i] >= HIGHLIGHT_BORDER_MIN and h_margin >= HIGHLIGHT_BORDER_MARGIN:
        is_me_values[h_best_i] = True
        is_me_reasons[h_best_i] = "닉네임/테두리 강조"
        return h_best_i, "닉네임/테두리 강조", is_me_values, is_me_reasons

    is_me_values[best_i] = "확인 필요"
    is_me_reasons[best_i] = "확인 필요"
    return None, "확인 필요", is_me_values, is_me_reasons


def _box_within(inner: Optional[Dict[str, int]], outer: Optional[Dict[str, int]]) -> bool:
    """hero_crop_box가 row_box 내부에 있는지 확인하는 안전장치."""
    if not inner or not outer:
        return False
    return (
        inner["x0"] >= outer["x0"] and inner["y0"] >= outer["y0"]
        and inner["x1"] <= outer["x1"] and inner["y1"] <= outer["y1"]
    )


def _build_team_rows(
    cv2, np, image, row_boxes: List[Optional[Dict[str, int]]], templates, team: str, team_box: Optional[Dict[str, int]],
) -> Tuple[List[Dict[str, Any]], int, Optional[int], str, List[Optional[Any]], List[Optional[Any]]]:
    """row_boxes(이미 헤더가 제외된 실제 플레이어 행 경계)로 5개 슬롯을 항상
    만든다. 역할은 고정 순서(ROW_ROLES/ROW_ROLE_CODES)로 배정하고, 영웅 아이콘
    비교 후보를 그 역할로 제한한다. row crop은 row_box의 x0:x1 안에서만 만들고,
    hero crop은 그 안에서 세로 중앙부 + 행 높이 기준 정사각형으로 다시 좁힌다.

    hero_crop_relative_x가 team_box 대비 지나치게 오른쪽이면 좌표 계산 오류로
    보고 매칭 자체를 시도하지 않는다. clamp 후 폭이 거의 사라지면(row_box 폭
    부족 등) 마찬가지로 매칭을 건너뛴다."""
    row_images = [
        image[rb["y0"]:rb["y1"], rb["x0"]:rb["x1"]] if rb else None
        for rb in row_boxes
    ]
    detected_count = sum(1 for img in row_images if img is not None)

    hero_crops: List[Optional[Any]] = []
    hero_crop_boxes: List[Optional[Dict[str, int]]] = []
    hero_results: List[Dict[str, Any]] = []
    relative_xs: List[Optional[float]] = []

    for i, (rb, row_img) in enumerate(zip(row_boxes, row_images)):
        role_code = ROW_ROLE_CODES[i]
        if row_img is None:
            hero_crops.append(None)
            hero_crop_boxes.append(None)
            hero_results.append(_empty_hero_result(role_code, "행 인식 실패(팀 배경색 영역을 찾지 못함)"))
            relative_xs.append(None)
            continue

        row_h_full = row_img.shape[0]
        trim_px = int(round(row_h_full * HERO_CROP_VERTICAL_TRIM))
        y0_local, y1_local = trim_px, row_h_full - trim_px
        if y1_local <= y0_local:
            y0_local, y1_local = 0, row_h_full
        row_center = row_img[y0_local:y1_local, :]

        row_h = row_center.shape[0]
        row_w = row_center.shape[1]
        requested_x0 = int(row_h * ROLE_ICON_WIDTH_RATIO) + int(row_h * HERO_ICON_X_OFFSET_RATIO)
        requested_x1 = requested_x0 + int(row_h * HERO_ICON_SIZE_RATIO)
        icon_x0 = max(0, min(requested_x0, row_w))
        icon_x1 = max(icon_x0, min(requested_x1, row_w))

        icon_region = row_center[:, icon_x0:icon_x1]
        # hero crop의 원본(리사이즈 전) 픽셀 크기 — 템플릿(64x64)보다 훨씬
        # 작으면 확대 과정에서 정보가 부족해 유사도가 떨어질 수 있다.
        logger.info(
            "[SCOREBOARD DIAG] icon crop native size team=%s row=%d role=%s size(w,h)=(%d,%d)",
            team, i + 1, role_code, icon_x1 - icon_x0, y1_local - y0_local,
        )
        hero_crops.append(icon_region)
        hero_crop_box = {
            "x0": rb["x0"] + icon_x0, "y0": rb["y0"] + y0_local,
            "x1": rb["x0"] + icon_x1, "y1": rb["y0"] + y1_local,
        }
        hero_crop_boxes.append(hero_crop_box)

        relative_x = None
        if team_box:
            team_width = team_box["x1"] - team_box["x0"]
            if team_width > 0:
                relative_x = round((hero_crop_box["x0"] - team_box["x0"]) / team_width, 3)
        relative_xs.append(relative_x)

        requested_width = requested_x1 - requested_x0
        clamped_width = icon_x1 - icon_x0
        if clamped_width < MIN_HERO_CROP_PIXELS:
            result = _empty_hero_result(
                role_code,
                f"hero_crop_box가 clamp되어 폭이 거의 사라짐"
                f"(요청 폭 {requested_width}px → 실제 폭 {clamped_width}px, row_box 폭 부족 의심)",
            )
            result["crop_size_before"] = [clamped_width, y1_local - y0_local]
            result["native_size"] = [clamped_width, y1_local - y0_local]
        elif relative_x is not None and relative_x > HERO_CROP_MAX_RELATIVE_X:
            result = _empty_hero_result(
                role_code,
                f"좌표 오류 의심(영웅 아이콘이 있을 수 없는 위치, hero_crop_relative_x={relative_x})",
            )
            result["crop_size_before"] = [clamped_width, y1_local - y0_local]
            result["native_size"] = [clamped_width, y1_local - y0_local]
        else:
            matching_region, upscaled, native_size = _upscale_icon_if_small(cv2, np, icon_region)
            if upscaled:
                logger.info(
                    "[SCOREBOARD DIAG] icon crop upscaled team=%s row=%d role=%s native=%s -> %s (INTER_CUBIC)",
                    team, i + 1, role_code, native_size, matching_region.shape[1::-1],
                )
            result = _match_hero_icon(cv2, np, matching_region, templates, role_code)
            result["upscaled"] = upscaled
            result["native_size"] = list(native_size) if native_size else None
        hero_results.append(result)

    self_row_idx: Optional[int] = None
    self_reason = "확인 필요 (상대팀은 본인 판별 대상이 아님)"
    is_me_values: List[Any] = [False] * len(row_images)
    is_me_reasons: List[Optional[str]] = [None] * len(row_images)
    if team == "ally":
        self_row_idx, self_reason, is_me_values, is_me_reasons = _determine_self_row(cv2, np, row_images)

    entries = []
    for i, rb in enumerate(row_boxes):
        hr = hero_results[i]
        hero_crop_box = hero_crop_boxes[i]
        entry: Dict[str, Any] = {
            "row_index": i + 1,
            "team": team,
            "role": ROW_ROLES[i],
            "role_code": ROW_ROLE_CODES[i],
            "hero": hr["hero"],
            "hero_confidence": hr["score"],
            "hero_confidence_label": hr["confidence_label"],
            "hero_second_score": hr["second_score"],
            "hero_source": hr["source"],
            "hero_unknown_reason": hr["reason"],
            "hero_top_matches": hr["top_matches"],
            "hero_pre_role_top_matches": hr["pre_role_top_matches"],
            "hero_post_role_top_matches": hr["post_role_top_matches"],
            "hero_pre_role_best_score": hr["pre_role_best_score"],
            "hero_post_role_best_score": hr["post_role_best_score"],
            "hero_rank1": hr["rank1_hero"],
            "hero_rank2": hr["rank2_hero"],
            "hero_raw_gray_score": hr["raw_gray_score"],
            "hero_blurred_gray_score": hr["blurred_gray_score"],
            "hero_clahe_gray_score": hr["clahe_gray_score"],
            "hero_edge_score": hr["edge_score"],
            "hero_baseline_score": hr["baseline_score"],
            "hero_final_score": hr["final_score"],
            "hero_template_path": hr["template_path"],
            "hero_crop_size_before": hr["crop_size_before"],
            "hero_crop_size_after": hr["crop_size_after"],
            "hero_role_fallback_used": hr["role_fallback_used"],
            "hero_color_shortlist": hr["color_shortlist"],
            "hero_color_prefilter_applied": hr["color_prefilter_applied"],
            "hero_upscaled": hr["upscaled"],
            "hero_native_size": hr["native_size"],
            "row_box": rb,
            "hero_crop_box": hero_crop_box,
            "hero_crop_box_within_row_box": _box_within(hero_crop_box, rb),
            "hero_crop_relative_x": relative_xs[i],
            "row_crop_path": None,  # turn_id가 있으면 analyze_scoreboard_image()가 저장 후 채운다.
            "crop_path": None,      # 위와 동일(hero crop 경로).
            "kda": {"kill": None, "death": None, "assist": None},
            "damage": None,
            "healing": None,
            "mitigation": None,
        }
        if team == "ally":
            entry["is_me"] = is_me_values[i]
            entry["is_me_reason"] = is_me_reasons[i]
        else:
            entry["is_me"] = False
            entry["is_me_reason"] = None
        entries.append(entry)

    return entries, detected_count, self_row_idx, self_reason, row_images, hero_crops


# ============================================================
# 4단계: 숫자(K/D/A, 피해량, 치유량, 경감량) 인식 — Gemini
# ============================================================

NUMBERS_PROMPT_TEMPLATE = """이 이미지는 오버워치2 TAB 점수판이다.
화면 위쪽부터 파란 배경의 우리팀 {my_count}명이 있고, 그 아래 화면 아래쪽에
빨간 배경의 상대팀 {enemy_count}명이 있다.

각 플레이어 행은 왼쪽부터 순서대로 [역할 아이콘, 영웅 아이콘, 닉네임, 처치,
도움, 죽음, 피해량, 치유량, 경감량] 9개 칸으로 구성된다. 이 중 처치(kill)/
도움(assist)/죽음(death)/피해량(damage)/치유량(healing)/경감량(mitigation)
6개 숫자 칸만 읽어라 — 역할/영웅/닉네임은 이미 다른 방식으로 파악했으니
무시해라.

규칙:
- 쉼표는 제거하고 정수로 반환해라.
- 실제로 값이 0이라 빈칸이나 '-'로 표시된 경우는 0으로 채워라.
- 글자가 가려지거나 흐려서 값을 전혀 읽을 수 없으면 그 필드는 반드시 JSON
  null로 채워라(추측해서 숫자를 만들어내지 마라).

화면 위쪽부터 아래쪽까지 순서대로 총 {total}개 행 각각에 대해 아래 JSON 배열
형식으로만 답해라. 다른 텍스트, 설명, 코드 펜스(```) 없이 배열 하나만 출력해라.

[
  {{"kill": 0, "assist": 0, "death": 0, "damage": 0, "healing": 0, "mitigation": 0}}
]

배열 길이는 반드시 {total}개여야 한다(우리팀 {my_count}개 다음 상대팀
{enemy_count}개 순서)."""


def _clean_number(value: Any) -> Optional[int]:
    """Gemini가 문자열로 반환했을 수 있는 숫자(쉼표 포함 등)를 정수로 정리한다.
    읽지 못한 값(None, 빈 문자열, 숫자를 찾을 수 없는 문자열)은 None(=null)으로 통일한다."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in ("확인 필요", "null", "None", "-", "?"):
        return None
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    return int(digits)


def _read_numbers_with_gemini(llm, image_bytes: bytes, mime_type: str, my_count: int, enemy_count: int) -> List[Dict[str, Any]]:
    total = my_count + enemy_count
    if total == 0:
        return []

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    from langchain_core.messages import HumanMessage

    prompt = NUMBERS_PROMPT_TEMPLATE.format(my_count=my_count, enemy_count=enemy_count, total=total)
    message = HumanMessage(content=[
        {"type": "text", "text": prompt},
        {"type": "file", "source_type": "base64", "mime_type": mime_type, "data": image_b64},
    ])

    try:
        raw_text = call_llm_text(llm, [message])
    except Exception as exc:
        raise ScoreboardAnalysisError(f"숫자 인식 요청이 실패했습니다: {exc}") from exc

    parsed = safe_json_loads(raw_text, default=None)
    if not isinstance(parsed, list):
        logger.warning("[SCOREBOARD] 숫자 인식 JSON 파싱 실패. raw=%s", raw_text)
        raise ScoreboardAnalysisError("점수판 숫자를 읽지 못했습니다.")

    stats = []
    for item in parsed:
        if not isinstance(item, dict):
            item = {}
        stats.append({
            "kill": _clean_number(item.get("kill")),
            "death": _clean_number(item.get("death")),
            "assist": _clean_number(item.get("assist")),
            "damage": _clean_number(item.get("damage")),
            "healing": _clean_number(item.get("healing")),
            "mitigation": _clean_number(item.get("mitigation")),
        })

    while len(stats) < total:
        stats.append({k: None for k in ("kill", "death", "assist", "damage", "healing", "mitigation")})
    return stats[:total]


# ============================================================
# 5단계: 관리자 전용 진단 로그 (사용자에게는 노출하지 않음)
# ============================================================

def _build_admin_log(
    my_team: List[Dict[str, Any]], enemy_team: List[Dict[str, Any]],
    ally_detected_count: int, enemy_detected_count: int,
    self_row_idx: Optional[int], self_reason: str,
    original_image_path: Optional[str],
    layout: Dict[str, Any],
    pair_evaluations: List[Dict[str, Any]],
    coarse_crop_box: Optional[Dict[str, int]] = None,
    coarse_crop_used: bool = False,
    coarse_crop_reason: Optional[str] = None,
    coarse_crop_image_path: Optional[str] = None,
    original_image_shape: Optional[Tuple[int, int]] = None,
) -> Dict[str, Any]:
    """사용자 응답에는 절대 포함하지 않고 ChatLog.metadata에만 저장하는
    진단 정보. team_layout에는 팀 패널 후보/쌍 선택/검증 결과를, hero_rows에는
    행별 역할 제한 전/후 후보와 raw/blurred/clahe/edge/최종 점수를 담는다.
    team_layout의 좌표는 coarse crop 적용 여부와 무관하게 항상 원본 이미지
    기준이다. coarse_crop_box가 원본 크기와 정확히 같으면(점수판만 캡처한
    경우 흔함) 실제로는 아무것도 좁히지 않은 no-op이라는 뜻이다."""
    self_entry = my_team[self_row_idx] if self_row_idx is not None else None

    team_layout = {}
    for team in ("ally", "enemy"):
        data = layout.get(team) or {}
        team_layout[team] = {
            "team_box": data.get("team_box"),
            "header_height": data.get("header_height"),
            "header_detection": data.get("header_detection"),
            "player_area_box": data.get("player_area_box"),
            "mask_candidate_boxes": data.get("mask_candidate_boxes"),
            "selected_candidate_box": data.get("selected_candidate_box"),
            "selected_candidate_score": data.get("selected_candidate_score"),
            "pair_score": data.get("pair_score"),
            "pair_details": data.get("pair_details"),
            "selected_by": data.get("selected_by"),
            "x_fallback_used": data.get("x_fallback_used"),
            "y_fallback_used": data.get("y_fallback_used"),
            "layout_validation_ok": data.get("layout_validation_ok"),
            "layout_validation_reason": data.get("layout_validation_reason"),
            "expected_row_height": data.get("expected_row_height"),
            "row_heights": data.get("row_heights"),
        }

    hero_rows = [
        {
            "team": e["team"],
            "row_index": e["row_index"],
            "role": e["role_code"],
            "row_box": e["row_box"],
            "hero_crop_box": e["hero_crop_box"],
            "hero_crop_box_within_row_box": e["hero_crop_box_within_row_box"],
            "hero_crop_relative_x": e["hero_crop_relative_x"],
            "row_crop_path": e["row_crop_path"],
            "crop_path": e["crop_path"],
            "hero": e["hero"],
            "confidence_label": e["hero_confidence_label"],
            "best_score": e["hero_confidence"],
            "second_score": e["hero_second_score"],
            "top_matches": e["hero_top_matches"],
            "unknown_reason": e["hero_unknown_reason"],
            "pre_role_top_matches": e["hero_pre_role_top_matches"],
            "post_role_top_matches": e["hero_post_role_top_matches"],
            "pre_role_best_score": e["hero_pre_role_best_score"],
            "post_role_best_score": e["hero_post_role_best_score"],
            "rank1_hero": e["hero_rank1"],
            "rank2_hero": e["hero_rank2"],
            "score_gap": round(e["hero_confidence"] - e["hero_second_score"], 3),
            "raw_gray_score": e["hero_raw_gray_score"],
            "blurred_gray_score": e["hero_blurred_gray_score"],
            "clahe_gray_score": e["hero_clahe_gray_score"],
            "edge_score": e["hero_edge_score"],
            "baseline_score": e["hero_baseline_score"],
            "final_score": e["hero_final_score"],
            "template_path": e["hero_template_path"],
            "crop_size_before": e["hero_crop_size_before"],
            "crop_size_after": e["hero_crop_size_after"],
            "role_fallback_used": e["hero_role_fallback_used"],
            "color_shortlist": e["hero_color_shortlist"],
            "color_prefilter_applied": e["hero_color_prefilter_applied"],
            "upscaled": e["hero_upscaled"],
            "native_size": e["hero_native_size"],
        }
        for e in (my_team + enemy_team)
    ]
    hero_recognition_failed = [row for row in hero_rows if row["hero"] == "unknown"]

    missing_stats = []
    for e in (my_team + enemy_team):
        missing = [
            field for field, value in (
                ("kill", e["kda"]["kill"]), ("assist", e["kda"]["assist"]), ("death", e["kda"]["death"]),
                ("damage", e["damage"]), ("healing", e["healing"]), ("mitigation", e["mitigation"]),
            ) if value is None
        ]
        if missing:
            missing_stats.append({"team": e["team"], "row_index": e["row_index"], "role": e["role_code"], "fields": missing})

    coarse_crop_is_noop = False
    if coarse_crop_used and coarse_crop_box and original_image_shape:
        h, w = original_image_shape[:2]
        coarse_crop_is_noop = (
            coarse_crop_box.get("x0") == 0 and coarse_crop_box.get("y0") == 0
            and coarse_crop_box.get("x1") == w and coarse_crop_box.get("y1") == h
        )

    return {
        "original_image_path": original_image_path,
        "original_image_shape": list(original_image_shape) if original_image_shape else None,
        "coarse_crop_box": coarse_crop_box,
        "coarse_crop_used": coarse_crop_used,
        "coarse_crop_is_noop": coarse_crop_is_noop,
        "coarse_crop_fallback_reason": coarse_crop_reason,
        "coarse_crop_image_path": coarse_crop_image_path,
        "team_layout": team_layout,
        "pair_evaluations": pair_evaluations,
        "ally_detected_count": ally_detected_count,
        "enemy_detected_count": enemy_detected_count,
        "self_row_index": (self_row_idx + 1) if self_row_idx is not None else None,
        "self_reason": self_reason,
        "self_hero": self_entry["hero"] if self_entry else None,
        "self_determination_failed": self_row_idx is None,
        "hero_icon_method": HERO_ICON_METHOD_LABEL,
        "hero_rows": hero_rows,
        "hero_recognition_failed": hero_recognition_failed,
        "missing_stats": missing_stats,
    }


# ============================================================
# 6단계: 코치 피드백 생성 — Gemini (역할/행 기준, 비난 금지, 불확실 정보 단정 금지)
# ============================================================

def _format_team_for_feedback(team: List[Dict[str, Any]]) -> str:
    lines = []
    for entry in team:
        kda = entry["kda"]
        all_empty = entry["hero"] == "unknown" and all(
            v is None for v in [kda["kill"], kda["death"], kda["assist"], entry["damage"], entry["healing"], entry["mitigation"]]
        )
        if all_empty:
            continue
        tag = " (본인)" if entry.get("is_me") is True else ""
        lines.append(
            f"- {entry['role']} {entry['hero']}{tag}: "
            f"{_fmt_num(kda['kill'])}/{_fmt_num(kda['death'])}/{_fmt_num(kda['assist'])} K/D/A, "
            f"피해 {_fmt_num(entry.get('damage'))}, 치유 {_fmt_num(entry.get('healing'))}, "
            f"경감 {_fmt_num(entry.get('mitigation'))}"
        )
    return "\n".join(lines) if lines else "정보 없음"


TEAM_FEEDBACK_PROMPT_TEMPLATE = """너는 오버워치2 코치다. 방금 TAB 점수판에서
추출한 이번 판 결과를 보고 코칭 피드백을 작성해라.

우리팀(행 순서: 탱커, 딜러, 딜러, 힐러, 힐러):
{my_team_text}

상대팀:
{enemy_team_text}

{opponent_instruction}
{recognition_instruction}

규칙:
- 특정 닉네임이나 개인을 비난하듯 표현하지 말고 "탱커 라인", "딜러진",
  "힐러진"처럼 역할 기준으로 표현해라. "누가 못했다"처럼 공격적으로 쓰지 말고
  "어떤 역할의 기여가 낮았다", "어떤 라인이 압박을 많이 받았다"처럼 코칭
  톤으로 써라.
- 확인되지 않은 정보를 사실처럼 단정하지 말고, 실제로 제공된 스탯 범위
  안에서만 판단해라. 승률/티어 같은 통계는 언급하지 마라.
- 스탯 항목마다 그 영웅이 애초에 그 수치를 낼 수 있는 스킬을 가졌는지부터
  너의 오버워치2 지식으로 판단해라. 힐 전담형 영웅의 낮은 피해량, 피해를
  막거나 흡수하는 스킬(방벽·보호막·벽 등)이 없는 영웅의 경감량 0은 전부
  구조적으로 정상인 수치이니 약점처럼 지적하지 마라. 특히 경감량은 실제로
  그런 스킬을 쓴 결과로만 기록되는 값이므로, 그런 스킬이 없는 영웅에게
  "경감량이 낮으니 어떤 스킬을 더 활용했어야 한다"처럼 확인되지 않은
  스킬 운용을 지어내지 마라.
- 치유량 대비 피해량 비율처럼 팀 내부 숫자만 보고 판단하지 말고, 상대팀에서
  같은 역할 영웅의 실제 수치와 비교해서 상대적으로 높은지 낮은지를 근거로
  삼아라. 상대 같은 역할보다 치유량과 피해량이 모두 앞선다면 그건 약점이
  아니라 강점이니 아쉬운 점으로 지적하지 마라. 단, 이 비교 규칙은 두 힐러
  모두 스스로 의미 있는 피해를 낼 수 있는 킷일 때만 적용해라. 메르시처럼
  피해 증폭(우클릭) 말고는 사실상 자체 공격 수단이 없는 힐러는, 상대
  힐러가 딜을 얼마나 냈든 상관없이 피해량 항목 자체를 비교·지적 대상에서
  제외해라 — "상대 힐러는 공격적으로 운영했는데 이쪽은 피해 기여가 없다"
  같은 문장도 메르시에게는 쓰지 마라. 단, 치유량은 예외가 아니다 — 이런
  힐러도 치유량이 같은 역할 상대보다 유의미하게 낮으면 그건 정상적인 약점
  지적 대상이다.
- 경감량도 같은 원리로 판단해라: 방벽·보호막이나 디플렉트/매트릭스처럼
  피해를 흡수·차단하는 스킬이 있는 탱커는 그런 스킬이 없는 탱커보다
  경감량이 구조적으로 훨씬 높게 나온다. 아군과 상대 탱커의 킷이 다르면
  경감량 차이만으로 어느 쪽이 못했다고 판단하지 마라.
- 회복 자원을 소모해 채워야 하는 킷(자원이 바닥나면 다시 찰 때까지 치유를
  못 하는 힐러 등)을 하는 영웅은, 치유량/피해량 총합만 보지 말고 두
  수치를 그 영웅의 자원 관리 메커니즘에 맞게 배분했는지도 함께 판단해라.
- 영웅의 고유 특성(예: 생존력이 높다, 기동성이 좋다)을 근거로 언급할 때,
  그 특성 덕분에 나온 결과(예: 데스가 적음)를 "~인 영웅임에도" 처럼
  특성과 결과가 서로 모순되는 것처럼 쓰지 마라. 특성이 원인이라면
  "~답게", "~덕분에"처럼 인과관계가 맞는 표현만 써라(예: "생존력이 뛰어난
  영웅답게 0데스를 유지하며..." O, "생존력이 뛰어난 영웅임에도 0데스" X).
- 마크다운 문법(**, #, - 등)은 쓰지 마라. 문단 서술로 작성해라.
- 각 항목은 2~4문장 이내로 간결하게 작성해라.

아래 JSON 형식으로만 답해라. 다른 텍스트, 설명, 코드펜스 없이 객체 하나만
출력해라.

{{
  "overview": "우리팀과 상대팀의 처치/죽음/피해량/치유량/경감량을 비교한 전체 교전 흐름 요약",
  "good_points": "우리팀에서 좋았던 점(높은 피해량/치유량, 낮은 데스, 높은 경감량 등)",
  "concerns": "아쉬웠던 점(특정 역할군의 데스가 높거나 피해량/치유량이 낮은 경우, 역할 기준으로만)",
  "next_tips": "다음 판 개선 방향(탱커/딜러/힐러 관점에서 바로 적용 가능한 운영 팁)"
}}"""


def _generate_team_feedback(llm, my_team: List[Dict[str, Any]], enemy_team: List[Dict[str, Any]], enemy_ok: bool, low_hero_recognition: bool) -> Dict[str, str]:
    opponent_instruction = (
        "상대 조합 정보가 충분히 인식되지 않았다. 상대 영웅 구성에 대한 분석/추측은 "
        "하지 말고, 처치/데스/피해량/치유량/경감량 등 스탯 비교 중심으로만 작성해라."
        if not enemy_ok else
        "상대 조합 정보도 참고해서 조언해라."
    )
    recognition_instruction = (
        "영웅 이름이 여러 자리에서 unknown으로 확인되지 않았다. 영웅 조합이나 "
        "상성에 대한 언급은 최소화하고, K/D/A·피해량·치유량·경감량 등 스탯 "
        "비교를 중심으로 분석해라."
        if low_hero_recognition else ""
    )
    prompt = TEAM_FEEDBACK_PROMPT_TEMPLATE.format(
        my_team_text=_format_team_for_feedback(my_team),
        enemy_team_text=_format_team_for_feedback(enemy_team) if enemy_ok else "인식 실패/정보 부족",
        opponent_instruction=opponent_instruction,
        recognition_instruction=recognition_instruction,
    )
    try:
        raw = call_llm_text(llm, prompt)
    except Exception as exc:
        logger.warning("[SCOREBOARD] 팀 피드백 생성 실패: %s", exc)
        raw = ""

    parsed = safe_json_loads(raw, default={}) if raw else {}
    if not isinstance(parsed, dict):
        parsed = {}

    return {
        "overview": (parsed.get("overview") or "").strip() or "이번 판의 교전 흐름을 판단하기에 정보가 부족합니다.",
        "good_points": (parsed.get("good_points") or "").strip() or "확인된 정보 안에서 뚜렷한 강점을 판단하기 어렵습니다.",
        "concerns": (parsed.get("concerns") or "").strip() or "확인된 정보 안에서 뚜렷한 약점을 판단하기 어렵습니다.",
        "next_tips": (parsed.get("next_tips") or "").strip() or "다음 판에는 좀 더 선명한 점수판 스크린샷으로 다시 확인해보세요.",
    }


PERSONAL_FEEDBACK_PROMPT_TEMPLATE = """너는 오버워치2 코치다. 본인은 이번 판에
{role}({hero})로 플레이했고 다음 스탯을 기록했다.

{stat_line}
{enemy_counterpart_line}

본인 스탯을 중심으로 잘한 점과 아쉬운 점, 다음 판에 바로 적용할 팁 1~2가지를
문단 서술로 3~4문장 이내로 작성해라. 마크다운 문법은 쓰지 말고, 확인되지 않은
정보를 단정하지 마라.

판단 기준:
- 스탯 항목마다 그 영웅이 애초에 그 수치를 낼 수 있는 스킬을 가졌는지부터
  너의 오버워치2 지식으로 판단해라. 힐 전담형 영웅의 낮은 피해량, 피해를
  막거나 흡수하는 스킬(방벽·보호막·벽 등)이 없는 영웅의 경감량 0은 전부
  구조적으로 정상인 수치이니 약점으로 지적하지 마라. 특히 경감량은 실제로
  그런 스킬을 쓴 결과로만 기록되는 값이므로, 그런 스킬이 없는 영웅에게
  "경감량이 낮으니 어떤 스킬을 더 활용했어야 한다"처럼 확인되지 않은
  스킬 운용을 지어내지 마라.
- 위에 상대팀 같은 역할 스탯이 주어졌다면, 치유량 대비 피해량 같은 내부 비율
  만으로 판단하지 말고 그 상대 수치와 비교해서 실제로 상대적으로 낮은지
  판단해라. 상대보다 앞서는 수치를 약점처럼 지적하지 마라. 단, 본인이
  메르시처럼 피해 증폭(우클릭) 말고는 사실상 자체 공격 수단이 없는
  힐러라면, 상대 힐러가 딜을 얼마나 냈든 상관없이 피해량 항목은 비교·
  지적 대상에서 제외해라(치유량은 예외가 아니다 — 상대보다 유의미하게
  낮으면 그건 정상적인 약점 지적 대상이다).
- 경감량도 같은 원리로 판단해라: 본인이나 상대가 방벽·보호막이나 디플렉트/
  매트릭스처럼 피해를 흡수·차단하는 스킬을 쓰는 탱커라면, 그런 스킬이
  없는 탱커보다 경감량이 구조적으로 훨씬 높게 나온다 — 킷이 다른 탱커와의
  경감량 차이만으로 못했다고 판단하지 마라.
- 본인이 회복 자원을 소모해 채워야 하는 킷(자원이 바닥나면 다시 찰 때까지
  치유를 못 하는 힐러 등)이라면, 치유량/피해량 총합만 보지 말고 두
  수치를 그 영웅의 자원 관리 메커니즘에 맞게 배분했는지도 함께 판단해라.
- 영웅의 고유 특성(예: 생존력이 높다, 기동성이 좋다)을 근거로 언급할 때,
  그 특성 덕분에 나온 결과(예: 데스가 적음)를 "~인 영웅임에도" 처럼
  특성과 결과가 서로 모순되는 것처럼 쓰지 마라. 특성이 원인이라면
  "~답게", "~덕분에"처럼 인과관계가 맞는 표현만 써라."""


def _self_feedback_eligible(self_row_idx: Optional[int], my_team: List[Dict[str, Any]]) -> bool:
    """본인 행/영웅/스탯이 모두 확인된 경우에만 개인 피드백을 만든다."""
    if self_row_idx is None:
        return False
    entry = my_team[self_row_idx]
    if entry["hero"] == "unknown":
        return False
    return any(
        v is not None for v in [
            entry["kda"]["kill"], entry["kda"]["death"], entry["kda"]["assist"],
            entry["damage"], entry["healing"], entry["mitigation"],
        ]
    )


def _generate_personal_feedback(
    llm, self_entry: Dict[str, Any], enemy_counterpart: Optional[Dict[str, Any]] = None,
) -> str:
    kda = self_entry["kda"]
    stat_line = (
        f"K/D/A {_fmt_num(kda['kill'])}/{_fmt_num(kda['death'])}/{_fmt_num(kda['assist'])}, "
        f"피해량 {_fmt_num(self_entry.get('damage'))}, 치유량 {_fmt_num(self_entry.get('healing'))}, "
        f"경감량 {_fmt_num(self_entry.get('mitigation'))}"
    )
    enemy_counterpart_line = ""
    if enemy_counterpart is not None:
        enemy_kda = enemy_counterpart["kda"]
        enemy_counterpart_line = (
            f"참고로 상대팀 같은 역할({enemy_counterpart['hero']})의 이번 판 스탯: "
            f"K/D/A {_fmt_num(enemy_kda['kill'])}/{_fmt_num(enemy_kda['death'])}/{_fmt_num(enemy_kda['assist'])}, "
            f"피해량 {_fmt_num(enemy_counterpart.get('damage'))}, "
            f"치유량 {_fmt_num(enemy_counterpart.get('healing'))}, "
            f"경감량 {_fmt_num(enemy_counterpart.get('mitigation'))}"
        )
    prompt = PERSONAL_FEEDBACK_PROMPT_TEMPLATE.format(
        role=self_entry["role"], hero=self_entry["hero"], stat_line=stat_line,
        enemy_counterpart_line=enemy_counterpart_line,
    )
    try:
        return call_llm_text(llm, prompt).strip()
    except Exception as exc:
        logger.warning("[SCOREBOARD] 개인 피드백 생성 실패: %s", exc)
        return ""


# ============================================================
# 7단계: 사용자에게 보여줄 최종 출력(표 + 코치 피드백만)
# ============================================================

def _team_table(entries: List[Dict[str, Any]]) -> str:
    headers = ["역할", "영웅", "K/D/A", "피해량", "치유량", "경감량"]
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for e in entries:
        kda = e["kda"]
        row = [
            e["role"], e["hero"],
            f"{_fmt_num(kda['kill'])}/{_fmt_num(kda['death'])}/{_fmt_num(kda['assist'])}",
            _fmt_num(e["damage"]), _fmt_num(e["healing"]), _fmt_num(e["mitigation"]),
        ]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _build_stat_dict(team: List[Dict[str, Any]]) -> Dict[str, Any]:
    """chatbot_graph의 my_team_stats/enemy_stats 포맷(영웅명 -> kills/assists/
    deaths/damage/healing)으로 변환한다. 영웅 인식 실패(unknown)나 숫자 인식
    실패 행은 제외한다."""
    result: Dict[str, Any] = {}
    for e in team:
        if e["hero"] == "unknown":
            continue
        kda = e["kda"]
        if all(v is None for v in [kda["kill"], kda["death"], kda["assist"], e.get("damage"), e.get("healing")]):
            continue
        result[e["hero"]] = {
            "kills": kda["kill"],
            "deaths": kda["death"],
            "assists": kda["assist"],
            "damage": e.get("damage"),
            "healing": e.get("healing"),
        }
    return result


def build_scoreboard_report(
    my_team: List[Dict[str, Any]], enemy_team: List[Dict[str, Any]],
    team_feedback: Dict[str, str], personal_feedback: Optional[str],
) -> str:
    """사용자에게 그대로 보여줄 마크다운. 인식 실패 목록/본인 판별 근거/
    영웅 인식 방식 같은 진단 문구는 절대 포함하지 않는다 — 그런 정보는
    admin_log에만 담긴다."""
    lines = [
        "### 우리팀",
        _team_table(my_team),
        "",
        "### 상대팀",
        _team_table(enemy_team),
        "",
        "### 코치 피드백",
        "",
        "1. 전체 흐름",
        team_feedback["overview"],
        "",
        "2. 우리팀에서 좋았던 점",
        team_feedback["good_points"],
        "",
        "3. 아쉬웠던 점",
        team_feedback["concerns"],
        "",
        "4. 다음 판 개선 방향",
        team_feedback["next_tips"],
    ]
    if personal_feedback:
        lines += ["", "### 개인 피드백", personal_feedback]
    return "\n".join(lines)


# ============================================================
# 전체 파이프라인
# ============================================================

def analyze_scoreboard_image(image_bytes: bytes, mime_type: str = "image/png", turn_id: Optional[str] = None) -> Dict[str, Any]:
    """TAB 점수판 스크린샷(bytes)을 분석해 "report"(사용자에게 보여줄 표+
    피드백 마크다운)와 "admin_log"(관리자 전용 진단 정보) dict를 반환한다.
    turn_id를 넘기면 원본 이미지와 행별 row/hero crop을
    logs/scoreboard_debug/{turn_id}/에 저장하고 경로를 admin_log에 담는다."""
    cv2, np = _cv2_np()

    array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        raise ScoreboardAnalysisError("이미지를 읽을 수 없습니다.")
    # 디코딩된 원본 해상도를 남겨 인식 실패 시 원인 파악에 참고한다.
    logger.info(
        "[SCOREBOARD DIAG] decoded image shape=%s (h,w,c), bytes=%d, mime=%s",
        image.shape, len(image_bytes), mime_type,
    )

    templates = _load_hero_icon_templates(cv2, np)
    layout = _compute_team_layout_with_coarse_crop(cv2, np, image)
    meta = layout.pop("_meta", {})
    pair_evaluations = meta.get("pair_evaluations", [])
    coarse_crop_meta = meta.get("coarse_crop") or {}
    coarse_crop_box = coarse_crop_meta.get("coarse_crop_box")
    coarse_crop_used = bool(coarse_crop_meta.get("coarse_crop_used"))
    coarse_crop_reason = coarse_crop_meta.get("coarse_crop_reason")
    coarse_crop_image = coarse_crop_meta.get("sub_image")
    # 1단계(coarse) 검출 결과를 남겨, sub_image가 원본 대비 얼마나 좁혀졌는지
    # 인식 실패 시 참고할 수 있게 한다.
    logger.info(
        "[SCOREBOARD DIAG] coarse_crop_used=%s coarse_crop_box=%s coarse_crop_reason=%s sub_image_shape=%s",
        coarse_crop_used, coarse_crop_box, coarse_crop_reason,
        coarse_crop_image.shape if coarse_crop_image is not None else None,
    )

    # layout["ally"/"enemy"]의 team_box/row_boxes는 이미 원본 이미지 기준
    # 절대 좌표로 변환돼 있으므로(_translate_layout_coordinates), 아래
    # _build_team_rows()에는 sub_image가 아니라 항상 원본 image를 넘긴다.
    my_team, ally_detected_count, self_row_idx, self_reason, ally_row_crops, ally_hero_crops = _build_team_rows(
        cv2, np, image, layout["ally"]["row_boxes"], templates, team="ally", team_box=layout["ally"]["team_box"],
    )
    enemy_team, enemy_detected_count, _, _, enemy_row_crops, enemy_hero_crops = _build_team_rows(
        cv2, np, image, layout["enemy"]["row_boxes"], templates, team="enemy", team_box=layout["enemy"]["team_box"],
    )

    if turn_id:
        debug_paths = _save_debug_images(
            cv2, turn_id, ally_row_crops, ally_hero_crops, enemy_row_crops, enemy_hero_crops,
        )
        for i, entry in enumerate(my_team):
            entry["row_crop_path"] = debug_paths.get(f"ally_{i + 1}_row")
            entry["crop_path"] = debug_paths.get(f"ally_{i + 1}_hero")
        for i, entry in enumerate(enemy_team):
            entry["row_crop_path"] = debug_paths.get(f"enemy_{i + 1}_row")
            entry["crop_path"] = debug_paths.get(f"enemy_{i + 1}_hero")

    enemy_heroes_identified = sum(1 for e in enemy_team if e["hero"] != "unknown")
    enemy_ok = enemy_detected_count >= 3 and enemy_heroes_identified >= 2

    total_unknown = sum(1 for e in (my_team + enemy_team) if e["hero"] == "unknown")
    low_hero_recognition = total_unknown >= (PLAYERS_PER_TEAM * 2) * LOW_HERO_RECOGNITION_RATIO

    if ENABLE_GEMINI_STATS_AND_FEEDBACK:
        try:
            _, _, llm = get_chatbot_components()
        except Exception as exc:
            raise ScoreboardAnalysisError(f"모델을 불러오지 못했습니다: {exc}") from exc

        stats = _read_numbers_with_gemini(llm, image_bytes, mime_type, len(my_team), len(enemy_team))
        for entry, stat in zip(my_team + enemy_team, stats):
            entry["kda"] = {"kill": stat["kill"], "death": stat["death"], "assist": stat["assist"]}
            entry["damage"] = stat["damage"]
            entry["healing"] = stat["healing"]
            entry["mitigation"] = stat["mitigation"]

        team_feedback = _generate_team_feedback(llm, my_team, enemy_team, enemy_ok, low_hero_recognition)

        self_known = _self_feedback_eligible(self_row_idx, my_team)
        enemy_counterpart = None
        if self_known and enemy_ok and self_row_idx < len(enemy_team):
            # 행 순서가 [탱커, 딜러, 딜러, 힐러, 힐러]로 고정이라 같은 행
            # 인덱스가 곧 같은 역할이다.
            candidate = enemy_team[self_row_idx]
            if candidate["hero"] != "unknown":
                enemy_counterpart = candidate
        personal_feedback = (
            _generate_personal_feedback(llm, my_team[self_row_idx], enemy_counterpart)
            if self_known else None
        )
    else:
        # ENABLE_GEMINI_STATS_AND_FEEDBACK=False — Gemini를 아예 호출하지
        # 않는다. my_team/enemy_team의 kda/damage/healing/mitigation은
        # 이미 None으로 초기화돼 있어(_build_team_rows) 표에는 그대로
        # "확인 필요"로 나간다.
        team_feedback = {
            "overview": "영웅 인식(OpenCV) 정확도 개선 작업 중이라 코치 피드백은 잠시 꺼둔 상태입니다.",
            "good_points": "-",
            "concerns": "-",
            "next_tips": "-",
        }
        self_known = False
        personal_feedback = None

    # my_team/enemy_team의 kda/damage/... 값이 최종 확정된 뒤(Gemini가 채웠든
    # 그대로 None이든) admin_log를 만들어야 missing_stats가 정확하다.
    admin_log = _build_admin_log(
        my_team, enemy_team, ally_detected_count, enemy_detected_count,
        self_row_idx, self_reason, None, layout, pair_evaluations,
        coarse_crop_box=coarse_crop_box, coarse_crop_used=coarse_crop_used,
        coarse_crop_reason=coarse_crop_reason, coarse_crop_image_path=None,
        original_image_shape=image.shape[:2],
    )
    admin_log["self_feedback_eligible"] = self_known
    admin_log["enemy_composition_analysis_allowed"] = enemy_ok
    admin_log["low_hero_recognition"] = low_hero_recognition
    admin_log["gemini_stats_and_feedback_enabled"] = ENABLE_GEMINI_STATS_AND_FEEDBACK

    report = build_scoreboard_report(my_team, enemy_team, team_feedback, personal_feedback)

    return {
        "report": report,
        "admin_log": admin_log,
        "my_team_stats": _build_stat_dict(my_team),
        "enemy_team_stats": _build_stat_dict(enemy_team),
        "my_stats": _build_stat_dict([my_team[self_row_idx]]) if self_known else {},
    }

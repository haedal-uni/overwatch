import json
import logging
import os
import re
import shutil
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.contrib import admin
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join

from .chatbot_graph import ROLE_LABELS
from .models import ChatLog, ErrorChatLog, UnsatisfiedChatLog

logger = logging.getLogger(__name__)

# 점수판 디버그 이미지 파일명 화이트리스트 — vision_stats._save_debug_images가
# 만드는 파일명 규칙과 일치해야 한다. scoreboard_debug_image_view가 경로
# 조작 방지를 위해 이 패턴에 안 맞는 이름은 전부 거부한다. 팀당 인원수(5/6)
# 가 이미지마다 달라질 수 있어 행 번호는 \d+로 매칭한다.
SCOREBOARD_DEBUG_FILENAME_RE = re.compile(
    r"(?:ally|enemy)_row_\d+_(?:row|hero)_crop\.png|original\.png|coarse_crop\.png"
)
SCOREBOARD_DEBUG_TURN_ID_RE = re.compile(r"[A-Za-z0-9\-]+")


def _delete_scoreboard_debug_dirs(turn_ids):
    """ChatLog 삭제 시 그 turn_id의 logs/scoreboard_debug/{turn_id}/ 폴더
    (디버그 이미지)도 함께 지운다. 대부분의 turn_id는 일반 채팅이라 폴더가
    없으며 이 경우는 건너뛴다. 삭제 실패(주로 서버 파일 권한 문제) turn_id
    목록을 반환해 호출자가 관리자에게 알릴 수 있게 한다."""
    base_dir = str(getattr(settings, "BASE_DIR", os.getcwd()))
    debug_root = os.path.normpath(os.path.join(base_dir, "logs", "scoreboard_debug"))
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
            logger.warning("[SCOREBOARD] 디버그 폴더 삭제 실패(권한 문제 의심): %s", target, exc_info=True)
            failed_turn_ids.append(turn_id)
    return failed_turn_ids


ROLE_BADGE_STYLES = {
    "USER": ("#e0e7ff", "#3730a3", "사용자"),
    "AI": ("#dcfce7", "#166534", "AI 응답"),
    "ERROR": ("#fee2e2", "#991b1b", "❌ 오류"),
}


class ChatLogDisplayMixin:
    """
    ChatLog와 그 프록시 모델(ErrorChatLog, UnsatisfiedChatLog) admin이 공통으로
    쓰는 표시용 메서드 모음. 프록시 모델은 테이블은 같고 조회 범위만 다르므로
    화면 렌더링 로직을 중복해서 적을 필요가 없다.
    """

    def has_add_permission(self, request):
        # 로그는 챗봇 응답 파이프라인에서만 생성되어야 하며, 관리자가 수동으로
        # 만들 이유가 없다.
        return False

    @property
    def media(self):
        # 표 컬럼 헤더 클릭 시 그 값으로 필터링하는 드롭다운을 추가한다.
        # 서버 필터링 로직은 그대로 두고, 사이드바 필터 링크를 JS가 재사용해
        # 팝업으로 보여준다(column_filter.js 참고).
        return super().media + forms.Media(
            css={"all": ("chat/admin/column_filter.css",)},
            js=("chat/admin/column_filter.js",),
        )

    def has_change_permission(self, request, obj=None):
        # 로그는 읽기 전용이다 — 저장 버튼을 노출하면 클릭할 때마다 실질적
        # 변경 없이 LogEntry만 쌓인다(목록/상세 조회는 별도 권한이라 가능).
        return False

    def history_view(self, request, object_id, extra_context=None):
        """기본 "히스토리" 버튼을 세션 대화 전체보기로 리다이렉트한다.

        ChatLog는 자동 생성만 되어 Django 기본 히스토리(LogEntry)가 항상
        비어 있으므로, 실제 대화 내용은 session_transcript_view로 보여준다.
        """
        obj = self.get_object(request, object_id)
        if obj is not None and obj.log_session_id:
            url = reverse("admin:chat_chatlog_session_transcript", args=[obj.log_session_id])
            return HttpResponseRedirect(url)
        return super().history_view(request, object_id, extra_context)

    def _warn_if_debug_dir_cleanup_failed(self, request, failed_turn_ids):
        """디스크의 점수판 디버그 폴더 삭제가 실패했으면(주로 서버의 파일
        소유권/권한 문제) DB 로그 삭제는 이미 끝났더라도 관리자에게 알려서
        조용히 고아 폴더가 쌓이지 않게 한다."""
        if not failed_turn_ids:
            return
        self.message_user(
            request,
            f"로그는 삭제됐지만 디스크의 스탯창 디버그 폴더 {len(failed_turn_ids)}개는 "
            f"권한 문제로 삭제하지 못했습니다(turn_id: {', '.join(failed_turn_ids)}). "
            "서버에서 logs/scoreboard_debug 폴더의 소유권/권한을 확인해주세요.",
            level="warning",
        )

    def delete_model(self, request, obj):
        """개별 로그 삭제(변경 화면의 "삭제") 시 그 turn_id의 점수판 디버그
        이미지 폴더도 함께 지운다."""
        turn_id = obj.turn_id
        super().delete_model(request, obj)
        failed = _delete_scoreboard_debug_dirs([turn_id])
        self._warn_if_debug_dir_cleanup_failed(request, failed)

    def delete_queryset(self, request, queryset):
        """기본 "delete_selected" 일괄 삭제 액션의 삭제 경로. 지워지는 로그들의
        turn_id를 먼저 모아두고, 삭제 후 해당 디버그 이미지 폴더도 함께 지운다."""
        turn_ids = list(queryset.values_list("turn_id", flat=True).distinct())
        super().delete_queryset(request, queryset)
        failed = _delete_scoreboard_debug_dirs(turn_ids)
        self._warn_if_debug_dir_cleanup_failed(request, failed)

    @admin.action(description="선택한 로그가 속한 세션 전체 삭제(같은 log_session_id의 모든 USER/AI/ERROR 로그)")
    def delete_entire_session(self, request, queryset):
        """선택한 로그가 속한 세션(log_session_id) 전체를, 선택하지 않은 행과
        다른 role(USER/AI/ERROR)까지 포함해 통째로 지운다. 프록시 모델
        (ErrorChatLog 등)에서 실행해도 항상 원본 ChatLog 기준으로 삭제된다."""
        session_ids = list(queryset.values_list("log_session_id", flat=True).distinct())
        if not session_ids:
            self.message_user(request, "선택한 로그에 세션 정보가 없습니다.", level="warning")
            return
        session_logs = ChatLog.objects.filter(log_session_id__in=session_ids)
        turn_ids = list(session_logs.values_list("turn_id", flat=True).distinct())
        deleted_count, _ = session_logs.delete()
        failed = _delete_scoreboard_debug_dirs(turn_ids)
        self.message_user(
            request,
            f"세션 {len(session_ids)}개, 로그 {deleted_count}건을 삭제했습니다.",
        )
        self._warn_if_debug_dir_cleanup_failed(request, failed)

    @admin.display(description="구분", ordering="role")
    def role_badge(self, obj):
        bg, fg, label = ROLE_BADGE_STYLES.get(
            obj.role,
            ("#f3f4f6", "#374151", obj.role),
        )
        return format_html(
            '<span style="background:{}; color:{}; padding:3px 9px; '
            'border-radius:999px; font-size:12px; font-weight:700; '
            'white-space:nowrap; display:inline-block;">{}</span>',
            bg, fg, label,
        )

    @admin.display(description="세션", ordering="log_session_id")
    def log_session_link(self, obj):
        """같은 세션의 모든 로그를 대화하듯 순서대로 보여주는 전용 화면으로 연결한다."""
        if not obj.log_session_id:
            return "-"
        url = reverse("admin:chat_chatlog_session_transcript", args=[obj.log_session_id])
        return format_html('<a href="{}" style="font-weight:600;">{}</a>', url, obj.log_session_id[:10])

    @admin.display(description="턴", ordering="turn_id")
    def turn_link(self, obj):
        if not obj.turn_id:
            return "-"
        url = (
            reverse("admin:chat_chatlog_changelist")
            + "?" + urlencode({"turn_id": obj.turn_id})
        )
        return format_html('<a href="{}" style="font-weight:600;">{}</a>', url, obj.turn_id[:10])

    @admin.display(description="메시지 미리보기")
    def message_preview(self, obj):
        # 컬럼 폭이 좁아지면 텍스트가 여러 줄로 줄바꿈돼 행 높이가 늘어나므로
        # CSS로 한 줄 말줄임을 강제한다.
        text = (obj.message or "").replace("\n", " ").strip()
        truncated = text[:80] + ("…" if len(text) > 80 else "")
        color = "color:#b91c1c; font-weight:600;" if obj.role == "ERROR" else ""
        return format_html(
            '<span style="display:block; max-width:280px; white-space:nowrap; '
            'overflow:hidden; text-overflow:ellipsis; {}">{}</span>',
            color, truncated,
        )

    @admin.display(description="메시지")
    def message_box(self, obj):
        if not obj.message:
            return "-"
        bg = "#fff7ed" if obj.role == "ERROR" else "#f9fafb"
        border = "#fdba74" if obj.role == "ERROR" else "#e5e7eb"
        return format_html(
            '<div style="white-space:pre-wrap; word-break:break-word; '
            'background:{}; border:1px solid {}; padding:14px; '
            'border-radius:8px; line-height:1.6; font-size:14px;">{}</div>',
            bg, border, obj.message,
        )

    @admin.display(description="메타데이터")
    def metadata_pretty(self, obj):
        if not obj.metadata:
            return "-"
        pretty = json.dumps(obj.metadata, ensure_ascii=False, indent=2)
        return format_html(
            '<pre style="white-space:pre-wrap; word-break:break-word; '
            'background:#0b1021; color:#d1d5db; padding:14px; '
            'border-radius:8px; max-height:600px; overflow:auto; '
            'font-size:12px; line-height:1.5;">{}</pre>',
            pretty,
        )

    @admin.display(description="상대 조합")
    def enemy_team_display(self, obj):
        """target_enemy는 카운터 대상 1명만 담으므로, metadata의 enemy_team
        전체를 옆에 함께 보여준다(답변 생성은 이미 enemy_team 전체를 참고한다)."""
        metadata = obj.metadata or {}
        enemy_team = (
            metadata.get("context_patch", {}).get("enemy_team")
            or metadata.get("context_after", {}).get("enemy_team")
            or metadata.get("context_before", {}).get("enemy_team")
        )
        if not enemy_team:
            return "-"
        return ", ".join(enemy_team)

    @admin.display(description="상성 카드")
    def matchup_card_display(self, obj):
        """카운터 질문 답변의 핵심인 상성 카드(metadata.matchup_card)를 message_box 옆에 풀어서 보여준다."""
        metadata = obj.metadata or {}
        card = metadata.get("matchup_card")
        if not card:
            return "-"

        def render_list(heroes):
            if not heroes:
                return "-"
            items = format_html_join(
                "", "<li><strong>{}</strong> — {}</li>",
                ((h.get("hero", ""), h.get("note", "")) for h in heroes),
            )
            return format_html('<ul style="margin:4px 0 0 18px; padding:0;">{}</ul>', items)

        return format_html(
            '<div style="background:#f9fafb; border:1px solid #e5e7eb; padding:14px; '
            'border-radius:8px; font-size:14px; line-height:1.6;">'
            '<div style="margin-bottom:8px;"><strong>분석 대상:</strong> {} ({})</div>'
            '<div style="margin-bottom:8px;"><strong>상대하기 어려운 영웅</strong>{}</div>'
            '<div><strong>상대하기 쉬운 영웅</strong>{}</div>'
            '</div>',
            card.get("subject", ""),
            ROLE_LABELS.get(card.get("subject_role"), card.get("subject_role") or "-"),
            render_list(card.get("hard_heroes")),
            render_list(card.get("easy_heroes")),
        )

    @admin.display(description="추천 영웅 카드")
    def recommend_card_display(self, obj):
        """교체(swap)/조합(composition) 질문 답변의 추천 영웅 카드(metadata.recommend_card)를 풀어서 보여준다."""
        metadata = obj.metadata or {}
        card = metadata.get("recommend_card")
        if not card:
            return "-"

        mode_label = {"swap": "교체 추천", "composition": "조합 추천"}.get(
            card.get("mode"), card.get("mode") or "-"
        )
        heroes = card.get("heroes")
        if not heroes:
            heroes_html = "-"
        else:
            items = format_html_join(
                "", "<li><strong>{}</strong> — {}</li>",
                ((h.get("hero", ""), h.get("note", "")) for h in heroes),
            )
            heroes_html = format_html('<ul style="margin:4px 0 0 18px; padding:0;">{}</ul>', items)

        return format_html(
            '<div style="background:#f9fafb; border:1px solid #e5e7eb; padding:14px; '
            'border-radius:8px; font-size:14px; line-height:1.6;">'
            '<div style="margin-bottom:8px;"><strong>카드 종류:</strong> {}</div>'
            '<div><strong>추천 영웅</strong>{}</div>'
            '</div>',
            mode_label,
            heroes_html,
        )

    @admin.display(description="예측 질문")
    def suggested_questions_display(self, obj):
        """AI 답변에 딸린 예측 질문 3개(metadata.suggested_questions)를 목록으로 보여준다."""
        metadata = obj.metadata or {}
        questions = metadata.get("suggested_questions")
        if not questions:
            return "-"
        items = format_html_join("", "<li>{}</li>", ((q,) for q in questions))
        return format_html('<ul style="margin:0 0 0 18px; padding:0;">{}</ul>', items)

    def _scoreboard_debug_image_url(self, turn_id, rel_path):
        """admin_log에 저장된 "logs/scoreboard_debug/{turn_id}/파일명" 상대
        경로를, 그 파일을 실제로 서빙하는 관리자 전용 URL로 바꾼다."""
        if not rel_path or not turn_id:
            return None
        filename = rel_path.rsplit("/", 1)[-1]
        return reverse("admin:chat_chatlog_scoreboard_debug_image", args=[turn_id, filename])

    def _render_debug_thumb(self, turn_id, rel_path, alt):
        """crop 경로 하나를 클릭하면 원본 크기로 열리는 56x56 썸네일 <img>로 렌더링한다."""
        img_url = self._scoreboard_debug_image_url(turn_id, rel_path)
        if not img_url:
            return "(저장 안 됨)"
        return format_html(
            '<a href="{0}" target="_blank"><img src="{0}" alt="{1}" '
            'style="width:56px; height:56px; object-fit:cover; border:1px solid #374151; '
            'border-radius:4px;"></a>',
            img_url, alt,
        )

    @staticmethod
    def _format_box(box):
        if not box:
            return "-"
        return f"({box['x0']},{box['y0']})-({box['x1']},{box['y1']})"

    # Django admin의 readonly 필드 카드 배경 때문에 <tr>에서 상속한 색상이
    # 적용되지 않을 수 있어, 셀마다 배경/글자색을 직접 인라인으로 지정한다.
    SCOREBOARD_TD_STYLE = "padding:6px 8px; border-bottom:1px solid #374151; background:#111827; color:#e5e7eb; vertical-align:top;"
    SCOREBOARD_TH_STYLE = "padding:6px 8px; text-align:left; background:#1f2937; color:#f9fafb; font-weight:700; white-space:nowrap;"

    def _sb_td(self, content):
        return format_html('<td style="{}">{}</td>', self.SCOREBOARD_TD_STYLE, content)

    def _sb_th(self, text):
        return format_html('<th style="{}">{}</th>', self.SCOREBOARD_TH_STYLE, text)

    @admin.display(description="스탯창 분석 진단 정보 (관리자 전용)")
    def scoreboard_admin_log_display(self, obj):
        """점수판 분석 진단 정보(metadata.admin_log: 팀 패널 좌표, 행별 crop,
        영웅 유사도 점수/후보)를 관리자 전용으로 렌더링한다. 사용자 응답에는
        포함되지 않는다."""
        metadata = obj.metadata or {}
        log = metadata.get("admin_log")
        if not log:
            return "-"

        turn_id = obj.turn_id

        def render_missing(rows):
            if not rows:
                return "없음"
            items = format_html_join(
                "", "<li>{} {}행({}): {}</li>",
                (
                    (
                        "우리팀" if r["team"] == "ally" else "상대팀", r["row_index"],
                        ROLE_LABELS.get(r["role"], r["role"]), ", ".join(r["fields"]),
                    )
                    for r in rows
                ),
            )
            return format_html('<ul style="margin:4px 0 0 18px; padding:0;">{}</ul>', items)

        def render_team_layout(team_key, team_label):
            # 후보 목록(mask_candidate_boxes)은 데이터에는 남아있지만 화면에는 표시하지 않는다.
            data = (log.get("team_layout") or {}).get(team_key) or {}
            pair_details = data.get("pair_details") or {}
            return format_html(
                '<div style="margin-bottom:10px;"><strong>{}</strong> — team_box: {} · '
                'header_height: {}px · player_area_box: {}<br>'
                '<span style="color:#9ca3af; font-size:11px;">'
                'selected_by: {} · selected_candidate: {} (점수 {}) · '
                'pair_score: {} · x_fallback: {} · y_fallback: {} · '
                'expected_row_height: {} · row_heights: {}<br>'
                'layout 검증: {} ({})<br>'
                '역할 순서(role_labels): {}<br>'
                'pair 세부 점수: {}'
                '</span>'
                '</div>',
                team_label, self._format_box(data.get("team_box")),
                data.get("header_height"), self._format_box(data.get("player_area_box")),
                data.get("selected_by") or "-",
                self._format_box(data.get("selected_candidate_box")), data.get("selected_candidate_score"),
                data.get("pair_score"),
                "예" if data.get("x_fallback_used") else "아니오",
                "예" if data.get("y_fallback_used") else "아니오",
                data.get("expected_row_height"), data.get("row_heights") or "-",
                "통과" if data.get("layout_validation_ok") else "실패",
                data.get("layout_validation_reason") or "-",
                ", ".join(data.get("role_labels") or []) or "-",
                pair_details or "-",
            )

        def fmt_matches(matches):
            if not matches:
                return "없음"
            return ", ".join(f"{m['hero']} {m['score']}" for m in matches)

        def render_hero_row(r):
            cells = [
                self._sb_td("우리팀" if r["team"] == "ally" else "상대팀"),
                self._sb_td(r["row_index"]),
                self._sb_td(ROLE_LABELS.get(r["role"], r["role"])),
                self._sb_td(self._render_debug_thumb(turn_id, r.get("row_crop_path"), "row crop")),
                self._sb_td(self._render_debug_thumb(turn_id, r.get("crop_path"), "hero crop")),
                self._sb_td(format_html("{} ({})", r["hero"], r["confidence_label"])),
                self._sb_td(fmt_matches(r.get("post_role_top_matches"))),
                self._sb_td(r.get("template_path") or "-"),
            ]
            row_html = format_html_join("", "{}", ((c,) for c in cells))
            return format_html("<tr>{}</tr>", row_html)

        hero_rows = log.get("hero_rows", [])
        if hero_rows:
            headers = [
                "팀", "행", "역할", "row crop", "hero crop",
                "인식 결과 (확신도)",
                "TOP3",
                "사용 템플릿",
            ]
            header_html = format_html_join("", "{}", ((self._sb_th(h),) for h in headers))
            rows_html = format_html_join("", "{}", ((render_hero_row(r),) for r in hero_rows))
            table_html = format_html(
                '<div style="overflow-x:auto;">'
                '<table style="width:100%; border-collapse:collapse; font-size:12px; margin-top:8px; '
                'background:#111827;">'
                '<thead><tr>{}</tr></thead><tbody>{}</tbody></table>'
                '</div>',
                header_html, rows_html,
            )
        else:
            table_html = "행별 인식 데이터가 없습니다."

        def render_pair_evaluations(evaluations):
            if not evaluations:
                return "평가된 쌍 없음(한쪽 색상에 유효 후보가 없었음)"
            items = format_html_join(
                "", "<li>파란 {} / 빨간 {} → {} (pair_score {}) · {}</li>",
                (
                    (
                        self._format_box(ev.get("blue_box")), self._format_box(ev.get("red_box")),
                        "통과" if ev.get("passes") else "탈락", ev.get("pair_score"),
                        ", ".join(ev.get("rejection_reasons") or []) or ev.get("details"),
                    )
                    for ev in evaluations
                ),
            )
            return format_html('<ul style="margin:4px 0 0 18px; padding:0;">{}</ul>', items)

        original_url = self._scoreboard_debug_image_url(turn_id, log.get("original_image_path"))
        original_html = (
            format_html(
                '<a href="{0}" target="_blank"><img src="{0}" alt="원본" '
                'style="max-width:360px; border:1px solid #374151; border-radius:6px;"></a>',
                original_url,
            )
            if original_url else "-"
        )

        coarse_url = self._scoreboard_debug_image_url(turn_id, log.get("coarse_crop_image_path"))
        coarse_thumb_html = (
            format_html(
                '<a href="{0}" target="_blank"><img src="{0}" alt="1단계 coarse crop" '
                'style="max-width:360px; border:1px solid #374151; border-radius:6px;"></a>',
                coarse_url,
            )
            if coarse_url else "(이미지 없음)"
        )
        if log.get("coarse_crop_is_noop"):
            # 표만 캡처한 경우 완화된 색 영역이 이미지 대부분을 덮어 경계에
            # clamp되므로, 전체화면 캡처 오인식이 아니라 정상적인 no-op이다.
            used_text = "예 (원본 이미지 전체 크기와 동일 — 실질적으로 아무것도 안 좁힌 no-op)"
        elif log.get("coarse_crop_used"):
            used_text = "예"
        else:
            used_text = "아니오(원본 이미지 그대로 사용)"
        coarse_html = format_html(
            '<div><strong>사용 여부:</strong> {} · <strong>좌표(원본 기준):</strong> {} · '
            '<strong>원본 크기:</strong> {} · <strong>폴백 사유:</strong> {}</div>'
            '<div style="margin-top:4px;">{}</div>',
            used_text,
            self._format_box(log.get("coarse_crop_box")),
            (
                f"{log['original_image_shape'][1]}×{log['original_image_shape'][0]}"
                if log.get("original_image_shape") else "-"
            ),
            log.get("coarse_crop_fallback_reason") or "-",
            coarse_thumb_html,
        )

        return format_html(
            '<div style="background:#111827; color:#e5e7eb; border:1px solid #374151; '
            'padding:14px; border-radius:8px; font-size:13px; line-height:1.6;">'
            '<div><strong>우리팀 인식:</strong> {}명 중 {}명 · <strong>상대팀 인식:</strong> {}명 중 {}명</div>'
            '<div style="margin-top:6px;"><strong>본인 추정 행:</strong> {} · <strong>이유:</strong> {} · '
            '<strong>본인 영웅:</strong> {} · <strong>판별 실패:</strong> {}</div>'
            '<div style="margin-top:6px;"><strong>영웅 인식 방식:</strong> {}</div>'
            '<div style="margin-top:10px;"><strong>1단계 coarse crop(전체화면 캡처 대응):</strong>'
            '<div style="margin-top:4px;">{}</div></div>'
            '<div style="margin-top:10px;"><strong>팀 레이아웃(패널 검출/쌍 선택/검증):</strong>'
            '<div style="margin-top:4px;">{}</div><div style="margin-top:2px;">{}</div></div>'
            '<div style="margin-top:10px;"><strong>평가된 파란·빨간 후보 쌍:</strong>{}</div>'
            '<div style="margin-top:10px;"><strong>원본 이미지:</strong><br>{}</div>'
            '<div style="margin-top:10px;"><strong>행별 영웅 인식 상세:</strong>{}</div>'
            '<div style="margin-top:10px;"><strong>수치 누락 항목:</strong>{}</div>'
            '<div style="margin-top:10px;"><strong>개인 피드백 생성 여부:</strong> {} · '
            '<strong>상대 조합 분석 허용:</strong> {} · <strong>영웅 인식률 낮음:</strong> {}</div>'
            '</div>',
            log.get("ally_roster_size") or 5, log.get("ally_detected_count"),
            log.get("enemy_roster_size") or 5, log.get("enemy_detected_count"),
            log.get("self_row_index") or "확인 필요", log.get("self_reason") or "-",
            log.get("self_hero") or "-", "예" if log.get("self_determination_failed") else "아니오",
            log.get("hero_icon_method") or "-",
            coarse_html,
            render_team_layout("ally", "우리팀"), render_team_layout("enemy", "상대팀"),
            render_pair_evaluations(log.get("pair_evaluations", [])),
            original_html,
            table_html,
            render_missing(log.get("missing_stats", [])),
            "예" if log.get("self_feedback_eligible") else "아니오",
            "예" if log.get("enemy_composition_analysis_allowed") else "아니오",
            "예" if log.get("low_hero_recognition") else "아니오",
        )

    @admin.display(description="연관 질문")
    def related_question(self, obj):
        """같은 turn_id를 공유하는 USER 로그를 찾아, 이 AI 답변의 원래 질문을 보여준다."""
        if obj.role != "AI":
            return "-"
        user_log = (
            ChatLog.objects
            .filter(turn_id=obj.turn_id, role="USER")
            .order_by("created_at")
            .first()
        )
        if not user_log:
            return "-"
        return format_html(
            '<div style="white-space:pre-wrap; word-break:break-word; '
            'background:#eff6ff; border:1px solid #bfdbfe; padding:14px; '
            'border-radius:8px; line-height:1.6; font-size:14px;">{}</div>',
            user_log.message,
        )


@admin.register(ChatLog)
class ChatLogAdmin(ChatLogDisplayMixin, admin.ModelAdmin):
    # actions를 명시하지 않으면 Django가 delete_selected만 자동으로 넣으므로,
    # delete_entire_session도 항상 노출되도록 둘 다 명시적으로 등록한다.
    actions = ["delete_selected", "delete_entire_session"]

    list_display = (
        "created_at",
        "role_badge",
        "log_session_link",
        "turn_link",
        "intent",
        "current_hero",
        "target_enemy",
        "enemy_team_display",
        "message_preview",
        "is_unsatisfied",
    )

    list_filter = (
        "role",
        "intent",
        "current_hero",
        "target_enemy",
        "is_unsatisfied",
        "created_at",
    )

    search_fields = (
        "log_session_id",
        "turn_id",
        "message",
        "intent",
        "current_hero",
        "target_enemy",
        "feedback_reason",
    )

    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_per_page = 50

    readonly_fields = (
        "created_at",
        "role_badge",
        "log_session_id",
        "turn_id",
        "role",
        "intent",
        "current_hero",
        "target_enemy",
        "enemy_team_display",
        "message_box",
        "matchup_card_display",
        "recommend_card_display",
        "suggested_questions_display",
        "scoreboard_admin_log_display",
        "metadata_pretty",
        "related_question",
        "is_unsatisfied",
        "feedback_reason",
    )

    fieldsets = (
        ("기본 정보", {
            "fields": ("created_at", "role_badge", "role", "log_session_id", "turn_id"),
        }),
        ("분석 정보", {
            "fields": ("intent", "current_hero", "target_enemy", "enemy_team_display"),
        }),
        ("메시지", {
            "fields": ("related_question", "message_box", "matchup_card_display", "recommend_card_display", "suggested_questions_display"),
        }),
        ("스탯창 분석 진단 정보 (관리자 전용)", {
            "fields": ("scoreboard_admin_log_display",),
            "classes": ("collapse",),
        }),
        ("사용자 피드백", {
            "fields": ("is_unsatisfied", "feedback_reason"),
        }),
        ("상세 컨텍스트 / 메타데이터", {
            "fields": ("metadata_pretty",),
            "classes": ("collapse",),
        }),
    )

    def get_urls(self):
        custom_urls = [
            path(
                "session/<str:log_session_id>/transcript/",
                self.admin_site.admin_view(self.session_transcript_view),
                name="chat_chatlog_session_transcript",
            ),
            path(
                "session/<str:log_session_id>/delete/",
                self.admin_site.admin_view(self.session_delete_view),
                name="chat_chatlog_session_delete",
            ),
            path(
                "scoreboard-debug/<str:turn_id>/<str:filename>/",
                self.admin_site.admin_view(self.scoreboard_debug_image_view),
                name="chat_chatlog_scoreboard_debug_image",
            ),
        ]
        return custom_urls + super().get_urls()

    def session_delete_view(self, request, log_session_id):
        """대화 전체보기(session_transcript_view) 화면의 "이 세션 전체 삭제"
        버튼이 POST하는 곳. 이 세션의 모든 로그(USER/AI/ERROR 전부)를 지우고
        changelist로 돌아간다. GET으로는 실행되지 않게 막는다(실수로 링크를
        클릭/미리보기하다 삭제되는 것 방지)."""
        if request.method != "POST":
            raise Http404
        if not self.has_delete_permission(request):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        session_logs = ChatLog.objects.filter(log_session_id=log_session_id)
        turn_ids = list(session_logs.values_list("turn_id", flat=True).distinct())
        deleted_count, _ = session_logs.delete()
        failed = _delete_scoreboard_debug_dirs(turn_ids)
        self.message_user(request, f"세션 {log_session_id}의 로그 {deleted_count}건을 삭제했습니다.")
        self._warn_if_debug_dir_cleanup_failed(request, failed)
        return HttpResponseRedirect(reverse("admin:chat_chatlog_changelist"))

    def scoreboard_debug_image_view(self, request, turn_id, filename):
        """logs/scoreboard_debug/{turn_id}/의 디버그 이미지를 관리자에게만 서빙한다.

        admin_view가 staff 권한을 강제하고, turn_id/filename을 화이트리스트
        정규식으로 검증한 뒤 최종 경로가 디버그 루트를 벗어나지 않는지도 다시
        확인해 경로 조작을 이중으로 막는다.
        """
        if not SCOREBOARD_DEBUG_TURN_ID_RE.fullmatch(turn_id) or not SCOREBOARD_DEBUG_FILENAME_RE.fullmatch(filename):
            raise Http404

        base_dir = str(getattr(settings, "BASE_DIR", os.getcwd()))
        debug_root = os.path.normpath(os.path.join(base_dir, "logs", "scoreboard_debug"))
        file_path = os.path.normpath(os.path.join(debug_root, turn_id, filename))
        if os.path.commonpath([debug_root, file_path]) != debug_root or not os.path.isfile(file_path):
            raise Http404

        return FileResponse(open(file_path, "rb"), content_type="image/png")

    def session_transcript_view(self, request, log_session_id):
        """같은 log_session_id의 로그를 표가 아니라 시간순 대화 형태로 보여준다.

        ChatLogDisplayMixin의 표시 메서드(role_badge/message_box 등)를 그대로
        재사용해 렌더링 로직을 중복하지 않는다.
        """
        logs = list(
            ChatLog.objects.filter(log_session_id=log_session_id).order_by("created_at", "id")
        )
        transcript_messages = [
            {
                "obj": log,
                "role_badge": self.role_badge(log),
                "message_box": self.message_box(log),
                "matchup_card": self.matchup_card_display(log),
                "recommend_card": self.recommend_card_display(log),
                "suggested_questions": self.suggested_questions_display(log),
                "scoreboard_admin_log": self.scoreboard_admin_log_display(log),
            }
            for log in logs
        ]
        context = {
            **self.admin_site.each_context(request),
            "title": f"대화 전체 보기 — {log_session_id}",
            "log_session_id": log_session_id,
            "transcript_messages": transcript_messages,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/chat/chatlog_session_transcript.html", context)


@admin.register(ErrorChatLog)
class ErrorChatLogAdmin(ChatLogDisplayMixin, admin.ModelAdmin):
    """role="ERROR" 로그(파이썬 예외든 그래프 내부 오류든)만 모아 보여준다."""

    actions = ["delete_selected", "delete_entire_session"]

    list_display = (
        "created_at",
        "log_session_link",
        "turn_link",
        "intent",
        "current_hero",
        "target_enemy",
        "enemy_team_display",
        "message_preview",
    )
    list_filter = ("intent", "created_at")
    search_fields = ("log_session_id", "turn_id", "message")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_per_page = 50

    readonly_fields = (
        "created_at",
        "log_session_id",
        "turn_id",
        "intent",
        "current_hero",
        "target_enemy",
        "enemy_team_display",
        "message_box",
        "metadata_pretty",
    )

    fieldsets = (
        ("기본 정보", {
            "fields": ("created_at", "log_session_id", "turn_id"),
        }),
        ("분석 정보", {
            "fields": ("intent", "current_hero", "target_enemy", "enemy_team_display"),
        }),
        ("오류 내용", {
            "fields": ("message_box",),
        }),
        ("상세 컨텍스트 / 메타데이터", {
            "fields": ("metadata_pretty",),
            "classes": ("collapse",),
        }),
    )


@admin.register(UnsatisfiedChatLog)
class UnsatisfiedChatLogAdmin(ChatLogDisplayMixin, admin.ModelAdmin):
    """사용자가 불만족으로 표시한 AI 답변만 모아, 질문/답변/이유를 한 화면에서 보여준다."""

    actions = ["delete_selected", "delete_entire_session"]

    list_display = (
        "created_at",
        "log_session_link",
        "turn_link",
        "intent",
        "current_hero",
        "target_enemy",
        "enemy_team_display",
        "message_preview",
        "feedback_reason_preview",
        "resolved_badge",
    )
    list_filter = ("intent", "current_hero", "is_resolved", "created_at")
    search_fields = ("log_session_id", "turn_id", "message", "feedback_reason", "resolution_note")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_per_page = 50

    # is_resolved/resolution_note만 관리자가 직접 편집할 수 있어야 하므로,
    # 이 admin에서만 has_change_permission을 True로 되돌린다(다른 로그
    # admin은 ChatLogDisplayMixin의 기본값인 완전 읽기 전용을 유지).
    def has_change_permission(self, request, obj=None):
        return True

    readonly_fields = (
        "created_at",
        "log_session_id",
        "turn_id",
        "intent",
        "current_hero",
        "target_enemy",
        "enemy_team_display",
        "related_question",
        "message_box",
        "matchup_card_display",
        "recommend_card_display",
        "suggested_questions_display",
        "scoreboard_admin_log_display",
        "feedback_reason",
        "metadata_pretty",
    )

    fieldsets = (
        ("기본 정보", {
            "fields": ("created_at", "log_session_id", "turn_id", "intent", "current_hero", "target_enemy", "enemy_team_display"),
        }),
        ("질문 / AI 답변", {
            "fields": ("related_question", "message_box", "matchup_card_display", "recommend_card_display", "suggested_questions_display", "scoreboard_admin_log_display"),
        }),
        ("불만족 이유", {
            "fields": ("feedback_reason",),
        }),
        ("처리 상태 (관리자 메모)", {
            "fields": ("is_resolved", "resolution_note"),
            "description": "처리 완료 여부는 체크만으로 표시할 수 있고, 메모는 처리 완료와 별개로 자유롭게 남기고 나중에 다시 고칠 수 있습니다.",
        }),
        ("상세 컨텍스트 / 메타데이터", {
            "fields": ("metadata_pretty",),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="처리 상태", ordering="is_resolved", boolean=True)
    def resolved_badge(self, obj):
        return obj.is_resolved

    @admin.display(description="불만족 이유")
    def feedback_reason_preview(self, obj):
        text = (obj.feedback_reason or "").replace("\n", " ").strip()
        truncated = text[:60] + ("…" if len(text) > 60 else "")
        return format_html(
            '<span style="display:block; max-width:220px; white-space:nowrap; '
            'overflow:hidden; text-overflow:ellipsis;">{}</span>',
            truncated,
        )

import json
from urllib.parse import urlencode

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import ChatLog, ErrorChatLog, UnsatisfiedChatLog


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

    def has_change_permission(self, request, obj=None):
        # 로그는 수정하지 못하게 막고, 상세 조회만 가능하게 한다.
        return True

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
        if not obj.log_session_id:
            return "-"
        url = (
            reverse("admin:chat_chatlog_changelist")
            + "?" + urlencode({"log_session_id": obj.log_session_id})
        )
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
        # 컬럼 자체 너비 없이 텍스트만 잘라내면, 좁은 화면(특히 모바일)에서
        # 컬럼이 좁게 잡힐 때 이 텍스트가 여러 줄로 줄바꿈되어 행 전체 높이가
        # 비정상적으로 늘어난다. CSS로 한 줄 말줄임을 강제해 행 높이를 고정한다.
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

    @admin.display(description="연관 질문")
    def related_question(self, obj):
        """
        이 AI 답변(turn_id)과 짝을 이루는 사용자 질문을 찾아 보여준다.
        USER/AI 로그가 같은 turn_id를 공유하는 구조를 활용한다.
        """
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
    list_display = (
        "created_at",
        "role_badge",
        "log_session_link",
        "turn_link",
        "intent",
        "current_hero",
        "target_enemy",
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
        "message_box",
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
            "fields": ("intent", "current_hero", "target_enemy"),
        }),
        ("메시지", {
            "fields": ("related_question", "message_box"),
        }),
        ("사용자 피드백", {
            "fields": ("is_unsatisfied", "feedback_reason"),
        }),
        ("상세 컨텍스트 / 메타데이터", {
            "fields": ("metadata_pretty",),
            "classes": ("collapse",),
        }),
    )


@admin.register(ErrorChatLog)
class ErrorChatLogAdmin(ChatLogDisplayMixin, admin.ModelAdmin):
    """
    role="ERROR" 로그만 모아 보여준다. 챗봇 응답이 실패했을 때
    (파이썬 예외든, 그래프 내부에서 state["error"]로 반환됐든) 여기서
    한눈에 확인할 수 있다.
    """

    list_display = (
        "created_at",
        "log_session_link",
        "turn_link",
        "intent",
        "current_hero",
        "target_enemy",
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
        "message_box",
        "metadata_pretty",
    )

    fieldsets = (
        ("기본 정보", {
            "fields": ("created_at", "log_session_id", "turn_id"),
        }),
        ("분석 정보", {
            "fields": ("intent", "current_hero", "target_enemy"),
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
    """
    사용자가 "이 답변이 만족스럽지 않다"고 표시한 AI 답변만 모아 보여준다.
    질문 → AI 답변 → 불만족 이유를 한 화면에서 확인할 수 있다.
    """

    list_display = (
        "created_at",
        "log_session_link",
        "turn_link",
        "current_hero",
        "target_enemy",
        "message_preview",
        "feedback_reason_preview",
    )
    list_filter = ("current_hero", "created_at")
    search_fields = ("log_session_id", "turn_id", "message", "feedback_reason")
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
        "related_question",
        "message_box",
        "feedback_reason",
        "metadata_pretty",
    )

    fieldsets = (
        ("기본 정보", {
            "fields": ("created_at", "log_session_id", "turn_id", "intent", "current_hero", "target_enemy"),
        }),
        ("질문 / AI 답변", {
            "fields": ("related_question", "message_box"),
        }),
        ("불만족 이유", {
            "fields": ("feedback_reason",),
        }),
        ("상세 컨텍스트 / 메타데이터", {
            "fields": ("metadata_pretty",),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="불만족 이유")
    def feedback_reason_preview(self, obj):
        text = (obj.feedback_reason or "").replace("\n", " ").strip()
        truncated = text[:60] + ("…" if len(text) > 60 else "")
        return format_html(
            '<span style="display:block; max-width:220px; white-space:nowrap; '
            'overflow:hidden; text-overflow:ellipsis;">{}</span>',
            truncated,
        )

# admin.py

import json
from urllib.parse import urlencode

from django.contrib import admin
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join

from .chatbot_graph import ROLE_LABELS
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

    def history_view(self, request, object_id, extra_context=None):
        """
        Django 관리자 기본 제공 "히스토리" 버튼(상세 화면 오른쪽 위)은 이
        관리자 화면을 통해 직접 값을 수정한 기록(django.contrib.admin의
        LogEntry)만 보여준다. ChatLog는 사람이 admin에서 수정하는 게 아니라
        챗봇 파이프라인이 자동으로만 생성/기록하므로 이 LogEntry가 항상
        비어 있어 "이 개체는 변경 기록이 없습니다"만 뜬다 — 즉 이 버튼은
        원래부터 대화 기록을 보여주는 용도가 아니었다. 실제로 "한 세션의
        대화 전체를 보고 싶다"는 요청에 맞는 화면은 세션별 대화 전체보기
        (session_transcript_view, 목록의 "세션" 컬럼 링크)이므로, 헷갈리지
        않도록 히스토리 버튼 자체를 그 화면으로 리다이렉트한다.
        """
        obj = self.get_object(request, object_id)
        if obj is not None and obj.log_session_id:
            url = reverse("admin:chat_chatlog_session_transcript", args=[obj.log_session_id])
            return HttpResponseRedirect(url)
        return super().history_view(request, object_id, extra_context)

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
        """
        기존에는 이 링크가 log_session_id로 필터링된 목록 테이블(changelist)로
        갔는데, 한 세션의 질문/답변을 한 턴씩 표로 훑어봐야 해서 대화 흐름을
        파악하기 힘들다는 지적을 받았다. 대신 같은 세션의 모든 로그를 실제
        대화하듯 순서대로 쭉 보여주는 전용 화면(session_transcript_view)으로
        연결한다.
        """
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

    @admin.display(description="상대 조합")
    def enemy_team_display(self, obj):
        """
        target_enemy는 "카운터 대상 1명"만 담는 필드라, 여러 상대를 동시에
        언급한 질문에서는 그 중 하나만 보인다(그래프가 실제 답변을 생성할 때는
        target_enemy와 별개로 enemy_team 전체를 참고하므로 답변 자체는 정상이다).
        enemy_team은 ChatLog의 별도 컬럼이 아니라 metadata(JSON) 안에만 있어
        목록/상세에서 바로 안 보였는데, 여기서 꺼내 target_enemy 옆에 나란히
        보여준다.
        """
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
        """
        카운터(counter) 질문의 실제 답변은 말풍선의 짧은 intro 문장
        (message_box)뿐 아니라 상성 카드(상대하기 어려운/쉬운 영웅 목록)가
        핵심 내용인데, 카드 데이터는 metadata.matchup_card 안에만 있어 접힌
        메타데이터 JSON을 펼치기 전에는 답변 내용을 온전히 확인할 수 없었다.
        message_box와 같은 화면에서 바로 보이도록 풀어서 렌더링한다.
        """
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
        """
        교체(swap)/조합(composition) 질문의 실제 답변은 말풍선의 짧은 intro
        문장(message_box)뿐 아니라 "추천 영웅 카드"(단일 목록 + 이유)가 핵심
        내용인데, matchup_card_display와 달리 이 카드는 여태 관리자 페이지
        어디에도 노출되지 않고 metadata.recommend_card 안에만 있어 접힌
        메타데이터 JSON을 펼치기 전에는 확인할 수 없었다. message_box와 같은
        화면에서 바로 보이도록 풀어서 렌더링한다.
        """
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
        """
        AI 답변 하단에 버튼으로 노출되는 "다음에 물어볼 만한 질문 3개"
        (LLM이 예측한 것)도 metadata.suggested_questions 안에만 있어 접힌
        메타데이터를 펼치기 전에는 확인할 수 없었다. message_box와 같은
        화면에서 바로 보이도록 목록으로 풀어서 보여준다.
        """
        metadata = obj.metadata or {}
        questions = metadata.get("suggested_questions")
        if not questions:
            return "-"
        items = format_html_join("", "<li>{}</li>", ((q,) for q in questions))
        return format_html('<ul style="margin:0 0 0 18px; padding:0;">{}</ul>', items)

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
        ]
        return custom_urls + super().get_urls()

    def session_transcript_view(self, request, log_session_id):
        """
        같은 log_session_id를 가진 로그를 표(changelist)가 아니라 실제 대화하듯
        시간순으로 쭉 이어 보여준다. 질문 하나, 답변 하나씩 따로 열어봐야 하는
        기존 방식이 대화 흐름을 파악하기 힘들다는 지적에 따라 추가했다.
        기존 표시 로직(role_badge/message_box/matchup_card_display 등, 전부
        ChatLogDisplayMixin에 이미 있음)을 그대로 재사용해 이중으로 유지보수할
        코드를 늘리지 않는다.
        """
        logs = list(
            ChatLog.objects.filter(log_session_id=log_session_id).order_by("created_at", "id")
        )
        messages = [
            {
                "obj": log,
                "role_badge": self.role_badge(log),
                "message_box": self.message_box(log),
                "matchup_card": self.matchup_card_display(log),
                "recommend_card": self.recommend_card_display(log),
                "suggested_questions": self.suggested_questions_display(log),
            }
            for log in logs
        ]
        context = {
            **self.admin_site.each_context(request),
            "title": f"대화 전체 보기 — {log_session_id}",
            "log_session_id": log_session_id,
            "messages": messages,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/chat/chatlog_session_transcript.html", context)


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
        "enemy_team_display",
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
        "enemy_team_display",
        "related_question",
        "message_box",
        "matchup_card_display",
        "recommend_card_display",
        "suggested_questions_display",
        "feedback_reason",
        "metadata_pretty",
    )

    fieldsets = (
        ("기본 정보", {
            "fields": ("created_at", "log_session_id", "turn_id", "intent", "current_hero", "target_enemy", "enemy_team_display"),
        }),
        ("질문 / AI 답변", {
            "fields": ("related_question", "message_box", "matchup_card_display", "recommend_card_display", "suggested_questions_display"),
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

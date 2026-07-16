import json
import logging
import time
import uuid
import traceback

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .chatbot_graph import ROLE_LABELS, run_chatbot_graph, try_canned_shortcut
from .models import ChatLog
from .vision_stats import analyze_scoreboard_image, ScoreboardAnalysisError

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8MB

# content_type은 클라이언트가 보내는 값이라 그대로 신뢰하지 않고 화이트리스트로 검증한다.
ALLOWED_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
}

def index(request):
    return render(request, "chat.html")


def _fingerprint(value):
    """쿠키/헤더 값 원문을 로그에 그대로 남기면 CSRF 시크릿이 노출되므로,
    "두 요청이 같은 값을 보냈는지"만 비교할 수 있는 정도로만(길이 + 끝 6글자)
    잘라서 남긴다."""
    if not value:
        return None
    return {"len": len(value), "tail": value[-6:]}


def csrf_failure_debug(request, reason=""):
    """CSRF 403 발생 시 쿠키/헤더 상태를 진단 로그로 남기고 Django 기본 CSRF
    실패 페이지를 그대로 반환한다. settings.CSRF_FAILURE_VIEW로 등록해서 쓴다."""
    csrf_cookie = request.COOKIES.get("csrftoken")
    csrf_header = request.META.get("HTTP_X_CSRFTOKEN")
    session_cookie_present = "sessionid" in request.COOKIES
    try:
        session_key = request.session.session_key
        session_is_empty = request.session.is_empty()
    except Exception:
        session_key, session_is_empty = None, None

    logger.warning(
        "[CSRF DIAG] reason=%r path=%s method=%s "
        "csrf_cookie_present=%s csrf_cookie=%s "
        "x_csrftoken_header_present=%s x_csrftoken_header=%s "
        "session_cookie_present=%s session_key=%s session_is_empty=%s "
        "content_type=%r referer=%r origin=%r user_agent=%r",
        reason, request.path, request.method,
        csrf_cookie is not None, _fingerprint(csrf_cookie),
        csrf_header is not None, _fingerprint(csrf_header),
        session_cookie_present, session_key, session_is_empty,
        request.META.get("CONTENT_TYPE"),
        request.META.get("HTTP_REFERER"),
        request.META.get("HTTP_ORIGIN"),
        (request.META.get("HTTP_USER_AGENT") or "")[:120],
    )

    from django.views.csrf import csrf_failure as django_csrf_failure
    return django_csrf_failure(request, reason=reason)

def save_chat_log(
    *,
    log_session_id,
    turn_id,
    role,
    message,
    intent=None,
    current_hero=None,
    target_enemy=None,
    metadata=None,
):
    """USER / AI / ERROR 메시지를 ChatLog에 저장한다."""
    ChatLog.objects.create(
        log_session_id=log_session_id,
        turn_id=turn_id,
        role=role,
        message=message,
        intent=intent,
        current_hero=current_hero,
        target_enemy=target_enemy,
        metadata=metadata or {},
    )


@require_http_methods(["POST"])
def chat_api(request):
    try:
        data = json.loads(request.body or b"{}")

        message = data.get("message", "").strip()
        role_filter = data.get("role_filter")
        # "어떤 영웅 기준으로?" 되묻기에 사용자가 고른 영웅. role_filter 버튼과
        # 동일하게 message=''와 함께 온다.
        focus_hero = (data.get("focus_hero") or "").strip() or None
        reset = bool(data.get("reset", False))

        # 알 수 없는 값이면 None으로 넘겨 run_chatbot_graph가 세션에 남은 이전
        # 선택(없으면 기본값 detailed)을 쓰도록 한다.
        answer_style = data.get("answer_style")
        if answer_style not in ("simple", "detailed"):
            answer_style = None

        if reset:
            request.session.pop("coach_context", None)
            request.session.modified = True
            return JsonResponse({"ok": True})

        if not message and not role_filter and not focus_hero:
            return JsonResponse(
                {"error": "질문을 입력해주세요."},
                status=400,
            )

        # Django 세션키는 로그인/세션 만료 등으로 바뀔 수 있어 로그 식별자로
        # 쓰기에 적합하지 않다. 로그 전용 UUID를 따로 저장해 대화 묶음을 추적한다.
        if not request.session.get("log_session_id"):
            request.session["log_session_id"] = str(uuid.uuid4())
            request.session.modified = True

        log_session_id = request.session["log_session_id"]
        turn_id = str(uuid.uuid4())

        conversation_context = request.session.get("coach_context", {})
        context_before = dict(conversation_context)

        # 웰컴 화면 5개 버튼(과 비슷한 표현)은 그래프 전체를 실행하지 않고 미리
        # 써둔 답을 즉시 돌려준다. 매칭되지 않으면 is_canned가 False로 남고
        # 평소처럼 run_chatbot_graph를 호출한다.
        canned = try_canned_shortcut(message, role_filter, conversation_context, answer_style)
        if canned["context_updates"]:
            conversation_context.update(canned["context_updates"])

        graph_message = message
        if canned["resume_message"] is not None:
            graph_message = canned["resume_message"]

        is_canned = canned["result"] is not None
        if is_canned:
            result = canned["result"]
        else:
            result = run_chatbot_graph(
                message=graph_message,
                conversation_context=conversation_context,
                role_filter=role_filter,
                answer_style=answer_style,
                focus_hero_pick=focus_hero,
            )

        context_patch = result.get("context_patch", {})
        conversation_context.update(context_patch)

        request.session["coach_context"] = conversation_context
        request.session.modified = True

        answer = result.get("answer", "")
        graph_error = result.get("error")

        current_hero = context_patch.get("current_hero") or conversation_context.get("current_hero")
        target_enemy = context_patch.get("target_enemy") or conversation_context.get("target_enemy")
        intent = result.get("intent")

        # 역할/영웅 선택 버튼을 클릭하면 message가 빈 문자열이고 role_filter/
        # focus_hero만 채워져 온다. 이 경우에도 로그를 남기되, 어떤 버튼을
        # 눌렀는지와 원래 질문(merge_context_node가 복원한 result["message"])을
        # 함께 표시해 맥락을 알 수 있게 한다.
        if message:
            user_log_message = message
        elif focus_hero:
            original_question = result.get("message") or ""
            user_log_message = (
                f"[영웅 선택: {focus_hero}] {original_question}".strip()
                if original_question
                else f"[영웅 선택: {focus_hero}]"
            )
        else:
            role_label = ROLE_LABELS.get(role_filter, role_filter)
            original_question = result.get("message") or ""
            user_log_message = (
                f"[역할 선택: {role_label}] {original_question}".strip()
                if original_question
                else f"[역할 선택: {role_label}]"
            )

        save_chat_log(
            log_session_id=log_session_id,
            turn_id=turn_id,
            role="USER",
            message=user_log_message,
            intent=intent,
            current_hero=current_hero,
            target_enemy=target_enemy,
            metadata={
                "role_filter": role_filter,
                "context_before": context_before,
            },
        )

        if graph_error:
            # run_chatbot_graph 내부 오류는 예외로 전파되지 않고 state["error"]로
            # 반환되므로, 이 경우 ERROR 로그로 남겨 관리자 페이지에서 구분한다.
            save_chat_log(
                log_session_id=log_session_id,
                turn_id=turn_id,
                role="ERROR",
                message=str(graph_error),
                intent=intent,
                current_hero=current_hero,
                target_enemy=target_enemy,
                metadata={
                    "context_before": context_before,
                    "context_after": conversation_context,
                    "context_patch": context_patch,
                    "source": "graph_error",
                },
            )
        else:
            save_chat_log(
                log_session_id=log_session_id,
                turn_id=turn_id,
                role="AI",
                message=answer,
                intent=intent,
                current_hero=current_hero,
                target_enemy=target_enemy,
                metadata={
                    "context_before": context_before,
                    "context_after": conversation_context,
                    "context_patch": context_patch,
                    "recommendation_type": result.get("recommendation_type"),
                    "recommended_heroes": result.get("recommended_heroes", []),
                    "suggested_questions": result.get("suggested_questions", []),
                    "choice_buttons": result.get("choice_buttons", []),
                    "has_stats": result.get("has_stats", False),
                    "answer_style": result.get("answer_style"),
                    "matchup_card": result.get("matchup_card"),
                    "recommend_card": result.get("recommend_card"),
                    **({"source": "canned_response"} if is_canned else {}),
                },
            )

        # 프론트에서 "이 답변이 별로예요" 피드백을 보낼 때 어떤 AI 답변에 대한
        # 피드백인지 알아야 하므로, 응답에 turn_id를 함께 내려준다.
        result["turn_id"] = turn_id

        return JsonResponse(result)

    except Exception as e:
        logger.exception("chat_api 오류: %s", e)

        try:
            if not request.session.get("log_session_id"):
                request.session["log_session_id"] = str(uuid.uuid4())
                request.session.modified = True

            save_chat_log(
                log_session_id=request.session["log_session_id"],
                turn_id=str(uuid.uuid4()),
                role="ERROR",
                message=str(e),
                metadata={
                    "traceback": traceback.format_exc(),
                },
            )
        except Exception:
            pass

        # 사용자에게는 서버 내부 정보가 노출될 수 있는 원본 예외 메시지 대신
        # 일반 메시지만 보여주고, 실제 원인은 관리자 페이지의 ERROR 로그에서 확인한다.
        return JsonResponse(
            {"error": "요청을 처리하는 중 오류가 발생했습니다."},
            status=500,
        )


@require_http_methods(["POST"])
def chat_feedback(request):
    """turn_id로 해당 ChatLog(role="AI")를 찾아 불만족 사유를 저장한다."""
    try:
        data = json.loads(request.body or b"{}")

        turn_id = (data.get("turn_id") or "").strip()
        reason = (data.get("reason") or "").strip()

        if not turn_id or not reason:
            return JsonResponse(
                {"error": "피드백 내용을 입력해주세요."},
                status=400,
            )

        updated = ChatLog.objects.filter(turn_id=turn_id, role="AI").update(
            is_unsatisfied=True,
            feedback_reason=reason,
        )

        if not updated:
            return JsonResponse(
                {"error": "해당 답변을 찾을 수 없습니다."},
                status=404,
            )

        return JsonResponse({"ok": True})

    except Exception as e:
        logger.exception("chat_feedback 오류: %s", e)
        return JsonResponse({"error": "피드백 저장 중 오류가 발생했습니다."}, status=500)


@require_http_methods(["POST"])
def chat_scoreboard_ocr(request):
    """TAB 점수판 스크린샷을 분석해 표+코치 피드백 마크다운(report)을 반환한다.

    분석은 vision_stats.analyze_scoreboard_image가 담당한다(팀/역할/본인 판별과
    영웅 인식은 OpenCV, 숫자와 피드백 생성은 Gemini). 진단 정보(admin_log)는
    ChatLog.metadata에만 남기고 사용자 응답에는 포함하지 않는다.
    """
    try:
        image_file = request.FILES.get("image")

        if not image_file:
            return JsonResponse({"error": "이미지 파일이 없습니다."}, status=400)

        if image_file.size > MAX_IMAGE_BYTES:
            return JsonResponse({"error": "이미지 파일이 너무 큽니다."}, status=400)

        mime_type = image_file.content_type or "image/png"
        if mime_type not in ALLOWED_IMAGE_TYPES:
            return JsonResponse(
                {"error": "지원하지 않는 이미지 형식입니다."},
                status=400,
            )

        # vision_stats가 디버그 이미지를 logs/scoreboard_debug/{turn_id}/에
        # 저장하므로, 분석 호출 전에 만든 turn_id를 아래 ChatLog에도 그대로 써야
        # 관리자 페이지에서 로그와 이미지를 연결할 수 있다.
        turn_id = str(uuid.uuid4())

        image_bytes = image_file.read()
        result = analyze_scoreboard_image(image_bytes, mime_type=mime_type, turn_id=turn_id)
        admin_log = result.get("admin_log", {})

        if not request.session.get("log_session_id"):
            request.session["log_session_id"] = str(uuid.uuid4())
            request.session.modified = True

        save_chat_log(
            log_session_id=request.session["log_session_id"],
            turn_id=turn_id,
            role="AI",
            message=result["report"],
            intent="scoreboard_analysis",
            current_hero=admin_log.get("self_hero"),
            metadata={
                "source": "chat_scoreboard_analysis",
                "admin_log": admin_log,
            },
        )

        # 점수판에서 인식된 팀 조합을 coach_context에 남겨, 이어지는 채팅
        # 질문이 다시 언급하지 않아도 이어받게 한다. current_hero는 매 턴
        # 메시지에 직접 선언해야만 인정되는 값이라 여기서는 건드리지 않는다.
        hero_rows = admin_log.get("hero_rows", [])
        enemy_team = [
            r["hero"] for r in sorted(hero_rows, key=lambda r: r["row_index"])
            if r.get("team") == "enemy" and r.get("hero") and r["hero"] != "unknown"
        ]
        ally_team = [
            r["hero"] for r in sorted(hero_rows, key=lambda r: r["row_index"])
            if r.get("team") == "ally" and r.get("hero") and r["hero"] != "unknown"
        ]
        # 팀 조합 이름뿐 아니라 실제 K/D/A·피해량·치유량 수치도 함께 세션에
        # 남긴다. my_stats(본인 1인 스탯)는 chatbot_graph.infer_current_hero()가
        # current_hero 판단의 최우선 근거로 쓰므로, 여기서 채워두면 점수판
        # 분석 직후 후속 질문에서 current_hero/역할이 자동으로 확정된다.
        my_team_stats = result.get("my_team_stats") or {}
        enemy_team_stats = result.get("enemy_team_stats") or {}
        my_stats = result.get("my_stats") or {}
        if enemy_team or ally_team or my_team_stats or enemy_team_stats or my_stats:
            conversation_context = request.session.get("coach_context", {})
            if enemy_team:
                conversation_context["enemy_team"] = enemy_team
            if ally_team:
                conversation_context["ally_team"] = ally_team
            if my_team_stats:
                conversation_context["my_team_stats"] = my_team_stats
            if enemy_team_stats:
                conversation_context["enemy_stats"] = enemy_team_stats
            if my_stats:
                conversation_context["my_stats"] = my_stats
            # merge_context_node는 마지막 채팅 메시지로부터 10분이 지나면
            # coach_context를 초기화한다(SESSION_TIMEOUT_SECONDS). 점수판
            # 분석은 별도 엔드포인트라 이 타임스탬프를 갱신하지 않으면, 방금
            # patch한 값이 다음 채팅 질문에서 바로 삭제될 수 있다.
            conversation_context["last_message_ts"] = time.time()
            request.session["coach_context"] = conversation_context
            request.session.modified = True

        return JsonResponse({"report": result["report"], "turn_id": turn_id})

    except ScoreboardAnalysisError as e:
        logger.warning("chat_scoreboard_ocr 분석 실패: %s", e)
        return JsonResponse({"error": "이미지를 분석할 수 없습니다. 스탯창 화면 캡처인지 확인해주세요."}, status=400)

    except Exception as e:
        logger.exception("chat_scoreboard_ocr 오류: %s", e)

        # 사용자에게는 일반 메시지만 보여주고, traceback은 ErrorChatLog에만 남긴다.
        try:
            if not request.session.get("log_session_id"):
                request.session["log_session_id"] = str(uuid.uuid4())
                request.session.modified = True

            save_chat_log(
                log_session_id=request.session["log_session_id"],
                turn_id=str(uuid.uuid4()),
                role="ERROR",
                message=str(e),
                metadata={
                    "source": "chat_scoreboard_ocr",
                    "traceback": traceback.format_exc(),
                },
            )
        except Exception:
            pass

        return JsonResponse({"error": "이미지 분석 중 오류가 발생했습니다."}, status=500)
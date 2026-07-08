import base64
import json
import logging
import uuid
import traceback

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from langchain_core.messages import HumanMessage

from .chatbot_graph import ROLE_LABELS, run_chatbot_graph, call_llm_text, try_canned_shortcut
from .chatbot_service import get_chatbot_components
from .models import ChatLog

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 20 * 1024 * 1024  # 20MB

# 브라우저 MediaRecorder가 실제로 만들어낼 수 있는 오디오 포맷만 허용한다.
# content_type은 클라이언트가 보내는 값이라 그대로 신뢰하지 않고 화이트리스트로 검증한다.
ALLOWED_AUDIO_TYPES = {
    "audio/webm",
    "audio/wav",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
}

def index(request):
    return render(request, "chat.html")

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
    """
    USER / AI / ERROR 메시지를 DB(ChatLog)에 저장하는 함수.
    """
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
        reset = bool(data.get("reset", False))

        # 간단히/자세히 답변 스타일. 프론트가 보내지 않거나 알 수 없는 값이면 None으로
        # 넘겨 run_chatbot_graph가 세션에 남아있는 이전 선택(없으면 기본값 detailed)을
        # 쓰도록 한다.
        answer_style = data.get("answer_style")
        if answer_style not in ("simple", "detailed"):
            answer_style = None

        if reset:
            request.session.pop("coach_context", None)
            request.session.modified = True
            return JsonResponse({"ok": True})

        if not message and not role_filter:
            return JsonResponse(
                {"error": "질문을 입력해주세요."},
                status=400,
            )

        # Django 세션키(request.session.session_key)는 로그인/세션 만료 등으로
        # 바뀔 수 있어 로그 식별자로 쓰기에 적합하지 않다. 로그 전용 UUID를
        # 세션에 따로 저장해, Django 세션 자체와 무관하게 대화 묶음을 추적한다.
        if not request.session.get("log_session_id"):
            request.session["log_session_id"] = str(uuid.uuid4())
            request.session.modified = True

        log_session_id = request.session["log_session_id"]
        turn_id = str(uuid.uuid4())

        conversation_context = request.session.get("coach_context", {})
        context_before = dict(conversation_context)

        # 카운터/조합 추천/맵 운영/스탯 피드백/영웅 유지 — 웰컴 화면 5개 버튼(과
        # 비슷한 표현)은 그래프 전체를 실행하지 않고 미리 써둔 답을 즉시 돌려준다.
        # 매칭되지 않으면(대상이 다르거나 캐시가 없는 역할을 고른 경우)
        # is_canned가 False로 남고 평소처럼 run_chatbot_graph를 호출한다.
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

        # USER 질문 저장
        # 역할(전체/탱커/딜러/힐러) 버튼 클릭은 message가 빈 문자열이고
        # role_filter만 채워져 온다. 기존에는 "if message:"로 이 경우를 걸러
        # 아예 저장하지 않아서, 관리자 페이지에서 사용자가 어떤 버튼을 눌렀는지
        # 전혀 보이지 않고 뒤이은 AI 답변만 덩그러니 남는 문제가 있었다.
        # role_filter만 온 경우에도 로그를 남기되, 메시지는 이번 답변이 실제로
        # 응답하고 있는 원래 질문(merge_context_node가 pending_question에서
        # 복원한 result["message"])과 함께 표시해 맥락을 알 수 있게 한다.
        if message:
            user_log_message = message
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
            # run_chatbot_graph 내부 노드(judge_strategy/generate_answer 등)에서 예외가 나면
            # 파이썬 예외로 튀어오르지 않고 state["error"]에 담겨 정상 반환된다. 이 경우를
            # 걸러내지 않으면 답변 없이 빈 AI 로그만 남아 관리자 페이지에서 실패를 알아챌
            # 수 없었다. USER/AI를 구분하지 않고 실패로 남긴다.
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
            # AI 답변 저장
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
    """
    사용자가 특정 AI 답변에 만족하지 못해 남긴 이유를 저장한다.
    turn_id로 해당 답변의 ChatLog(role="AI") 행을 찾아 표시한다.
    """
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
def chat_stt(request):
    """
    사용자가 마이크로 녹음한 음성을 Gemini(멀티모달 입력)로 전사(STT)한다.
    프론트에서 녹음을 멈춘 뒤 오디오 파일 하나를 통째로 보내면, 그 안의
    한국어 음성을 텍스트로 받아써서 돌려준다.
    """
    try:
        audio_file = request.FILES.get("audio")

        if not audio_file:
            return JsonResponse({"error": "오디오 파일이 없습니다."}, status=400)

        if audio_file.size > MAX_AUDIO_BYTES:
            return JsonResponse({"error": "오디오 파일이 너무 큽니다."}, status=400)

        # 브라우저 MediaRecorder가 만든 Blob의 mime type(예: audio/webm)이
        # 업로드 시 content_type에 담겨오지만, 이는 클라이언트가 보낸 값이라
        # 그대로 신뢰하지 않고 화이트리스트로 검증한다.
        mime_type = audio_file.content_type or "audio/webm"
        if mime_type not in ALLOWED_AUDIO_TYPES:
            return JsonResponse(
                {"error": "지원하지 않는 오디오 형식입니다."},
                status=400,
            )

        audio_bytes = audio_file.read()
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

        _bot, _retriever, llm = get_chatbot_components()

        message = HumanMessage(content=[
            {
                "type": "text",
                "text": (
                    "다음 오디오에 담긴 한국어 음성을 있는 그대로 받아써줘. "
                    "인사말이나 설명 없이 받아쓴 텍스트만 출력해. "
                    "음성이 없거나 알아들을 수 없으면 빈 문자열만 출력해."
                ),
            },
            {
                "type": "file",
                "source_type": "base64",
                "mime_type": mime_type,
                "data": audio_b64,
            },
        ])

        text = call_llm_text(llm, [message]).strip()

        return JsonResponse({"text": text})

    except Exception as e:
        logger.exception("chat_stt 오류: %s", e)

        # 사용자에게는 서버 내부 정보(경로/설정 등)가 노출될 수 있는 원본 예외
        # 메시지 대신 일반 메시지만 보여주고, 실제 원인은 관리자 페이지의
        # ERROR 로그(ErrorChatLog)에서 확인할 수 있도록 traceback을 남긴다.
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
                    "source": "chat_stt",
                    "traceback": traceback.format_exc(),
                },
            )
        except Exception:
            pass

        return JsonResponse({"error": "음성 인식 중 오류가 발생했습니다."}, status=500)
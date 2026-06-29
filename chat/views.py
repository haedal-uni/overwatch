import json
import logging
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .chatbot_graph import run_chatbot_graph

logger = logging.getLogger(__name__)

def index(request):
    return render(request, "chat.html")


def save_user_question_jsonl(message, extra_context=None, request=None):
    """
    사용자가 입력한 질문을 DB가 아닌 jsonl 파일로 저장하는 함수

    저장 위치:
    BASE_DIR/logs/chat_questions.jsonl
    """
    if not message:
        return

    extra_context = extra_context or {}

    # logs 폴더 생성
    log_dir = Path(settings.BASE_DIR) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "chat_questions.jsonl"

    # 세션 키가 없으면 생성
    if request and not request.session.session_key:
        request.session.create()

    data = {
        "time": timezone.localtime().strftime("%Y-%m-%d %H:%M:%S"),
        "message": message,
        "map_name": extra_context.get("map_name", ""),
        "side": extra_context.get("side", ""),
        "session_key": request.session.session_key if request else "",
    }

    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


@csrf_exempt
@require_http_methods(["POST"])
def chat_api(request):
    try:
        data = json.loads(request.body or b"{}")

        message = str(data.get("message", "")).strip()
        role_filter = data.get("role_filter") or None
        reset = bool(data.get("reset", False))
        extra_context = data.get("extra_context") or {}

        # 대화 초기화
        if reset:
            request.session.pop("coach_context", None)
            request.session.modified = True
            return JsonResponse({"ok": True})

        if not message and not role_filter:
            return JsonResponse({"error": "질문을 입력해주세요."}, status=400,)

        # 사용자가 직접 입력한 질문만 jsonl 파일에 저장
        # AI 응답 오류가 나도 질문은 먼저 저장됨
        if message:
            try:
                save_user_question_jsonl(
                    message=message,
                    extra_context=extra_context,
                    request=request,
                )
            except Exception as log_exc:
                # 질문 저장 실패가 챗봇 응답 실패로 이어지지 않도록 처리
                logger.exception("질문 저장 실패: %s", log_exc)

        # 세션 컨텍스트 + extra_context 병합
        conversation_context = {
            **(request.session.get("coach_context", {}) or {}),
            **extra_context,
        }

        # LangGraph 실행
        graph_result = run_chatbot_graph(
            message=message,
            conversation_context=conversation_context,
            role_filter=role_filter,
        )

        if "error" in graph_result:
            return JsonResponse(graph_result, status=500)

        result = dict(graph_result)
        context_patch = result.pop("context_patch", {}) or {}

        base_context = request.session.get("coach_context", {}) or {}
        updated_context = {**base_context, **context_patch}

        if not result.get("choice_buttons"):
            updated_context.pop("pending_question", None)
            updated_context.pop("pending_intent", None)

        request.session["coach_context"] = updated_context
        request.session.modified = True

        return JsonResponse(result)

    except json.JSONDecodeError:
        return JsonResponse({"error": "요청 body가 올바른 JSON 형식이 아닙니다."}, status=400)
    except Exception as exc:
        logger.exception("chat_api 오류: %s", exc)
        payload = {"error": "서버 내부 오류 발생"}
        if settings.DEBUG:
            payload["detail"] = str(exc)
        return JsonResponse(payload, status=500)
import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .chatbot_graph import run_chatbot_graph

logger = logging.getLogger(__name__)

def index(request):
    return render(request, "chat.html")


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
            return JsonResponse({"error": "질문을 입력해주세요."}, status=400)

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
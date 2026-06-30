import json
import logging
import uuid
import traceback
from pathlib import Path
from datetime import datetime

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .chatbot_graph import run_chatbot_graph

logger = logging.getLogger(__name__)

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "chat_questions.jsonl"

def index(request):
    return render(request, "chat.html")

def save_chat_jsonl(
    *,
    session_key,
    turn_id,
    role,
    message,
    intent=None,
    current_hero=None,
    target_enemy=None,
    metadata=None,
):
    """
    USER / AI / ERROR 메시지를 jsonl 파일로 저장하는 함수
    한 줄에 하나의 JSON 객체를 저장한다.
    """

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    row = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "session_key": session_key,
        "turn_id": turn_id,
        "role": role,
        "message": message,
        "intent": intent,
        "current_hero": current_hero,
        "target_enemy": target_enemy,
        "metadata": metadata or {},
    }

    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


@csrf_exempt
@require_http_methods(["POST"])
def chat_api(request):
    try:
        data = json.loads(request.body or b"{}")

        message = data.get("message", "").strip()
        role_filter = data.get("role_filter")
        reset = bool(data.get("reset", False))

        if reset:
            request.session.pop("coach_context", None)
            request.session.modified = True
            return JsonResponse({"ok": True})

        if not message and not role_filter:
            return JsonResponse(
                {"error": "질문을 입력해주세요."},
                status=400,
            )

        if not request.session.session_key:
            request.session.create()

        session_key = request.session.session_key
        turn_id = str(uuid.uuid4())

        conversation_context = request.session.get("coach_context", {})
        context_before = dict(conversation_context)

        result = run_chatbot_graph(
            message=message,
            conversation_context=conversation_context,
            role_filter=role_filter,
        )

        context_patch = result.get("context_patch", {})
        conversation_context.update(context_patch)

        request.session["coach_context"] = conversation_context
        request.session.modified = True

        answer = result.get("answer", "")

        current_hero = context_patch.get("current_hero") or conversation_context.get("current_hero")
        target_enemy = context_patch.get("target_enemy") or conversation_context.get("target_enemy")
        intent = result.get("intent")

        # USER 질문 저장
        if message:
            save_chat_jsonl(
                session_key=session_key,
                turn_id=turn_id,
                role="USER",
                message=message,
                intent=intent,
                current_hero=current_hero,
                target_enemy=target_enemy,
                metadata={
                    "role_filter": role_filter,
                    "context_before": context_before,
                },
            )

        # AI 답변 저장
        save_chat_jsonl(
            session_key=session_key,
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
            },
        )

        return JsonResponse(result)

    except Exception as e:
        error_message = str(e)

        try:
            if not request.session.session_key:
                request.session.create()

            save_chat_jsonl(
                session_key=request.session.session_key,
                turn_id=str(uuid.uuid4()),
                role="ERROR",
                message=error_message,
                metadata={
                    "traceback": traceback.format_exc(),
                },
            )
        except Exception:
            pass

        return JsonResponse(
            {"error": error_message},
            status=500,
        )
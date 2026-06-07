import json

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from rag_class import ChatBot

_rag = None
_llm = None
_retriever = None

def get_rag_components():
    global _rag, _llm, _retriever

    if _rag is None:
        _rag = ChatBot()
        _llm = _rag.get_llm()
        _retriever = _rag.build_rag_components()

    return _rag, _llm, _retriever


def index(request):
    return render(request, "chat.html")


@csrf_exempt
@require_http_methods(["POST"])
def chat_api(request):
    try:
        data = json.loads(request.body)
        message = data.get("message", "").strip()

        if not message:
            return JsonResponse(
                {"error": "질문을 입력해주세요."},
                status=400,
            )
        rag, llm, retriever = get_rag_components()

        result = rag.answer(
            retriever=retriever,
            llm=llm,
            message=message,
        )

        return JsonResponse(result)

    except FileNotFoundError as e:
        return JsonResponse(
            {
                "error": (
                    "VectorDB가 없습니다. "
                    "create_vectorstore()를 먼저 실행하세요.\n"
                    f"{str(e)}"
                )
            },
            status=500,
        )

    except json.JSONDecodeError:
        return JsonResponse(
            {"error": "요청 body가 올바른 JSON 형식이 아닙니다."},
            status=400,
        )

    except Exception as e:
        return JsonResponse(
            {"error": str(e)},
            status=500,
        )

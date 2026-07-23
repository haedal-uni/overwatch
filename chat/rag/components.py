import logging
import threading
from typing import Any, Optional, Tuple

from chat.rag.vectorstore import ChatBot

logger = logging.getLogger(__name__)

_chatbot: Optional[ChatBot] = None
_retriever: Optional[Any] = None
_llm: Optional[Any] = None

# 임베딩 모델 로드는 수십 초에 GB 단위 메모리를 쓰므로 락으로 최초 1회만
# 만든다. 내부에서도 여러 스레드가 이 함수를 부른다(retrieve_docs_node).
_init_lock = threading.Lock()


def initialize_chatbot() -> Tuple[ChatBot, Any, Any]:
    """RAG 챗봇에 필요한 무거운 컴포넌트를 최초 1회만 초기화하고 이후 재사용한다."""
    global _chatbot, _retriever, _llm

    # 초기화가 끝난 뒤에는 락 없이 바로 반환한다(대부분의 호출이 이 경로).
    if _chatbot is not None and _retriever is not None and _llm is not None:
        return _chatbot, _retriever, _llm

    with _init_lock:
        # 락을 기다리는 동안 다른 스레드가 초기화를 끝냈을 수 있으므로 다시 확인한다.
        if _chatbot is not None and _retriever is not None and _llm is not None:
            return _chatbot, _retriever, _llm

        logger.info("ChatBot 컴포넌트 초기화 시작")

        bot = ChatBot()
        retriever = bot.build_rag_components()
        llm = bot.get_llm()

        _chatbot = bot
        _retriever = retriever
        _llm = llm

        logger.info("ChatBot 컴포넌트 초기화 완료")

    return _chatbot, _retriever, _llm


def get_chatbot_components() -> Tuple[ChatBot, Any, Any]:
    """LangGraph 노드에서 쓰는 공용 컴포넌트 getter."""
    return initialize_chatbot()
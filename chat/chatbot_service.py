# chatbot_service.py
import logging
from typing import Any, Optional, Tuple

from .rag_class import ChatBot

logger = logging.getLogger(__name__)

_chatbot: Optional[ChatBot] = None
_retriever: Optional[Any] = None
_llm: Optional[Any] = None


def initialize_chatbot() -> Tuple[ChatBot, Any, Any]:
    """
    RAG 챗봇에 필요한 무거운 컴포넌트를 한 번만 초기화한다.
    """
    global _chatbot, _retriever, _llm

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
    """
    LangGraph 노드에서 사용하는 공용 컴포넌트 getter.
    """
    return initialize_chatbot()
"""터미널에서 RAG 검색/답변을 직접 확인하는 개발용 CLI.

    python manage.py rag_cli

원래 이 코드는 `chat/rag/vectorstore.py`(당시 `chat/rag_class.py`)의 `ChatBot` 안에 있었다. 웹 서비스가 쓰는
컴포넌트(`build_rag_components`/`get_llm`)와 CLI 전용 대화 기록(`chat_history`)
이 한 클래스에 섞여 있었고, 그 인스턴스는 `chatbot_service`에서 싱글턴으로
공유되기 때문에 "이 chat_history를 웹 대화 기억으로 쓰면 안 된다"는 주석으로만
막아둔 상태였다. CLI를 이쪽으로 옮겨 그 위험 자체를 없앴다.

여기서 만드는 대화 기록은 이 명령 실행 동안만 살아있는 지역 변수라 웹 요청과
절대 섞이지 않는다.
"""

import logging

from django.core.management.base import BaseCommand
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from chat.rag.vectorstore import ChatBot

logger = logging.getLogger(__name__)

MAX_HISTORY = 5

SEARCH_QUERY_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "당신은 RAG 검색 질문을 재작성하는 도우미입니다.\n"
            "이전 대화가 필요한 표현이 있으면 구체적인 검색 질문으로 바꾸세요.\n"
            "답변하지 말고 재작성된 검색 질문만 출력하세요.",
        ),
        (
            "human",
            "이전 대화:\n{chat_history}\n\n"
            "현재 질문:\n{question}\n\n"
            "재작성된 검색 질문:",
        ),
    ]
)

ANSWER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "당신은 오버워치 2 전문 코치이자 분석가입니다.\n"
            "제공된 문서를 바탕으로 질문의 의도를 파악하고 답변하세요.\n"
            "내부 chunk 번호나 출처 표시는 사용자에게 보여주지 마세요.",
        ),
        (
            "human",
            "이전 대화:\n{chat_history}\n\n"
            "참고 문서:\n{context}\n\n"
            "현재 질문:\n{question}",
        ),
    ]
)


def format_chat_history(history):
    if not history:
        return "이전 대화 없음"

    blocks = []
    for i, turn in enumerate(history[-MAX_HISTORY:], start=1):
        blocks.append(f"[이전 대화 {i}]\n사용자: {turn['user']}\nAI: {turn['ai']}")
    return "\n\n".join(blocks)


def build_search_query(llm, history, message):
    """이전 대화가 있으면 현재 질문을 검색용 질문으로 재작성한다."""
    question = message.strip()
    if not history:
        return question

    chain = SEARCH_QUERY_PROMPT | llm | StrOutputParser()
    rewritten = chain.invoke(
        {"chat_history": format_chat_history(history), "question": question}
    ).strip()

    return rewritten or question


def answer_once(retriever, llm, history, message):
    """질문 하나에 대한 (답변, 참고 chunk 목록)을 만든다."""
    original_question = message.strip()
    query = build_search_query(llm, history, original_question)
    docs = retriever.invoke(query)

    if not docs:
        return "관련 문서를 찾지 못했습니다. 질문을 조금 더 구체적으로 입력해주세요.", []

    context_blocks = []
    references = []
    for i, doc in enumerate(docs, start=1):
        context_blocks.append(doc.page_content)

        meta = getattr(doc, "metadata", {}) or {}
        header_info = " > ".join(v for k, v in sorted(meta.items()) if k in ("H1", "H2", "H3"))
        preview = doc.page_content.strip()[:100].replace("\n", " ")
        ref_label = f"[{header_info}] " if header_info else ""
        references.append(f"청크 {i}: {ref_label}...{preview}...")

    chain = ANSWER_PROMPT | llm | StrOutputParser()
    ai_answer = chain.invoke(
        {
            "chat_history": format_chat_history(history),
            "context": "\n\n".join(context_blocks),
            "question": query,
        }
    ).strip()

    return ai_answer, list(dict.fromkeys(references))


class Command(BaseCommand):
    help = "터미널에서 RAG 챗봇(검색 + 답변)을 직접 테스트한다."

    def handle(self, *args, **options):
        bot = ChatBot()

        try:
            llm = bot.get_llm()
            retriever = bot.build_rag_components()
        except Exception as exc:
            self.stderr.write(f"llm, vectorDB 호출에 실패했습니다: {exc}")
            return

        history = []

        while True:
            try:
                message = input("[질문 (q:종료) ] : ")
            except (EOFError, KeyboardInterrupt):
                self.stdout.write("")
                return

            if not message.strip():
                self.stdout.write("질문을 입력해주세요.")
                continue

            if message.strip().lower() in ("q", "quit", "exit"):
                return

            ai_answer, references = answer_once(retriever, llm, history, message)

            if references:
                self.stdout.write("참고한 문서 chunk:")
                for ref in references:
                    self.stdout.write(f"- {ref}")
            else:
                self.stdout.write("참고한 문서 chunk 없음")

            self.stdout.write(f"[AI] {ai_answer}")

            history.append({"user": message.strip(), "ai": ai_answer})
            if len(history) > MAX_HISTORY:
                history = history[-MAX_HISTORY:]

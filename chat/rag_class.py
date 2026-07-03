import logging
import os
import shutil

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_community.document_loaders import TextLoader
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

logger = logging.getLogger(__name__)

MD_PATH = "source/overwatch.md"
DB_PATH = "./chroma_db_overwatch"
COLLECTION_NAME = "overwatch_docs"

# Component class
class ChatBot:
    """
    - markdown 문서 로드
    - 문서 chunk 분할
    - HuggingFace embedding 로드
    - Chroma vectorstore 생성/로드
    - Gemini LLM 로드
    - CLI 테스트용 질의응답

    주의:
    - self.chat_history는 CLI 테스트용이다.
    - Django 웹 서비스에서는 사용자별 세션 컨텍스트를 사용해야 하므로
      singleton ChatBot의 chat_history를 웹 대화 기억으로 쓰면 안 된다.
    """

    def __init__(
        self,
        md_path=MD_PATH,
        db_path=DB_PATH,
        collection_name=COLLECTION_NAME,
        chunk_size=800,
        chunk_overlap=50,
        search_k=7,
        embedding_device="cpu",
        llm_model="gemini-3.1-flash-lite", # gemini-2.5-flash
        temperature=0,
        max_history=5,
    ):
        load_dotenv()

        self.md_path = md_path
        self.db_path = db_path
        self.collection_name = collection_name
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.search_k = search_k
        self.embedding_device = embedding_device
        self.llm_model = llm_model
        self.temperature = temperature

        # CLI 전용 대화 기록
        self.chat_history = []
        self.max_history = max_history

        self._embeddings = None
        self._llm = None

    def load_docs(self):
        """
        markdown 문서를 읽고 RAG 검색용 chunk로 나눈다.
        """
        if not os.path.exists(self.md_path):
            raise FileNotFoundError(f"'{self.md_path}' 파일이 없습니다.")

        loader = TextLoader(self.md_path, encoding="utf-8")
        pages = loader.load()

        if not pages:
            raise ValueError(f"'{self.md_path}' 파일에서 내용을 읽지 못했습니다.")

        md_text = pages[0].page_content

        header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "H1"),
                ("##", "H2"),
                ("###", "H3"),
            ],
            strip_headers=False,
        )
        header_docs = header_splitter.split_text(md_text)

        char_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", " ", ""],
        )
        docs = char_splitter.split_documents(header_docs)
        docs = [doc for doc in docs if doc.page_content.strip()]

        logger.info("헤더 분할: %s개 섹션", len(header_docs))
        logger.info("최종 chunk: %s개", len(docs))

        return docs

    def get_embeddings(self):
        """
        HuggingFace embedding 모델을 lazy loading한다.
        """
        if self._embeddings is None:
            from langchain_huggingface import HuggingFaceEmbeddings

            logger.info("임베딩 모델 로드: BAAI/bge-m3")

            self._embeddings = HuggingFaceEmbeddings(
                model_name="BAAI/bge-m3",
                model_kwargs={"device": self.embedding_device},
                encode_kwargs={"normalize_embeddings": True},
            )

        return self._embeddings

    def create_vectorstore(self, reset=True):
        """
        markdown 문서를 chunk로 나눈 뒤 Chroma vectorstore를 생성한다.
        """
        logger.info("=" * 50)
        logger.info("[VectorStore 생성] markdown 문서 저장 시작")

        docs = self.load_docs()

        if reset and os.path.exists(self.db_path):
            shutil.rmtree(self.db_path)

        vectorstore = Chroma.from_documents(
            documents=docs,
            embedding=self.get_embeddings(),
            collection_name=self.collection_name,
            persist_directory=self.db_path,
        )

        logger.info("%s개 chunk를 '%s'에 저장했습니다.", len(docs), self.db_path)
        return vectorstore

    def build_rag_components(self):
        """
        RAG 검색에 사용할 retriever를 준비한다.
        """
        if not os.path.exists(self.db_path):
            logger.info("'%s' 디렉토리가 없습니다. VectorStore를 새로 생성합니다.", self.db_path)
            self.create_vectorstore(reset=True)

        vectorstore = Chroma(
            collection_name=self.collection_name,
            embedding_function=self.get_embeddings(),
            persist_directory=self.db_path,
        )

        try:
            count = vectorstore._collection.count()
            logger.info("VectorStore: %s개 chunk 로드 완료", count)
        except Exception:
            logger.info("VectorStore 로드 완료")

        retriever = vectorstore.as_retriever(
            search_type="similarity",
            search_kwargs={"k": self.search_k},
        )

        return retriever

    def get_llm(self):
        """
        Gemini LLM을 lazy loading한다.
        """
        if self._llm is None:
            api_key = os.getenv("GOOGLE_API_KEY")

            if not api_key:
                raise EnvironmentError("GOOGLE_API_KEY 환경 변수를 설정해주세요.")

            from langchain_google_genai import ChatGoogleGenerativeAI

            self._llm = ChatGoogleGenerativeAI(
                model=self.llm_model,
                google_api_key=api_key,
                temperature=self.temperature,
                max_output_tokens=1024,
            )

        return self._llm

    def format_chat_history(self):
        """CLI 테스트용 이전 대화 포맷팅."""
        if not self.chat_history:
            return "이전 대화 없음"

        recent_history = self.chat_history[-self.max_history:]

        history_text = []
        for i, chat in enumerate(recent_history, start=1):
            history_text.append(
                f"[이전 대화 {i}]\n"
                f"사용자: {chat['user']}\n"
                f"AI: {chat['ai']}"
            )

        return "\n\n".join(history_text)

    def add_chat_history(self, user_message, ai_message):
        """CLI 테스트용 대화 기록 저장."""
        self.chat_history.append({"user": user_message, "ai": ai_message})

        if len(self.chat_history) > self.max_history:
            self.chat_history = self.chat_history[-self.max_history:]

    def build_search_query(self, llm, message):
        """이전 대화가 있는 경우 현재 질문을 검색용 질문으로 재작성한다."""
        current_question = message.strip()

        if not self.chat_history:
            return current_question

        chat_history = self.format_chat_history()

        prompt = ChatPromptTemplate.from_messages(
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

        chain = prompt | llm | StrOutputParser()

        rewritten_query = chain.invoke(
            {
                "chat_history": chat_history,
                "question": current_question,
            }
        ).strip()

        return rewritten_query or current_question

    def build_prompt(self):
        """CLI 테스트용 최종 답변 prompt."""
        return ChatPromptTemplate.from_messages(
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

    def answer(self, retriever, llm, message):
        """CLI 테스트용 RAG 답변 함수."""
        original_question = message.strip()

        query = self.build_search_query(llm, original_question)
        docs = retriever.invoke(query)

        if not docs:
            ai_answer = "관련 문서를 찾지 못했습니다. 질문을 조금 더 구체적으로 입력해주세요."
            self.add_chat_history(user_message=original_question, ai_message=ai_answer)
            return {"answer": ai_answer, "references": []}

        context_blocks = []
        references = []

        for i, doc in enumerate(docs, start=1):
            context_blocks.append(doc.page_content)

            meta = getattr(doc, "metadata", {}) or {}
            header_info = " > ".join(
                v for k, v in sorted(meta.items()) if k in ("H1", "H2", "H3")
            )
            preview = doc.page_content.strip()[:100].replace("\n", " ")
            ref_label = f"[{header_info}] " if header_info else ""
            references.append(f"청크 {i}: {ref_label}...{preview}...")

        context = "\n\n".join(context_blocks)
        chat_history = self.format_chat_history()

        prompt = self.build_prompt()
        chain = prompt | llm | StrOutputParser()

        ai_answer = chain.invoke(
            {
                "chat_history": chat_history,
                "context": context,
                "question": query,
            }
        ).strip()

        self.add_chat_history(user_message=original_question, ai_message=ai_answer)

        return {
            "answer": ai_answer,
            "references": list(dict.fromkeys(references)),
        }

    def print_references(self, references):
        """CLI 터미널에만 참고 chunk를 출력한다."""
        if not references:
            logger.info("참고한 문서 chunk 없음")
            return

        logger.info("참고한 문서 chunk:")
        for ref in references:
            logger.info("- %s", ref)

    def generate_response(self, retriever, llm, human_message):
        """CLI에서 질문 하나에 대한 답변 문자열만 반환한다."""
        result = self.answer(retriever=retriever, llm=llm, message=human_message)
        self.print_references(result["references"])
        return result["answer"]

    def run_cli(self):
        """터미널에서 RAG 챗봇을 테스트하는 CLI 실행 함수."""
        try:
            llm = self.get_llm()
            retriever = self.build_rag_components()
        except Exception as exc:
            logger.exception("llm, vectorDB 호출에 실패: %s", exc)
            return

        while True:
            human_message = input("[질문 (q:종료) ] : ")

            if not human_message.strip():
                logger.info("질문을 입력해주세요.")
                continue

            if human_message.strip().lower() in ["q", "quit", "exit"]:
                return

            ai_message = self.generate_response(
                retriever=retriever,
                llm=llm,
                human_message=human_message,
            )

            logger.info("[AI] %s", ai_message)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bot = ChatBot()
    bot.run_cli()
import logging
import os
import shutil

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

logger = logging.getLogger(__name__)

MD_PATH = "source/overwatch.md"
DB_PATH = "./chroma_db_overwatch"
COLLECTION_NAME = "overwatch_docs"

class ChatBot:
    """markdown 문서를 chunk/임베딩/벡터스토어로 준비하고 Gemini LLM을 로드하는 RAG 컴포넌트 빌더.

    이 클래스는 "무거운 컴포넌트를 만들어주는 빌더"일 뿐 대화 상태를 갖지 않는다
    — `chatbot_service`가 인스턴스를 싱글턴으로 공유하므로 여기에 대화 기록 같은
    사용자별 상태를 두면 모든 사용자가 그것을 공유하게 된다. 대화 맥락은 Django
    세션의 `coach_context`가 담당한다.

    터미널에서 검색/답변을 직접 확인하는 CLI는 `python manage.py rag_cli`로
    분리돼 있다(chat/management/commands/rag_cli.py).
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
        llm_model="gemini-3.1-flash-lite",
        temperature=0,
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

        self._embeddings = None
        self._llm = None

    def load_docs(self):
        """markdown 문서를 읽고 RAG 검색용 chunk로 나눈다."""
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
        """HuggingFace embedding 모델을 최초 호출 시 생성하고 이후 재사용한다."""
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
        """markdown 문서를 chunk로 나눈 뒤 Chroma vectorstore를 생성한다."""
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
        """RAG 검색에 사용할 retriever를 준비한다."""
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
        """Gemini LLM을 최초 호출 시 생성하고 이후 재사용한다."""
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

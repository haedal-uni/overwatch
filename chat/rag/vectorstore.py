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

# 검색 인덱스에서 통째로 빼는 섹션(H1 기준).
#
# "6. 영웅 수 검증"은 영웅 이름만 나열해둔 검산용 메모라 답변 근거가 될 내용이
# 없는데, 모든 영웅 이름이 한 chunk에 들어있어서 어떤 영웅 질문에도 유사도가
# 높게 잡힌다. 실제 평가에서 카운터 질문의 검색 1위가 이 chunk였다 — 상위 k
# 자리를 하나 차지하고 정보는 주지 않으니, 그 자리에 들어왔어야 할 상성 문서가
# 밀려난다. 인덱싱에서 빼면 카운터 유형 MRR이 0.695 → 0.729로 오르고 다른
# 질문 유형에는 부작용이 없었다(rag_eval/eval_report.md 7절).
#
# 이 목록을 고치면 벡터스토어를 다시 만들어야 반영된다(create_vectorstore).
EXCLUDED_H1_SECTIONS = {"6. 영웅 수 검증"}

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
        # 2026-07-29 재측정으로 정한 값(rag_eval/eval_report.md).
        # overlap 0: 겹침을 주면 같은 내용의 chunk가 늘어 상위 k를 중복이 차지하고
        #   서로 다른 정답 섹션이 밀려난다(k=10에서 Hit@k 0.840 → 0.800).
        #   분할 경계 376개를 전수 조사한 결과 문장이 끊긴 사례는 0건이라 겹침이 필요 없었다.
        # search_k 10: k=7 대비 Hit@k가 0.680 → 0.840으로 오른다. chunk가 작아
        #   한 섹션이 여러 조각으로 나뉘므로 같은 내용을 담으려면 더 가져와야 한다.
        # 이 셋 중 하나라도 바꾸면 ChromaDB를 재구축해야 한다(chunk 경계가 달라진다).
        chunk_size=800,
        chunk_overlap=0,
        search_k=10,
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

        kept_docs = [
            doc for doc in header_docs
            if doc.metadata.get("H1") not in EXCLUDED_H1_SECTIONS
        ]
        if len(kept_docs) != len(header_docs):
            logger.info(
                "검색 제외 섹션: %s개 헤더 제거 (%s)",
                len(header_docs) - len(kept_docs),
                ", ".join(sorted(EXCLUDED_H1_SECTIONS)),
            )
            header_docs = kept_docs

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

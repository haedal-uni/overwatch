import os
import shutil

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_community.document_loaders import TextLoader
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_text_splitters import RecursiveCharacterTextSplitter

MD_PATH = "source/overwatch.md"
DB_PATH = "./chroma_db_overwatch"
COLLECTION_NAME = "overwatch_docs"

class ChatBot:
    def __init__(
        self,
        md_path: str = MD_PATH,
        db_path: str = DB_PATH,
        collection_name: str = COLLECTION_NAME,
        chunk_size: int = 800,
        chunk_overlap: int = 50,
        search_k: int = 7,
        embedding_device: str = "cpu",
        llm_model: str = "gemini-2.5-flash",
        temperature: float = 0,
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

    def load_docs(self):
        if not os.path.exists(self.md_path):
            print(f"[오류] '{self.md_path}' 파일이 없습니다.")
            return None

        loader = TextLoader(self.md_path, encoding="utf-8")
        pages = loader.load()

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n## ", "\n### ", "\n#### ", "\n", " ", ""],
        )

        docs = splitter.split_documents(pages)
        docs = [d for d in docs if d.page_content.strip()]

        print(f"  → {len(docs)}개 유효 chunk 준비 완료")
        return docs

    def get_embeddings(self):
        from langchain_huggingface import HuggingFaceEmbeddings
        print("  임베딩: BAAI/bge-m3 (로컬, API 키 불필요)")

        return HuggingFaceEmbeddings(
            model_name="BAAI/bge-m3",
            model_kwargs={"device": self.embedding_device},
            encode_kwargs={"normalize_embeddings": True},
        )

    def create_vectorstore(self):
        print("=" * 50)
        print("[VectorStore 생성] MD 파일 저장 중...")

        docs = self.load_docs()
        if docs is None:
            return None

        if os.path.exists(self.db_path):
            shutil.rmtree(self.db_path)

        vectorstore = Chroma.from_documents(
            documents=docs,
            embedding=self.get_embeddings(),
            collection_name=self.collection_name,
            persist_directory=self.db_path,
        )

        print(f"  → {len(docs)}개 chunk를 '{self.db_path}'에 저장했습니다.")
        return vectorstore

    def build_rag_components(self):
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(
                f"'{self.db_path}' 디렉토리가 없습니다.\n"
                "  → create_vectorstore()를 먼저 실행하세요."
            )

        vectorstore = Chroma(
            collection_name=self.collection_name,
            embedding_function=self.get_embeddings(),
            persist_directory=self.db_path,
        )

        count = vectorstore._collection.count()
        print(f"  VectorStore: {count}개 chunk 로드 완료 (오버워치 운영 가이드)")

        retriever = vectorstore.as_retriever(
            search_type="similarity",
            search_kwargs={"k": self.search_k},  # top-k 개수 설정
        )

        return retriever

    def get_llm(self):
        api_key = os.getenv("GOOGLE_API_KEY")

        if not api_key:
            raise EnvironmentError("GOOGLE_API_KEY 환경 변수를 설정해주세요.")

        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=self.llm_model,
            google_api_key=api_key,
            temperature=self.temperature,
        )

    def build_prompt(self):
        return ChatPromptTemplate.from_messages([
        (
            "system",
            "당신은 오버워치 2 전문 코치이자 분석가입니다.\n"
            "제공된 문서를 바탕으로 질문의 의도를 파악하고, 단편적인 정보들을 논리적으로 조합하여 답변하세요.\n\n"
            
            "【지식 조합 및 추론 규칙】\n"
            "- 문장에 직접적인 답이 없더라도, 영웅들의 스킬 메커니즘(예: 투사체 판정, 광선 판정, 방벽 등)과 상성을 문서에서 찾아내어 논리적으로 연결하세요.\n"
            "- (예: 'A의 스킬은 투사체를 막는다'와 'B의 공격은 광선이다'라는 정보가 있다면, 'B가 A를 카운터할 수 있다'고 유추하여 답변할 것)\n"
            "- 주어진 문서로 판단이 불가능한 영역은 무리하게 지어내지 말고, '[참고 문서 외 의견]'이라고 명확히 구분하여 답변하세요.\n\n"
            
            "【답변 원칙 및 제한 사항】\n"
            "- 사용자가 질문한 역할군(탱커/딜러/서포터)이나 상황에 일치하는 영웅만 추천하세요. (딜러 카운터를 묻는데 힐러를 추천하지 말 것)\n"
            "- 모든 영웅 및 스킬 이름은 반드시 '오버워치 2 한국어 공식 명칭'만 사용하세요. 영문 표기나 알파벳 단축키(E스킬, Shift스킬 등) 대신 공식 스킬명(예: 튕겨내기(E 스킬), 소멸(Shift))을 쓰세요.\n\n"
            
            "【질문 유형별 출력 형식】\n"
            "질문 유형에 딱 맞는 항목만 선택하여 답변하고, 불필요한 인사말이나 격려 문구는 절대 쓰지 마세요.\n\n"
            
            "1. 카운터/픽 추천 질문인 경우:\n"
            "### 추천 영웅: [영웅 이름]\n"
            "- **카운터 이유 (상성 분석):** [스킬 판정 및 메커니즘 관점에서 조합한 이유를 3줄 이내로 작성]\n"
            "- **핵심 스킬 대치법:** [카운터 칠 때 핵심이 되는 스킬 활용법을 2줄 이내로 작성]\n\n"
            
            "2. 운영법/스킬 질문인 경우:\n"
            "### [영웅 이름/스킬 이름] 핵심 분석\n"
            "- **핵심 전략/설명:** [운영 및 스킬 메커니즘 설명을 3줄 이내로 작성]\n"
            "- **상황별 활용 팁:** [실전에서 쓸 수 있는 팁을 2줄 이내로 작성]"
        ),
        ("human", "참고 문서:\n{context}\n\n질문: {question}"),
    ])

    def answer(self, retriever, llm, message: str):
        cleaned = message.strip().rstrip("?!.")
        docs = retriever.invoke(cleaned)
        context_blocks = []
        references = []

        for i, d in enumerate(docs):
            source = d.metadata.get("source", "알 수 없음")

            block = f"[청크 {i + 1} | 출처: {source}]\n{d.page_content}"
            context_blocks.append(block)

            preview = d.page_content.strip()[:100].replace("\n", " ")
            references.append(f"청크 {i + 1}: ...{preview}...")
        context = "\n\n".join(context_blocks)

        prompt = self.build_prompt()
        chain = prompt | llm | StrOutputParser()
        answer = chain.invoke({
            "context": context,
            "question": message,
        })
        return {
            "answer": answer.strip(),
            "references": list(dict.fromkeys(references)),  # 중복 제거 + 순서 유지
        }

    def runnable_lambda(self, retriever, llm, human_message: str):
        result = self.answer(
            retriever=retriever,
            llm=llm,
            message=human_message,
        )
        print("\n📌 참고한 문서 chunk (터미널 전용):")
        for ref in result["references"]:
            print(f"  - {ref}")

        return result["answer"]

    def run_cli(self):
        try:
            llm = self.get_llm()
            retriever = self.build_rag_components()

        except Exception as exc:
            print(f"llm, vectorDB 호출에 실패 - [상세 오류] : {exc}")
            return

        while True:
            human_message = input("[질문 (q:종료) ] : ")

            if human_message == "q":
                return

            ai_message = self.runnable_lambda(
                retriever=retriever,
                llm=llm,
                human_message=human_message,
            )

            print(f"[AI] {ai_message}")
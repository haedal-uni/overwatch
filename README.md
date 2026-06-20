# overwatch
오버워치 2 영웅 추천, 카운터 픽, 운영 팁을 문서 기반으로 답변하는 RAG 챗봇

<img src="https://github.com/user-attachments/assets/0d1a274a-2baf-4e25-b40a-1143824aa274" />

채팅 화면

<br><br>

<img src="https://github.com/user-attachments/assets/e3e2bfe1-c0e7-441c-8060-3772d8f1ab5d" width="40%" />

<br><br> 

<img src="https://github.com/user-attachments/assets/76d20276-4ca0-437e-b11a-09fe1cc62d50" />

공수 구분이 있을 경우 선택 버튼 

<br>

<img src="https://github.com/user-attachments/assets/9e56c7d3-8959-4490-86f4-ec9aa6552dba" />

맵 구분

<br><br>

<img src="https://github.com/user-attachments/assets/d0a866d7-199c-4c10-b799-354ac667f2cc" />

스탯 감지

<br><br> 

기존엔 단일 체인(prompt | llm | parser) 구조의 단순 RAG로 구현했으나

멀티턴 대화 맥락 유지와 역할 고정(Role Lock) 제약 하에서의 영웅 추천처럼

여러 단계의 판단과 분기가 필요해지면서 LangGraph로 대화 흐름을 노드 단위로 재설계

<br><br>

```
질문 검증 → 스탯/문맥 파싱(LLM) → 문맥 병합 → (필요 시 역할 필터 확인) →
검색 쿼리 생성 → ChromaDB 문서 검색 → 전략 판단(LLM) → 답변 생성(LLM) →
추천 질문 생성(LLM) → 응답 포맷팅
```
순서로 진행되는 LangGraph 기반 파이프라인

<br>

세션 컨텍스트(현재 영웅, 상대 영웅, 맵, 진행 중인 스탯 등)를 

매 턴마다 LLM 추출 결과와 규칙 기반 추출 결과를 함께 병합해 유지하며 

영웅이 바뀌거나 맵이 바뀌는 시점을 감지해 이전 상대 정보를 자동으로 초기화

<br>

<img src="https://github.com/user-attachments/assets/5368c7d4-0f2c-40c6-9fd7-3c8f57dc64eb" width="60%"/>


<br><br>

## Install
```bash
Set-ExecutionPolicy RemoteSigned -scope CurrentUser

powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

uv add python-dotenv langchain-chroma langchain-community langchain-core langchain-text-splitters langchain-huggingface sentence-transformers langchain-google-genai
```

### Django
```bash
uv init --python 3.11
uv add django langgraph
uv run django-admin startproject config .
uv run python manage.py startapp chat
uv run python manage.py runserver
```






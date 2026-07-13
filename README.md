# overwatch
오버워치 2 영웅 추천, 카운터 픽, 운영 팁을 문서 기반으로 답변하는 RAG 챗봇


https://github.com/user-attachments/assets/55608d31-8515-4a07-b698-2bdbb32e7998

<br><br> 

**도움말**      

<img src="https://github.com/user-attachments/assets/60137cb3-3d19-46b8-9223-45f0bf383d0b" width="60%" />

<img src="https://github.com/user-attachments/assets/e04de4fb-c1e5-48f3-a833-cbf291f3e3b2" width="80%" />   

<br><br>

**이미지 분석**    

<img src="https://github.com/user-attachments/assets/881567f9-4e82-41e1-9204-bfe1b45c040a" width="80%" />  

<br><br>

**맵 선택하기**

<img src="https://github.com/user-attachments/assets/6ccc5e90-ca8e-4885-94e5-061636b3d268" width="50%"/>   

<img src="https://github.com/user-attachments/assets/c5d7e5a9-f173-4819-9838-58ba27d4f831" width="70%"/>

<img src="https://github.com/user-attachments/assets/94d809b3-5e40-4a62-85f0-71bbfbd2df95" width="70%"/>

<br><br>

**스탯 감지**

<img src="https://github.com/user-attachments/assets/3bd9320a-3136-44b9-a134-ac5358a3be87" width="80%"/>    

<br><br> 

**대화 저장하기 및 불러오기**

<img src="https://github.com/user-attachments/assets/b6f9314e-3dd7-449d-8c48-5712a5044205"  width="80%"/>    

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

<img src="https://github.com/user-attachments/assets/152df4dc-5bc0-49a8-a410-ab256b1ec99d" width="50%"/>     

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






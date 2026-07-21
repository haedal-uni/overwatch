"""RAG 컴포넌트 계층(벡터 검색 + LLM).

- vectorstore.py  markdown 문서 → chunk → Chroma 벡터스토어, Gemini LLM 빌더
- components.py   무거운 컴포넌트(임베딩/retriever/LLM) 싱글턴 초기화
- llm_utils.py    LLM 호출/응답 파싱, retriever 호출 래퍼
"""

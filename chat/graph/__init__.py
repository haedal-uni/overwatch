"""LangGraph 챗봇 파이프라인 계층.

- state.py           노드 사이를 흐르는 상태(ChatbotGraphState) 정의
- nodes_context.py   입력 검증 → 문맥 파악/병합 → 되묻기
- nodes_retrieval.py 잡담 차단 → 검색 질의 생성 → 문서 검색 → 전략 판단
- nodes_answer.py    답변/상성 카드/추천 카드 생성 + 역할 고정 검사
- pipeline.py        노드 배선(build_chatbot_graph)과 실행(run_chatbot_graph)
- canned.py          웰컴 화면 예시 버튼용 캐시 응답(그래프를 우회)
"""

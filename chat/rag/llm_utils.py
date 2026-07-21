"""LLM 호출과 응답 파싱, 문서 검색을 감싸는 얇은 유틸.

LangChain 모델/retriever 구현이 버전마다 조금씩 다른(응답이 str인지 list인지,
retriever가 invoke인지 get_relevant_documents인지) 부분을 여기서 흡수해,
노드 코드가 그 차이를 신경 쓰지 않게 한다.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def call_llm_text(llm: Any, prompt: str) -> str:
    t0 = time.time()
    response = llm.invoke(prompt)
    logger.info("[TIMING] LLM 호출: %.2fs (prompt_len=%d)", time.time() - t0, len(prompt))
    if hasattr(response, "content"):
        content = response.content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(item.get("text", str(item)))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(content)
    return str(response)


def call_llm_text_creative(llm: Any, prompt: str) -> str:
    creative_llm = llm
    if hasattr(llm, "bind"):
        try:
            creative_llm = llm.bind(temperature=0.7)
        except Exception:
            creative_llm = llm
    return call_llm_text(creative_llm, prompt)


def safe_json_loads(text: str, default: Any) -> Any:
    try:
        cleaned = str(text or "").strip()
        fence_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned)
        if fence_match:
            cleaned = fence_match.group(1).strip()
        return json.loads(cleaned)
    except Exception:
        logger.warning("JSON 파싱 실패. raw=%s", text)
        return default


def retrieve_documents(retriever: Any, query: str) -> List[Any]:
    if hasattr(retriever, "invoke"):
        return retriever.invoke(query)
    if hasattr(retriever, "get_relevant_documents"):
        return retriever.get_relevant_documents(query)
    raise TypeError("retriever는 invoke 또는 get_relevant_documents 메서드를 가져야 합니다.")


def document_to_dict(doc: Any) -> Dict[str, Any]:
    if hasattr(doc, "page_content"):
        return {"content": doc.page_content, "metadata": getattr(doc, "metadata", {})}
    return {"content": str(doc), "metadata": {}}

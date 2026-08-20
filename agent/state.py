"""단일 프로토콜 섹션 검토 그래프의 상태."""

from typing import TypedDict

from retrieval.section_review import Citation


class ReviewState(TypedDict, total=False):
    # 입력
    protocol_chunk: dict
    top_k: int

    # hyde_node가 채움
    hyde_query: str

    # retrieve_node가 채움
    guideline_docs: list[str]
    guideline_metas: list[dict]

    # compare_node가 채움
    llm_result: dict

    # groundedness_node가 채움
    citations: list[Citation]
    groundedness_score: float
    needs_human_review: bool

    # 종단 노드(escalate/auto_accept)가 채움
    status: str  # "auto_accepted" | "pending_human_review"

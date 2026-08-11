"""
프로토콜 섹션 1건을 검토하는 LangGraph.

    START -> retrieve -> compare -> groundedness --(조건 분기)--> escalate -> END
                                                   \\-> auto_accept -> END

에이전트 흐름에 조건 분기(신뢰도/근거 부족 시 사람에게 에스컬레이션, CLAUDE.md §3.5)가
있어서 상태 기반 그래프 모델을 썼다 — retrieve/compare/groundedness 판별 자체는
retrieval/section_review.py의 순수 함수를 그대로 호출할 뿐이고, 이 파일이 하는 일은
그 함수들을 언제/어떤 순서로/어떤 조건으로 부를지 배선하는 것뿐이다 (§3.2: 오케스트레이션은
LangGraph, 도메인 로직은 직접 구현).

각 노드는 OpenTelemetry span으로 감싼다 (§3.3, Phase 6). retrieve span에
retrieval hit/미스와 결과 개수를, compare span에 토큰 사용량을, groundedness
span에 groundedness score와 최종 flag/confidence를 attribute로 남긴다 —
"어느 단계가 느린지/토큰을 얼마나 쓰는지/근거 없는 인용이 어디서 나오는지"를
나중에 trace만 보고 답할 수 있게 하는 게 목적이다.
"""

from anthropic import Anthropic
from langgraph.graph import END, START, StateGraph
from opentelemetry import trace

import chromadb

from agent.state import ReviewState
from observability.tracing import configure_tracing
from retrieval.section_review import (
    TOP_K_GUIDELINES,
    build_citations,
    build_review_prompt,
    call_review_llm,
    compute_groundedness_score,
    decide_escalation,
    retrieve_guidelines,
)

tracer = configure_tracing()


def build_review_graph(anthropic_client: Anthropic, chroma_client: chromadb.ClientAPI):
    def retrieve_node(state: ReviewState) -> dict:
        with tracer.start_as_current_span("retrieve") as span:
            top_k = state.get("top_k", TOP_K_GUIDELINES)
            retrieved = retrieve_guidelines(chroma_client, state["protocol_chunk"]["text"], top_k)
            docs = retrieved["documents"][0]

            span.set_attribute("retrieval.top_k", top_k)
            span.set_attribute("retrieval.n_results", len(docs))
            span.set_attribute(
                "retrieval.guideline_doc_ids",
                ",".join(sorted({m["doc_id"] for m in retrieved["metadatas"][0]})),
            )

            return {
                "guideline_docs": docs,
                "guideline_metas": retrieved["metadatas"][0],
            }

    def compare_node(state: ReviewState) -> dict:
        with tracer.start_as_current_span("compare") as span:
            prompt = build_review_prompt(
                state["protocol_chunk"], state["guideline_docs"], state["guideline_metas"]
            )
            llm_result, usage = call_review_llm(anthropic_client, prompt)

            span.set_attribute("llm.input_tokens", usage["input_tokens"])
            span.set_attribute("llm.output_tokens", usage["output_tokens"])
            span.set_attribute("llm.flag", llm_result["flag"])
            span.set_attribute("llm.confidence", float(llm_result["confidence"]))

            return {"llm_result": llm_result}

    def groundedness_node(state: ReviewState) -> dict:
        with tracer.start_as_current_span("groundedness") as span:
            citations = build_citations(
                state["llm_result"], state["guideline_docs"], state["guideline_metas"]
            )
            groundedness_score = compute_groundedness_score(citations)
            needs_human_review = decide_escalation(
                state["llm_result"]["flag"], state["llm_result"]["confidence"], citations, groundedness_score
            )

            # retrieval "hit"은 LLM이 retrieval된 발췌문 중 실제로 인용할 만한 게
            # 있었는지로 정의한다 — citations가 비어 있으면 retrieval이 이 섹션과
            # 관련된 가이드라인을 못 찾은 것(miss)으로 본다.
            span.set_attribute("retrieval.hit", bool(citations))
            span.set_attribute("groundedness.score", groundedness_score)
            span.set_attribute("groundedness.n_citations", len(citations))
            span.set_attribute("groundedness.n_grounded", sum(1 for c in citations if c.grounded))
            span.set_attribute("review.needs_human_review", needs_human_review)

            return {
                "citations": citations,
                "groundedness_score": groundedness_score,
                "needs_human_review": needs_human_review,
            }

    def route_after_groundedness(state: ReviewState) -> str:
        return "escalate" if state["needs_human_review"] else "auto_accept"

    def escalate_node(state: ReviewState) -> dict:
        with tracer.start_as_current_span("escalate") as span:
            span.set_attribute("review.status", "pending_human_review")
            return {"status": "pending_human_review"}

    def auto_accept_node(state: ReviewState) -> dict:
        with tracer.start_as_current_span("auto_accept") as span:
            span.set_attribute("review.status", "auto_accepted")
            return {"status": "auto_accepted"}

    graph = StateGraph(ReviewState)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("compare", compare_node)
    graph.add_node("groundedness", groundedness_node)
    graph.add_node("escalate", escalate_node)
    graph.add_node("auto_accept", auto_accept_node)

    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "compare")
    graph.add_edge("compare", "groundedness")
    graph.add_conditional_edges(
        "groundedness",
        route_after_groundedness,
        {"escalate": "escalate", "auto_accept": "auto_accept"},
    )
    graph.add_edge("escalate", END)
    graph.add_edge("auto_accept", END)

    return graph.compile()

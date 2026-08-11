"""프로토콜 문서 하나를 LangGraph로 섹션별 검토해서 리포트를 만드는 CLI."""

import argparse
import json
import sys
from pathlib import Path

import chromadb
from anthropic import Anthropic
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# .env는 MODEL_NAME 등 다른 모듈이 import 시점에 읽는 값들을 채우므로, 프로젝트
# 내부 모듈을 import하기 전에 먼저 로드해야 한다 (안 그러면 COMPARE_MODEL 같은
# 값이 기본값으로 조용히 고정되는 버그가 생긴다 — 실제로 있었음).
load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.graph import build_review_graph
from observability.tracing import configure_tracing
from retrieval.section_review import SectionReview

ROOT = Path(__file__).resolve().parent.parent
CHUNKS_DIR = ROOT / "data" / "chunks" / "protocols"
VECTORSTORE_DIR = ROOT / "data" / "vectorstore"
REPORTS_DIR = ROOT / "data" / "reviews"

MIN_CHUNK_CHARS = 80  # 이보다 짧은 섹션(참조 번호만 있는 등)은 검토 대상에서 제외


def load_protocol_chunks(doc_id: str) -> list[dict]:
    path = CHUNKS_DIR / f"{doc_id}.json"
    chunks = json.loads(path.read_text(encoding="utf-8"))
    return [
        c
        for c in chunks
        if c["breadcrumb"] != "(문서 서두)" and c["char_count"] >= MIN_CHUNK_CHARS
    ]


def state_to_section_review(protocol_chunk: dict, final_state: dict) -> SectionReview:
    llm_result = final_state["llm_result"]
    return SectionReview(
        protocol_doc_id=protocol_chunk["doc_id"],
        protocol_section_number=protocol_chunk["section_number"],
        protocol_section_title=protocol_chunk["section_title"],
        protocol_breadcrumb=protocol_chunk["breadcrumb"],
        flag=llm_result["flag"],
        rationale=llm_result["rationale"],
        confidence=float(llm_result["confidence"]),
        citations=final_state["citations"],
        groundedness_score=final_state["groundedness_score"],
        needs_human_review=final_state["needs_human_review"],
    )


def main():
    parser = argparse.ArgumentParser(description="LangGraph 기반 프로토콜 섹션별 규제 가이드라인 대조 리뷰")
    parser.add_argument("--doc-id", required=True, help="예: NCT03958331_Prot_000")
    parser.add_argument("--top-k", type=int, default=4)
    args = parser.parse_args()

    chunks = load_protocol_chunks(args.doc_id)
    print(f"{args.doc_id}: 검토 대상 섹션 {len(chunks)}건", file=sys.stderr)

    anthropic_client = Anthropic()
    chroma_client = chromadb.PersistentClient(path=str(VECTORSTORE_DIR))
    graph = build_review_graph(anthropic_client, chroma_client)
    tracer = configure_tracing()

    reviews = []
    for i, chunk in enumerate(chunks, start=1):
        print(
            f"  [{i}/{len(chunks)}] {chunk['section_number']} {chunk['section_title']}",
            file=sys.stderr,
        )
        # 섹션 1건 = trace 1개. retrieve/compare/groundedness/escalate 노드 span은
        # 이 span 아래로 중첩된다 (OTel context가 contextvars로 전파되기 때문에
        # graph.invoke가 동기 호출인 이상 별도 배선 없이 자동으로 부모-자식이 잡힌다).
        with tracer.start_as_current_span("review_protocol_section") as section_span:
            section_span.set_attribute("protocol.doc_id", chunk["doc_id"])
            section_span.set_attribute("protocol.section_number", chunk["section_number"] or "")
            section_span.set_attribute("protocol.section_title", chunk["section_title"] or "")

            final_state = graph.invoke({"protocol_chunk": chunk, "top_k": args.top_k})
            reviews.append(state_to_section_review(chunk, final_state))

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"{args.doc_id}.json"
    out_path.write_text(
        json.dumps([r.to_dict() for r in reviews], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    n_flagged = sum(1 for r in reviews if r.flag != "aligned")
    n_escalated = sum(1 for r in reviews if r.needs_human_review)
    print(
        f"\n완료: {len(reviews)}건 검토, {n_flagged}건 플래그, "
        f"{n_escalated}건 사람 검토 필요 -> {out_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

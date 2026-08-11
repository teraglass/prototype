"""
프로토콜 섹션 → 관련 가이드라인 retrieval → 정합/충돌 대조 → 리스크 플래그 + 출처.

CLAUDE.md §3.2의 원칙: 오케스트레이션은 LangGraph, 도메인 로직은 직접 구현.
이 모듈은 그 "도메인 로직" 쪽이다 — LangGraph를 전혀 몰라도 되는 순수 함수들만
모아둔다. 그래프 배선(조건 분기 포함)은 agent/graph.py에서 이 함수들을 노드로
감싸서 구성한다.

- 이 모듈은 "규정 위반/승인 가능 여부"를 판정하지 않는다. "이 섹션이 이 가이드라인과
  관련이 있고, 검토가 필요하다"까지만 말한다 — flag는 aligned/review_needed/conflict
  세 가지뿐이고, conflict도 "명백히 충돌"이 아니라 "충돌 가능성이 있어 보임"의 의미다.
- groundedness는 LLM 자기평가가 아니라 프로그램으로 직접 검증한다: LLM이 인용한
  quoted_evidence가 실제로 retrieval된 가이드라인 원문에 존재하는 substring인지
  체크한다. 이게 없으면(=근거를 지어냈으면) 그 인용은 groundedness 실패로 표시하고,
  전체 결과를 사람 검토로 에스컬레이션한다 (§3.5).
"""

import difflib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import chromadb
from anthropic import Anthropic
from langsmith import traceable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from retrieval.embeddings import embed_query

ROOT = Path(__file__).resolve().parent.parent
VECTORSTORE_DIR = ROOT / "data" / "vectorstore"

TOP_K_GUIDELINES = 4
CONFIDENCE_ESCALATION_THRESHOLD = 0.7
GROUNDEDNESS_MATCH_RATIO = 0.85  # difflib 유사도 기준 — OCR/공백 차이 정도는 허용

MODEL_NAME = os.environ.get("COMPARE_MODEL", "claude-sonnet-5")

SYSTEM_PROMPT = """\
당신은 임상시험 프로토콜을 규제 가이드라인과 대조해 검토 포인트를 찾아주는 보조 도구입니다.

중요한 제약:
- 당신은 승인 여부를 판정하지 않습니다. "이 프로토콜은 승인 가능/불가능하다" 같은 말을
  하지 마세요. 오직 "이 섹션이 이 가이드라인과 관련이 있고 검토가 필요한지"만 판단합니다.
- flag는 세 가지 중 하나입니다:
  - aligned: 프로토콜 섹션이 가이드라인 요구사항을 충족하는 것으로 보임
  - review_needed: 가이드라인과 관련은 있으나 프로토콜에 명시가 부족하거나 불명확함
  - conflict: 프로토콜 내용이 가이드라인 권고/요구와 어긋나는 것으로 보임
- 제공된 가이드라인 발췌문에 실제로 없는 내용을 지어내지 마세요. citations의
  quoted_evidence는 반드시 제공된 발췌문에서 그대로(verbatim) 가져온 문장이어야 합니다.
- 프로토콜 섹션이 가이드라인과 관련이 없다면 flag=aligned, confidence를 낮게 주고
  citations는 빈 배열로 두세요.
"""

REVIEW_TOOL = {
    "name": "submit_section_review",
    "description": "프로토콜 섹션에 대한 검토 결과를 제출한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "flag": {
                "type": "string",
                "enum": ["aligned", "review_needed", "conflict"],
            },
            "rationale": {
                "type": "string",
                "description": "이 판단을 내린 이유 (한국어, 2-4문장)",
            },
            "confidence": {
                "type": "number",
                "description": "이 판단에 대한 확신도, 0.0~1.0",
            },
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "guideline_ref": {
                            "type": "string",
                            "description": "인용한 가이드라인 발췌문의 [N] 라벨",
                        },
                        "quoted_evidence": {
                            "type": "string",
                            "description": "해당 발췌문에서 그대로 가져온 근거 문장 (verbatim)",
                        },
                    },
                    "required": ["guideline_ref", "quoted_evidence"],
                },
            },
        },
        "required": ["flag", "rationale", "confidence", "citations"],
    },
}


@dataclass
class Citation:
    guideline_ref: str
    doc_id: str
    breadcrumb: str
    page_start: int
    page_end: int
    quoted_evidence: str
    grounded: bool


@dataclass
class SectionReview:
    protocol_doc_id: str
    protocol_section_number: str | None
    protocol_section_title: str | None
    protocol_breadcrumb: str
    flag: str
    rationale: str
    confidence: float
    citations: list[Citation]
    groundedness_score: float
    needs_human_review: bool
    # 사람이 에스컬레이션 큐를 처리한 뒤 채우는 필드 (Phase 7). 처리 전엔 둘 다 None —
    # to_dict()가 이 둘을 항상 포함하므로 기존 리포트 JSON을 다시 읽어도 키 누락 없이
    # 그대로 라운드트립된다.
    human_decision: str | None = None  # "confirmed" | "false_positive" | "approved_as_is"
    human_note: str | None = None

    def to_dict(self) -> dict:
        return {
            "protocol_doc_id": self.protocol_doc_id,
            "protocol_section_number": self.protocol_section_number,
            "protocol_section_title": self.protocol_section_title,
            "protocol_breadcrumb": self.protocol_breadcrumb,
            "flag": self.flag,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "citations": [c.__dict__ for c in self.citations],
            "groundedness_score": self.groundedness_score,
            "needs_human_review": self.needs_human_review,
            "human_decision": self.human_decision,
            "human_note": self.human_note,
        }

    @staticmethod
    def from_dict(d: dict) -> "SectionReview":
        return SectionReview(
            protocol_doc_id=d["protocol_doc_id"],
            protocol_section_number=d["protocol_section_number"],
            protocol_section_title=d["protocol_section_title"],
            protocol_breadcrumb=d["protocol_breadcrumb"],
            flag=d["flag"],
            rationale=d["rationale"],
            confidence=d["confidence"],
            citations=[Citation(**c) for c in d["citations"]],
            groundedness_score=d["groundedness_score"],
            needs_human_review=d["needs_human_review"],
            human_decision=d.get("human_decision"),
            human_note=d.get("human_note"),
        )


def retrieve_guidelines(client: chromadb.ClientAPI, protocol_text: str, top_k: int) -> dict:
    collection = client.get_collection("guideline")
    return collection.query(query_embeddings=[embed_query(protocol_text)], n_results=top_k)


def is_substring_grounded(quote: str, source_text: str) -> bool:
    quote_norm = re.sub(r"\s+", " ", quote).strip()
    source_norm = re.sub(r"\s+", " ", source_text).strip()

    if not quote_norm:
        return False
    if quote_norm in source_norm:
        return True

    # PDF 추출 과정에서 공백/줄바꿈이 미묘하게 달라질 수 있어 정확 일치 대신
    # 슬라이딩 윈도우로 가장 가까운 부분과의 유사도를 본다.
    window = len(quote_norm)
    best_ratio = 0.0
    step = max(1, window // 4)
    for start in range(0, max(1, len(source_norm) - window + 1), step):
        candidate = source_norm[start : start + window]
        ratio = difflib.SequenceMatcher(None, quote_norm, candidate).ratio()
        best_ratio = max(best_ratio, ratio)
        if best_ratio >= GROUNDEDNESS_MATCH_RATIO:
            return True
    return best_ratio >= GROUNDEDNESS_MATCH_RATIO


def build_review_prompt(protocol_chunk: dict, guideline_docs: list[str], guideline_metas: list[dict]) -> str:
    excerpt_blocks = []
    for i, (doc, meta) in enumerate(zip(guideline_docs, guideline_metas), start=1):
        excerpt_blocks.append(
            f"[{i}] 출처: {meta['doc_id']} / {meta['breadcrumb']} (p{meta['page_start']}-{meta['page_end']})\n{doc}"
        )
    excerpts_text = "\n\n".join(excerpt_blocks)

    return f"""\
## 프로토콜 섹션
출처: {protocol_chunk['doc_id']} / {protocol_chunk['breadcrumb']}

{protocol_chunk['text']}

## 관련 가이드라인 발췌문 (retrieval 결과)

{excerpts_text}

위 프로토콜 섹션을 가이드라인 발췌문들과 대조해서 검토 결과를 submit_section_review로 제출하세요.
"""


@traceable(run_type="llm", name="claude_section_review")
def call_review_llm(anthropic_client: Anthropic, prompt: str) -> tuple[dict, dict]:
    response = anthropic_client.messages.create(
        model=MODEL_NAME,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[REVIEW_TOOL],
        tool_choice={"type": "tool", "name": "submit_section_review"},
        messages=[{"role": "user", "content": prompt}],
    )
    tool_use = next(b for b in response.content if b.type == "tool_use")
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return tool_use.input, usage


def build_citations(
    llm_result: dict, guideline_docs: list[str], guideline_metas: list[dict]
) -> list[Citation]:
    ref_to_meta = {
        str(i): (guideline_metas[i - 1], guideline_docs[i - 1]) for i in range(1, len(guideline_docs) + 1)
    }

    citations: list[Citation] = []
    for c in llm_result.get("citations", []):
        ref = re.sub(r"\D", "", c.get("guideline_ref", ""))
        meta_doc = ref_to_meta.get(ref)
        if meta_doc is None:
            # 존재하지 않는 발췌문을 인용했다면 그 자체로 근거 없는 인용
            citations.append(
                Citation(
                    guideline_ref=c.get("guideline_ref", ""),
                    doc_id="unknown",
                    breadcrumb="unknown",
                    page_start=0,
                    page_end=0,
                    quoted_evidence=c.get("quoted_evidence", ""),
                    grounded=False,
                )
            )
            continue

        meta, source_text = meta_doc
        grounded = is_substring_grounded(c.get("quoted_evidence", ""), source_text)
        citations.append(
            Citation(
                guideline_ref=c.get("guideline_ref", ""),
                doc_id=meta["doc_id"],
                breadcrumb=meta["breadcrumb"],
                page_start=meta["page_start"],
                page_end=meta["page_end"],
                quoted_evidence=c.get("quoted_evidence", ""),
                grounded=grounded,
            )
        )
    return citations


def compute_groundedness_score(citations: list[Citation]) -> float:
    if not citations:
        return 1.0
    return sum(1 for c in citations if c.grounded) / len(citations)


def decide_escalation(flag: str, confidence: float, citations: list[Citation], groundedness_score: float) -> bool:
    # citations가 비어 있다는 건 이 섹션이 retrieval된 가이드라인과 뚜렷하게 엮이지
    # 않았다는 뜻이다 (예: ABSTRACT처럼 서술형 요약 섹션). 이 경우 confidence가
    # 낮은 건 "이 섹션은 특정 조항과 1:1로 안 엮인다"는 정상적인 신호이지 리스크가
    # 아니므로, confidence 기준 에스컬레이션은 citations가 실제로 있을 때만 적용한다.
    # groundedness 실패(=근거를 지어냄)는 citations 유무와 무관하게 항상 에스컬레이션.
    return (
        groundedness_score < 1.0
        or (bool(citations) and confidence < CONFIDENCE_ESCALATION_THRESHOLD)
        or (flag == "conflict" and confidence < 0.85)
    )

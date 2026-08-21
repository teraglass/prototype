"""retrieval/section_review.py의 순수 도메인 로직 테스트 (groundedness, 에스컬레이션 판단)."""

from retrieval.section_review import (
    Citation,
    build_citations,
    compute_groundedness_score,
    decide_escalation,
    is_substring_grounded,
)


def make_citation(grounded: bool, doc_id: str = "D1") -> Citation:
    return Citation(
        guideline_ref="[1]",
        doc_id=doc_id,
        breadcrumb="B1",
        page_start=1,
        page_end=2,
        quoted_evidence="text",
        grounded=grounded,
    )


class TestIsSubstringGrounded:
    def test_exact_match(self):
        assert is_substring_grounded("hello world", "some prefix hello world some suffix")

    def test_whitespace_normalized_match(self):
        # PDF 추출 과정에서 줄바꿈/공백이 미묘하게 다를 수 있어 정규화 후 비교한다
        assert is_substring_grounded("hello   world", "prefix hello world suffix")

    def test_no_match_for_unrelated_text(self):
        assert not is_substring_grounded(
            "completely unrelated fabricated sentence about nothing",
            "the actual guideline text discusses something else entirely",
        )

    def test_empty_quote_is_not_grounded(self):
        assert not is_substring_grounded("", "any source text here")

    def test_near_match_within_fuzzy_tolerance(self):
        # 슬라이딩 윈도우 유사도 허용 범위 — 한두 글자 정도의 OCR/공백 차이
        quote = "The sponsor should ensure that the systems are validated"
        source = "prefix text The sponsor should ensure the systems are validated suffix"
        assert is_substring_grounded(quote, source)


class TestComputeGroundednessScore:
    def test_no_citations_defaults_to_full_score(self):
        assert compute_groundedness_score([]) == 1.0

    def test_all_grounded(self):
        citations = [make_citation(True), make_citation(True)]
        assert compute_groundedness_score(citations) == 1.0

    def test_mixed_grounding(self):
        citations = [make_citation(True), make_citation(False)]
        assert compute_groundedness_score(citations) == 0.5

    def test_none_grounded(self):
        citations = [make_citation(False), make_citation(False)]
        assert compute_groundedness_score(citations) == 0.0


class TestDecideEscalation:
    def test_no_citations_low_confidence_not_escalated(self):
        # citations가 비어있다는 건 이 섹션이 특정 조항과 안 엮인다는 정상 신호 —
        # confidence가 낮아도 에스컬레이션 대상이 아니다 (Phase 4/5에서 실측으로
        # 확인한 규칙).
        assert decide_escalation("aligned", 0.2, [], groundedness_score=1.0) is False

    def test_citations_present_low_confidence_escalates(self):
        citations = [make_citation(True)]
        assert decide_escalation("review_needed", 0.5, citations, groundedness_score=1.0) is True

    def test_citations_present_high_confidence_not_escalated(self):
        citations = [make_citation(True)]
        assert decide_escalation("review_needed", 0.85, citations, groundedness_score=1.0) is False

    def test_groundedness_failure_always_escalates_even_with_high_confidence(self):
        citations = [make_citation(True)]
        assert decide_escalation("aligned", 0.95, citations, groundedness_score=0.5) is True

    def test_conflict_requires_higher_confidence_bar(self):
        citations = [make_citation(True)]
        # 0.8은 일반 임계값(0.7)은 넘지만 conflict 전용 임계값(0.85)은 못 넘음
        assert decide_escalation("conflict", 0.8, citations, groundedness_score=1.0) is True
        assert decide_escalation("conflict", 0.9, citations, groundedness_score=1.0) is False


class TestBuildCitations:
    def test_valid_reference_is_grounded(self):
        llm_result = {"citations": [{"guideline_ref": "[1]", "quoted_evidence": "exact text"}]}
        guideline_docs = ["some prefix exact text suffix"]
        guideline_metas = [{"doc_id": "D1", "breadcrumb": "B1", "page_start": 1, "page_end": 2}]

        result = build_citations(llm_result, guideline_docs, guideline_metas)

        assert len(result) == 1
        assert result[0].doc_id == "D1"
        assert result[0].grounded is True

    def test_reference_to_nonexistent_excerpt_is_ungrounded(self):
        # LLM이 retrieval 결과에 없는 [5]번을 인용한 경우 — 존재하지 않는
        # 발췌문을 참조했으니 그 자체로 근거 없음으로 취급한다.
        llm_result = {"citations": [{"guideline_ref": "[5]", "quoted_evidence": "made up text"}]}
        guideline_docs = ["only one excerpt here"]
        guideline_metas = [{"doc_id": "D1", "breadcrumb": "B1", "page_start": 1, "page_end": 2}]

        result = build_citations(llm_result, guideline_docs, guideline_metas)

        assert len(result) == 1
        assert result[0].doc_id == "unknown"
        assert result[0].grounded is False

    def test_fabricated_quote_within_valid_reference_is_ungrounded(self):
        llm_result = {"citations": [{"guideline_ref": "[1]", "quoted_evidence": "text that was never there"}]}
        guideline_docs = ["completely different actual content"]
        guideline_metas = [{"doc_id": "D1", "breadcrumb": "B1", "page_start": 1, "page_end": 2}]

        result = build_citations(llm_result, guideline_docs, guideline_metas)

        assert result[0].grounded is False

    def test_no_citations_returns_empty_list(self):
        llm_result = {"citations": []}
        assert build_citations(llm_result, [], []) == []

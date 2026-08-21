"""
retrieval/build_vectorstore.py의 retrieval 후보 필터링 로직 테스트.

두 필터 다 eval harness로 실측한 실제 노이즈 패턴을 코드로 남긴 것이다 —
자세한 근거는 build_vectorstore.py의 주석과 data/eval/eval_report.md 참고.
"""

from retrieval.build_vectorstore import (
    LOW_CONTENT_CHAR_THRESHOLD,
    _is_definition,
    _is_low_content,
)


class TestIsDefinition:
    def test_glossary_section_is_definition(self):
        assert _is_definition("1 GLOSSARY > 1.9 Audit Trail") is True

    def test_requirement_section_is_not_definition(self):
        assert _is_definition("5 SPONSOR > 5.5 Trial Management, Data Handling") is False

    def test_case_insensitive_and_whitespace_tolerant(self):
        assert _is_definition("  1 glossary  > 1.1 Something") is True

    def test_empty_breadcrumb_is_not_definition(self):
        assert _is_definition("") is False


class TestIsLowContent:
    def test_checklist_prompt_chunk_is_low_content(self):
        # 실제로 발견한 케이스: ICH E6(R2) 6.14 — 요구사항이 아니라 체크리스트 프롬프트
        chunk = {
            "text": "Financing and insurance if not addressed in a separate agreement.",
            "breadcrumb": "6 CLINICAL TRIAL PROTOCOL AND PROTOCOL AMENDMENT(S) > 6.14 Financing and Insurance",
        }
        assert _is_low_content(chunk) is True

    def test_substantive_requirement_chunk_is_not_low_content(self):
        chunk = {
            "text": "x" * (LOW_CONTENT_CHAR_THRESHOLD + 1),
            "breadcrumb": "5 SPONSOR > 5.5 Trial Management, Data Handling, and Record Keeping",
        }
        assert _is_low_content(chunk) is False

    def test_document_preamble_exempt_regardless_of_length(self):
        chunk = {"text": "short", "breadcrumb": "(문서 서두)"}
        assert _is_low_content(chunk) is False

    def test_boundary_at_threshold(self):
        exactly_at_threshold = {
            "text": "x" * LOW_CONTENT_CHAR_THRESHOLD,
            "breadcrumb": "5 SPONSOR > 5.1 Quality Assurance",
        }
        one_under = {
            "text": "x" * (LOW_CONTENT_CHAR_THRESHOLD - 1),
            "breadcrumb": "5 SPONSOR > 5.1 Quality Assurance",
        }
        assert _is_low_content(exactly_at_threshold) is False
        assert _is_low_content(one_under) is True

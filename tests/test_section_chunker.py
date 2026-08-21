"""
chunking/section_chunker.py의 핵심 판별 로직 테스트.

전부 회귀 테스트다 — 실제로 PDF를 파싱하다가 만난 구체적인 버그를 재현하는
최소 케이스로 만들었다. 실제 PDF 파일이 필요한 chunk_pdf() 전체 흐름은 여기서
다루지 않는다(무겁고, gitignore된 대용량 PDF에 의존하게 됨) — 이 내부 함수들이
그 전체 흐름의 실질적인 판단 로직이라 여기서 잡아도 회귀 방지 효과는 충분하다.
"""

from chunking.section_chunker import (
    DOT_LEADER,
    Line,
    _check_title_style,
    _is_heading_line,
    _make_line,
    _match_heading_number,
    _strip_gutter_numbers,
    _strip_header_footer_lines,
    _strip_toc_pages,
)

BODY_STYLE = (12.0, "Regular")


def word(text, top, x0, size=12.0, fontname="Regular"):
    return {"text": text, "top": top, "x0": x0, "size": size, "fontname": fontname}


class TestMakeLineWordOrder:
    def test_reorders_by_x0_even_when_top_differs_slightly(self):
        # 실제로 만난 버그: MFDS PDF에서 "I."(top=71.5)와 "서론"(top=70.6)이
        # 같은 줄로는 묶이지만 top 기준 정렬 때문에 "서론 I."로 뒤집혔었다.
        words = [word("서론", top=70.6, x0=73.7), word("I.", top=71.5, x0=56.6)]
        line = _make_line(words, page_num=1)
        assert line.text == "I. 서론"

    def test_normal_left_to_right_order_preserved(self):
        words = [word("Hello", top=10.0, x0=10.0), word("World", top=10.0, x0=50.0)]
        line = _make_line(words, page_num=1)
        assert line.text == "Hello World"


class TestDotLeader:
    def test_matches_ascii_period_leader(self):
        assert DOT_LEADER.search("Chapter 1 .......... 5")

    def test_matches_middle_dot_leader(self):
        # MFDS 사례집 목차가 실제로 쓰는 문자 (U+00B7)
        assert DOT_LEADER.search("I. 서론 ·······················1")

    def test_no_match_on_normal_text(self):
        assert DOT_LEADER.search("This is a normal sentence.") is None


class TestGutterNumberStripping:
    def test_strips_number_far_left_of_body_margin(self):
        # FDA 초안 가이던스의 여백 줄번호 (x0=42) — 본문 좌측(x0=72)보다 왼쪽
        words = [word("78", top=10, x0=42.0), word("Introduction", top=10, x0=72.0)]
        result = _strip_gutter_numbers(words, body_left_margin=72.0)
        assert [w["text"] for w in result] == ["Introduction"]

    def test_keeps_number_at_normal_margin(self):
        words = [word("1", top=10, x0=72.0), word("Introduction", top=10, x0=90.0)]
        result = _strip_gutter_numbers(words, body_left_margin=72.0)
        assert [w["text"] for w in result] == ["1", "Introduction"]

    def test_keeps_non_digit_words_regardless_of_position(self):
        words = [word("(IRB/IEC)", top=10, x0=30.0)]
        result = _strip_gutter_numbers(words, body_left_margin=72.0)
        assert result == words


class TestMatchHeadingNumber:
    def test_arabic_level_1(self):
        assert _match_heading_number("5") == (1, "5")

    def test_arabic_level_2(self):
        assert _match_heading_number("5.5") == (2, "5.5")

    def test_arabic_level_3(self):
        assert _match_heading_number("5.18.7") == (3, "5.18.7")

    def test_roman_numeral(self):
        assert _match_heading_number("I.") == (1, "I")

    def test_non_numbering_token_returns_none(self):
        assert _match_heading_number("Hello") is None


class TestHeadingDetection:
    def test_bold_number_with_regular_body_is_not_a_heading(self):
        # ICH E6(R2)에서 실제로 확인한 패턴: "2.13 Systems with procedures..."는
        # 조항 번호만 Bold고 본문은 Regular인 일반 조항이지 헤딩이 아니다.
        words = [
            word("2.13", 10, 50, fontname="Bold"),
            word("Systems", 10, 60, fontname="Regular"),
            word("with", 10, 70, fontname="Regular"),
            word("procedures", 10, 80, fontname="Regular"),
        ]
        line = Line(text="2.13 Systems with procedures", words=words, top=10, page=1)
        assert _is_heading_line(line, BODY_STYLE) is None

    def test_fully_bold_line_is_a_heading(self):
        words = [
            word("3.", 10, 50, fontname="Bold"),
            word("INSTITUTIONAL", 10, 60, fontname="Bold"),
            word("REVIEW", 10, 70, fontname="Bold"),
        ]
        line = Line(text="3. INSTITUTIONAL REVIEW", words=words, top=10, page=1)
        assert _is_heading_line(line, BODY_STYLE) == (1, "3", "INSTITUTIONAL REVIEW")

    def test_larger_size_heading_no_bold_needed(self):
        # MFDS 사례집 패턴: Bold 여부가 아니라 크기 차이(15pt vs 12pt 본문)
        words = [word("I.", 10, 50, size=15.0), word("서론", 10, 65, size=15.0)]
        line = Line(text="I. 서론", words=words, top=10, page=1)
        assert _is_heading_line(line, BODY_STYLE) == (1, "I", "서론")

    def test_korean_case_numbering(self):
        words = [
            word("보완사례", 10, 50, size=15.0),
            word("1", 10, 90, size=15.0),
            word("효력", 10, 100, size=15.0),
        ]
        line = Line(text="보완사례 1 효력", words=words, top=10, page=1)
        assert _is_heading_line(line, BODY_STYLE) == (2, "보완사례 1", "효력")

    def test_bare_number_with_no_title_is_not_a_heading(self):
        words = [word("5.5", 10, 50, fontname="Bold")]
        line = Line(text="5.5", words=words, top=10, page=1)
        assert _is_heading_line(line, BODY_STYLE) is None

    def test_title_longer_than_max_words_is_not_a_heading(self):
        long_title = [word(f"w{i}", 10, 50 + i, fontname="Bold") for i in range(25)]
        words = [word("5.5", 10, 50, fontname="Bold")] + long_title
        line = Line(text="5.5 " + " ".join(w["text"] for w in long_title), words=words, top=10, page=1)
        assert _is_heading_line(line, BODY_STYLE) is None


class TestCheckTitleStyle:
    def test_partial_style_match_below_threshold_rejected(self):
        # 제목 단어 중 본문과 다른 스타일인 비율이 MIN_STYLE_MATCH_RATIO(0.7) 미만이면 헤딩 아님
        title_words = [
            word("Mostly", 10, 60, fontname="Regular"),
            word("Regular", 10, 70, fontname="Regular"),
            word("Text", 10, 80, fontname="Regular"),
            word("Bold", 10, 90, fontname="Bold"),
        ]
        assert _check_title_style(1, "5", title_words, BODY_STYLE) is None


class TestStripTocPages:
    def test_removes_pages_dominated_by_dot_leaders(self):
        lines = [
            Line(text="Chapter 1 .......... 5", words=[], top=1, page=1),
            Line(text="Chapter 2 .......... 6", words=[], top=2, page=1),
            Line(text="Real section content here.", words=[], top=1, page=2),
        ]
        result = _strip_toc_pages(lines)
        assert [l.page for l in result] == [2]

    def test_keeps_page_with_few_dot_leader_lines(self):
        lines = [
            Line(text=f"paragraph line {i}", words=[], top=i, page=1) for i in range(10)
        ] + [Line(text="Contents .......... 5", words=[], top=11, page=1)]
        result = _strip_toc_pages(lines)
        assert len(result) == 11  # 11개 중 1개만 dot leader -> 9% < 25% 임계값, 페이지 유지


class TestStripHeaderFooterLines:
    def test_removes_repeated_boilerplate_with_varying_page_number(self):
        lines = [Line(text=f"Version 5 Page {i}", words=[], top=1, page=i) for i in range(1, 6)]
        lines.append(Line(text="Unique real content", words=[], top=2, page=1))
        result = _strip_header_footer_lines(lines, num_pages=5)
        assert [l.text for l in result] == ["Unique real content"]

    def test_keeps_non_repeated_lines(self):
        # 숫자만 다른 문장은 정규화 후 전부 같은 문자열이 되어 반복으로 잡히므로
        # (의도된 동작 — "Page 3" vs "Page 6" 케이스), 진짜 서로 다른 문장으로 테스트한다.
        sentences = [
            "The investigator should review the protocol carefully.",
            "Adverse events must be reported within the required timeframe.",
            "Informed consent should be obtained prior to enrollment.",
            "The sponsor is responsible for trial oversight.",
            "Source documents should be retained per regulation.",
        ]
        lines = [Line(text=s, words=[], top=1, page=i) for i, s in enumerate(sentences, start=1)]
        result = _strip_header_footer_lines(lines, num_pages=5)
        assert len(result) == 5

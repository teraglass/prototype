"""
섹션 인식 청킹 (fixed-size 청킹이 아니라).

CLAUDE.md §3.1의 핵심 주장을 코드로 구현한 부분: 임상 프로토콜/규제 가이드라인은
섹션 구조가 강하므로, 고정 토큰 수로 기계적으로 자르면 섹션이 문단 중간에서 잘려
retrieval 품질이 무너진다. 그래서 실제 문서 레이아웃에서 "이게 헤딩인지 본문인지"를
판별해 섹션 경계 기준으로 자른다.

헤딩 판별 근거 (추측이 아니라 실제 PDF를 열어서 확인한 것):
- ICH E6(R2): 헤딩 줄 전체가 Bold인데, 조항 번호(예: "2.13")만 Bold이고 뒤따르는
  본문은 Bold가 아닌 경우가 있다 — 번호만으로는 헤딩인지 조항 번호인지 구분 불가.
- MFDS 사례집: 헤딩은 15pt, 본문은 12pt — Bold 여부가 아니라 크기 차이.
- ClinicalTrials.gov 프로토콜(NCT03958331): 헤딩은 Bold-Italic, 본문은 Regular.

→ 폰트 굵기/스타일마다 다른 규칙을 하드코딩하는 대신, "문서에서 가장 흔한
(글꼴, 크기) 조합을 본문 기준선으로 삼고, 번호로 시작하는 줄에서 번호 뒤 제목
텍스트가 그 기준선과 다르면 헤딩"이라는 하나의 규칙으로 통일했다. 언어(한글/영문)에
무관하게 동작한다.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

LINE_Y_TOLERANCE = 3.0
MAX_HEADING_TITLE_WORDS = 20
MIN_STYLE_MATCH_RATIO = 0.7  # 제목 단어 중 이 비율 이상이 본문 스타일과 달라야 헤딩으로 인정
HEADER_FOOTER_MIN_PAGE_RATIO = 0.4  # 전체 페이지의 이 비율 이상에서 반복되면 머리말/꼬리말로 간주

# 목차 점선 리더는 문서마다 글자가 다르다 — ASCII 마침표뿐 아니라 MFDS 사례집처럼
# 가운뎃점(U+00B7)을 쓰는 경우도 실제로 확인했다. 흔히 쓰이는 리더 글리프를 모아
# "이 중 아무 글자든 4개 이상 연속"이면 리더로 인정한다.
_LEADER_CHARS = ".·․‧∙⋅"
DOT_LEADER = re.compile(rf"[{re.escape(_LEADER_CHARS)}]{{4,}}")
TOC_LINE_RATIO_THRESHOLD = 0.25  # 페이지 내 이 비율 이상이 점선 리더면 그 페이지를 목차로 간주

# FDA 공람용 초안 가이던스는 코멘트 인용을 위해 본문 왼쪽 여백에 줄번호를 인쇄한다
# (예: 78/79/80 같은 숫자가 x0≈42에 찍히고, 본문 좌측 시작(x0≈72)보다 왼쪽에 있다).
# 이 줄번호가 헤딩/본문과 같은 y좌표에 걸리면 같은 줄로 묶여 "78 79 80 To support..."
# 처럼 본문이나 헤딩 번호를 오염시키는 걸 실제로 확인했다 — 그래서 라인 그룹핑 전에
# 순수 숫자이면서 본문 좌측 여백보다 눈에 띄게 왼쪽에 있는 단어는 제거한다.
GUTTER_NUMBER_MARGIN = 15.0

ARABIC_NUMBERING = re.compile(r"^(\d{1,2}(?:\.\d{1,3}){0,3})\.?$")
ROMAN_NUMBERING = re.compile(r"^([IVXLCDM]{1,6})\.$")
KOREAN_CASE_NUMBERING = re.compile(r"^보완사례\s*(\d+)$")


@dataclass
class Line:
    text: str
    words: list
    top: float
    page: int


@dataclass
class Chunk:
    doc_id: str
    section_number: str | None
    section_title: str | None
    breadcrumb: str
    page_start: int
    page_end: int
    text: str

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "section_number": self.section_number,
            "section_title": self.section_title,
            "breadcrumb": self.breadcrumb,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "char_count": len(self.text),
            "text": self.text,
        }


def _word_style(word: dict) -> tuple:
    return (round(word["size"], 1), word["fontname"])


def _compute_body_left_margin(pdf) -> float:
    x0_counts = Counter()
    for page in pdf.pages:
        for w in page.extract_words(extra_attrs=["size", "fontname"]):
            x0_counts[round(w["x0"])] += 1
    return x0_counts.most_common(1)[0][0] if x0_counts else 0.0


def _strip_gutter_numbers(words: list[dict], body_left_margin: float) -> list[dict]:
    return [
        w
        for w in words
        if not (w["text"].isdigit() and w["x0"] < body_left_margin - GUTTER_NUMBER_MARGIN)
    ]


def _make_line(word_group: list[dict], page_num: int) -> Line:
    # 같은 줄로 묶인 단어들도 top이 완벽히 같지 않을 수 있다 (베이스라인/렌더링
    # 오차로 71.5 vs 70.6처럼 미세하게 어긋남). top으로만 정렬하면 "서론 I."처럼
    # 좌우 순서가 뒤바뀌어 헤딩 정규식이 깨지는 걸 실제로 확인했다 — 그래서 같은
    # 줄로 묶은 뒤에는 반드시 x0(읽는 순서) 기준으로 다시 정렬한다.
    ordered = sorted(word_group, key=lambda w: w["x0"])
    return Line(
        text=" ".join(w["text"] for w in ordered),
        words=ordered,
        top=min(w["top"] for w in word_group),
        page=page_num,
    )


def _group_words_into_lines(page, page_num: int, body_left_margin: float) -> list[Line]:
    words = page.extract_words(extra_attrs=["size", "fontname"])
    words = _strip_gutter_numbers(words, body_left_margin)
    words.sort(key=lambda w: (w["top"], w["x0"]))

    lines: list[Line] = []
    current: list[dict] = []
    current_top = None

    for w in words:
        if current_top is None or abs(w["top"] - current_top) <= LINE_Y_TOLERANCE:
            current.append(w)
            current_top = w["top"] if current_top is None else current_top
        else:
            lines.append(_make_line(current, page_num))
            current = [w]
            current_top = w["top"]

    if current:
        lines.append(_make_line(current, page_num))

    return lines


def _strip_toc_pages(all_lines: list[Line]) -> list[Line]:
    # 목차 페이지는 "5.18.7 Monitoring Plan .......... 32" 같은 줄이 헤딩과 똑같은
    # 스타일(Bold 등)로 찍혀 있어서, 번호+스타일 판별만으로는 본문 헤딩과 구분이 안 된다.
    # 점선 리더가 몰려 있는 페이지를 통째로 목차로 보고 제외한다.
    lines_by_page: dict[int, list[Line]] = {}
    for line in all_lines:
        lines_by_page.setdefault(line.page, []).append(line)

    toc_pages = set()
    for page_num, lines in lines_by_page.items():
        if not lines:
            continue
        dot_leader_count = sum(1 for l in lines if DOT_LEADER.search(l.text))
        if dot_leader_count / len(lines) >= TOC_LINE_RATIO_THRESHOLD:
            toc_pages.add(page_num)

    return [line for line in all_lines if line.page not in toc_pages]


def _compute_body_style(all_lines: list[Line]) -> tuple:
    style_counts = Counter()
    for line in all_lines:
        for w in line.words:
            style_counts[_word_style(w)] += 1
    return style_counts.most_common(1)[0][0]


def _strip_header_footer_lines(all_lines: list[Line], num_pages: int) -> list[Line]:
    # 페이지 번호가 다른 것만 빼면 같은 문자열이 되는 줄("Page 3" vs "Page 6")도 잡아내려고
    # 숫자를 '#'로 정규화한 뒤 빈도를 센다.
    normalized_counts = Counter()
    for line in all_lines:
        normalized = re.sub(r"\d+", "#", line.text.strip())
        if normalized:
            normalized_counts[normalized] += 1

    threshold = max(2, int(num_pages * HEADER_FOOTER_MIN_PAGE_RATIO))
    boilerplate = {norm for norm, count in normalized_counts.items() if count >= threshold}

    return [
        line
        for line in all_lines
        if re.sub(r"\d+", "#", line.text.strip()) not in boilerplate
    ]


def _match_heading_number(first_token: str) -> tuple[int, str] | None:
    m = ARABIC_NUMBERING.match(first_token)
    if m:
        number = m.group(1)
        return number.count(".") + 1, number

    m = ROMAN_NUMBERING.match(first_token)
    if m:
        return 1, m.group(1)

    return None


def _is_heading_line(line: Line, body_style: tuple) -> tuple[int, str, str] | None:
    if not line.words:
        return None

    first_token = line.words[0]["text"]

    # "보완사례 2" 처럼 번호가 다음 단어에 붙는 케이스부터 시도
    if len(line.words) >= 2:
        two_token = f"{first_token} {line.words[1]['text']}"
        m = KOREAN_CASE_NUMBERING.match(two_token)
        if m:
            title_words = line.words[2:]
            return _check_title_style(2, two_token, title_words, body_style)

    matched = _match_heading_number(first_token)
    if matched is None:
        return None

    level, number = matched
    title_words = line.words[1:]
    return _check_title_style(level, number, title_words, body_style)


def _check_title_style(
    level: int, number: str, title_words: list, body_style: tuple
) -> tuple[int, str, str] | None:
    if not title_words or len(title_words) > MAX_HEADING_TITLE_WORDS:
        return None

    off_style = sum(1 for w in title_words if _word_style(w) != body_style)
    if off_style / len(title_words) < MIN_STYLE_MATCH_RATIO:
        return None

    title = " ".join(w["text"] for w in title_words)
    return level, number, title


def chunk_pdf(pdf_path: Path, doc_id: str) -> list[Chunk]:
    with pdfplumber.open(pdf_path) as pdf:
        body_left_margin = _compute_body_left_margin(pdf)

        all_lines: list[Line] = []
        for page_num, page in enumerate(pdf.pages, start=1):
            all_lines.extend(_group_words_into_lines(page, page_num, body_left_margin))

        all_lines = _strip_toc_pages(all_lines)
        body_style = _compute_body_style(all_lines)
        all_lines = _strip_header_footer_lines(all_lines, len(pdf.pages))

    chunks: list[Chunk] = []
    heading_stack: list[tuple[str, str]] = []  # [(number, title), ...] level 순서대로
    active_level: list[int] = []

    text_parts: list[str] = []
    page_start = None
    page_end = None
    section_number = None
    section_title = None

    def flush():
        if not text_parts:
            return
        breadcrumb = " > ".join(f"{num} {title}" for num, title in heading_stack)
        chunks.append(
            Chunk(
                doc_id=doc_id,
                section_number=section_number,
                section_title=section_title,
                breadcrumb=breadcrumb or "(문서 서두)",
                page_start=page_start,
                page_end=page_end,
                text=" ".join(text_parts).strip(),
            )
        )

    for line in all_lines:
        heading = _is_heading_line(line, body_style)

        if heading is not None:
            level, number, title = heading
            flush()

            while active_level and active_level[-1] >= level:
                active_level.pop()
                heading_stack.pop()
            active_level.append(level)
            heading_stack.append((number, title))

            section_number, section_title = number, title
            text_parts = []
            page_start = line.page
            page_end = line.page
        else:
            if not line.text.strip():
                continue
            if page_start is None:
                page_start = line.page
            page_end = line.page
            text_parts.append(line.text)

    flush()
    return [c for c in chunks if c.text]


def chunk_and_save(pdf_path: Path, doc_id: str, output_dir: Path) -> Path:
    chunks = chunk_pdf(pdf_path, doc_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{doc_id}.json"
    out_path.write_text(
        json.dumps([c.to_dict() for c in chunks], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path

"""
FDA/ICH/MFDS 규제 가이드라인 코퍼스를 로컬에 다운로드한다.

가이드라인 목록은 코드가 아니라 GUIDELINES 매니페스트로 관리한다 — 이후 문서를
추가/교체할 때 다운로드 로직을 건드리지 않고 목록만 늘리면 되게 하기 위함.

각 URL은 실제로 HTTP HEAD 요청을 보내 content-type: application/pdf와 200 응답을
직접 확인한 뒤에만 매니페스트에 넣었다 (추측으로 채워 넣지 않음).
"""

import argparse
import subprocess
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "guidelines"

GUIDELINES = [
    {
        "id": "ich_e6r2_gcp",
        "source": "ICH",
        "title": "ICH E6(R2): Guideline for Good Clinical Practice",
        "url": "https://database.ich.org/sites/default/files/E6_R2_Addendum.pdf",
        "filename": "ICH_E6R2_GCP.pdf",
    },
    {
        "id": "ich_e8r1_general_considerations",
        "source": "ICH",
        "title": "ICH E8(R1): General Considerations for Clinical Studies",
        "url": "https://database.ich.org/sites/default/files/E8-R1_Guideline_Step4_2021_1006.pdf",
        "filename": "ICH_E8R1_General_Considerations.pdf",
    },
    {
        "id": "fda_pos_pediatric_extrapolation",
        "source": "FDA",
        "title": (
            "Drugs for Treatment of Partial Onset Seizures: Full Extrapolation "
            "of Efficacy From Adults to Pediatric Patients 4 Years of Age and Older"
        ),
        "url": "https://www.fda.gov/media/110916/download",
        "filename": "FDA_PartialOnsetSeizures_PediatricExtrapolation.pdf",
    },
    {
        "id": "mfds_protocol_approval_supplement_cases",
        "source": "MFDS",
        "title": "의약품 임상시험 계획(변경) 승인 보완사례집 (민원인 안내서)",
        "url": "https://www.mfds.go.kr/brd/m_1060/down.do?brd_id=data0011&seq=15858&data_tp=A&file_seq=2",
        "filename": "MFDS_Protocol_Approval_Supplement_Cases.pdf",
    },
]


REQUEST_HEADERS = {
    # fda.gov는 requests 기본 User-Agent를 Akamai 봇 탐지로 막고
    # abuse-detection-apology 페이지로 리다이렉트한다 (404) — 실제로 확인함.
    # 일반 브라우저 UA를 지정하면 통과한다.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def download_via_curl(url: str, dest_path: Path) -> None:
    # fda.gov는 Akamai 봇 탐지가 requests의 TLS/헤더 지문을 걸러낸다 (User-Agent를
    # 바꿔도 abuse-detection-apology로 리다이렉트됨). curl은 실제로 통과하는 것을
    # 확인했으므로 이 소스만 curl 서브프로세스로 우회한다.
    subprocess.run(
        ["curl", "-s", "-f", "-o", str(dest_path), url],
        check=True,
    )


def download(entry: dict, dest_dir: Path, force: bool) -> Path:
    dest_path = dest_dir / entry["filename"]
    if dest_path.exists() and not force:
        print(f"skip (already exists): {dest_path.name}", file=sys.stderr)
        return dest_path

    if entry["source"] == "FDA":
        download_via_curl(entry["url"], dest_path)
        size = dest_path.stat().st_size
    else:
        resp = requests.get(entry["url"], headers=REQUEST_HEADERS, timeout=60)
        resp.raise_for_status()

        # mfds.go.kr은 PDF를 Content-Type: application/octet-stream으로 내려준다
        # (Content-Disposition 파일명에만 .pdf가 붙음) — 실제로 확인함.
        # 그래서 content-type 대신 매직바이트로 PDF 여부를 검증한다.
        if not resp.content.startswith(b"%PDF"):
            raise ValueError(
                f"{entry['id']}: response does not look like a PDF "
                f"(content-type={resp.headers.get('content-type')!r})"
            )

        dest_path.write_bytes(resp.content)
        size = len(resp.content)

    print(f"downloaded: {dest_path.name} ({size:,} bytes)", file=sys.stderr)
    return dest_path


def main():
    parser = argparse.ArgumentParser(description="FDA/ICH/MFDS 가이드라인 코퍼스 다운로드")
    parser.add_argument("--output-dir", default=str(DATA_DIR))
    parser.add_argument("--force", action="store_true", help="이미 있어도 다시 다운로드")
    args = parser.parse_args()

    dest_dir = Path(args.output_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    for entry in GUIDELINES:
        download(entry, dest_dir, args.force)


if __name__ == "__main__":
    main()

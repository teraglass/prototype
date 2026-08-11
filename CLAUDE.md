# CLAUDE.md — 임상 프로토콜 규제 대조 보조 에이전트

> 이 문서는 Claude Code가 세션 시작 시 읽는 프로젝트 컨텍스트이자,
> 설계 의도를 남겨 면접·자기소개서에 그대로 활용하기 위한 근거 문서다.
> **"무엇을(What)"보다 "왜(Why)"를 두껍게 적는다.**

---

## 1. 프로젝트 목적과 스코프

### 만드는 것
비정형 임상시험 프로토콜 문서를 입력받아, 규제 가이드라인 코퍼스를 근거로
**검토 포인트와 잠재 리스크를 출처와 함께 뽑아주는** 에이전트 파이프라인.
사람이 최종 판단하도록 돕는 것이 목표다.

### 명시적으로 만들지 않는 것 (스코프 경계)
- **규제 판정/승인 여부를 결정하는 도구가 아니다.** "이 프로토콜은 FDA 승인 가능"
  같은 판정은 하지 않는다. "이 항목이 어떤 규정과 관련되며 검토가 필요하다"까지만.
- 범용 문서처리 플랫폼이 아니다. 임상 프로토콜 ↔ 규제 가이드라인 대조라는
  **단일 시나리오**에 집중한다.
- 완벽한 대시보드/프론트엔드는 1차 목표가 아니다. observability는 계측 훅을
  처음부터 심는 것이 핵심이지, 화려한 UI가 아니다.

> ⚠️ Claude Code에게: 기능을 스스로 부풀리지 말 것. 위 경계를 넘는 제안은
> 먼저 확인을 구할 것.

---

## 2. 아키텍처 개요

데이터 흐름:

```
[수집]  ClinicalTrials.gov API v2 ──▶ 프로토콜 PDF (검토 대상 문서)
        FDA / ICH / MFDS 가이드라인 ─▶ 규제 코퍼스 (대조 기준 문서)
          │
[청킹]  섹션 인식 청킹 (구조 기반 분할) ── 두 코퍼스 모두
          │
[임베딩] 임베딩 → Chroma (로컬 벡터스토어)
          │
[에이전트] LangGraph 오케스트레이션
          ├─ 1) 프로토콜 파싱·섹션 구조화 추출
          ├─ 2) 관련 규제 규칙 retrieval
          ├─ 3) 정합/충돌 대조 → 리스크 플래그 + 근거 출처
          └─ 4) 신뢰도 낮으면 human-in-the-loop 에스컬레이션
          │
[출력]  검토 리포트 (플래그 + 출처 인용)
          │
[관측]  OpenTelemetry 계측 (전 단계) ── trace / latency / token
        retrieval hit·miss / groundedness score
        + 개발 중 확인용 LangSmith (보조)
```

**두 코퍼스 구조가 핵심이다.** 프로토콜은 "검사 대상", 가이드라인은 "대조 기준".
이 분리가 있어야 "이 프로토콜의 X항목이 규정 Y와 관련"이라는 근거 있는 대조가 된다.

---

## 3. 설계 결정과 근거 (면접 자산 — 가장 중요)

각 선택의 "왜"를 남긴다. 면접에서 "이거 왜 이렇게 만들었어요?"에 그대로 답한다.

### 3.1 섹션 인식 청킹 (고정 크기 청킹이 아니라)
임상 프로토콜은 목적·적격기준·투여계획·평가변수·통계계획 등 **섹션 구조가 강하다.**
고정 500토큰으로 기계적으로 자르면 "적격기준"이 문단 중간에서 잘려 retrieval 품질이
무너진다. 그래서 문서 구조를 인식해 섹션 경계 기준으로 분할한다.
→ JD의 *"define an appropriate technical approach"*를 증명하는 지점.

### 3.2 오케스트레이션은 LangGraph, 도메인 로직은 직접 구현
에이전트 흐름에 조건 분기(신뢰도 낮으면 에스컬레이션)가 있어 상태 기반 그래프 모델이
적합하다. 반면 청킹·대조 같은 **핵심 도메인 로직은 직접 구현**해 투명성을 확보한다.
"프레임워크에 맡길 것과 직접 짤 것을 구분한" 판단 자체가 어필 포인트.

### 3.3 Observability는 OpenTelemetry 메인, LangSmith 보조
LangSmith만 쓰면 "LLM 앱 개발자" 도구에서 멈춘다. OTel은 벤더 중립 표준이라
trace·metric·log를 어디로든 보낼 수 있고, 이는 **인프라 레벨 관측 = 시스템 엔지니어
정체성**을 증명한다. 개발 편의용으로 LangSmith를 병행하되, 스토리 전면엔 OTel을 둔다.
→ JD가 core로 지정한 *"observability into pipelines from the outset"* 정면 충족.

### 3.4 스코프 = 판정 아닌 검토 보조
규제 판정을 자동화한다고 하면 과장이자 위험. "근거 문서로 검토 포인트를 뽑아 사람이
판단하게 돕는다"가 정확하고, JD의 *"현업 수작업을 줄이는 자동화"*와도 일치.

### 3.5 신뢰도 기반 human-in-the-loop
불확실할 때 자동 결정하지 않고 사람에게 에스컬레이션. 그 비율을 지표로 추적한다.
대부분의 데모가 happy path만 보여주는 것과 차별화되는 성숙도 지점.

---

## 4. 단계별 작업 계획 (조각내서 진행)

> **통째로 시키지 말 것.** 한 조각씩, 결과를 확인하고 다음으로. 각 단계마다
> Tera가 코드를 이해하고 있어야 면접에서 막힘없이 설명할 수 있다.

### Phase 0 — 스캐폴드
저장소 구조, 가상환경, 의존성, `.env` 설정.
- 프롬프트 예: "파이썬 3.11 프로젝트 스캐폴드를 만들어줘. 구조는 ingestion/,
  chunking/, retrieval/, agent/, observability/ 로 나누고, requirements와
  .env.example만 먼저. 아직 로직은 넣지 마."

### Phase 1 — 수집 스크립트 (첫 실물, 여기부터 시작)
ClinicalTrials.gov API v2로 특정 질환(뇌전증, SK바이오팜 도메인) 필터 →
`hasProtocol=true`인 트라이얼만 → 프로토콜 PDF 다운로드.
- 프롬프트 예: "ClinicalTrials.gov API v2로 condition=epilepsy 필터해서
  study protocol 문서가 첨부된 트라이얼 10건의 메타데이터와 PDF 다운로드 URL을
  가져오는 스크립트를 짜줘. 응답 구조를 먼저 하나 출력해서 같이 확인하자."
- **응답 구조를 실제로 받아본 뒤** 파싱을 짠다. 추측 금지.

### Phase 2 — 가이드라인 코퍼스 + 섹션 청킹
FDA/ICH/MFDS 가이드라인 PDF 수집 → 섹션 인식 청킹. 프로토콜도 동일 청킹.
- 여기서 §3.1 근거를 코드로 구현. 왜 이렇게 잘랐는지 주석으로 남긴다.

### Phase 3 — 임베딩 + 벡터스토어
Chroma에 두 코퍼스를 컬렉션 분리해 적재. (protocol / guideline)

### Phase 4 — retrieval + 대조 로직
프로토콜 섹션 → 관련 가이드라인 retrieval → 정합/충돌 대조 → 플래그 + 출처.
직접 구현(§3.2). groundedness를 측정 가능하게 설계.

### Phase 5 — LangGraph 에이전트 배선
위 조각들을 그래프 노드로 연결. 조건 분기(신뢰도 → 에스컬레이션) 포함.

### Phase 6 — OpenTelemetry 계측
LangGraph 노드에 span 심기. trace/latency/token/retrieval hit·miss/groundedness.
- **1차엔 핵심 span 몇 개만.** 대시보드는 후순위. "처음부터 심었다"가 목표.
- 개발 중 확인용 LangSmith 병행.

### Phase 7 — 리포트 출력 + human-in-the-loop
검토 리포트 포맷팅. 신뢰도 낮은 항목 에스컬레이션 경로.

---

## 5. 기술 스택과 제약

| 항목 | 선택 | 비고 |
|------|------|------|
| 언어 | Python 3.11+ | |
| 오케스트레이션 | LangGraph | 조건 분기·상태 그래프 |
| 벡터스토어 | Chroma | 로컬, 가볍게 |
| Observability | OpenTelemetry (메인) | 벤더 중립, 인프라 색 |
| 개발용 추적 | LangSmith (보조) | 편의용, 전면에 안 세움 |
| PDF 파싱 | pypdf / pdfplumber | 섹션 구조 추출 |
| LLM | Anthropic 또는 OpenAI API | |
| 데이터 | ClinicalTrials.gov API v2 | 프로토콜 |
|      | FDA / ICH / MFDS 가이드라인 | 규제 코퍼스 |

**제약:**
- 무거운 프레임워크를 멋대로 끌어오지 말 것. 위 스택 밖은 먼저 확인.
- 도메인 로직(청킹·대조)은 직접 구현. 블랙박스에 위임 금지.
- 모든 리스크 플래그는 **출처(어느 문서 어느 섹션)를 반드시 동반**한다.

---

## 6. JD 매핑 (자소서·면접용 대조표)

| JD 문구 | 이 프로젝트의 증명 |
|---------|------------------|
| manual workflows → automated service | 프로토콜 수작업 검토를 에이전트로 보조 |
| define an appropriate technical approach | 섹션 청킹, LLM 쓸 곳/안 쓸 곳 판단 |
| build agentic AI / multi-agent | LangGraph 다단계 에이전트 |
| RAG pipelines | 가이드라인 근거 retrieval |
| observability from the outset | OTel 계측을 처음부터 |
| monitor, diagnose, improve | retrieval hit·miss·groundedness 추적 |
| communicating with non-technical stakeholders | 리포트가 근거·출처로 검토자를 설득 |
| learning unfamiliar tech by doing | 규제/임상 도메인을 직접 파며 구현 |

---

## 7. 작업 원칙 (Claude Code에게)
- 한 번에 한 Phase. 완료 후 다음으로.
- 추측하지 말고, 외부 API 응답은 실제로 받아본 뒤 파싱한다.
- 설계 결정을 바꿀 땐 그 근거를 이 문서 §3에 남긴다.
- 스코프(§1) 경계를 넘는 제안은 먼저 확인을 구한다.

# docsRead

약관·상품설명서 PDF를 구조화 데이터로 바꾸고, 유사 조항을 공통 모듈로 묶어 편집하는 시스템.

## 현재 단계 (1단계: 원본과 같은 데이터화)

| 계층 | 테이블 | 내용 |
|---|---|---|
| 레이아웃 | `layout_items` | 글자(글꼴·크기·색·좌표), 도형(벡터 경로), 이미지를 그리기 순서대로 보존 |
| 구조 | `blocks` | 제목·섹션·조(제N조)·문단·표(병합 칸 포함)·그림·꼬리말 |
| 자산 | `assets` | 이미지 원본(내용 해시로 중복 제거) |

## 2단계: 공통 모듈

`uv run python -m docsread.modules.build` 가 구조 블록에서 모듈을 다시 만든다(로컬 계산, 외부 통신 없음).

- 모듈(`modules`): 같은 취지의 조·항목 묶음 — 글자 n-gram 유사도 군집
- 문안(`module_variants`): 모듈 안의 템플릿. 문서 간 차이가 짧으면 변수, 문장 단위로 다르면 별도 문안
- 사용처(`module_occurrences`): 문서 안의 위치와 그 자리의 변수 값
- 기준값은 `backend/docsread/modules/build.py` 상단의 `SIM_THRESHOLD`, `MAX_CHANGED`, `MAX_SPAN`

## 3단계: 편집기

화면 상단 **편집기** 탭. 기존 문서를 복제해 편집 문서(`drafts`)를 만들고 TipTap(ProseMirror) 위에서 고친다.

- 조·항목은 모듈 블록, 문서마다 달라지는 짧은 자리는 변수 칩으로 들어온다(`backend/docsread/editor/convert.py`)
- 같은 이름의 변수는 값을 한 번에 바꾼다. 선택한 글자를 변수로 만들 수 있다
- 모듈 블록은 다른 문안으로 교체하거나, 라이브러리에서 검색해 새로 넣을 수 있다
- 편집 용지는 원본에서 잰 값(용지 치수, 본문 글자 크기, 줄 간격, 표 괘선·선 색·행 높이)으로 그린다
- 디자인 요소는 모두 편집 가능한 부품이다: 박스(테두리·둥근 모서리·배경), 단 나눔, 표(병합·괘선·칸 배경·행 높이·열 너비 끌기·띄움),
  글자 크기·색·정렬, 이미지와 캡션 그림, 사진 묶음. 원본 문서도 같은 부품으로 변환되므로 빈 문서에서 같은 결과를 만들 수 있다
- 이미지 올리기는 `POST /api/assets` (PNG 로 바꿔 `data/assets` 에 저장, 15MB 이하)
- 편집 문서는 스스로 완결된 JSON 이라 모듈을 다시 계산해도 깨지지 않는다. 원본 문서 데이터는 바뀌지 않는다

## 4단계: AI 변환(멀티모달 LLM)

템플릿이 제각각인 문서를 위해, 쪽 이미지를 LLM 이 읽고 **정해진 규격의 JSON** 으로만 답하게 한 뒤 자체 코드로 편집기 문서를 만든다.

`PDF 쪽 이미지(200dpi) → OpenAI (Structured Outputs 규격 강제 + few-shot + temperature 0) → 정형 JSON → to_editor.py → 편집 문서`

- 규격: `backend/docsread/llm/schema.py` — 블록 종류(title·section·article_title·paragraph·notice·table·image·page_artifact)와
  색(default·red·gray)·정렬이 열거형이라 모델이 태그·스타일을 지어낼 수 없다
- 모양: `backend/docsread/llm/to_editor.py` 상단 상수(색 값·칸 배경 단계·안내 상자)가 정한다 → 같은 종류 문서는 같은 모양
- 역할 나눔: LLM 은 '무엇인지'(제목·조·문단·표 구조·붉은 글·정렬·두 단), PDF 데이터화 결과는 '치수와 그림'
  (글자가 90% 이상 맞는 PDF 표의 열 너비·칸 배경·괘선, 사진 묶음 배치·캡션, 꼬리말 로고·정보 줄·전화번호 띠).
  PDF 표 인식이 틀린 경우(사진 영역까지 표로 잡음 등)에는 LLM 의 열 비율·배경 단계를 쓰고, 그 표에 묶였던 그림은 사진 묶음으로 돌린다
- 도형으로 그린 꼬리말 로고는 PDF 에서 그 영역만 잘라 그림 자산으로 넣는다
- few-shot: `data/llm_fewshot/<문서종류>_<이름>_p<쪽>.png` + `.json`(정답). 변환하는 문서와 같은 종류(terms|brochure)만 2쪽까지 보낸다.
  검수된 편집 문서에서 만든다: `uv run python -m docsread.llm.fewshot <편집문서 id> <원본 문서 id> <이름>`
  (쪽 나눔으로 원본 쪽과 짝짓고, 원본 글자와 97% 미만으로 맞으면 저장하지 않음)
- 캐시: 쪽 결과는 `llm_pages` 에 저장되고, 모델·프롬프트·규격·few-shot 이 같으면 다시 보내지 않는다
- 검증: 쪽마다 PDF 글자 레이어와 LLM 결과의 글자를 대조해 97% 미만이면 경고(빠뜨림·오독). 한 쪽이라도 실패하면 편집 문서를 만들지 않는다
- 화면: 편집기 왼쪽 검색 결과의 **AI 변환**(키가 설정된 경우에만 보임). API: `POST /api/llm/convert {document_id}`, `GET /api/llm/jobs/{id}`

설정: 프로젝트 루트 `.env`(`.env.example` 복사, git 제외) 또는 환경변수.
`DOCSREAD_LLM_PROVIDER`(`gemini` | `openai` | `claude`), `DOCSREAD_LLM_MODEL`(예: `gemini-3.8-flash`, `claude-haiku-4-5`), 키 `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`,
`DOCSREAD_LLM_EFFORT`(Claude 생각 깊이, 기본 보내지 않음 — Haiku 4.5 는 받지 않음. Claude 에는 temperature 를 보내지 않음),
`DOCSREAD_LLM_TEMPERATURE`(기본 0, 비우면 보내지 않음), `DOCSREAD_LLM_THINKING`(Gemini 생각 수준: 숫자=토큰 한도, 글자=low 등),
`DOCSREAD_LLM_DPI`(기본 200). 쓸 수 있는 Gemini 모델 ID 확인: `uv run python -m docsread.llm.models flash`

## 실행

```bash
uv sync
uv run python -m docsread.ingest.pipeline          # docs/ → data/docsread.db (원본 PDF는 읽기만 함)
uv run python -m docsread.modules.build            # 공통 모듈·변수 추출
cd frontend && npm install && npm run build && cd ..
uv run uvicorn docsread.api.main:app --port 8000   # http://127.0.0.1:8000
```

화면에서 올리기: **문서** 탭 왼쪽의 **PDF 올리기**(여러 개 선택·끌어 놓기 가능). `POST /api/documents/upload?filename=…`
(본문 = PDF 바이트, 100MB 이하)로 `docs/uploads/` 에 저장하고 곧바로 데이터화한다. 같은 내용은 다시 적재하지 않고,
같은 이름·다른 내용은 `이름 (2).pdf` 로 따로 저장한다. 공통 모듈은 자동으로 다시 계산하지 않으므로
목록 아래 **다시 계산** 버튼(`POST /api/module-rebuild`, 백그라운드)으로 반영한다.

화면 개발 시에는 `cd frontend && npm run dev` (5173 포트, /api 는 8000 으로 프록시).

## 구성

- `backend/docsread/ingest/extract.py` — PDF 내부 값 추출(글자·도형·이미지)
- `backend/docsread/ingest/tables.py` — 괘선으로부터 표 격자·병합 칸 복원
- `backend/docsread/ingest/structure.py` — 읽기 순서 정렬, 조·항·문단·표 구조화
- `backend/docsread/api/main.py` — 조회 API
- `frontend/` — 원본 / 데이터 재현 / 구조 비교 화면

## IT 검토가 필요한 지점

- 패키지: PyMuPDF 는 AGPL 라이선스(사내 전용이면 통상 무방, 외부 제공 시 상용 라이선스 검토)
- 외부 통신(AI 변환): 쪽 이미지가 고른 공급자(Google Gemini · OpenAI · Anthropic Claude) API 로 전송된다. 버튼을 누른 문서만 보내며, 키는 `.env`(git 제외) 또는 환경변수로 받는다(코드·DB 에 저장 안 함).
  개인정보가 들어간 문서(청약서·가입 내역 등)는 보내지 않도록 운영 기준 필요. 패키지 `google-genai`, `openai`, `anthropic` 추가
- 외부 통신: 화면이 Google Fonts(Noto Sans KR)를 불러온다. 폐쇄망이면 글꼴을 내부에 두도록 변경 필요
- 저장 위치: 결과는 `data/` (SQLite 파일 + 이미지). 다중 사용자 운영 시 PostgreSQL 로 이전(`DOCSREAD_DB`)

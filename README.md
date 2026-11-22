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
- 사진 묶음과 꼬리말은 원본 그리기 요소를 좌표째 담은 "원본 영역" 노드로 넣는다(통째로 이동·삭제만 가능)
- 편집 문서는 스스로 완결된 JSON 이라 모듈을 다시 계산해도 깨지지 않는다. 원본 문서 데이터는 바뀌지 않는다

## 실행

```bash
uv sync
uv run python -m docsread.ingest.pipeline          # docs/ → data/docsread.db (원본 PDF는 읽기만 함)
uv run python -m docsread.modules.build            # 공통 모듈·변수 추출
cd frontend && npm install && npm run build && cd ..
uv run uvicorn docsread.api.main:app --port 8000   # http://127.0.0.1:8000
```

화면 개발 시에는 `cd frontend && npm run dev` (5173 포트, /api 는 8000 으로 프록시).

## 구성

- `backend/docsread/ingest/extract.py` — PDF 내부 값 추출(글자·도형·이미지)
- `backend/docsread/ingest/tables.py` — 괘선으로부터 표 격자·병합 칸 복원
- `backend/docsread/ingest/structure.py` — 읽기 순서 정렬, 조·항·문단·표 구조화
- `backend/docsread/api/main.py` — 조회 API
- `frontend/` — 원본 / 데이터 재현 / 구조 비교 화면

## IT 검토가 필요한 지점

- 패키지: PyMuPDF 는 AGPL 라이선스(사내 전용이면 통상 무방, 외부 제공 시 상용 라이선스 검토)
- 외부 통신: 화면이 Google Fonts(Noto Sans KR)를 불러온다. 폐쇄망이면 글꼴을 내부에 두도록 변경 필요
- 저장 위치: 결과는 `data/` (SQLite 파일 + 이미지). 다중 사용자 운영 시 PostgreSQL 로 이전(`DOCSREAD_DB`)

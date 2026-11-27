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

## 원본 PDF 직접 수정용 글꼴 (시험 단계)

상품설명서는 다시 조판하지 않고 원본 PDF 의 글자만 바꿔 넣는 방식을 시험 중이다.
PDF 안의 글꼴은 쓰인 글자 조각뿐이라 새 글자를 쓸 수 없으므로, 공개 글꼴을 따로 받아 둔다.

- 본고딕(Source Han Sans KR, SIL OFL) 6종을 `data/fonts/` 에 받는다:
  `github.com/adobe-fonts/source-han-sans` 의 `SubsetOTF/KR/SourceHanSansKR-{Light,Normal,Regular,Medium,Bold,Heavy}.otf`
- `uv run python -m docsread.pdfedit.fonts` 로 TrueType 사본(`data/fonts/ttf/`)을 만든다.
  받은 OTF 를 그대로 쓰면 PDF 라이브러리(MuPDF)가 글자 번호를 잘못 적어 한글이 다른 글자로 찍힌다
- 글자를 지울 때는 글줄 가운데의 얇은 띠만 지운다(글자 상자 전체로 지우면 위아래 줄이 함께 지워진다)

## 상품설명서: 원본 PDF 쪽 위에서 고치기 (쪽 편집)

상품설명서는 다시 조판하지 않고, 원본 PDF 의 글자·그림만 바꾼다. LLM 은 쓰지 않는다.

- 화면: **문서** 탭에서 상품설명서를 고르고 **원본 위에서 고치기** → **쪽 편집** 탭. 글자를 눌러 고치고 Enter, 그림 위에서 **그림 바꾸기**.
  화면의 쪽 그림은 서버가 고친 PDF 를 그린 것이라 **PDF 내려받기** 결과와 같다
- 원본과 맞추는 값(조각마다 원본에서 잰다): 글꼴·굵기, 크기, 색, **장평**(원본은 대개 94~98%), **자간**, 띄어쓰기 폭, 기준선.
  글자가 바뀌지 않은 앞부분은 원본의 글자 위치를 그대로 쓴다(커닝까지 보존)
- 길이가 달라지면 원본 맞춤(왼쪽·가운데·오른쪽, 자동 판단 + 직접 선택)에 따라 다시 놓고 같은 줄의 뒤 글자를 민다.
  칸을 넘으면 자간(-5% 까지) → 장평(88% 까지) 순으로 줄여 맞추고, 그래도 넘으면 경고한다
- 그림은 같은 자리·크기·기울기로 바꿔 끼운다(비율이 다르면 가운데를 자름)
- **표 행 추가·삭제**: 글자를 누른 뒤 **아래에 행 추가 / 이 행 삭제**. 원본 쪽을 가로 띠로 잘라 다시 쌓는다 —
  추가는 그 행(선·배경 포함)을 복제해 아래에 끼우고 글자만 새로 쓰며, 삭제는 그 띠를 뺀다.
  늘거나 준 높이는 쪽 전체(표가 쪽 너비를 다 쓸 때) 또는 그 표 안에서 세로 비율을 조금 바꿔 흡수한다(한 행에 약 1%).
  여러 행에 걸친 병합 칸의 제목은 새 칸의 가운데로 옮긴다. 지운 행은 왼쪽 목록에서 되살릴 수 있다. 열 추가는 지원하지 않는다
- 원본은 읽기만 한다. 고친 목록만 `pdf_edits` 테이블에 저장하고 볼 때마다 원본에 다시 입힌다
- 코드: `backend/docsread/pdfedit/engine.py`(재기·지우기·쓰기), `frontend/src/PageEditView.tsx`, API `/api/pdfedits`
- 검증: `uv run python -m docsread.pdfedit.verify` — 모든 글자를 같은 글자로 다시 써서 원본과 화소 비교.
  상품설명서 14건 표본에서 한쪽에만 있는 화소 0.30% 이하, 글자 위치 오차 99% 가 0.09pt 이하
- 한계: 본고딕이 아닌 글꼴(나눔고딕·Apple SD 등, 글자의 약 5%)은 본고딕으로 대체해 쓴다(화면에 표시). 세로쓰기는 고칠 수 없다.
  줄바꿈을 새로 만들지는 않는다(한 줄 안에서만 고침). 색 띠 안의 제목 일부는 가운데 맞춤을 자동으로 못 알아봐 직접 골라야 한다

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
- 외부 통신: 화면이 Google Fonts(Noto Sans KR)를 불러온다. 폐쇄망이면 글꼴을 내부에 두도록 변경 필요
- 저장 위치: 결과는 `data/` (SQLite 파일 + 이미지). 다중 사용자 운영 시 PostgreSQL 로 이전(`DOCSREAD_DB`)

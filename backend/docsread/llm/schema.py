"""LLM 출력 규격. 이 모델이 그대로 OpenAI Structured Outputs(strict JSON Schema)로 전달된다.

- 블록 종류·색·정렬·배경은 모두 열거형이라, 모델은 정해진 값 밖의 태그·스타일을 만들 수 없다.
- 실제 모양(색 값·괘선·박스·여백)은 모델이 아니라 to_editor.py 가 정한다 → 같은 종류의 문서는 같은 모양.
- 표 치수·꼬리말·사진 배치는 PDF 에서 잰 값을 우선 쓰고, 맞는 것이 없을 때만 여기 값(col_widths·shade)을 쓴다.
- 규격을 바꾸면 prompt_version 이 바뀌어 캐시된 결과를 쓰지 않는다.
"""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Segment(_M):
    text: str = Field(description="원문 글자 그대로. 고치거나 요약하지 않는다")
    bold: bool
    color: Literal["default", "red", "gray"] = Field(
        description="red: 붉은 글씨(강조·중요 안내), gray: 회색 보조 글씨, default: 그 밖의 모든 글씨")


HeadColor = Literal["default", "red"]


class Title(_M):
    type: Literal["title"]
    text: str = Field(description="문서 맨 위 큰 제목(예: 상조 서비스 약관, 상품 설명서). 문서 첫 쪽에 하나")
    color: HeadColor


class Section(_M):
    type: Literal["section"]
    text: str = Field(description="장·편 제목 또는 색 띠·둥근 머리 상자로 구분된 큰 항목 제목"
                                  "(예: 장례 상품 정보, 소비자 피해보상보험 계약 안내)")
    color: HeadColor
    columns: Literal[1, 2] = Field(description="이 제목 아래 내용이 좌우 두 단으로 흘러가면 2, 아니면 1")


class ArticleTitle(_M):
    type: Literal["article_title"]
    text: str = Field(description="조 제목 한 줄 전체(예: 제1조 (목적))")
    color: HeadColor = Field(description="조 제목 글자가 붉은색이면 red")


class Paragraph(_M):
    type: Literal["paragraph"]
    style: Literal["body", "bullet", "numbered"] = Field(
        description="bullet: •·※·- 등 기호로 시작, numbered: ①·1.·가. 등 번호로 시작, body: 그 밖의 문단")
    align: Literal["left", "center", "right"] = Field(description="글 영역 가운데에 놓인 줄(제목 아래 안내 문구 등)이면 center")
    segments: list[Segment]


class Notice(_M):
    type: Literal["notice"]
    tone: Literal["red", "gray", "plain"] = Field(
        description="테두리 상자나 배경색 상자 안의 안내문. red: 붉은 테두리·붉은 글, gray: 회색 배경, plain: 그 밖")
    paragraphs: list[Paragraph]


class Cell(_M):
    segments: list[Segment] = Field(description="칸 안의 글. 칸 안 줄바꿈은 text 안의 줄바꿈 문자로 둔다")
    header: bool = Field(description="제목 칸(굵은 글·배경색 칸)이면 true")
    shade: Literal["none", "light", "medium", "dark", "cream"] = Field(
        description="칸 배경색. light: 아주 옅은 회색, medium: 회색, dark: 진한 회색(흰 글씨), cream: 옅은 노랑·크림색, none: 흰 배경")
    image: bool = Field(description="칸 안에 사진·그림이 있으면 true(그림 안의 글자는 옮기지 않는다)")
    colspan: int = Field(description="가로로 합쳐진 칸 수(합쳐지지 않았으면 1)")
    rowspan: int = Field(description="세로로 합쳐진 칸 수(합쳐지지 않았으면 1)")
    align: Literal["left", "center", "right"]


class Row(_M):
    cells: list[Cell] = Field(description="이 행에서 새로 시작하는 칸만. 위 행에서 세로로 합쳐져 내려온 칸은 다시 쓰지 않는다")


class Table(_M):
    type: Literal["table"]
    col_widths: list[int] = Field(description="열마다 표 전체 너비에 대한 비율(%). 열 수만큼, 합 100")
    rows: list[Row]


class Image(_M):
    type: Literal["image"]
    caption: str = Field(description="사진·그림 바로 아래 설명 글. 없으면 빈 문자열. 꼬리말의 회사 로고는 image 가 아니다")


class SideStart(_M):
    type: Literal["side_start"]
    widths: list[int] = Field(description="좌우로 나란히 놓인 영역의 열 너비 비율(%). 왼쪽부터, 열 수만큼, 합 100")


class SideNext(_M):
    type: Literal["side_next"]


class SideEnd(_M):
    type: Literal["side_end"]


class PageArtifact(_M):
    type: Literal["page_artifact"]
    text: str = Field(description="쪽 번호, 매 쪽 반복되는 머리말·꼬리말(회사 로고·주소·대표이사·전화번호 줄)")


Block = Union[Title, Section, ArticleTitle, Paragraph, Notice, Table, Image, SideStart, SideNext, SideEnd, PageArtifact]


class PageOut(_M):
    starts_mid_paragraph: bool = Field(
        description="쪽 첫 글이 앞 쪽 문단의 이어지는 부분(문장 중간에서 시작)이면 true")
    blocks: list[Block] = Field(description="읽는 순서대로. 좌우로 나란한 영역은 side_start → 왼쪽 열 블록들 → side_next → "
                                            "오른쪽 열 블록들 → side_end 로 감싼다(겹쳐 쓰지 않는다)")

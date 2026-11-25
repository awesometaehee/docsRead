"""저장 모델. SQLite 로 시작하되 SQLAlchemy 로 작성해 PostgreSQL 로 그대로 옮길 수 있다.

두 계층으로 저장한다.
- 레이아웃 계층(layout_items): 글자·도형·이미지를 좌표와 함께 그대로 보존 → 원본과 같은 화면 재현
- 구조 계층(blocks): 조·항·문단·표·그림 등 의미 단위 → 유사 조항 묶기(모듈화)와 편집기의 입력
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import DATA_DIR, DB_URL


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    rel_path: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(String(16), index=True)      # terms | brochure
    company_code: Mapped[str | None] = mapped_column(String(8), index=True)
    company: Mapped[str | None] = mapped_column(String(32))
    form_no: Mapped[str | None] = mapped_column(String(32))
    product: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[str | None] = mapped_column(String(16))
    valid_to: Mapped[str | None] = mapped_column(String(16))
    revised_at: Mapped[str | None] = mapped_column(String(16))
    page_count: Mapped[int] = mapped_column(Integer)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    pages: Mapped[list["Page"]] = relationship(back_populates="document", cascade="all, delete-orphan",
                                               order_by="Page.page_no")
    blocks: Mapped[list["Block"]] = relationship(back_populates="document", cascade="all, delete-orphan",
                                                 order_by="Block.seq")


class Page(Base):
    __tablename__ = "pages"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    width: Mapped[float] = mapped_column(Float)
    height: Mapped[float] = mapped_column(Float)
    document: Mapped[Document] = relationship(back_populates="pages")
    items: Mapped[list["LayoutItem"]] = relationship(back_populates="page", cascade="all, delete-orphan",
                                                     order_by="LayoutItem.z")


class LayoutItem(Base):
    """페이지 위의 그리기 요소 하나. z 는 PDF 의 그리기 순서."""
    __tablename__ = "layout_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    z: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(8))   # text | path | image
    x0: Mapped[float] = mapped_column(Float)
    y0: Mapped[float] = mapped_column(Float)
    x1: Mapped[float] = mapped_column(Float)
    y1: Mapped[float] = mapped_column(Float)
    data: Mapped[dict] = mapped_column(JSON)
    page: Mapped[Page] = relationship(back_populates="items")


class Asset(Base):
    """이미지 원본. 내용 해시로 중복 제거(로고·공통 사진은 한 번만 저장)."""
    __tablename__ = "assets"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    bytes: Mapped[int] = mapped_column(Integer)
    use_count: Mapped[int] = mapped_column(Integer, default=0)


class Block(Base):
    """의미 단위. kind: title|section|article|paragraph|table|figure|footer"""
    __tablename__ = "blocks"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    parent_seq: Mapped[int | None] = mapped_column(Integer)
    page_no: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    label: Mapped[str | None] = mapped_column(String(32))     # 예: 제5조
    title: Mapped[str | None] = mapped_column(Text)           # 예: 계약상품, 납입금, 납입방법, 납입종별
    text: Mapped[str] = mapped_column(Text, default="")       # 정규화된 순수 텍스트(검색·유사도용)
    content: Mapped[dict] = mapped_column(JSON, default=dict) # 서식 run / 표 셀 / 그림 정보
    bbox: Mapped[list | None] = mapped_column(JSON)
    document: Mapped[Document] = relationship(back_populates="blocks")


Index("ix_blocks_doc_seq", Block.document_id, Block.seq)


class Module(Base):
    """여러 문서에 반복되는 같은 취지의 내용 묶음(예: '청약철회권의 행사')."""
    __tablename__ = "modules"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)   # article | section | item | paragraph | footer
    title: Mapped[str] = mapped_column(Text)
    n_docs: Mapped[int] = mapped_column(Integer, index=True)
    n_occurrences: Mapped[int] = mapped_column(Integer)
    n_variants: Mapped[int] = mapped_column(Integer)
    n_slots: Mapped[int] = mapped_column(Integer)
    doc_types: Mapped[list] = mapped_column(JSON)
    variants: Mapped[list["ModuleVariant"]] = relationship(back_populates="module", cascade="all, delete-orphan",
                                                           order_by="ModuleVariant.ordinal")


class ModuleVariant(Base):
    """모듈의 한 가지 문안(템플릿). parts 는 고정 문구와 변수 자리의 나열."""
    __tablename__ = "module_variants"
    id: Mapped[int] = mapped_column(primary_key=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("modules.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    n_docs: Mapped[int] = mapped_column(Integer)
    n_occurrences: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)                    # 대표 문안(변수에 기본값을 채운 것)
    parts: Mapped[list] = mapped_column(JSON)                  # [{t: 고정 문구} | {v: 변수 번호}]
    slots: Mapped[list] = mapped_column(JSON)                  # [{name, type, default, n_values, top}]
    module: Mapped[Module] = relationship(back_populates="variants")
    occurrences: Mapped[list["ModuleOccurrence"]] = relationship(back_populates="variant", cascade="all, delete-orphan")


class ModuleOccurrence(Base):
    """문서 안의 실제 위치 하나와, 그 자리에서 변수들이 가진 값."""
    __tablename__ = "module_occurrences"
    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("module_variants.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    block_seq: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(32))
    chars: Mapped[int] = mapped_column(Integer)
    values: Mapped[list] = mapped_column(JSON)
    variant: Mapped[ModuleVariant] = relationship(back_populates="occurrences")

class Draft(Base):
    """편집 문서. content 는 편집기(ProseMirror) 문서 JSON 으로, 모듈 재계산과 무관하게 스스로 완결된다."""
    __tablename__ = "drafts"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(String(16))
    base_document_id: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSON)
    style: Mapped[dict] = mapped_column(JSON, default=dict)   # 용지 디자인 값(본문 크기·줄 간격 등)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LlmPage(Base):
    """쪽 하나를 LLM 으로 읽은 결과(정형 JSON). 같은 모델·프롬프트 판이면 다시 부르지 않고 이 값을 쓴다."""
    __tablename__ = "llm_pages"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(16))
    output: Mapped[dict] = mapped_column(JSON)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


Index("ix_llm_pages_key", LlmPage.document_id, LlmPage.page_no, LlmPage.model, LlmPage.prompt_version)


DATA_DIR.mkdir(parents=True, exist_ok=True)
engine = create_engine(DB_URL, future=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    # 가벼운 스키마 보강: 이미 있는 테이블에 새 열 추가(데이터는 건드리지 않는다)
    from sqlalchemy import inspect, text
    cols = {c["name"] for c in inspect(engine).get_columns("drafts")}
    if "style" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE drafts ADD COLUMN style JSON"))

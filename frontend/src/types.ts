export interface DocRow {
  id: number; filename: string; doc_type: "terms" | "brochure"; company_code: string | null;
  company: string | null; form_no: string | null; product: string | null; valid_from: string | null;
  valid_to: string | null; revised_at: string | null; page_count: number;
  stats: { chars: number; paths: number; images: number; tables: number; articles: number; sections: number; warnings: string[] };
}
export interface DocDetail extends DocRow { pages: { page_no: number; width: number; height: number }[] }

export interface Span { t: string; font: string; size: number; w: number; color: string; o: [number, number]; x1: number }
export interface LayoutItem {
  k: "text" | "path" | "image"; b: [number, number, number, number];
  spans?: Span[]; dir?: [number, number];
  d?: string; fill?: string; fo?: number; eo?: number; stroke?: string; sw?: number; so?: number; dash?: string;
  asset?: string; m?: number[];
}
export interface Layout { width: number; height: number; items: LayoutItem[] }

export interface Run { t: string; b?: number; c?: string; s?: number }
export interface Cell { r: number; c: number; rs: number; cs: number; runs: Run[]; text: string; align: string; bd: number[]; fill?: string }
export interface Block {
  seq: number; parent: number | null; page: number; kind: string; label: string | null; title: string | null; text: string;
  content: {
    runs?: Run[]; clauses?: { no: number; text: string }[];
    rows?: number; cols?: number; col_w?: number[]; row_h?: number[]; cells?: Cell[];
    records?: { row: number; path: string[]; value: string }[];
    asset?: string; w?: number; h?: number; caption?: string | null;
  };
  bbox: number[] | null;
}

export interface ModuleRow { id: number; kind: string; title: string; n_docs: number; n_occurrences: number; n_variants: number; n_slots: number; doc_types: string[] }
export interface Slot { name: string; type: string; default: string; n_values: number; top: [string, number][] }
export type Part = { t: string } | { v: number };
export interface Occurrence { doc_id: number; seq: number; label: string | null; values: string[]; doc_type: string; company: string | null; product: string | null; revised_at: string | null }
export interface Variant { id: number; ordinal: number; n_docs: number; n_occurrences: number; text: string; parts: Part[]; slots: Slot[]; occurrences: Occurrence[] }
export interface ModuleDetail extends ModuleRow { variants: Variant[] }
export interface ModuleSummary { modules: number; shared_modules: number; units: number; chars: number; shared_char_ratio: number; variants: number }
export type BlockModules = Record<string, { module_id: number; title: string; n_docs: number; variant: number; n_variants: number }>;

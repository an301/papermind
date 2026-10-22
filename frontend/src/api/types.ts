/**
 * Wire types. A mirror of the pydantic models in papermind/schemas/*.py; names
 * and fields are identical so a diff between the two reads cleanly. Change
 * both together.
 *
 * Conventions carried over from the server:
 * - datetimes are ISO 8601 strings (`datetime` → string, `date` → 'YYYY-MM-DD');
 * - page indexes are 0-based;
 * - geometry is in PDF points with a top-left origin (PyMuPDF). The reader
 *   converts to pdf.js viewport space; nothing on the server flips y.
 */

/** ISO 8601 datetime, e.g. "2026-10-09T20:38:49.179909Z". */
export type IsoDateTime = string
/** ISO 8601 date, e.g. "2017-06-12". */
export type IsoDate = string

// ---------------------------------------------------------------- tools.py

export type Level = 'beginner' | 'intermediate' | 'expert'
export type Altitude = 'technical' | 'scaffolded' | 'intuition'
export type RetrievalMode = 'hybrid' | 'bm25' | 'dense'

export const LEVELS: readonly Level[] = ['beginner', 'intermediate', 'expert']

export interface ConceptRef {
  concept_id: string
  name: string
}

export interface Gap {
  concept_id: string
  name: string
  /** BFS distance from the asked-about concept (0 = the concept itself). */
  depth: number
  description?: string | null
}

// ---------------------------------------------------------------- paper.py

/** A rectangle on one page, in PDF points, origin top-left. */
export interface Box {
  page: number
  x0: number
  y0: number
  x1: number
  y1: number
}

/** One heading in the paper's outline. */
export interface Section {
  /** e.g. 'sec_3.2' or 'sec_abstract'; unique within the paper. */
  section_id: string
  /** '3.2', 'A.1', or null for unnumbered sections. */
  number?: string | null
  title: string
  /** 1 = top-level section. */
  level: number
  /** 0-based page the heading is on. */
  page: number
  /** Heading top in points from the page top, for scroll-to. */
  y: number
}

export type PaperStatus = 'queued' | 'processing' | 'ready' | 'failed'

/**
 * Ingestion stages, in order. `mapping` is the optional "Finding the ideas it
 * builds on" step from DESIGN.md Appendix A3; the server doesn't emit it yet.
 */
export type IngestStage =
  | 'downloading'
  | 'extracting'
  | 'sectioning'
  | 'chunking'
  | 'mapping'
  | 'indexing'
  | 'done'

/** Everything about a paper except its chunks. */
export interface PaperMeta {
  /** arXiv id without version ('1706.03762') or 'up_<sha12>' for uploads. */
  paper_id: string
  source: 'arxiv' | 'upload'
  arxiv_id?: string | null
  /** Resolved version, e.g. 'v7'. */
  arxiv_version?: string | null
  title: string
  authors: string[]
  abstract?: string | null
  published?: IsoDate | null
  /** Original filename for uploads. */
  filename?: string | null
  num_pages: number
  /** (width, height) in points per page. */
  page_sizes: [number, number][]
  sections: Section[]
  chunk_count: number
  chunker_version: string
  ingested_at: IsoDateTime
  /** DESIGN.md Appendix A3; absent until the server maps concepts at ingest. */
  builds_on?: ConceptRef[]
}

/** Counts behind the progress line, e.g. pages read or passages indexed. */
export interface StageDetail {
  done: number
  total: number
}

/** A row in the library, including in-flight ingestions. */
export interface PaperSummary {
  paper_id: string
  source: 'arxiv' | 'upload'
  title: string
  authors: string[]
  published?: IsoDate | null
  num_pages: number
  status: PaperStatus
  stage?: IngestStage | null
  /** Overall progress, 0..1, only ever moves forward. */
  progress: number
  stage_detail?: StageDetail | null
  error?: string | null
  /** Machine-readable failure reason, see AddPaperErrorCode. */
  error_code?: string | null
  added_at: IsoDateTime
}

// ---------------------------------------------------------------- api.py

export interface ServiceCheck {
  ok: boolean
  detail?: string | null
}

export interface HealthResponse {
  status: 'ok' | 'degraded'
  version: string
  /** redis, neo4j, mcp:retriever, mcp:prereq_graph, mcp:semantic_scholar, anthropic_key */
  checks: Record<string, ServiceCheck>
  /** Every route but /health needs the X-Access-Code header (a public server). */
  access_code_required?: boolean
  /** False on a shared public server: finished arXiv papers can't be removed (a reader's own uploads always can). */
  paper_delete_allowed?: boolean
}

export type AddPaperErrorCode =
  | 'invalid_input'
  | 'not_found'
  | 'withdrawn'
  | 'newer_version'
  | 'already_added'
  | 'arxiv_unreachable'
  | 'not_a_pdf'
  | 'too_large'
  | 'encrypted'
  | 'no_text'
  | 'ingest_failed'

/** Body of {"error": ...} for add-paper failures. */
export interface AddPaperError {
  code: AddPaperErrorCode
  message: string
  latest_version?: string | null
  paper_id?: string | null
}

export type FeedbackReason = 'wrong' | 'unclear' | 'wrong_level' | 'bad_citation' | 'other'

export interface FeedbackRequest {
  message_id: string
  session_id: string
  reason: FeedbackReason
  note?: string
}

export interface AddArxivPaperRequest {
  /** arXiv id or abs/pdf URL, versioned or not. */
  arxiv: string
}

export interface PaperDetail {
  summary: PaperSummary
  /** Present once status == ready. */
  meta?: PaperMeta | null
}

export interface ChunkDetail {
  chunk_id: string
  paper_id: string
  section_id: string
  section_number?: string | null
  section_title: string
  text: string
  page_start: number
  page_end: number
  boxes: Box[]
}

export interface SuggestionsResponse {
  paper_id: string
  level: Level
  questions: string[]
}

export interface UpdateUserRequest {
  display_name?: string | null
  level?: Level | null
  onboarded?: boolean | null
}

export interface AddKnowledgeRequest {
  /** Concept id or name. */
  concept: string
}

export interface OnboardingAreasRequest {
  /** ConceptArea.area_id values the reader says they are comfortable with. */
  areas: string[]
}

export interface ConceptArea {
  area_id: string
  name: string
  description: string
  concept_count: number
}

export interface ConceptDetail {
  concept: ConceptInfo
  prerequisites: ConceptRef[]
  known: boolean
  /** For the given user, prerequisites first. */
  learning_path: Gap[]
}

export interface CreateSessionRequest {
  user_id: string
  paper_id: string
  /** Defaults to the user's profile level. */
  level?: Level | null
}

export interface UpdateSessionRequest {
  scroll?: ScrollPosition | null
  level?: Level | null
}

export interface ChatRequest {
  session_id: string
  /** 1–4000 characters. */
  message: string
  quote?: Quote | null
  /** Override for this turn; also updates the session level. */
  level?: Level | null
}

/** Every non-2xx response: {"error": {"code", "message"}} (papermind/api/errors.py). */
export interface ErrorBody {
  error: { code: string; message: string } & Partial<Omit<AddPaperError, 'code' | 'message'>>
}

// ---------------------------------------------------------------- session.py

/** Text the reader selected in the PDF and asked about. */
export interface Quote {
  text: string
  page?: number | null
}

export interface ScrollPosition {
  page: number
  /** Fraction down the page, 0..1. */
  offset: number
  zoom?: number | null
}

/** One line of the agent's trail, persisted so a reloaded transcript can show it. */
export interface ActivityItem {
  tool: string
  label: string
  summary?: string | null
  ok: boolean
  duration_ms?: number | null
}

export interface ChatMessage {
  message_id: string
  role: 'user' | 'assistant'
  text: string
  level?: Level | null
  quote?: Quote | null
  sources: SourceRef[]
  external: ExternalRef[]
  gaps?: GapsFound | null
  activity: ActivityItem[]
  followups: string[]
  /** Total turn time, shown at the end of the trail summary. */
  latency_ms?: number | null
  error?: string | null
  error_code?: string | null
  created_at: IsoDateTime
}

export interface Session {
  session_id: string
  user_id: string
  paper_id: string
  level: Level
  scroll: ScrollPosition
  messages: ChatMessage[]
  created_at: IsoDateTime
  updated_at: IsoDateTime
}

export interface UserProfile {
  user_id: string
  display_name?: string | null
  level: Level
  onboarded: boolean
  created_at: IsoDateTime
}

export type KnowledgeSource = 'self_reported' | 'inferred' | 'onboarding'

/**
 * DESIGN.md Appendix A9: one piece of evidence with its kind, so the profile
 * can say "You asked about it in …" or "You explained …". Optional until the
 * server sends it; the profile falls back to `evidence` + `source`.
 */
export interface EvidenceItem {
  kind: 'asked' | 'explained' | 'said' | 'onboarding' | 'came_up'
  paper_id?: string | null
  paper_title?: string | null
  section?: string | null
  at: IsoDateTime
  /** The reader's own words, when the evidence quotes them. */
  quote?: string | null
}

export interface KnownConcept {
  concept_id: string
  name: string
  /** ConceptArea.area_id. */
  area?: string | null
  confidence: number
  source: KnowledgeSource
  evidence: string
  updated_at: IsoDateTime
  /** Appendix A9. */
  evidence_items?: EvidenceItem[]
}

export interface ConceptInfo {
  concept_id: string
  name: string
  area?: string | null
  description?: string | null
  aliases: string[]
  /** Direct prerequisite concept_ids. */
  requires: string[]
}

// ---------------------------------------------------------------- events.py

export interface TurnStarted {
  type: 'turn.started'
  turn_id: string
  session_id: string
  level: Level
  user_message_id: string
}

export interface ToolStarted {
  type: 'tool.started'
  call_id: string
  tool: string
  /** Human sentence, e.g. 'Searching the paper for “label smoothing”'. */
  label: string
  /** Validated model-facing args (no injected context). */
  args: Record<string, unknown>
}

export interface ToolRetry {
  type: 'tool.retry'
  call_id: string
  tool: string
  /** Validation error text that was returned to the model. */
  error: string
  attempt: number
}

export interface ToolFinished {
  type: 'tool.finished'
  call_id: string
  tool: string
  ok: boolean
  duration_ms: number
  /** Human sentence, e.g. 'Found 5 passages in §3.2 and §5.4'. */
  summary: string
  detail?: Record<string, unknown> | null
}

export interface GapsFound {
  type: 'gaps'
  concept: ConceptRef | null
  gaps: Gap[]
  /** Prerequisites first, target last. */
  teaching_order: ConceptRef[]
  altitude: Altitude
}

export interface SourceRef {
  /** Marker the writer uses inline, e.g. 'S1'. */
  label: string
  chunk_id: string
  section_number?: string | null
  section_title: string
  page_start: number
  page_end: number
  /** First ~240 characters, for hover previews. */
  snippet: string
  /** The sentence of the chunk the answer relied on most, for bolding in passage cards. */
  cited_sentence?: string | null
}

export interface SourcesReady {
  type: 'sources'
  sources: SourceRef[]
}

export interface AnswerDelta {
  type: 'answer.delta'
  text: string
}

/** A Semantic Scholar paper the writer cited inline as [P1], [P2] … */
export interface ExternalRef {
  label: string
  s2_id: string
  title: string
  authors: string[]
  year?: number | null
  venue?: string | null
  tldr?: string | null
  arxiv_id?: string | null
  url?: string | null
}

export interface AnswerDone {
  type: 'answer.done'
  message_id: string
  /** Full markdown; paper passages cited as [S1], external papers as [P1]. */
  text: string
  /** Source labels actually used in the text. */
  cited: string[]
  external: ExternalRef[]
  /** The final sources, same labels as the 'sources' event, with cited_sentence filled in. */
  sources: SourceRef[]
  /** Which writer produced the text, e.g. 'claude:claude-opus-5-5'. */
  writer: string
}

export interface KnowledgeUpdated {
  type: 'knowledge.updated'
  added: ConceptRef[]
  removed: ConceptRef[]
  /** Self-reported concepts the reader now seems unsure of: the UI asks, the system never removes them. */
  ask: ConceptRef[]
}

/** Up to two follow-up questions grounded in this answer and its gaps (after answer.done). */
export interface Followups {
  type: 'followups'
  message_id: string
  questions: string[]
}

export interface UsageReport {
  type: 'usage'
  model_calls: number
  input_tokens: number
  output_tokens: number
  cache_read_input_tokens: number
  cache_creation_input_tokens: number
  cost_usd: number
  latency_ms: number
  /** Time to first answer token. */
  ttft_ms?: number | null
  tool_calls: number
  tool_retries: number
}

export type ErrorCode =
  | 'no_api_key'
  | 'auth_failed'
  | 'rate_limited'
  | 'overloaded'
  | 'api_error'
  | 'refusal'
  | 'paper_not_ready'
  | 'session_not_found'
  | 'tool_failed'
  | 'internal'

export interface ErrorEvent {
  type: 'error'
  code: ErrorCode
  /** Plain-language explanation and what to do about it. */
  message: string
  retryable: boolean
}

export interface Done {
  type: 'done'
}

/**
 * Typed events streamed over one SSE channel per turn. Order within a turn:
 *
 *     turn.started
 *     (tool.started | tool.retry | tool.finished | gaps)*
 *     sources
 *     answer.delta*
 *     answer.done
 *     knowledge.updated?
 *     followups?
 *     usage
 *     done
 *
 * An `error` event may replace anything after `turn.started`; `done` always ends the stream.
 */
export type ChatEvent =
  | TurnStarted
  | ToolStarted
  | ToolRetry
  | ToolFinished
  | GapsFound
  | SourcesReady
  | AnswerDelta
  | AnswerDone
  | KnowledgeUpdated
  | Followups
  | UsageReport
  | ErrorEvent
  | Done

export type ChatEventType = ChatEvent['type']

export const CHAT_EVENT_TYPES: readonly ChatEventType[] = [
  'turn.started',
  'tool.started',
  'tool.retry',
  'tool.finished',
  'gaps',
  'sources',
  'answer.delta',
  'answer.done',
  'knowledge.updated',
  'followups',
  'usage',
  'error',
  'done',
]

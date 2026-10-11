/**
 * TanStack Query hooks for every server resource. Components read server
 * state only through these; the chat stream is the one exception (it goes
 * through `streamChat` in ./sse.ts, never through the query cache).
 */
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import { bootstrapUser, forgetStoredUser } from '../lib/user'
import { api, isApiError, uploadPdf } from './client'
import { qk } from './keys'
import { queryClient as sharedClient } from './queryClient'
import type {
  ChunkDetail,
  ConceptArea,
  ConceptDetail,
  ConceptInfo,
  CreateSessionRequest,
  HealthResponse,
  KnownConcept,
  Level,
  PaperDetail,
  PaperSummary,
  Session,
  SuggestionsResponse,
  UpdateSessionRequest,
  UpdateUserRequest,
  UserProfile,
} from './types'

const enc = encodeURIComponent

// ---------------------------------------------------------------- health

/** GET /health. Re-checked every 30 s so the API-key bar and the offline state stay honest. */
export function useHealth() {
  return useQuery({
    queryKey: qk.health,
    queryFn: ({ signal }) => api<HealthResponse>('/health', { signal }),
    refetchInterval: 30_000,
    staleTime: 15_000,
    retry: false,
  })
}

export const isAnthropicKeyMissing = (health: HealthResponse | undefined): boolean =>
  health?.checks.anthropic_key?.ok === false

/** False on a shared public server, where finished papers can't be removed (unknown counts as allowed). */
export const canRemovePapers = (health: HealthResponse | undefined): boolean => health?.paper_delete_allowed !== false

// ---------------------------------------------------------------- papers

export const isInFlight = (p: Pick<PaperSummary, 'status'>): boolean =>
  p.status === 'queued' || p.status === 'processing'

/** How often the library polls while something is being prepared. */
export const INGEST_POLL_MS = 1500

/** Poll the library only while something is being prepared. */
export const papersPollInterval = (papers: readonly PaperSummary[] | undefined): number | false =>
  papers?.some(isInFlight) ? INGEST_POLL_MS : false

/** GET /papers, polled while any paper is queued or processing. */
export function usePapers(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: qk.papers,
    queryFn: ({ signal }) => api<PaperSummary[]>('/papers', { signal }),
    enabled: options.enabled ?? true,
    refetchInterval: (query) => papersPollInterval(query.state.data),
    staleTime: 5_000,
  })
}

/** GET /papers/{id}; polls while the paper is still being prepared. */
export function usePaper(paperId: string | undefined) {
  return useQuery({
    queryKey: qk.paper(paperId ?? ''),
    queryFn: ({ signal }) => api<PaperDetail>(`/papers/${enc(paperId!)}`, { signal }),
    enabled: !!paperId,
    refetchInterval: (query) => (query.state.data && isInFlight(query.state.data.summary) ? INGEST_POLL_MS : false),
    staleTime: 30_000,
  })
}

/** Put a summary at the top of the cached library list (or replace its row). */
export function upsertPaperInCache(client: QueryClient, summary: PaperSummary): void {
  client.setQueryData<PaperSummary[]>(qk.papers, (list = []) => [
    summary,
    ...list.filter((p) => p.paper_id !== summary.paper_id),
  ])
}

export function removePaperFromCache(client: QueryClient, paperId: string): PaperSummary | undefined {
  let removed: PaperSummary | undefined
  client.setQueryData<PaperSummary[]>(qk.papers, (list) => {
    if (!list) return list
    removed = list.find((p) => p.paper_id === paperId)
    return list.filter((p) => p.paper_id !== paperId)
  })
  return removed
}

/** POST /papers {arxiv}. Resolves with the summary; status 'ready' means it was already in the library. */
export function useAddArxivPaper() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (arxiv: string) => api<PaperSummary>('/papers', { method: 'POST', body: { arxiv } }),
    onSuccess: (summary) => {
      upsertPaperInCache(client, summary)
      void client.invalidateQueries({ queryKey: qk.papers, exact: true })
    },
  })
}

export interface UploadVariables {
  file: File
  onProgress?: (fraction: number) => void
  signal?: AbortSignal
}

/** POST /papers/upload (multipart) with byte progress. */
export function useUploadPaper() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ file, onProgress, signal }: UploadVariables) => uploadPdf(file, onProgress, signal),
    onSuccess: ({ summary }) => {
      upsertPaperInCache(client, summary)
      void client.invalidateQueries({ queryKey: qk.papers, exact: true })
    },
  })
}

/** DELETE /papers/{id}. Also cancels an in-flight ingestion. */
export function useDeletePaper() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (paperId: string) => deletePaper(paperId),
    onSettled: () => client.invalidateQueries({ queryKey: qk.papers, exact: true }),
  })
}

/** Plain function form, for deferred deletes that must outlive the component (Undo toasts). */
export const deletePaper = (paperId: string, options: { keepalive?: boolean } = {}): Promise<void> =>
  api<void>(`/papers/${enc(paperId)}`, { method: 'DELETE', keepalive: options.keepalive })

/** GET /papers/{id}/chunks/{chunk_id}. Chunks never change for a given paper, so they are cached forever. */
export const chunkQuery = (paperId: string, chunkId: string) => ({
  queryKey: qk.chunk(paperId, chunkId),
  queryFn: ({ signal }: { signal: AbortSignal }) =>
    api<ChunkDetail>(`/papers/${enc(paperId)}/chunks/${enc(chunkId)}`, { signal }),
  staleTime: Infinity,
  gcTime: 30 * 60_000,
})

export function useChunk(paperId: string | undefined, chunkId: string | undefined) {
  return useQuery({ ...chunkQuery(paperId ?? '', chunkId ?? ''), enabled: !!paperId && !!chunkId })
}

/** Fetch (or read from cache) a chunk's boxes outside React. */
export const fetchChunk = (paperId: string, chunkId: string): Promise<ChunkDetail> =>
  sharedClient.fetchQuery(chunkQuery(paperId, chunkId))

/** GET /papers/{id}/suggestions?level= : three starter questions for the empty panel. */
export function useSuggestions(paperId: string | undefined, level: Level, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: qk.suggestions(paperId ?? '', level),
    queryFn: ({ signal }) =>
      api<SuggestionsResponse>(`/papers/${enc(paperId!)}/suggestions`, { query: { level }, signal }),
    enabled: !!paperId && (options.enabled ?? true),
    staleTime: Infinity,
    retry: false,
  })
}

// ---------------------------------------------------------------- user

/** The current reader's profile (created on first visit; see src/lib/user.ts). */
export function useUser() {
  return useQuery({
    queryKey: qk.user,
    queryFn: bootstrapUser,
    staleTime: Infinity,
  })
}

/** The current reader's id, or undefined while the profile loads. */
export const useUserId = (): string | undefined => useUser().data?.user_id

/**
 * A user-scoped call failed with user_not_found: the server lost the profile.
 * Drop the stored id and bootstrap a new user; the dependent queries refetch.
 */
async function recoverFromLostUser<T>(call: () => Promise<T>): Promise<T> {
  try {
    return await call()
  } catch (e) {
    if (isApiError(e, 'user_not_found')) {
      forgetStoredUser()
      await sharedClient.invalidateQueries({ queryKey: qk.user })
    }
    throw e
  }
}

/** PATCH /users/{id}: level, onboarded, display_name. */
export function useUpdateUser() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (changes: UpdateUserRequest) => {
      const user = client.getQueryData<UserProfile>(qk.user) ?? (await bootstrapUser())
      return recoverFromLostUser(() =>
        api<UserProfile>(`/users/${enc(user.user_id)}`, { method: 'PATCH', body: changes }),
      )
    },
    onMutate: async (changes) => {
      await client.cancelQueries({ queryKey: qk.user })
      const previous = client.getQueryData<UserProfile>(qk.user)
      if (previous) {
        const next: UserProfile = { ...previous }
        if (changes.level) next.level = changes.level
        if (typeof changes.onboarded === 'boolean') next.onboarded = changes.onboarded
        if (changes.display_name !== undefined) next.display_name = changes.display_name
        client.setQueryData(qk.user, next)
      }
      return { previous }
    },
    onError: (_e, _v, context) => {
      if (context?.previous) client.setQueryData(qk.user, context.previous)
    },
    onSuccess: (user) => client.setQueryData(qk.user, user),
  })
}

// ---------------------------------------------------------------- knowledge

/** GET /users/{id}/knowledge: every KNOWS edge, including ones below the known threshold. */
export function useKnowledge() {
  const userId = useUserId()
  return useQuery({
    queryKey: qk.knowledge(userId ?? ''),
    queryFn: ({ signal }) =>
      recoverFromLostUser(() => api<KnownConcept[]>(`/users/${enc(userId!)}/knowledge`, { signal })),
    enabled: !!userId,
    staleTime: 30_000,
  })
}

function useKnowledgeKey() {
  const userId = useUserId()
  return { userId, key: qk.knowledge(userId ?? '') }
}

/** POST /users/{id}/knowledge {concept}: "I know this" (self-reported). */
export function useAddKnowledge() {
  const client = useQueryClient()
  const { userId, key } = useKnowledgeKey()
  return useMutation({
    mutationFn: (concept: string) =>
      recoverFromLostUser(() =>
        api<KnownConcept>(`/users/${enc(userId!)}/knowledge`, { method: 'POST', body: { concept } }),
      ),
    onSuccess: (edge) => {
      client.setQueryData<KnownConcept[]>(key, (list = []) =>
        [...list.filter((k) => k.concept_id !== edge.concept_id), edge].sort((a, b) =>
          a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }),
        ),
      )
    },
  })
}

/** DELETE /users/{id}/knowledge/{concept_id}: "Forget this", and Undo for an added concept. */
export function useForgetKnowledge() {
  const client = useQueryClient()
  const { userId, key } = useKnowledgeKey()
  return useMutation({
    mutationFn: (conceptId: string) =>
      recoverFromLostUser(() =>
        api<void>(`/users/${enc(userId!)}/knowledge/${enc(conceptId)}`, { method: 'DELETE' }),
      ),
    onSuccess: (_r, conceptId) => {
      client.setQueryData<KnownConcept[]>(key, (list) => list?.filter((k) => k.concept_id !== conceptId))
    },
  })
}

/** POST /users/{id}/knowledge/areas {areas}: onboarding and the empty profile's area checkboxes. */
export function useAddOnboardingAreas() {
  const client = useQueryClient()
  const { userId, key } = useKnowledgeKey()
  return useMutation({
    mutationFn: (areas: string[]) =>
      recoverFromLostUser(() =>
        api<KnownConcept[]>(`/users/${enc(userId!)}/knowledge/areas`, { method: 'POST', body: { areas } }),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: key }),
  })
}

/**
 * "Reset what you know" (Settings, §5.23): forget every concept. There is no
 * bulk endpoint, so this deletes them a few at a time and reports how many
 * could not be removed (0 = all gone). The list refetches afterwards either way.
 */
export function useResetKnowledge() {
  const client = useQueryClient()
  const { userId, key } = useKnowledgeKey()
  return useMutation({
    mutationFn: async (conceptIds: readonly string[]) => {
      const queue = [...conceptIds]
      let failed = 0
      const worker = async () => {
        for (let id = queue.shift(); id !== undefined; id = queue.shift()) {
          try {
            await recoverFromLostUser(() => api<void>(`/users/${enc(userId!)}/knowledge/${enc(id!)}`, { method: 'DELETE' }))
          } catch {
            failed++
          }
        }
      }
      await Promise.all(Array.from({ length: Math.min(6, queue.length) }, worker))
      return { failed }
    },
    onSettled: () => client.invalidateQueries({ queryKey: key }),
  })
}

// ---------------------------------------------------------------- concepts

/** GET /concepts/areas: the 12 areas from data/concepts/areas.json, in file order. */
export function useConceptAreas() {
  return useQuery({
    queryKey: qk.areas,
    queryFn: ({ signal }) => api<ConceptArea[]>('/concepts/areas', { signal }),
    staleTime: Infinity,
  })
}

/** GET /concepts?q=&limit= ; keeps the previous results on screen while the next query loads. */
export function useConceptSearch(q: string, limit = 8) {
  const query = q.trim()
  return useQuery({
    queryKey: qk.conceptSearch(`${query}|${limit}`),
    queryFn: ({ signal }) => api<ConceptInfo[]>('/concepts', { query: { q: query, limit }, signal }),
    enabled: query.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
  })
}

/** GET /concepts/{id}?user_id= : prerequisites, and whether this reader knows it. */
export function useConcept(conceptId: string | undefined) {
  const userId = useUserId() ?? null
  return useQuery({
    queryKey: qk.concept(conceptId ?? '', userId),
    queryFn: ({ signal }) =>
      api<ConceptDetail>(`/concepts/${enc(conceptId!)}`, { query: { user_id: userId }, signal }),
    enabled: !!conceptId,
  })
}

// ---------------------------------------------------------------- sessions

/** POST /sessions. The caller remembers the id per paper (pm:session:{paperId}). */
export function useCreateSession() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateSessionRequest) => api<Session>('/sessions', { method: 'POST', body }),
    onSuccess: (session) => client.setQueryData(qk.session(session.session_id), session),
  })
}

/** GET /sessions/{id}. A 404 means Redis expired it (24 h); create a new one. */
export function useSession(sessionId: string | undefined) {
  return useQuery({
    queryKey: qk.session(sessionId ?? ''),
    queryFn: ({ signal }) => api<Session>(`/sessions/${enc(sessionId!)}`, { signal }),
    enabled: !!sessionId,
    staleTime: Infinity,
    retry: false,
  })
}

/** PATCH /sessions/{id} {scroll?, level?}. */
export function useUpdateSession() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ sessionId, ...body }: UpdateSessionRequest & { sessionId: string }) =>
      api<Session>(`/sessions/${enc(sessionId)}`, { method: 'PATCH', body }),
    onSuccess: (session) => client.setQueryData(qk.session(session.session_id), session),
  })
}

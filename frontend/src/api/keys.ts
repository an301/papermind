import type { Level } from './types'

/** Query keys in one place, so invalidation never misses a spelling. */
export const qk = {
  health: ['health'] as const,
  papers: ['papers'] as const,
  paper: (paperId: string) => ['papers', 'detail', paperId] as const,
  chunk: (paperId: string, chunkId: string) => ['chunks', paperId, chunkId] as const,
  suggestions: (paperId: string, level: Level) => ['suggestions', paperId, level] as const,
  user: ['user'] as const,
  knowledge: (userId: string) => ['knowledge', userId] as const,
  areas: ['concepts', 'areas'] as const,
  conceptSearch: (q: string) => ['concepts', 'search', q] as const,
  concept: (conceptId: string, userId: string | null) => ['concepts', 'detail', conceptId, userId] as const,
  session: (sessionId: string) => ['sessions', sessionId] as const,
}

import { QueryClient } from '@tanstack/react-query'
import { ApiError, NetworkError } from './client'

/**
 * The app's single QueryClient. Exported so code outside React (the reader
 * store's requestScrollToChunk, the user bootstrap) shares the same cache.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: false,
      // Retry once, quickly, and only what can succeed on a second try (the network, a 5xx).
      // A longer back-off would keep a skeleton on screen where the error belongs (§5.5).
      retry: (count, error) => {
        if (error instanceof ApiError) return error.status >= 500 && count < 1
        if (error instanceof NetworkError) return count < 1
        return false
      },
      retryDelay: 500,
    },
    mutations: { retry: false },
  },
})

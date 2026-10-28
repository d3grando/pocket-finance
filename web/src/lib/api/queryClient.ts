// Shared QueryClient: refetch on focus (ARCH §8 «перезапрос при фокусе»),
// no retries for mutations (idempotent retries are explicit, with the same key).
// networkMode 'always': without a network a request fails at once with NetworkError instead of
// pausing — no hidden offline queue of writes that would run by themselves later (DESIGN §8.4
// «Очереди офлайн-записей в MVP нет»), and no page stuck in «pending» forever.
import { QueryCache, QueryClient } from '@tanstack/react-query'
import { isNetworkError, isUnauthorized } from './client'

export const queryDefaults = {
  queries: {
    staleTime: 30_000,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    networkMode: 'always' as const,
    retry: (count: number, err: Error) => !isUnauthorized(err) && !isNetworkError(err) && count < 1,
  },
  mutations: { retry: 0, networkMode: 'always' as const },
}

export function createQueryClient() {
  return new QueryClient({ queryCache: new QueryCache(), defaultOptions: queryDefaults })
}

export const queryClient = createQueryClient()

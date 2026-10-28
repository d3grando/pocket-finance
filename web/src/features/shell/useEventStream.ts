// Starts the SSE subscription (lib/api/sse) while a session exists.
import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { startEventStream } from '@/lib/api/sse'

export function useEventStream(enabled: boolean) {
  const qc = useQueryClient()
  useEffect(() => {
    if (!enabled) return
    return startEventStream(qc)
  }, [enabled, qc])
}

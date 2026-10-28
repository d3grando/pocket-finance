// Debts data hooks over lib/api (DEBTS.md §7). Operations carry an Idempotency-Key
// bound to the body: a retry after a lost answer never records the loan twice.
import { useRef } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  actionsApi, ApiError, debtsApi, newIdempotencyKey, transactionsApi,
  type ActionOut, type DebtOpCreate, type DebtOpResult, type DebtOut, type DebtPatch, type Entity, type TransactionOut, type UndoResult,
} from '@/lib/api'
import { invalidateEntities, keepAttemptKey } from '@/features/operations/api'
import { latestLiveAction } from './lib'

export { useDebt, useDebts } from '@/lib/api/hooks'

/** A server without the debts routes (404 / 501) — hide debt UI instead of an error. */
export function debtsUnsupported(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 404 || e.status === 501)
}

const CHANGED = ['transactions', 'accounts', 'stats', 'debts'] as const

/** POST /debts/operations with a stable key per identical body (kept after a lost answer / 5xx). */
export function useDebtOperation() {
  const qc = useQueryClient()
  const ref = useRef<{ body: string; key: string } | null>(null)
  return useMutation({
    mutationFn: (body: DebtOpCreate): Promise<DebtOpResult> => {
      const json = JSON.stringify(body)
      if (!ref.current || ref.current.body !== json) ref.current = { body: json, key: newIdempotencyKey() }
      return debtsApi.operation(body, ref.current.key)
    },
    onSuccess: () => {
      ref.current = null
      invalidateEntities(qc, [...CHANGED])
    },
    onError: (e) => {
      if (!keepAttemptKey(e)) ref.current = null
    },
  })
}

export function usePatchDebt() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: DebtPatch }): Promise<DebtOut> => debtsApi.patch(id, patch),
    onSuccess: () => invalidateEntities(qc, ['debts']),
    onError: (e) => {
      if (e instanceof ApiError && e.code === 'version_conflict') invalidateEntities(qc, ['debts'])
    },
  })
}

export function useDeleteDebt() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => debtsApi.remove(id),
    onSuccess: () => invalidateEntities(qc, ['debts']),
  })
}

/** «Отменить последнюю» in the card of a person. A server that sends `DebtDetailOut.last_action_id` already
 *  knows the newest action still in effect on the latest operation — that one is undone. Older servers:
 *  undo the action that recorded the latest operation; when the operation was edited afterwards (409
 *  undo_conflict) or that action is already undone (the operation was restored), undo the newest action still
 *  in effect on it instead — from its audit history (`GET /transactions/{id}/history`) and the action log.
 *  DEBTS.md §15. */
export function useUndoLastDebtOp() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (tx: Pick<TransactionOut, 'id' | 'created_action_id'> & { last_action_id?: string | null }): Promise<UndoResult> => {
      if (tx.last_action_id) return actionsApi.undo(tx.last_action_id, newIdempotencyKey())
      const created = tx.created_action_id
      let first: unknown = null
      if (created) {
        try {
          const r = await actionsApi.undo(created, newIdempotencyKey())
          if (r.status === 'undone') return r
        } catch (e) {
          if (!(e instanceof ApiError && e.code === 'undo_conflict')) throw e
          first = e
        }
      }
      const [audit, actions] = await Promise.all([
        transactionsApi.history(tx.id),
        actionsApi.list({ limit: 200 }).catch((): ActionOut[] => []),
      ])
      const id = latestLiveAction(audit, actions, created)
      if (!id) {
        if (first) throw first
        throw new ApiError(409, 'nothing_to_undo', 'Нечего отменять')
      }
      return actionsApi.undo(id, newIdempotencyKey())
    },
    onSuccess: (r) => invalidateEntities(qc, [...CHANGED, ...((r.changed ?? []) as Entity[])]),
    onError: () => invalidateEntities(qc, ['debts']),
  })
}

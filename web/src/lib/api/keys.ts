// TanStack Query keys (ARCH §12) + SSE entity → keys mapping.
import type { Entity } from './types'

export const qk = {
  me: ['me'] as const,
  settings: ['settings'] as const,
  serverInfo: ['settings', 'server'] as const,
  transactions: (filter?: unknown) => ['transactions', filter ?? {}] as const,
  transaction: (id: number) => ['transaction', id] as const,
  needsAccount: ['needs-account'] as const,
  accounts: ['accounts'] as const,
  balances: ['balances'] as const,
  myAccounts: ['my-accounts'] as const,
  categories: ['categories'] as const,
  merchants: ['merchants'] as const,
  stats: (kind: string, params?: unknown) => ['stats', kind, params ?? {}] as const,
  rates: ['rates'] as const,
  recurring: ['recurring'] as const,
  actions: ['actions'] as const,
  devices: ['devices'] as const,
  passkeys: ['passkeys'] as const,
  sessions: ['sessions'] as const,
  /** GET /debts (include_settled → separate cache entry) */
  debts: (includeSettled = false) => ['debts', { settled: includeSettled }] as const,
  debt: (id: number) => ['debt', id] as const,
}

/** Query-key prefixes to invalidate for each SSE entity. Operations embed the category and account
 * name/icon (TransactionOut.category, .account, .title), so renaming either refetches the lists too.
 * Debt balances are balances of the person's service account, moved by ordinary operations: any
 * change of operations refetches the debts too (DEBTS.md §10). */
export const entityKeys: Record<Entity, readonly (readonly unknown[])[]> = {
  transactions: [['transactions'], ['transaction'], ['needs-account'], ['actions'], ['stats'], ['balances'], ['accounts'], ['debts'], ['debt']],
  accounts: [['accounts'], ['balances'], ['my-accounts'], ['transactions'], ['transaction']],
  categories: [['categories'], ['stats'], ['transactions'], ['transaction']],
  merchants: [['merchants'], ['transactions'], ['transaction']],
  settings: [['settings']],
  recurring: [['recurring']],
  stats: [['stats']],
  debts: [['debts'], ['debt'], ['transactions'], ['transaction']],
}

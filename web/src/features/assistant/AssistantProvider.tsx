// Assistant state: sending messages (Idempotency-Key per attempt), the stack of result
// cards and every card button action from API.md §9.1. «Сохранено» is shown only from a 2xx answer.
// Cards follow the server: a 409 (changed on another device) or an SSE change re-reads the operation
// and redraws the card with its real state.
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ApiError, NetworkError, newIdempotencyKey } from '@/lib/api/client'
import { actionsApi, assistantApi, transactionsApi } from '@/lib/api/endpoints'
import { keysForEntities, onServerChange } from '@/lib/api/sse'
import type { AssistantMessageIn, Button, Card, Entity, TransactionOut, TransactionPatch, UndoResult } from '@/lib/api/types'
import { connection } from '@/lib/connection'
import { debtRealAccount } from '@/features/debts/lib'
import { DEFAULT_TZ } from '@/lib/format'
import {
  AUTO_RETRY_KINDS, CREATING_KINDS, EPHEMERAL, KeyBook, RETRY_IN_PROGRESS, accountButtonForDigit, cardActionIds, cardFromTransaction,
  cardTransactions, editButton, keepsInput, localErrorCard, txCardTitle, undoButton, undoneCard, withDebtOf, withTransaction,
  withoutTransaction, type CardHotkey, type LocalErrorKind,
} from './logic'

export interface Origin { text: string; key: string; context: AssistantMessageIn['context'] }

export interface CardEntry {
  uid: string
  card: Card
  /** action ids «Отменить» reverts, oldest first */
  chain: string[]
  busy: string | null
  error: string | null
  editing: boolean
  /** this card is a local «нет связи» / «нет ответа» card (WifiOff marker) */
  network: boolean
  /** a card built by the client after a failed request (null = the server's answer) */
  local: LocalErrorKind | null
  /** the request that produced the card ([Повторить] resends it) */
  origin: Origin | null
  previewKey: string | null
  note: string | null
  createdAt: number
  touchedAt: number
  dismissed: boolean
  /** the card started by creating operations («Отменить» = remove them) */
  creates: boolean
  /** how many of `chain` came with the card itself (the rest are edits made on it) */
  rootActions: number
  /** the operation was changed elsewhere after the card appeared (its creation can't be undone) */
  stale: boolean
  /** actions «Вернуть» reverts (more than one after removing several operations) */
  redoChain: string[]
  autoRetries: number
}

export type SendOutcome = 'cleared' | 'keep' | 'ignored'

interface SendOpts {
  /** the card this send replaces ([Повторить] on it, auto-retry) */
  replace?: string
}

interface Ctx {
  entries: CardEntry[]
  pendingText: string | null
  announce: string
  send: (text: string, context?: AssistantMessageIn['context']) => Promise<SendOutcome>
  act: (uid: string, button: Button) => Promise<void>
  hotkey: (cmd: CardHotkey) => boolean
  dismiss: (uid: string) => void
  setEditing: (uid: string, on: boolean) => void
  applyEdit: (uid: string, txId: number, patch: TransactionPatch) => Promise<boolean>
  removeTx: (uid: string, tx: TransactionOut) => Promise<boolean>
  setCategory: (uid: string, txId: number, categoryId: number, categoryName?: string) => Promise<boolean>
  clear: () => void
  /** subscribe to «text was sent successfully» (the field clears if it still holds this text) */
  onSent: (fn: (text: string, outcome: SendOutcome) => void) => () => void
}

const AssistantContext = createContext<Ctx | null>(null)

export function useAssistant(): Ctx {
  const c = useContext(AssistantContext)
  if (!c) throw new Error('useAssistant outside <AssistantProvider>')
  return c
}

interface ProviderProps {
  children: React.ReactNode
  tz?: string
  onManual?: (prefill: Record<string, string>) => void
  onOpen?: (route: string) => void
  onAuthExpired?: (text: string) => void
}

let uidSeq = 0
const uid = () => `c${Date.now().toString(36)}${(uidSeq++).toString(36)}`

const now = () => Date.now()

function entry(card: Card, origin: Origin | null, extra: Partial<CardEntry> = {}): CardEntry {
  const t = now()
  const chain = cardActionIds(card)
  return {
    uid: uid(), card, chain, busy: null, error: null, editing: false, network: false, local: null,
    origin, previewKey: null, note: card.kind === 'created' ? card.note : null, createdAt: t, touchedAt: t, dismissed: false,
    creates: CREATING_KINDS.has(card.kind), rootActions: chain.length, stale: false, redoChain: [], autoRetries: 0,
    ...extra,
  }
}

/** auto-[Повторить] for «нет связи» cards: at most this many times, within 5 min (DESIGN §10.10.7) */
const MAX_AUTO_RETRIES = 3
const AUTO_RETRY_WINDOW = 5 * 60_000
const IN_PROGRESS_FOLLOW_UP = 10_000
const CHANGED_ELSEWHERE = 'Запись изменили на другом устройстве — показал актуальную'
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const isConflict = (e: unknown) => e instanceof ApiError && (e.status === 409 || e.status === 404) && e.code !== 'idempotency_in_progress' && e.code !== 'idempotency_mismatch'

export function AssistantProvider({ children, tz = DEFAULT_TZ, onManual, onOpen, onAuthExpired }: ProviderProps) {
  const qc = useQueryClient()
  const [entries, setEntries] = useState<CardEntry[]>([])
  const [pendingText, setPendingText] = useState<string | null>(null)
  const [announce, setAnnounce] = useState('')
  const keys = useRef(new KeyBook())
  const opKeys = useRef(new Map<string, string>())
  const sentListeners = useRef(new Set<(t: string, o: SendOutcome) => void>())
  const entriesRef = useRef(entries)
  const handlers = useRef({ onManual, onOpen, onAuthExpired })
  const sendRef = useRef<(text: string, context?: AssistantMessageIn['context'], opts?: SendOpts) => Promise<SendOutcome>>(null!)
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>())
  useEffect(() => {
    const set = timers.current
    return () => { set.forEach(clearTimeout); set.clear() }
  }, [])
  useLayoutEffect(() => {
    entriesRef.current = entries
    handlers.current = { onManual, onOpen, onAuthExpired }
  })

  const opKey = (id: string) => {
    let k = opKeys.current.get(id)
    if (!k) { k = newIdempotencyKey(); opKeys.current.set(id, k) }
    return k
  }

  const invalidate = useCallback((changed: Entity[] | string[]) => {
    const ents = (changed.length ? changed : ['transactions', 'stats', 'accounts']) as Entity[]
    for (const key of keysForEntities(ents)) void qc.invalidateQueries({ queryKey: key })
  }, [qc])

  const patchEntry = useCallback((id: string, fn: (e: CardEntry) => Partial<CardEntry> | null) => {
    setEntries((list) => list.map((e) => {
      if (e.uid !== id) return e
      const p = fn(e)
      return p ? { ...e, ...p, touchedAt: now() } : e
    }))
  }, [])

  const send = useCallback(async (raw: string, context: AssistantMessageIn['context'] = null, opts: SendOpts = {}): Promise<SendOutcome> => {
    const text = raw.trim()
    if (!text) return 'ignored'
    const { key, busy } = keys.current.acquire(text)
    if (busy) return 'ignored'
    const origin: Origin = { text, key, context }
    setPendingText(text)
    const finish = (o: SendOutcome) => { sentListeners.current.forEach((l) => l(text, o)); return o }
    // every earlier card of this attempt goes away: the card [Повторить] was pressed on, cards of the
    // same key (5xx / in progress / no answer) and «нет связи» cards for the same text
    const sameAttempt = (x: CardEntry) =>
      x.uid === opts.replace || (x.local !== null && x.origin !== null &&
        (x.origin.key === key || (x.network && KeyBook.norm(x.origin.text) === KeyBook.norm(text))))
    try {
      let attempt = 0
      for (;;) {
        try {
          const res = await assistantApi.send({ text, context: context ?? null }, key)
          keys.current.done(text)
          invalidate(res.changed)
          const fresh = res.cards.map((c) => entry(c, origin))
          setEntries((list) => [...fresh.reverse(), ...list.filter((e) => !sameAttempt(e))])
          const first = res.cards[0]
          if (first) setAnnounce(first.kind === 'created' ? `Записано: ${first.title}` : first.title)
          return finish(keepsInput(res.cards) ? 'keep' : 'cleared')
        } catch (e) {
          if (e instanceof ApiError && e.code === 'idempotency_in_progress' && attempt < RETRY_IN_PROGRESS.length) {
            await sleep(RETRY_IN_PROGRESS[attempt++])
            continue
          }
          throw e
        }
      }
    } catch (e) {
      let kind: LocalErrorKind
      let message: string | undefined
      if (e instanceof NetworkError) {
        keys.current.failed(text)
        // offline browser: the request did not leave. Otherwise (dropped answer, timeout) the server may
        // have saved it — say so honestly; a repeat with the same key settles it without a duplicate
        kind = typeof navigator !== 'undefined' && navigator.onLine === false ? 'network' : 'unknown'
      } else if (e instanceof ApiError && e.status === 401) {
        keys.current.failed(text)
        kind = 'auth'
        handlers.current.onAuthExpired?.(text)
      } else if (e instanceof ApiError && e.status === 429) {
        keys.current.failed(text)
        kind = 'rate'
        message = e.retryAfter ? `Подождите ${Math.ceil(e.retryAfter)} с и повторите.` : undefined
      } else if (e instanceof ApiError && e.code === 'idempotency_in_progress') {
        keys.current.failed(text)
        kind = 'in_progress'
      } else if (e instanceof ApiError && e.status >= 500) {
        keys.current.failed(text)
        kind = 'server'
        message = e.message && e.code !== 'internal' ? e.message : undefined
      } else {
        keys.current.done(text)
        kind = 'other'
        message = e instanceof ApiError ? e.message : undefined
      }
      const card = localErrorCard(kind, message)
      const network = kind === 'network' || kind === 'unknown'
      const fresh = entry(card, origin, { network, local: kind })
      setEntries((list) => {
        // an auto/[Повторить] attempt keeps the first failure's time and the auto-retry count
        const prev = list.find((x) => sameAttempt(x) && x.local !== null && AUTO_RETRY_KINDS.has(x.local))
        const next = prev ? { ...fresh, createdAt: prev.createdAt, autoRetries: prev.autoRetries } : fresh
        return [next, ...list.filter((x) => !sameAttempt(x))]
      })
      setAnnounce(card.title)
      if (kind === 'in_progress') {
        // the first attempt usually finishes a bit later: ask once more by itself
        const t = setTimeout(() => {
          timers.current.delete(t)
          const cur = entriesRef.current.find((x) => x.uid === fresh.uid)
          if (!cur || cur.dismissed || !cur.origin || cur.autoRetries >= MAX_AUTO_RETRIES) return
          patchEntry(cur.uid, (x) => ({ autoRetries: x.autoRetries + 1 }))
          void sendRef.current(cur.origin.text, cur.origin.context, { replace: cur.uid })
        }, IN_PROGRESS_FOLLOW_UP)
        timers.current.add(t)
      }
      return finish('keep')
    } finally {
      setPendingText(null)
    }
  }, [invalidate, patchEntry])
  useLayoutEffect(() => { sendRef.current = send })

  const failInline = (id: string, e: unknown, fallback: string) => {
    // offline: the request never left; online without an answer the outcome is unknown — a repeat uses
    // the same Idempotency-Key, so it can never apply twice
    const msg = e instanceof NetworkError
      ? (typeof navigator !== 'undefined' && navigator.onLine === false ? 'Нет связи — ничего не изменено' : 'Нет ответа сервера — повторите, дубля не будет')
      : e instanceof ApiError ? e.message : fallback
    patchEntry(id, () => ({ busy: null, error: msg }))
    setAnnounce(msg)
  }

  /** Re-read the card's operations and redraw it with the server state (after a 409 / SSE change). */
  const resync = useCallback(async (id: string, onlyIfChanged = false): Promise<boolean> => {
    const e = entriesRef.current.find((x) => x.uid === id)
    if (!e) return false
    const txs = cardTransactions(e.card)
    if (!txs.length) return false
    const got = await Promise.all(txs.map((t) => transactionsApi.get(t.id).then(
      (tx) => ({ old: t, tx, gone: false }),
      (err) => ({ old: t, tx: null, gone: err instanceof ApiError && err.status === 404 }),
    )))
    const changed = got.some((g) => g.gone || (g.tx && g.tx.version !== g.old.version))
    if (onlyIfChanged && !changed) return false
    patchEntry(id, (cur) => {
      if (onlyIfChanged && cur.busy) return null // a mutation of this card is in flight: its answer wins
      let card: Card | null = cur.card
      for (const g of got) {
        if (!card) break
        if (g.tx) card = withTransaction(card, g.tx, tz)
        else if (g.gone) card = withoutTransaction(card, g.old.id)
      }
      if (!card) {
        const gone: Card = {
          kind: 'undone', title: `Операцию уже удалили: ${txs.map(txCardTitle).join(' · ')}`, subtitle: null,
          undone_action_id: cur.chain[0] ?? '', undo_action_id: null, buttons: [],
        }
        return { card: gone, chain: [], redoChain: [], editing: false, stale: true }
      }
      return { card, stale: cur.stale || (cur.creates && changed) }
    })
    return changed
  }, [patchEntry, tz])

  /** A mutation on the card failed: a 409/404 means the operation changed elsewhere → show it as it is. */
  const failMutation = async (id: string, e: unknown, fallback: string) => {
    if (isConflict(e)) {
      try { await resync(id) } catch { /* keep the card as is */ }
      patchEntry(id, () => ({ busy: null, error: CHANGED_ELSEWHERE }))
      setAnnounce(CHANGED_ELSEWHERE)
      return
    }
    failInline(id, e, fallback)
  }

  const replaceWithTx = useCallback((id: string, tx: TransactionOut, actionId: string, note: string | null = null) => {
    patchEntry(id, (e) => {
      const card = e.card.kind === 'created_many'
        ? { ...e.card, transactions: e.card.transactions.map((t) => (t.id === tx.id ? tx : t)) }
        : withDebtOf(cardFromTransaction(tx, actionId, tz), e.card, tx)
      return { card, chain: [...e.chain, actionId], busy: null, error: null, editing: false, note: note ?? e.note }
    })
  }, [patchEntry, tz])

  const undo = useCallback(async (id: string) => {
    const e = entriesRef.current.find((x) => x.uid === id)
    if (!e || e.busy || !e.chain.length) return
    const txs = cardTransactions(e.card)
    // Created here and then changed (on this card or on another device): every change raises the
    // version, so undoing the creation itself is refused (409 undo_conflict). «Отменить» still means
    // «I don't want this operation» → delete what is on the card; «Вернуть» restores it.
    const removeInstead = e.creates && txs.length > 0 && (e.stale || e.chain.length > e.rootActions)
    patchEntry(id, () => ({ busy: 'undo', error: null }))
    const reverted: string[] = []
    try {
      if (removeInstead) {
        const removed: string[] = []
        const titles: string[] = []
        for (const t of txs) {
          const r = await transactionsApi.remove(t.id, t.version, opKey(`del:${t.id}:${t.version}`))
          removed.push(r.action_id)
          titles.push(txCardTitle(r.transaction))
        }
        invalidate(['transactions', 'accounts', 'stats'])
        const card: Card = {
          kind: 'undone', title: `Отменено: ${titles.join(' · ')}`, subtitle: null,
          undone_action_id: e.chain[e.chain.length - 1], undo_action_id: removed[0],
          buttons: [{ id: 'redo', label: 'Вернуть', style: 'ghost', hotkey: null, action: { kind: 'redo', action_id: removed[0] } }],
        }
        patchEntry(id, () => ({ card, chain: [], redoChain: removed, busy: null, editing: false, note: null }))
        setAnnounce(card.title)
        return
      }
      let last: UndoResult | null = null
      const changed = new Set<string>()
      for (const actionId of [...e.chain].reverse()) {
        last = await actionsApi.undo(actionId, opKey(`undo:${actionId}`))
        reverted.push(actionId)
        last.changed.forEach((c) => changed.add(c))
      }
      if (!last) return
      invalidate([...changed])
      const card = undoneCard(last)
      patchEntry(id, () => ({ card, chain: [], redoChain: [], busy: null, editing: false, note: null }))
      setAnnounce(card.title)
    } catch (err) {
      if (reverted.length) {
        invalidate(['transactions', 'accounts', 'stats'])
        patchEntry(id, (cur) => ({ chain: cur.chain.filter((a) => !reverted.includes(a)) }))
      }
      if (isConflict(err) && txs.length) {
        // show what the operation really is now; for a created card the next «Отменить» removes it
        try { await resync(id) } catch { /* keep the card */ }
        const msg = e.creates ? `${CHANGED_ELSEWHERE}. «Отменить» удалит её` : (err as ApiError).message
        patchEntry(id, (cur) => ({ busy: null, error: msg, stale: cur.stale || e.creates }))
        setAnnounce(msg)
        return
      }
      failInline(id, err, 'Не удалось отменить')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patchEntry, invalidate, resync])

  const redo = useCallback(async (id: string, actionId: string) => {
    const e = entriesRef.current.find((x) => x.uid === id)
    const ids = e?.redoChain.length ? e.redoChain : [actionId]
    patchEntry(id, () => ({ busy: 'redo', error: null }))
    try {
      const results: UndoResult[] = []
      for (const a of ids) results.push(await actionsApi.undo(a, opKey(`undo:${a}`)))
      invalidate([...new Set(results.flatMap((r) => r.changed))])
      const title = results.map((r) => r.summary.replace(/^Отменено:\s*/, '')).join(' · ')
      const undoIds = results.map((r) => r.undo_action_id).filter((x): x is string => !!x)
      const card: Card = {
        kind: 'undone', title: `Возвращено: ${title}`, subtitle: null, undone_action_id: results[0].undone_action_id, undo_action_id: undoIds[0] ?? null,
        buttons: undoIds.length ? [{ id: 'undo', label: 'Отменить', style: 'ghost', hotkey: 'Ctrl+Z', action: { kind: 'undo', action_id: undoIds[0] } }] : [],
      }
      patchEntry(id, () => ({ card, chain: undoIds, redoChain: [], creates: false, rootActions: undoIds.length, stale: false, busy: null }))
      setAnnounce(card.title)
    } catch (err) {
      failInline(id, err, 'Не удалось вернуть')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patchEntry, invalidate])

  const fetchVersion = async (e: CardEntry, txId: number): Promise<TransactionOut> => {
    const local = cardTransactions(e.card).find((t) => t.id === txId)
    return local ?? transactionsApi.get(txId)
  }

  const setCategory = useCallback(async (id: string, txId: number, categoryId: number, categoryName?: string) => {
    const e = entriesRef.current.find((x) => x.uid === id)
    if (!e) return false
    patchEntry(id, () => ({ busy: 'category', error: null }))
    try {
      const tx = await fetchVersion(e, txId)
      const r = await transactionsApi.patch(txId, { category_id: categoryId, version: tx.version }, opKey(`cat:${txId}:${categoryId}:${tx.version}`))
      invalidate(['transactions', 'merchants', 'categories'])
      const who = r.transaction.merchant?.name ?? r.transaction.counterparty
      const name = categoryName ?? r.transaction.category?.name
      replaceWithTx(id, r.transaction, r.action_id, who && name ? `Запомню: ${who} → ${name}` : null)
      setAnnounce(`Категория: ${name ?? ''}`)
      return true
    } catch (err) {
      await failMutation(id, err, 'Не удалось сменить категорию')
      return false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patchEntry, invalidate, replaceWithTx])

  const applyEdit = useCallback(async (id: string, txId: number, patch: TransactionPatch) => {
    patchEntry(id, () => ({ busy: 'edit', error: null }))
    try {
      const r = await transactionsApi.patch(txId, patch, opKey(`edit:${txId}:${patch.version}:${JSON.stringify(patch)}`))
      invalidate(['transactions'])
      replaceWithTx(id, r.transaction, r.action_id)
      setAnnounce(`Изменено: ${r.transaction.title}`)
      return true
    } catch (err) {
      // 409 version_conflict: the form re-opens with the current values (TxEditForm is keyed by version)
      await failMutation(id, err, 'Не удалось сохранить')
      return false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patchEntry, invalidate, replaceWithTx])

  const removeTx = useCallback(async (id: string, tx: TransactionOut) => {
    patchEntry(id, () => ({ busy: `del:${tx.id}`, error: null }))
    try {
      const r = await transactionsApi.remove(tx.id, tx.version, opKey(`del:${tx.id}:${tx.version}`))
      invalidate(['transactions'])
      patchEntry(id, (e) => {
        if (e.card.kind !== 'created_many') return { busy: null }
        const rest = e.card.transactions.filter((t) => t.id !== tx.id)
        return { card: { ...e.card, transactions: rest }, chain: [...e.chain, r.action_id], busy: null }
      })
      return true
    } catch (err) {
      await failMutation(id, err, 'Не удалось удалить')
      return false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patchEntry, invalidate])

  const act = useCallback(async (id: string, b: Button) => {
    const e = entriesRef.current.find((x) => x.uid === id)
    if (!e) return
    const a = b.action
    switch (a.kind) {
      case 'undo':
        return undo(id)
      case 'redo':
        if (a.action_id) return redo(id, a.action_id)
        return
      case 'edit':
        patchEntry(id, (x) => ({ editing: !x.editing, error: null }))
        return
      case 'choose_account': {
        const txId = a.transaction_id ?? ('transaction' in e.card ? e.card.transaction?.id : undefined)
        if (!txId) return
        patchEntry(id, () => ({ busy: b.id, error: null }))
        try {
          const tx = 'transaction' in e.card ? e.card.transaction : undefined
          const version = tx && tx.id === txId ? tx.version : undefined
          const r = await transactionsApi.assignAccount(txId, a.account_id ?? null, opKey(`acc:${txId}:${a.account_id ?? 0}`), version)
          invalidate(['transactions', 'accounts'])
          replaceWithTx(id, r.transaction, r.action_id)
          setAnnounce(`Счёт: ${(r.transaction.debt ? debtRealAccount(r.transaction) : r.transaction.account)?.name ?? 'без счёта'}`)
        } catch (err) {
          if (err instanceof ApiError && err.status === 409 && err.code === 'version_conflict') {
            // chosen on another device / in the bot already → show the current state
            try { await resync(id) } catch { /* keep the card as is */ }
            invalidate(['transactions', 'accounts'])
            patchEntry(id, () => ({ busy: null, error: 'Счёт уже выбран' }))
            setAnnounce('Счёт уже выбран')
            return
          }
          await failMutation(id, err, 'Не удалось выбрать счёт')
        }
        return
      }
      case 'later':
        patchEntry(id, () => ({ dismissed: true }))
        toast('Отложено — операция в «Разобрать»', handlers.current.onOpen ? {
          action: { label: 'Разобрать', onClick: () => handlers.current.onOpen?.('/operations?status=needs_account') },
        } : undefined)
        return
      case 'change_category':
        // UI opens the picker (ResultCard); nothing to send yet
        return
      case 'set_category':
        if (a.transaction_id && a.category_id) await setCategory(id, a.transaction_id, a.category_id)
        return
      case 'confirm_preview': {
        if (e.card.kind !== 'preview' || e.busy) return
        const token = a.preview_token ?? e.card.preview_token
        const key = e.previewKey ?? newIdempotencyKey()
        patchEntry(id, () => ({ busy: 'confirm', error: null, previewKey: key }))
        try {
          const res = await assistantApi.confirmPreview(token, key)
          invalidate(res.changed)
          const fresh = res.cards.map((c) => entry(c, e.origin))
          setEntries((list) => list.flatMap((x) => (x.uid === id ? fresh : [x])))
          if (res.cards[0]) setAnnounce(res.cards[0].title)
        } catch (err) {
          failInline(id, err, 'Не удалось применить. Ничего не изменено')
        }
        return
      }
      case 'cancel_preview': {
        if (e.card.kind !== 'preview') return
        const token = a.preview_token ?? e.card.preview_token
        setEntries((list) => list.filter((x) => x.uid !== id))
        setAnnounce('Превью отменено. Ничего не изменено')
        assistantApi.cancelPreview(token).catch(() => { /* expires by itself in 10 min */ })
        return
      }
      case 'send_text':
        if (a.text) await send(a.text)
        return
      case 'manual':
        handlers.current.onManual?.({ ...(e.origin ? { text: e.origin.text } : {}), ...(a.prefill ?? {}) })
        return
      case 'retry':
        if (e.origin) {
          if (e.local && e.local !== 'other') {
            // no answer / 5xx / still running: the first attempt may have reached the server → the SAME
            // key (a replay returns its result, never a duplicate)
            if (!keys.current.peek(e.origin.text)) keysBookRestore(keys.current, e.origin)
          } else {
            // the server answered (e.g. «AI недоступен», a 4xx) and wrote nothing; its answer is stored
            // under the old key and would only be replayed → a new attempt with a new key
            keys.current.forget(e.origin.text)
          }
          await send(e.origin.text, e.origin.context, { replace: id })
        }
        return
      case 'open':
        if (a.route) handlers.current.onOpen?.(a.route)
        return
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [undo, redo, patchEntry, invalidate, replaceWithTx, setCategory, send])

  const hotkey = useCallback((cmd: CardHotkey): boolean => {
    if (!cmd) return false
    const visible = entriesRef.current.filter((x) => !x.dismissed)
    if (typeof cmd === 'object') {
      const target = visible.find((x) => x.card.kind === 'needs_account')
      const b = target && accountButtonForDigit(target.card, cmd.digit)
      if (target && b) { void act(target.uid, b); return true }
      return false
    }
    const latest = visible[0]
    if (!latest) return false
    if (cmd === 'undo') {
      const b = undoButton(latest.card)
      if (b && latest.chain.length) { void act(latest.uid, b); return true }
    }
    if (cmd === 'edit') {
      const b = editButton(latest.card)
      if (b) { void act(latest.uid, b); return true }
    }
    return false
  }, [act])

  const dismiss = useCallback((id: string) => patchEntry(id, () => ({ dismissed: true })), [patchEntry])
  const setEditing = useCallback((id: string, on: boolean) => patchEntry(id, () => ({ editing: on, error: null })), [patchEntry])
  const clear = useCallback(() => setEntries([]), [])
  const onSent = useCallback((fn: (t: string, o: SendOutcome) => void) => {
    sentListeners.current.add(fn)
    return () => { sentListeners.current.delete(fn) }
  }, [])

  // 30 s without interaction → success cards collapse (they live on in «Последние»)
  useEffect(() => {
    const t = setInterval(() => {
      const cutoff = now() - 30_000
      setEntries((list) => {
        let changed = false
        const next = list.map((e) => {
          if (!e.dismissed && !e.busy && !e.editing && EPHEMERAL.has(e.card.kind) && e.touchedAt < cutoff) {
            changed = true
            return { ...e, dismissed: true }
          }
          return e
        })
        return changed ? next.filter((e, i) => !e.dismissed || i < 20) : list
      })
    }, 5_000)
    return () => clearInterval(t)
  }, [])

  // the server answers again after a failure / offline period → auto-[Повторить] the cards whose request
  // may not have landed (same key: a replay settles the outcome), within 5 min, a few times at most
  useEffect(() => connection.onRecovered(() => {
    const cutoff = now() - AUTO_RETRY_WINDOW
    for (const e of entriesRef.current) {
      if (!e.local || !AUTO_RETRY_KINDS.has(e.local) || e.dismissed || !e.origin || e.busy) continue
      if (e.createdAt <= cutoff || e.autoRetries >= MAX_AUTO_RETRIES) continue
      patchEntry(e.uid, (x) => ({ autoRetries: x.autoRetries + 1 }))
      void sendRef.current(e.origin.text, e.origin.context, { replace: e.uid })
    }
  }), [patchEntry])

  // an operation on a visible card changed on another device / in the bot → redraw it as it is now
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null
    const off = onServerChange((ents) => {
      if (!ents.includes('transactions')) return
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => {
        const visible = entriesRef.current.filter((x) => !x.dismissed && !x.busy && !x.editing && cardTransactions(x.card).length)
        for (const x of visible.slice(0, 3)) void resync(x.uid, true).catch(() => { /* offline: keep the card */ })
      }, 400)
    })
    return () => { off(); if (timer) clearTimeout(timer) }
  }, [resync])

  const value = useMemo<Ctx>(() => ({
    entries, pendingText, announce, send, act, hotkey, dismiss, setEditing, applyEdit, removeTx, setCategory, clear, onSent,
  }), [entries, pendingText, announce, send, act, hotkey, dismiss, setEditing, applyEdit, removeTx, setCategory, clear, onSent])

  return <AssistantContext.Provider value={value}>{children}</AssistantContext.Provider>
}

/** [Повторить] on a local card: resend with the request's original key (API.md §9.1 retry). */
function keysBookRestore(book: KeyBook, origin: Origin) {
  book.acquire(origin.text, () => origin.key)
  book.failed(origin.text)
}

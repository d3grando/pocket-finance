// Settings → Регулярные платежи: reminders only (bot sends [Записать] [Пропустить]); create/edit/pause/
// record now/skip/delete. POST /recurring/{id}/record requires an Idempotency-Key.
import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, Check, Ellipsis, Pencil, Plus, SkipForward, Trash2 } from 'lucide-react'
import { api, newIdempotencyKey, qk, type MutationResult, type RecurringCreate, type RecurringOut } from '@/lib/api'
import { errorMessage, invalidateEntities, isOfflineEmpty, keepAttemptKey, loadErrorMessage, useAccounts, useCategories, useToday } from '@/features/operations/api'
import { CategoryPicker } from '@/features/operations/components/CategoryPicker'
import { CurrencySelect } from '@/features/operations/components/CurrencySelect'
import { useUndoToast } from '@/features/operations/components/useUndoToast'
import { MONTHS_GEN, formatDateShort } from '@/features/operations/lib/dates'
import { normalizeAmountInput } from '@/features/operations/lib/decimal'
import { formatMoney } from '@/features/operations/lib/format'
import { Button, EmptyState, Field, Input, NativeSelect, OfflineState, Segmented, Skeleton, Switch } from '@/features/operations/ui/kit'
import { Menu, notify, Sheet } from '@/features/operations/ui/overlays'
import { SettingsSection } from '../ui'

const WD_ACC = ['понедельникам', 'вторникам', 'средам', 'четвергам', 'пятницам', 'субботам', 'воскресеньям']
const WD = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']

export function scheduleText(r: Pick<RecurringOut, 'frequency' | 'interval' | 'day_of_month' | 'weekday' | 'month'>): string {
  const n = r.interval || 1
  switch (r.frequency) {
    case 'daily':
      return n === 1 ? 'каждый день' : `раз в ${n} дн.`
    case 'weekly':
      return `${n === 1 ? '' : `раз в ${n} нед., `}по ${WD_ACC[r.weekday ?? 0]}`
    case 'monthly': {
      const d = r.day_of_month === 31 ? 'в последний день' : r.day_of_month ? `${r.day_of_month}-го` : ''
      return `${n === 1 ? 'каждый месяц' : `раз в ${n} мес.`}${d ? `, ${d}` : ''}`
    }
    case 'yearly':
      return `каждый год${r.day_of_month && r.month ? `, ${r.day_of_month} ${MONTHS_GEN[r.month - 1]}` : ''}`
  }
}

function RecurringForm({ initial, onDone }: { initial?: RecurringOut; onDone: () => void }) {
  const qc = useQueryClient()
  const { today } = useToday()
  const cats = useCategories().data ?? []
  const accounts = (useAccounts().data ?? []).filter((a) => !a.is_archived)
  const [title, setTitle] = useState(initial?.title ?? '')
  const [kind, setKind] = useState<'expense' | 'income'>(initial?.kind ?? 'expense')
  const [amount, setAmount] = useState(initial?.amount ?? '')
  const [currency, setCurrency] = useState(initial?.currency ?? 'VND')
  const [categoryId, setCategoryId] = useState<number | null>(initial?.category_id ?? null)
  const [accountId, setAccountId] = useState(initial?.account_id ? String(initial.account_id) : '')
  const [frequency, setFrequency] = useState<RecurringOut['frequency']>(initial?.frequency ?? 'monthly')
  const [interval, setInterval] = useState(String(initial?.interval ?? 1))
  const [day, setDay] = useState(String(initial?.day_of_month ?? Number(today.slice(8, 10))))
  const [weekday, setWeekday] = useState(String(initial?.weekday ?? 0))
  const [month, setMonth] = useState(String(initial?.month ?? Number(today.slice(5, 7))))
  const [next, setNext] = useState(initial?.next_due_on ?? today)
  const [time, setTime] = useState((initial?.remind_time ?? '10:00:00').slice(0, 5))
  const [err, setErr] = useState<string | null>(null)
  const m = useMutation({
    mutationFn: (body: RecurringCreate) => (initial ? api.patch<RecurringOut>(`/recurring/${initial.id}`, { ...body, kind: undefined, version: initial.version }) : api.post<RecurringOut>('/recurring', body)),
    onSuccess: () => {
      invalidateEntities(qc, ['recurring'])
      onDone()
    },
    onError: (e) => setErr(errorMessage(e)),
  })
  const submit = () => {
    if (!title.trim()) return setErr('Укажите название')
    const amt = amount ? normalizeAmountInput(amount) : null
    if (amount && !amt) return setErr('Сумма — число больше нуля (или оставьте пустой)')
    const iv = Number(interval)
    if (!Number.isInteger(iv) || iv < 1 || iv > 36) return setErr('Интервал — от 1 до 36')
    const body: RecurringCreate = {
      title: title.trim(),
      kind,
      amount: amt,
      currency,
      category_id: categoryId,
      account_id: accountId ? Number(accountId) : null,
      frequency,
      interval: iv,
      day_of_month: frequency === 'monthly' || frequency === 'yearly' ? Math.min(31, Math.max(1, Number(day) || 1)) : null,
      weekday: frequency === 'weekly' ? Number(weekday) : null,
      month: frequency === 'yearly' ? Number(month) : null,
      next_due_on: next,
      remind_time: `${time}:00`,
    }
    setErr(null)
    m.mutate(body)
  }
  return (
    <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); submit() }} aria-label="Регулярный платёж">
      <Field label="Название" htmlFor="rc-title">
        <Input id="rc-title" autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Hetzner, аренда" maxLength={128} />
      </Field>
      {!initial && <Segmented label="Тип" value={kind} onChange={(v) => { setKind(v); setCategoryId(null) }} options={[{ value: 'expense', label: 'Расход' }, { value: 'income', label: 'Доход' }]} />}
      <div className="grid grid-cols-[minmax(0,1fr)_120px] gap-2">
        <Field label="Сумма" htmlFor="rc-amount" hint="пусто — спрошу при напоминании">
          <Input id="rc-amount" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} className="num" />
        </Field>
        <Field label="Валюта" htmlFor="rc-cur">
          <CurrencySelect id="rc-cur" value={currency} onChange={setCurrency} />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Категория">
          <CategoryPicker cats={cats} value={categoryId} kind={kind} onChange={(c) => setCategoryId(c.id)} />
        </Field>
        <Field label="Счёт" htmlFor="rc-acc">
          <NativeSelect id="rc-acc" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
            <option value="">Авто</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </NativeSelect>
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Повтор" htmlFor="rc-freq">
          <NativeSelect id="rc-freq" value={frequency} onChange={(e) => setFrequency(e.target.value as RecurringOut['frequency'])}>
            <option value="daily">Каждый день</option>
            <option value="weekly">Каждую неделю</option>
            <option value="monthly">Каждый месяц</option>
            <option value="yearly">Каждый год</option>
          </NativeSelect>
        </Field>
        <Field label="Интервал" htmlFor="rc-int">
          <Input id="rc-int" type="number" min={1} max={36} value={interval} onChange={(e) => setInterval(e.target.value)} />
        </Field>
        {frequency === 'weekly' && (
          <Field label="День недели" htmlFor="rc-wd">
            <NativeSelect id="rc-wd" value={weekday} onChange={(e) => setWeekday(e.target.value)}>
              {WD.map((w, i) => (
                <option key={w} value={i}>
                  {w}
                </option>
              ))}
            </NativeSelect>
          </Field>
        )}
        {(frequency === 'monthly' || frequency === 'yearly') && (
          <Field label="Число" htmlFor="rc-day" hint="31 — последний день месяца">
            <Input id="rc-day" type="number" min={1} max={31} value={day} onChange={(e) => setDay(e.target.value)} />
          </Field>
        )}
        {frequency === 'yearly' && (
          <Field label="Месяц" htmlFor="rc-month">
            <NativeSelect id="rc-month" value={month} onChange={(e) => setMonth(e.target.value)}>
              {MONTHS_GEN.map((mn, i) => (
                <option key={mn} value={i + 1}>
                  {mn}
                </option>
              ))}
            </NativeSelect>
          </Field>
        )}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Ближайший раз" htmlFor="rc-next">
          <Input id="rc-next" type="date" value={next} onChange={(e) => setNext(e.target.value)} />
        </Field>
        <Field label="Напомнить в" htmlFor="rc-time">
          <Input id="rc-time" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
        </Field>
      </div>
      <p className="text-xs text-muted-foreground">Это только напоминание: бот пришлёт [Записать] [Пропустить], сама операция без подтверждения не создаётся.</p>
      {err && <p role="alert" className="rounded-sm bg-expense-soft px-2 py-1.5 text-sm text-destructive">{err}</p>}
      <Button type="submit" variant="primary" loading={m.isPending}>
        {initial ? 'Сохранить' : 'Добавить напоминание'}
      </Button>
    </form>
  )
}

export function RecurringSection() {
  const qc = useQueryClient()
  const { today } = useToday()
  const q = useQuery({ queryKey: qk.recurring, queryFn: () => api.get<RecurringOut[]>('/recurring') })
  const [edit, setEdit] = useState<RecurringOut | 'new' | null>(null)
  const undoToast = useUndoToast()
  const refresh = () => invalidateEntities(qc, ['recurring'])
  const toggle = useMutation({
    mutationFn: (r: RecurringOut) => api.patch<RecurringOut>(`/recurring/${r.id}`, { version: r.version, is_active: !r.is_active }),
    onSuccess: refresh,
    onError: (e) => { refresh(); notify.error(errorMessage(e)) },
  })
  // One Idempotency-Key per «Записать» attempt of a reminder: a repeat after a lost answer (no
  // connection, 5xx, still in progress) reuses it, so the server replays instead of recording twice.
  const recordKeys = useRef(new Map<number, string>())
  const record = useMutation({
    mutationFn: (r: RecurringOut) => {
      let key = recordKeys.current.get(r.id)
      if (!key) {
        key = newIdempotencyKey()
        recordKeys.current.set(r.id, key)
      }
      return api.post<MutationResult>(`/recurring/${r.id}/record`, {}, { idempotencyKey: key })
    },
    onSuccess: (res, r) => {
      recordKeys.current.delete(r.id)
      invalidateEntities(qc, ['recurring', 'transactions', 'accounts', 'stats'])
      undoToast(`✓ ${formatMoney(res.transaction.amount, res.transaction.currency)} · ${res.transaction.category?.name ?? res.transaction.title}`, res.action_id)
    },
    onError: (e, r) => {
      if (!keepAttemptKey(e)) recordKeys.current.delete(r.id)
      notify.error(errorMessage(e, 'Не удалось записать. Ничего не записано'))
    },
  })
  const skip = useMutation({ mutationFn: (r: RecurringOut) => api.post<RecurringOut>(`/recurring/${r.id}/skip`), onSuccess: (r) => { refresh(); notify.info(`Пропущено, следующий раз ${formatDateShort(r.next_due_on, today)}`) }, onError: (e) => notify.error(errorMessage(e)) })
  const del = useMutation({ mutationFn: (r: RecurringOut) => api.delete(`/recurring/${r.id}`), onSuccess: refresh, onError: (e) => notify.error(errorMessage(e)) })
  return (
    <SettingsSection
      title="Регулярные платежи"
      description="Напоминания в Telegram о подписках, аренде, серверах — с кнопками [Записать] [Пропустить]."
      actions={<Button size="sm" variant="primary" onClick={() => setEdit('new')}><Plus /> Напоминание</Button>}
    >
      {isOfflineEmpty(q) ? (
        <OfflineState what="Напоминания" onRetry={() => void q.refetch()} className="py-6" />
      ) : q.isLoading ? (
        <Skeleton className="h-32" />
      ) : q.error ? (
        <p className="text-sm text-destructive">{loadErrorMessage(q.error, 'Не удалось загрузить')}</p>
      ) : q.data?.length ? (
        <ul>
          {q.data.map((r) => (
            <li key={r.id} className="flex min-h-14 items-center gap-3 border-b border-border py-2 last:border-0">
              <CalendarClock className="size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">
                  {r.title}
                  {r.amount && <span className="num font-normal text-muted-foreground"> · {formatMoney(r.amount, r.currency)}</span>}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  {scheduleText(r)} · в {r.remind_time.slice(0, 5)} · следующий {formatDateShort(r.next_due_on, today)}
                </p>
              </div>
              <Switch checked={r.is_active} onCheckedChange={() => toggle.mutate(r)} label={`Напоминать: ${r.title}`} />
              <Menu
                label={`Действия «${r.title}»`}
                items={[
                  { label: 'Записать сейчас', icon: <Check />, onSelect: () => record.mutate(r), disabled: record.isPending },
                  { label: 'Пропустить раз', icon: <SkipForward />, onSelect: () => skip.mutate(r) },
                  { label: 'Изменить', icon: <Pencil />, onSelect: () => setEdit(r) },
                  'sep',
                  { label: 'Удалить', icon: <Trash2 />, onSelect: () => del.mutate(r), danger: true },
                ]}
                trigger={<Button size="iconSm" variant="ghost" aria-label={`Ещё для «${r.title}»`}><Ellipsis /></Button>}
              />
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState icon={<CalendarClock />} title="Напоминаний нет" text="Добавьте, например, «Hetzner, $45, 5-го числа»." className="py-6" />
      )}
      <Sheet open={edit !== null} onOpenChange={(v) => !v && setEdit(null)} title={edit === 'new' ? 'Новое напоминание' : 'Напоминание'}>
        {edit !== null && <RecurringForm key={edit === 'new' ? 'new' : edit.id} initial={edit === 'new' ? undefined : edit} onDone={() => setEdit(null)} />}
      </Sheet>
    </SettingsSection>
  )
}

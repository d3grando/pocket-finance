// Settings → Продавцы (rules «васт → vast.ai → Серверы/инфра») and → Мои аккаунты (@nicks treated as own).
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AtSign, Store, Trash2 } from 'lucide-react'
import { api, qk, type CategoryOut, type MerchantOut, type MyAccountOut } from '@/lib/api'
import { errorMessage, invalidateEntities, useAccounts, useCategories, useMerchants } from '@/features/operations/api'
import { CategoryPicker } from '@/features/operations/components/CategoryPicker'
import { countLabel } from '@/features/operations/lib/format'
import { useDebounced } from '@/features/operations/ui/hooks'
import { Button, EmptyState, Field, Input, NativeSelect, Skeleton } from '@/features/operations/ui/kit'
import { notify } from '@/features/operations/ui/overlays'
import { SettingsSection } from '../ui'

const splitAliases = (s: string) => s.split(',').map((x) => x.trim().toLowerCase()).filter(Boolean)

function MerchantRow({ m, cats }: { m: MerchantOut; cats: CategoryOut[] }) {
  const qc = useQueryClient()
  const patch = useMutation({
    mutationFn: (body: Partial<MerchantOut>) => api.patch<MerchantOut>(`/merchants/${m.id}`, body),
    onSuccess: () => invalidateEntities(qc, ['merchants']),
    onError: (e) => notify.error(errorMessage(e, 'Не удалось сохранить правило')),
  })
  const del = useMutation({
    mutationFn: () => api.delete(`/merchants/${m.id}`),
    onSuccess: () => {
      invalidateEntities(qc, ['merchants'])
      notify.info(`Правило «${m.name}» удалено`)
    },
    onError: (e) => notify.error(errorMessage(e, 'Не удалось удалить правило')),
  })
  const [aliases, setAliases] = useState(m.aliases.join(', '))
  const cat = cats.find((c) => c.id === m.category_id)
  return (
    <li className="grid gap-2 border-b border-border py-3 last:border-0 md:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)_auto] md:items-center">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium">
          {m.name}
          {m.handle && <span className="text-muted-foreground"> · @{m.handle}</span>}
        </p>
        <p className="text-xs text-muted-foreground">
          {countLabel(m.hits, ['раз', 'раза', 'раз'])} · {cat ? `→ ${cat.name}` : 'без категории'}
        </p>
      </div>
      <form className="min-w-0" onSubmit={(e) => { e.preventDefault(); patch.mutate({ aliases: splitAliases(aliases) }) }}>
        <Input value={aliases} onChange={(e) => setAliases(e.target.value)} onBlur={() => aliases !== m.aliases.join(', ') && patch.mutate({ aliases: splitAliases(aliases) })} aria-label={`Слова для «${m.name}»`} placeholder="васт, vast" className="h-8" />
      </form>
      <CategoryPicker cats={cats} value={m.category_id} onChange={(c) => patch.mutate({ category_id: c.id })} className="h-8" />
      <Button size="iconSm" variant="ghost" aria-label={`Удалить правило «${m.name}»`} loading={del.isPending} onClick={() => del.mutate()}>
        {!del.isPending && <Trash2 />}
      </Button>
    </li>
  )
}

export function MerchantsSection() {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const dq = useDebounced(search, 300)
  const q = useMerchants(dq)
  const cats = useCategories().data ?? []
  const [name, setName] = useState('')
  const [aliases, setAliases] = useState('')
  const [catId, setCatId] = useState<number | null>(null)
  const create = useMutation({
    mutationFn: () => api.post<MerchantOut>('/merchants', { name: name.trim(), aliases: splitAliases(aliases), category_id: catId }),
    onSuccess: (m) => {
      invalidateEntities(qc, ['merchants'])
      notify.info(`Запомню: ${m.name}${m.category_name ? ` → ${m.category_name}` : ''}`)
      setName('')
      setAliases('')
      setCatId(null)
    },
    onError: (e) => notify.error(errorMessage(e, 'Не удалось создать правило')),
  })
  return (
    <SettingsSection title="Продавцы" description="Правила «васт → vast.ai → Серверы/инфра». Если поправить категорию операции, правило обновится само.">
      <form className="mb-4 grid gap-2 rounded-md bg-surface-2 p-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto] md:items-end" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate() }}>
        <Field label="Продавец" htmlFor="mr-name">
          <Input id="mr-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="vast.ai" maxLength={128} />
        </Field>
        <Field label="Как пишу" htmlFor="mr-alias">
          <Input id="mr-alias" value={aliases} onChange={(e) => setAliases(e.target.value)} placeholder="васт, vast" />
        </Field>
        <Field label="Категория">
          <CategoryPicker cats={cats} value={catId} onChange={(c) => setCatId(c.id)} />
        </Field>
        <Button type="submit" variant="primary" disabled={!name.trim()} loading={create.isPending}>
          Добавить
        </Button>
      </form>
      <Input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Найти правило" aria-label="Найти правило" className="mb-2 md:max-w-72" />
      {q.isLoading ? (
        <Skeleton className="h-40" />
      ) : q.data?.length ? (
        <ul>
          {q.data.map((m) => (
            <MerchantRow key={m.id} m={m} cats={cats} />
          ))}
        </ul>
      ) : (
        <EmptyState icon={<Store />} title={search ? 'Ничего не нашлось' : 'Правил пока нет'} text="Продавцы запоминаются сами при записи операций." className="py-6" />
      )}
    </SettingsSection>
  )
}

export function MyAccountsSection() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: qk.myAccounts, queryFn: () => api.get<MyAccountOut[]>('/my-accounts') })
  const accounts = useAccounts(true).data ?? []
  const [handle, setHandle] = useState('')
  const [label, setLabel] = useState('')
  const [accountId, setAccountId] = useState('')
  const create = useMutation({
    mutationFn: () => api.post<MyAccountOut>('/my-accounts', { handle: handle.trim().startsWith('@') ? handle.trim() : `@${handle.trim()}`, label: label.trim() || null, account_id: accountId ? Number(accountId) : null }),
    onSuccess: (m) => {
      invalidateEntities(qc, ['accounts'])
      notify.info(`@${m.handle} — теперь «свой»: переводы на него не расход`)
      setHandle('')
      setLabel('')
      setAccountId('')
    },
    onError: (e) => notify.error(errorMessage(e, 'Не удалось добавить')),
  })
  const del = useMutation({
    mutationFn: (id: number) => api.delete(`/my-accounts/${id}`),
    onSuccess: () => invalidateEntities(qc, ['accounts']),
    onError: (e) => notify.error(errorMessage(e, 'Не удалось удалить')),
  })
  const valid = /^@?[A-Za-z0-9_]{3,64}$/.test(handle.trim())
  return (
    <SettingsSection title="Мои аккаунты" description="@ники, которые принадлежат вам. Перевод на них — между своими счетами, не расход. На любой другой @ник — расход с получателем.">
      <form className="mb-4 grid gap-2 rounded-md bg-surface-2 p-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto] md:items-end" onSubmit={(e) => { e.preventDefault(); if (valid) create.mutate() }}>
        <Field label="@ник" htmlFor="ma-handle" error={handle && !valid ? 'Латиница, цифры и _ (от 3 символов)' : undefined}>
          <Input id="ma-handle" value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="@myshop" maxLength={65} />
        </Field>
        <Field label="Подпись" htmlFor="ma-label">
          <Input id="ma-label" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="второй аккаунт" maxLength={64} />
        </Field>
        <Field label="Кошелёк" htmlFor="ma-acc" hint="пусто — создам кошелёк @ника">
          <NativeSelect id="ma-acc" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
            <option value="">Новый кошелёк</option>
            {accounts.filter((a) => !a.is_archived).map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Button type="submit" variant="primary" disabled={!valid} loading={create.isPending}>
          Добавить
        </Button>
      </form>
      {q.isLoading ? (
        <Skeleton className="h-24" />
      ) : q.data?.length ? (
        <ul>
          {q.data.map((m) => (
            <li key={m.id} className="flex min-h-11 items-center gap-3 border-b border-border py-1 last:border-0">
              <AtSign className="size-4 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate text-sm font-medium">
                @{m.handle}
                {m.label && <span className="font-normal text-muted-foreground"> · {m.label}</span>}
              </span>
              <span className="truncate text-[13px] text-muted-foreground">кошелёк: {accounts.find((a) => a.id === m.account_id)?.name ?? `#${m.account_id}`}</span>
              <Button size="iconSm" variant="ghost" aria-label={`Удалить @${m.handle}`} onClick={() => del.mutate(m.id)}>
                <Trash2 />
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState icon={<AtSign />} title="Список пуст" text="Добавьте свой второй аккаунт или кошелёк, чтобы «перевёл 3 тона @ник» считалось переводом между своими, а не расходом." className="py-6" />
      )}
    </SettingsSection>
  )
}

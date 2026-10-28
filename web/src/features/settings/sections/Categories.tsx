// Settings → Категории: groups Расходы / Бизнес-расходы / Доходы; 🆕 from AI on top; create, rename,
// icon, business/personal, aliases, confirm 🆕, merge («Перенесу 14 операций в «Еда»» → Enter), archive.
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Archive, ArchiveRestore, Check, Ellipsis, Merge, Pencil, Plus, X } from 'lucide-react'
import { api, type CategoryOut, type CategoryPatch } from '@/lib/api'
import { errorMessage, invalidateEntities, useCategories } from '@/features/operations/api'
import { groupCategories } from '@/features/operations/components/CategoryPicker'
import { countLabel } from '@/features/operations/lib/format'
import { CATEGORY_ICON_CHOICES, IconTile, iconByName } from '@/features/operations/ui/icons'
import { Badge, Button, Chip, EmptyState, Field, Input, NativeSelect, Segmented, Skeleton, Switch } from '@/features/operations/ui/kit'
import { Dialog, Menu, notify, Sheet } from '@/features/operations/ui/overlays'
import { SettingsSection } from '../ui'

function usePatchCategory() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: CategoryPatch }) => api.patch<CategoryOut>(`/categories/${id}`, patch),
    onSuccess: () => invalidateEntities(qc, ['categories', 'transactions']),
    onError: (e) => {
      invalidateEntities(qc, ['categories'])
      notify.error(errorMessage(e, 'Не удалось сохранить категорию'))
    },
  })
}

function CreateCategory({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [kind, setKind] = useState<'expense' | 'income'>('expense')
  const [biz, setBiz] = useState(false)
  const m = useMutation({
    mutationFn: () => api.post<CategoryOut>('/categories', { name: name.trim(), kind, is_business: biz, aliases: [] }),
    onSuccess: (c) => {
      invalidateEntities(qc, ['categories'])
      notify.info(`Категория «${c.name}» создана`)
      onDone()
    },
  })
  return (
    <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); if (name.trim()) m.mutate() }}>
      <Field label="Название" htmlFor="nc-name">
        <Input id="nc-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} maxLength={64} placeholder="Спорт" />
      </Field>
      <Segmented label="Тип категории" value={kind} onChange={setKind} options={[{ value: 'expense', label: 'Расход' }, { value: 'income', label: 'Доход' }]} />
      <label className="flex items-center justify-between gap-3 text-sm">
        Бизнес
        <Switch checked={biz} onCheckedChange={setBiz} label="Бизнес-категория" />
      </label>
      {m.error && <p role="alert" className="text-sm text-destructive">{errorMessage(m.error)}</p>}
      <Button type="submit" variant="primary" loading={m.isPending} disabled={!name.trim()}>
        Создать
      </Button>
    </form>
  )
}

function CategoryEditor({ cat, onDone }: { cat: CategoryOut; onDone: () => void }) {
  const patch = usePatchCategory()
  const [name, setName] = useState(cat.name)
  const [alias, setAlias] = useState('')
  const save = (p: Omit<CategoryPatch, 'version'>, after?: () => void) => patch.mutate({ id: cat.id, patch: { ...p, version: cat.version } }, { onSuccess: () => after?.() })
  return (
    <div className="grid gap-4">
      <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim() && name.trim() !== cat.name) save({ name: name.trim() }) }}>
        <Field label="Название" htmlFor={`cn-${cat.id}`} className="flex-1">
          <Input id={`cn-${cat.id}`} value={name} onChange={(e) => setName(e.target.value)} maxLength={64} />
        </Field>
        <Button type="submit" disabled={!name.trim() || name.trim() === cat.name} loading={patch.isPending}>
          Переименовать
        </Button>
      </form>
      {cat.kind === 'expense' && (
        <label className="flex items-center justify-between gap-3 text-sm">
          <span>
            Бизнес-расход
            <span className="block text-xs text-muted-foreground">бизнес или личное определяется категорией</span>
          </span>
          <Switch checked={cat.is_business} onCheckedChange={(v) => save({ is_business: v })} label="Бизнес" />
        </label>
      )}
      {cat.kind === 'income' && (
        <label className="flex items-center justify-between gap-3 text-sm">
          Бизнес-доход
          <Switch checked={cat.is_business} onCheckedChange={(v) => save({ is_business: v })} label="Бизнес" />
        </label>
      )}
      <Field label="Иконка">
        <div className="flex flex-wrap gap-1.5">
          {CATEGORY_ICON_CHOICES.map((n) => {
            const I = iconByName(n)
            return (
              <Chip key={n} active={cat.icon === n} aria-label={n} onClick={() => save({ icon: n })} className="w-9 justify-center px-0">
                <I />
              </Chip>
            )
          })}
        </div>
      </Field>
      <Field label="Слова, по которым узнаю категорию" hint="«еда, обед, food» — Enter, чтобы добавить">
        <div className="flex flex-wrap gap-1.5">
          {cat.aliases.map((a) => (
            <span key={a} className="inline-flex h-7 items-center gap-1 rounded-sm bg-muted pr-1 pl-2.5 text-[13px]">
              {a}
              <button type="button" aria-label={`Удалить слово ${a}`} onClick={() => save({ aliases: cat.aliases.filter((x) => x !== a) })} className="relative grid size-6 place-items-center rounded-xs text-muted-foreground after:absolute after:-inset-1 after:content-[''] hover:bg-surface-2">
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); const v = alias.trim().toLowerCase(); if (v && !cat.aliases.includes(v)) save({ aliases: [...cat.aliases, v] }, () => setAlias('')) }}>
          <Input value={alias} onChange={(e) => setAlias(e.target.value)} aria-label="Новое слово" className="h-8" maxLength={40} />
          <Button type="submit" size="sm" disabled={!alias.trim()}>
            Добавить
          </Button>
        </form>
      </Field>
      <Button variant="primary" onClick={onDone}>
        Готово
      </Button>
    </div>
  )
}

function MergeDialog({ source, cats, onClose }: { source: CategoryOut; cats: CategoryOut[]; onClose: () => void }) {
  const qc = useQueryClient()
  const targets = cats.filter((c) => c.kind === source.kind && c.id !== source.id && !c.is_archived)
  const [target, setTarget] = useState<string>('')
  const t = targets.find((c) => String(c.id) === target)
  const m = useMutation({
    mutationFn: () => api.post<CategoryOut>('/categories/merge', { source_id: source.id, target_id: Number(target) }),
    onSuccess: (r) => {
      invalidateEntities(qc, ['categories', 'transactions', 'merchants', 'stats'])
      notify.info(`«${source.name}» слита с «${r.name}»`)
      onClose()
    },
  })
  const n = source.tx_count ?? 0
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title={`Слить «${source.name}» с…`}
      description="Операции и правила продавцов перейдут в выбранную категорию. Слияние отменяемо из истории действий."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Отмена <span className="text-xs text-muted-foreground">Esc</span>
          </Button>
          <Button variant="primary" disabled={!t} loading={m.isPending} onClick={() => m.mutate()}>
            Слить <span className="text-xs opacity-80">⏎</span>
          </Button>
        </>
      }
    >
      <form onSubmit={(e) => { e.preventDefault(); if (t) m.mutate() }} className="grid gap-3">
        <Field label="Цель" htmlFor="merge-target">
          <NativeSelect id="merge-target" autoFocus value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Выберите категорию</option>
            {targets.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
                {c.is_business ? ' (бизнес)' : ''}
              </option>
            ))}
          </NativeSelect>
        </Field>
        {t && (
          <p className="rounded-md bg-primary-soft px-3 py-2 text-sm text-primary" aria-live="polite">
            Перенесу {countLabel(n, ['операцию', 'операции', 'операций'])} в «{t.name}»
          </p>
        )}
        {m.error && <p role="alert" className="text-sm text-destructive">{errorMessage(m.error)}</p>}
      </form>
    </Dialog>
  )
}

export function CategoriesSection() {
  const [archived, setArchived] = useState(false)
  const q = useCategories({ includeArchived: archived, withCounts: true })
  const patch = usePatchCategory()
  const qc = useQueryClient()
  const [edit, setEdit] = useState<CategoryOut | null>(null)
  const [merge, setMerge] = useState<CategoryOut | null>(null)
  const [create, setCreate] = useState(false)
  const del = useMutation({
    mutationFn: (c: CategoryOut) => api.delete(`/categories/${c.id}`),
    onSuccess: () => invalidateEntities(qc, ['categories']),
    onError: (e) => notify.error(errorMessage(e, 'Не удалось убрать категорию')),
  })
  const cats = q.data ?? []
  const live = cats.filter((c) => !c.is_archived)
  const groups = groupCategories(live)
  const editing = edit ? (cats.find((c) => c.id === edit.id) ?? edit) : null
  return (
    <SettingsSection
      title="Категории"
      description="Бизнес или личное определяется категорией. Новые от AI помечены 🆕."
      actions={
        <Button variant="primary" size="sm" onClick={() => setCreate(true)}>
          <Plus /> Категория
        </Button>
      }
    >
      {q.isLoading ? (
        <Skeleton className="h-72" />
      ) : q.error ? (
        <p className="text-sm text-destructive">{errorMessage(q.error, 'Не удалось загрузить категории')}</p>
      ) : (
        <div className="grid gap-5">
          {groups.map((g) => (
            <div key={g.heading}>
              <h3 className="mb-1 text-[13px] font-semibold text-muted-foreground">{g.heading}</h3>
              <ul className="grid">
                {g.items.map((c) => (
                  <li key={c.id} className="flex min-h-11 items-center gap-3 border-b border-border/70 py-1 last:border-0">
                    <IconTile icon={iconByName(c.icon)} business={c.is_business} />
                    <button type="button" onClick={() => setEdit(c)} className="min-h-11 min-w-0 flex-1 self-stretch truncate text-left text-sm font-medium hover:underline">
                      {c.name}
                    </button>
                    {c.is_new && (
                      <Badge tone="primary">
                        <span title="Новая категория, создана автоматически">🆕</span> новая
                      </Badge>
                    )}
                    {c.is_business && <Badge>бизнес</Badge>}
                    <span className="num w-16 shrink-0 text-right text-[13px] text-muted-foreground">{c.tx_count ?? 0}</span>
                    {c.is_new && (
                      <Button size="iconSm" variant="ghost" aria-label={`Подтвердить «${c.name}»`} title="Оставить категорию" onClick={() => patch.mutate({ id: c.id, patch: { version: c.version, is_new: false } })}>
                        <Check />
                      </Button>
                    )}
                    <Menu
                      label={`Действия «${c.name}»`}
                      items={[
                        { label: 'Изменить', icon: <Pencil />, onSelect: () => setEdit(c) },
                        { label: 'Слить с…', icon: <Merge />, onSelect: () => setMerge(c) },
                        'sep',
                        { label: 'В архив', icon: <Archive />, onSelect: () => del.mutate(c), disabled: c.is_system && c.name === 'Прочее' },
                      ]}
                      trigger={
                        <Button size="iconSm" variant="ghost" aria-label={`Ещё для «${c.name}»`}>
                          <Ellipsis />
                        </Button>
                      }
                    />
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {groups.length === 0 && <EmptyState icon={<Plus />} title="Категорий нет" text="Создайте первую категорию." />}
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            <Switch checked={archived} onCheckedChange={setArchived} label="Показать архив" /> Показать архивные
          </label>
          {archived && (
            <ul className="grid">
              {cats
                .filter((c) => c.is_archived)
                .map((c) => (
                  <li key={c.id} className="flex min-h-10 items-center gap-3 text-sm text-muted-foreground">
                    <IconTile icon={iconByName(c.icon)} />
                    <span className="flex-1 truncate">{c.name}</span>
                    <Button size="sm" variant="ghost" onClick={() => patch.mutate({ id: c.id, patch: { version: c.version, is_archived: false } })}>
                      <ArchiveRestore /> Вернуть
                    </Button>
                  </li>
                ))}
            </ul>
          )}
        </div>
      )}
      <Sheet open={!!editing} onOpenChange={(v) => !v && setEdit(null)} title={editing ? `Категория «${editing.name}»` : ''}>
        {editing && <CategoryEditor key={editing.id} cat={editing} onDone={() => setEdit(null)} />}
      </Sheet>
      <Sheet open={create} onOpenChange={setCreate} title="Новая категория">
        {create && <CreateCategory onDone={() => setCreate(false)} />}
      </Sheet>
      {merge && <MergeDialog source={merge} cats={live} onClose={() => setMerge(null)} />}
    </SettingsSection>
  )
}

// «?» — keyboard cheat sheet (DESIGN §11).
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { Kbd } from '@/components/pf/Kbd'

const ROWS: [string, string][] = [
  ['Ctrl K', 'AI-строка / командная палитра'],
  ['N', 'Новая операция вручную'],
  ['/', 'Поиск операций'],
  ['G H · G O · G A · G B · G S', 'Главная · Операции · Аналитика · Счета · Настройки'],
  ['[', 'Свернуть боковую панель'],
  ['Enter · Shift Enter', 'Отправить · новая строка'],
  ['↑', 'Вернуть последний отправленный текст'],
  ['Ctrl Z · Ctrl E', 'Отменить · изменить (поле пустое)'],
  ['1–9', 'Выбрать счёт в карточке'],
  ['Esc', 'Очистить поле / закрыть'],
]

export function ShortcutsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Клавиши</DialogTitle>
          <DialogDescription>Работают, когда фокус не в поле ввода (кроме Ctrl K и Esc).</DialogDescription>
        </DialogHeader>
        <dl className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2 text-small">
          {ROWS.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="flex flex-wrap gap-1">{k.split(' · ').map((x) => <Kbd key={x}>{x}</Kbd>)}</dt>
              <dd className="text-muted-foreground">{v}</dd>
            </div>
          ))}
        </dl>
      </DialogContent>
    </Dialog>
  )
}

// «Добавить» container. Desktop — right panel 400 px (Sheet) with the manual form.
// Mobile — bottom drawer (DESIGN §10.3): a tap on «+» opens it in AI mode (AI line focused, the result
// card right here, «Заполнить вручную» switches to the manual form 9.7); «Вручную» from a card or a
// long tap opens the manual form directly.
import { PencilLine } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from '@/components/ui/drawer'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { AiCommandBar } from '@/features/assistant/AiCommandBar'
import { CardStack } from '@/features/assistant/CardStack'
import { ManualTxForm, type ManualPrefill } from '@/features/manual/ManualTxForm'
import { useIsMobile } from '@/hooks/useMediaQuery'

export type AddMode = 'ai' | 'manual'

interface Props {
  open: boolean
  onOpenChange: (v: boolean) => void
  prefill?: ManualPrefill
  mode?: AddMode
  onModeChange?: (m: AddMode) => void
}

export function ManualSheet({ open, onOpenChange, prefill, mode = 'manual', onModeChange }: Props) {
  const mobile = useIsMobile()
  const form = <ManualTxForm key={JSON.stringify(prefill ?? {})} prefill={prefill} onSaved={() => onOpenChange(false)} />
  if (mobile) {
    const ai = mode === 'ai'
    return (
      <Drawer open={open} onOpenChange={onOpenChange}>
        <DrawerContent className="max-h-[92dvh] rounded-t-2xl" data-testid={ai ? 'add-sheet-ai' : 'add-sheet-manual'}>
          <DrawerHeader className="text-left">
            <DrawerTitle>{ai ? 'Записать' : 'Новая операция'}</DrawerTitle>
            <DrawerDescription className="sr-only">{ai ? 'Словами: сумма и на что' : 'Ручной ввод без AI'}</DrawerDescription>
          </DrawerHeader>
          <div className="overflow-y-auto px-4 pb-[max(16px,env(safe-area-inset-bottom))]">
            {ai ? (
              <div className="flex flex-col gap-3">
                <AiCommandBar variant="mobile" autoFocus primary={false} />
                <CardStack max={1} compact />
                <Button type="button" variant="outline" className="h-11" onClick={() => onModeChange?.('manual')} data-testid="add-sheet-manual-btn">
                  <PencilLine /> Заполнить вручную
                </Button>
              </div>
            ) : form}
          </div>
        </DrawerContent>
      </Drawer>
    )
  }
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-[400px] max-w-[100vw] overflow-y-auto sm:max-w-[400px]">
        <SheetHeader>
          <SheetTitle>Новая операция</SheetTitle>
          <SheetDescription>Без AI: сумма, категория, счёт</SheetDescription>
        </SheetHeader>
        <div className="px-4 pb-4">{form}</div>
      </SheetContent>
    </Sheet>
  )
}

// Toast with «Отменить» / «Вернуть» (DESIGN §9.7: «Удалено: 45 000 ₫ · Кофе [Вернуть]»).
import { newIdempotencyKey } from '@/lib/api'
import { errorMessage, useUndo } from '../api'
import { notify } from '../ui/overlays'

export function useUndoToast() {
  const undo = useUndo()
  const show = (message: string, actionId: string | null | undefined, label = 'Отменить') => {
    if (!actionId) {
      notify.info(message)
      return
    }
    const key = newIdempotencyKey()
    notify.info(message, {
      action: {
        label,
        onClick: () =>
          undo.mutate(
            { actionId, key },
            {
              onSuccess: (r) => {
                const redoId = r.undo_action_id
                notify.info(r.summary || 'Отменено', redoId ? { action: { label: 'Вернуть', onClick: () => undo.mutate({ actionId: redoId, key: newIdempotencyKey() }, { onError: (e) => notify.error(errorMessage(e, 'Не удалось вернуть')) }) } } : {})
              },
              onError: (e) => notify.error(errorMessage(e, 'Не удалось отменить')),
            },
          ),
      },
    })
  }
  return show
}

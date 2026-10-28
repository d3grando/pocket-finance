// Shell-level actions any page can call: open the manual form / the add sheet, the command palette.
import { createContext, useContext } from 'react'
import type { ManualPrefill } from '@/features/manual/ManualTxForm'

export interface ShellActions {
  openManual: (prefill?: ManualPrefill) => void
  /** mobile «+»: the add sheet with the AI line (DESIGN §10.3) */
  openAdd: () => void
  openPalette: () => void
  openShortcuts: () => void
}

export const ShellContext = createContext<ShellActions>({ openManual: () => {}, openAdd: () => {}, openPalette: () => {}, openShortcuts: () => {} })
export const useShell = () => useContext(ShellContext)

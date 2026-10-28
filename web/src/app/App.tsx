// Providers: TanStack Query, tooltips, toasts (sonner), router. Theme/SSE/auth live in
// main.tsx (boot), AppShell (SSE, auth gate) and the Quick page.
import { useMemo } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from 'react-router'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useIsMobile } from '@/hooks/useMediaQuery'
import { queryClient } from '@/lib/api/queryClient'
import { createAppRouter } from '@/app/routes'

export function App() {
  const router = useMemo(() => createAppRouter(), [])
  const mobile = useIsMobile()
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={300}>
        <RouterProvider router={router} />
        <Toaster
          position={mobile ? 'bottom-center' : 'bottom-right'}
          duration={6000}
          mobileOffset={{ bottom: 'calc(80px + env(safe-area-inset-bottom))' }}
          toastOptions={{ className: 'text-small' }}
        />
      </TooltipProvider>
    </QueryClientProvider>
  )
}

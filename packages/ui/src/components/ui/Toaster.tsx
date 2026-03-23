import { Toaster as SonnerToaster } from 'sonner'
import { useThemeStore } from '../../store/theme'

export function Toaster(): JSX.Element {
  const resolved = useThemeStore((s) => s.resolved)
  return <SonnerToaster position="bottom-right" richColors closeButton theme={resolved} />
}

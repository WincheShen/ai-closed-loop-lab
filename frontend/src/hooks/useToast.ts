import { useState, useCallback } from 'react'

export type ToastType = 'success' | 'error'

export interface Toast {
  id: number
  msg: string
  type: ToastType
}

export function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([])

  const show = useCallback((msg: string, type: ToastType = 'success') => {
    const id = Date.now()
    setToasts((prev) => [...prev, { id, msg, type }])
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, 3000)
  }, [])

  const remove = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  return { toasts, show, remove }
}

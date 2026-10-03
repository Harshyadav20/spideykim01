import React, { useCallback, useRef, useState } from 'react'

/**
 * Tiny toast queue: notify(message, type) from anywhere, rendered by <ToastStack/>.
 * Types: 'info' | 'success' | 'error'.
 */
export function useToasts(ttl = 5200) {
  const [toasts, setToasts] = useState([])
  const seq = useRef(0)

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((t) => t.id !== id))
  }, [])

  const notify = useCallback((message, type = 'info') => {
    if (!message) return
    const id = ++seq.current
    setToasts((list) => [...list.slice(-3), { id, message: String(message), type }])
    window.setTimeout(() => dismiss(id), type === 'error' ? ttl * 1.8 : ttl)
  }, [dismiss, ttl])

  return { toasts, notify, dismiss }
}

export function ToastStack({ toasts, onDismiss }) {
  if (!toasts.length) return null
  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.type}`}>
          <span aria-hidden="true">
            {t.type === 'success' ? '✅' : t.type === 'error' ? '⚠️' : '✨'}
          </span>
          <span>{t.message}</span>
          <button className="toast-close" onClick={() => onDismiss(t.id)} aria-label="Dismiss">
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}

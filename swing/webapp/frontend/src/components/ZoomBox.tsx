// 缩放手势容器:wheel 用原生非被动监听器绑定(否则 preventDefault 无效、页面跟着滚)。迁自 trade_dashboard。
import { useEffect, useRef } from 'react'

export interface PointerHandlers {
  onPointerDown: (e: React.PointerEvent) => void
  onPointerMove: (e: React.PointerEvent) => void
  onPointerUp: (e: React.PointerEvent) => void
}

export function ZoomBox({ onWheel, pointer, children }: {
  onWheel: (e: WheelEvent) => void
  pointer: PointerHandlers
  children: React.ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [onWheel])
  return (
    <div ref={ref} style={{ cursor: 'ew-resize', userSelect: 'none', touchAction: 'none' }} {...pointer}>
      {children}
    </div>
  )
}

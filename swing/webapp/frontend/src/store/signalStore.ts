// ML 信号页的共享状态(zustand):训练参数 / 组合份数 / payload / 训练动作。
import { create } from 'zustand'
import { message } from 'antd'
import { TrainPayloadSchema, type TrainPayload } from '../lib/schema'

interface Params { mode: string; tier: number; start: string; train: boolean }

interface SignalState {
  params: Params
  setParams: (p: Partial<Params>) => void
  parts: number
  setParts: (n: number) => void
  payload: TrainPayload | null
  loading: boolean
  train: (extra?: Record<string, unknown>) => Promise<void>
}

export const useSignalStore = create<SignalState>((set, get) => ({
  params: { mode: 'long', tier: 5, start: '20260101', train: false },
  setParams: (p) => set(s => ({ params: { ...s.params, ...p } })),
  parts: 4,
  setParts: (n) => set({ parts: n }),
  payload: null,
  loading: false,
  train: async (extra = {}) => {
    set({ loading: true, payload: null })
    try {
      const r = await fetch('/api/train', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...get().params, ...extra }),
      })
      const j = await r.json()
      if (!j.ok) { message.error('训练失败: ' + (j.error || '')); return }
      const pr = TrainPayloadSchema.safeParse(j)      // zod 软校验:失败则退回原始 j,不阻断
      const p = (pr.success ? pr.data : j) as TrainPayload
      p.signals.forEach((s: Record<string, unknown>) => { s.__latest = p.latest })
      set({ payload: p })
      message.success(`${p.cached ? '已加载缓存' : '完成'},共 ${p.signals.length} 条信号`)
    } catch (e) {
      message.error('请求失败,后端起了吗? ' + e)
    } finally {
      set({ loading: false })
    }
  },
}))

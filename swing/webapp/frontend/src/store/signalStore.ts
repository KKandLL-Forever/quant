// ML 信号页的共享状态(zustand):训练参数 / 组合份数 / payload / 训练动作。
import { createElement as h } from 'react'
import { create } from 'zustand'
import { message, notification } from 'antd'
import { TrainPayloadSchema, type TrainPayload } from '../lib/schema'

const _stat = (label: string, value: unknown, color = '#1f2733') =>
  h('div', { key: label, style: { background: '#f6f8fa', borderRadius: 8, padding: '6px 10px' } },
    h('div', { style: { fontSize: 11, color: '#8a94a6', marginBottom: 2 } }, label),
    h('div', { style: { fontSize: 17, fontWeight: 700, color, lineHeight: 1.1 } }, value == null ? '—' : String(value)))

// gap 偏高时区分成因:分子冲高(训练AUC≈1,疑特征泄露)/ 验证样本过少(高方差)/ 分母走低(验证段regime漂移,市场非平稳)
function _gapVerdict(tr: number | null, va: number | null, gap: number | null, nVal: number | null) {
  if (gap == null || gap <= 0.15) return h('div', { style: { marginTop: 8, fontSize: 12, color: '#0b8a4b' } }, '✓ 训练-验证差距健康')
  if (tr != null && tr >= 0.95)
    return h('div', { style: { marginTop: 8, fontSize: 12, color: '#c0392b' } }, `⚠️ 训练AUC≈${tr} 异常高 → 疑特征泄露/记忆,优先查特征与切分`)
  if (nVal != null && nVal < 500)
    return h('div', { style: { marginTop: 8, fontSize: 12, color: '#d48806' } }, `⚠️ 验证样本仅 ${nVal},gap 高方差、参考性弱,别据此改模型`)
  return h('div', { style: { marginTop: 8, fontSize: 12, color: '#d48806' } },
    '⚠️ gap 偏高多因验证段 regime 漂移(valAUC 走低、非训练AUC冲高)→ 属市场非平稳,当前信号泛化打折宜减仓;通常随新数据入训自愈,不必急改容量/正则')
}

function _trainDesc(mt: Record<string, unknown>) {
  const tr = (mt.tr_auc as number | null) ?? null
  const auc = mt.va_auc as number | null
  const gap = mt.gap as number | null
  const nVal = typeof mt.n_val === 'number' ? mt.n_val : null
  const aucColor = auc == null ? '#1f2733' : auc >= 0.62 ? '#0b8a4b' : auc >= 0.56 ? '#d48806' : '#c0392b'
  const gapColor = gap == null ? '#1f2733' : gap <= 0.1 ? '#0b8a4b' : gap <= 0.15 ? '#d48806' : '#c0392b'
  const trColor = tr != null && tr >= 0.95 ? '#c0392b' : '#1f2733'
  return h('div', {},
    h('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginTop: 6 } },
      _stat('训练 AUC', mt.tr_auc, trColor),
      _stat('验证 AUC', mt.va_auc, aucColor),
      _stat('过拟合 gap', gap == null ? null : (gap > 0 ? '+' : '') + gap, gapColor)),
    h('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginTop: 8 } },
      _stat('best_iter', mt.best_iter),
      _stat('训练样本', typeof mt.n_train === 'number' ? mt.n_train.toLocaleString() : mt.n_train),
      _stat('val 样本', typeof mt.n_val === 'number' ? mt.n_val.toLocaleString() : mt.n_val)),
    _gapVerdict(tr, auc ?? null, gap, nVal))
}

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
  params: { mode: 'long', tier: 20, start: '20260529', train: false },
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
      const mt = (p as { metrics?: Record<string, unknown> }).metrics
      if (!p.cached && mt && mt.loaded === false) {
        const gap = mt.gap as number | null
        notification.open({
          type: gap != null && gap > 0.15 ? 'warning' : 'success',
          message: h('span', { style: { fontWeight: 700 } }, '🧬 模型训练完成'),
          duration: null, placement: 'topRight', style: { width: 420 },
          description: _trainDesc(mt),
        })
      }
    } catch (e) {
      message.error('请求失败,后端起了吗? ' + e)
    } finally {
      set({ loading: false })
    }
  },
}))

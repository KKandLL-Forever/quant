// 情绪温度 tab:最高连板 × 手工情绪温度双轴图 + 三种录入方式(最新日滑杆 / 点图回填 / 表格逐日 / 批量粘贴)。温度存后端 first10/mood_temp.json。
import { useEffect, useMemo, useRef, useState } from 'react'
import { Card, Table, Slider, InputNumber, Button, Modal, Input, Spin, message, Tag } from 'antd'
import { MoodChart } from './MoodChart'
import type { DayBoard } from './BoardLineChart'

const WINDOWS = [{ label: '近1月', n: 22 }, { label: '近3月', n: 60 }, { label: '近半年', n: 120 }, { label: '近1年', n: 250 }, { label: '全部', n: 0 }]
const MARKS = { 0: '冰点', 25: '退潮', 50: '分歧', 75: '回暖', 100: '亢奋' }
const tempColor = (t: number) => (t >= 75 ? '#c0392b' : t >= 55 ? '#e8913a' : t >= 35 ? '#b8860b' : t >= 15 ? '#7fd4d0' : '#5b9bd5')

async function post(url: string, body: unknown) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const j = await r.json()
  if (!j.ok) throw new Error(j.error || '请求失败')
  return j
}

// 批量粘贴:每行「日期 温度」,日期允许 20260825 / 2026-08-25 / 08-25(按当前年补全),分隔符空格逗号制表符皆可。
function parseBulk(text: string, fallbackYear: string) {
  const items: Record<string, number> = {}
  const bad: string[] = []
  text.split(/\r?\n/).forEach(raw => {
    const line = raw.trim()
    if (!line) return
    const m = line.split(/[\s,，\t]+/).filter(Boolean)
    if (m.length < 2) { bad.push(line); return }
    let d = m[0].replace(/[-/.]/g, '')
    if (d.length === 4) d = fallbackYear + d
    const v = Number(m[1].replace(/[°度]/g, ''))
    if (!/^\d{8}$/.test(d) || !Number.isFinite(v) || v < 0 || v > 100) { bad.push(line); return }
    items[d] = v
  })
  return { items, bad }
}

export function MoodPanel() {
  const [days, setDays] = useState<DayBoard[]>([])
  const [temps, setTemps] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(false)
  const [win, setWin] = useState(60)
  const [draft, setDraft] = useState<number | null>(null)
  const [pick, setPick] = useState<string>('')
  const [bulkOpen, setBulkOpen] = useState(false)
  const [bulkText, setBulkText] = useState('')
  const pending = useRef<Record<string, number | null>>({})
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    (async () => {
      setLoading(true)
      try {
        const [b, m] = await Promise.all([post('/api/boardcal', { start: '20240101' }), post('/api/mood_temp', {})])
        setDays(b.days); setTemps(m.items)
        const last = b.days[b.days.length - 1]?.date || ''
        setPick(last); setDraft(m.items[last] ?? null)
      } catch (e) { message.error((e as Error).message) } finally { setLoading(false) }
    })()
  }, [])

  // 逐日录入不逐次打后端:攒 600ms 再一把合并写入
  const queue = (date: string, v: number | null) => {
    setTemps(t => { const n = { ...t }; if (v == null) delete n[date]; else n[date] = v; return n })
    pending.current[date] = v
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      const items = pending.current
      pending.current = {}
      try { await post('/api/mood_temp_save', { items }) } catch (e) { message.error('保存失败:' + (e as Error).message) }
    }, 600)
  }

  const shown = useMemo(() => (win ? days.slice(-win) : days), [days, win])
  const rows = useMemo(() => [...shown].reverse(), [shown])
  const filled = shown.filter(d => temps[d.date] != null).length

  const cols = [
    { title: '日期', dataIndex: 'date', width: 110 },
    { title: '最高板', dataIndex: 'maxBoard', width: 80, align: 'right' as const, render: (v: number) => <b>{v}板</b> },
    { title: '龙头', dataIndex: 'topName', width: 110, render: (v: string) => v || '—' },
    { title: '涨停数', dataIndex: 'total', width: 80, align: 'right' as const },
    {
      title: '情绪温度', dataIndex: 'date', width: 160,
      render: (d: string) => (
        <InputNumber size="small" min={0} max={100} step={5} style={{ width: 120 }} placeholder="未填"
          value={temps[d] ?? null} onChange={v => queue(d, v == null ? null : Number(v))}
          addonAfter={<span style={{ color: temps[d] == null ? 'var(--ink-soft)' : tempColor(temps[d]) }}>°</span>} />
      ),
    },
  ]

  if (loading) return <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>
  if (!days.length) return null

  return (
    <div>
      <Card size="small" style={{ marginBottom: 12 }}
        title={<span>录入情绪温度 · <b style={{ color: 'var(--accent)' }}>{pick || '—'}</b></span>}
        extra={<Button size="small" onClick={() => setBulkOpen(true)}>批量粘贴</Button>}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
          <div style={{ flex: 1, padding: '0 8px' }}>
            <Slider min={0} max={100} step={5} marks={MARKS} value={draft ?? 0} onChange={v => setDraft(v)} />
          </div>
          <InputNumber min={0} max={100} step={5} style={{ width: 96 }} value={draft} onChange={v => setDraft(v == null ? null : Number(v))} />
          <Button type="primary" disabled={!pick || draft == null} onClick={() => { queue(pick, draft); message.success(`${pick} → ${draft}°`) }}>保存</Button>
          <Button disabled={!pick || temps[pick] == null} onClick={() => { queue(pick, null); setDraft(null) }}>清除</Button>
        </div>
        <div style={{ fontSize: 12, color: 'var(--ink-soft)', marginTop: 10 }}>
          温度纯手工判断,无接口无算法 · <b>点击下方图上任一点</b>可切到该日回填 · 表格里也能逐日直接改(自动保存)
        </div>
      </Card>

      <Card size="small" title="最高连板趋势 × 短线情绪温度"
        extra={
          <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <Tag color={filled === shown.length ? 'green' : 'orange'}>已填 {filled}/{shown.length} 日</Tag>
            {WINDOWS.map(w => (
              <Button key={w.label} size="small" type={win === w.n ? 'primary' : 'default'} onClick={() => setWin(w.n)}>{w.label}</Button>
            ))}
          </span>
        }>
        <MoodChart days={shown} temps={temps} onPick={d => { setPick(d); setDraft(temps[d] ?? null) }} />
        <div style={{ fontSize: 12, color: 'var(--ink-soft)', marginTop: 6 }}>
          红实线=当日最高连板数(左轴,3板以上标龙头名) · 虚线=情绪温度(右轴 0-100,冷蓝→热红) · 未填的日子断开不连
        </div>
      </Card>

      <Card size="small" title="逐日录入" style={{ marginTop: 12 }}>
        <Table rowKey="date" columns={cols} dataSource={rows} size="small" pagination={{ pageSize: 20, showSizeChanger: false }} />
      </Card>

      <Modal title="批量粘贴情绪温度" open={bulkOpen} onCancel={() => setBulkOpen(false)} width={520}
        onOk={async () => {
          const { items, bad } = parseBulk(bulkText, (days[days.length - 1]?.date || '2026').slice(0, 4))
          const n = Object.keys(items).length
          if (!n) { message.error('没解析出有效行'); return }
          try {
            await post('/api/mood_temp_save', { items })
            setTemps(t => ({ ...t, ...items }))
            setBulkOpen(false); setBulkText('')
            message.success(`已写入 ${n} 天${bad.length ? `,${bad.length} 行未识别` : ''}`)
          } catch (e) { message.error((e as Error).message) }
        }}>
        <div style={{ fontSize: 12, color: 'var(--ink-soft)', marginBottom: 8 }}>
          每行一天,「日期 温度」。日期支持 20260825 / 2026-08-25 / 08-25(补当年),分隔符空格或逗号都行。已有的会被覆盖。
        </div>
        <Input.TextArea rows={12} value={bulkText} onChange={e => setBulkText(e.target.value)}
          placeholder={'20260821 30\n20260822 40\n08-25 20'} />
      </Modal>
    </div>
  )
}

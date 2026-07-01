// 涨停统计:连板梯队 / 连板成功率(按月) / 连板日历,三内嵌 tab。数据走后端(DuckDB limit_list_d)。
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Input, Button, Card, Table, Spin, Tabs, message } from 'antd'
import { Header, PageTitle } from '../../shell'
import { fetchLimitup, buildLadder, calcSuccessRates, type LimitStock, type SuccessRateRow } from '../../lib/limitUp'
import { useDateZoom, clipByRange, decimate } from '../../lib/useDateZoom'
import { ZoomBox } from '../../components/ZoomBox'
import { BoardLineChart, type DayBoard } from './BoardLineChart'
import { BoardGrid } from './BoardGrid'

const boardColor = (d: number) => (d >= 5 ? '#c0392b' : d >= 3 ? '#e07b39' : '#0b6e4f')

// ── 连板梯队 + 连板成功率(共用一次区间拉取)──
function LadderAndRate() {
  const [start, setStart] = useState('20250101')
  const [data, setData] = useState<{ dates: string[]; byDate: Map<string, LimitStock[]> } | null>(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true); setData(null)
    try { setData(await fetchLimitup(start)) }
    catch (e) { message.error((e as Error).message) }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  const latest = data?.dates[data.dates.length - 1]
  const ladder = data && latest ? buildLadder(latest, data.byDate.get(latest) || []) : null
  const maxBoard = ladder?.groups[0]?.days ?? 0

  // 按月成功率
  const months: Record<string, string[]> = {}
  ;(data?.dates || []).forEach(d => { const m = d.slice(0, 7); (months[m] = months[m] || []).push(d) })
  const mkeys = Object.keys(months).sort()
  const monthRows = mkeys.map((m, i) => {
    const seq = mkeys[i + 1] ? [...months[m], months[mkeys[i + 1]][0]] : months[m]
    const byBoard: Record<number, SuccessRateRow> = {}
    calcSuccessRates(seq, data!.byDate).forEach(r => { byBoard[r.days] = r })
    const cell = (n: number) => byBoard[n] ? `${(byBoard[n].rate * 100).toFixed(0)}% (${byBoard[n].successes}/${byBoard[n].attempts})` : '—'
    return { month: m, r12: cell(1), r23: cell(2), r34: cell(3), r45: cell(4) }
  }).reverse()

  const rateCols = [
    { title: '月份', dataIndex: 'month' },
    { title: '1→2板', dataIndex: 'r12' },
    { title: '2→3板', dataIndex: 'r23' },
    { title: '3→4板', dataIndex: 'r34' },
    { title: '4→5板', dataIndex: 'r45' },
  ]

  return (
    <>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', margin: '12px 0' }}>
        <span>起始</span><Input style={{ width: 120 }} value={start} onChange={e => setStart(e.target.value.trim())} onPressEnter={load} />
        <Button type="primary" loading={loading} onClick={load}>加载</Button>
        {data && <span style={{ fontSize: 13, color: 'var(--ink-soft)' }}>区间 {data.dates[0]} ~ {latest} · {data.dates.length} 个交易日</span>}
      </div>
      {loading && <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>}
      {ladder && (
        <Tabs items={[
          {
            key: 'ladder', label: '连板梯队', children: (
              <>
                <div style={{ fontSize: 14, margin: '4px 0 12px' }}>
                  最新交易日 <b>{latest}</b>:涨停 <b>{ladder.total}</b> 只 · 最高 <b style={{ color: boardColor(maxBoard) }}>{maxBoard} 板</b>
                </div>
                <Card size="small" title="连板梯队(高板在上)">
                  {ladder.groups.map(g => (
                    <div key={g.days} style={{ display: 'flex', gap: 10, padding: '6px 0', borderTop: '1px solid var(--line)' }}>
                      <div style={{ minWidth: 54, fontWeight: 700, color: boardColor(g.days) }}>{g.days}板<span style={{ color: 'var(--ink-soft)', fontWeight: 400, fontSize: 12 }}> ×{g.stocks.length}</span></div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {g.stocks.map(s => (
                          <span key={s.tsCode} className="hold-chip" title={`${s.industry} 封单${(s.fdAmount / 1e8).toFixed(2)}亿 炸板${s.openTimes}次`}>
                            {s.name}{s.openTimes > 0 && <span className="hold-tag" style={{ background: '#e07b39' }}>炸{s.openTimes}</span>}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </Card>
              </>
            ),
          },
          {
            key: 'rate', label: '连板成功率', children: (
              <Card size="small" title="各月晋级成功率(今日第N板 → 次日仍涨停)">
                <Table rowKey="month" columns={rateCols} dataSource={monthRows} size="small" pagination={false} />
              </Card>
            ),
          },
        ]} />
      )}
    </>
  )
}

// ── 连板日历(滚轮缩放 + 拖动平移,折线与龙头列共用同一缩放窗口)──
function BoardCalendar() {
  const [days, setDays] = useState<DayBoard[]>([])
  const [loading, setLoading] = useState(false)
  const load = async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/boardcal', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ start: '20200101' }) })
      const j = await r.json()
      if (!j.ok) throw new Error(j.error)
      setDays(j.days)
    } catch (e) { message.error((e as Error).message) } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  const masterDates = useMemo(() => days.map(d => d.date), [days])
  const { range, onWheel, pointerHandlers, reset, focusDate, isZoomed } = useDateZoom(masterDates)
  const data = useMemo(() => decimate(clipByRange(days, d => d.date, range), 400), [days, range])

  const boxRef = useRef<HTMLDivElement>(null)
  const [plotWidth, setPlotWidth] = useState(1200)
  useLayoutEffect(() => {
    const el = boxRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setPlotWidth(el.clientWidth))
    ro.observe(el); setPlotWidth(el.clientWidth)
    return () => ro.disconnect()
  }, [])
  const colW = Math.max(8, (plotWidth - 36 - 16) / Math.max(1, data.length))

  return (
    <div style={{ marginTop: 12 }}>
      {loading && <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>}
      {days.length > 0 && (
        <Card size="small">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 12, color: 'var(--ink-soft)', marginBottom: 6 }}>
            <span>{days.length} 个交易日 · 红=最高板、紫=次高板;底色=牛市区间 · 每日 6板+龙头</span>
            <span style={{ color: 'var(--accent)' }}>滚轮缩放 · 拖动平移</span>
            {isZoomed && <a onClick={reset} style={{ color: 'var(--accent)' }}>重置缩放</a>}
            {range && <span style={{ marginLeft: 'auto' }}>{range.start} ~ {range.end}</span>}
          </div>
          <div ref={boxRef}>
            <ZoomBox onWheel={onWheel} pointer={pointerHandlers}>
              <BoardLineChart data={data} />
              <div style={{ marginTop: 8 }}><BoardGrid data={data} colW={colW} onSelectDate={focusDate} /></div>
            </ZoomBox>
          </div>
        </Card>
      )}
    </div>
  )
}

export default function LimitUpPage() {
  return (
    <div style={{ maxWidth: 1850, margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="Limit-up Ladder / Success-rate / Calendar">涨停统计</PageTitle>
      <Tabs size="large" items={[
        { key: 'lr', label: '梯队 & 成功率', children: <LadderAndRate /> },
        { key: 'cal', label: '连板日历', children: <BoardCalendar /> },
      ]} />
    </div>
  )
}

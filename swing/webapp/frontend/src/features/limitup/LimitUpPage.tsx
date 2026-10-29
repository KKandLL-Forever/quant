// 涨停统计:连板梯队(可选交易日) / 连板成功率 / 连板日历 / 情绪温度 四同级 tab。数据走后端(DuckDB limit_list_d);情绪温度为手工录入,存后端 json。
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Input, Button, Card, Table, Spin, Tabs, Collapse, Progress, Select, message } from 'antd'
import { Header, PageTitle, SkelTable } from '../../shell'
import { StockName } from '../../StockInfo'
import { useAna } from '../../App'
import { fetchLimitup, buildLadder, calcSuccessRates, type LimitStock, type SuccessRateRow } from '../../lib/limitUp'
import { useDateZoom, clipByRange, decimate } from '../../lib/useDateZoom'
import { ZoomBox } from '../../components/ZoomBox'
import { BoardLineChart, type DayBoard } from './BoardLineChart'
import { BoardGrid } from './BoardGrid'
import { MoodPanel } from './MoodPanel'

const boardColor = (d: number) => (d >= 5 ? '#c0392b' : d >= 3 ? '#e07b39' : '#0b6e4f')
const rateColor = (r: number) => (r >= 0.6 ? '#c0392b' : r >= 0.4 ? '#e07b39' : r >= 0.2 ? '#b8860b' : '#5b554a')
const MONTHS = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月']
type RangeData = { dates: string[]; byDate: Map<string, LimitStock[]> }

// ── 连板梯队(可选交易日,默认区间内最新一天)──
function LadderView({ data }: { data: RangeData }) {
  const [day, setDay] = useState('')
  const [areaSel, setAreaSel] = useState<string[] | null>(null) // null=全部选上
  const dates = data.dates
  const cur = day && dates.includes(day) ? day : dates[dates.length - 1]
  const idx = dates.indexOf(cur)
  const dayStocks = data.byDate.get(cur) || []
  // 当日各地域及只数,只数多的在前
  const areaCounts = new Map<string, number>()
  for (const s of dayStocks) { const a = s.area || '未知'; areaCounts.set(a, (areaCounts.get(a) || 0) + 1) }
  const areaList = [...areaCounts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'zh'))
  const activeAreas = areaSel === null ? areaList.map(([a]) => a) : areaSel.filter(a => areaCounts.has(a))
  const ladder = buildLadder(cur, dayStocks.filter(s => activeAreas.includes(s.area || '未知')))
  const maxBoard = ladder.groups[0]?.days ?? 0
  const analyze = useAna()
  // 点选切换:默认全部选上,点已选中的地域即取消该地域
  const toggleArea = (a: string) => {
    if (activeAreas.includes(a)) setAreaSel(activeAreas.filter(x => x !== a))
    else setAreaSel([...activeAreas, a])
  }
  const opts = useMemo(() => [...dates].reverse().map(d => ({
    value: d, label: `${d}　${(data.byDate.get(d) || []).length}只`,
  })), [dates, data])
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, margin: '4px 0 10px', flexWrap: 'wrap' }}>
        <span>交易日</span>
        <Select showSearch style={{ width: 190 }} value={cur} options={opts} onChange={setDay}
          filterOption={(input, o) => String(o?.value || '').includes(input.replace(/[-/]/g, ''))} />
        <Button size="small" disabled={idx <= 0} onClick={() => setDay(dates[idx - 1])}>← 前一日</Button>
        <Button size="small" disabled={idx < 0 || idx >= dates.length - 1} onClick={() => setDay(dates[idx + 1])}>后一日 →</Button>
        {idx !== dates.length - 1 && <Button size="small" type="link" onClick={() => setDay('')}>回到最新</Button>}
        <span style={{ marginLeft: 4 }}>
          涨停 <b>{ladder.total}</b> 只 · 最高 <b style={{ color: boardColor(maxBoard) }}>{maxBoard} 板</b>
          {ladder.total !== dayStocks.length && <span style={{ color: 'var(--ink-soft)' }}>(当日共 {dayStocks.length} 只)</span>}
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 14, margin: '0 0 12px', flexWrap: 'wrap' }}>
        <span>地域</span>
        {areaList.map(([a, n]) => (
          <Button key={a} size="small" type={activeAreas.includes(a) ? 'primary' : 'default'}
            onClick={() => toggleArea(a)}>{a}<span style={{ marginLeft: 4, fontSize: 11, opacity: .65 }}>{n}</span></Button>
        ))}
        {!areaList.length && <span style={{ fontSize: 12, color: 'var(--ink-soft)' }}>当日无涨停</span>}
      </div>
      <Card size="small" title="连板梯队(高板在上)">
        {dayStocks.length > 0 && !ladder.groups.length && <div style={{ fontSize: 13, color: 'var(--ink-soft)', padding: '6px 0' }}>未选中任何地域</div>}
        {ladder.groups.map(g => (
          <div key={g.days} style={{ display: 'flex', gap: 10, padding: '6px 0', borderTop: '1px solid var(--line)' }}>
            <div style={{ minWidth: 54, fontWeight: 700, color: boardColor(g.days) }}>{g.days}板<span style={{ color: 'var(--ink-soft)', fontWeight: 400, fontSize: 12 }}> ×{g.stocks.length}</span></div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {g.stocks.map(s => (
                <StockName key={s.tsCode} code={s.tsCode}>
                  <span className="hold-chip" style={{ cursor: 'pointer' }} title={`${s.area || '-'} · ${s.industry} 封单${(s.fdAmount / 1e8).toFixed(2)}亿 炸板${s.openTimes}次 · 点击跑 LLM 分析`}
                    onClick={() => analyze(s.tsCode, cur, false, s.name)}>
                    {s.name}
                    {s.area && <span className="hold-tag" style={{ background: '#9b958a' }}>{s.area}</span>}
                    {s.openTimes > 0 && <span className="hold-tag" style={{ background: '#e07b39' }}>炸{s.openTimes}</span>}
                  </span>
                </StockName>
              ))}
            </div>
          </div>
        ))}
      </Card>
    </>
  )
}

// ── 连板成功率:年份卡 + 月份chip,点月份看该月分板晋级成功率(带进度条)──
function RateView({ data }: { data: RangeData }) {
  const months: Record<string, string[]> = {}
  data.dates.forEach(d => { const m = d.slice(0, 7); (months[m] = months[m] || []).push(d) })
  const mkeys = Object.keys(months).sort()          // 'YYYY-MM' 升序
  const [sel, setSel] = useState(mkeys[mkeys.length - 1] || '')

  const rows: SuccessRateRow[] = useMemo(() => {
    if (!sel) return []
    const i = mkeys.indexOf(sel)
    const seq = mkeys[i + 1] ? [...months[sel], months[mkeys[i + 1]][0]] : months[sel]
    return calcSuccessRates(seq, data.byDate)
  }, [sel]) // eslint-disable-line react-hooks/exhaustive-deps

  const years = Array.from(new Set(mkeys.map(m => m.slice(0, 4)))).sort().reverse()
  const cols = [
    { title: '板数', dataIndex: 'days', render: (d: number) => <span style={{ fontWeight: 600 }}>{d === 1 ? '首板' : `${d}连板`}<span style={{ color: 'var(--ink-soft)', fontWeight: 400, fontSize: 12 }}> → {d + 1}连板</span></span> },
    { title: '尝试', dataIndex: 'attempts', align: 'right' as const },
    { title: '晋级', dataIndex: 'successes', align: 'right' as const },
    { title: '成功率', dataIndex: 'rate', align: 'right' as const, render: (v: number) => <b style={{ color: rateColor(v) }}>{(v * 100).toFixed(1)}%</b> },
    { title: '可视化', dataIndex: 'rate', width: 180, render: (v: number) => <Progress percent={Math.round(v * 100)} size="small" strokeColor="#c0392b" /> },
  ]

  const collapseItems = years.map(y => {
    const ms = mkeys.filter(m => m.startsWith(y))
    return {
      key: y, label: <span><b>{y} 年</b> <span style={{ color: 'var(--ink-soft)', fontSize: 12 }}>{ms.length} 个月有数据</span></span>,
      children: (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {ms.map(m => {
            const mn = Number(m.slice(5, 7))
            const on = sel === m
            return (
              <button key={m} onClick={() => setSel(m)} style={{
                padding: '4px 12px', borderRadius: 6, cursor: 'pointer', fontSize: 13,
                border: on ? '1px solid var(--accent)' : '1px solid var(--line)',
                background: on ? 'var(--accent)' : 'var(--panel)', color: on ? '#fff' : 'var(--ink)', fontWeight: on ? 600 : 400,
              }}>{MONTHS[mn - 1]}</button>
            )
          })}
        </div>
      ),
    }
  })

  return (
    <div>
      <Collapse items={collapseItems} defaultActiveKey={years[0] ? [years[0]] : []} style={{ marginBottom: 12 }} />
      {sel && (
        <Card size="small" title={`${sel} 连板晋级成功率(今日第N板 → 次日仍涨停)`}>
          <Table rowKey="days" columns={cols} dataSource={rows} size="small" pagination={false} />
          <div style={{ fontSize: 12, color: 'var(--ink-soft)', marginTop: 8 }}>
            • 成功率 = 当日 N 连板、次个交易日依然封板的比例 · 月末最后交易日借用次月首日计算次日成功率
          </div>
        </Card>
      )}
    </div>
  )
}

// ── 连板日历(滚轮缩放 + 拖动平移,折线与龙头列共用同一缩放窗口)──
function BoardCalendar() {
  const [days, setDays] = useState<DayBoard[]>([])
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    (async () => {
      setLoading(true)
      try {
        const r = await fetch('/api/boardcal', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ start: '20200101' }) })
        const j = await r.json()
        if (!j.ok) throw new Error(j.error)
        setDays(j.days)
      } catch (e) { message.error((e as Error).message) } finally { setLoading(false) }
    })()
  }, [])

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

  if (loading) return <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>
  if (!days.length) return null
  return (
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
  )
}

export default function LimitUpPage() {
  const [start, setStart] = useState('20250101')
  const [data, setData] = useState<RangeData | null>(null)
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState('ladder')
  const load = async () => {
    setLoading(true); setData(null)
    try { setData(await fetchLimitup(start)) }
    catch (e) { message.error((e as Error).message) } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  return (
    <div style={{ maxWidth: 'min(2000px, 96vw)', margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="Limit-up Ladder / Success-rate / Calendar / Mood">涨停统计</PageTitle>

      {tab !== 'calendar' && tab !== 'mood' && (
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12 }}>
          <span>起始</span><Input style={{ width: 120 }} value={start} onChange={e => setStart(e.target.value.trim())} onPressEnter={load} />
          <Button type="primary" loading={loading} onClick={load}>加载</Button>
          {data && <span style={{ fontSize: 13, color: 'var(--ink-soft)' }}>区间 {data.dates[0]} ~ {data.dates[data.dates.length - 1]} · {data.dates.length} 个交易日</span>}
        </div>
      )}
      {loading && tab !== 'calendar' && tab !== 'mood' && <SkelTable rows={14} />}

      <Tabs size="large" activeKey={tab} onChange={setTab} items={[
        { key: 'ladder', label: '连板梯队', children: data ? <LadderView data={data} /> : null },
        { key: 'rate', label: '连板成功率', children: data ? <RateView data={data} /> : null },
        { key: 'calendar', label: '连板日历', children: <BoardCalendar /> },
        { key: 'mood', label: '情绪温度', children: <MoodPanel /> },
      ]} />
    </div>
  )
}

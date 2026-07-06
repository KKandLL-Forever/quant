// 牛市逃顶:全A整体法PE估值(+分位危险/机会线)、融资余额/流通、换手率、股东月度净减持。
// 数据走后端 /api/bulltop;前四图共用一个缩放窗口(滚轮缩放+拖动平移,联动)。
import { useEffect, useMemo, useState } from 'react'
import { Card, Spin, message } from 'antd'
import { ComposedChart, LineChart, Line, BarChart, Bar, ReferenceLine, ReferenceArea, XAxis, YAxis, Tooltip, CartesianGrid, Legend, ResponsiveContainer } from 'recharts'
import ReactECharts from 'echarts-for-react'
import { Header, PageTitle } from '../../shell'
import { useDateZoom, clipByRange, decimate } from '../../lib/useDateZoom'
import { ZoomBox } from '../../components/ZoomBox'

interface Val { date: string; peTtm: number; pct: number; circMv: number; totalMv: number; amountFull: number }
interface Tov { date: string; turnover: number; ma5: number | null }
interface Hld { month: string; netReduce: number }
interface Mgn { date: string; rzye: number; rzmre: number }
const BULLS = [
  { s: '20140701', e: '20150630', label: '杠杆牛', c: '#c0392b' },
  { s: '20190101', e: '20210228', label: '2019结构牛', c: '#8b5cf6' },
  { s: '20240924', e: '20991231', label: '2024行情', c: '#8b5cf6' },
]
const fmtDate = (d: string) => (d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}` : d)
const quantile = (xs: number[], p: number) => {
  if (!xs.length) return NaN
  const s = [...xs].sort((a, b) => a - b), i = p * (s.length - 1), lo = Math.floor(i), hi = Math.ceil(i)
  return lo === hi ? s[lo] : s[lo] + (s[hi] - s[lo]) * (i - lo)
}
const bullAreas = (dates: string[]) => BULLS.map(b => {
  const w = dates.filter(d => d >= b.s && d <= b.e)
  return w.length ? { x1: w[0], x2: w[w.length - 1], label: b.label, c: b.c } : null
}).filter((x): x is { x1: string; x2: string; label: string; c: string } => !!x)

const grid = <CartesianGrid strokeDasharray="3 3" stroke="#e6e0d3" />
const tip = { background: '#fffdf8', border: '1px solid #e6e0d3', borderRadius: 6, fontSize: 12 }

// echarts 试点:全A估值图(canvas 全量点不抽稀 + 原生 legend/dataZoom)
function EValuation({ data, danger, mid, opp }: { data: Val[]; danger: number; mid: number; opp: number }) {
  const dates = data.map(v => fmtDate(v.date))
  const flat = (y: number) => data.map(() => y)
  const nDanger = `危险 ${danger.toFixed(1)}`, nMid = `中位 ${mid.toFixed(1)}`, nOpp = `机会 ${opp.toFixed(1)}`
  const area = bullAreas(data.map(v => v.date)).map(b =>
    [{ xAxis: fmtDate(b.x1), itemStyle: { color: b.c, opacity: 0.07 }, name: b.label }, { xAxis: fmtDate(b.x2) }])
  const option = {
    animation: false,
    grid: { left: 46, right: 66, top: 18, bottom: 44 },
    legend: { bottom: 6, itemWidth: 18, itemHeight: 8, textStyle: { fontSize: 12 } },
    tooltip: {
      trigger: 'axis',
      formatter: (ps: any[]) => ps[0].axisValue + '<br/>' + ps.map(p =>
        `${p.marker}${p.seriesName}: <b>${p.seriesName === '总市值' ? (p.value / 1e4).toFixed(1) + '万亿' : (+p.value).toFixed(2)}</b>`).join('<br/>'),
    },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 }, boundaryGap: false },
    yAxis: [
      { type: 'value', scale: true, axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { color: '#eee6d6', type: 'dashed' } } },
      { type: 'value', position: 'right', axisLabel: { fontSize: 10, formatter: (v: number) => `${(v / 1e4).toFixed(0)}万亿` }, splitLine: { show: false } },
    ],
    dataZoom: [{ type: 'inside' }],
    series: [
      { name: '全A整体法PE-TTM', type: 'line', showSymbol: false, data: data.map(v => v.peTtm), lineStyle: { width: 1.6 }, itemStyle: { color: '#17140f' },
        markArea: area.length ? { silent: true, label: { fontSize: 10, color: '#8a7', position: 'insideTop' }, data: area } : undefined },
      { name: '总市值', type: 'line', yAxisIndex: 1, showSymbol: false, data: data.map(v => v.totalMv), lineStyle: { width: 1.3 }, itemStyle: { color: '#e07b39' } },
      { name: nDanger, type: 'line', showSymbol: false, data: flat(danger), lineStyle: { width: 1.2, type: 'dashed' }, itemStyle: { color: '#c0392b' } },
      { name: nMid, type: 'line', showSymbol: false, data: flat(mid), lineStyle: { width: 1.2, type: 'dashed' }, itemStyle: { color: '#b8860b' } },
      { name: nOpp, type: 'line', showSymbol: false, data: flat(opp), lineStyle: { width: 1.2, type: 'dashed' }, itemStyle: { color: '#1f8e5a' } },
    ],
  }
  return <ReactECharts option={option} notMerge style={{ height: 480 }} />
}

export default function BullTopPage() {
  const [d, setD] = useState<{ valuation: Val[]; turnover: Tov[]; holder: Hld[]; margin: Mgn[] } | null>(null)
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    (async () => {
      setLoading(true)
      try {
        const r = await fetch('/api/bulltop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ start: '20150101' }) })
        const j = await r.json()
        if (!j.ok) throw new Error(j.error)
        setD(j)
      } catch (e) { message.error((e as Error).message) } finally { setLoading(false) }
    })()
  }, [])

  const masterDates = useMemo(() => (d?.valuation || []).map(v => v.date), [d])
  const { range, onWheel, pointerHandlers, reset, isZoomed } = useDateZoom(masterDates)

  const pes = useMemo(() => (d?.valuation || []).map(v => v.peTtm), [d])
  const opp = quantile(pes, 0.2), mid = quantile(pes, 0.5), danger = quantile(pes, 0.8)
  const cur = d?.valuation.at(-1)
  const circByDate = useMemo(() => new Map((d?.valuation || []).map(v => [v.date, v.circMv])), [d])
  const amtByDate = useMemo(() => new Map((d?.valuation || []).map(v => [v.date, v.amountFull])), [d])

  const tovData = useMemo(() => decimate(clipByRange(d?.turnover || [], v => v.date, range), 600), [d, range])
  const mkMgn = (m: Mgn) => ({
    date: m.date,
    ratio: circByDate.get(m.date) ? +(m.rzye / circByDate.get(m.date)! * 100).toFixed(2) : null,
    buyShare: amtByDate.get(m.date) ? +(m.rzmre / amtByDate.get(m.date)! * 100).toFixed(2) : null,
  })
  const mgnData = useMemo(() => decimate(clipByRange(d?.margin || [], v => v.date, range).map(mkMgn), 600), [d, range, circByDate, amtByDate]) // eslint-disable-line react-hooks/exhaustive-deps
  const curMgn = d?.margin.length ? mkMgn(d.margin[d.margin.length - 1]) : null

  if (loading) return <div style={{ maxWidth: 1850, margin: '18px auto', padding: '0 16px' }}><Header /><div style={{ padding: 60, textAlign: 'center' }}><Spin /></div></div>
  if (!d) return null

  const zb = (node: React.ReactNode) => <ZoomBox onWheel={onWheel} pointer={pointerHandlers}>{node}</ZoomBox>
  const areas = (dates: string[]) => bullAreas(dates).map(b => (
    <ReferenceArea key={b.x1} x1={b.x1} x2={b.x2} fill={b.c} fillOpacity={0.07}
      label={{ value: b.label, position: 'insideTop', fill: b.c, fontSize: 10 }} />
  ))
  const tone = cur ? (cur.peTtm <= opp ? '#1f8e5a' : cur.peTtm >= danger ? '#c0392b' : '#17140f') : '#17140f'

  return (
    <div style={{ maxWidth: 1850, margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="Bull-market Top Radar · 剔金融石化整体法">牛市逃顶</PageTitle>
      <div style={{ fontSize: 12, color: 'var(--ink-soft)', marginBottom: 12 }}>
        滚轮缩放 · 拖动平移(四图联动) {isZoomed && <a onClick={reset} style={{ color: 'var(--accent)', marginLeft: 8 }}>重置缩放</a>}
        {range && <span style={{ marginLeft: 8 }}>{range.start} ~ {range.end}</span>}
      </div>

      <Card size="small" style={{ marginBottom: 14 }} title={<span>全A估值(剔金融石化,整体法 PE-TTM)
        {cur && <span style={{ marginLeft: 12, fontSize: 13 }}>
          当前 <b style={{ color: tone }}>{cur.peTtm.toFixed(2)}</b> · 分位 <b style={{ color: tone }}>{(cur.pct * 100).toFixed(1)}%</b>
          · <span style={{ color: '#c0392b' }}>危险 {danger.toFixed(1)}</span> / 中位 {mid.toFixed(1)} / <span style={{ color: '#1f8e5a' }}>机会 {opp.toFixed(1)}</span>
          · 总市值 {(cur.totalMv / 1e4).toFixed(1)}万亿</span>}
      </span>}>
        <EValuation data={d.valuation} danger={danger} mid={mid} opp={opp} />
        <div style={{ fontSize: 12, color: '#999', marginTop: 2 }}>⚡ echarts 试点:全量点不抽稀 · 原生图例点击隐藏 · 滚轮缩放(本图独立缩放,不与下方三图联动)</div>
      </Card>

      <Card size="small" style={{ marginBottom: 14 }} title={<span>两融拥挤度(融资+融券,3% 预警)
        {curMgn && <span style={{ marginLeft: 12, fontSize: 13 }}>
          当前 两融/流通 <b style={{ color: (curMgn.ratio ?? 0) >= 3 ? '#c0392b' : '#17140f' }}>{curMgn.ratio}%</b>
          · 融资买入占成交 <b>{curMgn.buyShare}%</b></span>}
      </span>}>
        {zb(
          <ResponsiveContainer width="100%" height={320}>
            <ComposedChart data={mgnData} margin={{ top: 8, right: 44, bottom: 0, left: 8 }}>
              {grid}{areas(mgnData.map(v => v.date))}
              <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={fmtDate} minTickGap={50} />
              <YAxis yAxisId="r" tick={{ fontSize: 10 }} width={36} tickFormatter={(v: any) => `${v}%`} />
              <YAxis yAxisId="b" orientation="right" tick={{ fontSize: 10 }} width={40} tickFormatter={(v: any) => `${v}%`} />
              <ReferenceLine yAxisId="r" y={3} stroke="#c0392b" strokeDasharray="4 3" label={{ value: '3%', fontSize: 10, fill: '#c0392b' }} />
              <Tooltip contentStyle={tip} labelFormatter={(l: any) => fmtDate(String(l))} formatter={(v: any, n: any) => [`${v}%`, n]} />
              <Legend />
              <Line yAxisId="r" type="linear" dataKey="ratio" name="两融/流通市值" stroke="#8b5cf6" strokeWidth={1.6} dot={false} isAnimationActive={false} connectNulls />
              <Line yAxisId="b" type="linear" dataKey="buyShare" name="融资买入占成交" stroke="#e07b39" strokeWidth={1.3} dot={false} isAnimationActive={false} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </Card>

      <Card size="small" style={{ marginBottom: 14 }} title="换手率(单日 / 5日均,3% 预警)">
        {zb(
          <ResponsiveContainer width="100%" height={320}>
            <LineChart data={tovData} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
              {grid}{areas(tovData.map(v => v.date))}
              <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={fmtDate} minTickGap={50} />
              <YAxis tick={{ fontSize: 10 }} width={36} tickFormatter={(v: any) => `${v}%`} />
              <ReferenceLine y={3} stroke="#c0392b" strokeDasharray="4 3" />
              <Tooltip contentStyle={tip} labelFormatter={(l: any) => fmtDate(String(l))} formatter={(v: any, n: any) => [`${v}%`, n]} />
              <Legend />
              <Line type="linear" dataKey="turnover" name="单日换手" stroke="#0b6e4f" strokeWidth={1} dot={false} isAnimationActive={false} />
              <Line type="linear" dataKey="ma5" name="5日均换手" stroke="#c0392b" strokeWidth={1.6} dot={false} isAnimationActive={false} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        )}
      </Card>

      <Card size="small" title="重要股东月度净减持(亿元,1000亿 预警)">
        <ResponsiveContainer width="100%" height={320}>
          <BarChart data={d.holder} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
            {grid}
            <XAxis dataKey="month" tick={{ fontSize: 10 }} minTickGap={40} />
            <YAxis tick={{ fontSize: 10 }} width={44} />
            <ReferenceLine y={1000} stroke="#c0392b" strokeDasharray="4 3" label={{ value: '1000亿', fontSize: 10, fill: '#c0392b' }} />
            <Tooltip contentStyle={tip} formatter={(v: any) => [`${v} 亿`, '净减持']} />
            <Legend />
            <Bar dataKey="netReduce" name="月度净减持(亿)" fill="#c0392b" />
          </BarChart>
        </ResponsiveContainer>
      </Card>
    </div>
  )
}

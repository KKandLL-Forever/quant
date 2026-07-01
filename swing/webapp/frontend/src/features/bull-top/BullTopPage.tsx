// 牛市逃顶:全A整体法PE估值(+分位危险/机会线)、融资余额/流通、换手率、股东月度净减持。
// 数据走后端 /api/bulltop;前四图共用一个缩放窗口(滚轮缩放+拖动平移,联动)。
import { useEffect, useMemo, useState } from 'react'
import { Card, Spin, message } from 'antd'
import { LineChart, Line, BarChart, Bar, ReferenceLine, ReferenceArea, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer } from 'recharts'
import { Header, PageTitle } from '../../shell'
import { useDateZoom, clipByRange, decimate } from '../../lib/useDateZoom'
import { ZoomBox } from '../../components/ZoomBox'

interface Val { date: string; peTtm: number; pct: number; circMv: number; totalMv: number }
interface Tov { date: string; turnover: number; ma5: number | null }
interface Hld { month: string; netReduce: number }
interface Mgn { date: string; rzye: number }
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

  const valData = useMemo(() => decimate(clipByRange(d?.valuation || [], v => v.date, range), 600), [d, range])
  const tovData = useMemo(() => decimate(clipByRange(d?.turnover || [], v => v.date, range), 600), [d, range])
  const mgnData = useMemo(() => decimate(clipByRange(d?.margin || [], v => v.date, range)
    .map(m => ({ date: m.date, ratio: circByDate.get(m.date) ? +(m.rzye / circByDate.get(m.date)! * 100).toFixed(2) : null, rzye: m.rzye })), 600), [d, range, circByDate])

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
        {zb(
          <ResponsiveContainer width="100%" height={360}>
            <LineChart data={valData} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
              {grid}{areas(valData.map(v => v.date))}
              <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={fmtDate} minTickGap={50} />
              <YAxis tick={{ fontSize: 10 }} width={36} />
              <ReferenceLine y={danger} stroke="#c0392b" strokeDasharray="4 3" />
              <ReferenceLine y={mid} stroke="#b8860b" strokeDasharray="4 3" />
              <ReferenceLine y={opp} stroke="#1f8e5a" strokeDasharray="4 3" />
              <Tooltip contentStyle={tip} labelFormatter={(l: any) => fmtDate(String(l))} formatter={(v: any) => [`${v}`, 'PE-TTM']} />
              <Line type="monotone" dataKey="peTtm" stroke="#17140f" strokeWidth={1.6} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </Card>

      <Card size="small" style={{ marginBottom: 14 }} title="两融余额 / 流通市值(融资+融券,3% 预警)">
        {zb(
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={mgnData} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
              {grid}{areas(mgnData.map(v => v.date))}
              <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={fmtDate} minTickGap={50} />
              <YAxis tick={{ fontSize: 10 }} width={36} tickFormatter={(v: any) => `${v}%`} />
              <ReferenceLine y={3} stroke="#c0392b" strokeDasharray="4 3" label={{ value: '3%', fontSize: 10, fill: '#c0392b' }} />
              <Tooltip contentStyle={tip} labelFormatter={(l: any) => fmtDate(String(l))} formatter={(v: any) => [`${v}%`, '两融/流通']} />
              <Line type="monotone" dataKey="ratio" stroke="#8b5cf6" strokeWidth={1.6} dot={false} isAnimationActive={false} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        )}
      </Card>

      <Card size="small" style={{ marginBottom: 14 }} title="换手率(单日 / 5日均,3% 预警)">
        {zb(
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={tovData} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
              {grid}{areas(tovData.map(v => v.date))}
              <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={fmtDate} minTickGap={50} />
              <YAxis tick={{ fontSize: 10 }} width={36} tickFormatter={(v: any) => `${v}%`} />
              <ReferenceLine y={3} stroke="#c0392b" strokeDasharray="4 3" />
              <Tooltip contentStyle={tip} labelFormatter={(l: any) => fmtDate(String(l))} formatter={(v: any, n: any) => [`${v}%`, n === 'ma5' ? '5日均' : '单日']} />
              <Line type="monotone" dataKey="turnover" stroke="#0b6e4f" strokeWidth={1} dot={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="ma5" stroke="#c0392b" strokeWidth={1.6} dot={false} isAnimationActive={false} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        )}
      </Card>

      <Card size="small" title="重要股东月度净减持(亿元,1000亿 预警)">
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={d.holder} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
            {grid}
            <XAxis dataKey="month" tick={{ fontSize: 10 }} minTickGap={40} />
            <YAxis tick={{ fontSize: 10 }} width={44} />
            <ReferenceLine y={1000} stroke="#c0392b" strokeDasharray="4 3" label={{ value: '1000亿', fontSize: 10, fill: '#c0392b' }} />
            <Tooltip contentStyle={tip} formatter={(v: any) => [`${v} 亿`, '净减持']} />
            <Bar dataKey="netReduce" fill="#c0392b" />
          </BarChart>
        </ResponsiveContainer>
      </Card>
    </div>
  )
}

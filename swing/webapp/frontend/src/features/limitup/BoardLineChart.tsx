// 连板日历折线:最高板/次高板双线 + 牛市区间底色(recharts)。迁自 trade_dashboard,配色改暖纸主题。
import { ComposedChart, Line, ReferenceArea, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'

export interface DayBoard { date: string; maxBoard: number; secondBoard: number; dragons: { tsCode: string; name: string; board: number }[] }

const BULL_MARKETS = [
  { start: '20140701', end: '20150630', label: '杠杆牛', color: '#c0392b' },
  { start: '20190101', end: '20210228', label: '2019结构牛', color: '#8b5cf6' },
  { start: '20240924', end: '20991231', label: '2024行情', color: '#8b5cf6' },
]
export const CHART_MARGIN = { top: 8, right: 16, bottom: 0, left: 8 }
export const Y_AXIS_WIDTH = 28
export const PLOT_LEFT = CHART_MARGIN.left + Y_AXIS_WIDTH

const fmtDate = (d: string) => (d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}` : d)

export function BoardLineChart({ data, height = 260 }: { data: DayBoard[]; height?: number }) {
  const dates = data.map(d => d.date)
  const bulls = BULL_MARKETS.map(b => {
    const within = dates.filter(d => d >= b.start && d <= b.end)
    return within.length ? { x1: within[0], x2: within[within.length - 1], label: b.label, color: b.color } : null
  }).filter((b): b is { x1: string; x2: string; label: string; color: string } => b !== null)

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={CHART_MARGIN}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e6e0d3" />
        <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={fmtDate} minTickGap={40} />
        {bulls.map(b => (
          <ReferenceArea key={b.x1} x1={b.x1} x2={b.x2} fill={b.color} fillOpacity={0.08}
            label={{ value: b.label, position: 'insideTop', fill: b.color, fontSize: 11 }} />
        ))}
        <YAxis tick={{ fontSize: 10 }} allowDecimals={false} width={Y_AXIS_WIDTH} />
        <Tooltip contentStyle={{ background: '#fffdf8', border: '1px solid #e6e0d3', borderRadius: 6, fontSize: 12 }}
          labelFormatter={(l: any) => fmtDate(String(l))} formatter={(v: any) => `${v} 板`} />
        <Line type="linear" dataKey="maxBoard" name="最高板" stroke="#c0392b" strokeWidth={2} dot={false} isAnimationActive={false} />
        <Line type="linear" dataKey="secondBoard" name="次高板" stroke="#8b5cf6" strokeWidth={1.5} strokeDasharray="5 4" dot={false} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

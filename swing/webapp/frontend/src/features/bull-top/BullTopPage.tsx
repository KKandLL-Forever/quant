// 牛市逃顶:全A整体法PE估值(+分位危险/机会线)、融资余额/流通、换手率、股东月度净减持。
// 数据走后端 /api/bulltop;前四图共用一个缩放窗口(滚轮缩放+拖动平移,联动)。
import { useEffect, useMemo, useState } from 'react'
import { Card, message } from 'antd'
import ReactECharts from 'echarts-for-react'
import * as echarts from 'echarts'
import { Header, PageTitle, SkelChart } from '../../shell'

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

const GROUP = 'bulltop-zoom'
const linkReady = (chart: echarts.ECharts) => { chart.group = GROUP; echarts.connect(GROUP) }
const areaData = (dates: string[]) => bullAreas(dates).map(b =>
  [{ xAxis: fmtDate(b.x1), itemStyle: { color: b.c, opacity: 0.07 }, name: b.label }, { xAxis: fmtDate(b.x2) }])
const IPO_MARKS = [
  { date: '2018-06-08', name: '工业富联' }, { date: '2018-06-11', name: '宁德时代' },
  { date: '2020-01-16', name: '京沪高铁' }, { date: '2020-07-16', name: '中芯国际' },
  { date: '2021-08-20', name: '中国电信' }, { date: '2022-01-05', name: '中国移动' },
  { date: '2022-04-21', name: '中国海油' }, { date: '2025-12-05', name: '摩尔线程' },
  { date: '2025-12-17', name: '沐曦股份' },
]
const ipoMarkLine = {
  silent: true, symbol: 'none',
  lineStyle: { color: '#7a6cff', type: 'dashed' as const, width: 1, opacity: 0.65 },
  label: { fontSize: 9, color: '#6b5bd6', rotate: 90 },
  data: IPO_MARKS.map((m, i) => ({
    xAxis: m.date,
    label: { formatter: m.name, position: (i % 2 ? 'insideEndBottom' : 'insideEndTop') as any },
  })),
}
const baseGrid = { left: 46, right: 46, top: 16, bottom: 44 }
const baseLegend = { bottom: 6, itemWidth: 18, itemHeight: 8, textStyle: { fontSize: 12 } }
const pctLine = (yAxisIndex: number) => ({ label: { formatter: '3%', fontSize: 10, color: '#c0392b' }, yAxis: 3, ...(yAxisIndex ? { yAxisIndex } : {}) })

// 全A估值图(canvas 全量点不抽稀 + 原生 legend + 滚轮缩放,四图联动)
function EValuation({ data, danger, mid, opp }: { data: Val[]; danger: number; mid: number; opp: number }) {
  const dates = data.map(v => fmtDate(v.date))
  const flat = (y: number) => data.map(() => y)
  const nDanger = `危险 ${danger.toFixed(1)}`, nMid = `中位 ${mid.toFixed(1)}`, nOpp = `机会 ${opp.toFixed(1)}`
  const area = areaData(data.map(v => v.date))
  const option = {
    animation: false,
    grid: { ...baseGrid, right: 66 },
    legend: baseLegend,
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
        markArea: area.length ? { silent: true, label: { fontSize: 10, color: '#8a7', position: 'insideTop' }, data: area } : undefined,
        markLine: ipoMarkLine },
      { name: '总市值', type: 'line', yAxisIndex: 1, showSymbol: false, data: data.map(v => v.totalMv), lineStyle: { width: 1.3 }, itemStyle: { color: '#e07b39' } },
      { name: nDanger, type: 'line', showSymbol: false, data: flat(danger), lineStyle: { width: 1.2, type: 'dashed' }, itemStyle: { color: '#c0392b' } },
      { name: nMid, type: 'line', showSymbol: false, data: flat(mid), lineStyle: { width: 1.2, type: 'dashed' }, itemStyle: { color: '#b8860b' } },
      { name: nOpp, type: 'line', showSymbol: false, data: flat(opp), lineStyle: { width: 1.2, type: 'dashed' }, itemStyle: { color: '#1f8e5a' } },
    ],
  }
  return <ReactECharts option={option} notMerge style={{ height: 480 }} onChartReady={linkReady} />
}

// 两融拥挤度(双轴 % + 3% 预警线 + 牛市阴影)
function EMargin({ data }: { data: { date: string; ratio: number | null; buyShare: number | null }[] }) {
  const option = {
    animation: false, grid: baseGrid, legend: baseLegend,
    tooltip: { trigger: 'axis', valueFormatter: (v: any) => v == null ? '-' : v + '%' },
    xAxis: { type: 'category', data: data.map(v => fmtDate(v.date)), boundaryGap: false, axisLabel: { fontSize: 10 } },
    yAxis: [{ type: 'value', axisLabel: { formatter: '{value}%', fontSize: 10 }, splitLine: { lineStyle: { color: '#eee6d6', type: 'dashed' } } },
      { type: 'value', position: 'right', axisLabel: { formatter: '{value}%', fontSize: 10 }, splitLine: { show: false } }],
    dataZoom: [{ type: 'inside' }],
    series: [
      { name: '两融/流通市值', type: 'line', showSymbol: false, connectNulls: true, data: data.map(v => v.ratio), lineStyle: { width: 1.6 }, itemStyle: { color: '#8b5cf6' },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: '#c0392b', type: 'dashed' }, data: [pctLine(0)] },
        markArea: { silent: true, data: areaData(data.map(v => v.date)) } },
      { name: '融资买入占成交', type: 'line', yAxisIndex: 1, showSymbol: false, connectNulls: true, data: data.map(v => v.buyShare), lineStyle: { width: 1.3 }, itemStyle: { color: '#e07b39' } },
    ],
  }
  return <ReactECharts option={option} notMerge style={{ height: 320 }} onChartReady={linkReady} />
}

// 换手率(单日 / 5日均 + 3% 预警线 + 牛市阴影)
function ETurnover({ data }: { data: { date: string; turnover: number; ma5: number | null }[] }) {
  const option = {
    animation: false, grid: baseGrid, legend: baseLegend,
    tooltip: { trigger: 'axis', valueFormatter: (v: any) => v == null ? '-' : v + '%' },
    xAxis: { type: 'category', data: data.map(v => fmtDate(v.date)), boundaryGap: false, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', axisLabel: { formatter: '{value}%', fontSize: 10 }, splitLine: { lineStyle: { color: '#eee6d6', type: 'dashed' } } },
    dataZoom: [{ type: 'inside' }],
    series: [
      { name: '单日换手', type: 'line', showSymbol: false, data: data.map(v => v.turnover), lineStyle: { width: 1 }, itemStyle: { color: '#0b6e4f' },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: '#c0392b', type: 'dashed' }, data: [{ yAxis: 3 }] },
        markArea: { silent: true, data: areaData(data.map(v => v.date)) } },
      { name: '5日均换手', type: 'line', showSymbol: false, connectNulls: true, data: data.map(v => v.ma5), lineStyle: { width: 1.6 }, itemStyle: { color: '#c0392b' } },
    ],
  }
  return <ReactECharts option={option} notMerge style={{ height: 320 }} onChartReady={linkReady} />
}

// 股东月度净减持(柱 + 1000亿 预警线,月频不参与联动)
function EHolder({ data }: { data: Hld[] }) {
  const option = {
    animation: false, grid: { ...baseGrid, right: 16 }, legend: baseLegend,
    tooltip: { trigger: 'axis', valueFormatter: (v: any) => `${v} 亿` },
    xAxis: { type: 'category', data: data.map(v => v.month), axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', axisLabel: { fontSize: 10 } },
    dataZoom: [{ type: 'inside' }],
    series: [{ name: '月度净减持(亿)', type: 'bar', data: data.map(v => v.netReduce), itemStyle: { color: '#c0392b' },
      markLine: { silent: true, symbol: 'none', lineStyle: { color: '#c0392b', type: 'dashed' }, data: [{ yAxis: 1000, label: { formatter: '1000亿', fontSize: 10, color: '#c0392b' } }] } }],
  }
  return <ReactECharts option={option} notMerge style={{ height: 320 }} />
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

  const pes = useMemo(() => (d?.valuation || []).map(v => v.peTtm), [d])
  const opp = quantile(pes, 0.2), mid = quantile(pes, 0.5), danger = quantile(pes, 0.8)
  const cur = d?.valuation.at(-1)
  const circByDate = useMemo(() => new Map((d?.valuation || []).map(v => [v.date, v.circMv])), [d])
  const amtByDate = useMemo(() => new Map((d?.valuation || []).map(v => [v.date, v.amountFull])), [d])

  const mkMgn = (m: Mgn) => ({
    date: m.date,
    ratio: circByDate.get(m.date) ? +(m.rzye / circByDate.get(m.date)! * 100).toFixed(2) : null,
    buyShare: amtByDate.get(m.date) ? +(m.rzmre / amtByDate.get(m.date)! * 100).toFixed(2) : null,
  })
  const mgnData = useMemo(() => (d?.margin || []).map(mkMgn), [d, circByDate, amtByDate]) // eslint-disable-line react-hooks/exhaustive-deps
  const curMgn = d?.margin.length ? mkMgn(d.margin[d.margin.length - 1]) : null

  if (loading) return (
    <div style={{ maxWidth: 1850, margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="Bull-market Top Radar · 剔金融石化整体法">牛市逃顶</PageTitle>
      <div style={{ fontSize: 12, color: 'var(--ink-soft)', marginBottom: 12 }}>⚡ echarts · 滚轮缩放(前三图联动,全量点不抽稀)</div>
      <SkelChart h={480} /><SkelChart h={320} /><SkelChart h={320} /><SkelChart h={320} />
    </div>
  )
  if (!d) return null

  const tone = cur ? (cur.peTtm <= opp ? '#1f8e5a' : cur.peTtm >= danger ? '#c0392b' : '#17140f') : '#17140f'

  return (
    <div style={{ maxWidth: 1850, margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="Bull-market Top Radar · 剔金融石化整体法">牛市逃顶</PageTitle>
      <div style={{ fontSize: 12, color: 'var(--ink-soft)', marginBottom: 12 }}>⚡ echarts · 滚轮缩放(前三图联动,全量点不抽稀)</div>

      <Card size="small" style={{ marginBottom: 14 }} title={<span>全A估值(剔金融石化,整体法 PE-TTM)
        {cur && <span style={{ marginLeft: 12, fontSize: 13 }}>
          当前 <b style={{ color: tone }}>{cur.peTtm.toFixed(2)}</b> · 分位 <b style={{ color: tone }}>{(cur.pct * 100).toFixed(1)}%</b>
          · <span style={{ color: '#c0392b' }}>危险 {danger.toFixed(1)}</span> / 中位 {mid.toFixed(1)} / <span style={{ color: '#1f8e5a' }}>机会 {opp.toFixed(1)}</span>
          · 总市值 {(cur.totalMv / 1e4).toFixed(1)}万亿</span>}
      </span>}>
        <EValuation data={d.valuation} danger={danger} mid={mid} opp={opp} />
      </Card>

      <Card size="small" style={{ marginBottom: 14 }} title={<span>两融拥挤度(融资+融券,3% 预警)
        {curMgn && <span style={{ marginLeft: 12, fontSize: 13 }}>
          当前 两融/流通 <b style={{ color: (curMgn.ratio ?? 0) >= 3 ? '#c0392b' : '#17140f' }}>{curMgn.ratio}%</b>
          · 融资买入占成交 <b>{curMgn.buyShare}%</b></span>}
      </span>}>
        <EMargin data={mgnData} />
      </Card>

      <Card size="small" style={{ marginBottom: 14 }} title="换手率(单日 / 5日均,3% 预警)">
        <ETurnover data={d.turnover} />
      </Card>

      <Card size="small" title="重要股东月度净减持(亿元,1000亿 预警)">
        <EHolder data={d.holder} />
      </Card>
    </div>
  )
}

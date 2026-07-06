// ETF份额:多只ETF总份额时序对比(echarts多线,原生图例/十字光标/滚轮缩放)。数据走后端 /api/etfshare(tushare etf_share_size)。
import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Spin, message } from 'antd'
import ReactECharts from 'echarts-for-react'
import { Header, PageTitle } from '../../shell'

interface EtfMeta { ts_code: string; name: string; color: string }
const ETF_LIST: EtfMeta[] = [
  { ts_code: '588080.SH', name: '科创板50ETF', color: '#ef4444' },
  { ts_code: '588000.SH', name: '科创50ETF', color: '#f97316' },
  { ts_code: '510050.SH', name: '上证50ETF', color: '#f59e0b' },
  { ts_code: '512100.SH', name: '中证1000ETF(SH)', color: '#eab308' },
  { ts_code: '560010.SH', name: '科创100ETF', color: '#84cc16' },
  { ts_code: '159845.SZ', name: '中证1000ETF(SZ)', color: '#22c55e' },
  { ts_code: '159915.SZ', name: '创业板ETF', color: '#10b981' },
  { ts_code: '510300.SH', name: '沪深300ETF华泰', color: '#3b82f6' },
  { ts_code: '510500.SH', name: '中证500ETF', color: '#ec4899' },
  { ts_code: '159919.SZ', name: '沪深300ETF嘉实', color: '#a855f7' },
]
const PRESETS = [['1M', '近1月'], ['3M', '近3月'], ['6M', '近6月'], ['1Y', '近1年']] as const
type Preset = typeof PRESETS[number][0]

const ymd = (d: Date) => `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`
function presetStart(k: Preset): string {
  const d = new Date()
  if (k === '1M') d.setMonth(d.getMonth() - 1)
  else if (k === '3M') d.setMonth(d.getMonth() - 3)
  else if (k === '6M') d.setMonth(d.getMonth() - 6)
  else d.setFullYear(d.getFullYear() - 1)
  return ymd(d)
}
const fmtDate = (d: string) => `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`

export default function EtfSharePage() {
  const [preset, setPreset] = useState<Preset>('1Y')
  const [series, setSeries] = useState<Record<string, { trade_date: string; fdShare: number }[]>>({})
  const [loading, setLoading] = useState(false)

  const load = async (p: Preset) => {
    setLoading(true)
    try {
      const r = await fetch('/api/etfshare', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ codes: ETF_LIST.map(e => e.ts_code), start: presetStart(p) }),
      })
      const j = await r.json()
      if (!j.ok) throw new Error(j.error)
      setSeries(j.series)
    } catch (e) { message.error((e as Error).message) } finally { setLoading(false) }
  }
  useEffect(() => { load(preset) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // 每个日期一行,各 ETF 一列
  const chartData = useMemo(() => {
    const byDate: Record<string, Record<string, number | string>> = {}
    for (const e of ETF_LIST) {
      for (const p of series[e.ts_code] || []) {
        (byDate[p.trade_date] = byDate[p.trade_date] || { date: p.trade_date })[e.ts_code] = p.fdShare
      }
    }
    return Object.values(byDate).sort((a, b) => (a.date < b.date ? -1 : 1))
  }, [series])

  // 合计:各 ETF 份额前向填充(某日无更新则沿用上一已知值),仅当全部 ETF 都已有数据才计入,避免早期缺列导致低估
  const totalData = useMemo(() => {
    const last: Record<string, number> = {}
    const out: { date: string; total: number }[] = []
    for (const row of chartData) {
      for (const e of ETF_LIST) { const v = row[e.ts_code]; if (typeof v === 'number') last[e.ts_code] = v }
      if (ETF_LIST.every(e => typeof last[e.ts_code] === 'number')) {
        let sum = 0
        for (const e of ETF_LIST) sum += last[e.ts_code]
        out.push({ date: row.date as string, total: Math.round(sum * 100) / 100 })
      }
    }
    return out
  }, [chartData])
  const totalSummary = useMemo(() => {
    if (totalData.length === 0) return null
    const last = totalData[totalData.length - 1]
    const prev = totalData.length >= 2 ? totalData[totalData.length - 2] : null
    return { date: last.date, total: last.total, delta: prev ? last.total - prev.total : null }
  }, [totalData])

  const mainOpt = useMemo(() => ({
    animation: false,
    grid: { left: 52, right: 20, top: 14, bottom: 64 },
    legend: { bottom: 4, type: 'scroll', textStyle: { fontSize: 11 } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, valueFormatter: (v: any) => v == null ? '-' : `${v} 亿份` },
    xAxis: { type: 'category', data: chartData.map(r => r.date as string), boundaryGap: false, axisLabel: { fontSize: 10, formatter: fmtDate } },
    yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { color: '#f0eadc' } } },
    dataZoom: [{ type: 'inside' }],
    series: ETF_LIST.map(e => ({
      name: `${e.name} ${e.ts_code}`, type: 'line', showSymbol: false, connectNulls: true,
      data: chartData.map(r => (r[e.ts_code] as number) ?? null), lineStyle: { color: e.color, width: 1.6 }, itemStyle: { color: e.color },
    })),
  }), [chartData])

  const totalOpt = useMemo(() => ({
    animation: false,
    grid: { left: 52, right: 20, top: 14, bottom: 28 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, valueFormatter: (v: any) => `${v} 亿份` },
    xAxis: { type: 'category', data: totalData.map(t => t.date), boundaryGap: false, axisLabel: { fontSize: 10, formatter: fmtDate } },
    yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { color: '#f0eadc' } } },
    dataZoom: [{ type: 'inside' }],
    series: [{ name: '合计', type: 'line', showSymbol: false, data: totalData.map(t => t.total), lineStyle: { color: '#17140f', width: 2 }, itemStyle: { color: '#17140f' } }],
  }), [totalData])

  return (
    <div style={{ maxWidth: 1850, margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="ETF Share Size · 份额=资金申赎的真实脚印">ETF 份额</PageTitle>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16 }}>
        {PRESETS.map(([k, lbl]) => (
          <Button key={k} type={preset === k ? 'primary' : 'default'} size="small"
            onClick={() => { setPreset(k); load(k) }}>{lbl}</Button>
        ))}
        <span style={{ fontSize: 12, color: 'var(--ink-soft)' }}>单位:亿份 · 点图例可隐藏/显示 · 滚轮缩放 · 份额增=资金净申购</span>
      </div>

      {loading && <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>}
      {!loading && (
        <Card size="small">
          <ReactECharts option={mainOpt} notMerge style={{ height: 480 }} />
        </Card>
      )}

      {!loading && totalSummary && (
        <Card size="small" style={{ marginTop: 16 }}
          title={<span>全部 ETF 份额合计
            <span style={{ marginLeft: 12, fontSize: 13, color: 'var(--ink-soft)' }}>最新 {fmtDate(totalSummary.date)}</span>
            <b style={{ marginLeft: 8, fontSize: 15 }}>{totalSummary.total.toFixed(2)} 亿份</b>
            {totalSummary.delta != null && (
              <span style={{ marginLeft: 8, fontSize: 13, color: totalSummary.delta > 0 ? '#c0392b' : totalSummary.delta < 0 ? '#1f8e5a' : '#5b554a' }}>
                较昨日{totalSummary.delta >= 0 ? '增加' : '减少'} {Math.abs(totalSummary.delta).toFixed(2)} 亿份
              </span>
            )}
          </span>}>
          <ReactECharts option={totalOpt} notMerge style={{ height: 220 }} />
        </Card>
      )}
    </div>
  )
}

// ETF份额:多只ETF总份额时序对比(recharts多线)。数据走后端 /api/etfshare(tushare etf_share_size)。
import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Spin, message } from 'antd'
import { LineChart, Line, XAxis, YAxis, Tooltip, Legend, CartesianGrid, ResponsiveContainer } from 'recharts'
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
  const [hidden, setHidden] = useState<Record<string, boolean>>({})

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

  // 合并成 recharts 数据集:每个日期一行,各 ETF 一列
  const chartData = useMemo(() => {
    const byDate: Record<string, Record<string, number | string>> = {}
    for (const e of ETF_LIST) {
      for (const p of series[e.ts_code] || []) {
        (byDate[p.trade_date] = byDate[p.trade_date] || { date: p.trade_date })[e.ts_code] = p.fdShare
      }
    }
    return Object.values(byDate).sort((a, b) => (a.date < b.date ? -1 : 1))
  }, [series])

  return (
    <div style={{ maxWidth: 1850, margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="ETF Share Size · 份额=资金申赎的真实脚印">ETF 份额</PageTitle>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16 }}>
        {PRESETS.map(([k, lbl]) => (
          <Button key={k} type={preset === k ? 'primary' : 'default'} size="small"
            onClick={() => { setPreset(k); load(k) }}>{lbl}</Button>
        ))}
        <span style={{ fontSize: 12, color: 'var(--ink-soft)' }}>单位:亿份 · 点图例可隐藏/显示 · 份额增=资金净申购</span>
      </div>

      {loading && <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>}
      {!loading && (
        <Card size="small">
          <ResponsiveContainer width="100%" height={480}>
            <LineChart data={chartData} margin={{ top: 8, right: 20, bottom: 0, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e6e0d3" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={fmtDate} minTickGap={50} />
              <YAxis tick={{ fontSize: 10 }} width={48} />
              <Tooltip contentStyle={{ background: '#fffdf8', border: '1px solid #e6e0d3', borderRadius: 6, fontSize: 12 }}
                labelFormatter={(l: any) => fmtDate(String(l))} formatter={(v: any) => `${v} 亿份`} />
              <Legend onClick={(o: any) => setHidden(h => ({ ...h, [o.dataKey]: !h[o.dataKey] }))} />
              {ETF_LIST.map(e => (
                <Line key={e.ts_code} type="monotone" dataKey={e.ts_code} name={`${e.name} ${e.ts_code}`}
                  stroke={e.color} strokeWidth={1.6} dot={false} isAnimationActive={false} hide={hidden[e.ts_code]} connectNulls />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </Card>
      )}
    </div>
  )
}

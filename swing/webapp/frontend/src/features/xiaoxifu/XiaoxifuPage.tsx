// 龙头动量轮动策略(复现):调仓动作表 + 累计收益曲线 + 绩效卡。数据走后端 /api/xiaoxifu(xiaoxifu/leader_momentum)。
import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Spin, Table, Tag, InputNumber, Statistic, Row, Col, message } from 'antd'
import { LineChart, Line, XAxis, YAxis, Tooltip, Legend, CartesianGrid, ResponsiveContainer } from 'recharts'
import { Header, PageTitle } from '../../shell'

interface Pick { code: string; name: string; weight: number }
interface Rebalance { date: string; picks: Pick[] }
interface Perf { 策略: string; 年化收益: number | null; 年化波动率: number | null; 最大回撤: number | null; 夏普比率: number | null; 卡玛比率: number | null }
interface Payload {
  ok: boolean; error?: string
  params: { N: number; K: number; L: number; start: string; end: string }
  cols: string[]
  summary: Perf[]
  equity: Record<string, number | string>[]
  rebalances: Rebalance[]
}

const LINE_COLORS: Record<string, string> = { 龙头动量轮动策略: '#c0392b', 等权重组合: '#1f8e5a', 科创50ETF: '#3b82f6' }
const pct = (v: number) => `${(v * 100).toFixed(1)}%`

export default function XiaoxifuPage() {
  const [N, setN] = useState(20)
  const [K, setK] = useState(5)
  const [L, setL] = useState(5)
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/xiaoxifu', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ N, K, L, start: '2024-01-01' }),
      })
      const j: Payload = await r.json()
      if (!j.ok) throw new Error(j.error || '请求失败')
      setData(j)
    } catch (e) { message.error((e as Error).message) } finally { setLoading(false) }
  }
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const equityData = useMemo(() => (data?.equity || []).map(row => {
    const o: Record<string, number | string> = { date: row.date }
    for (const c of data!.cols) o[c] = row[c] as number
    return o
  }), [data])

  const rebColumns = [
    { title: '调仓日', dataIndex: 'date', width: 120 },
    {
      title: '持仓(权重)', dataIndex: 'picks',
      render: (picks: Pick[]) => picks.length === 0
        ? <span style={{ color: '#999' }}>空仓(无正动量标的)</span>
        : picks.map(p => (
          <Tag key={p.code} color="red" style={{ marginBottom: 4 }}>
            {p.name} <b>{(p.weight * 100).toFixed(1)}%</b>
          </Tag>
        )),
    },
  ]

  return (
    <div style={{ maxWidth: 1850, margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="Leader Momentum · 风险调整动量 + 定期轮动(复现小西西弗)">龙头动量轮动</PageTitle>

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <span>动量周期 N</span><InputNumber min={5} max={60} value={N} onChange={v => setN(v || 20)} size="small" />
        <span>调仓间隔 K</span><InputNumber min={1} max={20} value={K} onChange={v => setK(v || 5)} size="small" />
        <span>持仓数 L</span><InputNumber min={1} max={22} value={L} onChange={v => setL(v || 5)} size="small" />
        <Button type="primary" size="small" onClick={load} loading={loading}>运行回测</Button>
        <span style={{ fontSize: 12, color: 'var(--ink-soft)' }}>22 只龙头股 · 2024-01-01 起 · 权重滞后1天(T+1执行)</span>
      </div>

      {loading && <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>}

      {!loading && data && (
        <>
          <Row gutter={12} style={{ marginBottom: 16 }}>
            {data.summary.map(s => (
              <Col key={s.策略} span={8}>
                <Card size="small" title={s.策略}
                  headStyle={{ color: LINE_COLORS[s.策略], fontWeight: 600 }}>
                  <Row gutter={8}>
                    <Col span={12}><Statistic title="年化收益" value={s.年化收益 ?? '—'} suffix="%"
                      valueStyle={{ color: '#c0392b', fontSize: 20 }} /></Col>
                    <Col span={12}><Statistic title="最大回撤" value={s.最大回撤 ?? '—'} suffix="%"
                      valueStyle={{ fontSize: 20 }} /></Col>
                    <Col span={8}><Statistic title="年化波动" value={s.年化波动率 ?? '—'} suffix="%" valueStyle={{ fontSize: 14 }} /></Col>
                    <Col span={8}><Statistic title="夏普" value={s.夏普比率 ?? '—'} valueStyle={{ fontSize: 14 }} /></Col>
                    <Col span={8}><Statistic title="卡玛" value={s.卡玛比率 ?? '—'} valueStyle={{ fontSize: 14 }} /></Col>
                  </Row>
                </Card>
              </Col>
            ))}
          </Row>

          <Card size="small" title="累计收益曲线" style={{ marginBottom: 16 }}>
            <ResponsiveContainer width="100%" height={420}>
              <LineChart data={equityData} margin={{ top: 8, right: 20, bottom: 0, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e6e0d3" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={50} />
                <YAxis tick={{ fontSize: 10 }} width={48} tickFormatter={(v: number) => pct(v)} />
                <Tooltip contentStyle={{ background: '#fffdf8', border: '1px solid #e6e0d3', borderRadius: 6, fontSize: 12 }}
                  formatter={(v: any) => pct(Number(v))} />
                <Legend />
                {data.cols.map(c => (
                  <Line key={c} type="linear" dataKey={c} name={c}
                    stroke={LINE_COLORS[c] || '#888'} strokeWidth={c === '龙头动量轮动策略' ? 2 : 1.4}
                    dot={false} isAnimationActive={false} connectNulls />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </Card>

          <Card size="small" title={`调仓动作(共 ${data.rebalances.length} 次,每 ${data.params.K} 交易日一次,最新在前)`}>
            <Table rowKey="date" size="small" columns={rebColumns} dataSource={data.rebalances}
              pagination={{ pageSize: 20, showSizeChanger: false }} />
          </Card>
        </>
      )}
    </div>
  )
}

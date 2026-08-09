// ETF 趋势跟踪(American 250/20 离散进出):今日买卖点 + 规则说明 + 净值对比 + 历史交易。数据走 /api/etftrend。
import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Table, Tag, Statistic, Row, Col, InputNumber, Alert, message } from 'antd'
import ReactECharts from 'echarts-for-react'
import { Header, PageTitle, SkelStatRow, SkelChart, SkelTable } from '../../shell'

interface Item {
  code: string; name: string; date: string; price: number
  fast: number; slow: number; entry_line: number; atr: number; atr_pct: number
  held: boolean; size: number; stop: number | null; stop_gap: number | null
  entry_date: string | null; entry_px: number | null; hold_days: number | null
  pnl: number | null; to_entry: number | null; signal_on: boolean; action: string
}
interface Trade {
  code: string; name: string; entry_date: string; entry_px: number
  exit_date: string; exit_px: number; size: number; hold_days: number; ret: number
}
interface Metric { cagr: number; sharpe: number | null; mdd: number; final: number }
interface Payload {
  ok: boolean; error?: string; date: string; capital: number; sleeve: number
  params: { slow: number; fast: number; omega: number; stop_p: number; atr_span: number; risk_r: number }
  items: Item[]; trades: Trade[]
  stats: { n_trades: number; winrate: number | null; avg_win: number | null; avg_loss: number | null; avg_hold: number | null }
  equity: { date: string; strat: number; bh: number }[]
  perf: { strat: Metric; bh: Metric; cost: number; years: number }
}

const ACTION_STYLE: Record<string, { color: string; bg: string; tip: string }> = {
  '买入': { color: '#c0392b', bg: '#fdecea', tip: '快线已上穿进场线,明日开盘买入' },
  '卖出': { color: '#1f8e5a', bg: '#eaf6ef', tip: '已跌破止损且趋势信号熄灭,明日开盘卖出' },
  '预警(已破止损,信号仍在)': { color: '#d98324', bg: '#fdf4e7', tip: '收盘已跌破止损,但快线仍在进场线上方,按规则继续持有;信号一熄即卖' },
  '持有': { color: '#0b6e4f', bg: '#eef6f1', tip: '持仓中,每日把止损抬到 收盘-5×ATR(只升不降)' },
  '观望': { color: '#8a8377', bg: '#f4f2ec', tip: '空仓,等快线上穿进场线' },
}

const fmtDate = (d: string) => (d && d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : d)
const pct = (v: number | null | undefined, digits = 2) =>
  v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`

export default function EtfTrendPage() {
  const [capital, setCapital] = useState(200000)
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(false)

  const load = async (cap: number) => {
    setLoading(true)
    try {
      const r = await fetch('/api/etftrend', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ capital: cap }),
      })
      const j: Payload = await r.json()
      if (!j.ok) throw new Error(j.error || '请求失败')
      setData(j)
    } catch (e) { message.error((e as Error).message) } finally { setLoading(false) }
  }
  useEffect(() => { load(capital) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const equityOpt = useMemo(() => {
    if (!data) return {}
    return {
      grid: { left: 62, right: 20, top: 34, bottom: 46 },
      tooltip: { trigger: 'axis', valueFormatter: (v: number) => `¥${Math.round(v).toLocaleString()}` },
      legend: { data: ['趋势跟踪', '等权买入持有'], top: 2 },
      xAxis: { type: 'category', data: data.equity.map(e => fmtDate(e.date)), axisLabel: { fontSize: 11 } },
      yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 11, formatter: (v: number) => `${(v / 10000).toFixed(0)}万` } },
      series: [
        { name: '趋势跟踪', type: 'line', data: data.equity.map(e => e.strat), showSymbol: false, lineStyle: { width: 2, color: '#0b6e4f' }, itemStyle: { color: '#0b6e4f' } },
        { name: '等权买入持有', type: 'line', data: data.equity.map(e => e.bh), showSymbol: false, lineStyle: { width: 1.6, color: '#c0392b', type: 'dashed' }, itemStyle: { color: '#c0392b' } },
      ],
    }
  }, [data])

  const alerts = data?.items.filter(i => i.action === '买入' || i.action === '卖出' || i.action.startsWith('预警')) ?? []

  const tradeCols = [
    { title: '标的', dataIndex: 'name', width: 110 },
    { title: '买入日', dataIndex: 'entry_date', width: 110, render: fmtDate,
      defaultSortOrder: 'descend' as const, sorter: (a: Trade, b: Trade) => (a.entry_date < b.entry_date ? -1 : 1) },
    { title: '买入价', dataIndex: 'entry_px', width: 88, render: (v: number) => `¥${v}` },
    { title: '卖出日', dataIndex: 'exit_date', width: 110, render: fmtDate },
    { title: '卖出价', dataIndex: 'exit_px', width: 88, render: (v: number) => `¥${v}` },
    { title: '仓位', dataIndex: 'size', width: 76, render: (v: number) => `${(v * 100).toFixed(0)}%` },
    { title: '持有', dataIndex: 'hold_days', width: 84, sorter: (a: Trade, b: Trade) => a.hold_days - b.hold_days, render: (v: number) => `${v}天` },
    { title: '收益', dataIndex: 'ret', width: 92, sorter: (a: Trade, b: Trade) => a.ret - b.ret,
      render: (v: number) => <b style={{ color: v >= 0 ? '#c0392b' : '#1f8e5a' }}>{pct(v * 100)}</b> },
  ]

  return (
    <div style={{ maxWidth: 'min(2000px, 96vw)', margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="American Trend-Following · 离散进出(Sepp & Lucic 2026,研究:etf_trend)">ETF 趋势跟踪</PageTitle>

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <span>总资金</span>
        <InputNumber value={capital} onChange={v => setCapital(v || 200000)} size="small" step={10000} min={10000}
          style={{ width: 130 }} formatter={v => `¥${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')} parser={v => Number((v || '').replace(/[^\d]/g, ''))} />
        <Button type="primary" size="small" onClick={() => load(capital)} loading={loading}
          style={{ background: 'linear-gradient(135deg,#0b6e4f,#159c70)', border: 'none' }}>▶ 刷新信号</Button>
        {data && <span style={{ fontSize: 12, color: 'var(--ink-soft)' }}>
          4 只 ETF 各分 ¥{data.sleeve.toLocaleString()} · 每只独立执行 · 只做多 · 数据至 {fmtDate(data.date)}
        </span>}
      </div>

      {loading && <><SkelStatRow n={4} /><SkelChart /><SkelTable /></>}

      {!loading && data && (
        <>
          {alerts.length > 0 && (
            <Alert type="warning" showIcon style={{ marginBottom: 14 }}
              message={`今日有 ${alerts.length} 个动作提示`}
              description={alerts.map(a => `${a.name}:${a.action}`).join('　·　')} />
          )}

          <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
            {data.items.map(it => {
              const st = ACTION_STYLE[it.action] || ACTION_STYLE['观望']
              return (
                <Col key={it.code} xs={24} sm={12} lg={6}>
                  <Card size="small" style={{ background: st.bg, borderColor: st.color + '55', height: '100%' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                      <b style={{ fontSize: 15 }}>{it.name}</b>
                      <Tag color={st.color} style={{ marginRight: 0 }}>{it.action}</Tag>
                    </div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 22, fontWeight: 600, margin: '6px 0 2px' }}>
                      ¥{it.price}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--ink-soft)', lineHeight: 1.9, marginTop: 6 }}>
                      {it.held ? (
                        <>
                          <div>仓位 <b>{(it.size * 100).toFixed(0)}%</b>　浮盈 <b style={{ color: (it.pnl ?? 0) >= 0 ? '#c0392b' : '#1f8e5a' }}>{pct(it.pnl)}</b></div>
                          <div>止损 <b>¥{it.stop}</b>　距止损 <b style={{ color: (it.stop_gap ?? 0) < 2 ? '#c0392b' : 'inherit' }}>{pct(it.stop_gap)}</b></div>
                          <div>{fmtDate(it.entry_date || '')} 买入 ¥{it.entry_px}　已持 {it.hold_days} 天</div>
                        </>
                      ) : (
                        <>
                          <div>空仓　距买入触发 <b style={{ color: (it.to_entry ?? 99) < 2 ? '#c0392b' : 'inherit' }}>{pct(it.to_entry)}</b></div>
                          <div>快线 {it.fast}　进场线 {it.entry_line}</div>
                          <div>快线需上穿进场线才买入</div>
                        </>
                      )}
                      <div style={{ opacity: .8 }}>ATR {it.atr_pct}%（决定仓位与止损宽度）</div>
                    </div>
                    <div style={{ fontSize: 11, color: st.color, marginTop: 8, lineHeight: 1.6 }}>{st.tip}</div>
                  </Card>
                </Col>
              )
            })}
          </Row>

          <Card size="small" style={{ marginBottom: 14 }} title="买卖规则(论文式 A.5–A.13,每只 ETF 独立执行)">
            <Row gutter={[16, 10]} style={{ fontSize: 13, lineHeight: 1.9 }}>
              <Col xs={24} md={12}>
                <div><b style={{ color: '#c0392b' }}>① 买入</b>　空仓时,<b>快线({data.params.fast}日EWMA) &gt; 慢线({data.params.slow}日EWMA) + {data.params.omega}×ATR</b></div>
                <div style={{ color: 'var(--ink-soft)', fontSize: 12, marginLeft: 14 }}>多要求一个 ATR 的缓冲,过滤贴着慢线来回蹭的假突破</div>
                <div style={{ marginTop: 6 }}><b>② 买多少</b>　仓位 = {data.params.risk_r} × 现价 ÷ ATR,上限 100%,<b>建仓时定死不再调</b></div>
                <div style={{ color: 'var(--ink-soft)', fontSize: 12, marginLeft: 14 }}>波动越大买越少;不做日常加减仓,是把成本压到极低的关键</div>
              </Col>
              <Col xs={24} md={12}>
                <div><b>③ 止损</b>　建仓日 = 收盘 − {data.params.stop_p}×ATR;之后每日 <b>max(昨日止损, 收盘 − {data.params.stop_p}×ATR)</b>,<b>只升不降</b></div>
                <div style={{ marginTop: 6 }}><b style={{ color: '#1f8e5a' }}>④ 卖出</b>　<b>收盘 &lt; 昨日止损</b> 且 <b>快线 ≤ 慢线 + {data.params.omega}×ATR</b>,两条同时成立才卖</div>
                <div style={{ color: 'var(--ink-soft)', fontSize: 12, marginLeft: 14 }}>只看止损会在趋势未变时被震出,次日信号仍亮又得买回,白付两趟手续费(论文脚注 10)</div>
              </Col>
            </Row>
            <div style={{ fontSize: 12, color: 'var(--ink-soft)', marginTop: 10, borderTop: '1px dashed var(--border, #e6e0d3)', paddingTop: 8 }}>
              信号很慢(平均每只每年约 1 笔),<b>周频检查即可</b>,不必天天盯。所有条件以<b>收盘价</b>确认,次日开盘执行。
            </div>
          </Card>

          <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
            <Col xs={12} lg={6}><Card size="small"><Statistic title={`策略年化(近${data.perf.years}年)`} value={data.perf.strat.cagr} suffix="%" precision={2} valueStyle={{ color: '#0b6e4f' }} /><div style={{ fontSize: 12, color: 'var(--ink-soft)' }}>买入持有 {data.perf.bh.cagr}%</div></Card></Col>
            <Col xs={12} lg={6}><Card size="small"><Statistic title="夏普" value={data.perf.strat.sharpe ?? 0} precision={2} valueStyle={{ color: '#0b6e4f' }} /><div style={{ fontSize: 12, color: 'var(--ink-soft)' }}>买入持有 {data.perf.bh.sharpe}</div></Card></Col>
            <Col xs={12} lg={6}><Card size="small"><Statistic title="最大回撤" value={data.perf.strat.mdd} suffix="%" precision={2} valueStyle={{ color: '#c0392b' }} /><div style={{ fontSize: 12, color: 'var(--ink-soft)' }}>买入持有 {data.perf.bh.mdd}%</div></Card></Col>
            <Col xs={12} lg={6}><Card size="small"><Statistic title="累计交易成本" value={data.perf.cost} prefix="¥" precision={0} /><div style={{ fontSize: 12, color: 'var(--ink-soft)' }}>{data.stats.n_trades} 笔 · 佣金万1保底5元+半价差</div></Card></Col>
          </Row>

          <Card size="small" style={{ marginBottom: 14 }}
            title={`资金曲线 ¥${data.capital.toLocaleString()} 起 · 策略 vs 等权买入持有(已扣真实交易成本)`}>
            <ReactECharts option={equityOpt} notMerge style={{ height: 380 }} />
          </Card>

          <Card size="small"
            title={`历史交易 ${data.stats.n_trades} 笔 · 胜率 ${data.stats.winrate}% · 平均盈利 +${data.stats.avg_win}% / 平均亏损 ${data.stats.avg_loss}% · 平均持有 ${data.stats.avg_hold} 天`}>
            <Table rowKey={(r: Trade) => r.code + r.entry_date} size="small" columns={tradeCols}
              dataSource={data.trades} pagination={{ pageSize: 20, showSizeChanger: false }} />
            <div style={{ fontSize: 12, color: '#999', marginTop: 8 }}>
              典型趋势跟踪画像:<b>胜率低但盈亏比高</b>——多数交易被止损小亏出局,少数几笔吃到完整趋势。
              必须能忍受连续小亏,否则做不下去。价格为原始收盘价(未复权),收益率按复权价计算;
              ETF 份额折算日前后的买卖价不可直接相减。参数在 45 个配置中选出,属样本内最优,实盘表现大概率不及回测。研究信号,非投资建议。
            </div>
          </Card>
        </>
      )}
    </div>
  )
}

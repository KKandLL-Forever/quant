// 小西西弗动量轮动策略复现(龙头/全天候/行业):Tab 切换,每策略 = 绩效卡 + 累计收益曲线 + 调仓动作表。数据走 /api/xiaoxifu。
import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Spin, Table, Tag, InputNumber, Statistic, Row, Col, Tabs, Select, Modal, Input, message } from 'antd'
import { LineChart, Line, XAxis, YAxis, Tooltip, Legend, CartesianGrid, ResponsiveContainer } from 'recharts'
import { Header, PageTitle } from '../../shell'

interface Pick { code: string; name: string; weight: number; price?: number | null }
interface Rebalance { date: string; picks: Pick[]; state?: string; next?: boolean }
interface Perf { 策略: string; 年化收益: number | null; 年化波动率: number | null; 最大回撤: number | null; 夏普比率: number | null; 卡玛比率: number | null }
interface Payload {
  ok: boolean; error?: string
  params: { N: number; K: number; L: number; start: string; end: string }
  cols: string[]; summary: Perf[]
  equity: Record<string, number | string>[]; rebalances: Rebalance[]
}

interface StratCfg { key: string; label: string; hasKL: boolean; fixed?: boolean; showL?: boolean; showShares?: boolean; showPool?: boolean; kicker: string; desc: string; actionTitle?: string }
interface PoolItem { code: string; name: string; industry: string }
const STRATS: StratCfg[] = [
  { key: 'leader', label: '龙头动量轮动', hasKL: true, showPool: true, kicker: 'Leader Momentum · 年化~102%(含手续费)',
    desc: '各赛道龙头股 · 每 K 交易日调仓取前 L · 基准科创50ETF · 股票池可自定义' },
  { key: 'allweather', label: '全天候动量轮动', hasKL: false, kicker: 'All-Weather · 年化~39%(含手续费)',
    desc: '纳指/沪深300/黄金 3 只跨资产 ETF · 每日调仓正动量全取 · 基准沪深300ETF' },
  { key: 'industry', label: '行业动量轮动', hasKL: true, showShares: true, kicker: 'Industry Rotation · 年化~20%(含手续费)',
    desc: '13 只主流行业 ETF · 每 K 交易日调仓取前 L · 基准科创50ETF' },
  { key: 'regime', label: '牛熊切换组合', hasKL: false, fixed: true, showL: true, showShares: true, kicker: 'Regime Switch · 年化~112%(含手续费) · 回撤减半',
    desc: '沪深300「MA30 且 MA60 同时走坏」→ 切全天候避险,否则持龙头(用「龙头动量」页设定的股票池) · 对照纯龙头/纯全天候',
    actionTitle: '牛熊切换记录' },
]
const PALETTE = ['#c0392b', '#1f8e5a', '#3b82f6']
const pct = (v: number) => `${(v * 100).toFixed(1)}%`

function StrategyView({ cfg }: { cfg: StratCfg }) {
  const [N, setN] = useState(20)
  const [K, setK] = useState(5)
  const [L, setL] = useState(5)
  const [capital, setCapital] = useState(100000)
  const [stockOpts, setStockOpts] = useState<{ label: string; value: string }[]>([])
  const [nameMap, setNameMap] = useState<Record<string, string>>({})
  const [defaultPool, setDefaultPool] = useState<PoolItem[]>([])
  const [pool, setPool] = useState<PoolItem[]>([])
  const [poolOpen, setPoolOpen] = useState(false)
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!cfg.showPool) return
    fetch('/api/leader_pool').then(r => r.json()).then((j: { ok: boolean; default: PoolItem[]; universe: PoolItem[]; saved?: PoolItem[] }) => {
      if (!j.ok) return
      setStockOpts(j.universe.map(it => ({ label: `${it.name} ${it.code}`, value: it.code })))
      setNameMap(Object.fromEntries(j.universe.map(it => [it.code, it.name])))
      setDefaultPool(j.default)
      setPool(j.saved && j.saved.length ? j.saved : j.default)   // 后端保存的池优先
    })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const savePool = (p: PoolItem[]) => {
    setPool(p)
    localStorage.setItem('xiaoxifu:leaderPool', JSON.stringify(p))
    fetch('/api/leader_pool_save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pool: p }) }).catch(() => {})
  }

  const load = async () => {
    setLoading(true)
    try {
      let codes = pool.map(p => p.code).filter(Boolean)
      if (cfg.key === 'regime') {   // 牛熊组合的龙头腿也用编辑好的股票池(存在 localStorage)
        const saved = localStorage.getItem('xiaoxifu:leaderPool')
        if (saved) codes = (JSON.parse(saved) as PoolItem[]).map(p => p.code).filter(Boolean)
      }
      const r = await fetch('/api/xiaoxifu', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy: cfg.key, N, K, L, start: '2024-01-01', codes: (cfg.showPool || cfg.key === 'regime') && codes.length ? codes : undefined }),
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
  const colorOf = (name: string) => data ? PALETTE[data.cols.indexOf(name) % PALETTE.length] || '#888' : '#888'

  const rebColumns = [
    { title: cfg.actionTitle ? '切换日' : '调仓日', dataIndex: 'date', width: 190,
      render: (d: string, row: Rebalance) => row.next ? <b style={{ color: '#0b6e4f' }}>🔮 {d}</b> : d },
    ...(cfg.actionTitle ? [{
      title: '切换为', dataIndex: 'state', width: 150,
      render: (state: string) => <Tag color={state?.includes('避险') ? 'blue' : 'red'}><b>{state}</b></Tag>,
    }] : []),
    {
      title: cfg.showShares ? `持仓(权重 · ¥${capital.toLocaleString()}下可买份额)` : cfg.actionTitle ? '当期持仓' : '持仓(权重)', dataIndex: 'picks',
      render: (picks: Pick[], row: Rebalance) => picks.length === 0
        ? <span style={{ color: '#999' }}>空仓(无正动量标的)</span>
        : picks.map(p => {
          const lots = cfg.showShares && p.price ? Math.floor(capital * p.weight / p.price / 100) * 100 : null
          return (
            <Tag key={p.code || p.name} color={row.state?.includes('避险') ? 'blue' : 'red'} style={{ marginBottom: 4 }}>
              {p.name} {p.code && <span style={{ opacity: .6 }}>{p.code}</span>} <b>{(p.weight * 100).toFixed(1)}%</b>
              {cfg.showShares && p.price && <span style={{ marginLeft: 4 }}>· ¥{p.price} · {lots && lots > 0 ? <b>{lots.toLocaleString()}{/^(5|15|16)/.test(p.code) ? '份' : '股'}</b> : <span style={{ color: '#c0392b' }}>无仓位</span>}</span>}
            </Tag>
          )
        }),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        {!cfg.fixed && <><span>动量周期 N</span><InputNumber min={5} max={60} value={N} onChange={v => setN(v || 20)} size="small" /></>}
        {cfg.hasKL && <><span>调仓间隔 K</span><InputNumber min={1} max={20} value={K} onChange={v => setK(v || 5)} size="small" /></>}
        {(cfg.hasKL || cfg.showL) && <><span>持仓数 L</span><InputNumber min={1} max={22} value={L} onChange={v => setL(v || 5)} size="small" /></>}
        {cfg.showShares && <><span>资金</span><InputNumber min={10000} step={10000} value={capital}
          onChange={v => setCapital(v || 100000)} size="small" style={{ width: 130 }}
          formatter={v => `¥${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')} parser={v => Number((v || '').replace(/[^\d]/g, ''))} /></>}
        {cfg.showPool && <Button size="small" onClick={() => setPoolOpen(true)}
          style={{ background: 'linear-gradient(135deg,#0b6e4f,#0f8a63)', color: '#fff', border: 'none',
            fontWeight: 500, boxShadow: '0 2px 8px -3px rgba(11,110,79,.6)' }}>
          ✎ 编辑股票池 <b style={{ marginLeft: 2 }}>{pool.length}</b> 只</Button>}
        <Button type="primary" size="small" onClick={load} loading={loading}>运行回测</Button>
        <span style={{ fontSize: 12, color: 'var(--ink-soft)' }}>{cfg.desc} · 2024-01-01 起 · 权重滞后1天(T+1执行)</span>
      </div>

      {loading && <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>}

      {!loading && data && (
        <>
          <Row gutter={12} style={{ marginBottom: 16 }}>
            {data.summary.map(s => (
              <Col key={s.策略} span={8}>
                <Card size="small" title={s.策略} styles={{ header: { color: colorOf(s.策略), fontWeight: 600 } }}>
                  <Row gutter={8}>
                    <Col span={12}><Statistic title="年化收益" value={s.年化收益 ?? '—'} suffix="%" valueStyle={{ color: '#c0392b', fontSize: 20 }} /></Col>
                    <Col span={12}><Statistic title="最大回撤" value={s.最大回撤 ?? '—'} suffix="%" valueStyle={{ fontSize: 20 }} /></Col>
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
                <Tooltip contentStyle={{ background: '#fffdf8', border: '1px solid #e6e0d3', borderRadius: 6, fontSize: 12 }} formatter={(v: any) => pct(Number(v))} />
                <Legend />
                {data.cols.map(c => (
                  <Line key={c} type="linear" dataKey={c} name={c} stroke={colorOf(c)}
                    strokeWidth={data.cols.indexOf(c) === 0 ? 2 : 1.4} dot={false} isAnimationActive={false} connectNulls />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </Card>

          <Card size="small" title={cfg.actionTitle
            ? `${cfg.actionTitle}(共 ${data.rebalances.length} 次切换,最新在前)`
            : `调仓动作(共 ${data.rebalances.length} 次${cfg.hasKL ? `,每 ${data.params.K} 交易日一次` : ',每日'},最新在前)`}>
            <Table rowKey="date" size="small" columns={rebColumns} dataSource={data.rebalances}
              onRow={(r: Rebalance) => ({ style: r.next ? { background: '#eef6f1' } : {} })}
              pagination={{ pageSize: 20, showSizeChanger: false }} />
          </Card>
        </>
      )}

      {cfg.showPool && <Modal open={poolOpen} width={640} title="编辑龙头股票池(赛道 : 股票)" onCancel={() => setPoolOpen(false)}
        footer={<div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span>
            <Button size="small" onClick={() => savePool([...pool, { industry: '', code: '', name: '' }])}>+ 添加一行</Button>
            <Button size="small" style={{ marginLeft: 8 }} onClick={() => savePool(defaultPool)}>恢复默认22只</Button>
          </span>
          <Button type="primary" onClick={() => setPoolOpen(false)}>完成</Button>
        </div>}>
        <div style={{ maxHeight: 460, overflow: 'auto' }}>
          {pool.map((row, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center' }}>
              <Input style={{ width: 90 }} placeholder="赛道" value={row.industry}
                onChange={e => savePool(pool.map((r, j) => j === i ? { ...r, industry: e.target.value } : r))} />
              <span>:</span>
              <Select showSearch style={{ flex: 1 }} placeholder="搜索代码/名称" value={row.code || undefined}
                options={stockOpts} optionFilterProp="label"
                onChange={v => savePool(pool.map((r, j) => j === i ? { ...r, code: v, name: nameMap[v] || v } : r))} />
              <Button size="small" danger onClick={() => savePool(pool.filter((_, j) => j !== i))}>删</Button>
            </div>
          ))}
          {pool.length === 0 && <div style={{ color: '#999', padding: 20, textAlign: 'center' }}>空池,点「添加一行」或「恢复默认」</div>}
        </div>
      </Modal>}
    </div>
  )
}

export default function XiaoxifuPage() {
  return (
    <div style={{ maxWidth: 1850, margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="小西西弗的量化之路 · 风险调整动量 + 定期轮动(复现)">小西西弗动量轮动</PageTitle>
      <Tabs items={STRATS.map(s => ({ key: s.key, label: s.label, children: <StrategyView cfg={s} /> }))} />
    </div>
  )
}

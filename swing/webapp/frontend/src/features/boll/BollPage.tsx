// BOLL缩口扩张+MACD金叉 当日信号(ML大盘池)。突出「第二次信号(前30天已出过)+大盘健康」为重点买点。数据走 /api/boll。
import { useEffect, useState } from 'react'
import { Button, Card, Spin, Table, Tag, Select, message } from 'antd'
import { Header, PageTitle } from '../../shell'

interface Sig { code: string; name: string; price: number; vol_ratio: number; atr_pct: number; days_since: number | null; is_rep30: boolean; ma60_up: boolean; rs_win: boolean; main_concepts?: string[] }
interface Payload { ok: boolean; error?: string; date: string; mkt_up: boolean | null; mkt_bad: boolean | null; pool: string; signals: Sig[]; concept_date?: string | null }

const POOLS = [{ value: 'ml', label: 'ML主升浪池(前800+热股)' }, { value: 'csi1000', label: '中证1000' }, { value: 'csi2000', label: '中证2000' }]

export default function BollPage() {
  const [pool, setPool] = useState('ml')
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/boll', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pool }) })
      const j: Payload = await r.json()
      if (!j.ok) throw new Error(j.error || '请求失败')
      setData(j)
    } catch (e) { message.error((e as Error).message) } finally { setLoading(false) }
  }
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const healthy = data ? data.mkt_bad === false : false
  const cols = [
    { title: '', dataIndex: 'is_rep30', width: 60, render: (_: boolean, r: Sig) => healthy && r.is_rep30 && (r.main_concepts?.length ?? 0) > 0 ? <Tag color="red">重点</Tag> : r.is_rep30 ? <Tag color="orange">第二次</Tag> : <Tag>首次</Tag> },
    { title: '名称', dataIndex: 'name', render: (v: string, r: Sig) => <span><b>{v}</b> <span style={{ opacity: .55 }}>{r.code}</span></span> },
    { title: '现价', dataIndex: 'price', render: (v: number) => `¥${v}` },
    { title: 'MA60趋势', dataIndex: 'ma60_up', width: 90, render: (v: boolean) => v ? <Tag color="red">上行</Tag> : <Tag color="green">下行</Tag> },
    { title: '相对大盘', dataIndex: 'rs_win', width: 90, render: (v: boolean) => v ? <Tag color="red">跑赢</Tag> : <Tag color="green">跑输</Tag> },
    { title: <span>主线概念{data?.concept_date && data.concept_date !== data.date ? <span style={{ color: '#c0392b', fontWeight: 400, marginLeft: 4 }}>⚠️今日概念数据未更新</span> : null}</span>, dataIndex: 'main_concepts', width: 180, render: (v: string[] | undefined) => (v && v.length) ? <span>{v.map(n => <Tag key={n} color="red" style={{ marginBottom: 2 }}>{n}</Tag>)}</span> : <span style={{ color: '#bbb' }}>—</span> },
    { title: '量比', dataIndex: 'vol_ratio', sorter: (a: Sig, b: Sig) => a.vol_ratio - b.vol_ratio, render: (v: number) => <b style={{ color: v >= 1 ? '#c0392b' : '#999' }}>{v}</b> },
    { title: 'ATR%', dataIndex: 'atr_pct', sorter: (a: Sig, b: Sig) => a.atr_pct - b.atr_pct, render: (v: number) => `${v}%` },
    { title: '距上次信号', dataIndex: 'days_since', sorter: (a: Sig, b: Sig) => (a.days_since ?? 999) - (b.days_since ?? 999),
      render: (v: number | null, r: Sig) => v == null ? <span style={{ color: '#999' }}>—(首次)</span> : <span style={{ color: r.is_rep30 ? '#c0392b' : '#5b554a' }}>{v}天{r.is_rep30 ? '(第二次)' : ''}</span> },
  ]

  return (
    <div style={{ maxWidth: 1850, margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="BOLL Squeeze Breakout · 缩口扩张 + MACD金叉(研究:boll_narrow_exit)">BOLL 突破信号</PageTitle>

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <span>股票池</span>
        <Select value={pool} onChange={setPool} options={POOLS} size="small" style={{ width: 240 }} />
        <Button type="primary" size="small" onClick={load} loading={loading}
          style={{ background: 'linear-gradient(135deg,#c0392b,#e05a3f)', border: 'none' }}>▶ 刷新当日信号</Button>
        <span style={{ fontSize: 12, color: 'var(--ink-soft)' }}>信号=BOLL缩口→扩张+MACD近3日金叉+站上中轨;次日入场,建议持有10~15日</span>
      </div>

      {loading && <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>}

      {!loading && data && (
        <>
          <Card size="small" style={{ marginBottom: 14, background: healthy ? '#eef6f1' : '#fbecec' }}>
            <b>最新交易日 {data.date}</b> · 沪深300 当天{data.mkt_up ? <span style={{ color: '#c0392b' }}>上涨</span> : <span style={{ color: '#1f8e5a' }}>下跌</span>}、
            <b style={{ color: healthy ? '#0b6e4f' : '#c0392b' }}>{healthy ? '健康' : '走坏(MA30&MA60同时走坏)'}</b>
            <div style={{ fontSize: 12, color: 'var(--ink-soft)', marginTop: 4 }}>
              重点买点(交叉回测验证):<b>大盘健康 + 第二次信号 + 所属概念处于RRG领先区</b> = 市场/个股/板块三重对齐,领先区为尾部增强(赢时更大)。持有约10~15日。
              {healthy ? '今日大盘健康,重点看下方红色「重点」(第二次+领先区概念)。' : <b style={{ color: '#c0392b' }}>今日大盘走坏,按纪律应空仓观望,信号仅供记录。</b>}
            </div>
          </Card>

          <Card size="small" title={`当日信号 ${data.signals.length} 条(重点=大盘健康+第二次+领先区概念,已置顶)`}>
            <Table rowKey="code" size="small" columns={cols} dataSource={data.signals} pagination={false}
              onRow={(r: Sig) => ({ style: r.is_rep30 && r.ma60_up && r.rs_win && healthy ? { background: '#fff7f5' } : {} })} />
          </Card>
          <div style={{ fontSize: 12, color: '#999', marginTop: 8 }}>
            量比=当日成交量/20日均量(&gt;1 为放量);ATR%=波动率/价;距上次信号=同股上一次信号到今天的天数(≤30 记为第二次)。
            本页为研究信号(见 boll_narrow_exit/README),非投资建议。
          </div>
        </>
      )}
    </div>
  )
}

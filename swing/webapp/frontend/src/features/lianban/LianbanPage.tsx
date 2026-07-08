// 连板信号:2板→4板(2进4·到4板)每日概率打分。数据走 /api/lianban/score(后端 subprocess 跑 ml_score_2lb_v6,按日期缓存)。
import { useEffect, useState } from 'react'
import { Button, Card, Spin, Table, Tag, DatePicker, message, Modal } from 'antd'
import dayjs from 'dayjs'
import { Header, PageTitle } from '../../shell'
import { StockName } from '../../StockInfo'
import { useAna } from '../../App'

interface Sig { ts_code: string; name: string; proba: number; tier: string | null; top10: boolean; rank: number | null; posn: string | null; concepts: string }
interface Payload {
  ok: boolean; error?: string; date?: string; n_signals?: number; note?: string; cached?: boolean
  regime?: { label: string; color: string; msg: string }
  market_state?: Record<string, number>; auc?: number
  tiers?: { proba: number; label: string; win: number | null }[]
  top10_thr?: number | null; signals?: Sig[]
}

const fmtDate = (d: string) => (d && d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}` : d)

export default function LianbanPage() {
  const [date, setDate] = useState<string>('')
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(false)
  const [retrain, setRetrain] = useState(false)
  const analyze = useAna()

  const load = async (d: string, refresh = false) => {
    setLoading(true)
    try {
      const q = new URLSearchParams()
      if (d) q.set('date', d)
      if (refresh) q.set('refresh', 'true')
      const j: Payload = await (await fetch(`/api/lianban/score?${q}`)).json()
      if (!j.ok) throw new Error(j.error || '打分失败')
      setData(j)
    } catch (e) { message.error((e as Error).message) } finally { setLoading(false) }
  }
  useEffect(() => { load('') }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const doRetrain = async () => {
    setRetrain(true)
    try {
      const j = await (await fetch('/api/lianban/retrain', { method: 'POST' })).json()
      Modal[j.ok ? 'success' : 'error']({ title: j.ok ? '部署模型重训完成' : '重训失败', width: 680,
        content: <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, maxHeight: 400, overflow: 'auto' }}>{j.log || j.error}</pre> })
    } catch (e) { message.error((e as Error).message) } finally { setRetrain(false) }
  }

  const cols = [
    { title: '', dataIndex: 'top10', width: 70, render: (_: boolean, r: Sig) => r.top10 ? <Tag color="red">Top{r.rank}</Tag> : <Tag>—</Tag> },
    { title: '名称', dataIndex: 'name', render: (v: string, r: Sig) => <span><StockName code={r.ts_code}><a onClick={() => data?.date && analyze(r.ts_code, fmtDate(data.date), false, v)}><b>{v}</b></a></StockName> <span style={{ opacity: .55 }}>{r.ts_code}</span></span> },
    { title: '到4板概率(2进4)', dataIndex: 'proba', defaultSortOrder: 'descend' as const, sorter: (a: Sig, b: Sig) => a.proba - b.proba,
      render: (v: number, r: Sig) => <b style={{ color: r.top10 ? '#c0392b' : '#999' }}>{(v * 100).toFixed(1)}%</b> },
    { title: '分位档', dataIndex: 'tier', width: 90, render: (v: string | null) => v ? <Tag color={v.includes('1%') ? 'red' : v.includes('5%') ? 'orange' : 'gold'}>{v}</Tag> : <span style={{ color: '#bbb' }}>档外</span> },
    { title: '仓位建议', dataIndex: 'posn', width: 110, render: (v: string | null) => v ? <b>{v}</b> : <span style={{ color: '#bbb' }}>—</span> },
    { title: '概念', dataIndex: 'concepts', render: (v: string) => v ? <span style={{ fontSize: 12, color: '#5b554a' }}>{v}</span> : <span style={{ color: '#bbb' }}>—</span> },
  ]

  const ms = data?.market_state
  return (
    <div style={{ maxWidth: 1500, margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="2进4·到4板 · XGBoost v6 部署模型(first10)">连板信号</PageTitle>

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <span>交易日</span>
        <DatePicker size="small" value={data?.date ? dayjs(fmtDate(data.date)) : null} allowClear
          onChange={(_, ds) => { const d = (ds as string || '').replace(/-/g, ''); setDate(d); load(d) }}
          disabledDate={d => d && d > dayjs().endOf('day')} />
        <Button size="small" type="primary" loading={loading} onClick={() => load(date, true)}
          style={{ background: 'linear-gradient(135deg,#c0392b,#e05a3f)', border: 'none' }}>▶ 刷新重跑打分</Button>
        <Button size="small" loading={retrain} onClick={doRetrain}>重训部署模型</Button>
        <span style={{ fontSize: 12, color: 'var(--ink-soft)' }}>信号=当日2板个股;proba=模型预测该2板最终走到≥4连板的概率;Top10%以上给仓位建议。按日期缓存,刷新才重跑(约10~20秒)</span>
      </div>

      {loading && <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>}

      {!loading && data && data.regime && (
        <Card size="small" style={{ marginBottom: 14, borderLeft: `4px solid ${data.regime.color}` }}>
          <b style={{ color: data.regime.color, fontSize: 15 }}>{data.regime.label}</b>
          <span style={{ marginLeft: 10, fontSize: 13, color: 'var(--ink-soft)' }}>{data.regime.msg}</span>
          <div style={{ fontSize: 12, color: '#999', marginTop: 6 }}>
            最新 {fmtDate(data.date || '')} · 2板信号 {data.n_signals} 只
            {ms && <> · 距60日高 {ms.dist_h60 != null ? (ms.dist_h60 * 100).toFixed(1) + '%' : '—'} · 5日均2板晋级率 {ms.rate_2lb_ma5 != null ? (ms.rate_2lb_ma5 * 100).toFixed(1) + '%' : '—'} · 最高 {ms.max_lianban ?? '—'} 板</>}
            {data.tiers && <> · 分位切点 {data.tiers.map(t => `${t.label}≥${(t.proba * 100).toFixed(0)}%`).join(' / ')}</>}
            {data.cached && <span style={{ marginLeft: 8, color: '#0b6e4f' }}>(缓存)</span>}
          </div>
        </Card>
      )}

      {!loading && data && (data.note ? <Card size="small">{data.note}</Card> : (
        <Card size="small" title={`当日 2板信号 ${data.signals?.length ?? 0} 只(按到4板概率排序,Top10%以上标红)`}>
          <Table rowKey="ts_code" size="small" columns={cols} dataSource={data.signals || []}
            pagination={{ pageSize: 20, showSizeChanger: false }}
            rowClassName={(r: Sig) => r.top10 ? 'lianban-top' : ''}
            onRow={(r: Sig) => ({ style: r.top10 ? { background: '#fff7f5' } : {} })} />
          <div style={{ fontSize: 12, color: '#999', marginTop: 8 }}>
            点名字可跑 LLM 分析。模型=xgb_2lb_v6_deploy(≤当日全量数据训练的实盘部署版);历史日期为复盘,部署模型对历史有数据穿越、成绩偏乐观,仅供参考。研究信号,非投资建议。
          </div>
        </Card>
      ))}
    </div>
  )
}

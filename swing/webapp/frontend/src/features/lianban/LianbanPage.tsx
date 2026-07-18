// 连板信号:2板→4板(2进4·到4板)每日概率打分。数据走 /api/lianban/score(后端 subprocess 跑 ml_score_2lb_v6,按日期缓存)。
import { useEffect, useState } from 'react'
import { Button, Card, Table, Tag, DatePicker, message, notification } from 'antd'
import dayjs from 'dayjs'
import { Header, PageTitle, SkelStatus, SkelTable } from '../../shell'
import { StockName } from '../../StockInfo'
import { useAna } from '../../App'

interface Sig { ts_code: string; name: string; proba: number; tier: string | null; top10: boolean; rank: number | null; posn: string | null; concepts: string }
interface HistRow { ts_code: string; name: string; trade_date: string; proba: number; boards: number | null; ret_real: number | null; ret_t7: number | null }
interface HistPayload { ok: boolean; error?: string; tier?: string; version?: string; start?: string; cached?: boolean; rows?: HistRow[]; summary?: { n: number; avg_real?: number | null; med_real?: number | null; win?: number | null; to3?: number | null; to4?: number | null } }
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
  const [hist, setHist] = useState<HistPayload | null>(null)
  const [histLoading, setHistLoading] = useState(false)
  const analyze = useAna()

  const loadHist = async (refresh = false) => {
    setHistLoading(true)
    try {
      const j: HistPayload = await (await fetch(`/api/lianban/history?start=20250101&tier=10${refresh ? '&refresh=true' : ''}`)).json()
      if (!j.ok) throw new Error(j.error || '历史扫描失败')
      setHist(j)
    } catch (e) { message.error((e as Error).message) } finally { setHistLoading(false) }
  }
  useEffect(() => { loadHist() }, []) // eslint-disable-line react-hooks/exhaustive-deps

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
  useEffect(() => {   // 进页切到最新交易日并重新打分(不吃旧缓存)
    fetch('/api/trade_cal').then(r => r.json()).then((j: { latest?: string | null }) => {
      const latest = j?.latest ? j.latest.replace(/-/g, '') : ''
      setDate(latest); load(latest, true)
    }).catch(() => load('', true))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const doRetrain = async () => {
    setRetrain(true)
    message.loading({ content: '正在重训 2进4 模型(缺评估模型会自动先训评估,分钟级)…', key: 'lbtrain', duration: 0 })
    try {
      const j = await (await fetch('/api/lianban/retrain', { method: 'POST' })).json()
      message.destroy('lbtrain')
      if (!j.ok) {
        notification.error({ message: '重训失败', duration: null, style: { width: 560 },
          description: <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, maxHeight: 360, overflow: 'auto', margin: 0 }}>{j.log || j.error}</pre> })
        return
      }
      const m = j.metrics
      notification.success({
        message: `2进4 部署模型重训完成${j.eval_ran ? '(已自动补训评估模型)' : ''}`,
        duration: null, placement: 'topRight', style: { width: 460 },
        description: m ? (
          <div style={{ fontSize: 13 }}>
            <div style={{ marginBottom: 6 }}>轮数 <b>{m.rounds}</b> · 样本 <b>{m.n_train}</b> · 到4板正例率 <b>{(m.pos_rate * 100).toFixed(1)}%</b></div>
            <div style={{ color: '#888', marginBottom: 4 }}>训练区间 {m.train_range}</div>
            <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
              <thead><tr style={{ color: '#888' }}><td>档</td><td>切点</td><td>实收</td><td>到4板</td><td>n</td></tr></thead>
              <tbody>{(m.tiers || []).map((t: any) => (
                <tr key={t.label} style={{ borderTop: '1px solid #eee' }}>
                  <td>{t.label}</td><td>≥{(t.proba * 100).toFixed(0)}%</td>
                  <td style={{ color: t.ret >= 0 ? '#c0392b' : '#1f8e5a' }}>{t.ret >= 0 ? '+' : ''}{(t.ret * 100).toFixed(1)}%</td>
                  <td>{(t.hit4 * 100).toFixed(0)}%</td><td>{t.n}</td>
                </tr>))}</tbody>
            </table>
          </div>
        ) : '训练完成(无指标返回)',
      })
      load(date, true); loadHist(true)
    } catch (e) { message.destroy('lbtrain'); message.error((e as Error).message) } finally { setRetrain(false) }
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
    <div style={{ maxWidth: 'min(2000px, 96vw)', margin: '18px auto', padding: '0 16px' }}>
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

      {loading && <><SkelStatus /><SkelTable /></>}

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

      <Card size="small" style={{ marginTop: 16 }} loading={histLoading}
        title={<span>历史信号(Top10% · v6评估版·无穿越 · 2025年起)
          {hist?.summary && hist.summary.n > 0 && <span style={{ marginLeft: 12, fontSize: 12, color: 'var(--ink-soft)' }}>
            {hist.summary.n} 条 · 智能止盈均收益 <b style={{ color: (hist.summary.avg_real ?? 0) >= 0 ? '#c0392b' : '#1f8e5a' }}>{hist.summary.avg_real != null ? (hist.summary.avg_real * 100).toFixed(1) + '%' : '—'}</b>
            · 胜率 <b>{hist.summary.win != null ? (hist.summary.win * 100).toFixed(0) + '%' : '—'}</b>
            · 到3板率 <b>{hist.summary.to3 != null ? (hist.summary.to3 * 100).toFixed(0) + '%' : '—'}</b>
            · 到4板率 <b>{hist.summary.to4 != null ? (hist.summary.to4 * 100).toFixed(0) + '%' : '—'}</b>
            {hist.cached && <span style={{ marginLeft: 6, color: '#0b6e4f' }}>(缓存)</span>}
          </span>}
          <Button size="small" style={{ marginLeft: 12 }} loading={histLoading} onClick={() => loadHist(true)}>刷新重扫</Button>
        </span>}>
        <Table rowKey={(r: HistRow) => r.ts_code + r.trade_date} size="small" pagination={{ pageSize: 20, showSizeChanger: false }}
          dataSource={hist?.rows || []}
          columns={[
            { title: '信号日', dataIndex: 'trade_date', width: 110, defaultSortOrder: 'descend' as const, sorter: (a: HistRow, b: HistRow) => a.trade_date < b.trade_date ? -1 : 1 },
            { title: '名称', dataIndex: 'name', render: (v: string, r: HistRow) => <span><StockName code={r.ts_code}><a onClick={() => analyze(r.ts_code, r.trade_date, false, v)}><b>{v}</b></a></StockName> <span style={{ opacity: .55 }}>{r.ts_code}</span></span> },
            { title: '到4板概率', dataIndex: 'proba', sorter: (a: HistRow, b: HistRow) => a.proba - b.proba, render: (v: number) => <b>{(v * 100).toFixed(1)}%</b> },
            { title: '最终高度', dataIndex: 'boards', width: 100, sorter: (a: HistRow, b: HistRow) => (a.boards ?? 0) - (b.boards ?? 0), render: (v: number | null) => v == null ? <span style={{ color: '#bbb' }}>—</span> : <Tag color={v >= 4 ? 'red' : v >= 3 ? 'orange' : 'default'}>{v}板</Tag> },
            { title: '智能止盈实收', dataIndex: 'ret_real', sorter: (a: HistRow, b: HistRow) => (a.ret_real ?? -9) - (b.ret_real ?? -9), render: (v: number | null) => v == null ? <span style={{ color: '#999' }}>持有中/未结算</span> : <b style={{ color: v >= 0 ? '#c0392b' : '#1f8e5a' }}>{v > 0 ? '+' : ''}{(v * 100).toFixed(1)}%</b> },
            { title: 'T+7', dataIndex: 'ret_t7', sorter: (a: HistRow, b: HistRow) => (a.ret_t7 ?? -9) - (b.ret_t7 ?? -9), render: (v: number | null) => v == null ? '—' : <span style={{ color: v >= 0 ? '#c0392b' : '#1f8e5a' }}>{v > 0 ? '+' : ''}{(v * 100).toFixed(1)}%</span> },
          ]} />
        <div style={{ fontSize: 12, color: '#999', marginTop: 6 }}>
          v6 评估版(walk-forward、末折≤2023,对 2024~ 是干净样本外),历史成绩可信、无穿越。智能止盈=2板次日开盘买、炸板次日开盘卖、连板持有到断板当日开盘卖。最新几条可能未结算(实收显示持有中)。
        </div>
      </Card>
    </div>
  )
}

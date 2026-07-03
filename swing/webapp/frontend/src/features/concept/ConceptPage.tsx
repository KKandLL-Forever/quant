// 概念轮动:扩散指标 + RRG 四象限(复现「做量化的西蒙」框架)。RRG散点(X=RS强度,Y=RS动量,气泡=扩散度,色=象限)+主线候选表。数据走 /api/concept。
import { useEffect, useState } from 'react'
import { Button, Card, Spin, Table, Tag, Select, message } from 'antd'
import { ScatterChart, Scatter, XAxis, YAxis, ZAxis, ReferenceLine, ReferenceArea, Tooltip, CartesianGrid, ResponsiveContainer, Cell } from 'recharts'
import { Header, PageTitle } from '../../shell'

interface Cpt { name: string; code: string; diffusion: number; diffusion_raw: number; mom20: number | null; rs_ratio: number; rs_momentum: number; quadrant: string; main: boolean }
interface Payload { ok: boolean; error?: string; date: string; bench: string; concepts: Cpt[] }

const BENCH = [{ value: '000852.SH', label: '中证1000' }, { value: '000001.SH', label: '上证综指' }, { value: '399852.SZ', label: '中证2000' }]
const UP = [{ value: 'ma20', label: '站上MA20' }, { value: 'pctup', label: '当日上涨' }]
const QC: Record<string, string> = { 领先: '#c0392b', 改善: '#e08e0b', 转弱: '#8a7f6a', 落后: '#1f8e5a' }
const pct = (x: number | null) => x == null ? '—' : `${(x * 100).toFixed(1)}%`

export default function ConceptPage() {
  const [bench, setBench] = useState('000852.SH')
  const [up, setUp] = useState('ma20')
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/concept', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ bench, up }) })
      const j: Payload = await r.json()
      if (!j.ok) throw new Error(j.error || '请求失败')
      setData(j)
    } catch (e) { message.error((e as Error).message) } finally { setLoading(false) }
  }
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const cs = data?.concepts || []
  const mains = cs.filter(c => c.main)
  // 散点只画扩散榜 top60,避免 400 个点糊成一团
  const scatter = [...cs].sort((a, b) => b.diffusion - a.diffusion).slice(0, 60)
  const dist = cs.reduce((m, c) => { m[c.quadrant] = (m[c.quadrant] || 0) + 1; return m }, {} as Record<string, number>)

  const cols = [
    { title: '', dataIndex: 'main', width: 56, render: (v: boolean) => v ? <Tag color="red">主线</Tag> : null },
    { title: '概念', dataIndex: 'name', render: (v: string, r: Cpt) => <span><b>{v}</b> <span style={{ opacity: .5, fontSize: 11 }}>{r.code}</span></span> },
    { title: '象限', dataIndex: 'quadrant', width: 80, filters: Object.keys(QC).map(q => ({ text: q, value: q })), onFilter: (v: any, r: Cpt) => r.quadrant === v, render: (v: string) => <Tag color={QC[v]} style={{ color: '#fff', border: 'none' }}>{v}</Tag> },
    { title: '扩散(MA20)', dataIndex: 'diffusion', defaultSortOrder: 'descend' as const, sorter: (a: Cpt, b: Cpt) => a.diffusion - b.diffusion, render: (v: number) => <b>{pct(v)}</b> },
    { title: '当日原始', dataIndex: 'diffusion_raw', sorter: (a: Cpt, b: Cpt) => a.diffusion_raw - b.diffusion_raw, render: (v: number, r: Cpt) => <span style={{ color: r.diffusion_raw < r.diffusion - 0.15 ? '#1f8e5a' : '#5b554a' }}>{pct(v)}</span> },
    { title: '20日扩散动量', dataIndex: 'mom20', sorter: (a: Cpt, b: Cpt) => (a.mom20 ?? -9) - (b.mom20 ?? -9), render: (v: number | null) => <span style={{ color: (v ?? 0) > 0 ? '#c0392b' : '#1f8e5a' }}>{v == null ? '—' : (v > 0 ? '+' : '') + pct(v)}</span> },
    { title: 'RS强度', dataIndex: 'rs_ratio', sorter: (a: Cpt, b: Cpt) => a.rs_ratio - b.rs_ratio, render: (v: number) => v.toFixed(1) },
    { title: 'RS动量', dataIndex: 'rs_momentum', sorter: (a: Cpt, b: Cpt) => a.rs_momentum - b.rs_momentum, render: (v: number) => v.toFixed(1) },
  ]

  return (
    <div style={{ maxWidth: 1850, margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="Concept Rotation · 扩散指标 + RRG 四象限(研究:concept_rotation)">概念轮动</PageTitle>

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <span>基准</span>
        <Select value={bench} onChange={setBench} options={BENCH} size="small" style={{ width: 130 }} />
        <span>上涨口径</span>
        <Select value={up} onChange={setUp} options={UP} size="small" style={{ width: 120 }} />
        <Button type="primary" size="small" onClick={load} loading={loading}
          style={{ background: 'linear-gradient(135deg,#c0392b,#e05a3f)', border: 'none' }}>▶ 刷新当日</Button>
        <span style={{ fontSize: 12, color: 'var(--ink-soft)' }}>主线候选 = 扩散榜前40 且落在 RRG 领先/改善区。散点仅画扩散榜前60。</span>
      </div>

      {loading && <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>}

      {!loading && data && (
        <>
          <Card size="small" style={{ marginBottom: 14 }}>
            <b>最新交易日 {data.date}</b> · 基准 {BENCH.find(b => b.value === data.bench)?.label} ·
            四象限分布 {Object.keys(QC).map(q => <Tag key={q} color={QC[q]} style={{ color: '#fff', border: 'none', marginLeft: 6 }}>{q} {dist[q] || 0}</Tag>)}
            <span style={{ marginLeft: 10, color: '#c0392b', fontWeight: 600 }}>主线候选 {mains.length} 个</span>
          </Card>

          <Card size="small" title="RRG 相对轮动图(扩散榜前60;右上=领先、左上=改善、右下=转弱、左下=落后;气泡越大扩散越高)" style={{ marginBottom: 14 }}>
            <ResponsiveContainer width="100%" height={480}>
              <ScatterChart margin={{ top: 10, right: 30, bottom: 24, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
                <ReferenceArea x1={100} y1={100} fill="#c0392b" fillOpacity={0.04} />
                <ReferenceArea x1={100} y2={100} fill="#8a7f6a" fillOpacity={0.04} />
                <ReferenceArea x2={100} y1={100} fill="#e08e0b" fillOpacity={0.05} />
                <ReferenceArea x2={100} y2={100} fill="#1f8e5a" fillOpacity={0.04} />
                <ReferenceLine x={100} stroke="#999" /><ReferenceLine y={100} stroke="#999" />
                <XAxis type="number" dataKey="rs_ratio" name="RS强度" domain={['dataMin - 0.5', 'dataMax + 0.5']} tickFormatter={(v: number) => v.toFixed(1)} label={{ value: 'RS强度 (相对强度) →', position: 'insideBottom', offset: -12, fontSize: 12 }} />
                <YAxis type="number" dataKey="rs_momentum" name="RS动量" domain={['dataMin - 0.5', 'dataMax + 0.5']} tickFormatter={(v: number) => v.toFixed(1)} label={{ value: 'RS动量 ↑', angle: -90, position: 'insideLeft', fontSize: 12 }} />
                <ZAxis type="number" dataKey="diffusion" range={[40, 600]} />
                <Tooltip cursor={{ strokeDasharray: '3 3' }} content={({ active, payload }: any) => {
                  if (!active || !payload?.length) return null
                  const d: Cpt = payload[0].payload
                  return <div style={{ background: '#fffdf8', border: '1px solid #e6e0d3', borderRadius: 6, padding: '6px 10px', fontSize: 12 }}>
                    <b>{d.name}</b> <Tag color={QC[d.quadrant]} style={{ color: '#fff', border: 'none', marginLeft: 4 }}>{d.quadrant}</Tag>
                    <div>扩散 {pct(d.diffusion)} · 20日动量 {d.mom20 == null ? '—' : pct(d.mom20)}</div>
                    <div>RS强度 {d.rs_ratio.toFixed(1)} · RS动量 {d.rs_momentum.toFixed(1)}</div>
                  </div>
                }} />
                <Scatter data={scatter}>
                  {scatter.map((c, i) => <Cell key={i} fill={QC[c.quadrant]} fillOpacity={c.main ? 0.95 : 0.5} stroke={c.main ? '#17140f' : 'none'} strokeWidth={c.main ? 1.2 : 0} />)}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          </Card>

          <Card size="small" title={`全概念榜(${cs.length};点表头可排序/按象限筛选,主线候选置顶标红)`}>
            <Table rowKey="code" size="small" dataSource={[...cs].sort((a, b) => (b.main ? 1 : 0) - (a.main ? 1 : 0) || b.diffusion - a.diffusion)}
              columns={cols as any} pagination={{ pageSize: 30, showSizeChanger: false }}
              rowClassName={(r: Cpt) => r.main ? 'concept-main-row' : ''} />
          </Card>
        </>
      )}
    </div>
  )
}

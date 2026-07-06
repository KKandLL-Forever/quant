// 概念轮动:扩散指标 + RRG 四象限(复现「做量化的西蒙」框架)。RRG散点(X=RS强度,Y=RS动量,气泡=扩散度,色=象限)+主线候选表。数据走 /api/concept。
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Card, Spin, Table, Tag, Select, message } from 'antd'
import ReactECharts from 'echarts-for-react'
import { Header, PageTitle } from '../../shell'

interface Cpt { name: string; code: string; diffusion: number; diffusion_raw: number; mom20: number | null; rs_ratio: number; rs_momentum: number; chg: number | null; excess: number | null; quadrant: string; main: boolean; trail: number[][] }
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

  const chartRef = useRef<any>(null)
  const cs = data?.concepts || []
  const mains = cs.filter(c => c.main)
  // 散点只画扩散榜 top60,避免 400 个点糊成一团
  const scatter = [...cs].sort((a, b) => b.diffusion - a.diffusion).slice(0, 60)
  // domain 把所有点+轨迹都算进去→固定不抖,且轨迹永远落在框内
  const allPts = scatter.flatMap(c => [[c.rs_ratio, c.rs_momentum] as number[], ...c.trail])
  const xsv = allPts.map(p => p[0]), ysv = allPts.map(p => p[1])
  const xdom: [number, number] = allPts.length ? [Math.min(...xsv) - 0.3, Math.max(...xsv) + 0.3] : [98, 102]
  const ydom: [number, number] = allPts.length ? [Math.min(...ysv) - 0.3, Math.max(...ysv) + 0.3] : [98, 102]
  const dist = cs.reduce((m, c) => { m[c.quadrant] = (m[c.quadrant] || 0) + 1; return m }, {} as Record<string, number>)

  const rrgOpt = useMemo(() => {
    const [xmin, xmax] = xdom, [ymin, ymax] = ydom
    return {
      animation: false,
      grid: { left: 44, right: 24, top: 14, bottom: 42 },
      tooltip: {
        trigger: 'item', formatter: (p: any) => {
          const d: Cpt = p.data?.c; if (!d) return ''
          return `<b>${d.name}</b> ${d.quadrant}<br/>当日 ${d.chg == null ? '—' : (d.chg > 0 ? '+' : '') + d.chg + '%'} · 超额 ${d.excess == null ? '—' : (d.excess > 0 ? '+' : '') + d.excess + '%'}`
            + `<br/>扩散 ${pct(d.diffusion)} · 20日动量 ${d.mom20 == null ? '—' : pct(d.mom20)}<br/>RS强度 ${d.rs_ratio.toFixed(1)} · RS动量 ${d.rs_momentum.toFixed(1)}`
        },
      },
      xAxis: { type: 'value', min: xmin, max: xmax, name: 'RS强度 →', nameLocation: 'middle', nameGap: 24, axisLabel: { formatter: (v: number) => v.toFixed(1), fontSize: 10 }, splitLine: { show: false } },
      yAxis: { type: 'value', min: ymin, max: ymax, name: 'RS动量 ↑', nameLocation: 'middle', nameGap: 32, axisLabel: { formatter: (v: number) => v.toFixed(1), fontSize: 10 }, splitLine: { show: false } },
      series: [
        { id: 'trail', type: 'line', z: 1, silent: true, showSymbol: true, data: [], lineStyle: { width: 2.4, type: 'dashed' } },
        {
          id: 'pts', type: 'scatter', z: 2,
          markArea: {
            silent: true, data: [
              [{ coord: [100, 100], itemStyle: { color: '#c0392b', opacity: 0.045 } }, { coord: [xmax, ymax] }],
              [{ coord: [100, ymin], itemStyle: { color: '#8a7f6a', opacity: 0.045 } }, { coord: [xmax, 100] }],
              [{ coord: [xmin, 100], itemStyle: { color: '#e08e0b', opacity: 0.05 } }, { coord: [100, ymax] }],
              [{ coord: [xmin, ymin], itemStyle: { color: '#1f8e5a', opacity: 0.045 } }, { coord: [100, 100] }],
            ],
          },
          markLine: { silent: true, symbol: 'none', lineStyle: { color: '#999' }, label: { show: false }, data: [{ xAxis: 100 }, { yAxis: 100 }] },
          data: scatter.map(c => ({
            value: [c.rs_ratio, c.rs_momentum], c, symbolSize: 8 + c.diffusion * 30,
            itemStyle: { color: QC[c.quadrant], opacity: c.main ? 0.95 : 0.5, borderColor: c.main ? '#17140f' : 'transparent', borderWidth: c.main ? 1.2 : 0 },
            label: { show: c.main, formatter: c.name, position: 'top', fontSize: 10, color: '#333' },
            emphasis: { scale: false, itemStyle: { borderColor: '#17140f', borderWidth: 2 } },
          })),
        },
      ],
    }
  }, [scatter, xdom, ydom])

  const cols = [
    { title: '', dataIndex: 'main', width: 56, render: (v: boolean) => v ? <Tag color="red">主线</Tag> : null },
    { title: '概念', dataIndex: 'name', render: (v: string, r: Cpt) => <span><b>{v}</b> <span style={{ opacity: .5, fontSize: 11 }}>{r.code}</span></span> },
    { title: '象限', dataIndex: 'quadrant', width: 80, filters: Object.keys(QC).map(q => ({ text: q, value: q })), onFilter: (v: any, r: Cpt) => r.quadrant === v, render: (v: string) => <Tag color={QC[v]} style={{ color: '#fff', border: 'none' }}>{v}</Tag> },
    { title: '当日涨跌', dataIndex: 'chg', width: 90, sorter: (a: Cpt, b: Cpt) => (a.chg ?? 0) - (b.chg ?? 0), render: (v: number | null) => v == null ? '—' : <b style={{ color: v >= 0 ? '#c0392b' : '#1f8e5a' }}>{v > 0 ? '+' : ''}{v}%</b> },
    { title: '超额(vs基准)', dataIndex: 'excess', width: 100, sorter: (a: Cpt, b: Cpt) => (a.excess ?? 0) - (b.excess ?? 0), render: (v: number | null, r: Cpt) => v == null ? '—' : <span style={{ color: v >= 0 ? '#c0392b' : '#1f8e5a' }}>{v > 0 ? '+' : ''}{v}%{r.main && v <= -3 ? ' ⚠️' : ''}</span> },
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

          <Card size="small" title="RRG 相对轮动图(扩散榜前60;右上=领先、左上=改善、右下=转弱、左下=落后;气泡越大扩散越高;鼠标悬停圆圈→显示近4周轨迹,渐粗+流动方向=往哪转)" style={{ marginBottom: 14 }}>
            <ReactECharts option={rrgOpt} notMerge style={{ height: 480 }}
              onChartReady={(c: any) => { chartRef.current = c }}
              onEvents={{
                mouseover: (p: any) => {
                  const d: Cpt = p.data?.c; if (!d || !chartRef.current || d.trail.length < 2) return
                  const col = QC[d.quadrant]; const n = d.trail.length
                  const [px, py] = d.trail[n - 2], [lx, ly] = d.trail[n - 1]
                  const seg = Math.hypot(lx - px, ly - py) || 1
                  const ux = (lx - px) / seg, uy = (ly - py) / seg          // 数据坐标单位方向
                  const rot = -Math.atan2(ux, uy) * 180 / Math.PI
                  const R = (8 + d.diffusion * 30) / 2 + 18                 // 圆半径+留白(像素),确保箭头在圆外
                  const tdata: any[] = d.trail.map((t, i) => ({
                    value: [t[0], t[1]], symbol: i === 0 ? 'circle' : 'none', symbolSize: i === 0 ? 8 : 0,
                    itemStyle: i === 0 ? { color: '#fff', borderColor: col, borderWidth: 1.6 } : { color: col },
                  }))
                  tdata.push({ value: [lx, ly], symbol: 'arrow', symbolSize: 13, symbolRotate: rot, symbolOffset: [ux * R, -uy * R], itemStyle: { color: col } })
                  chartRef.current.setOption({ series: [{ id: 'trail', data: tdata, lineStyle: { color: col, width: 2.4, type: 'dashed' } }] })
                },
                mouseout: () => chartRef.current?.setOption({ series: [{ id: 'trail', data: [] }] }),
              }} />
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

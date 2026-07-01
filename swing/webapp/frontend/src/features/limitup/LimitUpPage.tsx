// 涨停统计:连板梯队(最新交易日)+ 各板数晋级成功率(区间)。数据走后端 /api/limitup(DuckDB limit_list_d)。
import { useEffect, useState } from 'react'
import { Input, Button, Card, Table, Spin, message } from 'antd'
import { Header, PageTitle } from '../../shell'
import { fetchLimitup, buildLadder, calcSuccessRates, type LimitStock } from '../../lib/limitUp'

const boardColor = (d: number) => (d >= 5 ? '#c0392b' : d >= 3 ? '#e07b39' : '#0b6e4f')

export default function LimitUpPage() {
  const [start, setStart] = useState('20250101')
  const [data, setData] = useState<{ dates: string[]; byDate: Map<string, LimitStock[]> } | null>(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true); setData(null)
    try { setData(await fetchLimitup(start)) }
    catch (e) { message.error((e as Error).message) }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  const latest = data?.dates[data.dates.length - 1]
  const ladder = data && latest ? buildLadder(latest, data.byDate.get(latest) || []) : null
  const rates = data ? calcSuccessRates(data.dates, data.byDate) : []
  const maxBoard = ladder?.groups[0]?.days ?? 0

  const rateCols = [
    { title: '板数', dataIndex: 'days', render: (d: number) => <b style={{ color: boardColor(d) }}>{d}板→{d + 1}板</b> },
    { title: '尝试', dataIndex: 'attempts', align: 'right' as const },
    { title: '成功', dataIndex: 'successes', align: 'right' as const },
    { title: '晋级成功率', dataIndex: 'rate', align: 'right' as const, render: (v: number) => <b>{(v * 100).toFixed(0)}%</b> },
  ]

  return (
    <div style={{ maxWidth: 1850, margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="Limit-up Ladder · 连板梯队 & 晋级成功率">涨停统计</PageTitle>

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16 }}>
        <span>起始</span><Input style={{ width: 120 }} value={start} onChange={e => setStart(e.target.value.trim())} onPressEnter={load} />
        <Button type="primary" loading={loading} onClick={load}>加载</Button>
        {data && <span style={{ fontSize: 13, color: 'var(--ink-soft)' }}>区间 {data.dates[0]} ~ {latest} · 共 {data.dates.length} 个交易日</span>}
      </div>

      {loading && <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>}

      {ladder && (
        <>
          <div style={{ fontSize: 14, marginBottom: 12 }}>
            最新交易日 <b>{latest}</b>:涨停 <b>{ladder.total}</b> 只 · 最高 <b style={{ color: boardColor(maxBoard) }}>{maxBoard} 板</b>
          </div>
          <Card size="small" title="连板梯队(最新交易日 · 高板在上)" style={{ marginBottom: 16 }}>
            {ladder.groups.map(g => (
              <div key={g.days} style={{ display: 'flex', gap: 10, padding: '6px 0', borderTop: '1px solid var(--line)' }}>
                <div style={{ minWidth: 54, fontWeight: 700, color: boardColor(g.days) }}>{g.days}板<span style={{ color: 'var(--ink-soft)', fontWeight: 400, fontSize: 12 }}> ×{g.stocks.length}</span></div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {g.stocks.map(s => (
                    <span key={s.tsCode} className="hold-chip" title={`${s.industry} 封单${(s.fdAmount / 1e8).toFixed(2)}亿 炸板${s.openTimes}次`}>
                      {s.name}{s.openTimes > 0 && <span className="hold-tag" style={{ background: '#e07b39' }}>炸{s.openTimes}</span>}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </Card>
          <Card size="small" title={`各板数晋级成功率(区间 ${data!.dates[0]} ~ ${latest})`}>
            <Table rowKey="days" columns={rateCols} dataSource={rates} size="small" pagination={false} />
          </Card>
        </>
      )}
    </div>
  )
}

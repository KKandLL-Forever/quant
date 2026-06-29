import React, { useState, useMemo } from 'react'
import { Table, Button, Modal, Select, InputNumber, Checkbox, Card, Spin, Tag, message, Input, Popover, Tabs } from 'antd'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const MD = ({ children }) => (
  <div className="md" style={{ fontSize: 13, maxHeight: 320, overflow: 'auto', background: '#fafafa', padding: 10 }}>
    <ReactMarkdown remarkPlugins={[remarkGfm]}>{children || '(空)'}</ReactMarkdown>
  </div>
)

const INIT = 150000

// 组合回测:4等份,出现信号占一份买入(同股未清仓不加仓,最多4只),按出场口径平仓
function portfolio(rows, exk, retk, cal) {
  const buys = {}
  rows.forEach(r => {
    if (r[retk] == null) return
    const ex = r[exk] || r.__latest
    ;(buys[r.date] = buys[r.date] || []).push({ ts: r.ts, ex, ret: r[retk], sc: r.score })
  })
  let cash = INIT, op = [], curve = []
  for (const d of cal) {
    op = op.filter(p => { if (p.ex <= d) { cash += p.amt * (1 + p.ret); return false } return true })
    const bs = (buys[d] || []).slice().sort((a, b) => b.sc - a.sc)
    for (const b of bs) {
      if (op.length >= 4) break
      if (op.some(p => p.ts === b.ts)) continue
      const unit = (cash + op.reduce((s, p) => s + p.amt, 0)) / 4
      if (cash + 1e-6 >= unit && unit > 0) { cash -= unit; op.push({ ts: b.ts, ex: b.ex, ret: b.ret, amt: unit }) }
    }
    curve.push([d, Math.round(cash + op.reduce((s, p) => s + p.amt, 0))])
  }
  return curve
}

// 按组合规则(15万4等份/最多4只/满仓放弃/同股不加仓)重放,产出交易记录;未参与的信号标错过
function tradeLog(rows, exk, retk, holdk, openk, cal) {
  const byDate = {}
  rows.forEach(r => { if (r[retk] == null) return; (byDate[r.date] = byDate[r.date] || []).push(r) })
  const last = cal[cal.length - 1]
  let op = []
  const log = []
  for (const d of cal) {
    op = op.filter(p => p.ex > d)
    const bs = (byDate[d] || []).slice().sort((a, b) => b.score - a.score)
    for (const r of bs) {
      let status
      if (op.some(p => p.ts === r.ts)) status = '已持有'
      else if (op.length >= 4) status = '满仓错过'
      else { status = '已交易'; op.push({ ts: r.ts, ex: r[exk] || last }) }
      log.push({ key: r.ts + r.date, ts: r.ts, name: r.name, date: r.date, status,
                 exit: r[exk], ret: r[retk], hold: r[holdk], open: r[openk] })
    }
  }
  return log
}

function Chart({ title, series }) {
  if (!series || series.length < 2) return null
  const W = 1600, H = 240, pad = 50
  const vals = series.map(p => p[1])
  let mn = Math.min(...vals), mx = Math.max(...vals); if (mn === mx) { mn *= 0.99; mx *= 1.01 }
  const n = series.length
  const X = i => pad + i * (W - 2 * pad) / (n - 1)
  const Y = v => H - pad - (v - mn) * (H - 2 * pad) / (mx - mn)
  let d = ''; for (let i = 0; i < n; i++) d += (i ? 'L' : 'M') + X(i).toFixed(1) + ',' + Y(series[i][1]).toFixed(1)
  const last = series[n - 1][1], ret = ((last / INIT - 1) * 100).toFixed(1), up = last >= INIT
  const months = []
  for (let i = 0; i < n; i++) if (i === 0 || series[i][0].slice(0, 7) !== series[i - 1][0].slice(0, 7)) months.push(i)
  const step = Math.ceil(months.length / 12)
  return (
    <svg width={W} height={H} style={{ maxWidth: '100%', border: '1px solid #eee', borderRadius: 8, background: '#fff' }}>
      <text x={pad} y={20} fontSize={14} fontWeight={700}>{title}  期末 {Math.round(last).toLocaleString()} ({up ? '+' : ''}{ret}%)</text>
      <line x1={pad} y1={Y(INIT)} x2={W - pad} y2={Y(INIT)} stroke="#bbb" strokeDasharray="4 4" />
      <text x={6} y={Y(mx) + 4} fontSize={11} fill="#999">{Math.round(mx).toLocaleString()}</text>
      <text x={6} y={Y(mn) + 4} fontSize={11} fill="#999">{Math.round(mn).toLocaleString()}</text>
      {months.filter((_, k) => k % step === 0).map(gi => (
        <g key={gi}><line x1={X(gi)} y1={H - pad} x2={X(gi)} y2={H - pad + 4} stroke="#ccc" />
          <text x={X(gi)} y={H - 8} fontSize={10} fill="#999" textAnchor="middle">{series[gi][0].slice(0, 7)}</text></g>
      ))}
      <path d={d} fill="none" stroke={up ? '#c0392b' : '#27ae60'} strokeWidth={1.8} />
    </svg>
  )
}

const pct = (v, sign) => v == null ? '—' : <span style={{ color: v >= 0 ? '#c0392b' : '#27ae60' }}>{(v >= 0 && sign ? '+' : '') + v + '%'}</span>

// 统计卡:大数字 + 标签 + 计算方式小字(还原 py 版的 .calc)
const Stat = ({ v, label, calc }) => (
  <Card size="small" style={{ minWidth: 150, maxWidth: 230 }}>
    <div style={{ fontSize: 20, fontWeight: 700 }}>{v}</div>
    <div style={{ fontSize: 13, color: '#444' }}>{label}</div>
    <div style={{ fontSize: 11, color: '#999', marginTop: 2, lineHeight: 1.3 }}>{calc}</div>
  </Card>
)

export default function App() {
  const [params, setParams] = useState({ mode: 'quick', tier: 5, start: '20250101', train: false })
  const [payload, setPayload] = useState(null)
  const [loading, setLoading] = useState(false)
  const [showKc, setKc] = useState(true), [showCy, setCy] = useState(true), [only50, set50] = useState(false)
  const [ana, setAna] = useState(null)   // {open, loading, code, date, data}

  const train = async (extra = {}) => {
    setLoading(true); setPayload(null)
    try {
      const r = await fetch('/api/train', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...params, ...extra }) })
      const j = await r.json()
      if (!j.ok) { message.error('训练失败: ' + (j.error || '')); return }
      j.signals.forEach(s => { s.__latest = j.latest })
      setPayload(j); message.success(`${j.cached ? '已加载缓存' : '完成'},共 ${j.signals.length} 条信号`)
    } catch (e) { message.error('请求失败,后端起了吗? ' + e) } finally { setLoading(false) }
  }

  const analyze = async (code, date, force = false) => {
    setAna({ open: true, loading: true, code, date })
    try {
      const r = await fetch('/api/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code, date, force }) })
      const j = await r.json()
      setAna({ open: true, loading: false, code, date, data: j })
    } catch (e) { setAna({ open: true, loading: false, code, date, data: { error: String(e) } }) }
  }

  const rows = useMemo(() => !payload ? [] : payload.signals.filter(r =>
    (showKc || r.board !== '科创') && (showCy || r.board !== '创业') && (!only50 || r.price <= 50)), [payload, showKc, showCy, only50])

  const stats = useMemo(() => {
    if (!rows.length) return null
    const avg = a => a.length ? (a.reduce((x, y) => x + y, 0) / a.length).toFixed(1) + '%' : '—'
    const winr = a => a.length ? (a.filter(v => v > 0).length / a.length * 100).toFixed(0) + '%' : '—'
    const don = rows.filter(r => r.donret != null).map(r => r.donret)
    const sw = rows.filter(r => r.swret != null).map(r => r.swret)
    const cz = rows.filter(r => r.czret != null).map(r => r.czret)
    const done = rows.filter(r => r.status !== '进行中'), hit = done.filter(r => r.status === '已走出主升浪')
    const fwds = rows.filter(r => r.maxfwd != null).map(r => r.maxfwd)
    const days = a => a.length ? (a.reduce((x, y) => x + y, 0) / a.length).toFixed(0) + '天' : '—'
    const strat = (name, arr, holdKey, openKey) => ({
      name, n: arr.length, win: winr(arr), avg: avg(arr), dd: avg(arr.filter(v => v < 0)),
      hold: days(rows.filter(r => r[holdKey] != null).map(r => r[holdKey])),
      on: rows.filter(r => r[openKey]).length,
    })
    return {
      n: rows.length, perday: (rows.length / payload.ntrade).toFixed(2),
      avgfwd: avg(fwds),
      succ: done.length ? (hit.length / done.length * 100).toFixed(0) + '%' : '—', doneN: done.length, hitN: hit.length,
      strats: [
        strat('唐奇安', don, 'hold', 'donopen'),
        strat('波段', sw, 'swhold', 'swopen'),
        strat('缠论', cz, 'czhold', 'czopen'),
      ],
    }
  }, [rows, payload])

  const today = useMemo(() => {
    if (!payload) return null
    const L = payload.latest
    const buys = rows.filter(r => r.date === L).sort((a, b) => b.score - a.score)
    const sells = rows.filter(r => r.donexit === L || r.swexit === L || r.swtp === L)
    const holds = Object.values(rows.filter(r => r.donopen || r.swopen || r.czopen).reduce((m, r) => {
      const e = m[r.ts] || { ...r, donopen: false, swopen: false, czopen: false, score: -Infinity }
      m[r.ts] = { ...e, donopen: e.donopen || !!r.donopen, swopen: e.swopen || !!r.swopen, czopen: e.czopen || !!r.czopen, score: Math.max(e.score, r.score) }
      return m
    }, {})).sort((a, b) => b.score - a.score)
    return { L, buys, sells, holds }
  }, [rows, payload])

  const cols = [
    { title: '突破日', dataIndex: 'date', sorter: (a, b) => a.date < b.date ? -1 : 1, defaultSortOrder: 'descend' },
    { title: '板块', dataIndex: 'board', filters: ['主板', '科创', '创业'].map(v => ({ text: v, value: v })), onFilter: (v, r) => r.board === v },
    { title: '板块大盘', dataIndex: 'mkt', render: v => <span style={{ color: v === '健康' ? '#c0392b' : '#27ae60' }}>{v}</span> },
    { title: '档位/ML分', dataIndex: 'score', defaultSortOrder: 'descend', sorter: (a, b) => a.score - b.score, render: (v, r) => <span><b>{r.tier}</b> {v}</span> },
    { title: '代码', dataIndex: 'ts' }, { title: '名称', dataIndex: 'name' },
    { title: '价格', dataIndex: 'price', sorter: (a, b) => a.price - b.price, render: v => v + '元' },
    { title: '形态', dataIndex: 'typ', filters: [{ text: 'N字型', value: 'N字型' }, { text: 'W型', value: 'W型' }], onFilter: (v, r) => r.typ === v },
    { title: '至今最大涨幅', dataIndex: 'maxfwd', sorter: (a, b) => (a.maxfwd || -999) - (b.maxfwd || -999), render: v => pct(v, true) },
    { title: '唐奇安盈亏', dataIndex: 'donret', sorter: (a, b) => (a.donret ?? -999) - (b.donret ?? -999), render: (v, r) => <span>{pct(v, true)}{r.donopen ? '(持仓)' : ''}</span> },
    { title: '唐奇安离场', dataIndex: 'donexit', render: (v, r) => (v || '持仓中') + '(' + r.hold + '天)' },
    { title: '波段盈亏', dataIndex: 'swret', sorter: (a, b) => (a.swret ?? -999) - (b.swret ?? -999), render: (v, r) => <span>{pct(v, true)}{r.swopen ? '(持仓)' : ''}</span> },
    { title: '波段离场', dataIndex: 'swexit', render: (v, r) => (v || '持仓中') + '(' + r.swhold + '天)' },
    { title: '缠论盈亏', dataIndex: 'czret', sorter: (a, b) => (a.czret ?? -999) - (b.czret ?? -999), render: (v, r) => v == null ? '—' : <span>{pct(v, true)}{r.czopen ? '(持仓)' : ''}</span> },
    { title: '缠论离场', dataIndex: 'czexit', render: (v, r) => r.czret == null ? '—' : (v || '持仓中') + (r.czhold != null ? '(' + r.czhold + '天)' : '') },
    { title: 'LLM分析', fixed: 'right', render: (_, r) => <Button size="small" type="primary" ghost onClick={() => analyze(r.ts, r.date)}>分析</Button> },
  ]

  const banner = payload?.banner
  return (
    <div style={{ maxWidth: 1850, margin: '18px auto', padding: '0 16px' }}>
      <h2>ML 主升浪信号 + LLM 分析</h2>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
        <span>模式</span>
        <Select value={params.mode} style={{ width: 110 }} onChange={v => setParams({ ...params, mode: v })}
          options={[{ value: 'quick', label: 'quick(小赚)' }, { value: 'long', label: 'long(大赚)' }]} />
        <span>档位top</span><InputNumber min={1} max={100} value={params.tier} onChange={v => setParams({ ...params, tier: v })} />
        <span>起始</span><Input style={{ width: 110 }} value={params.start} onChange={e => setParams({ ...params, start: e.target.value })} />
        <Checkbox checked={params.train} onChange={e => setParams({ ...params, train: e.target.checked })}>重新训练模型</Checkbox>
        <Button type="primary" loading={loading} onClick={() => train()}>训练模型 / 出信号</Button>
        <Button loading={loading} onClick={() => train({ refresh: true, train: false })}>刷新数据(不重训)</Button>
      </div>

      {banner && <div style={{ background: '#f6f9ff', border: '1px solid #d0e0ff', borderRadius: 8, padding: '8px 12px', marginBottom: 6 }}>
        {Object.entries(banner.indices).map(([nm, st]) => <span key={nm} style={{ marginRight: 16 }}><b>{nm}</b> <span style={{ color: st === '健康' ? '#c0392b' : '#27ae60' }}>{st}</span></span>)}
        <span>抱团度 <b>{banner.crowd.value ?? '—'}</b>(分位{banner.crowd.pct != null ? (banner.crowd.pct * 100).toFixed(0) + '%' : '—'},{banner.crowd.label})</span>
      </div>}
      {payload && <p style={{ fontSize: 12, color: '#999', margin: '0 0 4px' }}>
        健康=指数收盘&gt;MA60 且 MA60上行(走坏时突破成功率显著下降)。抱团度=残差互信息系统性风险因子,越高=资金越抱团/系统性风险越大。
      </p>}
      {payload && <p style={{ fontSize: 12, color: '#999', margin: '0 0 4px' }}>
        <b>出场口径</b>(均从突破日入场、扣双边费):<b>唐奇安</b>=持有至跌破唐奇安20日下轨 或 所属板块大盘走坏即离场,否则一直持有(让利润奔跑);
        <b>波段止盈止损</b>=四开关任一触发:①硬止损 跌破 入场×0.9 与 入场−1×ATR 取更低;②涨到 入场×1.1 与 入场+2×ATR 取更低 时平50%(部分止盈);③涨过+3%激活、从最高点回落5%的跟踪止损;④持满20日超时平仓。
      </p>}
      {payload && stats && <p style={{ fontSize: 13, margin: '4px 0' }}>
        模型用 {payload.start} 之前数据训练({payload.pivot}枢轴/{payload.mode}),打分该区间信号 |
        列出 top{payload.tier}% = {payload.signals.length} 条 | 已满60日的 {stats.doneN} 条中走出主升浪 {stats.succ} | 档位:top1/3/5/10/20/30
      </p>}

      {today && <div style={{ background: '#eef6ff', border: '1px solid #b6d4fe', borderRadius: 8, padding: '8px 12px', margin: '8px 0' }}>
        <div style={{ fontWeight: 700, marginBottom: 4 }}>最新交易日({today.L}) · 买入 {today.buys.length} 条 / 需卖出 {today.sells.length} 条</div>
        <div><b>买入:</b> {today.buys.length ? today.buys.map(r => <span key={'b' + r.ts} style={{ marginRight: 10, fontSize: 13 }}><b>{r.name}({r.ts})</b> {r.tier} ML{r.score} <span style={{ color: r.mkt === '健康' ? '#c0392b' : '#27ae60' }}>[{r.board}{r.mkt}]</span></span>) : <span style={{ color: '#999' }}>无</span>}</div>
        <div style={{ marginTop: 4 }}><b>需卖出:</b> {today.sells.length ? today.sells.map(r => { const t = []; if (r.donexit === today.L) t.push('唐奇安清仓'); if (r.swexit === today.L) t.push('波段清仓'); if (r.swtp === today.L) t.push('波段部分止盈(卖50%)'); return <span key={'s' + r.ts} style={{ marginRight: 10, fontSize: 13 }}><b>{r.name}({r.ts})</b> <span style={{ color: '#27ae60' }}>{t.join('/')}</span></span> }) : <span style={{ color: '#999' }}>无</span>}</div>
        <div style={{ marginTop: 4 }}><b>当前持仓:</b> <span style={{ fontSize: 12, color: '#999' }}>(模型/策略仍持仓,移到名字上点分析→按最新交易日 {today.L} 判断该不该卖)</span><br />
          {today.holds.length ? today.holds.map(r => {
            const who = [r.donopen && '唐', r.swopen && '波', r.czopen && '缠'].filter(Boolean).join('/')
            return <Popover key={'h' + r.ts} trigger="hover" content={<Button size="small" type="primary" ghost onClick={() => analyze(r.ts, today.L)}>分析(@{today.L})</Button>}>
              <span style={{ marginRight: 12, fontSize: 13, cursor: 'pointer', borderBottom: '1px dashed #888' }}>{r.name}({r.ts})<sub style={{ color: '#999' }}>{who}</sub></span>
            </Popover>
          }) : <span style={{ color: '#999' }}>无</span>}
        </div>
      </div>}

      {payload && <div style={{ margin: '8px 0' }}>
        <Checkbox checked={showKc} onChange={e => setKc(e.target.checked)}>显示科创</Checkbox>
        <Checkbox checked={showCy} onChange={e => setCy(e.target.checked)} style={{ marginLeft: 12 }}>显示创业</Checkbox>
        <Checkbox checked={only50} onChange={e => set50(e.target.checked)} style={{ marginLeft: 12 }}>只看≤50元</Checkbox>
      </div>}

      {stats && <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <Stat v={`${stats.n} / ${stats.perday}`} label="信号数 / 平均每日" calc={`当前筛选下的信号条数,及 ÷ 区间总交易日数(${stats.n}/${payload.ntrade})`} />
        <Stat v={stats.avgfwd} label="平均最大涨幅" calc="所有信号突破后至今(或满60日)最高浮盈的均值;非实际买卖收益" />
        <Card size="small" style={{ flex: '1 1 460px' }}>
          <div style={{ fontSize: 13, color: '#444', marginBottom: 4 }}>三种出场口径对比</div>
          <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
            <thead><tr style={{ color: '#888', textAlign: 'right' }}>
              <th style={{ textAlign: 'left' }}>出场</th><th>胜率</th><th>平均盈亏</th><th>平均回撤</th><th>平均持仓</th><th>进行中</th>
            </tr></thead>
            <tbody>{stats.strats.map(s => (
              <tr key={s.name} style={{ textAlign: 'right', borderTop: '1px solid #f0f0f0' }}>
                <td style={{ textAlign: 'left', fontWeight: 600 }}>{s.name}</td>
                <td>{s.win}</td>
                <td style={{ color: '#c0392b' }}>{s.avg}</td>
                <td style={{ color: '#27ae60' }}>{s.dd}</td>
                <td>{s.hold}</td>
                <td>{s.on}/{s.n}</td>
              </tr>
            ))}</tbody>
          </table>
          <div style={{ fontSize: 11, color: '#999', marginTop: 4, lineHeight: 1.3 }}>胜率=盈亏&gt;0占比;平均盈亏=全部信号均值;平均回撤=亏损信号平均亏幅;均扣双边费、持仓中按现价。进行中=未离场/总条数</div>
        </Card>
      </div>}

      {payload && <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 12, color: '#666', margin: '6px 0' }}>组合回测:15万本金4等份,出现信号占一份买入(同股不加仓,最多4只,满仓放弃),费率已计</div>
        <Chart title="唐奇安出场" series={portfolio(rows, 'donexit', 'donr', payload.cal)} />
        <div style={{ height: 8 }} />
        <Chart title="波段止盈止损出场" series={portfolio(rows, 'swexit', 'swr', payload.cal)} />
        <div style={{ height: 8 }} />
        <Chart title="缠论卖点出场" series={portfolio(rows, 'czexit', 'czr', payload.cal)} />
      </div>}

      {payload && <Tabs style={{ marginBottom: 12 }} items={[
        { key: 'don', label: '唐奇安 交易记录', d: ['donexit', 'donret', 'hold', 'donopen'] },
        { key: 'sw', label: '波段 交易记录', d: ['swexit', 'swret', 'swhold', 'swopen'] },
        { key: 'cz', label: '缠论 交易记录', d: ['czexit', 'czret', 'czhold', 'czopen'] },
      ].map(t => {
        const log = tradeLog(rows, ...t.d, payload.cal)
        const tcols = [
          { title: '股票', render: (_, r) => `${r.name}(${r.ts})` },
          { title: '突破日', dataIndex: 'date', sorter: (a, b) => a.date < b.date ? -1 : 1, defaultSortOrder: 'descend' },
          { title: '状态', dataIndex: 'status', filters: ['已交易', '满仓错过', '已持有'].map(v => ({ text: v, value: v })), onFilter: (v, r) => r.status === v,
            render: v => v === '已交易' ? <Tag color="blue">已交易</Tag> : <Tag color="orange">错过·{v}</Tag> },
          { title: '离场日', dataIndex: 'exit', render: (v, r) => (v || '持仓中') + (r.hold != null ? `(${r.hold}天)` : '') },
          { title: '盈亏', dataIndex: 'ret', sorter: (a, b) => (a.ret ?? -999) - (b.ret ?? -999), render: (v, r) => <span>{pct(v, true)}{r.open ? '(持仓)' : ''}{r.status !== '已交易' ? ' (未参与)' : ''}</span> },
        ]
        const done = log.filter(r => r.status === '已交易'), missed = log.filter(r => r.status !== '已交易')
        const cl = done.filter(r => !r.open).map(r => r.ret)
        const avg = a => a.length ? (a.reduce((x, y) => x + y, 0) / a.length).toFixed(1) + '%' : '—'
        const summary = (
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 13, marginBottom: 8 }}>
            <span>已交易 <b>{done.length}</b> 笔(持仓中 {done.filter(r => r.open).length})</span>
            <span>胜率 <b>{cl.length ? (cl.filter(v => v > 0).length / cl.length * 100).toFixed(0) + '%' : '—'}</b></span>
            <span>平均盈亏 <b style={{ color: '#c0392b' }}>{avg(cl)}</b></span>
            <span>平均回撤 <b style={{ color: '#27ae60' }}>{avg(cl.filter(v => v < 0))}</b></span>
            <span>平均持仓 <b>{done.length ? (done.reduce((s, r) => s + (r.hold || 0), 0) / done.length).toFixed(0) + '天' : '—'}</b></span>
            <span style={{ color: '#999' }}>错过 <b>{missed.length}</b> 笔(其中本可盈利 {missed.filter(r => r.ret > 0).length} 笔,均值 {avg(missed.filter(r => r.ret > 0).map(r => r.ret))})</span>
          </div>
        )
        return {
          key: t.key, label: t.label,
          children: <div>{summary}<Table rowKey="key" columns={tcols} dataSource={log} size="small" pagination={{ pageSize: 30 }}
            onRow={r => ({ style: r.status !== '已交易' ? { background: '#fafafa', color: '#999' } : undefined })} /></div>,
        }
      })} />}

      {payload && <Table rowKey={r => r.ts + r.date} columns={cols} dataSource={rows} size="small" scroll={{ x: 1500 }} pagination={{ pageSize: 30 }} />}

      {payload && <div style={{ background: '#fff8e1', border: '1px solid #ffe082', borderRadius: 6, padding: 10, fontSize: 12, color: '#6d4c41', marginTop: 10 }}>
        <b>⚠️</b> "至今最大涨幅"=突破日到现在(或满60日)的最高浮盈,非实际买卖收益;"唐奇安/波段离场"括号内为持仓交易日数(仍持仓算到最新交易日);
        "进行中"=该出场口径下尚未离场。点表头可排序/筛选。模型严格用区间起始日之前的数据训练,无未来函数。
        「LLM分析」对该股在突破日跑 技术+消息面 多agent 分析(约1-3分钟),给分析师层(趋势感知)买/卖/持。
      </div>}

      <Modal open={!!ana?.open} width={900} footer={null} onCancel={() => setAna(null)}
        title={<span>LLM 分析 {ana?.code} @ {ana?.date}
          {ana?.data?.cached && <Tag color="default" style={{ marginLeft: 8 }}>已缓存</Tag>}
          {!ana?.loading && ana?.data && <Button size="small" style={{ marginLeft: 8 }} onClick={() => analyze(ana.code, ana.date, true)}>重新分析</Button>}
        </span>}>
        {ana?.loading ? <div style={{ textAlign: 'center', padding: 40 }}><Spin tip="多 agent 分析中(约 1-3 分钟)..." /><div style={{ height: 30 }} /></div> :
          ana?.data?.error ? <pre style={{ color: 'red', whiteSpace: 'pre-wrap' }}>{ana.data.error}</pre> :
            ana?.data && <div>
              {ana.data.verdict && <div style={{ background: '#fffbe6', border: '1px solid #ffe58f', borderRadius: 6, padding: 10, marginBottom: 12 }}>
                <b>分析师层判断(趋势感知):</b> <Tag color={ana.data.verdict.action === '卖出' ? 'red' : ana.data.verdict.action === '买入' ? 'green' : 'blue'}>{ana.data.verdict.action}</Tag>
                置信 {ana.data.verdict.confidence} — {ana.data.verdict.reasoning}
              </div>}
              {ana.data.business && <div style={{ background: '#f0f7ff', border: '1px solid #cfe2ff', borderRadius: 6, padding: 10, marginBottom: 12, fontSize: 13, lineHeight: 1.7 }}>
                {(() => { const b = ana.data.business; if (b.raw) return <span>{b.raw}</span>; return <>
                  <div><b>主营:</b> {b.products} {b.chain && <Tag style={{ marginLeft: 4 }}>{b.chain}</Tag>}<span style={{ color: '#666' }}>{b.chain_desc}</span></div>
                  {b.market_pos && <div><b>市场地位:</b> {b.market_pos}</div>}
                  {b.pricing && <div><b>议价能力:</b> {b.pricing}</div>}
                  {b.bottleneck && <div><b>卡脖子:</b> <Tag color={b.bottleneck === '被卡' ? 'red' : b.bottleneck === '卡别人' ? 'green' : b.bottleneck === '部分' ? 'orange' : 'default'}>{b.bottleneck}</Tag>{b.reason}</div>}
                  {b.summary && <div style={{ marginTop: 2 }}><b>小结:</b> {b.summary}</div>}
                  {b.fin && <div style={{ color: '#999', fontSize: 12 }}>财务: {b.fin}</div>}
                </> })()}
              </div>}
              <h4>技术面</h4><MD>{ana.data.market_report}</MD>
              <h4 style={{ marginTop: 12 }}>消息面(公告/新闻/研报)</h4><MD>{ana.data.news_report}</MD>
            </div>}
      </Modal>
    </div>
  )
}

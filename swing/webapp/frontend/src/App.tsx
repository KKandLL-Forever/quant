import React, { useState, useMemo, useEffect } from 'react'
import { Table, Button, Modal, Select, InputNumber, Checkbox, Card, Spin, Tag, message, Input, Tabs, DatePicker, ConfigProvider } from 'antd'
import type { TableColumnsType } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
dayjs.locale('zh-cn')
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const MD = ({ children }: { children?: string }) => (
  <div className="md" style={{ fontSize: 13, maxHeight: 320, overflow: 'auto', background: '#f4f0e7', padding: 10 }}>
    <ReactMarkdown remarkPlugins={[remarkGfm]}>{children || '(空)'}</ReactMarkdown>
  </div>
)

const AV: Record<string, string> = {
  market: encodeURI('/技术面分析师.png'), news: encodeURI('/消息面分析师.png'),
  biz: encodeURI('/基本面分析师.png'), decision: encodeURI('/综合决策.png'),
}
const ChatMsg = ({ av, bg, role, img, children }: { av?: string; bg: string; role: string; img?: string; children: React.ReactNode }) => (
  <div className="chat-msg">
    <div className="chat-av" style={{ background: img ? '#fff' : bg, overflow: 'hidden' }}>
      {img ? <img src={img} alt={role} style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : av}
    </div>
    <div className="chat-body"><div className="chat-role">{role}</div><div className="chat-bubble">{children}</div></div>
  </div>
)
const Typing = () => <span className="typing"><span /><span /><span /></span>

// 研究员内部讨论:可折叠、灰字小号、每人配色左边线,和正式报告区分(类"思考过程")
const DBG_COLOR: Record<string, string> = { 看涨研究员: '#c0392b', 看跌研究员: '#1f8e5a', 研究经理: '#6d5bd0', 交易员: '#c98a2b' }
function DebatePanel({ turns, live }: { turns: any[]; live: boolean }) {
  const [open, setOpen] = useState(true)
  const bodyRef = React.useRef<HTMLDivElement | null>(null)
  React.useEffect(() => { if (!live) setOpen(false) }, [live])   // 讨论进行中保持展开,四人说完(转下一阶段)自动收起
  React.useEffect(() => { if (live && bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight }, [turns.length, live])
  if (!turns?.length) return null
  return (
    <div style={{ margin: '0 0 14px 44px', borderLeft: '2px solid #e2ddd0', paddingLeft: 12 }}>
      <div onClick={() => setOpen(o => !o)} style={{ cursor: 'pointer', color: '#8a8378', fontSize: 12.5, fontWeight: 600, userSelect: 'none' }}>
        💭 研究员内部讨论 · {turns.length} 段发言 {live && <Typing />} <span style={{ fontSize: 11 }}>{open ? '▾ 收起' : '▸ 展开'}</span>
      </div>
      {open && <div ref={bodyRef} style={{ marginTop: 8, maxHeight: 260, overflowY: 'auto', paddingRight: 4 }}>
        {turns.map((d: any, i: number) => {
          const c = DBG_COLOR[d.role] || '#a49b8b'
          return (
            <div key={i} className="dbg-in" style={{ marginBottom: 10, borderLeft: `3px solid ${c}`, paddingLeft: 10 }}>
              <div style={{ fontSize: 11.5, color: c, fontWeight: 700, marginBottom: 2 }}>{d.av} {d.role}</div>
              <div className="md dbg-md" style={{ fontSize: 12.5, color: '#8f887b', lineHeight: 1.7, background: 'none', padding: 0 }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{d.text || ''}</ReactMarkdown>
              </div>
            </div>
          )
        })}
      </div>}
    </div>
  )
}

import { portfolio, tradeLog, czPortfolio, INIT } from './lib/portfolio'
import type { SignalRow } from './lib/portfolio'

type OHLC = [string, number, number, number, number]
type BiPt = [string, number]
interface ZsBox { sdt: string; edt: string; zg: number; zd: number }
interface KData { ohlc: OHLC[]; bis: BiPt[]; zs: ZsBox[]; bo: string }
interface Mark { date: string; kind: string; label: string }
type Series = [string, number][]
type Sig = SignalRow & {
  board?: string; tier?: number | string; price?: number | null; typ?: string; mkt?: string; status?: string
  maxfwd?: number | null
  donret?: number | null; donr?: number | null; donexit?: string | null; donopen?: boolean; hold?: number | null
  czret?: number | null; czr?: number | null; czexit?: string | null; czopen?: boolean; czhold?: number | null
  czstate?: string | null; czlegs?: [string, string][]; czposinfo?: [string | null, boolean] | null
  swret?: number | null; swr?: number | null; swexit?: string | null; swopen?: boolean; swhold?: number | null
}
import { useSignalStore } from './store/signalStore'
import { Header, PageTitle, QUANT_THEME } from './shell'
import HoldingsPage from './features/holdings/HoldingsPage'
import LimitUpPage from './features/limitup/LimitUpPage'
import EtfSharePage from './features/etf-share/EtfSharePage'
import BullTopPage from './features/bull-top/BullTopPage'
import XiaoxifuPage from './features/xiaoxifu/XiaoxifuPage'
import BollPage from './features/boll/BollPage'
import ConceptPage from './features/concept/ConceptPage'

// 简易蜡烛图 + 缠论 笔折线 + 中枢方框 + 突破日竖线 + 买卖标记
function KLineChart({ data, marks }: { data: KData; marks: Mark[] }) {
  const { ohlc, bis, zs, bo } = data
  if (!ohlc || ohlc.length < 2) return <div>无数据</div>
  const W = 1500, H = 580, padL = 52, padR = 14, padT = 14, padB = 26
  const n = ohlc.length
  const lo = Math.min(...ohlc.map(b => b[3])), hi = Math.max(...ohlc.map(b => b[2]))
  const X = (i: number) => padL + i * (W - padL - padR) / (n - 1)
  const Y = (v: number) => padT + (hi - v) * (H - padT - padB) / (hi - lo)
  const cw = Math.max(1.5, (W - padL - padR) / n * 0.6)
  const di: Record<string, number> = Object.fromEntries(ohlc.map((b, i) => [b[0], i]))
  const months: number[] = []
  for (let i = 0; i < n; i++) if (i === 0 || ohlc[i][0].slice(0, 7) !== ohlc[i - 1][0].slice(0, 7)) months.push(i)
  const biPts = (bis || []).filter(p => di[p[0]] != null).map(p => `${X(di[p[0]]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(' ')
  return (
    <svg width={W} height={H} style={{ maxWidth: '100%', background: '#fffdf8' }}>
      <text x={4} y={Y(hi) + 4} fontSize={11} fill="#999">{hi.toFixed(2)}</text>
      <text x={4} y={Y(lo) + 4} fontSize={11} fill="#999">{lo.toFixed(2)}</text>
      {(zs || []).map((z: ZsBox, k: number) => di[z.sdt] != null && di[z.edt] != null && (
        <rect key={k} x={X(di[z.sdt])} y={Y(z.zg)} width={X(di[z.edt]) - X(di[z.sdt])} height={Y(z.zd) - Y(z.zg)}
          fill="rgba(255,165,0,0.12)" stroke="rgba(230,126,34,0.6)" strokeWidth={1} />
      ))}
      {ohlc.map((b: OHLC, i: number) => {
        const up = b[4] >= b[1], col = up ? '#c0392b' : '#27ae60'
        return <g key={i}>
          <line x1={X(i)} y1={Y(b[2])} x2={X(i)} y2={Y(b[3])} stroke={col} strokeWidth={1} />
          <rect x={X(i) - cw / 2} y={Y(Math.max(b[1], b[4]))} width={cw} height={Math.max(1, Math.abs(Y(b[1]) - Y(b[4])))} fill={col} />
        </g>
      })}
      {biPts && <polyline points={biPts} fill="none" stroke="#1677ff" strokeWidth={1.6} />}
      {di[bo] != null && <line x1={X(di[bo])} y1={padT} x2={X(di[bo])} y2={H - padB} stroke="#999" strokeDasharray="3 3" />}
      {(() => { const seen: Record<string, number> = {}; return (marks || []).map((m: Mark, k: number) => {
        const i = di[m.date]; if (i == null) return null
        if (m.kind === 'buy') {
          const y = Y(ohlc[i][3]) + 10
          return <g key={k}><polygon points={`${X(i)},${y} ${X(i) - 9},${y + 16} ${X(i) + 9},${y + 16}`} fill="#c0392b" />
            <text x={X(i)} y={y + 32} fontSize={15} fontWeight={700} fill="#c0392b" textAnchor="middle">买</text></g>
        }
        const o = seen[m.date] || 0; seen[m.date] = o + 1; const y = Y(ohlc[i][2]) - 10 - o * 22
        return <g key={k}><polygon points={`${X(i)},${y} ${X(i) - 9},${y - 16} ${X(i) + 9},${y - 16}`} fill="#27ae60" />
          <text x={X(i)} y={y - 20} fontSize={15} fontWeight={700} fill="#27ae60" textAnchor="middle">{m.label}</text></g>
      }) })()}
      {months.map((gi: number) => <text key={gi} x={X(gi)} y={H - 6} fontSize={9} fill="#aaa" textAnchor="middle">{ohlc[gi][0].slice(2, 7)}</text>)}
    </svg>
  )
}

function Chart({ title, series }: { title: string; series: Series }) {
  if (!series || series.length < 2) return null
  const W = 1600, H = 240, pad = 50
  const vals = series.map(p => p[1])
  let mn = Math.min(...vals), mx = Math.max(...vals); if (mn === mx) { mn *= 0.99; mx *= 1.01 }
  const n = series.length
  const X = (i: number) => pad + i * (W - 2 * pad) / (n - 1)
  const Y = (v: number) => H - pad - (v - mn) * (H - 2 * pad) / (mx - mn)
  let d = ''; for (let i = 0; i < n; i++) d += (i ? 'L' : 'M') + X(i).toFixed(1) + ',' + Y(series[i][1]).toFixed(1)
  const last = series[n - 1][1], ret = ((last / INIT - 1) * 100).toFixed(1), up = last >= INIT
  const months: number[] = []
  for (let i = 0; i < n; i++) if (i === 0 || series[i][0].slice(0, 7) !== series[i - 1][0].slice(0, 7)) months.push(i)
  const step = Math.ceil(months.length / 12)
  return (
    <svg width={W} height={H} style={{ maxWidth: '100%', border: '1px solid #e6e0d3', borderRadius: 8, background: '#fffdf8' }}>
      <text x={pad} y={20} fontSize={14} fontWeight={700}>{title}  期末 {Math.round(last).toLocaleString()} ({up ? '+' : ''}{ret}%)</text>
      <line x1={pad} y1={Y(INIT)} x2={W - pad} y2={Y(INIT)} stroke="#bbb" strokeDasharray="4 4" />
      <text x={6} y={Y(mx) + 4} fontSize={11} fill="#999">{Math.round(mx).toLocaleString()}</text>
      <text x={6} y={Y(mn) + 4} fontSize={11} fill="#999">{Math.round(mn).toLocaleString()}</text>
      {months.filter((_: number, k: number) => k % step === 0).map((gi: number) => (
        <g key={gi}><line x1={X(gi)} y1={H - pad} x2={X(gi)} y2={H - pad + 4} stroke="#ccc" />
          <text x={X(gi)} y={H - 8} fontSize={10} fill="#999" textAnchor="middle">{series[gi][0].slice(0, 7)}</text></g>
      ))}
      <path d={d} fill="none" stroke={up ? '#c0392b' : '#27ae60'} strokeWidth={1.8} />
    </svg>
  )
}

const pct = (v: number | null | undefined, sign?: boolean) => v == null ? '—' : <span style={{ color: v >= 0 ? '#c0392b' : '#27ae60' }}>{(v >= 0 && sign ? '+' : '') + v + '%'}</span>
// 按板块给代码上色:科创=紫、创业=橙、主板=蓝
const boardColor = (c: string) => c.startsWith('688') || c.startsWith('689') ? '#7c3aed'
  : (c.slice(0, 3) === '300' || c.slice(0, 3) === '301') ? '#c2410c' : '#3b6ea5'

// 统计卡:大数字 + 标签 + 计算方式小字(还原 py 版的 .calc)
const Stat = ({ v, label, calc }: { v: React.ReactNode; label: string; calc: string }) => (
  <Card size="small" style={{ minWidth: 150, maxWidth: 230 }}>
    <div style={{ fontSize: 20, fontWeight: 700 }}>{v}</div>
    <div style={{ fontSize: 13, color: '#444' }}>{label}</div>
    <div style={{ fontSize: 11, color: '#999', marginTop: 2, lineHeight: 1.3 }}>{calc}</div>
  </Card>
)

function MainPage() {
  const { params, setParams, parts, setParts, payload, loading, train } = useSignalStore()
  const [showKc, setKc] = useState(true), [showCy, setCy] = useState(true), [only50, set50] = useState(false)
  const [ana, setAna] = useState<any>(null)   // {open, loading, code, date, data}
  const [kl, setKl] = useState<any>(null)     // K线弹窗 {open, loading, code, date, data}
  const [adv, setAdv] = useState<any>(null)   // 缠论卖点提示弹窗 {open, loading, code, date, data}

  const openAdvise = async (code: string, date: string) => {
    setAdv({ open: true, loading: true, code, date })
    try {
      const r = await fetch('/api/advise', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, buy_date: date }) })
      const j = await r.json()
      setAdv((a: any) => (a && a.code === code && a.date === date) ? { open: true, loading: false, code, date, data: j } : a)
    } catch (e) {
      setAdv((a: any) => (a && a.code === code) ? { open: true, loading: false, code, date, data: { ok: false, error: String(e) } } : a)
    }
  }

  const openKline = async (code: string, date: string, row?: Sig) => {
    const marks: Mark[] = []
    if (row?.czlegs?.length) {   // 缠论M3全历史腿事件:买/补=买点,缠/止=卖点(与表格同口径)
      for (const [d0, k] of row.czlegs) marks.push({ date: d0, kind: (k === '买' || k === '补') ? 'buy' : 'sell', label: k })
    } else {
      marks.push({ date, kind: 'buy', label: '买' })
    }
    if (row?.donexit) marks.push({ date: row.donexit, kind: 'sell', label: '唐' })
    setKl({ open: true, loading: true, code, date, marks })
    try {
      const r = await fetch('/api/kline', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code, date }) })
      setKl({ open: true, loading: false, code, date, marks, data: await r.json() })
    } catch (e) { setKl({ open: true, loading: false, code, date, marks, data: { error: String(e) } }) }
  }

  useEffect(() => { train() }, [])   // 进页自动按默认(long/20260101)加载,一般命中缓存秒显

  const esRef = React.useRef<EventSource | null>(null)
  const pollRef = React.useRef<any>(null)
  const curRid = React.useRef<string | null>(null)
  const timers = React.useRef<any[]>([])
  const scrollRef = React.useRef<HTMLDivElement | null>(null)
  const stickRef = React.useRef(true)
  React.useEffect(() => {   // 新内容到达时:若停在底部就跟随,若上滑了则不打扰
    const el = scrollRef.current
    if (el && stickRef.current) el.scrollTop = el.scrollHeight
  }, [ana?.market_report, ana?.news_report, (ana?.shownDlg || []).length, ana?.bizText, ana?.verText, ana?.phase, ana?.verdict, ana?.business])
  const up = (rid: string, patch: any) => setAna((a: any) => (a && a.rid === rid) ? { ...a, ...patch } : a)

  const _startStream = (rid: string, code: string, date: string) => {
    if (curRid.current !== rid) return
    const es = new EventSource(`/api/analyze/stream?rid=${rid}&code=${encodeURIComponent(code)}&date=${date}`)
    esRef.current = es
    up(rid, { phase: 'streaming' })
    es.onmessage = (e) => {
      const m = JSON.parse(e.data)
      if (m.t === 'biz') setAna((a: any) => a && a.rid === rid ? { ...a, bizText: (a.bizText || '') + m.d } : a)
      else if (m.t === 'biz_done') up(rid, { business: m.v })
      else if (m.t === 'ver') setAna((a: any) => a && a.rid === rid ? { ...a, verText: (a.verText || '') + m.d } : a)
      else if (m.t === 'done') { up(rid, { verdict: m.verdict, business: m.business, phase: 'done' }); es.close() }
      else if (m.t === 'error') { up(rid, { phase: 'error', stage: m.msg }); es.close() }
    }
    es.onerror = () => { es.close() }
  }

  const analyze = async (code: string, date: string, force = false, name = '') => {
    const rid = crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random())
    if (pollRef.current) clearInterval(pollRef.current)
    esRef.current?.close()
    timers.current.forEach(clearTimeout); timers.current = []
    curRid.current = rid
    setAna({ open: true, rid, code, date, name, phase: 'starting', stage: '启动分析…' })
    try {
      const r = await fetch('/api/analyze/start', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, date, force, rid }) })
      const j = await r.json()
      if (j.cached) { up(rid, { phase: 'done', cached: true, market_report: j.market_report, news_report: j.news_report, shownDlg: j.dialogue || [], business: j.business, verdict: j.verdict }); return }
      if (!j.ok) { up(rid, { phase: 'error', stage: j.error || '启动失败' }); return }
      up(rid, { phase: 'analyzing', stage: '多智能体分析中(约1-3分钟)…' })
      pollRef.current = setInterval(async () => {
        try {
          const pr = await (await fetch('/api/analyze/progress', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rid }) })).json()
          if (!pr.ok) { clearInterval(pollRef.current); up(rid, { phase: 'error', stage: pr.error }); return }
          up(rid, { stage: pr.stage || '分析中…', market_report: pr.market_report || '', news_report: pr.news_report || '', shownDlg: pr.dialogue || [] })   // 实时反映后端已产出的部分
          if (pr.done) { clearInterval(pollRef.current); _startStream(rid, code, date) }
        } catch { /* 网络抖动,下次轮询继续 */ }
      }, 1500)
    } catch (e) { up(rid, { phase: 'error', stage: String(e) }) }
  }

  const closeAna = () => {
    curRid.current = null
    if (pollRef.current) clearInterval(pollRef.current)
    timers.current.forEach(clearTimeout); timers.current = []
    esRef.current?.close()
    if (ana?.rid && ana?.phase !== 'done') {
      fetch('/api/analyze_cancel', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rid: ana.rid }) }).catch(() => {})
    }
    setAna(null)
  }

  const rows = useMemo<Sig[]>(() => !payload ? [] : (payload.signals as Sig[]).filter((r) =>
    (showKc || r.board !== '科创') && (showCy || r.board !== '创业') && (!only50 || (r.price ?? 0) <= 50)), [payload, showKc, showCy, only50])

  const stats = useMemo(() => {
    if (!rows.length) return null
    const avg = (a: number[]) => a.length ? (a.reduce((x, y) => x + y, 0) / a.length).toFixed(1) + '%' : '—'
    const winr = (a: number[]) => a.length ? (a.filter(v => v > 0).length / a.length * 100).toFixed(0) + '%' : '—'
    const don = rows.filter(r => r.donret != null).map(r => r.donret as number)
    // const sw = rows.filter(r => r.swret != null).map(r => r.swret)   // 波段先隐藏
    const cz = rows.filter(r => r.czret != null).map(r => r.czret as number)
    const done = rows.filter(r => r.status !== '进行中'), hit = done.filter(r => r.status === '已走出主升浪')
    const fwds = rows.filter(r => r.maxfwd != null).map(r => r.maxfwd as number)
    const days = (a: number[]) => a.length ? (a.reduce((x, y) => x + y, 0) / a.length).toFixed(0) + '天' : '—'
    const strat = (name: string, arr: number[], holdKey: string, openKey: string) => ({
      name, n: arr.length, win: winr(arr), avg: avg(arr), dd: avg(arr.filter(v => v < 0)),
      hold: days(rows.filter(r => r[holdKey] != null).map(r => r[holdKey] as number)),
      on: rows.filter(r => r[openKey]).length,
    })
    return {
      n: rows.length, perday: (rows.length / (payload?.ntrade || 1)).toFixed(2),
      avgfwd: avg(fwds),
      succ: done.length ? (hit.length / done.length * 100).toFixed(0) + '%' : '—', doneN: done.length, hitN: hit.length,
      strats: [
        strat('唐奇安', don, 'hold', 'donopen'),
        // strat('波段', sw, 'swhold', 'swopen'),   // 波段先隐藏
        strat('缠论M3', cz, 'czhold', 'czopen'),
      ],
    }
  }, [rows, payload])

  const today = useMemo(() => {
    if (!payload) return null
    const L = payload.latest ?? ''
    const buys = rows.filter(r => r.date === L).sort((a, b) => b.score - a.score)
    const sells = rows.filter(r => r.donexit === L || r.czexit === L)
    const holds = (Object.values(rows.filter(r => r.donopen || r.swopen || r.czopen).reduce((m: any, r: any) => {
      const e = m[r.ts] || { ...r, donopen: false, swopen: false, czopen: false, score: -Infinity }
      m[r.ts] = { ...e, donopen: e.donopen || !!r.donopen, swopen: e.swopen || !!r.swopen, czopen: e.czopen || !!r.czopen, score: Math.max(e.score, r.score) }
      return m
    }, {})) as any[]).sort((a, b) => b.score - a.score)
    return { L, buys, sells, holds }
  }, [rows, payload])

  const cols: TableColumnsType<Sig> = [
    { title: '突破日', dataIndex: 'date', sorter: (a, b) => a.date < b.date ? -1 : 1, defaultSortOrder: 'descend' },
    { title: '板块', dataIndex: 'board', filters: ['主板', '科创', '创业'].map(v => ({ text: v, value: v })), onFilter: (v, r) => r.board === v },
    { title: '档位/ML分', dataIndex: 'score', defaultSortOrder: 'descend', sorter: (a, b) => a.score - b.score, render: (v, r) => <span><b>{r.tier}</b> {v}</span> },
    { title: '代码', dataIndex: 'ts', render: (v: string) => <span style={{ color: boardColor(v), fontFamily: 'var(--font-mono)' }}>{v}</span> },
    { title: '名称', dataIndex: 'name', render: (v, r) => <a onClick={() => openKline(r.ts, r.date, r)}>{v}</a> },
    { title: '价格', dataIndex: 'price', sorter: (a, b) => (a.price ?? 0) - (b.price ?? 0), render: v => v + '元' },
    { title: '形态', dataIndex: 'typ', filters: [{ text: 'N字型', value: 'N字型' }, { text: 'W型', value: 'W型' }], onFilter: (v, r) => r.typ === v },
    { title: '至今最大涨幅', dataIndex: 'maxfwd', sorter: (a, b) => (a.maxfwd || -999) - (b.maxfwd || -999), render: v => pct(v, true) },
    { title: '唐奇安盈亏', dataIndex: 'donret', sorter: (a, b) => (a.donret ?? -999) - (b.donret ?? -999), render: (v, r) => <span>{pct(v, true)}{r.donopen ? '(持仓)' : ''}</span> },
    { title: '唐奇安离场', dataIndex: 'donexit', render: (v, r) => (v || '持仓中') + '(' + r.hold + '天)' },
    // 波段先隐藏
    // { title: '波段盈亏', dataIndex: 'swret', sorter: (a, b) => (a.swret ?? -999) - (b.swret ?? -999), render: (v, r) => <span>{pct(v, true)}{r.swopen ? '(持仓)' : ''}</span> },
    // { title: '波段离场', dataIndex: 'swexit', render: (v, r) => (v || '持仓中') + '(' + r.swhold + '天)' },
    { title: '缠论M3盈亏', dataIndex: 'czret', sorter: (a, b) => (a.czret ?? -999) - (b.czret ?? -999), render: (v, r) => v == null ? '—' : <span>{pct(v, true)}{r.czopen ? '(持仓)' : ''}</span> },
    { title: '缠论M3终止', dataIndex: 'czexit', render: (v, r) => {
      if (r.czret == null) return '—'
      if (r.czstate === '加仓') { const p = r.czposinfo; return <span><Tag color="gold">加仓</Tag>{p ? (p[1] ? <span style={{ color: '#888' }}>持仓中</span> : <span style={{ color: '#888' }}>离场 {p[0]}</span>) : null}</span> }
      if (r.czstate === '持仓中(回补)') return <Tag color="green">持仓中(回补)</Tag>
      if (r.czstate === '持仓中') return <Tag color="blue">持仓中</Tag>
      return (v || '持仓中') + (r.czhold != null ? '(' + r.czhold + '天)' : '')   // 已离场
    } },
    { title: 'LLM分析', fixed: 'right', render: (_, r) => <Button size="small" type="primary" ghost onClick={() => analyze(r.ts, r.date, false, r.name)}>分析</Button> },
    { title: '缠论提示', fixed: 'right', render: (_, r) => <Button size="small" ghost style={{ color: '#0b6e4f', borderColor: '#0b6e4f' }} onClick={() => openAdvise(r.ts, r.date)}>卖点</Button> },
  ]

  const banner: any = payload?.banner
  return (
    <div style={{ maxWidth: 1850, margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="Main-wave Signals · LightGBM + 缠论 + LLM">ML 主升浪信号</PageTitle>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
        <span>模式</span>
        <Select value={params.mode} style={{ width: 110 }} onChange={v => setParams({ ...params, mode: v })}
          options={[{ value: 'quick', label: 'quick(小赚)' }, { value: 'long', label: 'long(大赚)' }]} />
        <span>档位top</span><InputNumber min={1} max={100} value={params.tier} onChange={v => setParams({ ...params, tier: v ?? 5 })} />
        <span>起始</span><Input style={{ width: 110 }} value={params.start} onChange={e => setParams({ ...params, start: e.target.value })} />
        <Checkbox checked={params.train} onChange={e => setParams({ ...params, train: e.target.checked })}>重新训练模型</Checkbox>
        <Button type="primary" loading={loading} onClick={() => train()}>训练模型 / 出信号</Button>
        <Button loading={loading} onClick={() => train({ refresh: true, train: false })}>刷新数据(不重训)</Button>
      </div>

      {banner && <div style={{ background: '#fffdf8', border: '1px solid #e6e0d3', borderRadius: 8, padding: '8px 12px', marginBottom: 6 }}>
        {Object.entries(banner.indices).map(([nm, st]: [string, any]) => <span key={nm} style={{ marginRight: 16 }}><b>{nm}</b> <span style={{ color: st === '健康' ? '#c0392b' : '#27ae60' }}>{st}</span></span>)}
        <span>抱团度 <b>{banner.crowd.value ?? '—'}</b>(分位{banner.crowd.pct != null ? (banner.crowd.pct * 100).toFixed(0) + '%' : '—'},{banner.crowd.label})</span>
      </div>}
      {payload && <p style={{ fontSize: 12, color: '#999', margin: '0 0 4px' }}>
        健康/走坏=同小西西弗牛熊开关:走坏=MA30与MA60同时走坏(收盘&lt;均线且均线下行),至少一条多头即健康(走坏时突破成功率显著下降)。抱团度=残差互信息系统性风险因子,越高=资金越抱团/系统性风险越大。
      </p>}
      {payload && <p style={{ fontSize: 12, color: '#999', margin: '0 0 4px' }}>
        <b>出场口径</b>(均从突破日入场、扣双边费):<b>唐奇安</b>=持有至跌破唐奇安20日下轨即离场,否则一直持有(让利润奔跑);<b>缠论M3</b>=缠论卖点止盈+回调缠论买点回补,跌破60日线/入场价85%终止。
      </p>}
      {payload && stats && <p style={{ fontSize: 13, margin: '4px 0' }}>
        模型用 {payload.start} 之前数据训练({payload.pivot}枢轴/{payload.mode}),打分该区间信号 |
        列出 top{payload.tier}% = {payload.signals.length} 条 | 已满60日的 {stats.doneN} 条中走出主升浪 {stats.succ} | 档位:top1/3/5/10/20/30
      </p>}

      {today && <div className="tday">
        <div className="tday-head">
          <span className="tday-title">最新交易日</span>
          <span className="tday-date">{today.L}</span>
          <span style={{ flex: 1 }} />
          <span className="tday-pill" style={{ color: '#c0392b' }}>买入 {today.buys.length}</span>
          <span className="tday-pill" style={{ color: '#1f8e5a' }}>卖出 {today.sells.length}</span>
          <span className="tday-pill">持仓 {today.holds.length}</span>
        </div>
        <div className="tday-grid">
          <div>
            <div className="tday-colh" style={{ color: '#c0392b' }}>今日买入</div>
            {today.buys.length ? today.buys.map(r => (
              <span key={'b' + r.ts} className="chip chip-buy">
                <b>{r.name}</b><span className="chip-code" style={{ color: boardColor(r.ts) }}>{r.ts.slice(0, 6)}</span>
                <span className="chip-meta">{r.tier}·ML{r.score}</span>
                <span style={{ fontSize: 11, color: r.mkt === '健康' ? '#c0392b' : '#1f8e5a' }}>{r.board}{r.mkt}</span>
              </span>
            )) : <span className="tday-empty">无</span>}
          </div>
          <div>
            <div className="tday-colh" style={{ color: '#1f8e5a' }}>今日需卖出</div>
            {today.sells.length ? today.sells.map(r => {
              const t: string[] = []; if (r.donexit === today.L) t.push('唐奇安清仓'); if (r.czexit === today.L) t.push('缠论M3离场')
              return (
                <span key={'s' + r.ts} className="chip chip-sell">
                  <b>{r.name}</b><span className="chip-code" style={{ color: boardColor(r.ts) }}>{r.ts.slice(0, 6)}</span>
                  {t.map(x => <span key={x} className="chip-rsn">{x}</span>)}
                </span>
              )
            }) : <span className="tday-empty">无</span>}
          </div>
        </div>
        <div className="tday-holds">
          <div className="tday-colh" style={{ color: '#0b6e4f' }}>当前持仓 {today.holds.length}<span style={{ fontWeight: 400, textTransform: 'none', letterSpacing: 0, fontSize: 11, color: '#9b958a', marginLeft: 4 }}>点击分析该不该卖</span></div>
          <div style={{ display: 'flex', flexWrap: 'wrap' }}>
            {today.holds.length ? today.holds.map(r => {
              const who = [r.donopen && '唐', r.czopen && '缠'].filter(Boolean).join('/')
              return (
                <span key={'h' + r.ts} className="hold-chip" onClick={() => analyze(r.ts, today.L, false, r.name)}>
                  <span style={{ fontWeight: 600 }}>{r.name}</span>
                  <span className="hold-code" style={{ color: boardColor(r.ts) }}>{r.ts.slice(0, 6)}</span>
                  {who && <span className="hold-tag">{who}</span>}
                </span>
              )
            }) : <span className="tday-empty">无</span>}
          </div>
        </div>
      </div>}

      {payload && <div style={{ margin: '8px 0' }}>
        <Checkbox checked={showKc} onChange={e => setKc(e.target.checked)}>显示<span style={{ color: '#7c3aed', fontWeight: 600 }}>科创</span></Checkbox>
        <Checkbox checked={showCy} onChange={e => setCy(e.target.checked)} style={{ marginLeft: 12 }}>显示<span style={{ color: '#c2410c', fontWeight: 600 }}>创业</span></Checkbox>
        <Checkbox checked={only50} onChange={e => set50(e.target.checked)} style={{ marginLeft: 12 }}>只看≤50元</Checkbox>
        <span style={{ marginLeft: 12, fontSize: 12, color: '#9b958a' }}>代码色:<span style={{ color: '#3b6ea5' }}>主板</span>/<span style={{ color: '#c2410c' }}>创业</span>/<span style={{ color: '#7c3aed' }}>科创</span></span>
      </div>}

      {stats && <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <Stat v={`${stats.n} / ${stats.perday}`} label="信号数 / 平均每日" calc={`当前筛选下的信号条数,及 ÷ 区间总交易日数(${stats.n}/${payload?.ntrade ?? '—'})`} />
        <Stat v={stats.avgfwd} label="平均最大涨幅" calc="所有信号突破后至今(或满60日)最高浮盈的均值;非实际买卖收益" />
        <Card size="small" style={{ flex: '1 1 460px' }}>
          <div style={{ fontSize: 13, color: '#444', marginBottom: 4 }}>三种出场口径对比</div>
          <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
            <thead><tr style={{ color: '#888', textAlign: 'right' }}>
              <th style={{ textAlign: 'left' }}>出场</th><th>胜率</th><th>平均盈亏</th><th>平均回撤</th><th>平均持仓</th><th>进行中</th>
            </tr></thead>
            <tbody>{stats.strats.map(s => (
              <tr key={s.name} style={{ textAlign: 'right', borderTop: '1px solid #ece7db' }}>
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
        <div style={{ fontSize: 12, color: '#666', margin: '6px 0', display: 'flex', alignItems: 'center', gap: 8 }}>
          组合回测:15万本金分
          <Select size="small" style={{ width: 70 }} value={parts} onChange={setParts}
            options={[2, 3, 4, 5, 6, 8, 10].map(v => ({ value: v, label: v + '份' }))} />
          等份,每买入占一份、满仓放弃,费率已计。唐奇安:同股不加仓。缠论M3:同股可加仓、缠卖回补各占1份(B并仓)。跟随上方筛选。
        </div>
        <Chart title="唐奇安出场(同股不加仓)" series={portfolio(rows, 'donexit', 'donr', payload.cal ?? [], parts)} />
        <div style={{ height: 8 }} />
        {/* 波段先隐藏 <Chart title="波段止盈止损出场" series={portfolio(rows, 'swexit', 'swr', payload.cal, parts)} /> */}
        <div style={{ height: 8 }} />
        <Chart title="缠论M3(加仓并仓·卖点止盈+回调回补各占1份)" series={czPortfolio(rows, payload.cal ?? [], parts).curve} />
      </div>}

      {payload && (() => {
        const donLog = tradeLog(rows, 'donexit', 'donret', 'hold', 'donopen', payload.cal ?? [], parts)
          .map(r => ({ ...r, done: r.status === '已交易' }))
        const czLog = czPortfolio(rows, payload.cal ?? [], parts).log.map((r: any, i: number) =>
          ({ ...r, key: r.ts + r.date + i, done: r.status === '已买入' }))
        const renderTab = (log: any[], isCz: boolean) => {
          const done = log.filter(r => r.done), missed = log.filter(r => !r.done)
          const cl = done.map(r => r.ret).filter((v: any): v is number => v != null)
          const avg = (a: number[]) => a.length ? (a.reduce((x, y) => x + y, 0) / a.length).toFixed(1) + '%' : '—'
          const tcols: any[] = [
            { title: '股票', render: (_: any, r: any) => `${r.name}(${r.ts})` },
            ...(isCz ? [{ title: '类型', dataIndex: 'type', width: 70, filters: ['建仓', '加仓'].map(v => ({ text: v, value: v })), onFilter: (v: any, r: any) => r.type === v, render: (v: string) => <Tag color={v === '加仓' ? 'gold' : 'blue'}>{v}</Tag> }] : []),
            { title: '日期', dataIndex: 'date', sorter: (a: any, b: any) => a.date < b.date ? -1 : 1, defaultSortOrder: 'descend' as const },
            { title: '状态', dataIndex: 'status', render: (v: string, r: any) => r.done ? <Tag color="blue">{v}</Tag> : <Tag color="orange">{v}</Tag> },
            { title: '离场日', dataIndex: 'exit', render: (v: any, r: any) => r.done ? (v || '持仓中') + (r.hold != null ? `(${r.hold}天)` : '') : '—' },
            { title: '盈亏', dataIndex: 'ret', sorter: (a: any, b: any) => (a.ret ?? -999) - (b.ret ?? -999), render: (v: any, r: any) => r.done ? <span>{pct(v, true)}{r.open ? '(持仓)' : ''}</span> : <span style={{ color: '#bbb' }}>未参与</span> },
          ]
          const summary = (
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 13, marginBottom: 8 }}>
              <span>已{isCz ? '买入' : '交易'} <b>{done.length}</b> 笔{isCz && <span style={{ color: '#999' }}>(加仓 {done.filter(r => r.type === '加仓').length})</span>}(持仓中 {done.filter(r => r.open).length})</span>
              <span>胜率 <b>{cl.length ? (cl.filter((v: number) => v > 0).length / cl.length * 100).toFixed(0) + '%' : '—'}</b></span>
              <span>平均盈亏 <b style={{ color: '#c0392b' }}>{avg(cl)}</b></span>
              <span>平均回撤 <b style={{ color: '#27ae60' }}>{avg(cl.filter((v: number) => v < 0))}</b></span>
              <span>平均持仓 <b>{done.length ? (done.reduce((s: number, r: any) => s + (Number(r.hold) || 0), 0) / done.length).toFixed(0) + '天' : '—'}</b></span>
              <span style={{ color: '#999' }}>{isCz ? '满仓取消' : '错过'} <b>{missed.length}</b> 笔</span>
              <span style={{ color: '#bbb', fontSize: 12 }}>胜率/盈亏含持仓中按现价</span>
            </div>
          )
          return <div>{summary}<Table rowKey="key" columns={tcols} dataSource={log} size="small" pagination={{ pageSize: 30 }}
            onRow={(r: any) => ({ style: !r.done ? { background: '#f3efe5', color: '#9b958a' } : undefined })} /></div>
        }
        return <Tabs style={{ marginBottom: 12 }} items={[
          { key: 'don', label: '唐奇安 交易记录', children: renderTab(donLog, false) },
          { key: 'cz', label: '缠论M3 交易记录(并仓)', children: renderTab(czLog, true) },
        ]} />
      })()}

      {payload && <Table rowKey={r => r.ts + r.date} columns={cols} dataSource={rows} size="small" scroll={{ x: 1500 }} pagination={{ pageSize: 30 }} />}

      {payload && (() => {
        const bd = (c: string) => c.startsWith('688') || c.startsWith('689') ? '科创' : (c.slice(0, 3) === '300' || c.slice(0, 3) === '301') ? '创业' : '主板'
        const mlFc = ((payload as any).ml_forecast ?? []).filter((r: any) =>
          (showKc || bd(r.code) !== '科创') && (showCy || bd(r.code) !== '创业') && (!only50 || r.price <= 50))
        return mlFc.length > 0 && (
        <Card size="small" style={{ marginTop: 12 }} title={`明日预判 ${mlFc.length} 只(形态已成型、只差站上突破价;非实时,需明日收盘站上+放量+创新高)`}>
          <Table rowKey={(r: any) => r.code + r.typ} size="small" pagination={{ pageSize: 20 }}
            dataSource={mlFc}
            columns={[
              { title: '形态', dataIndex: 'typ', width: 100, filters: [{ text: 'N字型', value: 'N字型' }, { text: 'W型', value: 'W型' }], onFilter: (v: any, r: any) => r.typ.includes(v), render: (v: string) => v.includes('/') ? <Tag color="gold">N字/W型</Tag> : <Tag color={v === 'W型' ? 'purple' : 'blue'}>{v}</Tag> },
              { title: '名称', dataIndex: 'name', render: (v: string, r: any) => <a onClick={() => openKline(r.code, payload.latest ?? '', undefined)}>{v}</a> },
              { title: '代码', dataIndex: 'code' },
              { title: '现价', dataIndex: 'price', render: (v: number) => `${v}元` },
              { title: '突破价', dataIndex: 'trig', render: (v: number) => <b style={{ color: '#c0392b' }}>{v}元</b> },
              { title: '距突破', dataIndex: 'dist', defaultSortOrder: 'ascend', sorter: (a: any, b: any) => a.dist - b.dist, render: (v: number) => <span style={{ color: v <= 1 ? '#c0392b' : '#5b554a' }}>+{v}%</span> },
            ]} />
          <div style={{ fontSize: 12, color: '#999', marginTop: 6 }}>
            预判=N字(站上高B)/W型(站上颈线C)形态已成型、现价在突破价下方 ≤5% 的票。<b>明日站上突破价 + 放量 + 创新高</b> 才算触发;且突破后还需 ML 打分进档才进上方信号表。非实时提示。
          </div>
        </Card>
        )
      })()}

      {payload && <div style={{ background: '#f6efdd', border: '1px solid #e6d6a8', borderRadius: 8, padding: 10, fontSize: 12, color: '#7a5d18', marginTop: 10 }}>
        <b>⚠️</b> "至今最大涨幅"=突破日到现在(或满60日)的最高浮盈,非实际买卖收益;"唐奇安/缠论离场"括号内为持仓交易日数(仍持仓算到最新交易日);
        "进行中"=该出场口径下尚未离场。点表头可排序/筛选。模型严格用区间起始日之前的数据训练,无未来函数。
        「LLM分析」对该股在突破日跑 技术+消息面 多agent 分析(约1-3分钟),给分析师层(趋势感知)买/卖/持。
      </div>}

      <Modal open={!!kl?.open} width={1560} footer={null} onCancel={() => setKl(null)}
        title={<span>K线 + 缠论形态 {kl?.code} @ {kl?.date}(突破日)</span>}>
        {kl?.loading ? <div style={{ textAlign: 'center', padding: 40 }}><Spin tip="加载中..." /><div style={{ height: 30 }} /></div> :
          kl?.data?.error ? <pre style={{ color: 'red', whiteSpace: 'pre-wrap' }}>{kl.data.error}</pre> :
            kl?.data?.ok ? <div>
              <KLineChart data={kl.data} marks={kl.marks} />
              <div style={{ fontSize: 12, color: '#888', marginTop: 6 }}>蓝线=缠论笔;橙框=中枢;灰虚线=突破日;红▲=买(突破买入)/补(缠论买点回补);绿▼=缠(缠论卖点止盈)/止(跌破60日线·止损终止)/唐(唐奇安下轨清仓);缠论M3全历史口径,与表格一致;红涨绿跌(前复权)</div>
            </div> : <div>无数据</div>}
      </Modal>

      <Modal open={!!ana?.open} width={1200} footer={null} onCancel={closeAna}
        title={<span>LLM 分析 {ana?.name ? `${ana.name}(${ana.code})` : ana?.code} @ {ana?.date}
          {ana?.cached && <span className="ana-chip"><span className="ana-dot" />已缓存</span>}
          {ana?.phase === 'done' && <span className="ana-redo" onClick={() => analyze(ana.code, ana.date, true, ana.name)}>↻ 重新分析</span>}
        </span>}>
        {ana?.phase === 'error' ? <pre style={{ color: 'red', whiteSpace: 'pre-wrap' }}>{ana.stage}</pre> : ana && <div
          ref={scrollRef} onScroll={(e) => { const el = e.currentTarget; stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60 }}
          style={{ maxHeight: '68vh', overflowY: 'auto', paddingRight: 6 }}>
          {ana.market_report && <ChatMsg img={AV.market} bg="#3b82f6" role="技术面分析师"><MD>{ana.market_report}</MD></ChatMsg>}
          {ana.news_report && <ChatMsg img={AV.news} bg="#f97316" role="消息面分析师"><MD>{ana.news_report}</MD></ChatMsg>}
          <DebatePanel turns={ana.shownDlg || []} live={ana.phase === 'analyzing' || ana.phase === 'starting'} />

          {(ana.phase === 'starting' || ana.phase === 'analyzing') &&
            <ChatMsg av="🤖" bg="#8a8378" role="进行中">
              <span style={{ color: '#8a8378' }}>{ana.stage} <Typing /></span>
            </ChatMsg>}

          {(ana.business || ana.bizText) && <ChatMsg img={AV.biz} bg="#a855f7" role="基本面分析师">
            {ana.business ? (() => { const b = ana.business; if (b.raw) return <span>{b.raw}</span>; return <>
              <div><b>主营:</b> {b.products} {b.chain && <Tag style={{ marginLeft: 4 }}>{b.chain}</Tag>}<span style={{ color: '#666' }}>{b.chain_desc}</span></div>
              {b.market_pos && <div><b>市场地位:</b> {b.market_pos}</div>}
              {b.pricing && <div><b>议价能力:</b> {b.pricing}</div>}
              {b.bottleneck && <div><b>卡脖子:</b> <Tag color={b.bottleneck === '被卡' ? 'red' : b.bottleneck === '卡别人' ? 'green' : b.bottleneck === '部分' ? 'orange' : 'default'}>{b.bottleneck}</Tag>{b.reason}</div>}
              {b.peg_data && (() => { const p = b.peg_data; const col = p.peg < 1 ? '#c0392b' : p.peg < 1.5 ? '#7a5d18' : '#1f8e5a'
                return <div style={{ marginTop: 4, padding: '6px 8px', background: '#eef3f8', border: '1px solid #cfe0ef', borderRadius: 6 }}>
                  <b>前瞻PEG(林奇):</b> <b style={{ color: col, fontSize: 15 }}>{p.peg}</b> <Tag color={p.peg < 1 ? 'red' : p.peg < 1.5 ? 'gold' : 'green'}>{p.tier}</Tag>
                  <span style={{ color: '#5b554a' }}>= 前瞻PE {p.fwd_pe} ÷ (CAGR {p.cagr}%×100);券商全年预测净利 {p.fwd_np}亿{p.digest ? `;当前PE需 ${p.digest} 年增长消化到30x` : ''}</span>
                  {b.peg && <div style={{ marginTop: 2 }}>{b.peg}</div>}
                </div> })()}
              {!b.peg_data && b.peg && <div style={{ marginTop: 4, color: '#666' }}><b>PEG:</b> {b.peg}</div>}
              {b.valuation && <div style={{ marginTop: 4, padding: '6px 8px', background: '#f6efdd', border: '1px solid #e6d6a8', borderRadius: 6 }}><b>股价·业绩·估值匹配:</b> {b.valuation}</div>}
              {b.summary && <div style={{ marginTop: 2 }}><b>小结:</b> {b.summary}</div>}
              {b.fin && <div style={{ color: '#999', fontSize: 12 }}>财务: {b.fin}</div>}
            </> })() : <span style={{ color: '#666', whiteSpace: 'pre-wrap' }}>{ana.bizText}<span className="cursor">▍</span></span>}
          </ChatMsg>}

          {ana.phase === 'streaming' && !ana.business && !ana.bizText &&
            <ChatMsg img={AV.biz} bg="#a855f7" role="基本面分析师"><Typing /></ChatMsg>}

          {(ana.verdict || ana.verText) && <ChatMsg img={AV.decision} bg="#0b6e4f" role="综合决策">
            {ana.verdict
              ? <div className="verdict-card" style={{ background: '#f6efdd', border: '1px solid #e6d6a8', borderRadius: 8, padding: '10px 12px' }}>
                  <Tag style={{ fontSize: 15, padding: '2px 12px' }} color={ana.verdict.action === '卖出' ? 'red' : ana.verdict.action === '买入' ? 'green' : 'blue'}>{ana.verdict.action}</Tag>
                  <b style={{ marginLeft: 6 }}>置信 {ana.verdict.confidence}</b>
                  <div style={{ marginTop: 6 }}>{ana.verdict.reasoning}</div>
                </div>
              : <span style={{ color: '#666', whiteSpace: 'pre-wrap' }}>{ana.verText}<span className="cursor">▍</span></span>}
          </ChatMsg>}
        </div>}
      </Modal>

      <Modal open={!!adv?.open} width={1560} footer={null} onCancel={() => setAdv(null)}
        title={<span>缠论卖点提示 {adv?.code} · 突破日 {adv?.date} 买入</span>}>
        {adv?.loading ? <div style={{ textAlign: 'center', padding: 40 }}><Spin tip="缠论回放中..." /><div style={{ height: 30 }} /></div> :
          adv?.data?.ok === false ? <pre style={{ color: 'red', whiteSpace: 'pre-wrap' }}>{adv.data.error}</pre> :
            adv?.data && <AdviceResult res={adv.data} />}
      </Modal>
    </div>
  )
}


function AdvisePage() {
  const [code, setCode] = useState('')
  const [date, setDate] = useState('')
  const [res, setRes] = useState<any>(null)
  const [loading, setLoading] = useState(false)

  const run = async () => {
    if (!code || !date) { message.warning('请输入股票代码和买入日期'); return }
    setLoading(true); setRes(null)
    try {
      const r = await fetch('/api/advise', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code, buy_date: date }) })
      const j = await r.json()
      if (!j.ok) { message.error(j.error || '失败'); setLoading(false); return }
      setRes(j)
    } catch (e) { message.error('请求失败,后端起了吗? ' + e) } finally { setLoading(false) }
  }

  return (
    <div style={{ maxWidth: 1850, margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="Chan-theory Exit Advisor · 单只个股">缠论卖点提示</PageTitle>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12 }}>
        <span>代码</span><Input style={{ width: 190 }} placeholder="个股/ETF 如 300903 / 510300" value={code} onChange={e => setCode(e.target.value.trim())} onPressEnter={run} />
        <span>买入日期</span><DatePicker style={{ width: 150 }} value={date ? dayjs(date) : null}
          onChange={(_, ds) => setDate(ds as string)} disabledDate={d => d && d > dayjs().endOf('day')} />
        <Button type="primary" loading={loading} onClick={run}>确定</Button>
      </div>
      {loading && <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>}
      {res && <AdviceResult res={res} />}
    </div>
  )
}

function AdviceResult({ res }: { res: any }) {
  const a = res?.advice
  return (
      <div>
        <Card size="small" style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 15, fontWeight: 600 }}>{res.name}({res.code}) 买入 {res.bo} @ {res.entry} 元(前复权){a.legs > 1 ? `,已滚动 ${a.legs} 腿` : ''}</div>
          {a.state === 'holding' ? (
            a.czsc_sell_today
              ? <div style={{ color: '#27ae60', fontSize: 15, marginTop: 6 }}><b>⚠️ 最新交易日({a.latest_date})触发「{a.sell_rule}」→ 明日开盘止盈卖出</b>(卖后若回调出现缠论买点、价在60日线上方可回补)。当前 {a.latest_close} 元,累计 {a.total_ret_pct}%{a.sell_top != null && <span>。最近缠论顶分型 顶 <b>{a.sell_top}</b> / 低 <b>{a.sell_confirm}</b> 元({a.sell_top_date}),仅供参考</span>}</div>
              : <div style={{ fontSize: 15, marginTop: 6 }}><b>继续持有(M3 未触发离场)。</b>M3 卖出条件:①出现<b style={{ color: '#27ae60' }}>缠论一卖(顶背驰)/MACD顶背驰</b>→止盈;②或收盘<b style={{ color: '#27ae60' }}>跌破 {a.trigger} 元</b>(60日线 {a.ma60} / 现价回撤15%止损 {a.stop},取高者)→离场。当前 {a.latest_close} 元,累计 {a.total_ret_pct}%{a.sell_top != null && <span>。最近缠论顶分型 顶 <b>{a.sell_top}</b> / 低 <b>{a.sell_confirm}</b> 元({a.sell_top_date},仅供参考;<b>M3 按顶背驰一卖离场,不据跌破顶分型卖出</b>)</span>}</div>
          ) : a.state === 'waiting' ? (
            <div style={{ fontSize: 15, marginTop: 6 }}>已于 <b>{a.sold_date}</b> 触发「{a.sell_rule}」止盈 @ {a.sold_price} 元(已实现 <b style={{ color: a.realized_pct >= 0 ? '#c0392b' : '#27ae60' }}>{a.realized_pct >= 0 ? '+' : ''}{a.realized_pct}%</b>),<b>现空仓等回补</b>。{a.buy_today ? <b style={{ color: '#c0392b' }}>最新交易日已现缠论买点 → 明日可回补 @ 现价 {a.latest_close}</b> : <span>出现缠论买点且价在60日线({a.ma60})上方则买回;若先跌破60日线则放弃这波。当前 {a.latest_close} 元</span>}</div>
          ) : (
            <div style={{ fontSize: 15, marginTop: 6 }}>本轮已于 <b>{a.exit_date}</b> 因<b style={{ color: '#27ae60' }}>「{a.reason}」</b>终止 @ {a.exit_price} 元,{a.legs > 1 ? '复利' : ''}收益 <b style={{ color: a.ret_pct >= 0 ? '#c0392b' : '#27ae60' }}>{a.ret_pct >= 0 ? '+' : ''}{a.ret_pct}%</b></div>
          )}
          {a.state !== 'ended' && <div style={{ marginTop: 10, padding: '8px 10px', background: '#f1f6f2', border: '1px solid #d3e4da', borderRadius: 6, fontSize: 14 }}>
            <b>📌 下一交易日({a.latest_date} 的下一交易日)操作:</b>
            {a.state === 'holding' ? (<>
              <div style={{ marginTop: 4 }}><b style={{ color: '#27ae60' }}>卖出 →</b> ① 若出现缠论卖点(一卖/MACD顶背驰)→ 止盈卖出(卖后转为等回补);② 或 收盘跌破 <b>{a.trigger}</b> 元(60日线 {a.ma60} / 现价回撤15%止损 {a.stop} 取高)→ 离场。</div>
              <div><b style={{ color: '#999' }}>买入 →</b> 已持仓,无需操作(回补仅在止盈卖出后考虑)。</div>
            </>) : (<>
              <div style={{ marginTop: 4 }}><b style={{ color: '#c0392b' }}>买入 →</b> 若出现缠论买点 且 收盘在 60日线(<b>{a.ma60}</b>)上方 → 回补买入。</div>
              <div><b style={{ color: '#999' }}>卖出 →</b> 当前空仓无持仓可卖;若先跌破 60日线({a.ma60})→ 放弃这波,不再回补。</div>
            </>)}
          </div>}
        </Card>
        <KLineChart data={res} marks={res.marks} />
        <div style={{ fontSize: 12, color: '#888', marginTop: 6 }}>蓝线=缠论笔;橙框=中枢;红▲=买/回补(补=回调买回)、绿▼=缠论卖点止盈;红涨绿跌(前复权)。规则(M3):缠论卖点止盈 → 回调缠论买点回补 → 跌破60日线或入场价85%终止。</div>
      </div>
  )
}

export default function App() {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => {
    const f = () => setHash(window.location.hash)
    window.addEventListener('hashchange', f)
    return () => window.removeEventListener('hashchange', f)
  }, [])
  return (
    <ConfigProvider locale={zhCN} theme={QUANT_THEME}>
      {(() => {
        const r = hash.replace('#', '')
        if (r === '/advise') return <AdvisePage />
        if (r === '/holdings') return <HoldingsPage />
        if (r === '/limitup') return <LimitUpPage />
        if (r === '/etfshare') return <EtfSharePage />
        if (r === '/bulltop') return <BullTopPage />
        if (r === '/xiaoxifu') return <XiaoxifuPage />
        if (r === '/boll') return <BollPage />
        if (r === '/concept') return <ConceptPage />
        return <MainPage />
      })()}
    </ConfigProvider>
  )
}

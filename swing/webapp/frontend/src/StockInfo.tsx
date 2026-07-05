import { useState } from 'react'
import { Popover, Spin, Tag } from 'antd'

type Info = {
  ok: boolean; code: string; name: string
  price?: number | null; pct_chg?: number | null
  pe_ttm?: number | null; pb?: number | null; mv_yi?: number | null; dv?: number | null
  sw?: string | null; concepts?: string[]
}

const cache = new Map<string, Info>()

function Body({ code }: { code: string }) {
  const [info, setInfo] = useState<Info | null>(cache.get(code) || null)
  if (!info) {
    fetch(`/api/stock_info?code=${encodeURIComponent(code)}`)
      .then(r => r.json()).then((j: Info) => { if (j && j.ok) { cache.set(code, j); setInfo(j) } })
      .catch(() => {})
    return <div style={{ padding: 8, minWidth: 180 }}><Spin size="small" /></div>
  }
  const up = (info.pct_chg ?? 0) >= 0
  const num = (v?: number | null, s = '') => (v === null || v === undefined ? '—' : `${v}${s}`)
  return (
    <div style={{ minWidth: 220, fontSize: 12, lineHeight: 1.7 }}>
      <div style={{ fontWeight: 700, fontSize: 13 }}>{info.name} <span style={{ color: '#999', fontWeight: 400 }}>{info.code.slice(0, 6)}</span></div>
      <div>现价 <b>{num(info.price)}</b>{info.pct_chg !== null && info.pct_chg !== undefined &&
        <b style={{ color: up ? '#c0392b' : '#1f8e5a', marginLeft: 6 }}>{up ? '+' : ''}{info.pct_chg}%</b>}</div>
      <div style={{ color: '#555' }}>PE(TTM) {num(info.pe_ttm)} · PB {num(info.pb)} · 市值 {num(info.mv_yi, '亿')} · 股息 {num(info.dv, '%')}</div>
      {info.sw && <div><Tag color="blue" style={{ marginTop: 2 }}>{info.sw}</Tag></div>}
      {info.concepts && info.concepts.length > 0 &&
        <div style={{ marginTop: 2 }}>{info.concepts.map(c => <Tag key={c} color="geekblue">{c}</Tag>)}</div>}
    </div>
  )
}

export function StockName({ code, children }: { code: string; children: React.ReactNode }) {
  if (!code) return <>{children}</>
  return (
    <Popover content={<Body code={code} />} mouseEnterDelay={0.3} placement="right" trigger="hover">
      <span style={{ cursor: 'help', borderBottom: '1px dotted #bbb' }}>{children}</span>
    </Popover>
  )
}

// 组合回测纯逻辑(从 App.jsx 抽出,便于测试)。15万4等份/最多parts只/满仓放弃/同股不加仓。
export const INIT = 150000

export interface SignalRow {
  ts: string
  date: string
  name?: string
  score: number
  __latest?: string
  [k: string]: unknown
}

type Num = number | null | undefined

// 组合净值曲线。ponytail: 无逐日价,持仓中仓位按其(终值)ret 标记浮盈计入。
export function portfolio(rows: SignalRow[], exk: string, retk: string, cal: string[], parts: number): [string, number][] {
  const buys: Record<string, { ts: string; ex: string; ret: number; sc: number }[]> = {}
  rows.forEach(r => {
    const ret = r[retk] as Num
    if (ret == null) return
    const ex = (r[exk] as string) || (r.__latest as string)
    ;(buys[r.date] = buys[r.date] || []).push({ ts: r.ts, ex, ret, sc: r.score })
  })
  let cash = INIT
  let op: { ts: string; ex: string; ret: number; amt: number }[] = []
  const curve: [string, number][] = []
  for (const d of cal) {
    op = op.filter(p => { if (p.ex <= d) { cash += p.amt * (1 + p.ret); return false } return true })
    const bs = (buys[d] || []).slice().sort((a, b) => b.sc - a.sc)
    for (const b of bs) {
      if (op.length >= parts) break
      if (op.some(p => p.ts === b.ts)) continue
      const unit = (cash + op.reduce((s, p) => s + p.amt, 0)) / parts
      if (cash + 1e-6 >= unit && unit > 0) { cash -= unit; op.push({ ts: b.ts, ex: b.ex, ret: b.ret, amt: unit }) }
    }
    curve.push([d, Math.round(cash + op.reduce((s, p) => s + p.amt * (1 + p.ret), 0))])
  }
  return curve
}

export interface TradeRec {
  key: string; ts: string; name?: string; date: string; status: string
  exit: unknown; ret: Num; hold: unknown; open: unknown
}

// 按组合规则重放,产出交易记录;满仓/已持有的信号标"满仓错过"。
export function tradeLog(rows: SignalRow[], exk: string, retk: string, holdk: string, openk: string,
                        cal: string[], parts: number): TradeRec[] {
  const byDate: Record<string, SignalRow[]> = {}
  rows.forEach(r => { if ((r[retk] as Num) == null) return; (byDate[r.date] = byDate[r.date] || []).push(r) })
  const last = cal[cal.length - 1]
  let op: { ts: string; ex: string }[] = []
  const log: TradeRec[] = []
  for (const d of cal) {
    op = op.filter(p => p.ex > d)
    const bs = (byDate[d] || []).slice().sort((a, b) => b.score - a.score)
    for (const r of bs) {
      if (op.some(p => p.ts === r.ts)) continue
      const status = op.length >= parts ? '满仓错过' : '已交易'
      if (status === '已交易') op.push({ ts: r.ts, ex: (r[exk] as string) || last })
      log.push({ key: r.ts + r.date, ts: r.ts, name: r.name, date: r.date, status,
                 exit: r[exk], ret: r[retk] as Num, hold: r[holdk], open: r[openk] })
    }
  }
  return log
}

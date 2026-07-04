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

// 缠论M3 份级 B 并仓组合(前端算,吃筛选后的 rows)。镜像后端 run_ml_signals._cz_portfolio。
// 建仓/加仓/回补各占1份,满仓取消,缠卖/止释放整仓全部份,未平仓按最新后复权价盯市,扣双边费。
const CZ_COST = 0.0006
type CzLeg = [string, string, number]   // [日期, 买/补/缠/止, 后复权价]
interface CzRec { ts: string; name?: string; date: string; type: string; status: string; exit: string | null; ret: number | null; hold: number | null; open: boolean; key?: string }

function busdays(a: string, b: string): number {
  let n = 0; const d = new Date(a), e = new Date(b)
  while (d < e) { d.setDate(d.getDate() + 1); const w = d.getDay(); if (w !== 0 && w !== 6) n++ }
  return n
}

export function czPortfolio(rows: SignalRow[], cal: string[], parts: number): { curve: [string, number][]; log: CzRec[] } {
  if (!cal.length) return { curve: [], log: [] }
  const nameOf: Record<string, string> = {}
  const byStock: Record<string, SignalRow[]> = {}
  rows.forEach(r => { if ((r as any).czret == null) return; nameOf[r.ts] = r.name || ''; (byStock[r.ts] = byStock[r.ts] || []).push(r) })

  interface Pos { ts: string; buys: [string, string, number][]; rebuys: [string, number][]; sells: [string, number][]; curc: number }
  const positions: Pos[] = []
  for (const ts in byStock) {
    const rs = byStock[ts].slice().sort((a, b) => a.date < b.date ? -1 : 1)
    let cur: Pos | null = null
    let curExit: string | null = 'flat'   // 'flat' | null(持仓中) | 离场日
    for (const r of rs) {
      const legs = ((r as any).czlegs || []) as CzLeg[]
      const entry = legs[0]?.[2] ?? (r as any).price ?? 0
      const isAnchor = curExit === 'flat' || (curExit != null && curExit !== 'flat' && r.date > curExit)
      if (isAnchor) {
        cur = { ts, buys: [[r.date, '建仓', entry]], rebuys: [], sells: [], curc: (r as any).czcur ?? entry }
        for (const [ld, lk, lp] of legs) {
          if (lk === '补') cur.rebuys.push([ld, lp])
          else if (lk === '缠' || lk === '止') cur.sells.push([ld, lp])
        }
        positions.push(cur)
        curExit = (r as any).czopen ? null : ((r as any).czexit as string)
      } else if (cur) {
        cur.buys.push([r.date, '加仓', entry])
      }
    }
  }

  const ev: Record<string, [string, string, number, number, string][]> = {}   // date -> [kind,ts,pid,price,tag]
  positions.forEach((p, pid) => {
    p.buys.forEach(([bd, bk, bp]) => (ev[bd] = ev[bd] || []).push(['buy', p.ts, pid, bp, bk]))
    p.rebuys.forEach(([rd, rp]) => (ev[rd] = ev[rd] || []).push(['buy', p.ts, pid, rp, '回补']))
    p.sells.forEach(([sd, sp]) => (ev[sd] = ev[sd] || []).push(['sell', p.ts, pid, sp, '']))
  })
  const curc = positions.map(p => p.curc)
  const r1 = (x: number) => Math.round(x * 10) / 10
  let cash = INIT
  let op: { ts: string; pid: number; amt: number; entry: number; rec: CzRec | null }[] = []
  const log: CzRec[] = []
  const curve: [string, number][] = []
  const last = cal[cal.length - 1]
  for (const d of cal) {
    for (const [kind, ts, pid, price, tag] of (ev[d] || []).slice().sort((a, b) => (a[0] === 'sell' ? 0 : 1) - (b[0] === 'sell' ? 0 : 1))) {
      if (kind === 'sell') {
        const keep: typeof op = []
        for (const lg of op) {
          if (lg.pid === pid) {
            const gross = (price / lg.entry) * (1 - 2 * CZ_COST)
            cash += lg.amt * gross
            if (lg.rec) { lg.rec.ret = r1((gross - 1) * 100); lg.rec.exit = d; lg.rec.hold = busdays(lg.rec.date, d); lg.rec.open = false }
          } else keep.push(lg)
        }
        op = keep
      } else {
        let status = op.length >= parts ? '满仓取消' : (price ? '已买入' : '无价跳过')
        let rec: CzRec | null = null
        if (status === '已买入') {
          const unit = (cash + op.reduce((s, l) => s + l.amt, 0)) / parts
          if (cash + 1e-6 >= unit && unit > 0) {
            rec = { ts, name: nameOf[ts], date: d, type: tag, status: '已买入', exit: null, ret: null, hold: null, open: false }
            op.push({ ts, pid, amt: unit, entry: price, rec }); cash -= unit
          } else status = '满仓取消'
        }
        if (tag !== '回补') log.push(rec || { ts, name: nameOf[ts], date: d, type: tag, status, exit: null, ret: null, hold: null, open: false })
      }
    }
    let eq = cash
    for (const lg of op) eq += lg.amt * ((curc[lg.pid] || lg.entry) / lg.entry)
    curve.push([d, Math.round(eq)])
  }
  for (const lg of op) if (lg.rec) {
    const c = curc[lg.pid] || lg.entry
    lg.rec.ret = r1(((c / lg.entry) * (1 - 2 * CZ_COST) - 1) * 100); lg.rec.exit = null; lg.rec.hold = busdays(lg.rec.date, last); lg.rec.open = true
  }
  return { curve, log }
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

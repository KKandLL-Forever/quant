// 涨停统计:纯逻辑(buildLadder/calcSuccessRates 从 trade_dashboard 迁移)+ 从本项目后端取数(/api/limitup 读 DuckDB limit_list_d)。
export interface LimitStock {
  tsCode: string; name: string; industry: string; close: number; pctChg: number
  amount: number; turnoverRatio: number; openTimes: number; limitTimes: number
  fdAmount: number; upStat: string; firstTime: string; lastTime: string
  limitType: 'U' | 'D' | 'Z'; luDesc: string
}
export interface LadderGroup { days: number; stocks: LimitStock[]; successCount?: number; successRate?: number }
export interface LimitLadder { tradeDate: string; groups: LadderGroup[]; total: number }
export interface SuccessRateRow { days: number; attempts: number; successes: number; rate: number }

// 按连板天数分组构建梯队(高板在前)
export function buildLadder(tradeDate: string, stocks: LimitStock[]): LimitLadder {
  const map = new Map<number, LimitStock[]>()
  for (const s of stocks) {
    if (!map.has(s.limitTimes)) map.set(s.limitTimes, [])
    map.get(s.limitTimes)!.push(s)
  }
  const groups: LadderGroup[] = Array.from(map.entries())
    .sort(([a], [b]) => b - a)
    .map(([days, ss]) => ({ days, stocks: ss.slice().sort((a, b) => b.fdAmount - a.fdAmount) }))
  return { tradeDate, groups, total: stocks.length }
}

// 各板数的晋级成功率(今日第N板 → 次日仍涨停)
export function calcSuccessRates(dates: string[], data: Map<string, LimitStock[]>): SuccessRateRow[] {
  const counters = new Map<number, { attempts: number; successes: number }>()
  for (let i = 0; i < dates.length - 1; i++) {
    const todayStocks = data.get(dates[i]) ?? []
    const tomorrowSet = new Set((data.get(dates[i + 1]) ?? []).map(s => s.tsCode))
    for (const s of todayStocks) {
      if (!counters.has(s.limitTimes)) counters.set(s.limitTimes, { attempts: 0, successes: 0 })
      const c = counters.get(s.limitTimes)!
      c.attempts++
      if (tomorrowSet.has(s.tsCode)) c.successes++
    }
  }
  return Array.from(counters.entries())
    .sort(([a], [b]) => a - b)
    .map(([days, { attempts, successes }]) => ({ days, attempts, successes, rate: attempts > 0 ? successes / attempts : 0 }))
}

// 从后端拉区间涨停数据
export async function fetchLimitup(start: string, end?: string): Promise<{ dates: string[]; byDate: Map<string, LimitStock[]> }> {
  const r = await fetch('/api/limitup', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ start, end }),
  })
  const j = await r.json()
  if (!j.ok) throw new Error(j.error || '涨停数据获取失败')
  const byDate = new Map<string, LimitStock[]>(Object.entries(j.byDate))
  return { dates: j.dates as string[], byDate }
}

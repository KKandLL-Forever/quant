import { describe, it, expect } from 'vitest'
import { portfolio, tradeLog, INIT, type SignalRow } from './portfolio'

const cal = ['2026-01-01', '2026-01-02', '2026-01-03', '2026-01-04']

describe('portfolio', () => {
  it('单笔:1/4仓 +100% → 净值 = 本金 + 该份*100%', () => {
    const rows: SignalRow[] = [{ ts: 'A', date: '2026-01-01', score: 1, r: 1.0, ex: '2026-01-03' }]
    const c = portfolio(rows, 'ex', 'r', cal, 4)
    // 入场日买 1/4=37500,+100% → 该份变75000,总= 150000+37500=187500(平仓后现金化)
    expect(c[c.length - 1][1]).toBe(187500)
  })

  it('满仓放弃:parts=1 时第二只不再买', () => {
    const rows: SignalRow[] = [
      { ts: 'A', date: '2026-01-01', score: 2, r: 0.0, ex: '2026-01-04' },
      { ts: 'B', date: '2026-01-02', score: 1, r: 1.0, ex: '2026-01-03' },
    ]
    const c = portfolio(rows, 'ex', 'r', cal, 1)
    expect(c[c.length - 1][1]).toBe(INIT)   // 满仓,B 放弃,A 收益0 → 净值不变
  })

  it('ret 为 null 的行跳过', () => {
    const rows: SignalRow[] = [{ ts: 'A', date: '2026-01-01', score: 1, r: null, ex: '2026-01-03' }]
    expect(portfolio(rows, 'ex', 'r', cal, 4)[cal.length - 1][1]).toBe(INIT)
  })
})

describe('tradeLog', () => {
  it('满仓时后来的信号标"满仓错过"', () => {
    const rows: SignalRow[] = [
      { ts: 'A', date: '2026-01-01', score: 2, r: 0.1, ex: '2026-01-04', h: 3, o: false },
      { ts: 'B', date: '2026-01-02', score: 1, r: 0.2, ex: '2026-01-03', h: 1, o: false },
    ]
    const log = tradeLog(rows, 'ex', 'r', 'h', 'o', cal, 1)
    expect(log.find(x => x.ts === 'A')!.status).toBe('已交易')
    expect(log.find(x => x.ts === 'B')!.status).toBe('满仓错过')
  })

  it('同股持仓中再出信号被跳过(不重复入场)', () => {
    const rows: SignalRow[] = [
      { ts: 'A', date: '2026-01-01', score: 2, r: 0.1, ex: '2026-01-04', h: 3, o: false },
      { ts: 'A', date: '2026-01-02', score: 1, r: 0.2, ex: '2026-01-03', h: 1, o: false },
    ]
    const log = tradeLog(rows, 'ex', 'r', 'h', 'o', cal, 4)
    expect(log.length).toBe(1)
  })
})

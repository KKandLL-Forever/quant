// 交易/持仓相关类型(从 trade_dashboard 迁移)。Trade 由 zod schema 推导,其余为派生结构。
export type { Trade, TradeAction } from './schemas/trade'

export interface Holding {
  code: string
  tsCode: string
  name: string
  qty: number
  avgCost: number
  costBasis: number
}

export interface RealizedRecord {
  code: string
  tsCode: string
  name: string
  date: string
  qtySold: number
  avgCostAtSale: number
  sellPrice: number
  proceeds: number
  costPortion: number
  pnl: number
  pnlPct: number
  isInterest: boolean
}

export interface PortfolioState {
  holdings: Holding[]
  realized: RealizedRecord[]
  cash: number
  totalDeposit: number
  totalWithdraw: number
  totalRealizedPnl: number
  totalInterestIncome: number
  reportedFinalBalance: number
  firstDate: string
  lastDate: string
}

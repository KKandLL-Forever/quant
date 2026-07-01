// 券商流水 + 持仓派生(zustand persist localStorage)。从 trade_dashboard 迁移并简化。
import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import type { Trade, PortfolioState } from '../lib/tradeTypes'
import { buildPortfolio } from '../lib/tradesPortfolio'

interface TradesState {
  rawTrades: Trade[]
  fileName: string
  portfolio: PortfolioState | null
  setTrades: (t: Trade[], f: string) => void
  mergeTrades: (t: Trade[], f: string) => void
  clear: () => void
}

const key = (t: Trade): string =>
  t.contractNo ? t.contractNo : `${t.date}|${t.tsCode}|${t.action}|${t.price}|${t.qty}`

export const useTradesStore = create<TradesState>()(
  persist(
    (set) => ({
      rawTrades: [],
      fileName: '',
      portfolio: null,
      setTrades: (trades, fileName) =>
        set({ rawTrades: trades, fileName, portfolio: trades.length ? buildPortfolio(trades) : null }),
      mergeTrades: (nt, fileName) =>
        set((s) => {
          const ex = new Set(s.rawTrades.map(key))
          const merged = [...s.rawTrades, ...nt.filter(t => !ex.has(key(t)))].sort((a, b) => a.date.localeCompare(b.date))
          return { rawTrades: merged, fileName, portfolio: merged.length ? buildPortfolio(merged) : null }
        }),
      clear: () => set({ rawTrades: [], fileName: '', portfolio: null }),
    }),
    {
      name: 'quart:tradesStore',
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({ rawTrades: s.rawTrades, fileName: s.fileName }),
      onRehydrateStorage: () => (state) => {
        if (state && state.rawTrades?.length) state.portfolio = buildPortfolio(state.rawTrades)
      },
    },
  ),
)

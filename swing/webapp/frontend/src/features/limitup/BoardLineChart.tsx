// 连板日历折线:最高板/次高板双线 + 牛市区间底色(echarts)。
import ReactECharts from 'echarts-for-react'

export interface DayBoard { date: string; maxBoard: number; secondBoard: number; dragons: { tsCode: string; name: string; board: number }[] }

const BULL_MARKETS = [
  { start: '20140701', end: '20150630', label: '杠杆牛', color: '#c0392b' },
  { start: '20190101', end: '20210228', label: '2019结构牛', color: '#8b5cf6' },
  { start: '20240924', end: '20991231', label: '2024行情', color: '#8b5cf6' },
]
export const CHART_MARGIN = { top: 8, right: 16, bottom: 0, left: 8 }
export const Y_AXIS_WIDTH = 28
export const PLOT_LEFT = CHART_MARGIN.left + Y_AXIS_WIDTH

const fmtDate = (d: string) => (d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}` : d)

export function BoardLineChart({ data, height = 260 }: { data: DayBoard[]; height?: number }) {
  const dates = data.map(d => d.date)
  const area = BULL_MARKETS.map(b => {
    const within = dates.filter(d => d >= b.start && d <= b.end)
    return within.length
      ? [{ xAxis: within[0], itemStyle: { color: b.color, opacity: 0.08 }, label: { show: true, formatter: b.label, position: 'insideTop', color: b.color, fontSize: 11 } }, { xAxis: within[within.length - 1] }]
      : null
  }).filter((x): x is any[] => x !== null)

  const option = {
    animation: false,
    grid: { left: PLOT_LEFT, right: CHART_MARGIN.right, top: 12, bottom: 24 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, valueFormatter: (v: any) => `${v} 板` },
    xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { fontSize: 10, formatter: fmtDate } },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { color: '#f0eadc' } } },
    series: [
      { name: '最高板', type: 'line', data: data.map(d => d.maxBoard), showSymbol: false, lineStyle: { color: '#c0392b', width: 2 }, itemStyle: { color: '#c0392b' },
        markArea: area.length ? { silent: true, data: area } : undefined },
      { name: '次高板', type: 'line', data: data.map(d => d.secondBoard), showSymbol: false, lineStyle: { color: '#8b5cf6', width: 1.5, type: 'dashed' }, itemStyle: { color: '#8b5cf6' } },
    ],
  }
  return <ReactECharts option={option} notMerge style={{ height }} />
}

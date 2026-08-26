// 最高连板趋势 × 短线情绪温度:左轴连板高度(红线,标龙头名+板数)、右轴情绪温度(按值渐变着色)。点击任一点可回填该日温度。
import ReactECharts from 'echarts-for-react'
import type { DayBoard } from './BoardLineChart'

const fmtDate = (d: string) => (d.length === 8 ? `${d.slice(4, 6)}-${d.slice(6, 8)}` : d)
const BOARD_COLOR = '#b2564e'
const TEMP_COLORS = ['#5b9bd5', '#7fd4d0', '#f2d16b', '#e8913a', '#c0392b']

export function MoodChart({ days, temps, onPick, height = 520 }:
  { days: DayBoard[]; temps: Record<string, number>; onPick: (date: string) => void; height?: number }) {
  const dates = days.map(d => d.date)
  const option = {
    animation: false,
    grid: { left: 52, right: 58, top: 46, bottom: 34 },
    tooltip: {
      trigger: 'axis',
      formatter: (ps: any[]) => {
        const i = ps[0].dataIndex
        const d = days[i]
        const t = temps[d.date]
        return `${d.date}<br/>最高 <b>${d.maxBoard}板</b>${d.topName ? ` · ${d.topName}` : ''}`
          + `<br/>情绪温度 <b>${t == null ? '未填' : t + '°'}</b>`
      },
    },
    visualMap: { show: false, seriesIndex: 1, dimension: 1, min: 0, max: 100, inRange: { color: TEMP_COLORS } },
    xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { fontSize: 10, formatter: fmtDate } },
    yAxis: [
      { type: 'value', name: '连板高度', nameTextStyle: { fontSize: 11, color: '#8a94a6' }, nameGap: 14, min: 0, minInterval: 1, axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { color: '#f0eadc' } } },
      { type: 'value', name: '情绪温度 (°)', nameTextStyle: { fontSize: 11, color: '#8a94a6' }, nameGap: 14, min: 0, max: 100, interval: 20, axisLabel: { fontSize: 10 }, splitLine: { show: false } },
    ],
    dataZoom: [{ type: 'inside', xAxisIndex: 0 }],
    series: [
      {
        name: '最高连板数', type: 'line', yAxisIndex: 0, symbolSize: 5,
        data: days.map(d => d.maxBoard),
        lineStyle: { color: BOARD_COLOR, width: 2 }, itemStyle: { color: BOARD_COLOR },
        areaStyle: {
          color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [{ offset: 0, color: 'rgba(178,86,78,.42)' }, { offset: 0.55, color: 'rgba(178,86,78,.26)' },
              { offset: 0.85, color: 'rgba(178,86,78,.10)' }, { offset: 1, color: 'rgba(255,255,255,0)' }] },
        },
        labelLayout: { hideOverlap: true },
        label: {
          show: true, position: 'top', fontSize: 11, color: BOARD_COLOR, lineHeight: 13,
          backgroundColor: 'rgba(255,253,248,.92)', borderColor: '#e8d5d2', borderWidth: 1, borderRadius: 3, padding: [2, 4],
          formatter: (p: any) => {
            const d = days[p.dataIndex]
            return d.maxBoard >= 3 && d.topName ? `${d.topName}\n${d.maxBoard}板` : ''
          },
        },
      },
      {
        name: '短线情绪温度', type: 'line', yAxisIndex: 1, symbolSize: 5, connectNulls: false,
        data: dates.map(d => (temps[d] == null ? null : temps[d])),
        lineStyle: { width: 2 },
        labelLayout: { hideOverlap: true },
        label: { show: true, position: 'top', fontSize: 11, formatter: (p: any) => (p.value == null ? '' : `${p.value}°`) },
      },
    ],
  }
  return <ReactECharts option={option} notMerge style={{ height }} onEvents={{ click: (p: any) => onPick(dates[p.dataIndex]) }} />
}

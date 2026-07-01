// 持仓总览:上传券商对账单(GBK伪xls)→ 本地解析 → 持仓 / 已实现盈亏 / 汇总。纯前端,不接后端。
import { Upload, Button, Card, Table, Tag, message, Popconfirm } from 'antd'
import { useTradesStore } from '../../store/tradesStore'
import { parseTradeFile } from '../../lib/parser'
import type { Holding, RealizedRecord } from '../../lib/tradeTypes'

const money = (n: number, d = 0) => n.toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d })
const pnlColor = (n: number) => (n > 0 ? '#c0392b' : n < 0 ? '#1f8e5a' : '#5b554a')
const Signed = ({ v, d = 0, pct = false }: { v: number; d?: number; pct?: boolean }) => (
  <span style={{ color: pnlColor(v), fontWeight: 600 }}>
    {v > 0 ? '+' : ''}{pct ? (v * 100).toFixed(1) + '%' : money(v, d)}
  </span>
)

const StatCard = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <Card size="small" style={{ minWidth: 150 }}>
    <div style={{ fontSize: 12, color: 'var(--ink-soft)' }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>{children}</div>
  </Card>
)

export default function HoldingsPage() {
  const { portfolio, fileName, rawTrades, mergeTrades, clear } = useTradesStore()

  const onFile = async (file: File) => {
    try {
      const buf = await file.arrayBuffer()
      const trades = parseTradeFile(buf)
      mergeTrades(trades, file.name)
      message.success(`解析 ${trades.length} 笔,合并后共 ${useTradesStore.getState().rawTrades.length} 笔`)
    } catch (e) {
      message.error((e as Error)?.message || '解析失败,请确认是券商导出的对账单')
    }
    return false as const
  }

  const holdCols = [
    { title: '代码', dataIndex: 'tsCode', render: (v: string, r: Holding) => v || r.code },
    { title: '名称', dataIndex: 'name' },
    { title: '数量', dataIndex: 'qty', align: 'right' as const, render: (v: number) => money(v) },
    { title: '成本价', dataIndex: 'avgCost', align: 'right' as const, render: (v: number) => v.toFixed(3) },
    { title: '成本额', dataIndex: 'costBasis', align: 'right' as const, sorter: (a: Holding, b: Holding) => a.costBasis - b.costBasis, render: (v: number) => money(v) },
  ]
  const realCols = [
    { title: '日期', dataIndex: 'date', sorter: (a: RealizedRecord, b: RealizedRecord) => (a.date < b.date ? -1 : 1), defaultSortOrder: 'descend' as const },
    { title: '名称', dataIndex: 'name', render: (v: string, r: RealizedRecord) => <>{v} {r.isInterest && <Tag color="gold">逆回购</Tag>}</> },
    { title: '卖出量', dataIndex: 'qtySold', align: 'right' as const, render: (v: number) => money(v) },
    { title: '卖出价', dataIndex: 'sellPrice', align: 'right' as const, render: (v: number) => v.toFixed(3) },
    { title: '盈亏', dataIndex: 'pnl', align: 'right' as const, sorter: (a: RealizedRecord, b: RealizedRecord) => a.pnl - b.pnl, render: (v: number) => <Signed v={v} d={0} /> },
    { title: '盈亏%', dataIndex: 'pnlPct', align: 'right' as const, render: (v: number) => <Signed v={v} pct /> },
  ]

  return (
    <div style={{ maxWidth: 1850, margin: '18px auto', padding: '0 16px' }}>
      <Header />
      <PageTitle kicker="Local Trade Journal · 上传对账单,纯本地解析">持仓总览</PageTitle>

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16 }}>
        <Upload beforeUpload={onFile} showUploadList={false} accept=".xls,.txt,.csv">
          <Button type="primary">上传券商对账单</Button>
        </Upload>
        {fileName && <span style={{ fontSize: 13, color: 'var(--ink-soft)' }}>当前:{fileName} · {rawTrades.length} 笔流水</span>}
        {rawTrades.length > 0 && (
          <Popconfirm title="清空本地流水?" onConfirm={clear} okText="清空" cancelText="取消">
            <Button danger size="small">清空</Button>
          </Popconfirm>
        )}
      </div>

      {!portfolio ? (
        <Card><div style={{ padding: 30, textAlign: 'center', color: 'var(--ink-soft)' }}>
          上传券商导出的对账单(.xls / GBK TSV)开始。数据只存在你本地浏览器,不上传服务器。
        </div></Card>
      ) : (
        <>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 16 }}>
            <StatCard label="现金余额"><Signed v={portfolio.cash} /></StatCard>
            <StatCard label="持仓成本合计">{money(portfolio.holdings.reduce((s, h) => s + h.costBasis, 0))}</StatCard>
            <StatCard label="已实现盈亏"><Signed v={portfolio.totalRealizedPnl} /></StatCard>
            <StatCard label="逆回购利息"><Signed v={portfolio.totalInterestIncome} /></StatCard>
            <StatCard label="累计入金">{money(portfolio.totalDeposit)}</StatCard>
            <StatCard label="累计出金">{money(portfolio.totalWithdraw)}</StatCard>
            <StatCard label="报告期余额">{money(portfolio.reportedFinalBalance)}</StatCard>
          </div>
          <div style={{ fontSize: 12, color: 'var(--ink-soft)', margin: '0 0 8px' }}>
            区间 {portfolio.firstDate} ~ {portfolio.lastDate} · 持仓 {portfolio.holdings.length} 只 · 已实现 {portfolio.realized.length} 笔(市值/浮盈需接实时行情,暂按成本展示)
          </div>
          <Card size="small" title="当前持仓" style={{ marginBottom: 16 }}>
            <Table rowKey={(r: Holding) => r.tsCode || r.code} columns={holdCols} dataSource={portfolio.holdings} size="small" pagination={false} />
          </Card>
          <Card size="small" title="已实现盈亏">
            <Table rowKey={(r: RealizedRecord) => r.date + r.tsCode + r.qtySold} columns={realCols} dataSource={portfolio.realized} size="small" pagination={{ pageSize: 30 }} />
          </Card>
        </>
      )}
    </div>
  )
}

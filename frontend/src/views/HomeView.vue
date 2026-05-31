<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import axios from 'axios'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement,
  Title, Tooltip, Legend, Filler
} from 'chart.js'
import ChartDataLabels from 'chartjs-plugin-annotation'
import PositionsView from './PositionsView.vue'

ChartJS.register(
  CategoryScale, LinearScale, PointElement, LineElement,
  Title, Tooltip, Legend, Filler, ChartDataLabels
)

// ─── Types ───────────────────────────────────────────────────────────────────

interface Stats {
  today_buy_signals?: number
  latest_date?: string
}

interface PositionSummary {
  total_value?: number
  total_account_value?: number
  total_cost?: number
  holding_profit?: number
  history_profit?: number
  total_profit?: number
  profit_pct?: number
  count?: number
  available_cash?: number
}

interface Position {
  id?: number
  code: string
  name: string
  buy_date: string
  buy_price: number
  shares: number
  strategy?: string
  stop_loss_pct?: number
  status?: string
  sell_date?: string
  sell_price?: number
  sell_reason?: string
  profit_loss?: number
  profit_pct?: number
  notes?: string
}

// ─── Core state ──────────────────────────────────────────────────────────────

const stats = ref<Stats>({})
const positionSummary = ref<PositionSummary>({})
const loading = ref(true)
const positions = ref<Position[]>([])
const positionsLoading = ref(true)

// Equity Curve
const equityCurveData = ref<any>(null)
const equityCurveLoading = ref(true)

// Signals
const signals = ref<any>(null)
const signalsLoading = ref(true)
const signalTab = ref<'buy' | 'sell'>('buy')

// Strategy Comparison
const strategyData = ref<any>(null)
const strategyLoading = ref(true)

// History
const historyData = ref<any>(null)
const historyLoading = ref(true)

// ─── Modal state ─────────────────────────────────────────────────────────────

type ModalType = 'add' | 'buy' | 'sell' | 'delete' | null

const activeModal = ref<ModalType>(null)
const selectedPosition = ref<Position | null>(null)
const modalLoading = ref(false)
const modalError = ref('')
const modalSuccess = ref('')

// Form states
const addForm = ref({
  code: '', name: '', buy_date: '', buy_price: '',
  shares: '', strategy: 's1', stop_loss_pct: '3.0', notes: ''
})

const buyForm = ref({
  code: '', name: '', buy_date: '', buy_price: '',
  shares: '', strategy: 's1', stop_loss_pct: '3.0', reason: '', notes: ''
})

const sellForm = ref({
  sell_price: '', sell_date: '', sell_type: 'full', shares: '',
  reason: '', notes: ''
})

// ─── Signal computed ─────────────────────────────────────────────────────────

const buySignals = computed(() =>
  (signals.value?.signals || []).filter((s: any) => s.buy_signals?.length > 0)
)
const sellSignals = computed(() =>
  (signals.value?.signals || []).filter((s: any) => s.sell_signals?.length > 0)
)
const getSignalList = (signal: any) =>
  signalTab.value === 'buy' ? signal.buy_signals : signal.sell_signals

// ─── Holding positions ────────────────────────────────────────────────────────

const holdingPositions = computed(() =>
  positions.value.filter(p => p.status === 'holding')
)

// ─── API calls ────────────────────────────────────────────────────────────────

const fetchData = async () => {
  try {
    const res = await axios.get('/api/positions')
    const raw = res.data
    positionSummary.value = raw.summary || {}
    positions.value = raw.positions || []
  } catch (e) {
    console.error('Failed to fetch data:', e)
  } finally {
    loading.value = false
    positionsLoading.value = false
  }
}

const fetchEquityCurve = async () => {
  try {
    const res = await axios.get('/api/equity-curve')
    equityCurveData.value = res.data?.dates?.length > 0 ? res.data : null
  } catch (e) {
    equityCurveData.value = null
  } finally {
    equityCurveLoading.value = false
  }
}

const fetchSignals = async () => {
  try {
    signalsLoading.value = true
    const res = await axios.get('/api/signals')
    signals.value = res.data || []
  } catch (e) {
    console.error('Failed to fetch signals:', e)
  } finally {
    signalsLoading.value = false
  }
}

const fetchStrategyComparison = async () => {
  try {
    const res = await axios.get('/api/strategy-comparison')
    strategyData.value = (res.data?.dates?.length > 0 && res.data?.curves)
      ? res.data : null
  } catch (e) {
    strategyData.value = null
  } finally {
    strategyLoading.value = false
  }
}

const fetchHistory = async () => {
  try {
    const res = await axios.get('/api/history')
    historyData.value = res.data
  } catch (e) {
    console.error('Failed to fetch history:', e)
  } finally {
    historyLoading.value = false
  }
}

// ─── Modal helpers ────────────────────────────────────────────────────────────

const today = () => new Date().toISOString().slice(0, 10)

const openAddModal = () => {
  addForm.value = {
    code: '', name: '', buy_date: today(), buy_price: '',
    shares: '', strategy: 's1', stop_loss_pct: '3.0', notes: ''
  }
  modalError.value = ''
  modalSuccess.value = ''
  activeModal.value = 'add'
}

const openBuyModal = (signal?: any) => {
  buyForm.value = {
    code: signal?.code || '',
    name: signal?.name || '',
    buy_date: today(),
    buy_price: signal?.close ? String(signal.close.toFixed(2)) : '',
    shares: '', strategy: 's1', stop_loss_pct: '3.0',
    reason: signal ? `信号买入 ${signal.code}` : '',
    notes: ''
  }
  modalError.value = ''
  modalSuccess.value = ''
  activeModal.value = 'buy'
}

const openSellModal = (pos: Position) => {
  selectedPosition.value = pos
  sellForm.value = {
    sell_price: '', sell_date: today(),
    sell_type: 'full', shares: String(pos.shares),
    reason: '', notes: ''
  }
  modalError.value = ''
  modalSuccess.value = ''
  activeModal.value = 'sell'
}

const openDeleteModal = (pos: Position) => {
  selectedPosition.value = pos
  modalError.value = ''
  activeModal.value = 'delete'
}

const closeModal = () => {
  activeModal.value = null
  selectedPosition.value = null
  modalError.value = ''
  modalSuccess.value = ''
}

// ─── CRUD operations ─────────────────────────────────────────────────────────

const submitAddPosition = async () => {
  modalError.value = ''
  const f = addForm.value
  if (!f.code || !f.name || !f.buy_date || !f.buy_price || !f.shares) {
    modalError.value = '请填写所有必填字段'
    return
  }
  const shares = parseInt(f.shares)
  if (shares % 100 !== 0 || shares <= 0) {
    modalError.value = '购买数量必须是100的整数倍'
    return
  }
  modalLoading.value = true
  try {
    await axios.post('/api/positions', {
      code: f.code.trim(),
      name: f.name.trim(),
      buy_date: f.buy_date,
      buy_price: parseFloat(f.buy_price),
      shares,
      strategy: f.strategy,
      stop_loss_pct: parseFloat(f.stop_loss_pct) / 100,
      notes: f.notes,
      status: 'holding'
    })
    modalSuccess.value = '持仓已添加'
    await fetchData()
    setTimeout(closeModal, 1200)
  } catch (e: any) {
    modalError.value = e?.response?.data?.error || '添加失败，请检查数据'
  } finally {
    modalLoading.value = false
  }
}

const submitBuy = async () => {
  modalError.value = ''
  const f = buyForm.value
  if (!f.code || !f.buy_date || !f.buy_price || !f.shares || !f.reason) {
    modalError.value = '请填写所有必填字段'
    return
  }
  const shares = parseInt(f.shares)
  if (shares % 100 !== 0 || shares <= 0) {
    modalError.value = '买入数量必须是100的整数倍'
    return
  }
  modalLoading.value = true
  try {
    await axios.post('/api/trade/buy', {
      code: f.code.trim(),
      name: f.name.trim(),
      buy_date: f.buy_date,
      buy_price: parseFloat(f.buy_price),
      shares,
      strategy: f.strategy,
      stop_loss_pct: parseFloat(f.stop_loss_pct) / 100,
      reason: f.reason,
      notes: f.notes
    })
    modalSuccess.value = '买入记录已写入'
    await fetchData()
    setTimeout(closeModal, 1200)
  } catch (e: any) {
    modalError.value = e?.response?.data?.error || '买入失败'
  } finally {
    modalLoading.value = false
  }
}

const submitSell = async () => {
  modalError.value = ''
  const f = sellForm.value
  const pos = selectedPosition.value
  if (!pos) return
  if (!f.sell_price || !f.sell_date || !f.reason) {
    modalError.value = '请填写卖出价格、日期和原因'
    return
  }
  let sellShares = f.sell_type === 'full'
    ? pos.shares
    : parseInt(f.shares)
  if (f.sell_type === 'half' && (isNaN(sellShares) || sellShares % 100 !== 0 || sellShares <= 0)) {
    modalError.value = '卖出数量必须是100的整数倍'
    return
  }
  modalLoading.value = true
  try {
    await axios.post('/api/trade/sell', {
      position_id: pos.id,
      code: pos.code,
      sell_date: f.sell_date,
      sell_price: parseFloat(f.sell_price),
      shares: sellShares,
      sell_type: f.sell_type,
      reason: f.reason,
      notes: f.notes
    })
    modalSuccess.value = '卖出记录已写入'
    await fetchData()
    await fetchHistory()
    setTimeout(closeModal, 1200)
  } catch (e: any) {
    modalError.value = e?.response?.data?.error || '卖出失败'
  } finally {
    modalLoading.value = false
  }
}

const submitDelete = async () => {
  const pos = selectedPosition.value
  if (!pos?.id) return
  modalLoading.value = true
  try {
    await axios.delete(`/api/positions/${pos.id}`)
    await fetchData()
    closeModal()
  } catch (e: any) {
    modalError.value = e?.response?.data?.error || '删除失败'
  } finally {
    modalLoading.value = false
  }
}

// ─── Formatters ──────────────────────────────────────────────────────────────

const formatMoney = (value: number | undefined) => {
  if (value === null || value === undefined) return '--'
  const absValue = Math.abs(value)
  if (absValue >= 10000) return (value >= 0 ? '+' : '') + (value / 10000).toFixed(2) + '万'
  return (value >= 0 ? '+' : '') + value.toFixed(2)
}

const formatDate = (dateStr: string) => dateStr || '--'

// ─── Chart: Equity Curve ─────────────────────────────────────────────────────

const equityChartData = computed(() => {
  if (!equityCurveData.value) return null
  const { dates, values, benchmark } = equityCurveData.value
  return {
    labels: dates,
    datasets: [
      {
        label: '策略',
        data: values,
        borderColor: '#3b82f6',
        backgroundColor: 'rgba(59,130,246,0.1)',
        borderWidth: 2, fill: true, tension: 0.4,
        pointRadius: 0, pointHoverRadius: 6,
        pointHoverBackgroundColor: '#3b82f6'
      },
      {
        label: '基准',
        data: benchmark,
        borderColor: '#64748b',
        backgroundColor: 'transparent',
        borderWidth: 1.5, borderDash: [5, 5],
        fill: false, tension: 0.4,
        pointRadius: 0, pointHoverRadius: 4
      }
    ]
  }
})

const equityChartOptions = computed(() => ({
  responsive: true, maintainAspectRatio: false, height: 400,
  interaction: { mode: 'index' as const, intersect: false },
  plugins: {
    legend: {
      display: true, position: 'top' as const,
      labels: { color: '#94a3b8', usePointStyle: true, padding: 20 }
    },
    tooltip: {
      backgroundColor: 'rgba(30,41,59,0.95)',
      titleColor: '#f1f5f9', bodyColor: '#94a3b8',
      borderColor: '#334155', borderWidth: 1, padding: 12,
      callbacks: {
        label: (context: any) => {
          const label = context.dataset.label || ''
          return `${label}: ${context.parsed.y.toLocaleString()}`
        },
        afterLabel: (context: any) => {
          if (context.datasetIndex === 0 && equityCurveData.value?.values?.length > 0) {
            const d = equityCurveData.value
            return `持仓收益: ${(d.values[context.dataIndex] - d.values[0]).toFixed(2)}`
          }
          return ''
        }
      }
    },
    annotation: {
      annotations: equityCurveData.value ? {
        peak: {
          type: 'point' as const,
          xValue: equityCurveData.value.annotations.peak.date,
          yValue: equityCurveData.value.annotations.peak.value,
          backgroundColor: '#10b981', borderColor: '#10b981', radius: 6,
          label: {
            display: true, content: '峰值', color: '#10b981',
            position: 'start', backgroundColor: 'transparent', font: { size: 12 }
          }
        },
        maxdd: {
          type: 'point' as const,
          xValue: equityCurveData.value.annotations.max_drawdown.date,
          yValue: equityCurveData.value.annotations.max_drawdown.value,
          backgroundColor: '#ef4444', borderColor: '#ef4444', radius: 6,
          label: {
            display: true,
            content: `最大回撤${(equityCurveData.value.annotations.max_drawdown.drawdown * 100).toFixed(1)}%`,
            color: '#ef4444', position: 'start',
            backgroundColor: 'transparent', font: { size: 12 }
          }
        }
      } : {}
    }
  },
  scales: {
    x: { grid: { color: 'rgba(51,65,85,0.5)', drawBorder: false }, ticks: { color: '#64748b', maxTicksLimit: 8 } },
    y: { grid: { color: 'rgba(51,65,85,0.5)', drawBorder: false }, ticks: { color: '#64748b', callback: (v: any) => v.toLocaleString() } }
  }
}))

// ─── Chart: Strategy Comparison ──────────────────────────────────────────────

const strategyChartData = computed(() => {
  if (!strategyData.value) return null
  const { dates, initial_value, curves } = strategyData.value
  const colors = ['#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6','#ec4899','#06b6d4','#84cc16']
  const datasets = Object.entries(curves || {}).map(([name, curveData]: [string, any], idx) => ({
    label: name,
    data: (curveData.data || []).map((v: number) => ((v / initial_value) - 1) * 100),
    borderColor: curveData.color || colors[idx % colors.length],
    backgroundColor: 'transparent',
    borderWidth: 2, fill: false, tension: 0.4,
    pointRadius: 0, pointHoverRadius: 4
  }))
  return { labels: dates, datasets }
})

const strategyChartOptions = computed(() => ({
  responsive: true, maintainAspectRatio: false, height: 350,
  interaction: { mode: 'index' as const, intersect: false },
  plugins: {
    legend: {
      display: true, position: 'top' as const, align: 'end' as const,
      labels: { color: '#94a3b8', usePointStyle: true, padding: 15, font: { size: 11 } }
    },
    tooltip: {
      backgroundColor: 'rgba(30,41,59,0.95)', titleColor: '#f1f5f9',
      bodyColor: '#94a3b8', borderColor: '#334155', borderWidth: 1, padding: 12,
      callbacks: {
        label: (context: any) => {
          const label = context.dataset.label || ''
          const value = context.parsed.y
          return `${label}: ${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
        }
      }
    }
  },
  scales: {
    x: { grid: { color: 'rgba(51,65,85,0.5)', drawBorder: false }, ticks: { color: '#64748b', maxTicksLimit: 8 } },
    y: { grid: { color: 'rgba(51,65,85,0.5)', drawBorder: false }, ticks: { color: '#64748b', callback: (v: any) => `${v}%` } }
  }
}))

// ─── History computed ─────────────────────────────────────────────────────────

const historyTrades = computed(() => historyData.value?.history || [])
const historyStats = computed(() => {
  const trades = historyTrades.value as any[]
  const total_trades = trades.length
  const total_profit = trades.reduce((sum: number, t: any) => sum + (t.profit_loss || 0), 0)
  const winning_trades = trades.filter((t: any) => (t.profit_loss || 0) > 0).length
  const win_rate = total_trades > 0 ? winning_trades / total_trades : 0
  const win_total = trades.filter((t: any) => (t.profit_loss || 0) > 0).reduce((sum: number, t: any) => sum + (t.profit_loss || 0), 0)
  const loss_total = Math.abs(trades.filter((t: any) => (t.profit_loss || 0) < 0).reduce((sum: number, t: any) => sum + (t.profit_loss || 0), 0))
  const avg_win = winning_trades > 0 ? win_total / winning_trades : 0
  const avg_loss = (total_trades - winning_trades) > 0 ? loss_total / (total_trades - winning_trades) : 0
  const profit_loss_ratio = avg_loss > 0 ? avg_win / avg_loss : 0
  return { total_trades, total_profit, win_rate, profit_loss_ratio }
})

onMounted(() => {
  fetchData()
  fetchEquityCurve()
  fetchSignals()
  fetchStrategyComparison()
  fetchHistory()
})
</script>

<template>
  <div class="max-w-7xl mx-auto">

    <!-- ═══════════════════════════════════════════════════════
         §1  Stats Cards
    ════════════════════════════════════════════════════════════ -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
      <div class="glass-card p-4 animate-fade-in" style="animation-delay:0.1s">
        <p class="text-slate-400 text-sm mb-1">账户总资产</p>
        <p class="text-2xl font-bold text-slate-100">
          ¥{{ ((positionSummary.total_account_value ?? ((positionSummary.total_value || 0) + (positionSummary.available_cash || 0))) / 10000).toFixed(2) }}万
        </p>
        <p class="text-xs text-slate-500 mt-1">
          持仓 {{ positionSummary.count || 0 }} 只 |
          {{ positionSummary.total_value ? '市值¥' + (positionSummary.total_value / 10000).toFixed(2) + '万' : '空仓' }}
        </p>
      </div>
      <div class="glass-card p-4 animate-fade-in" style="animation-delay:0.2s">
        <p class="text-slate-400 text-sm mb-1">可用资金</p>
        <p class="text-2xl font-bold text-slate-100">
          ¥{{ ((positionSummary.available_cash || 0) / 10000).toFixed(2) }}万
        </p>
        <p class="text-xs text-slate-500 mt-1">
          初始资金 ¥{{ (500000 / 10000).toFixed(0) }}万
        </p>
      </div>
      <div class="glass-card p-4 animate-fade-in" style="animation-delay:0.3s">
        <p class="text-slate-400 text-sm mb-1">总盈亏</p>
        <p class="text-2xl font-bold"
          :class="(positionSummary.total_profit || 0) >= 0 ? 'profit-positive' : 'profit-negative'">
          {{ formatMoney(positionSummary.total_profit) }}
        </p>
        <p class="text-xs mt-1" :class="(positionSummary.history_profit || 0) >= 0 ? 'text-red-400/70' : 'text-green-400/70'">
          已实现 {{ formatMoney(positionSummary.history_profit) }}
          &nbsp;|&nbsp;
          <span :class="(positionSummary.holding_profit || 0) >= 0 ? 'text-red-400/70' : 'text-green-400/70'">
            浮盈 {{ formatMoney(positionSummary.holding_profit) }}
          </span>
        </p>
      </div>
      <div class="glass-card p-4 animate-fade-in" style="animation-delay:0.4s">
        <p class="text-slate-400 text-sm mb-1">盈亏比</p>
        <p class="text-2xl font-bold"
          :class="(historyStats.profit_loss_ratio || 0) >= 1 ? 'profit-positive' : 'profit-negative'">
          {{ (historyStats.profit_loss_ratio || 0).toFixed(2) }}
        </p>
      </div>
    </div>

    <!-- ═══════════════════════════════════════════════════════
         §2  Equity Curve + Signals
    ════════════════════════════════════════════════════════════ -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
      <!-- Equity Curve -->
      <div class="glass-card p-4 animate-fade-in lg:col-span-8" style="animation-delay:0.5s">
        <h3 class="text-lg font-semibold text-slate-100 mb-4">权益曲线</h3>
        <div v-if="equityCurveLoading" class="h-96 flex items-center justify-center text-slate-400">加载中...</div>
        <div v-else-if="!equityChartData" class="h-96 flex items-center justify-center flex-col gap-2 text-slate-400">
          <svg class="w-8 h-8 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
              d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z" />
          </svg>
          <span>暂无权益曲线数据</span>
          <span class="text-xs">开始交易后自动生成</span>
        </div>
        <div v-else class="h-[400px]">
          <Line :data="equityChartData" :options="equityChartOptions" />
        </div>
      </div>

      <!-- Signals Panel -->
      <div class="glass-card p-4 animate-fade-in lg:col-span-4" style="animation-delay:0.5s">
        <div class="flex items-center gap-3 mb-4">
          <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500/20 to-purple-500/20 flex items-center justify-center">
            <svg class="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
            </svg>
          </div>
          <h3 class="text-lg font-semibold text-slate-100">今日信号</h3>
        </div>

        <div class="flex gap-2 mb-4">
          <button @click="signalTab = 'buy'"
            :class="signalTab === 'buy' ? 'bg-red-900/50 text-red-400 border-red-700' : 'bg-slate-800/50 text-slate-400 border-slate-700'"
            class="px-3 py-2 rounded-lg text-sm font-medium transition-colors border flex-1">
            买入 ({{ buySignals.length }})
          </button>
          <button @click="signalTab = 'sell'"
            :class="signalTab === 'sell' ? 'bg-green-900/50 text-green-400 border-green-700' : 'bg-slate-800/50 text-slate-400 border-slate-700'"
            class="px-3 py-2 rounded-lg text-sm font-medium transition-colors border flex-1">
            卖出 ({{ sellSignals.length }})
          </button>
        </div>

        <div class="space-y-2 overflow-y-auto max-h-[340px]">
          <div v-for="signal in (signalTab === 'buy' ? buySignals : sellSignals)" :key="signal.code"
            class="p-3 rounded-lg border transition-colors"
            :class="signalTab === 'buy' ? 'bg-red-500/10 border-red-500/20' : 'bg-green-500/10 border-green-500/20'">
            <div class="flex items-center justify-between mb-2">
              <div>
                <span class="text-slate-100 font-medium">{{ signal.name }}</span>
                <span class="text-slate-500 text-xs ml-2">{{ signal.code }}</span>
              </div>
              <span class="text-slate-100 font-semibold">¥{{ (signal.close || 0).toFixed(2) }}</span>
            </div>
            <div class="flex flex-wrap gap-2 mb-2">
              <span v-for="sig in getSignalList(signal)" :key="sig.strategy"
                class="px-2 py-0.5 rounded text-xs font-medium"
                :class="signalTab === 'buy' ? 'bg-red-500/20 text-red-300' : 'bg-green-500/20 text-green-300'">
                {{ sig.strategy }} {{ sig.score.toFixed(1) }}分
              </span>
            </div>
            <!-- Quick action button on signal row -->
            <button v-if="signalTab === 'buy'"
              @click="openBuyModal(signal)"
              class="w-full mt-1 py-1 rounded text-xs font-semibold bg-red-600/30 hover:bg-red-600/60 text-red-300 transition-colors border border-red-600/40">
              📈 快速买入
            </button>
          </div>
        </div>

        <div v-if="(signalTab === 'buy' ? buySignals : sellSignals).length === 0"
          class="text-center py-8 text-slate-400">
          暂无{{ signalTab === 'buy' ? '买入' : '卖出' }}信号
        </div>
      </div>
    </div>

    <!-- ═══════════════════════════════════════════════════════
         §3  Strategy Comparison
    ════════════════════════════════════════════════════════════ -->
    <div class="glass-card p-4 mb-6 mt-6 animate-fade-in" style="animation-delay:0.6s">
      <h3 class="text-lg font-semibold text-slate-100 mb-4">策略对比</h3>
      <div v-if="strategyLoading" class="h-80 flex items-center justify-center text-slate-400">加载中...</div>
      <div v-else-if="!strategyChartData" class="h-80 flex items-center justify-center flex-col gap-2 text-slate-400">
        <svg class="w-8 h-8 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
            d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
        </svg>
        <span>暂无策略对比数据</span>
        <span class="text-xs">运行策略后自动生成</span>
      </div>
      <div v-else class="h-[350px]">
        <Line :data="strategyChartData" :options="strategyChartOptions" />
      </div>
    </div>

    <!-- ═══════════════════════════════════════════════════════
         §4  持仓管理（图形化操作）
    ════════════════════════════════════════════════════════════ -->
    <div class="glass-card p-4 mb-6 animate-fade-in" style="animation-delay:0.65s">
      <div class="flex items-center justify-between mb-5">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-emerald-500/20 to-blue-500/20 flex items-center justify-center">
            <svg class="w-5 h-5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
          </div>
          <div>
            <h3 class="text-lg font-semibold text-slate-100">持仓管理</h3>
            <p class="text-xs text-slate-500">{{ holdingPositions.length }} 只持仓中</p>
          </div>
        </div>
        <div class="flex gap-2">
          <button @click="openBuyModal()"
            class="action-btn-buy px-4 py-2 rounded-lg text-sm font-semibold flex items-center gap-2 transition-all">
            <span class="text-base">📈</span> 买入
          </button>
          <button @click="openAddModal()"
            class="action-btn-add px-4 py-2 rounded-lg text-sm font-semibold flex items-center gap-2 transition-all">
            <span class="text-base">＋</span> 添加持仓
          </button>
        </div>
      </div>

      <!-- Positions Table -->
      <div v-if="positionsLoading" class="text-center py-10 text-slate-400">加载中...</div>
      <div v-else-if="holdingPositions.length === 0"
        class="text-center py-12 flex flex-col items-center gap-3 text-slate-400">
        <svg class="w-12 h-12 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
            d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
        </svg>
        <p class="font-medium">当前无持仓</p>
        <p class="text-sm text-slate-500">点击右上角「添加持仓」或「买入」记录新的持仓</p>
        <button @click="openAddModal()"
          class="mt-2 px-5 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium text-white transition-colors">
          ＋ 添加第一笔持仓
        </button>
      </div>
      <div v-else class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-slate-700">
              <th class="text-left py-3 px-2 text-slate-400 font-medium">股票</th>
              <th class="text-left py-3 px-2 text-slate-400 font-medium">买入日期</th>
              <th class="text-right py-3 px-2 text-slate-400 font-medium">买入价</th>
              <th class="text-right py-3 px-2 text-slate-400 font-medium">持仓量</th>
              <th class="text-right py-3 px-2 text-slate-400 font-medium">市值</th>
              <th class="text-right py-3 px-2 text-slate-400 font-medium">盈亏</th>
              <th class="text-center py-3 px-2 text-slate-400 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="pos in holdingPositions" :key="pos.id"
              class="border-b border-slate-800/60 hover:bg-slate-800/30 transition-colors">
              <td class="py-3 px-2">
                <div class="text-slate-100 font-medium">{{ pos.name }}</div>
                <div class="text-slate-500 text-xs">{{ pos.code }}</div>
                <div v-if="pos.strategy" class="mt-0.5">
                  <span class="px-1.5 py-0.5 bg-blue-500/10 text-blue-400 text-xs rounded">{{ pos.strategy }}</span>
                </div>
              </td>
              <td class="py-3 px-2 text-slate-300">{{ formatDate(pos.buy_date) }}</td>
              <td class="py-3 px-2 text-right text-slate-300">{{ (pos.buy_price || 0).toFixed(3) }}</td>
              <td class="py-3 px-2 text-right text-slate-300">{{ (pos.shares || 0).toLocaleString() }}</td>
              <td class="py-3 px-2 text-right text-slate-300">
                {{ pos.buy_price && pos.shares ? '¥' + (pos.buy_price * pos.shares).toLocaleString('zh', { maximumFractionDigits: 0 }) : '--' }}
              </td>
              <td class="py-3 px-2 text-right font-medium"
                :class="(pos.profit_loss || 0) >= 0 ? 'text-red-400' : 'text-green-400'">
                {{ pos.profit_loss !== undefined && pos.profit_loss !== null
                    ? ((pos.profit_loss >= 0 ? '+' : '') + pos.profit_loss.toFixed(2))
                    : '--' }}
              </td>
              <td class="py-3 px-2">
                <div class="flex items-center justify-center gap-1">
                  <button @click="openSellModal(pos)"
                    class="px-2.5 py-1 bg-green-600/20 hover:bg-green-600/40 text-green-400 border border-green-600/30 rounded-md text-xs font-medium transition-colors">
                    卖出
                  </button>
                  <button @click="openDeleteModal(pos)"
                    class="px-2.5 py-1 bg-slate-700/40 hover:bg-red-600/30 text-slate-400 hover:text-red-400 border border-slate-600/40 hover:border-red-600/40 rounded-md text-xs font-medium transition-colors">
                    删除
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ═══════════════════════════════════════════════════════
         §5  History Trades
    ════════════════════════════════════════════════════════════ -->
    <div class="glass-card p-4 mb-6 animate-fade-in" style="animation-delay:0.7s">
      <div class="flex items-center justify-between mb-4">
        <h3 class="text-lg font-semibold text-slate-100">历史交易</h3>
        <router-link to="/history-analysis"
          class="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition-colors">
          查看分析
        </router-link>
      </div>

      <div v-if="historyData" class="grid grid-cols-3 gap-4 mb-4">
        <div class="bg-slate-800/50 rounded-lg p-3 text-center">
          <p class="text-slate-400 text-xs mb-1">总交易次数</p>
          <p class="text-xl font-bold text-slate-100">{{ historyStats.total_trades }}</p>
        </div>
        <div class="bg-slate-800/50 rounded-lg p-3 text-center">
          <p class="text-slate-400 text-xs mb-1">总盈亏</p>
          <p class="text-xl font-bold"
            :class="historyStats.total_profit >= 0 ? 'text-red-400' : 'text-green-400'">
            {{ formatMoney(historyStats.total_profit) }}
          </p>
        </div>
        <div class="bg-slate-800/50 rounded-lg p-3 text-center">
          <p class="text-slate-400 text-xs mb-1">胜率</p>
          <p class="text-xl font-bold text-slate-100">
            {{ ((historyStats.win_rate || 0) * 100).toFixed(1) }}%
          </p>
        </div>
      </div>

      <div v-if="historyLoading" class="text-center py-8 text-slate-400">加载中...</div>
      <div v-else-if="!historyData || historyTrades.length === 0" class="text-center py-8 text-slate-400">
        暂无历史交易
      </div>
      <div v-else class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-slate-700">
              <th class="text-left py-3 px-2 text-slate-400 font-medium">股票</th>
              <th class="text-left py-3 px-2 text-slate-400 font-medium">买入日期</th>
              <th class="text-right py-3 px-2 text-slate-400 font-medium">买入价</th>
              <th class="text-left py-3 px-2 text-slate-400 font-medium">卖出日期</th>
              <th class="text-right py-3 px-2 text-slate-400 font-medium">卖出价</th>
              <th class="text-left py-3 px-2 text-slate-400 font-medium">卖出原因</th>
              <th class="text-right py-3 px-2 text-slate-400 font-medium">盈亏</th>
              <th class="text-right py-3 px-2 text-slate-400 font-medium">盈亏%</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="trade in historyTrades" :key="trade.code + trade.buy_date"
              class="border-b border-slate-800 hover:bg-slate-800/30">
              <td class="py-3 px-2">
                <div class="text-slate-100">{{ trade.name }}</div>
                <div class="text-slate-400 text-xs">{{ trade.code }}</div>
                <div class="flex gap-2 mt-1 text-xs">
                  <span class="text-red-400">买: {{ trade.buy_signal_type || '趋势择时' }}</span>
                  <span class="text-green-400">卖: {{ trade.sell_signal_type || '信号卖出' }}</span>
                </div>
              </td>
              <td class="py-3 px-2 text-slate-300">{{ formatDate(trade.buy_date) }}</td>
              <td class="py-3 px-2 text-right text-slate-300">{{ (trade.buy_price || 0).toFixed(2) }}</td>
              <td class="py-3 px-2 text-slate-300">{{ formatDate(trade.sell_date) }}</td>
              <td class="py-3 px-2 text-right text-slate-300">{{ (trade.sell_price || 0).toFixed(2) }}</td>
              <td class="py-3 px-2 text-slate-400 text-xs">{{ trade.sell_reason }}</td>
              <td class="py-3 px-2 text-right font-medium"
                :class="(trade.profit_loss || 0) >= 0 ? 'text-red-400' : 'text-green-400'">
                {{ (trade.profit_loss || 0) >= 0 ? '+' : '' }}{{ (trade.profit_loss || 0).toFixed(2) }}
              </td>
              <td class="py-3 px-2 text-right font-medium"
                :class="(trade.profit_pct || 0) >= 0 ? 'text-red-400' : 'text-green-400'">
                {{ (trade.profit_pct || 0) >= 0 ? '+' : '' }}{{ (trade.profit_pct || 0).toFixed(2) }}%
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ═══════════════════════════════════════════════════════
         §6  PositionsView (embedded component, optional)
    ════════════════════════════════════════════════════════════ -->
    <!-- Uncomment if PositionsView provides additional detail panels -->
    <!-- <PositionsView embedded class="mb-6 animate-fade-in" style="animation-delay:0.75s" /> -->


    <!-- ═══════════════════════════════════════════════════════
         MODALS
    ════════════════════════════════════════════════════════════ -->

    <!-- Backdrop -->
    <Transition name="modal-bg">
      <div v-if="activeModal"
        class="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4"
        @click.self="closeModal">

        <!-- ── ADD POSITION modal ─────────────────────────── -->
        <Transition name="modal-slide">
          <div v-if="activeModal === 'add'" class="modal-card w-full max-w-md">
            <div class="modal-header">
              <div class="flex items-center gap-3">
                <div class="w-9 h-9 rounded-xl bg-blue-500/20 flex items-center justify-center">
                  <span class="text-lg">📋</span>
                </div>
                <div>
                  <h2 class="text-slate-100 font-semibold text-lg">添加持仓</h2>
                  <p class="text-slate-500 text-xs">手动录入持仓信息</p>
                </div>
              </div>
              <button @click="closeModal" class="modal-close-btn">✕</button>
            </div>

            <div class="modal-body space-y-4">
              <div class="grid grid-cols-2 gap-3">
                <div class="form-group">
                  <label class="form-label">股票代码 <span class="text-red-400">*</span></label>
                  <input v-model="addForm.code" class="form-input" placeholder="如 600519" maxlength="6" />
                </div>
                <div class="form-group">
                  <label class="form-label">股票名称 <span class="text-red-400">*</span></label>
                  <input v-model="addForm.name" class="form-input" placeholder="如 贵州茅台" />
                </div>
              </div>
              <div class="form-group">
                <label class="form-label">购买时间 <span class="text-red-400">*</span></label>
                <input v-model="addForm.buy_date" type="date" class="form-input" />
              </div>
              <div class="grid grid-cols-2 gap-3">
                <div class="form-group">
                  <label class="form-label">购买价格（元）<span class="text-red-400">*</span></label>
                  <input v-model="addForm.buy_price" type="number" step="0.001" class="form-input" placeholder="0.000" />
                </div>
                <div class="form-group">
                  <label class="form-label">购买数量（股）<span class="text-red-400">*</span></label>
                  <input v-model="addForm.shares" type="number" step="100" class="form-input" placeholder="100" />
                </div>
              </div>
              <div class="grid grid-cols-2 gap-3">
                <div class="form-group">
                  <label class="form-label">策略标签</label>
                  <select v-model="addForm.strategy" class="form-input">
                    <option value="s1">S1</option>
                    <option value="ma">MA趋势</option>
                    <option value="breakout">突破</option>
                    <option value="manual">手动</option>
                  </select>
                </div>
                <div class="form-group">
                  <label class="form-label">止损阈值（%）</label>
                  <input v-model="addForm.stop_loss_pct" type="number" step="0.5" class="form-input" placeholder="3.0" />
                </div>
              </div>
              <div class="form-group">
                <label class="form-label">备注</label>
                <input v-model="addForm.notes" class="form-input" placeholder="可选" />
              </div>

              <!-- Cost preview -->
              <div v-if="addForm.buy_price && addForm.shares" class="cost-preview">
                <div class="flex justify-between text-xs">
                  <span class="text-slate-400">成本估算</span>
                  <span class="text-slate-200 font-medium">
                    ¥{{ (parseFloat(addForm.buy_price || '0') * parseInt(addForm.shares || '0')).toLocaleString('zh', { maximumFractionDigits: 2 }) }}
                  </span>
                </div>
                <div class="flex justify-between text-xs mt-1">
                  <span class="text-slate-400">止损价参考</span>
                  <span class="text-orange-400 font-medium">
                    ¥{{ (parseFloat(addForm.buy_price || '0') * (1 - parseFloat(addForm.stop_loss_pct || '3') / 100)).toFixed(3) }}
                  </span>
                </div>
              </div>

              <div v-if="modalError" class="alert-error">{{ modalError }}</div>
              <div v-if="modalSuccess" class="alert-success">✓ {{ modalSuccess }}</div>
            </div>

            <div class="modal-footer">
              <button @click="closeModal" class="btn-secondary">取消</button>
              <button @click="submitAddPosition" :disabled="modalLoading" class="btn-primary">
                <span v-if="modalLoading" class="loading-dot">处理中</span>
                <span v-else>确认添加</span>
              </button>
            </div>
          </div>
        </Transition>

        <!-- ── BUY modal ──────────────────────────────────── -->
        <Transition name="modal-slide">
          <div v-if="activeModal === 'buy'" class="modal-card w-full max-w-md">
            <div class="modal-header">
              <div class="flex items-center gap-3">
                <div class="w-9 h-9 rounded-xl bg-red-500/20 flex items-center justify-center">
                  <span class="text-lg">📈</span>
                </div>
                <div>
                  <h2 class="text-slate-100 font-semibold text-lg">记录买入</h2>
                  <p class="text-slate-500 text-xs">写入买入记录并更新持仓</p>
                </div>
              </div>
              <button @click="closeModal" class="modal-close-btn">✕</button>
            </div>

            <div class="modal-body space-y-4">
              <div class="grid grid-cols-2 gap-3">
                <div class="form-group">
                  <label class="form-label">股票代码 <span class="text-red-400">*</span></label>
                  <input v-model="buyForm.code" class="form-input" placeholder="600519" maxlength="6" />
                </div>
                <div class="form-group">
                  <label class="form-label">股票名称</label>
                  <input v-model="buyForm.name" class="form-input" placeholder="可选，自动查询" />
                </div>
              </div>
              <div class="form-group">
                <label class="form-label">买入日期 <span class="text-red-400">*</span></label>
                <input v-model="buyForm.buy_date" type="date" class="form-input" />
              </div>
              <div class="grid grid-cols-2 gap-3">
                <div class="form-group">
                  <label class="form-label">买入价格（元）<span class="text-red-400">*</span></label>
                  <input v-model="buyForm.buy_price" type="number" step="0.001" class="form-input" placeholder="0.000" />
                </div>
                <div class="form-group">
                  <label class="form-label">买入手数（股）<span class="text-red-400">*</span></label>
                  <input v-model="buyForm.shares" type="number" step="100" class="form-input" placeholder="100" />
                </div>
              </div>
              <div class="grid grid-cols-2 gap-3">
                <div class="form-group">
                  <label class="form-label">策略</label>
                  <select v-model="buyForm.strategy" class="form-input">
                    <option value="s1">S1</option>
                    <option value="ma">MA趋势</option>
                    <option value="breakout">突破</option>
                    <option value="manual">手动</option>
                  </select>
                </div>
                <div class="form-group">
                  <label class="form-label">止损阈值（%）</label>
                  <input v-model="buyForm.stop_loss_pct" type="number" step="0.5" class="form-input" placeholder="3.0" />
                </div>
              </div>
              <div class="form-group">
                <label class="form-label">买入原因 <span class="text-red-400">*</span></label>
                <input v-model="buyForm.reason" class="form-input" placeholder="如: MA金叉+量能放大" />
              </div>
              <div class="form-group">
                <label class="form-label">备注</label>
                <input v-model="buyForm.notes" class="form-input" placeholder="可选" />
              </div>

              <!-- Commission preview -->
              <div v-if="buyForm.buy_price && buyForm.shares" class="cost-preview">
                <div class="flex justify-between text-xs">
                  <span class="text-slate-400">买入金额</span>
                  <span class="text-slate-200 font-medium">
                    ¥{{ (parseFloat(buyForm.buy_price || '0') * parseInt(buyForm.shares || '0')).toLocaleString('zh', { maximumFractionDigits: 2 }) }}
                  </span>
                </div>
                <div class="flex justify-between text-xs mt-1">
                  <span class="text-slate-400">佣金（0.025%）</span>
                  <span class="text-slate-400">
                    ¥{{ Math.max(5, parseFloat(buyForm.buy_price || '0') * parseInt(buyForm.shares || '0') * 0.00025).toFixed(2) }}
                  </span>
                </div>
              </div>

              <div v-if="modalError" class="alert-error">{{ modalError }}</div>
              <div v-if="modalSuccess" class="alert-success">✓ {{ modalSuccess }}</div>
            </div>

            <div class="modal-footer">
              <button @click="closeModal" class="btn-secondary">取消</button>
              <button @click="submitBuy" :disabled="modalLoading" class="btn-buy">
                <span v-if="modalLoading" class="loading-dot">处理中</span>
                <span v-else>确认买入</span>
              </button>
            </div>
          </div>
        </Transition>

        <!-- ── SELL modal ─────────────────────────────────── -->
        <Transition name="modal-slide">
          <div v-if="activeModal === 'sell' && selectedPosition" class="modal-card w-full max-w-md">
            <div class="modal-header">
              <div class="flex items-center gap-3">
                <div class="w-9 h-9 rounded-xl bg-green-500/20 flex items-center justify-center">
                  <span class="text-lg">📉</span>
                </div>
                <div>
                  <h2 class="text-slate-100 font-semibold text-lg">记录卖出</h2>
                  <p class="text-slate-500 text-xs">{{ selectedPosition.name }}（{{ selectedPosition.code }}）</p>
                </div>
              </div>
              <button @click="closeModal" class="modal-close-btn">✕</button>
            </div>

            <div class="modal-body space-y-4">
              <!-- Position info strip -->
              <div class="bg-slate-800/60 rounded-lg p-3 border border-slate-700/50 grid grid-cols-3 gap-2 text-xs">
                <div>
                  <p class="text-slate-500 mb-0.5">买入价</p>
                  <p class="text-slate-200 font-medium">¥{{ selectedPosition.buy_price.toFixed(3) }}</p>
                </div>
                <div>
                  <p class="text-slate-500 mb-0.5">持仓手数</p>
                  <p class="text-slate-200 font-medium">{{ selectedPosition.shares.toLocaleString() }} 股</p>
                </div>
                <div>
                  <p class="text-slate-500 mb-0.5">买入日期</p>
                  <p class="text-slate-200 font-medium">{{ formatDate(selectedPosition.buy_date) }}</p>
                </div>
              </div>

              <!-- Sell type -->
              <div class="form-group">
                <label class="form-label">卖出类型 <span class="text-red-400">*</span></label>
                <div class="flex gap-2">
                  <button @click="sellForm.sell_type = 'full'; sellForm.shares = String(selectedPosition!.shares)"
                    :class="sellForm.sell_type === 'full' ? 'bg-green-600/30 text-green-300 border-green-500/60' : 'bg-slate-800/50 text-slate-400 border-slate-600/40'"
                    class="flex-1 py-2 rounded-lg text-sm font-medium border transition-colors">
                    全仓清出（{{ selectedPosition.shares }} 股）
                  </button>
                  <button @click="sellForm.sell_type = 'half'; sellForm.shares = String(Math.max(100, Math.floor(selectedPosition!.shares / 2 / 100) * 100))"
                    :class="sellForm.sell_type === 'half' ? 'bg-yellow-600/30 text-yellow-300 border-yellow-500/60' : 'bg-slate-800/50 text-slate-400 border-slate-600/40'"
                    class="flex-1 py-2 rounded-lg text-sm font-medium border transition-colors">
                    半仓减持
                  </button>
                </div>
              </div>

              <div v-if="sellForm.sell_type === 'half'" class="form-group">
                <label class="form-label">卖出数量（股）<span class="text-red-400">*</span></label>
                <input v-model="sellForm.shares" type="number" step="100" class="form-input"
                  :placeholder="`最多 ${selectedPosition.shares}`" />
              </div>

              <div class="form-group">
                <label class="form-label">卖出日期 <span class="text-red-400">*</span></label>
                <input v-model="sellForm.sell_date" type="date" class="form-input" />
              </div>
              <div class="form-group">
                <label class="form-label">卖出价格（元）<span class="text-red-400">*</span></label>
                <input v-model="sellForm.sell_price" type="number" step="0.001" class="form-input" placeholder="0.000" />
              </div>
              <div class="form-group">
                <label class="form-label">卖出原因 <span class="text-red-400">*</span></label>
                <input v-model="sellForm.reason" class="form-input" placeholder="如: S1满仓信号 / 止损" />
              </div>
              <div class="form-group">
                <label class="form-label">备注</label>
                <input v-model="sellForm.notes" class="form-input" placeholder="可选" />
              </div>

              <!-- PnL preview -->
              <div v-if="sellForm.sell_price && sellForm.shares" class="cost-preview">
                <div class="flex justify-between text-xs">
                  <span class="text-slate-400">卖出金额（税前）</span>
                  <span class="text-slate-200 font-medium">
                    ¥{{ (parseFloat(sellForm.sell_price || '0') * parseInt(sellForm.shares || '0')).toLocaleString('zh', { maximumFractionDigits: 2 }) }}
                  </span>
                </div>
                <div class="flex justify-between text-xs mt-1">
                  <span class="text-slate-400">预估盈亏</span>
                  <span :class="(parseFloat(sellForm.sell_price || '0') - (selectedPosition?.buy_price || 0)) >= 0 ? 'text-red-400' : 'text-green-400'"
                    class="font-medium">
                    {{
                      ((parseFloat(sellForm.sell_price || '0') - (selectedPosition?.buy_price || 0))
                        * parseInt(sellForm.shares || '0') >= 0 ? '+' : '')
                    }}¥{{
                      ((parseFloat(sellForm.sell_price || '0') - (selectedPosition?.buy_price || 0))
                        * parseInt(sellForm.shares || '0') * 0.99924).toFixed(2)
                    }}
                  </span>
                </div>
              </div>

              <div v-if="modalError" class="alert-error">{{ modalError }}</div>
              <div v-if="modalSuccess" class="alert-success">✓ {{ modalSuccess }}</div>
            </div>

            <div class="modal-footer">
              <button @click="closeModal" class="btn-secondary">取消</button>
              <button @click="submitSell" :disabled="modalLoading" class="btn-sell">
                <span v-if="modalLoading" class="loading-dot">处理中</span>
                <span v-else>确认卖出</span>
              </button>
            </div>
          </div>
        </Transition>

        <!-- ── DELETE modal ───────────────────────────────── -->
        <Transition name="modal-slide">
          <div v-if="activeModal === 'delete' && selectedPosition" class="modal-card w-full max-w-sm">
            <div class="modal-header">
              <div class="flex items-center gap-3">
                <div class="w-9 h-9 rounded-xl bg-red-500/20 flex items-center justify-center">
                  <span class="text-lg">🗑️</span>
                </div>
                <h2 class="text-slate-100 font-semibold text-lg">删除持仓</h2>
              </div>
              <button @click="closeModal" class="modal-close-btn">✕</button>
            </div>

            <div class="modal-body">
              <div class="text-center py-4">
                <div class="w-16 h-16 mx-auto rounded-full bg-red-500/10 border border-red-500/20 flex items-center justify-center mb-4">
                  <svg class="w-8 h-8 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
                      d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                </div>
                <p class="text-slate-200 font-medium mb-1">确认删除此持仓？</p>
                <p class="text-slate-400 text-sm mb-1">
                  <span class="text-slate-200">{{ selectedPosition.name }}</span>（{{ selectedPosition.code }}）
                </p>
                <p class="text-slate-400 text-sm">
                  {{ selectedPosition.shares }} 股 @ ¥{{ selectedPosition.buy_price.toFixed(3) }}
                </p>
                <p class="text-red-400/80 text-xs mt-3">⚠️ 此操作不可撤销，仅删除数据库记录</p>
              </div>
              <div v-if="modalError" class="alert-error">{{ modalError }}</div>
            </div>

            <div class="modal-footer">
              <button @click="closeModal" class="btn-secondary flex-1">取消</button>
              <button @click="submitDelete" :disabled="modalLoading"
                class="flex-1 px-4 py-2.5 bg-red-600 hover:bg-red-700 text-white rounded-lg font-semibold text-sm transition-colors disabled:opacity-50">
                <span v-if="modalLoading">删除中...</span>
                <span v-else>确认删除</span>
              </button>
            </div>
          </div>
        </Transition>

      </div>
    </Transition>

  </div>
</template>

<style scoped>
/* ─── Base Cards ─────────────────────────────────────────────── */
.glass-card {
  background: rgba(30, 41, 59, 0.8);
  backdrop-filter: blur(10px);
  border: 1px solid #334155;
  border-radius: 12px;
}

/* ─── Profit colors ──────────────────────────────────────────── */
.profit-positive { color: #ef4444; }
.profit-negative { color: #10b981; }

/* ─── Page animation ─────────────────────────────────────────── */
.animate-fade-in {
  animation: fadeIn 0.5s ease-out both;
}
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(10px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* ─── Action Buttons ─────────────────────────────────────────── */
.action-btn-buy {
  background: rgba(34, 197, 94, 0.12);
  border: 1px solid rgba(34, 197, 94, 0.3);
  color: #86efac;
}
.action-btn-buy:hover {
  background: rgba(34, 197, 94, 0.25);
  border-color: rgba(34, 197, 94, 0.6);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(34, 197, 94, 0.15);
}
.action-btn-add {
  background: rgba(59, 130, 246, 0.12);
  border: 1px solid rgba(59, 130, 246, 0.3);
  color: #93c5fd;
}
.action-btn-add:hover {
  background: rgba(59, 130, 246, 0.25);
  border-color: rgba(59, 130, 246, 0.6);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(59, 130, 246, 0.15);
}

/* ─── Modal Transitions ──────────────────────────────────────── */
.modal-bg-enter-active, .modal-bg-leave-active { transition: opacity 0.2s ease; }
.modal-bg-enter-from, .modal-bg-leave-to { opacity: 0; }

.modal-slide-enter-active { transition: all 0.25s cubic-bezier(0.34, 1.56, 0.64, 1); }
.modal-slide-leave-active { transition: all 0.18s ease-in; }
.modal-slide-enter-from { opacity: 0; transform: scale(0.94) translateY(16px); }
.modal-slide-leave-to   { opacity: 0; transform: scale(0.96) translateY(8px); }

/* ─── Modal Card ─────────────────────────────────────────────── */
.modal-card {
  background: rgba(15, 23, 42, 0.97);
  border: 1px solid #334155;
  border-radius: 16px;
  box-shadow: 0 25px 60px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(255,255,255,0.04);
  overflow: hidden;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
}
.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 20px 16px;
  border-bottom: 1px solid #1e293b;
  flex-shrink: 0;
}
.modal-body {
  padding: 20px;
  overflow-y: auto;
  flex: 1;
}
.modal-footer {
  display: flex;
  gap: 10px;
  padding: 16px 20px;
  border-top: 1px solid #1e293b;
  flex-shrink: 0;
}
.modal-close-btn {
  width: 32px; height: 32px;
  border-radius: 8px;
  background: rgba(51,65,85,0.5);
  color: #94a3b8;
  font-size: 12px;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.15s;
}
.modal-close-btn:hover { background: rgba(239,68,68,0.2); color: #f87171; }

/* ─── Form Elements ──────────────────────────────────────────── */
.form-group { display: flex; flex-direction: column; gap: 5px; }
.form-label { font-size: 12px; color: #64748b; font-weight: 500; }
.form-input {
  background: rgba(30, 41, 59, 0.8);
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 8px 12px;
  color: #e2e8f0;
  font-size: 14px;
  transition: border-color 0.15s, box-shadow 0.15s;
  width: 100%;
}
.form-input:focus {
  outline: none;
  border-color: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.12);
}
.form-input::placeholder { color: #475569; }

/* ─── Cost preview ───────────────────────────────────────────── */
.cost-preview {
  background: rgba(30, 41, 59, 0.6);
  border: 1px solid #1e293b;
  border-radius: 8px;
  padding: 10px 12px;
}

/* ─── Alerts ─────────────────────────────────────────────────── */
.alert-error {
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: 8px;
  padding: 10px 14px;
  color: #fca5a5;
  font-size: 13px;
}
.alert-success {
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(16, 185, 129, 0.3);
  border-radius: 8px;
  padding: 10px 14px;
  color: #6ee7b7;
  font-size: 13px;
}

/* ─── Buttons ────────────────────────────────────────────────── */
.btn-primary, .btn-buy, .btn-sell, .btn-secondary {
  flex: 1;
  padding: 10px 18px;
  border-radius: 8px;
  font-weight: 600;
  font-size: 14px;
  transition: all 0.15s;
  cursor: pointer;
}
.btn-primary { background: #3b82f6; color: #fff; }
.btn-primary:hover:not(:disabled) { background: #2563eb; box-shadow: 0 4px 12px rgba(59,130,246,0.4); }
.btn-buy { background: linear-gradient(135deg, #dc2626, #b91c1c); color: #fff; }
.btn-buy:hover:not(:disabled) { box-shadow: 0 4px 12px rgba(220,38,38,0.4); }
.btn-sell { background: linear-gradient(135deg, #059669, #047857); color: #fff; }
.btn-sell:hover:not(:disabled) { box-shadow: 0 4px 12px rgba(5,150,105,0.4); }
.btn-secondary {
  background: rgba(51, 65, 85, 0.6);
  color: #94a3b8;
  border: 1px solid #334155;
}
.btn-secondary:hover { background: rgba(51,65,85,0.9); color: #e2e8f0; }
button:disabled { opacity: 0.5; cursor: not-allowed; }

/* ─── Loading dot ────────────────────────────────────────────── */
.loading-dot::after {
  content: '...';
  animation: dots 1.2s infinite;
}
@keyframes dots {
  0%, 20% { content: '.'; }
  40% { content: '..'; }
  60%, 100% { content: '...'; }
}
</style>

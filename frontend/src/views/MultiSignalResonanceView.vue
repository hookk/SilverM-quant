<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import axios from 'axios'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
  type Plugin,
} from 'chart.js'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend, Filler)

// ── 自定义插件：在当前选中日期处绘制竖线标记 ──────────────────────────
const selectedDateLinePlugin: Plugin<'line'> = {
  id: 'selectedDateLine',
  afterDraw(chart) {
    const meta = chart.data.datasets[0] ? chart.getDatasetMeta(0) : null
    if (!meta || !meta.data.length) return
    const dateLabel = (chart.options as any)._selectedDate as string
    if (!dateLabel) return
    const labels = chart.data.labels as string[]
    const idx = labels.findIndex(l => String(l).startsWith(dateLabel))
    if (idx < 0) return
    const point = meta.data[idx]
    if (!point) return
    const { ctx, chartArea: { top, bottom } } = chart
    ctx.save()
    ctx.beginPath()
    ctx.setLineDash([4, 3])
    ctx.strokeStyle = 'rgba(251,191,36,0.7)'
    ctx.lineWidth = 1.5
    ctx.moveTo(point.x, top)
    ctx.lineTo(point.x, bottom)
    ctx.stroke()
    // 顶部日期标签
    ctx.setLineDash([])
    ctx.fillStyle = 'rgba(251,191,36,0.85)'
    ctx.font = '10px monospace'
    ctx.textAlign = 'center'
    ctx.fillText(dateLabel, point.x, top - 4)
    ctx.restore()
  },
}
ChartJS.register(selectedDateLinePlugin)

interface StockSignal {
  code: string
  name: string
  signal_count: number
  signals: string[]
  close: number
  change_pct: number
}

interface ResonanceData {
  date: string
  count: number
  stocks: StockSignal[]
}

interface TrendData {
  dates: string[]
  signal_data: Record<string, number[]>
  total_counts: number[]
}

const loading = ref(true)
const selectedDate = ref('')
const dateOptions = ref<{ value: string; label: string }[]>([])
const resonanceData = ref<ResonanceData | null>(null)
const error = ref('')
const trendData = ref<TrendData | null>(null)
const trendLoading = ref(false)
const trendDays = ref(60)

const SIGNAL_COLORS: Record<string, string> = {
  B1: '#60a5fa',
  B2: '#34d399',
  BLK: '#fb923c',
  DL: '#a78bfa',
  DZ30: '#22d3ee',
  SCB: '#f472b6',
  BLKB2: '#a3e635',
}

const chartData = computed(() => {
  if (!trendData.value) return { labels: [], datasets: [] }

  const { dates, signal_data, total_counts } = trendData.value

  const signalDatasets = Object.entries(signal_data || {}).map(([signal, data]) => ({
    label: signal,
    data,
    borderColor: SIGNAL_COLORS[signal] || '#94a3b8',
    backgroundColor: (SIGNAL_COLORS[signal] || '#94a3b8') + '18',
    borderWidth: 1.5,
    pointRadius: 0,            // 默认不显示数据点，hover 时显示
    pointHoverRadius: 4,
    pointHoverBackgroundColor: SIGNAL_COLORS[signal] || '#94a3b8',
    tension: 0.35,
    yAxisID: 'y1',
    fill: false,
  }))

  return {
    labels: dates || [],
    datasets: [
      {
        label: '总共振数',
        data: total_counts || [],
        borderColor: '#e2e8f0',
        backgroundColor: 'rgba(226,232,240,0.08)',
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: '#f8fafc',
        tension: 0.35,
        yAxisID: 'y',
        fill: true,
      },
      ...signalDatasets,
    ],
  }
})

const chartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  interaction: {
    mode: 'index' as const,
    intersect: false,
  },
  // 把当前选中日期传给自定义插件
  // chart.js 会把 options 里的自定义字段挂载到 chart 实例
  _selectedDate: selectedDate.value,
  plugins: {
    selectedDateLine: true,  // 启用自定义插件
    legend: {
      position: 'top' as const,
      align: 'start' as const,
      labels: {
        color: '#94a3b8',
        font: { size: 11 },
        usePointStyle: true,
        pointStyleWidth: 8,
        padding: 16,
      },
    },
    title: { display: false },
    tooltip: {
      backgroundColor: 'rgba(10,15,30,0.96)',
      borderColor: 'rgba(71,85,105,0.6)',
      borderWidth: 1,
      titleColor: '#f1f5f9',
      titleFont: { size: 12, weight: 'bold' as const },
      bodyColor: '#cbd5e1',
      bodyFont: { size: 11 },
      padding: 10,
      caretSize: 5,
      callbacks: {
        title: (items: any[]) => items[0]?.label || '',
        label: (item: any) => {
          const val = item.parsed.y
          if (val === null || val === undefined) return ''
          return `  ${item.dataset.label}: ${val}`
        },
      },
    },
  },
  scales: {
    x: {
      ticks: {
        color: '#475569',
        font: { size: 10 },
        maxTicksLimit: 12,
        maxRotation: 0,        // 不旋转，横排更清晰
        autoSkip: true,
      },
      grid: { color: 'rgba(51,65,85,0.25)' },
      border: { color: 'rgba(51,65,85,0.5)' },
    },
    y: {
      type: 'linear' as const,
      position: 'left' as const,
      title: {
        display: true,
        text: '共振股票数',
        color: '#e2e8f0',
        font: { size: 11 },
        padding: { bottom: 6 },
      },
      ticks: {
        color: '#e2e8f0',
        font: { size: 10 },
        precision: 0,
      },
      grid: { color: 'rgba(51,65,85,0.25)' },
      border: { color: 'rgba(51,65,85,0.5)' },
    },
    y1: {
      type: 'linear' as const,
      position: 'right' as const,
      title: {
        display: true,
        text: '各信号数',
        color: '#64748b',
        font: { size: 11 },
        padding: { bottom: 6 },
      },
      ticks: {
        color: '#64748b',
        font: { size: 10 },
        precision: 0,
      },
      grid: { drawOnChartArea: false },
      border: { color: 'rgba(51,65,85,0.5)' },
    },
  },
}))

const fetchData = async () => {
  try {
    loading.value = true
    error.value = ''
    const response = await axios.get(`/api/multi-signal-resonance?date=${selectedDate.value}`)
    resonanceData.value = response.data
  } catch (e) {
    error.value = '加载失败'
    console.error(e)
  } finally {
    loading.value = false
  }
}

const fetchTrendData = async (days = 60) => {
  try {
    trendLoading.value = true
    const response = await axios.get(`/api/multi-signal-trend?days=${days}`)
    trendData.value = response.data
  } catch (e) {
    console.error('Failed to fetch trend data:', e)
  } finally {
    trendLoading.value = false
  }
}

const fetchDates = async () => {
  try {
    const response = await axios.get('/api/multi-signal-resonance/dates')
    dateOptions.value = response.data.dates || []
    if (dateOptions.value.length > 0) {
      selectedDate.value = dateOptions.value[0].value
    }
  } catch (e) {
    console.error('Failed to fetch dates:', e)
  }
}

const totalSignals = () => {
  if (!resonanceData.value?.stocks) return 0
  return resonanceData.value.stocks.reduce((sum, st) => sum + st.signal_count, 0)
}

const getChangeClass = (changePct: number) => {
  if (changePct > 0) return 'change-up'
  if (changePct < 0) return 'change-down'
  return 'change-zero'
}

const onDateChange = async () => {
  await fetchData()
}

onMounted(async () => {
  await fetchDates()
  await Promise.all([fetchData(), fetchTrendData(trendDays.value)])
})
</script>

<template>
  <div class="max-w-7xl mx-auto">
    <!-- Filter -->
    <div class="glass-card p-4 mb-6 flex items-center gap-4">
      <label class="text-slate-400 font-medium">选择日期:</label>
      <select
        v-model="selectedDate"
        @change="onDateChange"
        class="bg-slate-800 border border-slate-700 rounded-lg px-4 py-2 text-slate-200 focus:outline-none focus:border-blue-500"
      >
        <option v-for="opt in dateOptions" :key="opt.value" :value="opt.value">
          {{ opt.label }}
        </option>
      </select>
    </div>

    <!-- Stats -->
    <div class="grid grid-cols-3 gap-4 mb-6">
      <div class="glass-card p-4 text-center">
        <p class="text-3xl font-bold text-blue-400">{{ resonanceData?.count || '-' }}</p>
        <p class="text-sm text-slate-400 mt-1">共振股票数</p>
      </div>
      <div class="glass-card p-4 text-center">
        <p class="text-3xl font-bold text-emerald-400">{{ totalSignals() || '-' }}</p>
        <p class="text-sm text-slate-400 mt-1">总信号数</p>
      </div>
      <div class="glass-card p-4 text-center">
        <p class="text-3xl font-bold text-purple-400">{{ resonanceData?.date || '-' }}</p>
        <p class="text-sm text-slate-400 mt-1">当前日期</p>
      </div>
    </div>

    <!-- Trend Chart -->
    <div class="glass-card p-4 mb-6">
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-slate-200 font-semibold text-base flex items-center gap-2">
          <span class="inline-block w-2.5 h-2.5 rounded-full bg-blue-400"></span>
          信号趋势图
        </h2>
        <div class="flex items-center gap-3">
          <span v-if="trendLoading" class="text-slate-400 text-xs flex items-center gap-1">
            <svg class="animate-spin w-3 h-3" viewBox="0 0 24 24" fill="none">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
            </svg>
            加载中
          </span>
          <span v-else-if="trendData" class="text-slate-500 text-xs">
            {{ trendData.dates?.length || 0 }} 个交易日
            <span v-if="(trendData.dates?.length ?? 0) > 0 && (trendData.dates?.length ?? 0) < trendDays"
              class="text-amber-500 ml-1">
              (数据库共 {{ trendData.dates?.length }} 天)
            </span>
          </span>
        </div>
      </div>

      <!-- 天数选择器 -->
      <div v-if="trendData && !trendLoading" class="flex items-center gap-2 mb-3">
        <span class="text-slate-500 text-xs">显示最近：</span>
        <button
          v-for="d in [30, 60, 120, 365]" :key="d"
          @click="trendDays = d; fetchTrendData(d)"
          :class="[
            'px-2 py-0.5 rounded text-xs border transition-colors',
            trendDays === d
              ? 'bg-blue-500/20 border-blue-500/60 text-blue-300'
              : 'border-slate-700 text-slate-500 hover:border-slate-500 hover:text-slate-300'
          ]"
        >{{ d }}天</button>
      </div>

      <!-- 数据不足提示 -->
      <div
        v-if="trendData && !trendLoading && (trendData.dates?.length ?? 0) < 2"
        class="mb-2 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-400 text-xs flex items-center gap-2"
      >
        <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
        </svg>
        当前数据库仅有 <strong>{{ trendData.dates?.length ?? 0 }}</strong> 个交易日的扫描记录，
        趋势图需要至少 2 天数据才能展示走势。<br>
        可在服务器终端运行：
        <code class="ml-1 px-1.5 py-0.5 rounded bg-slate-800 text-amber-300 font-mono text-xs">
          python signals/scan_signals_v2.py --last {{ trendDays }}
        </code>
        补充历史数据后刷新页面。
      </div>

      <!-- 图表主体：用 style 明确设置高度，确保 chart.js canvas 能正确计算尺寸 -->
      <div
        v-if="trendData && !trendLoading && (trendData.dates?.length ?? 0) >= 2"
        class="chart-wrapper"
        style="position: relative; height: 300px; width: 100%;"
      >
        <Line :data="chartData" :options="(chartOptions as any)" />
      </div>

      <div v-else-if="trendLoading" style="height: 300px;" class="flex items-center justify-center">
        <p class="text-slate-500 text-sm">趋势数据加载中…</p>
      </div>

      <div v-else-if="!trendData" style="height: 300px;" class="flex flex-col items-center justify-center gap-2">
        <svg class="w-8 h-8 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
            d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4v16"/>
        </svg>
        <p class="text-slate-600 text-sm">暂无趋势数据</p>
      </div>

      <!-- 图例说明 -->
      <div v-if="trendData && !trendLoading" class="flex flex-wrap gap-x-4 gap-y-1 mt-3 pt-3 border-t border-slate-700/50">
        <span class="text-xs text-slate-500 flex items-center gap-1">
          <span class="inline-block w-6 h-0.5 bg-slate-300 rounded"></span> 共振股票数（左轴）
        </span>
        <span v-for="[sig, color] in Object.entries(SIGNAL_COLORS)" :key="sig"
          class="text-xs flex items-center gap-1"
          :style="{ color: color }">
          <span class="inline-block w-4 h-0.5 rounded" :style="{ background: color }"></span>
          {{ sig }}（右轴）
        </span>
        <span class="text-xs text-amber-400 flex items-center gap-1 ml-auto">
          <span class="inline-block w-4 border-t border-dashed border-amber-400"></span>
          当前选择日期
        </span>
      </div>
    </div>

    <!-- Loading State -->
    <div v-if="loading" class="glass-card p-12 text-center">
      <p class="text-slate-400">加载中...</p>
    </div>

    <!-- Error State -->
    <div v-else-if="error" class="glass-card p-12 text-center text-red-400">
      {{ error }}
    </div>

    <!-- Table -->
    <div v-else class="glass-card overflow-hidden">
      <table class="w-full">
        <thead>
          <tr class="border-b border-slate-700">
            <th class="text-left py-3 px-4 text-slate-400 font-medium text-sm">代码</th>
            <th class="text-left py-3 px-4 text-slate-400 font-medium text-sm">名称</th>
            <th class="text-center py-3 px-4 text-slate-400 font-medium text-sm">信号数</th>
            <th class="text-left py-3 px-4 text-slate-400 font-medium text-sm">信号列表</th>
            <th class="text-right py-3 px-4 text-slate-400 font-medium text-sm">收盘价</th>
            <th class="text-right py-3 px-4 text-slate-400 font-medium text-sm">涨跌幅</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!resonanceData?.stocks?.length">
            <td colspan="6" class="text-center py-8 text-slate-400">暂无数据</td>
          </tr>
          <tr
            v-for="stock in resonanceData?.stocks"
            :key="stock.code"
            class="table-row border-b border-slate-800"
          >
            <td class="py-3 px-4 stock-code">{{ stock.code }}</td>
            <td class="py-3 px-4 stock-name">{{ stock.name }}</td>
            <td class="py-3 px-4 text-center"><strong>{{ stock.signal_count }}</strong></td>
            <td class="py-3 px-4">
              <div class="flex gap-1 flex-wrap">
                <span
                  v-for="signal in stock.signals"
                  :key="signal"
                  :class="'signal-tag ' + signal"
                >
                  {{ signal }}
                </span>
              </div>
            </td>
            <td class="py-3 px-4 text-right">{{ stock.close.toFixed(2) }}</td>
            <td class="py-3 px-4 text-right" :class="getChangeClass(stock.change_pct)">
              {{ stock.change_pct >= 0 ? '+' : '' }}{{ stock.change_pct.toFixed(2) }}%
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.glass-card {
  background: rgba(15, 23, 42, 0.75);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(51, 65, 85, 0.45);
  border-radius: 12px;
}

.signal-tag {
  padding: 2px 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.03em;
}

.signal-tag.B1   { background: rgba(59, 130, 246, 0.25); color: #60a5fa; border: 1px solid rgba(59,130,246,0.3); }
.signal-tag.B2   { background: rgba(16, 185, 129, 0.25); color: #34d399; border: 1px solid rgba(16,185,129,0.3); }
.signal-tag.BLK  { background: rgba(249, 115, 22, 0.25); color: #fb923c; border: 1px solid rgba(249,115,22,0.3); }
.signal-tag.DL   { background: rgba(139, 92, 246, 0.25); color: #a78bfa; border: 1px solid rgba(139,92,246,0.3); }
.signal-tag.DZ30 { background: rgba(6, 182, 212, 0.25);  color: #22d3ee; border: 1px solid rgba(6,182,212,0.3); }
.signal-tag.SCB  { background: rgba(236, 72, 153, 0.25); color: #f472b6; border: 1px solid rgba(236,72,153,0.3); }
.signal-tag.BLKB2{ background: rgba(132, 204, 22, 0.25); color: #a3e635; border: 1px solid rgba(132,204,22,0.3); }

.stock-code { font-weight: 600; color: #f1f5f9; font-family: 'Courier New', monospace; font-size: 13px; }
.stock-name { color: #94a3b8; }

.change-up   { color: #ef4444; font-weight: 600; }
.change-down { color: #10b981; font-weight: 600; }
.change-zero { color: #94a3b8; }

.table-row { transition: background 0.15s; }
.table-row:hover { background: rgba(59, 130, 246, 0.08); }
</style>

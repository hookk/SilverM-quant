import { defineStore } from 'pinia'
import axios from 'axios'
import { ref, computed } from 'vue'

export interface Strategy {
  name: string
  description?: string
  threshold_required?: boolean
  min_data_days?: number
  version?: string
  author?: string
  class_path?: string
}

export interface BacktestParams {
  strategy_name: string
  start_date: string
  end_date: string
  stock_list?: string[]
  initial_capital?: number
}

export interface PerformanceMetrics {
  total_return: number
  annual_return: number
  benchmark_return: number
  excess_return: number
  sharpe_ratio: number
  sortino_ratio: number
  calmar_ratio: number
  max_drawdown: number
  max_drawdown_duration: number
  volatility: number
  win_rate: number
  profit_loss_ratio: number
  total_trades: number
}

export interface BacktestResult {
  run_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  metrics?: PerformanceMetrics
  error?: string | null
}

export interface BatchBacktestResult {
  task_id: string
  total_stocks: number
  success_count: number
  fail_count: number
  no_data_count: number
  success_rate: number
  avg_return: number
  avg_sharpe: number
  avg_win_rate: number
  avg_annual_return: number
  avg_max_drawdown: number
  total_trades: number
  top5_stocks: any[]
  bottom5_stocks: any[]
  param_results?: any[]
  stocks: any[]
}

export const useBacktestStore = defineStore('backtest', () => {
  // State
  const strategies = ref<Strategy[]>([])
  const selectedStrategies = ref<string[]>([])
  const startDate = ref<string>('')
  const endDate = ref<string>('')
  const stockSelectionMode = ref<'all' | 'single' | 'multiple'>('all')
  const selectedStocks = ref<string[]>([])
  const initialCapital = ref<number>(1000000)
  const isLoading = ref(false)
  const currentResult = ref<BacktestResult | null>(null)
  const error = ref<string | null>(null)

  // Batch backtest state
  const batchTaskId = ref<string | null>(null)
  const batchPollInterval = ref<number | null>(null)
  const batchStatus = ref<'idle' | 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'>('idle')
  const batchProgress = ref<number>(0)
  const batchMessage = ref<string>('')
  const batchResult = ref<BatchBacktestResult | null>(null)
  const batchError = ref<string | null>(null)

  // Getters
  const hasResult = computed(() => currentResult.value !== null && currentResult.value.status === 'completed')
  const isRunning = computed(() => isLoading.value)
  const isBatchRunning = computed(() => batchStatus.value === 'running' || batchStatus.value === 'pending')
  const hasBatchResult = computed(() => batchResult.value !== null && batchStatus.value === 'completed')

  // ✅ 修复：调用 detail=true 接口，返回完整对象数组，兼容字符串数组
  async function fetchStrategies() {
    try {
      isLoading.value = true
      error.value = null
      const response = await axios.get('/api/backtest/strategies?detail=true')
      const raw: (string | Strategy)[] = response.data.strategies || []
      // 兼容两种格式：字符串数组（旧接口）或对象数组（新接口）
      strategies.value = raw.map((item) =>
        typeof item === 'string' ? { name: item } : item
      )
    } catch (e) {
      error.value = '获取策略列表失败'
      console.error('Failed to fetch strategies:', e)
    } finally {
      isLoading.value = false
    }
  }

  async function runBacktest() {
    if (selectedStrategies.value.length === 0) {
      error.value = '请至少选择一个策略'
      return
    }

    if (!startDate.value || !endDate.value) {
      error.value = '请选择日期范围'
      return
    }

    try {
      isLoading.value = true
      error.value = null
      currentResult.value = { run_id: '', status: 'running' }

      const params: BacktestParams = {
        strategy_name: selectedStrategies.value[0], // API 只支持单策略
        start_date: startDate.value.replace(/-/g, ''),
        end_date: endDate.value.replace(/-/g, ''),
        initial_capital: initialCapital.value
      }

      if (stockSelectionMode.value === 'single' && selectedStocks.value.length > 0) {
        params.stock_list = [selectedStocks.value[0]]
      } else if (stockSelectionMode.value === 'multiple' && selectedStocks.value.length > 0) {
        params.stock_list = selectedStocks.value
      }

      const response = await axios.post('/api/backtest/run', params)

      if (response.data.error) {
        throw new Error(response.data.error)
      }

      // 后端 /run 只返回部分 metrics，补齐前端需要的所有字段
      const rawMetrics = response.data.metrics || {}
      const fullMetrics: PerformanceMetrics = {
        total_return:         rawMetrics.total_return      ?? 0,
        annual_return:        rawMetrics.annual_return     ?? rawMetrics.annualized_return ?? 0,
        benchmark_return:     rawMetrics.benchmark_return  ?? 0,
        excess_return:        rawMetrics.excess_return     ?? rawMetrics.total_return ?? 0,
        sharpe_ratio:         rawMetrics.sharpe_ratio      ?? 0,
        sortino_ratio:        rawMetrics.sortino_ratio     ?? 0,
        calmar_ratio:         rawMetrics.calmar_ratio      ?? 0,
        max_drawdown:         rawMetrics.max_drawdown      ?? 0,
        max_drawdown_duration:rawMetrics.max_drawdown_duration ?? 0,
        volatility:           rawMetrics.volatility        ?? 0,
        win_rate:             rawMetrics.win_rate          ?? 0,
        profit_loss_ratio:    rawMetrics.profit_loss_ratio ?? 0,
        total_trades:         rawMetrics.total_trades      ?? 0,
      }

      currentResult.value = {
        run_id: response.data.run_id || '',
        status: 'completed',
        metrics: fullMetrics,
        error: null
      }
    } catch (e: any) {
      const errMsg = e.response?.data?.error || e.message || '回测运行失败'
      error.value = `回测失败: ${errMsg}`
      currentResult.value = {
        run_id: '',
        status: 'failed',
        error: errMsg
      }
      console.error('Failed to run backtest:', e)
    } finally {
      isLoading.value = false
    }
  }

  function pollBatchTask() {
    if (!batchTaskId.value) return

    const poll = async () => {
      try {
        const response = await axios.get(`/api/backtest/batch-task/${batchTaskId.value}`)
        const data = response.data

        batchStatus.value = data.status
        batchProgress.value = data.progress
        batchMessage.value = data.message

        if (data.status === 'completed') {
          try {
            const resultRes = await axios.get(`/api/backtest/batch-results/${batchTaskId.value}`)
            batchResult.value = resultRes.data
          } catch (resultErr: any) {
            console.error('获取回测结果失败:', resultErr)
            batchError.value = resultErr.response?.data?.error || '获取结果失败'
            batchMessage.value = `❌ 获取结果失败: ${batchError.value}`
            batchStatus.value = 'failed'
            stopPolling()
            return
          }
          batchStatus.value = 'completed'
          batchProgress.value = 100
          batchMessage.value = '✅ 回测完成'
          stopPolling()
        } else if (data.status === 'failed') {
          // 优先显示 message（后端已注入 traceback 短摘要），fallback 到 error_message
          const errDetail = data.message || data.error_message || '回测失败，请查看服务端日志'
          batchError.value = data.error_message || errDetail
          batchMessage.value = `❌ ${errDetail}`
          batchProgress.value = data.progress ?? batchProgress.value
          batchStatus.value = 'failed'
          stopPolling()
        }
      } catch (e: any) {
        console.error('轮询失败:', e)
        const errMsg = e.response?.data?.error || e.message || '网络请求失败'
        batchError.value = errMsg
        batchMessage.value = `❌ 轮询失败: ${errMsg}`
        batchStatus.value = 'failed'
        stopPolling()
      }
    }

    batchPollInterval.value = window.setInterval(poll, 2000)

    window.setTimeout(() => {
      if (batchStatus.value === 'running' || batchStatus.value === 'pending') {
        batchError.value = '回测超时（30分钟），任务仍在后台运行'
        batchMessage.value = '❌ 回测超时（30分钟），可刷新页面重新查询历史'
        batchStatus.value = 'failed'
        stopPolling()
      }
    }, 30 * 60 * 1000)
  }

  function stopPolling() {
    if (batchPollInterval.value !== null) {
      clearInterval(batchPollInterval.value)
      batchPollInterval.value = null
    }
  }

  async function cancelBatchBacktest() {
    if (!batchTaskId.value) return
    try {
      await axios.delete(`/api/backtest/batch-task/${batchTaskId.value}`)
      batchStatus.value = 'cancelled'
      batchMessage.value = '任务已取消'
      stopPolling()
    } catch (e: any) {
      console.error('取消任务失败:', e)
    }
  }

  async function submitBatchBacktest(paramGrid?: string) {
    if (selectedStrategies.value.length === 0) {
      batchError.value = '请至少选择一个策略'
      return
    }

    if (!startDate.value || !endDate.value) {
      batchError.value = '请选择日期范围'
      return
    }

    try {
      batchError.value = null
      batchResult.value = null
      batchProgress.value = 0
      batchMessage.value = '提交回测任务...'

      const params: any = {
        strategy_name: selectedStrategies.value[0],
        start_date: startDate.value.replace(/-/g, ''),
        end_date: endDate.value.replace(/-/g, ''),
        initial_capital: initialCapital.value
      }

      if (stockSelectionMode.value !== 'all' && selectedStocks.value.length > 0) {
        params.stock_list = selectedStocks.value
      }

      if (paramGrid) {
        params.param_grid = paramGrid
      }

      const response = await axios.post('/api/backtest/batch-run', params)

      batchTaskId.value = response.data.task_id
      batchStatus.value = 'pending'
      batchMessage.value = '任务已提交...'

      pollBatchTask()
    } catch (e: any) {
      batchError.value = e.response?.data?.error || '提交失败'
      batchStatus.value = 'failed'
      console.error('提交批量回测失败:', e)
    }
  }

  function resetBatchResult() {
    stopPolling()
    batchTaskId.value = null
    batchStatus.value = 'idle'
    batchProgress.value = 0
    batchMessage.value = ''
    batchResult.value = null
    batchError.value = null
  }

  function resetResult() {
    currentResult.value = null
    error.value = null
  }

  function setDateRange(start: string, end: string) {
    startDate.value = start
    endDate.value = end
  }

  function setSelectedStrategies(stra: string[]) {
    selectedStrategies.value = stra
  }

  function setStockSelection(mode: 'all' | 'single' | 'multiple', stocks: string[] = []) {
    stockSelectionMode.value = mode
    selectedStocks.value = stocks
  }

  return {
    strategies,
    selectedStrategies,
    startDate,
    endDate,
    stockSelectionMode,
    selectedStocks,
    initialCapital,
    isLoading,
    currentResult,
    error,
    hasResult,
    isRunning,
    fetchStrategies,
    runBacktest,
    resetResult,
    setDateRange,
    setSelectedStrategies,
    setStockSelection,
    batchTaskId,
    batchStatus,
    batchProgress,
    batchMessage,
    batchResult,
    batchError,
    isBatchRunning,
    hasBatchResult,
    submitBatchBacktest,
    resetBatchResult,
    stopPolling,
    cancelBatchBacktest
  }
})

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import axios from 'axios'

interface Strategy {
  name: string
  description?: string
  threshold_required?: boolean
  min_data_days?: number
  version?: string
  author?: string
  class_path?: string
}

interface StrategyFile {
  path: string
  name: string
  size: number
  has_register: boolean
  is_jukuan: boolean
  registered_name: string | null
  already_registered: boolean
}

const strategies = ref<Strategy[]>([])
const isLoading = ref(false)
const message = ref('')
const messageType = ref<'success' | 'error'>('success')

// 文件浏览弹窗
const showFileBrowser = ref(false)
const fileList = ref<StrategyFile[]>([])
const fileLoading = ref(false)
const fileFilter = ref('')
const selectedFile = ref<StrategyFile | null>(null)
const registering = ref(false)

const filteredFiles = computed(() => {
  const q = fileFilter.value.trim().toLowerCase()
  if (!q) return fileList.value
  return fileList.value.filter(
    f => f.name.toLowerCase().includes(q) ||
         (f.registered_name || '').toLowerCase().includes(q) ||
         f.path.toLowerCase().includes(q)
  )
})

async function fetchStrategies() {
  isLoading.value = true
  try {
    const response = await axios.get('/api/backtest/strategies?detail=true')
    const raw = response.data.strategies || []
    strategies.value = raw.map((item: string | Strategy) =>
      typeof item === 'string' ? { name: item } : item
    )
    message.value = ''
  } catch (e) {
    showMessage('获取策略列表失败', 'error')
  } finally {
    isLoading.value = false
  }
}

async function openFileBrowser() {
  showFileBrowser.value = true
  fileLoading.value = true
  fileFilter.value = ''
  selectedFile.value = null
  try {
    const response = await axios.get('/api/backtest/browse-strategies')
    fileList.value = response.data.files || []
  } catch (e: any) {
    showMessage('获取策略文件列表失败: ' + (e.response?.data?.error || e.message), 'error')
    showFileBrowser.value = false
  } finally {
    fileLoading.value = false
  }
}

function closeFileBrowser() {
  showFileBrowser.value = false
  selectedFile.value = null
}

async function registerSelectedFile() {
  if (!selectedFile.value) return
  registering.value = true
  try {
    const response = await axios.post('/api/backtest/register-strategy-file', {
      path: selectedFile.value.path
    })
    if (response.data.success) {
      showMessage(`注册成功：${response.data.name}`, 'success')
      // 刷新已注册策略列表
      await fetchStrategies()
      // 刷新文件浏览列表（更新 already_registered 状态）
      const refreshed = await axios.get('/api/backtest/browse-strategies')
      fileList.value = refreshed.data.files || []
      selectedFile.value = null
    } else {
      showMessage('注册失败: ' + (response.data.error || '未知错误'), 'error')
    }
  } catch (e: any) {
    const errMsg = e.response?.data?.error || e.message
    const tb = e.response?.data?.traceback
    showMessage('注册失败: ' + errMsg + (tb ? '\n' + tb.slice(0, 300) : ''), 'error')
  } finally {
    registering.value = false
  }
}

async function registerAllStrategies() {
  isLoading.value = true
  try {
    const response = await axios.post('/api/backtest/register-strategies')
    const raw = response.data.strategies || []
    strategies.value = raw.map((item: string | Strategy) =>
      typeof item === 'string' ? { name: item } : item
    )
    showMessage(
      `全量扫描完成：成功 ${response.data.loaded} 个，失败 ${response.data.failed} 个`,
      response.data.failed > 0 ? 'error' : 'success'
    )
  } catch (e: any) {
    showMessage('全量注册失败: ' + (e.response?.data?.error || e.message), 'error')
  } finally {
    isLoading.value = false
  }
}

async function deleteStrategy(name: string) {
  if (!confirm(`确定删除策略 "${name}" 吗？`)) return
  isLoading.value = true
  try {
    await axios.delete(`/api/backtest/strategies/${encodeURIComponent(name)}`)
    await fetchStrategies()
    showMessage(`删除策略 "${name}" 成功`, 'success')
  } catch (e: any) {
    showMessage('删除策略失败: ' + (e.response?.data?.error || e.message), 'error')
  } finally {
    isLoading.value = false
  }
}

function showMessage(msg: string, type: 'success' | 'error') {
  message.value = msg
  messageType.value = type
  setTimeout(() => { message.value = '' }, 6000)
}

onMounted(() => {
  fetchStrategies()
})
</script>

<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
    <div class="max-w-5xl mx-auto">
      <header class="mb-8">
        <div class="flex items-center justify-between">
          <div>
            <h1 class="text-3xl font-bold tracking-tight">策略管理</h1>
            <p class="text-slate-400 mt-1">查看、注册和删除策略</p>
          </div>
          <div class="flex items-center gap-3">
            <!-- 主按钮：从文件注册 -->
            <button
              @click="openFileBrowser"
              :disabled="isLoading"
              class="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-600 text-white rounded-lg font-medium transition-colors flex items-center gap-2"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                  d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/>
              </svg>
              从文件注册策略
            </button>
            <!-- 次要按钮：全量扫描 -->
            <button
              @click="registerAllStrategies"
              :disabled="isLoading"
              title="扫描 strategies/ 目录下所有文件并批量注册（含 @register 装饰器的）"
              class="px-4 py-2 bg-slate-700 hover:bg-slate-600 disabled:bg-slate-600 text-slate-300 rounded-lg font-medium transition-colors text-sm"
            >
              全量扫描注册
            </button>
          </div>
        </div>
      </header>

      <div v-if="message" :class="[
        'mb-6 p-4 rounded-lg text-sm whitespace-pre-wrap',
        messageType === 'success'
          ? 'bg-green-500/20 border border-green-500/50 text-green-400'
          : 'bg-red-500/20 border border-red-500/50 text-red-400'
      ]">
        {{ message }}
      </div>

      <!-- 策略列表 -->
      <div class="bg-slate-800/50 backdrop-blur border border-slate-700 rounded-xl p-6">
        <div v-if="isLoading && strategies.length === 0" class="flex items-center justify-center py-12">
          <svg class="w-8 h-8 animate-spin text-blue-400" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
            <path class="opacity-75" fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 0114 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"/>
          </svg>
        </div>

        <div v-else-if="strategies.length === 0" class="text-center py-12">
          <svg class="w-16 h-16 mx-auto mb-4 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
          </svg>
          <p class="text-slate-400 mb-4">暂无已注册的策略</p>
          <button
            @click="openFileBrowser"
            class="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm transition-colors"
          >
            点击选择策略文件注册
          </button>
        </div>

        <div v-else>
          <div class="flex items-center justify-between mb-4">
            <h2 class="text-lg font-semibold">策略列表 ({{ strategies.length }})</h2>
            <button
              @click="fetchStrategies"
              :disabled="isLoading"
              class="text-sm text-blue-400 hover:text-blue-300 disabled:text-slate-500"
            >
              刷新
            </button>
          </div>

          <table class="w-full">
            <thead>
              <tr class="text-left text-slate-400 border-b border-slate-700">
                <th class="pb-3 pr-4">策略名称</th>
                <th class="pb-3 pr-4">需要 threshold</th>
                <th class="pb-3 pr-4">最小数据天数</th>
                <th class="pb-3 pr-4">版本</th>
                <th class="pb-3">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="strategy in strategies"
                :key="strategy.name"
                class="border-b border-slate-700/50 hover:bg-slate-700/30"
              >
                <td class="py-4 pr-4 font-medium">
                  {{ strategy.name }}
                  <span
                    v-if="strategy.description"
                    class="ml-2 text-xs text-slate-500 cursor-help"
                    :title="strategy.description"
                  >ⓘ</span>
                </td>
                <td class="py-4 pr-4">
                  <span v-if="strategy.threshold_required" class="text-green-400">是</span>
                  <span v-else class="text-slate-500">否</span>
                </td>
                <td class="py-4 pr-4">{{ strategy.min_data_days ?? '-' }}</td>
                <td class="py-4 pr-4 text-slate-400 text-sm">{{ strategy.version || '-' }}</td>
                <td class="py-4">
                  <button
                    @click="deleteStrategy(strategy.name)"
                    :disabled="isLoading"
                    class="text-red-400 hover:text-red-300 disabled:text-slate-600 text-sm"
                  >
                    删除
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 文件浏览弹窗 -->
    <Teleport to="body">
      <div
        v-if="showFileBrowser"
        class="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4"
        @click.self="closeFileBrowser"
      >
        <div class="bg-slate-800 border border-slate-600 rounded-xl w-full max-w-2xl max-h-[80vh] flex flex-col shadow-2xl">
          <!-- 弹窗标题 -->
          <div class="flex items-center justify-between px-6 py-4 border-b border-slate-700">
            <h3 class="text-lg font-semibold">选择策略文件注册</h3>
            <button @click="closeFileBrowser" class="text-slate-400 hover:text-white">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
              </svg>
            </button>
          </div>

          <!-- 搜索框 -->
          <div class="px-6 py-3 border-b border-slate-700">
            <input
              v-model="fileFilter"
              type="text"
              placeholder="过滤文件名 / 策略名..."
              class="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-400 focus:outline-none focus:border-blue-500"
            />
          </div>

          <!-- 文件列表 -->
          <div class="flex-1 overflow-y-auto px-6 py-2">
            <div v-if="fileLoading" class="flex items-center justify-center py-12">
              <svg class="w-6 h-6 animate-spin text-blue-400" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                <path class="opacity-75" fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 0114 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"/>
              </svg>
            </div>
            <div v-else-if="filteredFiles.length === 0" class="text-center py-8 text-slate-400 text-sm">
              没有找到匹配的策略文件
            </div>
            <div v-else class="space-y-1 py-2">
              <div
                v-for="file in filteredFiles"
                :key="file.path"
                @click="!file.already_registered && (selectedFile = selectedFile?.path === file.path ? null : file)"
                :class="[
                  'rounded-lg px-4 py-3 flex items-start gap-3 transition-colors',
                  file.already_registered
                    ? 'opacity-50 cursor-not-allowed'
                    : 'cursor-pointer',
                  selectedFile?.path === file.path
                    ? 'bg-blue-600/30 border border-blue-500'
                    : 'hover:bg-slate-700/60 border border-transparent',
                ]"
              >
                <!-- 文件图标 -->
                <div class="mt-0.5 shrink-0">
                  <svg class="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                      d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
                  </svg>
                </div>
                <!-- 文件信息 -->
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="font-medium text-sm truncate">{{ file.name }}</span>
                    <span v-if="file.registered_name" class="text-xs text-blue-400 truncate">
                      → {{ file.registered_name }}
                    </span>
                    <span v-if="file.already_registered"
                      class="text-xs bg-green-600/30 text-green-400 border border-green-600/40 rounded px-1.5 py-0.5 shrink-0">
                      已注册
                    </span>
                    <span v-if="file.is_jukuan"
                      class="text-xs bg-amber-600/30 text-amber-400 border border-amber-600/40 rounded px-1.5 py-0.5 shrink-0">
                      聚宽（仅元数据）
                    </span>
                    <span v-if="!file.has_register"
                      class="text-xs bg-slate-600/50 text-slate-400 border border-slate-600 rounded px-1.5 py-0.5 shrink-0">
                      无 @register
                    </span>
                  </div>
                  <div class="text-xs text-slate-500 mt-0.5 truncate">{{ file.path }}</div>
                </div>
              </div>
            </div>
          </div>

          <!-- 操作栏 -->
          <div class="px-6 py-4 border-t border-slate-700 flex items-center justify-between gap-3">
            <div class="text-sm text-slate-400">
              <span v-if="selectedFile">已选：{{ selectedFile.registered_name || selectedFile.name }}</span>
              <span v-else>点击列表中的文件选择</span>
            </div>
            <div class="flex gap-3">
              <button
                @click="closeFileBrowser"
                class="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg text-sm transition-colors"
              >
                取消
              </button>
              <button
                @click="registerSelectedFile"
                :disabled="!selectedFile || registering"
                class="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-600 disabled:cursor-not-allowed text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
              >
                <svg v-if="registering" class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                  <path class="opacity-75" fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 0114 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"/>
                </svg>
                {{ registering ? '注册中...' : '注册此策略' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

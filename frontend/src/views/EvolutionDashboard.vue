<template>
  <div class="evo-root">
    <!-- ── Header ─────────────────────────────────────────────── -->
    <div class="evo-header">
      <div class="evo-header-left">
        <div class="evo-logo">
          <span class="evo-logo-icon">⟳</span>
          <div>
            <h1 class="evo-title">进化系统</h1>
            <p class="evo-subtitle">Alpha Signal Auto-Evolution Dashboard</p>
          </div>
        </div>
      </div>
      <div class="evo-header-right">
        <button class="evo-btn evo-btn-ghost" @click="toggleAutoRefresh" :class="{ 'btn-auto-active': autoRefresh }">
          {{ autoRefresh ? '⏹ 自动刷新' : '▶ 自动刷新' }}
        </button>
        <button class="evo-btn evo-btn-ghost" @click="refreshAll" :disabled="loading">
          <span :class="loading ? 'spin' : ''">↻</span> 刷新
        </button>
        <button class="evo-btn evo-btn-accent" @click="showCreateModal = true">＋ 新建信号</button>
      </div>
    </div>

    <!-- ── Loading ────────────────────────────────────────────── -->
    <div v-if="loading && !signals.length" class="evo-loading">
      <div class="evo-spinner"></div>
      <p>加载进化系统数据…</p>
    </div>

    <!-- ── Empty state ────────────────────────────────────────── -->
    <div v-else-if="!loading && !signals.length" class="evo-empty">
      <div class="evo-empty-icon">🧬</div>
      <h3>尚无进化信号</h3>
      <p>使用 CLI 创建第一个信号：</p>
      <code class="evo-code-inline">python -m evolution.cli create my_signal --direction "探索价量背离"</code>
    </div>

    <template v-else>
      <!-- ── Signal grid ─────────────────────────────────────── -->
      <div class="evo-section-label">信号列表 · {{ signals.length }} 个</div>
      <div class="evo-signal-grid">
        <div
          v-for="sig in signals"
          :key="sig.name"
          class="evo-signal-card"
          :class="{ active: selectedSignal === sig.name }"
          @click="selectSignal(sig.name)"
        >
          <div class="evo-signal-card-top">
            <span class="evo-signal-name">{{ sig.name }}</span>
            <div class="evo-badges">
              <span v-if="sig.stop_pending" class="evo-badge badge-stop">停止中</span>
              <span v-else-if="sig.pending_inject" class="evo-badge badge-inject">待注入</span>
              <span v-else-if="sig.current_iter >= 0" class="evo-badge badge-active">运行中</span>
              <span v-else class="evo-badge badge-new">新建</span>
            </div>
          </div>
          <div class="evo-signal-score">
            <span class="evo-score-value">{{ fmtScore(sig.best_score) }}</span>
            <span class="evo-score-label">T+5 Sharpe</span>
          </div>
          <div class="evo-signal-meta">
            <span>第 {{ sig.current_iter >= 0 ? sig.current_iter : '—' }} 轮</span>
            <span>最佳 Iter {{ sig.best_iteration >= 0 ? sig.best_iteration : '—' }}</span>
          </div>
          <div class="evo-signal-hyp" v-if="sig.hypothesis">
            "{{ sig.hypothesis.slice(0, 60) }}{{ sig.hypothesis.length > 60 ? '…' : '' }}"
          </div>
        </div>
      </div>

      <!-- ── Detail panel ───────────────────────────────────── -->
      <template v-if="selectedSignal">
        <div class="evo-detail-header">
          <div class="evo-detail-title">
            <span class="evo-detail-name">{{ selectedSignal }}</span>
            <div class="evo-tab-bar">
              <button
                v-for="tab in tabs"
                :key="tab.key"
                class="evo-tab"
                :class="{ 'evo-tab-active': activeTab === tab.key }"
                @click="activeTab = tab.key"
              >{{ tab.label }}</button>
            </div>
          </div>
          <div class="evo-detail-actions">
            <button class="evo-btn evo-btn-ghost" @click="promoteSignal" :title="'推广最佳代码到 signals/ 目录'">🚀 推广</button>
            <button class="evo-btn evo-btn-accent" @click="showInjectModal = true">注入方向</button>
            <button class="evo-btn evo-btn-danger" @click="stopSignal">停止</button>
          </div>
        </div>

        <!-- Tab: 进化曲线 -->
        <div v-if="activeTab === 'curve'" class="evo-panel">
          <div v-if="!history.length" class="evo-panel-empty">暂无迭代数据</div>
          <template v-else>
            <div class="evo-chart-container">
              <canvas ref="chartCanvas" class="evo-chart"></canvas>
            </div>
            <div class="evo-metrics-row">
              <div class="evo-metric-box" v-for="m in summaryMetrics" :key="m.label">
                <div class="evo-metric-val" :class="m.color">{{ m.value }}</div>
                <div class="evo-metric-label">{{ m.label }}</div>
              </div>
            </div>
          </template>
        </div>

        <!-- Tab: 迭代详情 -->
        <div v-if="activeTab === 'history'" class="evo-panel">
          <div v-if="!history.length" class="evo-panel-empty">暂无迭代记录</div>
          <div v-else class="evo-table-wrap">
            <table class="evo-table">
              <thead>
                <tr>
                  <th>轮次</th>
                  <th>T+5 Sharpe</th>
                  <th>IC</th>
                  <th>胜率</th>
                  <th>覆盖率</th>
                  <th>训练 Sharpe</th>
                  <th>假设</th>
                  <th>结论</th>
                  <th>代码</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="iter in history"
                  :key="iter.iteration"
                  class="evo-table-row"
                  :class="{
                    'row-best': iter.iteration === bestIter,
                    'row-failed': iter.conclusion && iter.conclusion.includes('FAILED')
                  }"
                >
                  <td class="evo-iter-num">
                    <span v-if="iter.iteration === bestIter" class="evo-star">★</span>
                    {{ iter.iteration }}
                  </td>
                  <td>
                    <span :class="scoreClass(iter.valid?.sharpe_t5)">
                      {{ fmtScore(iter.valid?.sharpe_t5) }}
                    </span>
                  </td>
                  <td>{{ fmtScore(iter.valid?.ic_t5) }}</td>
                  <td>{{ fmtPct(iter.valid?.win_rate_t5) }}</td>
                  <td>{{ fmtPct(iter.valid?.coverage) }}</td>
                  <td :class="overfitClass(iter)">{{ fmtScore(iter.train?.sharpe_t5) }}</td>
                  <td class="evo-hyp-cell" :title="iter.hypothesis">
                    {{ (iter.hypothesis || '').slice(0, 40) }}{{ iter.hypothesis?.length > 40 ? '…' : '' }}
                  </td>
                  <td class="evo-concl-cell" :title="iter.conclusion">
                    {{ (iter.conclusion || '').slice(0, 40) }}{{ iter.conclusion?.length > 40 ? '…' : '' }}
                  </td>
                  <td>
                    <button
                      class="evo-btn evo-btn-xs"
                      @click="viewCode(iter.iteration)"
                    >查看</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Tab: 代码查看 -->
        <div v-if="activeTab === 'code'" class="evo-panel">
          <div class="evo-code-toolbar">
            <div class="evo-code-selector">
              <label>版本 A</label>
              <select v-model="diffV1" class="evo-select">
                <option v-for="n in codeIters" :key="n" :value="n">iter_{{ String(n).padStart(3,'0') }}</option>
              </select>
              <label>版本 B</label>
              <select v-model="diffV2" class="evo-select">
                <option v-for="n in codeIters" :key="n" :value="n">iter_{{ String(n).padStart(3,'0') }}</option>
              </select>
              <button class="evo-btn evo-btn-sm" @click="loadDiff">对比</button>
              <button class="evo-btn evo-btn-sm" @click="loadSingleCode(diffV2)">查看单个</button>
            </div>
            <div v-if="diffStats" class="evo-diff-stats">
              <span class="diff-add">+{{ diffStats.adds }}</span>
              <span class="diff-rem">-{{ diffStats.removes }}</span>
            </div>
          </div>

          <div v-if="codeLoading" class="evo-panel-empty">加载中…</div>

          <!-- Single code view -->
          <div v-else-if="singleCode" class="evo-code-view">
            <pre class="evo-pre"><code>{{ singleCode }}</code></pre>
          </div>

          <!-- Diff view -->
          <div v-else-if="diffLines.length" class="evo-diff-view">
            <div
              v-for="(line, i) in diffLines"
              :key="i"
              class="evo-diff-line"
              :class="diffLineClass(line)"
            >{{ line }}</div>
          </div>

          <div v-else class="evo-panel-empty">请选择版本后点击「对比」或「查看单个」</div>
        </div>

        <!-- Tab: 记忆摘要 -->
        <div v-if="activeTab === 'memory'" class="evo-panel">
          <div v-if="memoryLoading" class="evo-panel-empty">加载中…</div>
          <div v-else>
            <div class="evo-memory-grid">
              <!-- Best result -->
              <div class="evo-memory-box">
                <h4 class="evo-box-title">🏆 当前最佳</h4>
                <div v-if="memoryData?.best_result">
                  <div class="evo-kv" v-for="(v, k) in bestMetricsDisplay" :key="k">
                    <span class="evo-kv-key">{{ k }}</span>
                    <span class="evo-kv-val">{{ v }}</span>
                  </div>
                  <div class="evo-kv" v-if="memoryData.best_result.best_params">
                    <span class="evo-kv-key">最优参数</span>
                    <code class="evo-params-code">{{ JSON.stringify(memoryData.best_result.best_params, null, 2) }}</code>
                  </div>
                </div>
                <div v-else class="evo-panel-empty">尚无最佳结果</div>
              </div>

              <!-- L2 feedback -->
              <div class="evo-memory-box">
                <h4 class="evo-box-title">📊 最近 L2 反馈</h4>
                <div v-if="memoryData?.l2_feedback">
                  <div class="evo-l2-section" v-if="memoryData.l2_feedback.summary">
                    <div class="evo-kv" v-for="(v, k) in l2SummaryDisplay" :key="k">
                      <span class="evo-kv-key">{{ k }}</span>
                      <span class="evo-kv-val">{{ v }}</span>
                    </div>
                  </div>
                  <div class="evo-l2-cap" v-if="memoryData.l2_feedback.cap_group">
                    <div class="evo-cap-label">市值分组 Sharpe</div>
                    <div class="evo-cap-bars">
                      <div
                        v-for="(v, grp) in memoryData.l2_feedback.cap_group"
                        :key="grp"
                        class="evo-cap-bar-row"
                      >
                        <span class="evo-cap-grp">{{ grp }}</span>
                        <div class="evo-cap-bar-wrap">
                          <div class="evo-cap-bar" :style="capBarStyle(v)"></div>
                        </div>
                        <span class="evo-cap-val">{{ fmtScore(v) }}</span>
                      </div>
                    </div>
                  </div>
                </div>
                <div v-else class="evo-panel-empty">尚无 L2 反馈（每10轮自动触发）</div>
              </div>
            </div>

            <!-- Summary MD -->
            <div class="evo-summary-box">
              <h4 class="evo-box-title">📝 进化摘要 (summary.md)</h4>
              <div v-if="memoryData?.summary_md" class="evo-summary-text">
                <pre class="evo-pre evo-pre-md">{{ memoryData.summary_md }}</pre>
              </div>
              <div v-else class="evo-panel-empty">尚无摘要（满10轮后自动生成）</div>
            </div>
          </div>
        </div>

        <!-- Tab: Test 集评估 -->
        <div v-if="activeTab === 'test'" class="evo-panel">
          <div class="evo-test-warning">
            <span class="evo-warn-icon">🔒</span>
            <div>
              <strong>人工专属 · Test 集结果</strong>
              <p>以下结果永远不会暴露给进化 Agent。这是信号在 2025-10-01 之后真实市场上的表现。</p>
            </div>
          </div>

          <div v-if="testLoading" class="evo-panel-empty">加载中…</div>
          <div v-else-if="!testData?.exists" class="evo-panel-empty">
            <p>{{ testData?.message || '尚无 Test 集评估' }}</p>
            <code class="evo-code-inline">evolve evaluate {{ selectedSignal }}</code>
          </div>
          <div v-else class="evo-test-results">
            <div class="evo-test-grid">
              <div class="evo-test-box" v-for="m in testMetrics" :key="m.label">
                <div class="evo-test-val" :class="m.color">{{ m.value }}</div>
                <div class="evo-test-label">{{ m.label }}</div>
              </div>
            </div>
          </div>
        </div>

      </template>
    </template>

    <!-- ── Create Signal Modal ───────────────────────────────── -->
    <div v-if="showCreateModal" class="evo-modal-overlay" @click.self="showCreateModal = false">
      <div class="evo-modal">
        <h3 class="evo-modal-title">＋ 新建进化信号</h3>
        <p class="evo-modal-desc">初始化一个新的信号 memory 目录。创建后可使用 CLI 启动进化循环：<br><code class="evo-code-inline-sm">python -m evolution.cli run &lt;name&gt;</code></p>
        <div class="evo-form-field">
          <label class="evo-form-label">信号名称 <span class="evo-form-hint">（字母开头，仅字母/数字/下划线）</span></label>
          <input
            v-model="createName"
            class="evo-input"
            placeholder="例如: volume_reversal_v2"
            @keyup.enter="doCreate"
            :class="{ 'input-error': createNameError }"
          />
          <div v-if="createNameError" class="evo-input-error-msg">{{ createNameError }}</div>
        </div>
        <div class="evo-form-field">
          <label class="evo-form-label">初始研究方向 <span class="evo-form-hint">（可选）</span></label>
          <textarea
            v-model="createDirection"
            class="evo-textarea"
            placeholder="例如：探索价量背离信号，关注缩量反弹形态..."
            rows="3"
          ></textarea>
        </div>
        <div class="evo-modal-actions">
          <button class="evo-btn evo-btn-ghost" @click="showCreateModal = false; createName = ''; createDirection = ''; createNameError = ''">取消</button>
          <button class="evo-btn evo-btn-accent" @click="doCreate" :disabled="!createName.trim() || creating">
            {{ creating ? '创建中…' : '创建' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ── Inject Modal ────────────────────────────────────────── -->
    <div v-if="showInjectModal" class="evo-modal-overlay" @click.self="showInjectModal = false">
      <div class="evo-modal">
        <h3 class="evo-modal-title">注入研究方向</h3>
        <p class="evo-modal-desc">这条提示将在下次迭代时传递给 Agent，引导其改进方向。</p>
        <textarea
          v-model="injectText"
          class="evo-textarea"
          placeholder="例如：尝试在小盘股上加入成交量过滤，关注缩量反弹..."
          rows="4"
        ></textarea>
        <div class="evo-modal-actions">
          <button class="evo-btn evo-btn-ghost" @click="showInjectModal = false">取消</button>
          <button class="evo-btn evo-btn-accent" @click="doInject" :disabled="!injectText.trim()">注入</button>
        </div>
      </div>
    </div>

    <!-- ── Code Modal ─────────────────────────────────────────── -->
    <div v-if="codeModalOpen" class="evo-modal-overlay" @click.self="codeModalOpen = false">
      <div class="evo-modal evo-modal-wide">
        <div class="evo-modal-header">
          <h3 class="evo-modal-title">iter_{{ String(codeModalIter).padStart(3,'0') }}.py · {{ selectedSignal }}</h3>
          <button class="evo-btn evo-btn-ghost" @click="codeModalOpen = false">✕</button>
        </div>
        <pre class="evo-pre evo-pre-modal"><code>{{ codeModalSource }}</code></pre>
      </div>
    </div>

    <!-- ── Toast ──────────────────────────────────────────────── -->
    <transition name="toast">
      <div v-if="toast" class="evo-toast" :class="`toast-${toast.type}`">
        {{ toast.message }}
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'

// ── Types ──────────────────────────────────────────────────────────────────────

interface Signal {
  name: string
  exists: boolean
  current_iter: number
  best_score: number | null
  best_iteration: number
  summary_lines: number
  pending_inject: boolean
  stop_pending: boolean
  hypothesis: string
  sharpe_t5: number | null
  ic_t5: number | null
  win_rate_t5: number | null
  coverage: number | null
}

interface IterRecord {
  iteration: number
  timestamp: string
  hypothesis: string
  conclusion: string
  code_path: string
  best_params: Record<string, unknown>
  valid: {
    primary: number | null
    sharpe_t3: number | null
    sharpe_t5: number | null
    sharpe_t10: number | null
    sharpe_t20: number | null
    ic_t5: number | null
    win_rate_t5: number | null
    coverage: number | null
    n_obs: number | null
  }
  train: {
    primary: number | null
    sharpe_t5: number | null
  }
}

// ── State ──────────────────────────────────────────────────────────────────────

const loading          = ref(false)
const signals          = ref<Signal[]>([])
const selectedSignal   = ref<string | null>(null)
const activeTab        = ref('curve')

const history          = ref<IterRecord[]>([])
const memoryData       = ref<any>(null)
const memoryLoading    = ref(false)
const testData         = ref<any>(null)
const testLoading      = ref(false)

const diffV1           = ref<number>(0)
const diffV2           = ref<number>(0)
const diffLines        = ref<string[]>([])
const diffStats        = ref<{ adds: number; removes: number } | null>(null)
const singleCode       = ref<string>('')
const codeLoading      = ref(false)

const showInjectModal  = ref(false)
const injectText       = ref('')

const showCreateModal  = ref(false)
const createName       = ref('')
const createDirection  = ref('')
const createNameError  = ref('')
const creating         = ref(false)

const autoRefresh      = ref(false)
let   autoRefreshTimer: ReturnType<typeof setInterval> | null = null

const codeModalOpen    = ref(false)
const codeModalIter    = ref(0)
const codeModalSource  = ref('')

const toast            = ref<{ message: string; type: 'success' | 'error' } | null>(null)
let   toastTimer: ReturnType<typeof setTimeout> | null = null

const chartCanvas      = ref<HTMLCanvasElement | null>(null)
let   chartInstance: any = null

// ── Tabs ───────────────────────────────────────────────────────────────────────

const tabs = [
  { key: 'curve',   label: '进化曲线' },
  { key: 'history', label: '迭代详情' },
  { key: 'code',    label: '代码查看' },
  { key: 'memory',  label: '记忆摘要' },
  { key: 'test',    label: 'Test 集评估' },
]

// ── Computed ───────────────────────────────────────────────────────────────────

const bestIter = computed(() => {
  if (!history.value.length) return -1
  let best = -Infinity, bestN = -1
  for (const r of history.value) {
    const s = r.valid?.sharpe_t5 ?? -Infinity
    if (s > best) { best = s; bestN = r.iteration }
  }
  return bestN
})

const codeIters = computed(() => {
  if (!memoryData.value?.code_iters?.length) {
    return history.value.map(r => r.iteration)
  }
  return memoryData.value.code_iters as number[]
})

const summaryMetrics = computed(() => {
  if (!history.value.length) return []
  const best   = history.value.find(r => r.iteration === bestIter.value)
  const latest = history.value[history.value.length - 1]
  const vm     = best?.valid ?? {}
  return [
    { label: '最佳 Sharpe (T+5)',  value: fmtScore(vm?.sharpe_t5),   color: scoreClass(vm?.sharpe_t5) },
    { label: '最佳 IC (T+5)',      value: fmtScore(vm?.ic_t5),        color: '' },
    { label: '最佳胜率',           value: fmtPct(vm?.win_rate_t5),    color: '' },
    { label: '最佳覆盖率',         value: fmtPct(vm?.coverage),       color: '' },
    { label: '总迭代次数',         value: String(history.value.length), color: '' },
    { label: '最新 Sharpe',        value: fmtScore(latest?.valid?.sharpe_t5), color: scoreClass(latest?.valid?.sharpe_t5) },
  ]
})

const bestMetricsDisplay = computed(() => {
  const br = memoryData.value?.best_result
  if (!br) return {}
  const vm = br.valid_metrics || {}
  return {
    '轮次':        br.iteration ?? '—',
    'Sharpe T+5':  fmtScore(vm.sharpe_t5),
    'IC T+5':      fmtScore(vm.ic_t5),
    '胜率':        fmtPct(vm.win_rate_t5),
    '覆盖率':      fmtPct(vm.coverage),
    '假设':        (br.hypothesis || '—').slice(0, 100),
  }
})

const l2SummaryDisplay = computed(() => {
  const s = memoryData.value?.l2_feedback?.summary
  if (!s) return {}
  const fmt = (v: any, pct = false) =>
    v == null ? '—' : pct ? `${(v * 100).toFixed(2)}%` : Number(v).toFixed(4)
  return {
    '总收益':   fmt(s.total_return, true),
    '年化收益': fmt(s.annual_return, true),
    'Sharpe':   fmt(s.sharpe_ratio),
    '最大回撤': fmt(s.max_drawdown, true),
    '胜率':     fmt(s.win_rate, true),
  }
})

const testMetrics = computed(() => {
  const d = testData.value?.data
  if (!d) return []
  const l1 = d.l1_metrics || {}
  const l2s = d.l2_backtest?.summary || {}
  const fmt = (v: any) => fmtScore(v)
  const fmtP = (v: any) => v == null ? '—' : `${(v * 100).toFixed(2)}%`
  return [
    { label: 'T+5 Sharpe (L1)',   value: fmt(l1.primary || l1.sharpe_t5), color: scoreClass(l1.primary || l1.sharpe_t5) },
    { label: 'IC T+5',            value: fmt(l1.ic_t5),     color: '' },
    { label: '胜率 T+5',          value: fmtP(l1.win_rate_t5), color: '' },
    { label: '覆盖率',            value: fmtP(l1.coverage), color: '' },
    { label: 'L2 总收益',         value: fmtP(l2s.total_return), color: '' },
    { label: 'L2 年化收益',       value: fmtP(l2s.annual_return), color: '' },
    { label: 'L2 Sharpe',         value: fmt(l2s.sharpe_ratio), color: scoreClass(l2s.sharpe_ratio) },
    { label: 'L2 最大回撤',       value: fmtP(l2s.max_drawdown), color: '' },
  ]
})

// ── Formatters ─────────────────────────────────────────────────────────────────

function fmtScore(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—'
  return v.toFixed(4)
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—'
  return `${(v * 100).toFixed(1)}%`
}

function scoreClass(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return ''
  if (v >= 1.5) return 'score-excellent'
  if (v >= 0.8) return 'score-good'
  if (v >= 0.3) return 'score-fair'
  return 'score-poor'
}

function overfitClass(iter: IterRecord): string {
  const tr = iter.train?.sharpe_t5
  const vl = iter.valid?.sharpe_t5
  if (tr == null || vl == null) return ''
  return tr - vl > 1.0 ? 'overfit-warn' : ''
}

function diffLineClass(line: string): string {
  if (line.startsWith('+') && !line.startsWith('+++')) return 'diff-add-line'
  if (line.startsWith('-') && !line.startsWith('---')) return 'diff-rem-line'
  if (line.startsWith('@@')) return 'diff-hunk-line'
  return ''
}

function capBarStyle(v: number | null): Record<string, string> {
  const val = Math.max(0, Math.min(v ?? 0, 3))
  return {
    width:      `${(val / 3) * 100}%`,
    background: val >= 1.5 ? '#22c55e' : val >= 0.8 ? '#3b82f6' : '#f59e0b',
  }
}

// ── API calls ──────────────────────────────────────────────────────────────────

async function fetchSignals() {
  loading.value = true
  try {
    const r = await fetch('/api/evolution/list')
    const d = await r.json()
    signals.value = d.signals || []
  } catch (e) {
    showToast('加载信号列表失败', 'error')
  } finally {
    loading.value = false
  }
}

async function fetchHistory(name: string) {
  try {
    const r = await fetch(`/api/evolution/${name}/history`)
    const d = await r.json()
    history.value = d.iterations || []
    await nextTick()
    renderChart()
  } catch (e) {
    history.value = []
  }
}

async function fetchMemory(name: string) {
  memoryLoading.value = true
  try {
    const r = await fetch(`/api/evolution/${name}/memory`)
    memoryData.value = await r.json()
    // Set diff defaults to last two iters
    const iters = memoryData.value?.code_iters || []
    if (iters.length >= 2) {
      diffV1.value = iters[iters.length - 2]
      diffV2.value = iters[iters.length - 1]
    } else if (iters.length === 1) {
      diffV1.value = iters[0]
      diffV2.value = iters[0]
    }
  } catch (e) {
    memoryData.value = null
  } finally {
    memoryLoading.value = false
  }
}

async function fetchTestResults(name: string) {
  testLoading.value = true
  try {
    const r = await fetch(`/api/evolution/${name}/test_results`)
    testData.value = await r.json()
  } catch (e) {
    testData.value = null
  } finally {
    testLoading.value = false
  }
}

async function loadDiff() {
  if (diffV1.value === diffV2.value) {
    showToast('请选择不同的两个版本', 'error')
    return
  }
  codeLoading.value = true
  singleCode.value  = ''
  diffLines.value   = []
  diffStats.value   = null
  try {
    const r = await fetch(`/api/evolution/${selectedSignal.value}/diff/${diffV1.value}/${diffV2.value}`)
    const d = await r.json()
    if (d.error) { showToast(d.error, 'error'); return }
    diffLines.value  = d.diff_lines || []
    diffStats.value  = { adds: d.adds, removes: d.removes }
    if (d.identical) showToast('两个版本代码完全一致', 'success')
  } catch (e) {
    showToast('加载 diff 失败', 'error')
  } finally {
    codeLoading.value = false
  }
}

async function loadSingleCode(iter: number) {
  codeLoading.value = true
  diffLines.value   = []
  diffStats.value   = null
  singleCode.value  = ''
  try {
    const r = await fetch(`/api/evolution/${selectedSignal.value}/code/${iter}`)
    const d = await r.json()
    if (d.error) { showToast(d.error, 'error'); return }
    singleCode.value = d.source || ''
  } catch (e) {
    showToast('加载代码失败', 'error')
  } finally {
    codeLoading.value = false
  }
}

async function viewCode(iter: number) {
  try {
    const r = await fetch(`/api/evolution/${selectedSignal.value}/code/${iter}`)
    const d = await r.json()
    if (d.error) { showToast(d.error, 'error'); return }
    codeModalIter.value   = iter
    codeModalSource.value = d.source || ''
    codeModalOpen.value   = true
  } catch (e) {
    showToast('加载代码失败', 'error')
  }
}

async function doInject() {
  const text = injectText.value.trim()
  if (!text || !selectedSignal.value) return
  try {
    const r = await fetch(`/api/evolution/${selectedSignal.value}/inject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ direction: text }),
    })
    const d = await r.json()
    if (d.success) {
      showToast('方向已注入，将在下次迭代中生效', 'success')
      injectText.value  = ''
      showInjectModal.value = false
      await fetchSignals()
    } else {
      showToast(d.error || '注入失败', 'error')
    }
  } catch (e) {
    showToast('注入请求失败', 'error')
  }
}

async function doCreate() {
  const name = createName.value.trim()
  createNameError.value = ''
  if (!name) return
  if (!/^[a-zA-Z][a-zA-Z0-9_]{0,63}$/.test(name)) {
    createNameError.value = '名称只允许字母/数字/下划线，须以字母开头，最长64字符'
    return
  }
  creating.value = true
  try {
    const r = await fetch('/api/evolution/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, direction: createDirection.value.trim() }),
    })
    const d = await r.json()
    if (d.success) {
      showToast(`信号 ${name} 已创建`, 'success')
      showCreateModal.value = false
      createName.value = ''
      createDirection.value = ''
      await fetchSignals()
    } else {
      createNameError.value = d.error || '创建失败'
    }
  } catch (e) {
    createNameError.value = '请求失败，请检查后端连接'
  } finally {
    creating.value = false
  }
}

async function promoteSignal() {
  if (!selectedSignal.value) return
  if (!confirm(`确认将 ${selectedSignal.value} 的最佳代码推广到 signals/ 目录？`)) return
  try {
    const r = await fetch(`/api/evolution/${selectedSignal.value}/promote`, { method: 'POST' })
    const d = await r.json()
    if (d.success) {
      showToast(`🚀 ${d.message}`, 'success')
    } else {
      showToast(d.error || '推广失败', 'error')
    }
  } catch (e) {
    showToast('推广请求失败', 'error')
  }
}

function toggleAutoRefresh() {
  autoRefresh.value = !autoRefresh.value
  if (autoRefresh.value) {
    autoRefreshTimer = setInterval(async () => {
      await fetchSignals()
      if (selectedSignal.value) {
        await fetchHistory(selectedSignal.value)
      }
    }, 15000) // every 15 s
    showToast('自动刷新已开启（15s）', 'success')
  } else {
    if (autoRefreshTimer) { clearInterval(autoRefreshTimer); autoRefreshTimer = null }
    showToast('自动刷新已关闭', 'success')
  }
}

async function stopSignal() {
  if (!selectedSignal.value) return
  if (!confirm(`确认停止 ${selectedSignal.value} 的进化循环？`)) return
  try {
    const r = await fetch(`/api/evolution/${selectedSignal.value}/stop`, { method: 'POST' })
    const d = await r.json()
    if (d.success) {
      showToast('停止信号已发送，当前轮次完成后退出', 'success')
      await fetchSignals()
    } else {
      showToast(d.error || '停止失败', 'error')
    }
  } catch (e) {
    showToast('停止请求失败', 'error')
  }
}

// ── Signal selection ───────────────────────────────────────────────────────────

async function selectSignal(name: string) {
  selectedSignal.value = name
  activeTab.value      = 'curve'
  diffLines.value      = []
  singleCode.value     = ''
  diffStats.value      = null
  history.value        = []
  memoryData.value     = null
  testData.value       = null
  await fetchHistory(name)
  await fetchMemory(name)
}

watch(activeTab, (tab) => {
  if (tab === 'test' && selectedSignal.value && !testData.value) {
    fetchTestResults(selectedSignal.value)
  }
  if (tab === 'curve') {
    nextTick(renderChart)
  }
})

async function refreshAll() {
  await fetchSignals()
  if (selectedSignal.value) {
    await fetchHistory(selectedSignal.value)
    await fetchMemory(selectedSignal.value)
  }
}

// ── Chart ──────────────────────────────────────────────────────────────────────

function renderChart() {
  if (!chartCanvas.value || !history.value.length) return

  // Dynamically load Chart.js from CDN if not available
  if (typeof (window as any).Chart === 'undefined') {
    const script = document.createElement('script')
    script.src = 'https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js'
    script.onload = () => buildChart()
    document.head.appendChild(script)
  } else {
    buildChart()
  }
}

function buildChart() {
  const Chart = (window as any).Chart
  if (!Chart || !chartCanvas.value) return

  if (chartInstance) {
    chartInstance.destroy()
    chartInstance = null
  }

  const iters  = history.value.map(r => `Iter ${r.iteration}`)
  const sharpe = history.value.map(r => r.valid?.sharpe_t5 ?? null)
  const ic     = history.value.map(r => (r.valid?.ic_t5 ?? null) !== null ? (r.valid.ic_t5! * 10) : null)
  const train  = history.value.map(r => r.train?.sharpe_t5 ?? null)

  chartInstance = new Chart(chartCanvas.value, {
    type: 'line',
    data: {
      labels: iters,
      datasets: [
        {
          label: 'Valid T+5 Sharpe',
          data: sharpe,
          borderColor: '#818cf8',
          backgroundColor: 'rgba(129,140,248,0.15)',
          tension: 0.3,
          pointRadius: 4,
          pointHoverRadius: 7,
          fill: true,
          spanGaps: true,
        },
        {
          label: 'Train Sharpe',
          data: train,
          borderColor: '#f59e0b',
          backgroundColor: 'transparent',
          tension: 0.3,
          pointRadius: 3,
          borderDash: [6, 3],
          fill: false,
          spanGaps: true,
        },
        {
          label: 'IC×10 (T+5)',
          data: ic,
          borderColor: '#34d399',
          backgroundColor: 'rgba(52,211,153,0.1)',
          tension: 0.3,
          pointRadius: 3,
          borderDash: [4, 4],
          fill: false,
          spanGaps: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: '#94a3b8', font: { family: 'monospace', size: 12 } } },
        tooltip: {
          backgroundColor: '#1e293b',
          titleColor: '#e2e8f0',
          bodyColor: '#94a3b8',
          borderColor: '#334155',
          borderWidth: 1,
        },
      },
      scales: {
        x: {
          ticks: { color: '#64748b', maxTicksLimit: 15 },
          grid:  { color: 'rgba(51,65,85,0.5)' },
        },
        y: {
          ticks: { color: '#64748b' },
          grid:  { color: 'rgba(51,65,85,0.5)' },
        },
      },
    },
  })
}

// ── Toast ──────────────────────────────────────────────────────────────────────

function showToast(message: string, type: 'success' | 'error' = 'success') {
  toast.value = { message, type }
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.value = null }, 3200)
}

// ── Lifecycle ──────────────────────────────────────────────────────────────────

onMounted(() => { fetchSignals() })

onUnmounted(() => {
  if (chartInstance) chartInstance.destroy()
  if (toastTimer) clearTimeout(toastTimer)
  if (autoRefreshTimer) clearInterval(autoRefreshTimer)
})
</script>

<style scoped>
/* ── Root ──────────────────────────────────────────────────────────────────── */
.evo-root {
  min-height: 100vh;
  background: #0f172a;
  color: #e2e8f0;
  padding: 24px 32px 60px;
  font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', monospace;
}

/* ── Header ──────────────────────────────────────────────────────────────── */
.evo-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 32px;
  padding-bottom: 20px;
  border-bottom: 1px solid #1e293b;
}
.evo-header-left { display: flex; align-items: center; gap: 16px; }
.evo-logo { display: flex; align-items: center; gap: 14px; }
.evo-logo-icon {
  font-size: 32px;
  color: #818cf8;
  animation: rotateSlow 8s linear infinite;
}
@keyframes rotateSlow { to { transform: rotate(360deg); } }
.evo-title {
  font-size: 22px;
  font-weight: 700;
  color: #f1f5f9;
  margin: 0;
  letter-spacing: -0.3px;
}
.evo-subtitle {
  font-size: 11px;
  color: #475569;
  margin: 2px 0 0;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

/* ── Buttons ─────────────────────────────────────────────────────────────── */
.evo-btn {
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  font-family: inherit;
  padding: 7px 14px;
  transition: all 0.15s;
}
.evo-btn-ghost {
  background: #1e293b;
  color: #94a3b8;
  border: 1px solid #334155;
}
.evo-btn-ghost:hover { background: #263249; color: #e2e8f0; }
.evo-btn-accent { background: #4f46e5; color: #fff; }
.evo-btn-accent:hover { background: #4338ca; }
.evo-btn-accent:disabled { opacity: 0.4; cursor: default; }
.evo-btn-danger { background: #7f1d1d; color: #fca5a5; border: 1px solid #991b1b; }
.evo-btn-danger:hover { background: #991b1b; }
.evo-btn-sm { padding: 5px 10px; font-size: 12px; background: #1e293b; color: #94a3b8; border: 1px solid #334155; }
.evo-btn-sm:hover { background: #263249; }
.evo-btn-xs { padding: 2px 8px; font-size: 11px; background: #1e293b; color: #818cf8; border: 1px solid #334155; border-radius: 4px; }
.evo-btn-xs:hover { background: #1d2a4a; }

/* ── Loading / Empty ─────────────────────────────────────────────────────── */
.evo-loading, .evo-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 80px 0;
  color: #475569;
}
.evo-spinner {
  width: 36px; height: 36px;
  border: 3px solid #1e293b;
  border-top-color: #818cf8;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.evo-empty-icon { font-size: 48px; opacity: 0.4; }
.evo-empty h3 { font-size: 18px; color: #64748b; margin: 0; }
.evo-empty p  { font-size: 13px; margin: 0; }
.evo-code-inline {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 6px;
  padding: 8px 14px;
  font-size: 13px;
  color: #818cf8;
}

/* ── Section label ───────────────────────────────────────────────────────── */
.evo-section-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 1.2px;
  color: #475569;
  margin-bottom: 14px;
}

/* ── Signal grid ─────────────────────────────────────────────────────────── */
.evo-signal-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 14px;
  margin-bottom: 32px;
}
.evo-signal-card {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 10px;
  padding: 16px;
  cursor: pointer;
  transition: all 0.18s;
  position: relative;
}
.evo-signal-card:hover { border-color: #4f46e5; background: #1d2a4a; }
.evo-signal-card.active {
  border-color: #818cf8;
  background: #1d2a4a;
  box-shadow: 0 0 0 1px #818cf8;
}
.evo-signal-card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.evo-signal-name { font-size: 14px; font-weight: 600; color: #f1f5f9; }
.evo-badges { display: flex; gap: 4px; }
.evo-badge {
  font-size: 10px;
  padding: 2px 7px;
  border-radius: 20px;
  font-weight: 600;
  letter-spacing: 0.3px;
}
.badge-active { background: #064e3b; color: #6ee7b7; border: 1px solid #065f46; }
.badge-new    { background: #1e3a5f; color: #93c5fd; border: 1px solid #1e40af; }
.badge-stop   { background: #450a0a; color: #fca5a5; border: 1px solid #7f1d1d; }
.badge-inject { background: #451a03; color: #fdba74; border: 1px solid #7c2d12; }

.evo-signal-score {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 8px;
}
.evo-score-value { font-size: 24px; font-weight: 700; color: #818cf8; }
.evo-score-label { font-size: 11px; color: #475569; }
.evo-signal-meta {
  display: flex;
  gap: 10px;
  font-size: 11px;
  color: #475569;
  margin-bottom: 8px;
}
.evo-signal-hyp {
  font-size: 11px;
  color: #64748b;
  font-style: italic;
  line-height: 1.4;
  border-top: 1px solid #1e293b;
  padding-top: 8px;
}

/* ── Detail header ───────────────────────────────────────────────────────── */
.evo-detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 20px;
  flex-wrap: wrap;
  gap: 12px;
}
.evo-detail-title { display: flex; flex-direction: column; gap: 12px; }
.evo-detail-name { font-size: 20px; font-weight: 700; color: #818cf8; }
.evo-detail-actions { display: flex; gap: 8px; align-items: center; }

/* ── Tabs ────────────────────────────────────────────────────────────────── */
.evo-tab-bar { display: flex; gap: 2px; }
.evo-tab {
  background: none;
  border: 1px solid transparent;
  border-radius: 6px 6px 0 0;
  color: #64748b;
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  padding: 7px 16px;
  transition: all 0.15s;
}
.evo-tab:hover { color: #94a3b8; border-color: #334155; }
.evo-tab-active {
  background: #1e293b;
  border-color: #334155;
  border-bottom-color: #1e293b;
  color: #818cf8;
  font-weight: 600;
}

/* ── Panel ───────────────────────────────────────────────────────────────── */
.evo-panel {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 0 10px 10px 10px;
  padding: 24px;
  margin-bottom: 32px;
  min-height: 200px;
}
.evo-panel-empty {
  color: #475569;
  font-size: 14px;
  text-align: center;
  padding: 40px 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: center;
}

/* ── Chart ───────────────────────────────────────────────────────────────── */
.evo-chart-container {
  height: 300px;
  margin-bottom: 24px;
  position: relative;
}
.evo-chart { width: 100% !important; height: 100% !important; }

.evo-metrics-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
}
.evo-metric-box {
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 12px 14px;
  text-align: center;
}
.evo-metric-val { font-size: 20px; font-weight: 700; margin-bottom: 4px; }
.evo-metric-label { font-size: 11px; color: #475569; }

/* ── Score colors ────────────────────────────────────────────────────────── */
.score-excellent { color: #22c55e; }
.score-good      { color: #818cf8; }
.score-fair      { color: #f59e0b; }
.score-poor      { color: #ef4444; }

/* ── Table ───────────────────────────────────────────────────────────────── */
.evo-table-wrap { overflow-x: auto; }
.evo-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.evo-table th {
  text-align: left;
  color: #64748b;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 8px 10px;
  border-bottom: 1px solid #334155;
  white-space: nowrap;
}
.evo-table td {
  padding: 9px 10px;
  border-bottom: 1px solid #1a2535;
  vertical-align: top;
}
.evo-table-row:hover td { background: #162032; }
.row-best td { background: rgba(79,70,229,0.08); }
.row-best:hover td { background: rgba(79,70,229,0.14); }
.row-failed td { opacity: 0.5; }
.evo-iter-num { font-weight: 600; color: #818cf8; white-space: nowrap; }
.evo-star { color: #fbbf24; margin-right: 3px; }
.evo-hyp-cell, .evo-concl-cell { color: #64748b; max-width: 200px; }
.overfit-warn { color: #f59e0b; }

/* ── Code viewer ─────────────────────────────────────────────────────────── */
.evo-code-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 10px;
}
.evo-code-selector { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; color: #64748b; }
.evo-select {
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 6px;
  color: #e2e8f0;
  font-family: inherit;
  font-size: 13px;
  padding: 5px 10px;
}
.evo-diff-stats { display: flex; gap: 8px; font-size: 13px; font-weight: 600; }
.diff-add { color: #22c55e; }
.diff-rem { color: #ef4444; }

.evo-pre {
  background: #0f172a;
  border: 1px solid #1e293b;
  border-radius: 8px;
  color: #94a3b8;
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 12.5px;
  line-height: 1.6;
  margin: 0;
  max-height: 480px;
  overflow: auto;
  padding: 16px 18px;
  white-space: pre;
}
.evo-pre-md { max-height: 360px; }
.evo-pre-modal { max-height: 65vh; }

.evo-diff-view {
  background: #0f172a;
  border: 1px solid #1e293b;
  border-radius: 8px;
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 12.5px;
  line-height: 1.6;
  max-height: 480px;
  overflow: auto;
  padding: 6px 0;
}
.evo-diff-line { padding: 1px 16px; white-space: pre; }
.diff-add-line  { background: rgba(34,197,94,0.1);  color: #86efac; }
.diff-rem-line  { background: rgba(239,68,68,0.1);   color: #fca5a5; }
.diff-hunk-line { background: rgba(96,165,250,0.08); color: #93c5fd; font-weight: 600; }

/* ── Memory ──────────────────────────────────────────────────────────────── */
.evo-memory-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 20px;
}
@media (max-width: 900px) { .evo-memory-grid { grid-template-columns: 1fr; } }

.evo-memory-box, .evo-summary-box {
  background: #0f172a;
  border: 1px solid #1e293b;
  border-radius: 10px;
  padding: 16px;
}
.evo-box-title { font-size: 13px; font-weight: 600; color: #94a3b8; margin: 0 0 14px; }

.evo-kv { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; padding: 5px 0; border-bottom: 1px solid #1e293b; font-size: 13px; }
.evo-kv:last-child { border-bottom: none; }
.evo-kv-key { color: #475569; flex-shrink: 0; }
.evo-kv-val { color: #e2e8f0; text-align: right; word-break: break-all; }
.evo-params-code { background: #1e293b; border-radius: 4px; color: #818cf8; font-size: 11px; padding: 2px 6px; white-space: pre; display: block; max-height: 120px; overflow: auto; }

.evo-l2-section { margin-bottom: 14px; }
.evo-l2-cap { }
.evo-cap-label { font-size: 11px; color: #475569; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
.evo-cap-bars { display: flex; flex-direction: column; gap: 6px; }
.evo-cap-bar-row { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.evo-cap-grp { width: 48px; color: #64748b; text-transform: capitalize; flex-shrink: 0; }
.evo-cap-bar-wrap { flex: 1; background: #1e293b; border-radius: 4px; height: 8px; overflow: hidden; }
.evo-cap-bar { height: 100%; border-radius: 4px; transition: width 0.4s ease; }
.evo-cap-val { width: 48px; text-align: right; color: #94a3b8; }

/* ── Test results ────────────────────────────────────────────────────────── */
.evo-test-warning {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  background: rgba(234,179,8,0.07);
  border: 1px solid rgba(234,179,8,0.25);
  border-radius: 8px;
  padding: 14px 16px;
  margin-bottom: 20px;
}
.evo-warn-icon { font-size: 20px; flex-shrink: 0; }
.evo-test-warning strong { display: block; color: #fde68a; font-size: 14px; margin-bottom: 4px; }
.evo-test-warning p { color: #92400e; font-size: 12px; margin: 0; }

.evo-test-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
}
.evo-test-box {
  background: #0f172a;
  border: 1px solid #1e293b;
  border-radius: 8px;
  padding: 14px;
  text-align: center;
}
.evo-test-val { font-size: 22px; font-weight: 700; margin-bottom: 5px; }
.evo-test-label { font-size: 11px; color: #475569; }

/* ── Inject modal ────────────────────────────────────────────────────────── */
.evo-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.65);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(4px);
}
.evo-modal {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 14px;
  max-width: 520px;
  width: 90%;
  padding: 28px;
}
.evo-modal-wide { max-width: 860px; }
.evo-modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.evo-modal-title { font-size: 16px; font-weight: 700; color: #f1f5f9; margin: 0 0 12px; }
.evo-modal-desc  { font-size: 13px; color: #64748b; margin: 0 0 16px; line-height: 1.6; }
.evo-textarea {
  width: 100%;
  box-sizing: border-box;
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 8px;
  color: #e2e8f0;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.6;
  padding: 12px;
  resize: vertical;
}
.evo-textarea:focus { outline: none; border-color: #818cf8; }
.evo-modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}

/* ── Toast ───────────────────────────────────────────────────────────────── */
.evo-toast {
  position: fixed;
  bottom: 28px;
  right: 28px;
  padding: 12px 20px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  z-index: 2000;
  pointer-events: none;
}
.toast-success { background: #064e3b; color: #6ee7b7; border: 1px solid #065f46; }
.toast-error   { background: #450a0a; color: #fca5a5; border: 1px solid #7f1d1d; }
.toast-enter-active, .toast-leave-active { transition: all 0.25s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(10px); }

/* ── Spin utility ────────────────────────────────────────────────────────── */
.spin { display: inline-block; animation: spin 0.8s linear infinite; }

/* ── Auto-refresh button active state ───────────────────────────────────── */
.btn-auto-active {
  background: #064e3b;
  color: #6ee7b7;
  border-color: #065f46;
}
.btn-auto-active:hover { background: #065f46; }

/* ── Create modal form ───────────────────────────────────────────────────── */
.evo-form-field { margin-bottom: 16px; }
.evo-form-label { display: block; font-size: 12px; font-weight: 600; color: #94a3b8; margin-bottom: 6px; }
.evo-form-hint  { font-weight: 400; color: #475569; }
.evo-input {
  width: 100%;
  box-sizing: border-box;
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 8px;
  color: #e2e8f0;
  font-family: inherit;
  font-size: 13px;
  padding: 9px 12px;
  transition: border-color 0.15s;
}
.evo-input:focus { outline: none; border-color: #818cf8; }
.evo-input.input-error { border-color: #ef4444; }
.evo-input-error-msg { font-size: 11px; color: #ef4444; margin-top: 5px; }
.evo-code-inline-sm {
  display: inline;
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 4px;
  padding: 2px 7px;
  font-size: 12px;
  color: #818cf8;
  font-family: 'SF Mono', 'Fira Code', monospace;
}
</style>

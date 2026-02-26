import { useState, useRef, useEffect } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, KnownFailureHistory, BuildHistoryEntry, KnownFailure, KnownFailureInstance, FailuresByBuild, HistoryStatus } from '../api/client'
import LogSnippet from './LogSnippet'

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return ''
  const now = Date.now()
  const then = new Date(dateStr).getTime()
  const diffMs = now - then
  if (diffMs < 0) return 'just now'
  const minutes = Math.floor(diffMs / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d ago`
  const weeks = Math.floor(days / 7)
  return `${weeks}w ago`
}

const STATUS_CONFIG: Record<string, { color: string; label: string; textColor: string }> = {
  not_run: { color: 'bg-gray-600', label: 'Not Run', textColor: 'text-gray-500' },
  job_fail: { color: 'bg-yellow-500', label: 'Job Failed', textColor: 'text-yellow-400' },
  infra_fail: { color: 'bg-orange-500', label: 'Infra', textColor: 'text-orange-400' },
  other_fail: { color: 'bg-green-700', label: 'Other Fail', textColor: 'text-green-300' },
  diff_fail: { color: 'bg-purple-600', label: 'Diff Error', textColor: 'text-purple-400' },
  fail: { color: 'bg-red-500', label: 'Failed', textColor: 'text-red-400' },
  pass: { color: 'bg-green-500', label: 'Passed', textColor: 'text-green-400' },
  flaky_pass: { color: 'bg-blue-400', label: 'Flaky Pass', textColor: 'text-blue-400' },
}

function StatusDot({ status, size = 'sm' }: { status: HistoryStatus; size?: 'sm' | 'md' }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.not_run
  const sizeClass = size === 'md' ? 'w-3 h-3' : 'w-2.5 h-2.5'
  return (
    <span
      className={`inline-block ${sizeClass} rounded-full ${cfg.color} flex-shrink-0`}
      title={cfg.label}
    />
  )
}

function BuildPill({ build }: { build: { build_number: number; build_url: string | null; build_type: string | null; status: HistoryStatus } }) {
  const cfg = STATUS_CONFIG[build.status] || STATUS_CONFIG.not_run
  return (
    <a
      href={build.build_url || '#'}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()}
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-700 hover:bg-gray-600 text-xs"
      title={`#${build.build_number} - ${cfg.label}${build.build_type ? ` (${build.build_type})` : ''}`}
    >
      <span className={`inline-block w-2 h-2 rounded-full ${cfg.color}`} />
      <span className="text-gray-300 font-mono">#{build.build_number}</span>
      {build.build_type && (
        <span className="text-gray-500">{build.build_type}</span>
      )}
    </a>
  )
}


function HistoryEntry({ entry, onTriageCommit, triageState }: {
  entry: BuildHistoryEntry
  onTriageCommit: (sha: string) => void
  triageState: string | undefined  // 'running' | 'synced' | 'triaged' | 'no_builds' | 'error:...' | undefined
}) {
  const [expanded, setExpanded] = useState(false)
  const hasDetails = (entry.status === 'fail' || entry.status === 'flaky_pass') && entry.failures.length > 0
  const needsTriage = !entry.triaged && entry.commit_sha
  const isRunning = triageState === 'running'
  const hasResult = triageState && triageState !== 'running'

  // When triage completes, override display status based on result
  const displayStatus: HistoryStatus = hasResult
    ? (triageState === 'synced' ? 'pass' : triageState === 'no_builds' ? 'not_run' : entry.status)
    : entry.status
  const cfg = STATUS_CONFIG[displayStatus] || STATUS_CONFIG.not_run

  // Determine status label
  let statusLabel = cfg.label
  if (isRunning) statusLabel = 'Triaging'
  else if (hasResult && triageState === 'synced') statusLabel = 'Passed'
  else if (hasResult && triageState === 'triaged') statusLabel = 'Triaged'
  else if (hasResult && triageState === 'no_builds') statusLabel = 'No CI builds'
  else if (hasResult && triageState?.startsWith('error:')) statusLabel = 'Error'
  else if (needsTriage) statusLabel = 'Not Triaged'

  return (
    <div className="border-b border-gray-700/50 last:border-b-0">
      <div
        className={`flex items-center gap-3 py-2 px-4 ${hasDetails ? 'cursor-pointer hover:bg-gray-750' : ''}`}
        onClick={() => hasDetails && setExpanded(!expanded)}
      >
        {hasDetails && (
          <span className="text-gray-500 w-4 text-xs flex-shrink-0">
            {expanded ? '▼' : '▶'}
          </span>
        )}
        {!hasDetails && <span className="w-4 flex-shrink-0" />}

        <StatusDot status={displayStatus} size="md" />

        {entry.commit_sha && (
          <a
            href={`https://github.com/vllm-project/vllm/commit/${entry.commit_sha}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-400 hover:underline font-mono text-sm flex-shrink-0"
            onClick={(e) => e.stopPropagation()}
          >
            {entry.commit_sha.slice(0, 8)}
          </a>
        )}

        <span className={`text-xs ${cfg.textColor} flex-shrink-0`}>
          {statusLabel}
        </span>

        {/* Build pills - show all builds for this commit */}
        <div className="flex items-center gap-1 flex-shrink-0">
          {entry.builds.map(b => (
            <BuildPill key={b.build_number} build={b} />
          ))}
        </div>

        {entry.message && (
          <span className="text-gray-500 text-xs truncate min-w-0">
            {entry.message.split('\n')[0].slice(0, 80)}
          </span>
        )}

        <div className="flex items-center gap-2 flex-shrink-0 ml-auto">
          {isRunning && (
            <span className="px-2 py-0.5 bg-blue-900 text-blue-300 text-xs rounded animate-pulse">
              Triaging...
            </span>
          )}
          {needsTriage && !triageState && (
            <button
              onClick={(e) => {
                e.stopPropagation()
                onTriageCommit(entry.commit_sha!)
              }}
              className="px-2 py-0.5 bg-blue-700 hover:bg-blue-600 text-blue-100 text-xs rounded transition-colors"
            >
              Triage
            </button>
          )}
          <div className="flex flex-col items-end">
            {entry.committed_at && (
              <span className="text-gray-500 text-xs">
                {timeAgo(entry.committed_at)}
              </span>
            )}
            {entry.created_at && (
              <span className="text-gray-600 text-xs">
                triaged {timeAgo(entry.created_at)}
              </span>
            )}
          </div>
        </div>
      </div>

      {expanded && entry.failures.length > 0 && (
        <div className="bg-gray-850 px-12 py-2 space-y-1">
          {entry.failures.map((f) => {
            const tests = Array.isArray(f.failing_test) ? f.failing_test : (f.failing_test ? [f.failing_test] : [])
            return (
              <div key={f.failure_id} className="flex items-center gap-2 text-xs">
                <a
                  href={f.job_url || '#'}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-gray-400 hover:text-blue-400 truncate max-w-[200px]"
                  onClick={(e) => e.stopPropagation()}
                >
                  {f.job_name}
                </a>
                {tests.length > 0 && (
                  <>
                    <span className="text-gray-600">&rarr;</span>
                    <span className="text-red-400 font-mono truncate">{tests[0]}</span>
                    {tests.length > 1 && (
                      <span className="text-gray-500">+{tests.length - 1} more</span>
                    )}
                  </>
                )}
                {f.error_message && (
                  <span className="text-gray-500 truncate ml-2" title={f.error_message}>
                    {f.error_message.slice(0, 80)}
                  </span>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function getLogUrl(instance: KnownFailureInstance): string | null {
  if (instance.log_excerpt) {
    const match = instance.log_excerpt.match(/\[View in Buildkite at line \d+\]\(([^)]+)\)/)
    if (match) return match[1]
  }
  return instance.job_url || null
}

function FailureInstanceRow({
  instance,
  selected,
  onToggle,
  canSelect,
}: {
  instance: KnownFailureInstance
  selected: boolean
  onToggle: (failureId: number) => void
  canSelect: boolean
}) {
  const tests = Array.isArray(instance.failing_test) ? instance.failing_test : (instance.failing_test ? [instance.failing_test] : [])
  const logUrl = getLogUrl(instance)
  return (
    <div className="flex items-center gap-2 text-xs py-0.5 group">
      {canSelect && (
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggle(instance.failure_id)}
          className="w-3.5 h-3.5 rounded border-gray-600 bg-gray-700 text-orange-500 focus:ring-0 focus:ring-offset-0 cursor-pointer flex-shrink-0"
        />
      )}
      <a
        href={instance.job_url || '#'}
        target="_blank"
        rel="noopener noreferrer"
        className="text-gray-400 hover:text-blue-400 truncate max-w-[200px]"
      >
        {instance.job_name}
      </a>
      {tests.length > 0 && (
        <>
          <span className="text-gray-600">&rarr;</span>
          <span className="text-red-400 font-mono truncate" title={tests.join(', ')}>{tests[0]}</span>
          {tests.length > 1 && (
            <span className="text-gray-500">+{tests.length - 1} more</span>
          )}
        </>
      )}
      {logUrl && (
        <a
          href={logUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-gray-600 hover:text-blue-400 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity"
          title="View log in Buildkite"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
          </svg>
        </a>
      )}
    </div>
  )
}

function FailureBuildGroup({
  group,
  selectedIds,
  onToggle,
  canSelect,
}: {
  group: FailuresByBuild
  selectedIds: Set<number>
  onToggle: (failureId: number) => void
  canSelect: boolean
}) {
  return (
    <div className="pl-6 py-2 border-t border-gray-700/50">
      <div className="flex items-center gap-2 text-xs mb-1">
        <a
          href={group.build_url || '#'}
          target="_blank"
          rel="noopener noreferrer"
          className="text-blue-400 hover:underline font-mono"
        >
          #{group.build_number}
        </a>
        {group.commit_sha && (
          <a
            href={`https://github.com/vllm-project/vllm/commit/${group.commit_sha}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-gray-500 hover:text-blue-400 font-mono"
          >
            {group.commit_sha.slice(0, 8)}
          </a>
        )}
        {group.committed_at && (
          <span className="text-gray-500">{timeAgo(group.committed_at)}</span>
        )}
        {group.created_at && (
          <span className="text-gray-600">triaged {timeAgo(group.created_at)}</span>
        )}
      </div>
      {group.failures.map((instance) => (
        <FailureInstanceRow
          key={instance.failure_id}
          instance={instance}
          selected={selectedIds.has(instance.failure_id)}
          onToggle={onToggle}
          canSelect={canSelect}
        />
      ))}
    </div>
  )
}

function MergeDropdown({
  currentId,
  onSelect,
  onCancel,
}: {
  currentId: number
  onSelect: (targetId: number) => void
  onCancel: () => void
}) {
  const [search, setSearch] = useState('')
  const [isOpen, setIsOpen] = useState(true)
  const inputRef = useRef<HTMLInputElement>(null)

  const { data: allKnownFailures } = useQuery<KnownFailure[]>({
    queryKey: ['known-failures-all'],
    queryFn: () => api.knownFailures.list({ status: 'all', category: 'all' }),
  })

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const filtered = (allKnownFailures || [])
    .filter(kf => kf.id !== currentId)
    .filter(kf => {
      if (!search.trim()) return true
      const q = search.toLowerCase()
      return (
        kf.title.toLowerCase().includes(q) ||
        String(kf.id).includes(q) ||
        kf.affected_jobs.some(j => j.toLowerCase().includes(q))
      )
    })
    .slice(0, 15)

  return (
    <div className="p-2 bg-gray-900 rounded space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-400 flex-shrink-0">Merge into:</span>
        <div className="relative flex-1">
          <input
            ref={inputRef}
            type="text"
            placeholder="Search by title, ID, or job name..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setIsOpen(true) }}
            onFocus={() => setIsOpen(true)}
            className="w-full px-2 py-1 bg-gray-700 text-white rounded text-sm border border-gray-600 focus:border-blue-500 focus:outline-none"
          />
          {isOpen && (
            <div className="absolute z-10 top-full left-0 right-0 mt-1 bg-gray-800 border border-gray-600 rounded shadow-lg max-h-60 overflow-y-auto">
              {filtered.length === 0 ? (
                <div className="px-3 py-2 text-gray-500 text-xs">
                  {allKnownFailures ? 'No matching known failures' : 'Loading...'}
                </div>
              ) : (
                filtered.map(kf => (
                  <button
                    key={kf.id}
                    onClick={() => { onSelect(kf.id); setIsOpen(false) }}
                    className="w-full text-left px-3 py-1.5 hover:bg-gray-700 text-xs border-b border-gray-700/50 last:border-b-0"
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-gray-500 font-mono flex-shrink-0">#{kf.id}</span>
                      <span className={`px-1 py-0 rounded text-[10px] flex-shrink-0 ${
                        kf.status === 'open' ? 'bg-red-900 text-red-300' : 'bg-green-900 text-green-300'
                      }`}>
                        {kf.status}
                      </span>
                      <span className="text-gray-300 truncate">{kf.title}</span>
                    </div>
                    <div className="text-gray-600 mt-0.5 truncate">
                      {kf.affected_jobs.join(', ')} &middot; {kf.failure_count} instances
                    </div>
                  </button>
                ))
              )}
            </div>
          )}
        </div>
        <button
          onClick={onCancel}
          className="px-2 py-1 bg-gray-600 text-white rounded text-xs hover:bg-gray-500"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

const PAGE_SIZE = 20

export default function KnownFailureDetail() {
  const { id } = useParams<{ id: string }>()
  const knownFailureId = parseInt(id || '0', 10)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)
  const [loadingEarlier, setLoadingEarlier] = useState(false)
  // Tracks triage state per commit SHA. Persists results on the frontend
  // so they survive the server clearing the active triage after 10s.
  const [triageResults, setTriageResults] = useState<Record<string, string>>({})

  // Action state
  const [showLinkIssue, setShowLinkIssue] = useState(false)
  const [showResolve, setShowResolve] = useState(false)
  const [showCreateIssue, setShowCreateIssue] = useState(false)
  const [showMerge, setShowMerge] = useState(false)
  const [linkInput, setLinkInput] = useState('')
  const [prInput, setPrInput] = useState('')
  const [issueTitle, setIssueTitle] = useState('')
  const [issueBody, setIssueBody] = useState('')
  const [linking, setLinking] = useState(false)
  const [resolving, setResolving] = useState(false)
  const [unlinking, setUnlinking] = useState(false)
  const [creatingIssue, setCreatingIssue] = useState(false)

  // Split state
  const [splitMode, setSplitMode] = useState(false)
  const [selectedFailures, setSelectedFailures] = useState<Set<number>>(new Set())
  const [splitTitle, setSplitTitle] = useState('')
  const [splitting, setSplitting] = useState(false)

  // Edit state
  const [editingTitle, setEditingTitle] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editingSummary, setEditingSummary] = useState(false)
  const [editSummary, setEditSummary] = useState('')
  const [editingMatchPrompt, setEditingMatchPrompt] = useState(false)
  const [editMatchPrompt, setEditMatchPrompt] = useState('')
  const [saving, setSaving] = useState(false)

  // Merge state
  const [merging, setMerging] = useState(false)

  const { data: kf, isLoading: kfLoading } = useQuery<KnownFailure>({
    queryKey: ['known-failure', knownFailureId],
    queryFn: () => api.knownFailures.get(knownFailureId),
    enabled: knownFailureId > 0,
  })

  const { data: history, isLoading: historyLoading } = useQuery<KnownFailureHistory>({
    queryKey: ['known-failure-history', knownFailureId],
    queryFn: () => api.knownFailures.getHistory(knownFailureId),
    enabled: knownFailureId > 0,
  })

  // Are there any running triages?
  const hasRunningTriages = Object.values(triageResults).some(s => s === 'running')

  // Poll for active triages while any are running
  const { data: activeTriages } = useQuery<{ commits: string[]; statuses: Record<string, string> }>({
    queryKey: ['known-failure-active-triages', knownFailureId],
    queryFn: () => api.knownFailures.getActiveTriages(knownFailureId),
    enabled: knownFailureId > 0 && hasRunningTriages,
    refetchInterval: hasRunningTriages ? 3000 : false,
  })

  // Sync server statuses into local triageResults
  useEffect(() => {
    if (!activeTriages) return
    const serverStatuses = activeTriages.statuses || {}

    // Check for completions before updating state
    const completions: [string, string][] = []
    for (const [sha, status] of Object.entries(serverStatuses)) {
      if (triageResults[sha] === 'running' && status !== 'running') {
        completions.push([sha, status])
      }
    }

    if (completions.length > 0) {
      setTriageResults(prev => {
        const next = { ...prev }
        for (const [sha, status] of completions) {
          next[sha] = status
        }
        return next
      })
      // Refresh history data so the entry picks up synced builds
      queryClient.invalidateQueries({ queryKey: ['known-failure-history', knownFailureId] })
      queryClient.invalidateQueries({ queryKey: ['known-failure', knownFailureId] })
    }
  }, [activeTriages])

  const refreshData = () => {
    queryClient.invalidateQueries({ queryKey: ['known-failure', knownFailureId] })
    queryClient.invalidateQueries({ queryKey: ['known-failure-history', knownFailureId] })
    queryClient.invalidateQueries({ queryKey: ['known-failures'] })
  }

  const handleLinkIssue = async () => {
    if (!linkInput.trim()) return
    const match = linkInput.match(/(\d+)\s*$/) || linkInput.match(/issues\/(\d+)/)
    if (!match) return
    const issueNumber = parseInt(match[1], 10)
    setLinking(true)
    try {
      await api.knownFailures.linkIssue(knownFailureId, issueNumber)
      setShowLinkIssue(false)
      setLinkInput('')
      refreshData()
    } finally {
      setLinking(false)
    }
  }

  const handleUnlinkIssue = async () => {
    setUnlinking(true)
    try {
      await api.knownFailures.unlinkIssue(knownFailureId)
      refreshData()
    } finally {
      setUnlinking(false)
    }
  }

  const handleResolve = async () => {
    const prNumber = prInput.trim() ? parseInt((prInput.match(/(\d+)\s*$/) || prInput.match(/pull\/(\d+)/) || [])[1] || '0', 10) : undefined
    setResolving(true)
    try {
      await api.knownFailures.resolve(knownFailureId, prNumber || undefined)
      setShowResolve(false)
      setPrInput('')
      refreshData()
    } finally {
      setResolving(false)
    }
  }

  const handleReopen = async () => {
    await api.knownFailures.reopen(knownFailureId)
    refreshData()
  }

  const generateIssueContent = () => {
    // Collect all unique failing tests
    const allTests: string[] = []
    for (const group of kf?.failures_by_build || []) {
      for (const f of group.failures) {
        const tests = Array.isArray(f.failing_test) ? f.failing_test : (f.failing_test ? [f.failing_test] : [])
        for (const t of tests) { if (!allTests.includes(t)) allTests.push(t) }
      }
    }

    const lines: string[] = []

    // Name of failing test(s)
    lines.push(`## Name of failing test`)
    lines.push(``)
    if (allTests.length > 0) {
      for (const t of allTests.slice(0, 10)) lines.push(`- \`${t}\``)
      if (allTests.length > 10) lines.push(`- ... and ${allTests.length - 10} more`)
    } else {
      lines.push(`- ${kf?.title || 'Unknown'}`)
    }
    lines.push(``)

    // Basic information
    lines.push(`## Basic information`)
    lines.push(``)
    lines.push(`- [${kf?.is_flaky ? 'x' : ' '}] Flaky test`)
    lines.push(`- [ ] Can reproduce locally`)
    lines.push(`- [ ] Caused by external libraries`)
    lines.push(``)

    // Affected jobs
    lines.push(`**Affected jobs:** ${kf?.affected_jobs.join(', ')}`)
    lines.push(`**Category:** ${kf?.category || 'unknown'}`)
    lines.push(``)

    // Describe the failing test
    lines.push(`## Describe the failing test`)
    lines.push(``)
    if (kf?.summary) {
      lines.push(kf.summary)
      lines.push(``)
    }

    // Collect distinct error messages and log excerpts across all failures
    const seenMessages = new Set<string>()
    const seenExcerpts = new Set<string>()
    const allFailures = kf?.failures_by_build?.flatMap(b => b.failures) || []

    for (const f of allFailures) {
      if (f.error_message && !seenMessages.has(f.error_message)) {
        seenMessages.add(f.error_message)
      }
    }
    if (seenMessages.size > 0) {
      lines.push('```')
      lines.push([...seenMessages].map(m => m.slice(0, 1500)).join('\n\n'))
      lines.push('```')
      lines.push(``)
    }

    for (const f of allFailures) {
      if (f.log_excerpt && !seenExcerpts.has(f.log_excerpt)) {
        seenExcerpts.add(f.log_excerpt)
      }
    }
    if (seenExcerpts.size > 0) {
      const excerpts = [...seenExcerpts]
      for (let i = 0; i < Math.min(excerpts.length, 3); i++) {
        const label = excerpts.length > 1 ? `Log excerpt ${i + 1}` : 'Log excerpt'
        lines.push(`<details>`)
        lines.push(`<summary>${label}</summary>`)
        lines.push(``)
        lines.push('```')
        lines.push(excerpts[i].slice(0, 5000))
        lines.push('```')
        lines.push(`</details>`)
        lines.push(``)
      }
    }

    // Relevant builds and logs
    const builds = kf?.failures_by_build || []
    if (builds.length > 0) {
      lines.push(`## Relevant builds`)
      lines.push(``)
      for (const group of builds.slice(0, 5)) {
        const buildLabel = `Build #${group.build_number}`
        const buildLink = group.build_url ? `[${buildLabel}](${group.build_url})` : buildLabel
        const sha = group.commit_sha ? ` (${group.commit_sha.slice(0, 8)})` : ''
        lines.push(`- ${buildLink}${sha}`)
        const seenJobs = new Set<string>()
        for (const f of group.failures) {
          const jobKey = f.job_url || f.job_name
          if (seenJobs.has(jobKey)) continue
          seenJobs.add(jobKey)
          const jobLink = f.job_url ? `[${f.job_name}](${f.job_url})` : f.job_name
          lines.push(`  - ${jobLink}`)
        }
      }
      if (builds.length > 5) lines.push(`- ... and ${builds.length - 5} more builds`)
      lines.push(``)
    }

    // History of failing test
    lines.push(`## History of failing test`)
    lines.push(``)
    if (kf?.first_seen_build) {
      lines.push(`- **First seen:** Build #${kf.first_seen_build.build_number} (${kf.first_seen_build.commit_sha?.slice(0, 8) || 'unknown'})`)

      // Find last pass before first failure from history entries (newest-first)
      if (history?.entries) {
        const firstSeenIdx = history.entries.findIndex(e =>
          e.builds.some(b => b.build_number === kf.first_seen_build!.build_number)
        )
        if (firstSeenIdx >= 0) {
          for (let i = firstSeenIdx + 1; i < history.entries.length; i++) {
            const entry = history.entries[i]
            if (entry.status === 'pass' || entry.status === 'flaky_pass') {
              const passBuilds = entry.builds
              const passBuildNum = passBuilds.length > 0 ? passBuilds[0].build_number : null
              const passSha = entry.commit_sha?.slice(0, 8) || 'unknown'
              lines.push(`- **Last pass:** ${passBuildNum ? `Build #${passBuildNum}` : 'unknown'} (${passSha})`)
              // Count commits between last pass and first seen
              const commitsBetween = i - firstSeenIdx - 1
              if (commitsBetween > 0) {
                lines.push(`- **Commits between last pass and first seen:** ${commitsBetween}`)
              }
              break
            }
          }
        }
      }
    }
    lines.push(``)

    // Dashboard link
    lines.push(`---`)
    lines.push(`*Filed from [CI Triage Dashboard](${window.location.origin}/known-failures/${kf?.id})*`)

    return { title: `[CI] ${kf?.title || ''}`, body: lines.join('\n') }
  }

  const handleShowCreateIssue = () => {
    if (!showCreateIssue) {
      const { title, body } = generateIssueContent()
      setIssueTitle(title)
      setIssueBody(body)
    }
    setShowCreateIssue(!showCreateIssue)
    setShowLinkIssue(false)
    setShowResolve(false)
    setShowMerge(false)
  }

  const handleCreateIssue = async () => {
    if (!issueTitle.trim()) return
    const firstFailureId = kf?.failures_by_build?.[0]?.failures[0]?.failure_id
    if (!firstFailureId) return
    setCreatingIssue(true)
    try {
      const issue = await api.issues.createForFailure(firstFailureId, {
        title: issueTitle, body: issueBody, labels: ['ci-failure'],
      })
      await api.knownFailures.linkIssue(knownFailureId, issue.github_issue_number)
      setShowCreateIssue(false)
      setIssueTitle('')
      setIssueBody('')
      refreshData()
    } finally {
      setCreatingIssue(false)
    }
  }

  const totalFailureInstances = kf?.failures_by_build?.reduce((sum, g) => sum + g.failures.length, 0) || 0
  const canSelect = totalFailureInstances > 1

  const toggleFailure = (failureId: number) => {
    setSelectedFailures(prev => {
      const next = new Set(prev)
      if (next.has(failureId)) {
        next.delete(failureId)
      } else {
        next.add(failureId)
      }
      return next
    })
  }

  const handleSplit = async () => {
    if (selectedFailures.size === 0 || selectedFailures.size === totalFailureInstances) return
    const title = splitTitle.trim()
    if (!title) return
    setSplitting(true)
    try {
      const result = await api.knownFailures.split(Array.from(selectedFailures), title)
      setSelectedFailures(new Set())
      setSplitTitle('')
      // Navigate to the new KF
      navigate(`/known-failures/${result.new_id}`)
    } finally {
      setSplitting(false)
    }
  }

  const handleSaveTitle = async () => {
    if (!editTitle.trim() || editTitle === kf?.title) {
      setEditingTitle(false)
      return
    }
    setSaving(true)
    try {
      await api.knownFailures.update(knownFailureId, { title: editTitle.trim() })
      refreshData()
      setEditingTitle(false)
    } finally {
      setSaving(false)
    }
  }

  const handleSaveSummary = async () => {
    setSaving(true)
    try {
      await api.knownFailures.update(knownFailureId, { summary: editSummary.trim() || '' })
      refreshData()
      setEditingSummary(false)
    } finally {
      setSaving(false)
    }
  }

  const handleSaveMatchPrompt = async () => {
    setSaving(true)
    try {
      await api.knownFailures.update(knownFailureId, { match_prompt: editMatchPrompt.trim() || '' })
      refreshData()
      setEditingMatchPrompt(false)
    } finally {
      setSaving(false)
    }
  }

  const handleMerge = async (targetId: number) => {
    if (targetId === knownFailureId) return
    setMerging(true)
    try {
      await api.knownFailures.merge(knownFailureId, targetId)
      navigate(`/known-failures/${targetId}`)
    } finally {
      setMerging(false)
    }
  }

  // Auto-generate split title from selected failures
  useEffect(() => {
    if (selectedFailures.size === 0) {
      setSplitTitle('')
      return
    }
    const allInstances: KnownFailureInstance[] = []
    for (const group of kf?.failures_by_build || []) {
      for (const f of group.failures) {
        if (selectedFailures.has(f.failure_id)) allInstances.push(f)
      }
    }
    // Derive title from tests
    const tests = new Set<string>()
    for (const inst of allInstances) {
      const ft = Array.isArray(inst.failing_test) ? inst.failing_test : (inst.failing_test ? [inst.failing_test] : [])
      for (const t of ft) tests.add(t)
    }
    if (tests.size > 0) {
      const testList = Array.from(tests)
      setSplitTitle(testList.length === 1 ? testList[0] : `${testList[0]} (+${testList.length - 1} more)`)
    } else {
      setSplitTitle(allInstances[0]?.error_message?.slice(0, 100) || kf?.title || '')
    }
  }, [selectedFailures, kf])

  if (kfLoading || historyLoading) {
    return <div className="text-gray-400 py-8">Loading...</div>
  }

  if (!kf || !history) {
    return <div className="text-gray-400 py-8">Known failure not found</div>
  }

  const failuresByBuild = kf.failures_by_build || []
  const previewLogExcerpt = failuresByBuild[0]?.failures[0]?.log_excerpt || failuresByBuild[0]?.failures[0]?.error_message

  return (
    <div className="space-y-4">
      {/* Back link */}
      <Link to="/" className="text-blue-400 hover:text-blue-300 text-sm">
        &larr; Back to Dashboard
      </Link>

      {/* Header */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
        <div className="flex items-center gap-3 mb-1">
          <span className="text-gray-500 text-sm font-mono">#{kf.id}</span>
          {editingTitle ? (
            <div className="flex items-center gap-2 flex-1">
              <input
                type="text"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleSaveTitle()
                  if (e.key === 'Escape') setEditingTitle(false)
                }}
                className="flex-1 px-2 py-1 bg-gray-700 text-white rounded text-lg font-mono border border-gray-600 focus:border-blue-500 focus:outline-none"
                autoFocus
              />
              <button
                onClick={handleSaveTitle}
                disabled={saving}
                className="px-2 py-1 bg-blue-600 text-white rounded text-xs hover:bg-blue-500 disabled:opacity-50"
              >
                {saving ? '...' : 'Save'}
              </button>
              <button
                onClick={() => setEditingTitle(false)}
                className="px-2 py-1 bg-gray-600 text-white rounded text-xs hover:bg-gray-500"
              >
                Cancel
              </button>
            </div>
          ) : (
            <h1
              className="text-white text-lg font-mono cursor-pointer hover:text-blue-300 transition-colors"
              onClick={() => { setEditTitle(kf.title); setEditingTitle(true) }}
              title="Click to edit"
            >
              {kf.title}
            </h1>
          )}
          <span className={`px-2 py-0.5 rounded text-xs flex-shrink-0 ${
            kf.status === 'open' ? 'bg-red-600 text-white' : 'bg-green-600 text-white'
          }`}>
            {kf.status}
          </span>
          {kf.is_flaky && (
            <span className="text-yellow-400 text-xs flex-shrink-0">(likely flaky)</span>
          )}
          {/* Issue badge */}
          {kf.github_issue ? (
            <div className="flex items-center gap-1 ml-auto flex-shrink-0">
              <a
                href={kf.github_issue.github_issue_url || '#'}
                target="_blank"
                rel="noopener noreferrer"
                className={`px-2 py-0.5 rounded text-xs text-white ${kf.github_issue?.state === 'closed' ? 'bg-github-purple' : 'bg-github-green'}`}
              >
                #{kf.github_issue.github_issue_number}
              </a>
              <button
                onClick={handleUnlinkIssue}
                disabled={unlinking}
                className="text-gray-500 hover:text-red-400 text-xs"
                title="Unlink issue"
              >
                {unlinking ? '...' : '×'}
              </button>
            </div>
          ) : kf.resolved_by_pr ? (
            <a
              href={`https://github.com/vllm-project/vllm/pull/${kf.resolved_by_pr}`}
              target="_blank"
              rel="noopener noreferrer"
              className="px-2 py-0.5 rounded text-xs bg-purple-600 text-white ml-auto flex-shrink-0"
            >
              PR #{kf.resolved_by_pr}
            </a>
          ) : null}
        </div>

        {/* Summary */}
        <div className="mb-2 ml-8">
          {editingSummary ? (
            <div className="flex items-start gap-2">
              <textarea
                value={editSummary}
                onChange={(e) => setEditSummary(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && e.metaKey) handleSaveSummary()
                  if (e.key === 'Escape') setEditingSummary(false)
                }}
                placeholder="Add a summary describing this failure pattern..."
                className="flex-1 px-2 py-1 bg-gray-700 text-gray-300 rounded text-sm border border-gray-600 focus:border-blue-500 focus:outline-none resize-none h-16"
                autoFocus
              />
              <div className="flex flex-col gap-1">
                <button
                  onClick={handleSaveSummary}
                  disabled={saving}
                  className="px-2 py-1 bg-blue-600 text-white rounded text-xs hover:bg-blue-500 disabled:opacity-50"
                >
                  {saving ? '...' : 'Save'}
                </button>
                <button
                  onClick={() => setEditingSummary(false)}
                  className="px-2 py-1 bg-gray-600 text-white rounded text-xs hover:bg-gray-500"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : kf.summary ? (
            <p
              className="text-gray-400 text-sm cursor-pointer hover:text-gray-300 transition-colors"
              onClick={() => { setEditSummary(kf.summary || ''); setEditingSummary(true) }}
              title="Click to edit"
            >
              {kf.summary}
            </p>
          ) : (
            <button
              onClick={() => { setEditSummary(''); setEditingSummary(true) }}
              className="text-gray-600 text-xs hover:text-gray-400 transition-colors"
            >
              + Add summary
            </button>
          )}
        </div>

        {/* Match prompt */}
        <div className="mb-2 ml-8">
          {editingMatchPrompt ? (
            <div className="flex items-start gap-2">
              <textarea
                value={editMatchPrompt}
                onChange={(e) => setEditMatchPrompt(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && e.metaKey) handleSaveMatchPrompt()
                  if (e.key === 'Escape') setEditingMatchPrompt(false)
                }}
                placeholder="Describe when Claude should match failures to this issue (e.g., 'Match when test_foo fails with AssertionError about accuracy below threshold')"
                className="flex-1 px-2 py-1 bg-gray-700 text-gray-300 rounded text-sm border border-gray-600 focus:border-blue-500 focus:outline-none resize-none h-16 font-mono"
                autoFocus
              />
              <div className="flex flex-col gap-1">
                <button
                  onClick={handleSaveMatchPrompt}
                  disabled={saving}
                  className="px-2 py-1 bg-blue-600 text-white rounded text-xs hover:bg-blue-500 disabled:opacity-50"
                >
                  {saving ? '...' : 'Save'}
                </button>
                <button
                  onClick={() => setEditingMatchPrompt(false)}
                  className="px-2 py-1 bg-gray-600 text-white rounded text-xs hover:bg-gray-500"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : kf.match_prompt ? (
            <p
              className="text-gray-500 text-xs font-mono cursor-pointer hover:text-gray-400 transition-colors"
              onClick={() => { setEditMatchPrompt(kf.match_prompt || ''); setEditingMatchPrompt(true) }}
              title="Click to edit match prompt"
            >
              <span className="text-gray-600">match: </span>{kf.match_prompt}
            </p>
          ) : (
            <button
              onClick={() => { setEditMatchPrompt(''); setEditingMatchPrompt(true) }}
              className="text-gray-600 text-xs hover:text-gray-400 transition-colors"
            >
              + Add match prompt
            </button>
          )}
        </div>

        {/* Affected jobs & tests */}
        <div className="space-y-1.5 mb-3">
          <div className="flex items-start gap-2 flex-wrap">
            <span className="text-xs text-gray-400 mt-0.5">Affected jobs:</span>
            <div className="flex items-center gap-1 flex-wrap">
              {history.affected_jobs.map(job => (
                <span key={job} className="px-1.5 py-0.5 rounded bg-gray-700 text-gray-300 font-mono text-xs">
                  {job}
                </span>
              ))}
            </div>
          </div>
          {history.affected_tests.length > 0 && (
            <div className="flex items-start gap-2 flex-wrap pl-6">
              <span className="text-xs text-gray-400 mt-0.5">Tests:</span>
              <div className="flex flex-col gap-0.5">
                {history.affected_tests.map(test => (
                  <span key={test} className="text-xs text-red-300 font-mono">
                    {test}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Action buttons */}
        <div className="mt-3 pt-3 border-t border-gray-700 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            {!kf.github_issue && (
              <>
                <button
                  onClick={handleShowCreateIssue}
                  className="px-2 py-1 bg-green-700 text-white rounded text-xs hover:bg-green-600"
                >
                  + Create Issue
                </button>
                <button
                  onClick={() => { setShowLinkIssue(!showLinkIssue); setShowResolve(false); setShowCreateIssue(false); setShowMerge(false) }}
                  className="px-2 py-1 bg-blue-700 text-white rounded text-xs hover:bg-blue-600"
                >
                  Link Issue
                </button>
              </>
            )}
            {kf.status === 'open' ? (
              <button
                onClick={() => { setShowResolve(!showResolve); setShowLinkIssue(false); setShowCreateIssue(false); setShowMerge(false) }}
                className="px-2 py-1 bg-purple-700 text-white rounded text-xs hover:bg-purple-600"
              >
                Mark Resolved
              </button>
            ) : (
              <button
                onClick={handleReopen}
                className="px-2 py-1 bg-red-700 text-white rounded text-xs hover:bg-red-600"
              >
                Reopen
              </button>
            )}
            <button
              onClick={() => { setShowMerge(!showMerge); setShowLinkIssue(false); setShowCreateIssue(false); setShowResolve(false) }}
              className="px-2 py-1 bg-orange-700 text-white rounded text-xs hover:bg-orange-600"
            >
              Merge Into...
            </button>
          </div>

          {/* Create issue form */}
          {showCreateIssue && (
            <div className="space-y-2 p-2 bg-gray-900 rounded">
              <input
                type="text"
                placeholder="Issue title"
                value={issueTitle}
                onChange={(e) => setIssueTitle(e.target.value)}
                className="w-full px-2 py-1 bg-gray-700 text-white rounded text-sm border border-gray-600 focus:border-blue-500 focus:outline-none"
              />
              <textarea
                placeholder="Issue body"
                value={issueBody}
                onChange={(e) => setIssueBody(e.target.value)}
                className="w-full px-2 py-1 bg-gray-700 text-white rounded text-sm h-64 font-mono text-xs border border-gray-600 focus:border-blue-500 focus:outline-none"
              />
              <div className="flex gap-2">
                <button
                  onClick={handleCreateIssue}
                  disabled={creatingIssue || !issueTitle.trim()}
                  className="px-2 py-1 bg-green-600 text-white rounded text-xs hover:bg-green-500 disabled:opacity-50"
                >
                  {creatingIssue ? 'Creating...' : 'Create'}
                </button>
                <button
                  onClick={() => { setShowCreateIssue(false); setIssueTitle(''); setIssueBody('') }}
                  className="px-2 py-1 bg-gray-600 text-white rounded text-xs hover:bg-gray-500"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* Link issue form */}
          {showLinkIssue && (
            <div className="flex items-center gap-2 p-2 bg-gray-900 rounded">
              <input
                type="text"
                placeholder="Issue # or URL"
                value={linkInput}
                onChange={(e) => setLinkInput(e.target.value)}
                className="flex-1 px-2 py-1 bg-gray-700 text-white rounded text-sm border border-gray-600 focus:border-blue-500 focus:outline-none"
                onKeyDown={(e) => e.key === 'Enter' && handleLinkIssue()}
              />
              <button
                onClick={handleLinkIssue}
                disabled={linking || !linkInput.trim()}
                className="px-2 py-1 bg-blue-600 text-white rounded text-xs hover:bg-blue-500 disabled:opacity-50"
              >
                {linking ? '...' : 'Link'}
              </button>
              <button
                onClick={() => { setShowLinkIssue(false); setLinkInput('') }}
                className="px-2 py-1 bg-gray-600 text-white rounded text-xs hover:bg-gray-500"
              >
                Cancel
              </button>
            </div>
          )}

          {/* Resolve form */}
          {showResolve && (
            <div className="flex items-center gap-2 p-2 bg-gray-900 rounded">
              <input
                type="text"
                placeholder="PR # or URL (optional)"
                value={prInput}
                onChange={(e) => setPrInput(e.target.value)}
                className="flex-1 px-2 py-1 bg-gray-700 text-white rounded text-sm border border-gray-600 focus:border-blue-500 focus:outline-none"
                onKeyDown={(e) => e.key === 'Enter' && handleResolve()}
              />
              <button
                onClick={handleResolve}
                disabled={resolving}
                className="px-2 py-1 bg-purple-600 text-white rounded text-xs hover:bg-purple-500 disabled:opacity-50"
              >
                {resolving ? '...' : 'Resolve'}
              </button>
              <button
                onClick={() => { setShowResolve(false); setPrInput('') }}
                className="px-2 py-1 bg-gray-600 text-white rounded text-xs hover:bg-gray-500"
              >
                Cancel
              </button>
            </div>
          )}

          {/* Merge dropdown */}
          {showMerge && (
            <MergeDropdown
              currentId={knownFailureId}
              onSelect={(targetId) => {
                setShowMerge(false)
                handleMerge(targetId)
              }}
              onCancel={() => setShowMerge(false)}
            />
          )}
        </div>

      </div>

      {/* Failure instances by build */}
      {failuresByBuild.length > 0 && (
        <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
          <div className="p-3 border-b border-gray-700 flex items-center justify-between">
            <div>
              <h2 className="text-white font-medium">Failure Instances</h2>
              <p className="text-gray-500 text-xs">{totalFailureInstances} instance{totalFailureInstances !== 1 ? 's' : ''} across {failuresByBuild.length} build{failuresByBuild.length !== 1 ? 's' : ''}</p>
            </div>
            <div className="flex items-center gap-2">
              {splitMode && selectedFailures.size > 0 && selectedFailures.size < totalFailureInstances && (
                <>
                  <input
                    type="text"
                    placeholder="New issue title"
                    value={splitTitle}
                    onChange={(e) => setSplitTitle(e.target.value)}
                    className="px-2 py-1 bg-gray-700 text-white rounded text-xs border border-gray-600 focus:border-orange-500 focus:outline-none w-64"
                  />
                  <button
                    onClick={handleSplit}
                    disabled={splitting || !splitTitle.trim()}
                    className="px-3 py-1 bg-orange-600 text-white rounded text-xs hover:bg-orange-500 disabled:opacity-50 flex-shrink-0"
                  >
                    {splitting ? 'Splitting...' : `Split ${selectedFailures.size} selected`}
                  </button>
                </>
              )}
              {canSelect && (
                <button
                  onClick={() => {
                    setSplitMode(!splitMode)
                    if (splitMode) { setSelectedFailures(new Set()); setSplitTitle('') }
                  }}
                  className={`px-2 py-1 rounded text-xs ${
                    splitMode
                      ? 'bg-orange-600 text-white hover:bg-orange-500'
                      : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                  }`}
                >
                  {splitMode ? 'Done' : 'Split'}
                </button>
              )}
            </div>
          </div>
          {failuresByBuild.map((group) => (
            <FailureBuildGroup
              key={group.build_number}
              group={group}
              selectedIds={selectedFailures}
              onToggle={toggleFailure}
              canSelect={splitMode && canSelect}
            />
          ))}
        </div>
      )}

      {/* Log preview */}
      {previewLogExcerpt && (
        <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
          <div className="p-3 border-b border-gray-700">
            <h2 className="text-white font-medium">Log Preview</h2>
          </div>
          <div className="p-3">
            <LogSnippet log={previewLogExcerpt} />
          </div>
        </div>
      )}

      {/* Predates history warning + load earlier button */}
      {history.predates_history && (
        <div className="bg-yellow-900/30 border border-yellow-700/50 rounded-lg p-3 flex items-center justify-between">
          <span className="text-yellow-300 text-sm">
            This failure may predate recorded history. The earliest recorded build is the first known occurrence.
          </span>
          <button
            onClick={async () => {
              setLoadingEarlier(true)
              try {
                await api.knownFailures.loadEarlierHistory(knownFailureId)
                queryClient.invalidateQueries({ queryKey: ['known-failure-history', knownFailureId] })
                queryClient.invalidateQueries({ queryKey: ['known-failure', knownFailureId] })
              } finally {
                setLoadingEarlier(false)
              }
            }}
            disabled={loadingEarlier}
            className="px-3 py-1.5 bg-yellow-700 hover:bg-yellow-600 text-yellow-100 text-sm rounded transition-colors disabled:opacity-50 flex-shrink-0 ml-3"
          >
            {loadingEarlier ? 'Loading...' : 'Load Earlier History'}
          </button>
        </div>
      )}

      {/* No prior runs info */}
      {history.no_prior_runs && !history.predates_history && (
        <div className="bg-gray-800 border border-gray-600 rounded-lg p-3">
          <span className="text-gray-400 text-sm">
            No prior test runs found. This test was first run in the build where it failed — there is no passing baseline to compare against.
          </span>
        </div>
      )}

      {/* Legend */}
      <div className="flex items-center gap-4 text-xs text-gray-400 flex-wrap">
        <span>Legend:</span>
        <div className="flex items-center gap-1">
          <StatusDot status="fail" /> <span>Test failed</span>
        </div>
        <div className="flex items-center gap-1">
          <StatusDot status="pass" /> <span>Passed</span>
        </div>
        <div className="flex items-center gap-1">
          <StatusDot status="flaky_pass" /> <span>Passed after retry</span>
        </div>
        <div className="flex items-center gap-1">
          <StatusDot status="job_fail" /> <span>Job failed before test</span>
        </div>
        <div className="flex items-center gap-1">
          <StatusDot status="infra_fail" /> <span>Infrastructure failure</span>
        </div>
        <div className="flex items-center gap-1">
          <StatusDot status="diff_fail" /> <span>Test failed, different error</span>
        </div>
        <div className="flex items-center gap-1">
          <StatusDot status="other_fail" /> <span>Job failed, Test Passed</span>
        </div>
        <div className="flex items-center gap-1">
          <StatusDot status="not_run" /> <span>Not Run</span>
        </div>
      </div>

      {/* Full timeline */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
        <div className="p-3 border-b border-gray-700">
          <h2 className="text-white font-medium">Commit History</h2>
          <p className="text-gray-500 text-xs">Newest first, showing context from last pass before failure</p>
        </div>
        {history.entries.length === 0 ? (
          <div className="p-4 text-center text-gray-500">No history entries</div>
        ) : (
          <>
            {history.entries.slice(0, visibleCount).map(entry => (
              <HistoryEntry
                key={entry.commit_sha}
                entry={entry}
                onTriageCommit={async (sha) => {
                  // Set running state immediately
                  setTriageResults(prev => ({ ...prev, [sha]: 'running' }))
                  try {
                    await api.knownFailures.triageCommit(knownFailureId, sha)
                  } catch {
                    // Remove on error
                    setTriageResults(prev => {
                      const next = { ...prev }
                      delete next[sha]
                      return next
                    })
                  }
                }}
                triageState={entry.commit_sha ? triageResults[entry.commit_sha] : undefined}
              />
            ))}
            {visibleCount < history.entries.length && (
              <div className="p-3 border-t border-gray-700/50 flex items-center justify-center">
                <button
                  onClick={() => setVisibleCount(c => c + PAGE_SIZE)}
                  className="px-4 py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-300 text-sm rounded transition-colors"
                >
                  Load more ({history.entries.length - visibleCount} remaining)
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {/* Merging overlay */}
      {merging && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg p-4 text-white">Merging...</div>
        </div>
      )}
    </div>
  )
}

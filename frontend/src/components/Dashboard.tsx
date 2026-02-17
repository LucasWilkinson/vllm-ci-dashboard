import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import AnsiToHtml from 'ansi-to-html'
import { api, BuildWithFailures, FailedJobSummary, FailureSuggestion, CurrentIssue, CurrentIssueGroup } from '../api/client'

interface TriageProgress {
  build_number: number
  total_jobs: number
  completed_jobs: number
  current_job: string | null
  status: 'pending' | 'running' | 'completed' | 'error'
}

function TriageStatusIndicator() {
  const [triages, setTriages] = useState<TriageProgress[]>([])
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/triage-status`)
    wsRef.current = ws

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      if (data.type === 'triage_status') {
        setTriages(data.active_triages)
      }
    }

    ws.onerror = () => {
      // Silently handle errors - WebSocket is optional
    }

    return () => {
      ws.close()
    }
  }, [])

  if (triages.length === 0) return null

  return (
    <div className="bg-blue-900/50 border border-blue-700 rounded-lg p-3 mb-4">
      <div className="flex items-center gap-2 text-blue-300 text-sm mb-2">
        <div className="w-2 h-2 bg-blue-400 rounded-full animate-pulse" />
        <span className="font-medium">Triaging in progress...</span>
      </div>
      {triages.map((t) => (
        <div key={t.build_number} className="text-xs text-blue-200 ml-4">
          <span className="font-mono">Build #{t.build_number}</span>
          <span className="text-blue-400 mx-2">
            {t.completed_jobs}/{t.total_jobs} jobs
          </span>
          {t.current_job && (
            <span className="text-blue-300 truncate">→ {t.current_job}</span>
          )}
        </div>
      ))}
    </div>
  )
}

const ansiConverter = new AnsiToHtml({
  fg: '#d1d5db',
  bg: '#1f2937',
  colors: {
    0: '#374151', 1: '#ef4444', 2: '#22c55e', 3: '#eab308',
    4: '#3b82f6', 5: '#a855f7', 6: '#06b6d4', 7: '#d1d5db',
    8: '#6b7280', 9: '#f87171', 10: '#4ade80', 11: '#facc15',
    12: '#60a5fa', 13: '#c084fc', 14: '#22d3ee', 15: '#f3f4f6',
  },
})

function LogSnippet({ log }: { log: string }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [log])

  const linkMatch = log.match(/^\[View in Buildkite at line (\d+)\]\(([^)]+)\)/)
  const bkLink = linkMatch ? { line: linkMatch[1], url: linkMatch[2] } : null
  const logContent = linkMatch ? log.slice(linkMatch[0].length).trim() : log

  // Parse lines and extract line numbers (format: "1234\tcontent" or "L1234: content")
  const lines = logContent.split('\n').map(line => {
    // New format: line number + tab + content
    const tabMatch = line.match(/^(\d+)\t(.*)$/)
    if (tabMatch) {
      return { lineNum: tabMatch[1], content: tabMatch[2] }
    }
    // Legacy format: L1234: content
    const legacyMatch = line.match(/^L(\d+):\s*(.*)$/)
    if (legacyMatch) {
      return { lineNum: legacyMatch[1], content: legacyMatch[2] }
    }
    return { lineNum: null, content: line }
  })

  // Build Buildkite URL for a specific line
  const getLineUrl = (lineNum: string) => {
    if (!bkLink) return null
    return bkLink.url.replace(/\?line=\d+/, `?line=${lineNum}`)
  }

  return (
    <div className="rounded overflow-hidden border border-gray-700" onClick={(e) => e.stopPropagation()}>
      {/* Buildkite-style header */}
      {bkLink && (
        <div className="flex items-center justify-between px-3 py-1.5 bg-gray-900 border-b border-gray-700">
          <span className="text-gray-400 text-xs font-mono">Log output</span>
          <a
            href={bkLink.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-400 hover:text-blue-300 text-xs flex items-center gap-1"
          >
            <span>View in Buildkite</span>
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
          </a>
        </div>
      )}
      {/* Log content with Buildkite-style line numbers */}
      <div
        ref={containerRef}
        className="text-xs font-mono overflow-y-auto max-h-64 overflow-x-auto bg-gray-950"
      >
        <table className="w-full border-collapse">
          <tbody>
            {lines.map((line, idx) => (
              <tr key={idx} className="group hover:bg-gray-800/50">
                {line.lineNum ? (
                  <td className="select-none text-right align-top py-0 px-0 border-r border-gray-800 bg-gray-900/50 sticky left-0">
                    <a
                      href={getLineUrl(line.lineNum) || '#'}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block px-2 py-px text-gray-600 hover:text-blue-400 hover:bg-gray-800"
                      title={`Line ${line.lineNum}`}
                    >
                      {line.lineNum}
                    </a>
                  </td>
                ) : (
                  <td className="select-none text-right align-top py-0 px-2 border-r border-gray-800 bg-gray-900/50 sticky left-0 text-gray-700">
                    {idx + 1}
                  </td>
                )}
                <td className="py-px px-3 whitespace-pre-wrap break-all">
                  <span
                    style={{ color: '#d1d5db' }}
                    dangerouslySetInnerHTML={{ __html: ansiConverter.toHtml(line.content) }}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function CurrentIssueFailureRow({ issue, onRefresh, nested = false, inline = false }: { issue: CurrentIssue; onRefresh: () => void; nested?: boolean; inline?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const [showCreateIssue, setShowCreateIssue] = useState(false)
  const [showLinkIssue, setShowLinkIssue] = useState(false)
  const [showResolvedByPR, setShowResolvedByPR] = useState(false)
  const [showFlakyDetails, setShowFlakyDetails] = useState(false)
  const [linkInput, setLinkInput] = useState('')
  const [prInput, setPrInput] = useState('')
  const [issueTitle, setIssueTitle] = useState('')
  const [issueBody, setIssueBody] = useState('')
  const [creating, setCreating] = useState(false)
  const [linkingManual, setLinkingManual] = useState(false)
  const [markingResolved, setMarkingResolved] = useState(false)
  const [unlinking, setUnlinking] = useState(false)

  const tests = Array.isArray(issue.failing_test) ? issue.failing_test : (issue.failing_test ? [issue.failing_test] : [])

  // Show all tests or summarize if multiple different test functions
  const getDisplayName = () => {
    if (tests.length === 0) return issue.error_signature || 'Unknown'
    if (tests.length === 1) return tests[0]

    // Extract test function names (before the [ param bracket)
    const testFuncs = tests.map(t => {
      const match = t.match(/::([^[]+)/)
      return match ? match[1] : t
    })
    const uniqueFuncs = [...new Set(testFuncs)]

    if (uniqueFuncs.length === 1) {
      // Same test function, different params - use Claude's deduped format
      return tests[0]
    } else {
      // Different test functions - show count
      return `${tests.length} different tests`
    }
  }
  const displayName = getDisplayName()

  const brief = issue.error_message
    ? issue.error_message.slice(0, 120) + (issue.error_message.length > 120 ? '...' : '')
    : 'Unknown error'

  const generateIssueTitle = () => `[CI Failure] ${issue.job_name}: ${displayName}`

  const generateIssueBody = () => {
    const lines = [
      `## CI Failure Details`,
      ``,
      `**Job:** ${issue.job_name}`,
      `**Failure Type:** ${displayName}`,
      `**First seen:** Build #${issue.first_seen_build}`,
      issue.job_url ? `**Buildkite:** ${issue.job_url}` : '',
      ``,
      `## Error`,
      '```',
      issue.error_message || 'No error message available',
      '```',
    ]
    return lines.filter(Boolean).join('\n')
  }

  const handleShowCreateIssue = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!showCreateIssue) {
      setIssueTitle(generateIssueTitle())
      setIssueBody(generateIssueBody())
    }
    setShowCreateIssue(!showCreateIssue)
    setShowLinkIssue(false)
  }

  const handleCreateIssue = async () => {
    if (!issueTitle) return
    setCreating(true)
    try {
      await api.issues.createForFailure(issue.failure_id, { title: issueTitle, body: issueBody, labels: ['ci-failure'] })
      setShowCreateIssue(false)
      setIssueTitle('')
      setIssueBody('')
      onRefresh()
    } finally {
      setCreating(false)
    }
  }

  const handleManualLink = async () => {
    if (!linkInput.trim()) return
    const match = linkInput.match(/(\d+)\s*$/) || linkInput.match(/issues\/(\d+)/)
    if (!match) return
    const issueNumber = parseInt(match[1], 10)
    setLinkingManual(true)
    try {
      await api.issues.linkToFailure(issue.failure_id, issueNumber)
      setShowLinkIssue(false)
      setLinkInput('')
      onRefresh()
    } finally {
      setLinkingManual(false)
    }
  }

  const handleMarkResolvedByPR = async () => {
    if (!prInput.trim()) return
    const match = prInput.match(/(\d+)\s*$/) || prInput.match(/pull\/(\d+)/)
    if (!match) return
    const prNumber = parseInt(match[1], 10)
    setMarkingResolved(true)
    try {
      await api.triages.markResolvedByPR(issue.failure_id, prNumber)
      setShowResolvedByPR(false)
      setPrInput('')
      onRefresh()
    } finally {
      setMarkingResolved(false)
    }
  }

  const handleUnlink = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!issue.failure_id || !issue.linked_issue_number) return
    setUnlinking(true)
    try {
      await api.issues.unlinkFromFailure(issue.failure_id, issue.linked_issue_number)
      onRefresh()
    } finally {
      setUnlinking(false)
    }
  }

  // For inline mode (single issue in group), just show expandable details
  if (inline) {
    return expanded ? (
      <div className="px-4 pb-3 pl-12 space-y-3 bg-gray-850">
        {tests.length > 1 && (
          <div className="text-xs">
            <span className="text-gray-400">Failing tests:</span>
            <ul className="ml-4 mt-1 space-y-0.5">
              {tests.map((t, i) => (
                <li key={i} className="text-red-400 font-mono">{t}</li>
              ))}
            </ul>
          </div>
        )}
        {(issue.log_excerpt || issue.error_message) && (
          <div className="bg-gray-800 p-2 rounded">
            <LogSnippet log={issue.log_excerpt || issue.error_message || ''} />
          </div>
        )}
        <div className="text-xs text-gray-500">
          <span>Signature: </span>
          <span className="font-mono text-gray-400">{issue.error_signature || 'N/A'}</span>
        </div>
      </div>
    ) : null
  }

  return (
    <div className={nested ? "bg-gray-850 border-t border-gray-700" : "bg-gray-850"}>
      <div
        className={`py-2 px-4 ${nested ? 'pl-12' : 'pl-8'} cursor-pointer hover:bg-gray-700`}
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2 flex-1 min-w-0">
            <span className="text-gray-500 text-xs">{expanded ? '▼' : '▶'}</span>
            {!nested && <span className="px-2 py-0.5 rounded text-xs bg-red-600 text-red-100">test</span>}
            <span className="text-red-400 font-mono text-xs truncate" title={displayName}>
              {displayName}
            </span>
            {tests.length > 1 && (
              <span className="text-gray-500 text-xs">+{tests.length - 1} more</span>
            )}
            {issue.is_flaky && (
              <span
                onClick={(e) => { e.stopPropagation(); setShowFlakyDetails(!showFlakyDetails) }}
                className="text-yellow-400 text-xs cursor-pointer hover:text-yellow-300"
                title="Click for flaky details"
              >
                (flaky)
              </span>
            )}
          </div>
          <div className="flex items-center space-x-2 ml-2">
            {issue.linked_issue_number ? (
              <>
                <a
                  href={issue.linked_issue_url || '#'}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  className="px-2 py-0.5 rounded text-xs bg-github-green text-white"
                >
                  #{issue.linked_issue_number}
                </a>
                <button
                  onClick={handleUnlink}
                  disabled={unlinking}
                  className="text-gray-500 hover:text-red-400 text-xs"
                  title="Unlink issue"
                >
                  {unlinking ? '...' : '×'}
                </button>
              </>
            ) : issue.resolved_by_pr ? (
              <a
                href={`https://github.com/vllm-project/vllm/pull/${issue.resolved_by_pr}`}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="px-2 py-0.5 rounded text-xs bg-purple-600 text-white"
                title="Resolved by PR"
              >
                PR #{issue.resolved_by_pr}
              </a>
            ) : (
              <span className="px-2 py-0.5 rounded text-xs bg-gray-600 text-gray-300">
                No Issue
              </span>
            )}
          </div>
        </div>
        <div className="pl-6 mt-1">
          <span className="text-gray-100 text-sm font-mono">{brief}</span>
        </div>
        {showFlakyDetails && issue.is_flaky && (
          <div
            className="ml-6 mt-1 p-2 bg-yellow-900/30 border border-yellow-700 rounded text-xs"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="text-yellow-400 font-medium mb-1">Flaky Test Details</div>
            <div className="text-gray-300 space-y-0.5">
              <div>Retry success rate: <span className="text-yellow-400">{issue.flaky_rate ? `${(issue.flaky_rate * 100).toFixed(0)}%` : 'N/A'}</span></div>
              <div>Retries succeeded: <span className="text-yellow-400">{issue.retry_success_count ?? 0}</span> / <span className="text-gray-400">{issue.signature_occurrence_count ?? 0} occurrences</span></div>
              <div className="text-gray-500 text-xs mt-1">Marked flaky when retry success rate ≥ 30%</div>
            </div>
          </div>
        )}
      </div>

      {expanded && (
        <div className="px-4 pb-3 pl-12 space-y-3">
          {tests.length > 1 && (
            <div className="text-xs">
              <span className="text-gray-400">Failing tests:</span>
              <ul className="ml-4 mt-1 space-y-0.5">
                {tests.map((t, i) => (
                  <li key={i} className="text-red-400 font-mono">{t}</li>
                ))}
              </ul>
            </div>
          )}

          {(issue.log_excerpt || issue.error_message) && (
            <div className="bg-gray-800 p-2 rounded">
              <LogSnippet log={issue.log_excerpt || issue.error_message || ''} />
            </div>
          )}

          <div className="text-xs text-gray-500">
            <span>Signature: </span>
            <span className="font-mono text-gray-400">{issue.error_signature || 'N/A'}</span>
          </div>

          {/* Failing builds list */}
          {issue.failing_builds && issue.failing_builds.length > 0 && (
            <div className="text-xs">
              <span className="text-gray-400">Failing builds ({issue.failing_builds.length}):</span>
              <div className="mt-1 flex flex-wrap gap-1">
                {issue.failing_builds.slice(0, 10).map((build, i) => (
                  <a
                    key={i}
                    href={build.job_url || build.build_url || '#'}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-gray-700 rounded hover:bg-gray-600 text-gray-300"
                    title={build.commit_sha ? `Commit: ${build.commit_sha}` : undefined}
                  >
                    <span>#{build.build_number}</span>
                    {build.commit_sha && (
                      <span className="text-gray-500 font-mono">{build.commit_sha.slice(0, 7)}</span>
                    )}
                  </a>
                ))}
                {issue.failing_builds.length > 10 && (
                  <span className="text-gray-500">+{issue.failing_builds.length - 10} more</span>
                )}
              </div>
            </div>
          )}

          {/* Create/Link issue/Resolved by PR buttons */}
          <div className="space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              {!issue.linked_issue_number && !issue.resolved_by_pr && (
                <>
                  <button
                    onClick={handleShowCreateIssue}
                    className="px-2 py-1 bg-green-700 text-white rounded text-xs hover:bg-green-600"
                  >
                    + Create Issue
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); setShowLinkIssue(!showLinkIssue); setShowCreateIssue(false); setShowResolvedByPR(false) }}
                    className="px-2 py-1 bg-blue-700 text-white rounded text-xs hover:bg-blue-600"
                  >
                    Link Issue
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); setShowResolvedByPR(!showResolvedByPR); setShowCreateIssue(false); setShowLinkIssue(false) }}
                    className="px-2 py-1 bg-purple-700 text-white rounded text-xs hover:bg-purple-600"
                  >
                    Resolved by PR
                  </button>
                </>
              )}
            </div>

            {/* Create issue form for current issues */}
            {showCreateIssue && (
              <div className="space-y-2 p-2 bg-gray-800 rounded" onClick={(e) => e.stopPropagation()}>
                <input
                  type="text"
                  placeholder="Issue title"
                  value={issueTitle}
                  onChange={(e) => setIssueTitle(e.target.value)}
                  className="w-full px-2 py-1 bg-gray-700 text-white rounded text-sm"
                />
                <textarea
                  placeholder="Issue body"
                  value={issueBody}
                  onChange={(e) => setIssueBody(e.target.value)}
                  className="w-full px-2 py-1 bg-gray-700 text-white rounded text-sm h-32 font-mono text-xs"
                />
                <div className="flex gap-2">
                  <button
                    onClick={handleCreateIssue}
                    disabled={creating || !issueTitle}
                    className="px-2 py-1 bg-green-600 text-white rounded text-xs hover:bg-green-500 disabled:opacity-50"
                  >
                    {creating ? 'Creating...' : 'Create'}
                  </button>
                  <button
                    onClick={() => setShowCreateIssue(false)}
                    className="px-2 py-1 bg-gray-600 text-white rounded text-xs hover:bg-gray-500"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Link issue form */}
            {showLinkIssue && (
              <div className="flex items-center gap-2 p-2 bg-gray-800 rounded" onClick={(e) => e.stopPropagation()}>
                <input
                  type="text"
                  placeholder="Issue # or URL (e.g., 123 or github.com/.../issues/123)"
                  value={linkInput}
                  onChange={(e) => setLinkInput(e.target.value)}
                  className="flex-1 px-2 py-1 bg-gray-700 text-white rounded text-sm"
                  onKeyDown={(e) => e.key === 'Enter' && handleManualLink()}
                />
                <button
                  onClick={handleManualLink}
                  disabled={linkingManual || !linkInput.trim()}
                  className="px-2 py-1 bg-blue-600 text-white rounded text-xs hover:bg-blue-500 disabled:opacity-50"
                >
                  {linkingManual ? '...' : 'Link'}
                </button>
                <button
                  onClick={() => { setShowLinkIssue(false); setLinkInput('') }}
                  className="px-2 py-1 bg-gray-600 text-white rounded text-xs hover:bg-gray-500"
                >
                  Cancel
                </button>
              </div>
            )}

            {/* Resolved by PR form */}
            {showResolvedByPR && (
              <div className="flex items-center gap-2 p-2 bg-gray-800 rounded" onClick={(e) => e.stopPropagation()}>
                <input
                  type="text"
                  placeholder="PR # or URL (e.g., 12345 or github.com/.../pull/12345)"
                  value={prInput}
                  onChange={(e) => setPrInput(e.target.value)}
                  className="flex-1 px-2 py-1 bg-gray-700 text-white rounded text-sm"
                  onKeyDown={(e) => e.key === 'Enter' && handleMarkResolvedByPR()}
                />
                <button
                  onClick={handleMarkResolvedByPR}
                  disabled={markingResolved || !prInput.trim()}
                  className="px-2 py-1 bg-purple-600 text-white rounded text-xs hover:bg-purple-500 disabled:opacity-50"
                >
                  {markingResolved ? '...' : 'Mark Resolved'}
                </button>
                <button
                  onClick={() => { setShowResolvedByPR(false); setPrInput('') }}
                  className="px-2 py-1 bg-gray-600 text-white rounded text-xs hover:bg-gray-500"
                >
                  Cancel
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function CurrentIssueGroupRow({ group, onRefresh }: { group: CurrentIssueGroup; onRefresh: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const hasMultiple = group.issues.length > 1

  return (
    <div className="border-b border-gray-700 last:border-b-0">
      {/* Group header - clickable if multiple issues */}
      <div
        className={`flex items-start gap-3 py-2 px-4 bg-gray-800 ${hasMultiple ? 'cursor-pointer hover:bg-gray-750' : ''}`}
        onClick={() => hasMultiple && setExpanded(!expanded)}
      >
        {/* Expand/collapse indicator */}
        {hasMultiple ? (
          <span className="text-gray-500 mt-0.5 w-4 flex-shrink-0">
            {expanded ? '▼' : '▶'}
          </span>
        ) : (
          <span className="w-4 flex-shrink-0" />
        )}

        {/* Category badge */}
        <span className="px-2 py-0.5 rounded text-xs bg-red-600 text-red-100 flex-shrink-0">
          test
        </span>

        {/* Error message and affected tests count */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            {hasMultiple && (
              <span className="px-1.5 py-0.5 rounded text-xs bg-blue-600 text-blue-100 flex-shrink-0">
                {group.total_affected_tests} tests
              </span>
            )}
            <span className="text-red-400 text-sm font-mono truncate">
              {group.error_message || 'Unknown error'}
            </span>
          </div>
          {/* Show first failing test if single issue */}
          {!hasMultiple && group.issues[0] && (
            <div className="text-gray-400 text-xs mt-1 truncate">
              {Array.isArray(group.issues[0].failing_test)
                ? group.issues[0].failing_test[0]
                : group.issues[0].failing_test}
            </div>
          )}
        </div>

        {/* Issue badge and build info */}
        <div className="flex items-center gap-2 flex-shrink-0">
          {group.linked_issue_number ? (
            <a
              href={group.linked_issue_url || '#'}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="px-2 py-0.5 rounded text-xs bg-github-green text-white"
            >
              #{group.linked_issue_number}
            </a>
          ) : (
            <span className="px-2 py-0.5 rounded text-xs bg-gray-600 text-gray-300">
              No Issue
            </span>
          )}
          <span className="text-gray-500 text-xs">
            #{group.first_seen_build}
            {group.first_seen_build !== group.last_seen_build && ` → #${group.last_seen_build}`}
          </span>
        </div>
      </div>

      {/* Expanded view showing individual issues */}
      {expanded && (
        <div className="bg-gray-850 border-t border-gray-700">
          {group.issues.map((issue, idx) => (
            <CurrentIssueFailureRow key={idx} issue={issue} onRefresh={onRefresh} nested />
          ))}
        </div>
      )}

      {/* Single issue - show inline details */}
      {!hasMultiple && group.issues[0] && (
        <CurrentIssueFailureRow issue={group.issues[0]} onRefresh={onRefresh} inline />
      )}
    </div>
  )
}

function CurrentIssuesSection({ groups, onRefresh }: { groups: CurrentIssueGroup[]; onRefresh: () => void }) {
  const totalIssues = groups.reduce((sum, g) => sum + g.issues.length, 0)

  if (groups.length === 0) {
    return (
      <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
        <div className="text-green-400 text-center">No current test failures</div>
      </div>
    )
  }

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
      <div className="p-3 border-b border-gray-700">
        <h2 className="text-white font-medium">Current Issues ({totalIssues} tests in {groups.length} groups)</h2>
        <p className="text-gray-500 text-xs">Test failures from latest nightly/daily that haven't passed on main since</p>
      </div>
      <div>
        {groups.map((group, idx) => (
          <CurrentIssueGroupRow key={idx} group={group} onRefresh={onRefresh} />
        ))}
      </div>
    </div>
  )
}

function IssueStatusBadge({ job }: { job: FailedJobSummary }) {
  if (!job.linked_issue_number) {
    return (
      <span className="px-2 py-0.5 rounded text-xs bg-gray-600 text-gray-300">
        No Issue
      </span>
    )
  }

  const isOpen = job.linked_issue_state === 'open' || job.linked_issue_state === 'OPEN'

  return (
    <a
      href={job.linked_issue_url || '#'}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()}
      className={`px-2 py-0.5 rounded text-xs inline-flex items-center space-x-1 ${
        isOpen ? 'bg-github-green text-white' : 'bg-github-purple text-white'
      }`}
    >
      <span>#{job.linked_issue_number}</span>
      <span>{isOpen ? 'open' : 'closed'}</span>
    </a>
  )
}

function CategoryBadge({
  category,
  editable = false,
  onEdit
}: {
  category: string | null
  editable?: boolean
  onEdit?: (newCategory: string) => void
}) {
  const [showMenu, setShowMenu] = useState(false)

  const handleClick = (e: React.MouseEvent) => {
    if (!editable) return
    e.stopPropagation()
    setShowMenu(!showMenu)
  }

  const handleSelect = (newCategory: string) => {
    onEdit?.(newCategory)
    setShowMenu(false)
  }

  const badgeClass = category === 'infra'
    ? 'bg-yellow-600 text-yellow-100'
    : category === 'test'
    ? 'bg-red-600 text-red-100'
    : 'bg-gray-600 text-gray-300'

  const label = category || 'unknown'

  return (
    <div className="relative inline-block">
      <span
        className={`px-2 py-0.5 rounded text-xs ${badgeClass} ${editable ? 'cursor-pointer hover:opacity-90' : ''}`}
        onClick={handleClick}
        title={editable ? 'Click to change category' : undefined}
      >
        {label}
        {editable && <span className="ml-1 text-xs">▾</span>}
      </span>
      {showMenu && (
        <div
          className="absolute left-0 top-full z-50 mt-1 bg-gray-800 border border-gray-600 rounded shadow-lg"
          style={{ minWidth: '80px' }}
        >
          <button
            onClick={(e) => { e.stopPropagation(); handleSelect('infra') }}
            className={`block w-full text-left px-3 py-1 text-xs hover:bg-gray-700 ${category === 'infra' ? 'text-yellow-400' : 'text-gray-200'}`}
          >
            infra
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); handleSelect('test') }}
            className={`block w-full text-left px-3 py-1 text-xs hover:bg-gray-700 ${category === 'test' ? 'text-red-400' : 'text-gray-200'}`}
          >
            test
          </button>
        </div>
      )}
    </div>
  )
}

function FailureRow({ job, onRefresh }: { job: FailedJobSummary; onRefresh: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const [suggestions, setSuggestions] = useState<FailureSuggestion[] | null>(null)
  const [loadingSuggestions, setLoadingSuggestions] = useState(false)
  const [showCreateIssue, setShowCreateIssue] = useState(false)
  const [showLinkIssue, setShowLinkIssue] = useState(false)
  const [linkInput, setLinkInput] = useState('')
  const [issueTitle, setIssueTitle] = useState('')
  const [issueBody, setIssueBody] = useState('')
  const [creating, setCreating] = useState(false)
  const [linking, setLinking] = useState<number | null>(null)
  const [linkingManual, setLinkingManual] = useState(false)
  const [unlinking, setUnlinking] = useState(false)
  const [currentCategory, setCurrentCategory] = useState(job.failure_category)
  const [updatingCategory, setUpdatingCategory] = useState(false)

  const loadSuggestions = async () => {
    // Don't load suggestions if already linked to an issue
    if (!job.failure_id || suggestions !== null || job.linked_issue_number) return
    setLoadingSuggestions(true)
    try {
      const data = await api.triages.getSuggestions(job.failure_id)
      setSuggestions(data)
    } finally {
      setLoadingSuggestions(false)
    }
  }

  const handleExpand = () => {
    if (!expanded) {
      loadSuggestions()
    }
    setExpanded(!expanded)
  }

  const handleCreateIssue = async () => {
    if (!job.failure_id || !issueTitle) return
    setCreating(true)
    try {
      await api.issues.createForFailure(job.failure_id, { title: issueTitle, body: issueBody, labels: ['ci-failure'] })
      setShowCreateIssue(false)
      setIssueTitle('')
      setIssueBody('')
      onRefresh()
    } finally {
      setCreating(false)
    }
  }

  const handleLinkSuggestion = async (issueNumber: number) => {
    if (!job.failure_id) return
    setLinking(issueNumber)
    try {
      await api.issues.linkToFailure(job.failure_id, issueNumber)
      onRefresh()
    } finally {
      setLinking(null)
    }
  }

  const handleUnlink = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!job.failure_id || !job.linked_issue_number) return
    setUnlinking(true)
    try {
      await api.issues.unlinkFromFailure(job.failure_id, job.linked_issue_number)
      onRefresh()
    } finally {
      setUnlinking(false)
    }
  }

  const handleManualLink = async () => {
    if (!job.failure_id || !linkInput.trim()) return
    // Extract issue number from input (could be number or URL like github.com/.../issues/123)
    const match = linkInput.match(/(\d+)\s*$/) || linkInput.match(/issues\/(\d+)/)
    if (!match) return
    const issueNumber = parseInt(match[1], 10)
    setLinkingManual(true)
    try {
      await api.issues.linkToFailure(job.failure_id, issueNumber)
      setShowLinkIssue(false)
      setLinkInput('')
      onRefresh()
    } finally {
      setLinkingManual(false)
    }
  }

  const handleCategoryChange = async (newCategory: string) => {
    if (!job.failure_id || updatingCategory) return
    setUpdatingCategory(true)
    try {
      await api.triages.updateFailure(job.failure_id, { failure_category: newCategory })
      setCurrentCategory(newCategory)
      onRefresh()
    } finally {
      setUpdatingCategory(false)
    }
  }

  // Use failing_test from API (already deduped by Claude)
  const getFailingTests = (): string[] => {
    if (!job.failing_test) return []
    if (Array.isArray(job.failing_test)) return job.failing_test
    return [job.failing_test]
  }

  const failingTests = getFailingTests()

  // Show appropriate label for multiple different test functions
  const getTestLabel = () => {
    if (failingTests.length === 0) return job.error_signature || 'failure'
    if (failingTests.length === 1) return failingTests[0]

    // Extract test function names (before the [ param bracket)
    const testFuncs = failingTests.map(t => {
      const match = t.match(/::([^[]+)/)
      return match ? match[1] : t
    })
    const uniqueFuncs = [...new Set(testFuncs)]

    if (uniqueFuncs.length === 1) {
      // Same test function, different params
      return failingTests[0]
    } else {
      // Different test functions
      return `${failingTests.length} different tests`
    }
  }
  const testLabel = getTestLabel()
  const additionalTests = failingTests.slice(1)

  const brief = job.error_message
    ? job.error_message.slice(0, 120) + (job.error_message.length > 120 ? '...' : '')
    : 'Unknown error'

  const generateIssueTitle = () => `[CI Failure] ${job.job_name}: ${testLabel}`

  const generateIssueBody = () => {
    const lines = [
      `## CI Failure Details`,
      ``,
      `**Job:** ${job.job_name}`,
      `**Category:** ${currentCategory || 'unknown'}`,
      `**Failure Type:** ${testLabel}`,
      job.job_url ? `**Buildkite:** ${job.job_url}` : '',
      ``,
      `## Error`,
      '```',
      job.error_message || 'No error message available',
      '```',
    ]
    if (job.error_message?.includes('pytest') || job.job_name.toLowerCase().includes('test')) {
      const testMatch = job.error_message?.match(/(\S+\.py::\S+)/)
      if (testMatch) {
        lines.push('', '## Reproduce', '```bash', `pytest ${testMatch[1]} -xvs`, '```')
      }
    }
    return lines.filter(Boolean).join('\n')
  }

  const handleShowCreateIssue = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!showCreateIssue) {
      setIssueTitle(generateIssueTitle())
      setIssueBody(generateIssueBody())
    }
    setShowCreateIssue(!showCreateIssue)
  }

  return (
    <div className="bg-gray-850">
      {/* Failure row - expandable */}
      <div
        className="py-2 px-4 pl-8 cursor-pointer hover:bg-gray-700"
        onClick={handleExpand}
      >
        {/* Line 1: category + test name + issue */}
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2 flex-1 min-w-0">
            <span className="text-gray-500 text-xs">{expanded ? '▼' : '▶'}</span>
            <CategoryBadge
              category={currentCategory}
              editable={!!job.failure_id}
              onEdit={handleCategoryChange}
            />
            {updatingCategory && <span className="text-gray-500 text-xs">...</span>}
            <span className="text-red-400 text-xs font-mono truncate">{testLabel}</span>
            {additionalTests.length > 0 && (
              <span className="text-gray-500 text-xs">+{additionalTests.length} more</span>
            )}
            {job.is_flaky && <span className="text-yellow-400 text-xs">(flaky)</span>}
          </div>
          <div className="flex items-center space-x-2 ml-2">
            <IssueStatusBadge job={job} />
            {job.linked_issue_number && (
              <button
                onClick={handleUnlink}
                disabled={unlinking}
                className="text-gray-500 hover:text-red-400 text-xs"
                title="Unlink issue"
              >
                {unlinking ? '...' : '×'}
              </button>
            )}
          </div>
        </div>
        {/* Line 2: brief */}
        <div className="pl-6 mt-1">
          <span className="text-gray-100 text-sm font-mono">{brief}</span>
        </div>
      </div>

      {/* Expanded: log snippet, suggestions, create issue */}
      {expanded && (
        <div className="px-4 pb-3 pl-12 space-y-3">
          {/* All failing tests (if multiple) */}
          {failingTests.length > 1 && (
            <div className="text-xs">
              <span className="text-gray-400">Failing tests:</span>
              <ul className="ml-4 mt-1 space-y-0.5">
                {failingTests.map((t, i) => (
                  <li key={i} className="text-red-400 font-mono">{t}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Log snippet - scrollable with ANSI colors */}
          {(job.log_excerpt || job.error_message) && (
            <div className="bg-gray-800 p-2 rounded">
              <LogSnippet log={job.log_excerpt || job.error_message || ''} />
            </div>
          )}

          {/* Suggested issues + Create issue inline */}
          <div className="space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              {!job.linked_issue_number && (
                <>
                  <button
                    onClick={handleShowCreateIssue}
                    className="px-2 py-1 bg-green-700 text-white rounded text-xs hover:bg-green-600"
                  >
                    + Create Issue
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); setShowLinkIssue(!showLinkIssue); setShowCreateIssue(false) }}
                    className="px-2 py-1 bg-blue-700 text-white rounded text-xs hover:bg-blue-600"
                  >
                    Link Issue
                  </button>
                </>
              )}
              {!job.linked_issue_number && loadingSuggestions && <span className="text-xs text-gray-500">Loading suggestions...</span>}
              {!job.linked_issue_number && suggestions && suggestions.length > 0 && (
                <span className="text-xs text-gray-400">Suggested:</span>
              )}
              {!job.linked_issue_number && suggestions && suggestions.map((s) => (
                <div
                  key={s.github_issue_number}
                  className="flex items-center gap-1 text-xs"
                  onClick={(e) => e.stopPropagation()}
                >
                  <a
                    href={s.github_issue_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`hover:underline ${s.state === 'open' ? 'text-green-400' : 'text-purple-400'}`}
                    title={`${s.title} - ${s.match_reason}`}
                  >
                    #{s.github_issue_number}
                  </a>
                  <button
                    onClick={() => handleLinkSuggestion(s.github_issue_number)}
                    disabled={linking === s.github_issue_number}
                    className="px-1.5 py-0.5 bg-blue-600 text-white rounded text-xs hover:bg-blue-500 disabled:opacity-50"
                  >
                    {linking === s.github_issue_number ? '...' : 'Link'}
                  </button>
                </div>
              ))}
              {suggestions && suggestions.length === 0 && !job.linked_issue_number && (
                <span className="text-xs text-gray-500">No suggestions</span>
              )}
            </div>

            {/* Create issue form */}
            {showCreateIssue && (
              <div className="space-y-2 p-2 bg-gray-800 rounded" onClick={(e) => e.stopPropagation()}>
                <input
                  type="text"
                  placeholder="Issue title"
                  value={issueTitle}
                  onChange={(e) => setIssueTitle(e.target.value)}
                  className="w-full px-2 py-1 bg-gray-700 text-white rounded text-sm"
                />
                <textarea
                  placeholder="Issue body"
                  value={issueBody}
                  onChange={(e) => setIssueBody(e.target.value)}
                  className="w-full px-2 py-1 bg-gray-700 text-white rounded text-sm h-32 font-mono text-xs"
                />
                <div className="flex gap-2">
                  <button
                    onClick={handleCreateIssue}
                    disabled={creating || !issueTitle}
                    className="px-2 py-1 bg-green-600 text-white rounded text-xs hover:bg-green-500 disabled:opacity-50"
                  >
                    {creating ? 'Creating...' : 'Create'}
                  </button>
                  <button
                    onClick={() => setShowCreateIssue(false)}
                    className="px-2 py-1 bg-gray-600 text-white rounded text-xs hover:bg-gray-500"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Link issue form */}
            {showLinkIssue && (
              <div className="flex items-center gap-2 p-2 bg-gray-800 rounded" onClick={(e) => e.stopPropagation()}>
                <input
                  type="text"
                  placeholder="Issue # or URL (e.g., 123 or github.com/.../issues/123)"
                  value={linkInput}
                  onChange={(e) => setLinkInput(e.target.value)}
                  className="flex-1 px-2 py-1 bg-gray-700 text-white rounded text-sm"
                  onKeyDown={(e) => e.key === 'Enter' && handleManualLink()}
                />
                <button
                  onClick={handleManualLink}
                  disabled={linkingManual || !linkInput.trim()}
                  className="px-2 py-1 bg-blue-600 text-white rounded text-xs hover:bg-blue-500 disabled:opacity-50"
                >
                  {linkingManual ? '...' : 'Link'}
                </button>
                <button
                  onClick={() => { setShowLinkIssue(false); setLinkInput('') }}
                  className="px-2 py-1 bg-gray-600 text-white rounded text-xs hover:bg-gray-500"
                >
                  Cancel
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function BuildCard({ build, onSync, onRefresh }: { build: BuildWithFailures; onSync: (buildNumber: number) => void; onRefresh: () => void }) {
  const infraCount = build.failed_jobs.filter(j => j.failure_category === 'infra').length
  const testCount = build.failed_jobs.filter(j => j.failure_category === 'test').length
  const hasRealFailures = build.failed_jobs.length > 0
  const [syncing, setSyncing] = useState(false)

  // State colors: failed (red), failing (red if real failures), running (blue), passed (green)
  const getStateColor = () => {
    if (build.state === 'running' || build.state === 'scheduled') return 'text-blue-400'
    if (build.state === 'passed') return 'text-green-400'
    if (build.state === 'failed') return 'text-red-400'
    if (build.state === 'failing') {
      return hasRealFailures ? 'text-red-400' : 'text-yellow-400'
    }
    return 'text-gray-400'
  }
  const stateColor = getStateColor()

  const handleSync = async (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setSyncing(true)
    try {
      await onSync(build.buildkite_build_number)
    } finally {
      setSyncing(false)
    }
  }

  const commitUrl = build.commit_sha
    ? `https://github.com/vllm-project/vllm/commit/${build.commit_sha}`
    : null

  // Extract PR number from commit message (e.g., "(#12345)" or "PR #12345")
  const prMatch = build.message?.match(/\(#(\d+)\)|PR\s*#(\d+)/i)
  const prNumber = prMatch ? (prMatch[1] || prMatch[2]) : null
  const prUrl = prNumber
    ? `https://github.com/vllm-project/vllm/pull/${prNumber}`
    : null

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
      <div className="p-4 hover:bg-gray-750 transition-colors">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <Link
              to={`/builds/${build.buildkite_build_number}`}
              className="text-white font-mono text-lg hover:underline"
            >
              #{build.buildkite_build_number}
            </Link>
            {commitUrl && (
              <a
                href={commitUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-400 hover:underline text-xs font-mono"
                title="View commit on GitHub"
              >
                {build.commit_sha?.slice(0, 7)}
              </a>
            )}
            {prUrl && (
              <a
                href={prUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-purple-400 hover:underline text-xs"
                title={`View PR #${prNumber} on GitHub`}
              >
                #{prNumber}
              </a>
            )}
            <span className={stateColor}>{build.state}</span>
            {build.build_type && (
              <span className="bg-gray-700 px-2 py-0.5 rounded text-xs text-gray-300">
                {build.build_type}
              </span>
            )}
          </div>
          <div className="flex items-center space-x-4 text-sm">
            <span className="text-gray-400">{build.total_jobs} jobs</span>
            {build.failed_jobs.length > 0 && (
              <div className="flex space-x-2">
                {infraCount > 0 && (
                  <span className="text-yellow-400">{infraCount} infra</span>
                )}
                {testCount > 0 && (
                  <span className="text-red-400">{testCount} test</span>
                )}
              </div>
            )}
            <button
              onClick={handleSync}
              disabled={syncing}
              className="px-2 py-1 bg-gray-700 text-gray-300 rounded text-xs hover:bg-gray-600 disabled:opacity-50"
              title="Sync this build from Buildkite"
            >
              {syncing ? '...' : '⇅'}
            </button>
          </div>
        </div>
        <Link
          to={`/builds/${build.buildkite_build_number}`}
          className="block mt-1 text-gray-500 text-xs"
        >
          {build.branch} • {build.created_at ? new Date(build.created_at).toLocaleString() : 'N/A'}
        </Link>
      </div>

      {build.failed_jobs.length > 0 && (
        <div className="border-t border-gray-700">
          {/* Group failures by job_id */}
          {(() => {
            const byJob: Record<number, FailedJobSummary[]> = {}
            for (const job of build.failed_jobs) {
              if (!byJob[job.job_id]) byJob[job.job_id] = []
              byJob[job.job_id].push(job)
            }
            return Object.entries(byJob).map(([jobId, failures]) => {
              const firstJob = failures[0]
              return (
                <div key={jobId} className="border-b border-gray-700 last:border-b-0">
                  {/* Job header row - always visible */}
                  <div className="flex items-center justify-between py-2 px-4 bg-gray-800">
                    <span className="text-gray-200 font-medium text-sm">{firstJob.job_name}</span>
                    <div className="flex items-center space-x-2">
                      <button
                        onClick={() => api.jobs.retry(firstJob.job_id).then(onRefresh)}
                        className="px-2 py-0.5 bg-gray-600 text-gray-200 rounded text-xs hover:bg-gray-500"
                        title="Retry job"
                      >
                        ⟳
                      </button>
                      {firstJob.job_url && (
                        <a
                          href={firstJob.job_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-blue-400 hover:underline text-xs"
                        >
                          BK
                        </a>
                      )}
                    </div>
                  </div>
                  {/* Failure rows for this job */}
                  {failures.map((job) => (
                    <FailureRow key={job.failure_id} job={job} onRefresh={onRefresh} />
                  ))}
                </div>
              )
            })
          })()}
        </div>
      )}
    </div>
  )
}

export default function Dashboard() {
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<'all' | 'main' | 'nightly_daily'>('nightly_daily')

  const { data: currentIssueGroups } = useQuery<CurrentIssueGroup[]>({
    queryKey: ['current-issues-grouped'],
    queryFn: api.builds.currentIssuesGrouped,
  })

  const { data: builds, isLoading } = useQuery<BuildWithFailures[]>({
    queryKey: ['builds', activeTab],
    queryFn: () => {
      if (activeTab === 'nightly_daily') {
        return api.builds.list({ nightly_daily: 'true' })
      } else if (activeTab === 'main') {
        return api.builds.list({ branch: 'main' })
      }
      return api.builds.list({})
    },
  })

  const syncMutation = useMutation({
    mutationFn: () => api.builds.sync(20),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['builds'] })
      queryClient.invalidateQueries({ queryKey: ['current-issues-grouped'] })
    },
  })

  const syncBuildMutation = useMutation({
    mutationFn: (buildNumber: number) => api.builds.syncBuild(buildNumber),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['builds'] })
      queryClient.invalidateQueries({ queryKey: ['current-issues-grouped'] })
    },
  })

  const handleSyncBuild = async (buildNumber: number) => {
    await syncBuildMutation.mutateAsync(buildNumber)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">CI Triage Dashboard</h1>
        <button
          onClick={() => syncMutation.mutate()}
          disabled={syncMutation.isPending}
          className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {syncMutation.isPending ? 'Syncing...' : 'Sync Builds'}
        </button>
      </div>

      <TriageStatusIndicator />

      {currentIssueGroups && (
        <CurrentIssuesSection groups={currentIssueGroups} onRefresh={() => {
          queryClient.invalidateQueries({ queryKey: ['current-issues-grouped'] })
          queryClient.invalidateQueries({ queryKey: ['builds'] })
        }} />
      )}

      {/* Tabs */}
      <div className="flex space-x-1 bg-gray-800 p-1 rounded-lg w-fit">
        <button
          onClick={() => setActiveTab('all')}
          className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
            activeTab === 'all'
              ? 'bg-blue-600 text-white'
              : 'text-gray-400 hover:text-white hover:bg-gray-700'
          }`}
        >
          All
        </button>
        <button
          onClick={() => setActiveTab('main')}
          className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
            activeTab === 'main'
              ? 'bg-blue-600 text-white'
              : 'text-gray-400 hover:text-white hover:bg-gray-700'
          }`}
        >
          Main
        </button>
        <button
          onClick={() => setActiveTab('nightly_daily')}
          className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
            activeTab === 'nightly_daily'
              ? 'bg-blue-600 text-white'
              : 'text-gray-400 hover:text-white hover:bg-gray-700'
          }`}
        >
          Nightly/Daily
        </button>
      </div>

      {isLoading ? (
        <div className="text-gray-400">Loading builds...</div>
      ) : (
        <div className="space-y-4">
          {builds?.map((build) => (
            <BuildCard key={build.id} build={build} onSync={handleSyncBuild} onRefresh={() => queryClient.invalidateQueries({ queryKey: ['builds'] })} />
          ))}
          {builds?.length === 0 && (
            <div className="text-gray-400 text-center py-8">
              No builds found. Click "Sync Builds" to fetch from Buildkite.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

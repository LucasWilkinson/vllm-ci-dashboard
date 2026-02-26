import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, BuildInTimeline, CommitTimelineEntry, FailedJobSummary, KnownFailure, BuildRef } from '../api/client'

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

interface TriageProgress {
  build_number: number
  total_jobs: number
  completed_jobs: number
  current_job: string | null
  status: 'pending' | 'running' | 'completed' | 'error'
  phase: 'fetching_logs' | 'analyzing' | 'processing' | 'individual'
}

function triagePhaseLabel(t: TriageProgress): string {
  switch (t.phase) {
    case 'fetching_logs': return `Fetching logs for ${t.total_jobs} failed jobs...`
    case 'analyzing': return `Analyzing ${t.total_jobs} failed jobs...`
    case 'processing': return `Saving results for ${t.total_jobs} jobs...`
    case 'individual': return `${t.completed_jobs}/${t.total_jobs} jobs (fallback)`
    default: return `${t.total_jobs} failed jobs`
  }
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
            {triagePhaseLabel(t)}
          </span>
          {t.phase === 'individual' && t.current_job && (
            <span className="text-blue-300 truncate">→ {t.current_job}</span>
          )}
        </div>
      ))}
    </div>
  )
}


// ============================================================================
// KnownFailures Section (replaces CurrentIssuesSection)
// ============================================================================

function BuildRefDisplay({ label, buildRef }: { label: string; buildRef: BuildRef }) {
  return (
    <span className="text-xs text-gray-500">
      <span className="text-gray-600">{label}:</span>{' '}
      #{buildRef.build_number}
      {buildRef.commit_sha && (
        <>
          {' '}
          <a
            href={`https://github.com/vllm-project/vllm/commit/${buildRef.commit_sha}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-400 hover:text-blue-300 font-mono"
            onClick={(e) => e.stopPropagation()}
          >
            {buildRef.commit_sha.slice(0, 8)}
          </a>
        </>
      )}
      {(buildRef.committed_at || buildRef.created_at) && (
        <span className="text-gray-600"> ({timeAgo(buildRef.committed_at || buildRef.created_at)})</span>
      )}
    </span>
  )
}

function KnownFailureRow({ kf }: { kf: KnownFailure }) {
  return (
    <Link
      to={`/known-failures/${kf.id}`}
      className="flex items-start gap-3 py-2 px-4 bg-gray-800 hover:bg-gray-750 border-b border-gray-700 last:border-b-0"
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-gray-600 text-xs font-mono">#{kf.id}</span>
          <span className="text-red-400 text-sm font-mono truncate">
            {kf.title}
          </span>
          {kf.is_flaky && (
            <span className="text-yellow-400 text-xs">(likely flaky)</span>
          )}
          {kf.status === 'resolved' && kf.resolved_by === 'auto' && (
            <span className="px-1.5 py-0.5 rounded text-xs bg-green-700 text-green-200">
              auto-resolved
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 mt-0.5 text-xs flex-wrap">
          {kf.affected_jobs.map((job) => (
            <span key={job} className="px-1.5 py-0.5 rounded bg-gray-700 text-gray-300 font-mono">
              {job}
            </span>
          ))}
        </div>
        {kf.status === 'resolved' && kf.resolved_by === 'auto' && kf.resolved_in_build && (
          <div className="mt-0.5 text-xs text-green-400">
            first pass on{' '}
            <span className="font-mono text-green-300">
              {kf.resolved_in_build.commit_sha?.slice(0, 8)}
            </span>
            {kf.resolved_in_build.message && (
              <span className="text-gray-400 ml-1">
                {kf.resolved_in_build.message.split('\n')[0].slice(0, 80)}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Build range */}
      <div className="flex-shrink-0 text-xs text-gray-500 whitespace-nowrap space-y-0.5 text-right">
        {kf.first_seen_build && kf.last_seen_build && kf.first_seen_build.build_number !== kf.last_seen_build.build_number && (
          <div>
            <BuildRefDisplay label="first seen" buildRef={kf.first_seen_build} />
          </div>
        )}
        {kf.last_seen_build && (
          <div>
            <BuildRefDisplay
              label={kf.first_seen_build && kf.first_seen_build.build_number !== kf.last_seen_build.build_number ? 'last seen' : 'seen in'}
              buildRef={kf.last_seen_build}
            />
          </div>
        )}
      </div>

      {/* Issue badge */}
      <div className="flex items-center gap-2 flex-shrink-0">
        {kf.github_issue ? (
          <span className={`px-2 py-0.5 rounded text-xs text-white ${kf.github_issue.state === 'closed' ? 'bg-github-purple' : 'bg-github-green'}`}>
            #{kf.github_issue.github_issue_number}
          </span>
        ) : kf.resolved_by_pr ? (
          <span className="px-2 py-0.5 rounded text-xs bg-purple-600 text-white">
            PR #{kf.resolved_by_pr}
          </span>
        ) : (
          <span className="px-2 py-0.5 rounded text-xs bg-gray-600 text-gray-300">
            No Issue
          </span>
        )}
      </div>
    </Link>
  )
}

function KnownFailuresSection({ knownFailures, flakyFailures, resolvedFailures }: { knownFailures: KnownFailure[]; flakyFailures: KnownFailure[]; resolvedFailures: KnownFailure[] }) {
  const [issueTab, setIssueTab] = useState<'open' | 'flaky' | 'resolved'>('open')
  const displayList = issueTab === 'open' ? knownFailures : issueTab === 'flaky' ? flakyFailures : resolvedFailures

  const headerText = issueTab === 'open'
    ? { title: `Current Issues (${knownFailures.length})`, subtitle: 'Active test failures across builds' }
    : issueTab === 'flaky'
    ? { title: `Flaky Issues (${flakyFailures.length})`, subtitle: 'Intermittent failures — require manual resolution' }
    : { title: `Recently Resolved (${resolvedFailures.length})`, subtitle: 'Issues resolved in the last 48 hours' }

  const emptyText = issueTab === 'open' ? 'No current issues' : issueTab === 'flaky' ? 'No flaky issues' : 'No recently resolved issues'

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
      <div className="p-3 border-b border-gray-700 flex items-center justify-between">
        <div>
          <h2 className="text-white font-medium">{headerText.title}</h2>
          <p className="text-gray-500 text-xs">{headerText.subtitle}</p>
        </div>
        <div className="flex space-x-1 bg-gray-700 p-0.5 rounded">
          <button
            onClick={() => setIssueTab('open')}
            className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
              issueTab === 'open'
                ? 'bg-red-600 text-white'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Open ({knownFailures.length})
          </button>
          <button
            onClick={() => setIssueTab('flaky')}
            className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
              issueTab === 'flaky'
                ? 'bg-yellow-600 text-white'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Flaky ({flakyFailures.length})
          </button>
          <button
            onClick={() => setIssueTab('resolved')}
            className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
              issueTab === 'resolved'
                ? 'bg-green-600 text-white'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Resolved ({resolvedFailures.length})
          </button>
        </div>
      </div>
      <div>
        {displayList.length === 0 ? (
          <div className="p-4 text-center text-gray-500">{emptyText}</div>
        ) : (
          displayList.map((kf) => (
            <KnownFailureRow key={kf.id} kf={kf} />
          ))
        )}
      </div>
    </div>
  )
}


function BuildPill({ build }: { build: BuildInTimeline }) {
  const getStateColor = () => {
    if (build.state === 'running' || build.state === 'scheduled') return 'bg-blue-500'
    if (build.state === 'passed') return 'bg-green-500'
    if (build.state === 'failed') return 'bg-red-500'
    if (build.state === 'failing') return build.failed_job_count > 0 ? 'bg-red-500' : 'bg-yellow-500'
    return 'bg-gray-500'
  }

  return (
    <Link
      to={`/builds/${build.build_number}`}
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-gray-700 hover:bg-gray-600 text-xs"
      onClick={(e) => e.stopPropagation()}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${getStateColor()}`} />
      <span className="text-gray-300 font-mono">#{build.build_number}</span>
      {build.build_type && (
        <span className="text-gray-500">{build.build_type}</span>
      )}
    </Link>
  )
}

function CommitTimelineRow({ entry }: { entry: CommitTimelineEntry }) {
  const infraCount = entry.failed_jobs.filter(j => j.failure_category === 'infra').length
  const testFailures = entry.failed_jobs.filter(j => j.failure_category !== 'infra')
  const hasFailures = entry.failed_jobs.length > 0
  const passedCount = entry.builds.reduce((sum, b) => sum + b.passed_job_count, 0)
  const notRunCount = entry.builds.reduce((sum, b) => sum + b.not_run_job_count, 0)

  const isNotTriaged = entry.status === 'not_triaged'

  const getStatusDotColor = () => {
    if (isNotTriaged) return 'bg-gray-600'
    if (entry.status === 'running' || entry.status === 'scheduled') return 'bg-blue-400'
    if (entry.status === 'passed') return 'bg-green-400'
    if (entry.status === 'failed') return 'bg-red-400'
    if (entry.status === 'failing') return hasFailures ? 'bg-red-400' : 'bg-yellow-400'
    return 'bg-gray-400'
  }

  const commitUrl = entry.commit_sha
    ? `https://github.com/vllm-project/vllm/commit/${entry.commit_sha}`
    : null

  // Extract PR number from commit message
  const prMatch = entry.message?.match(/\(#(\d+)\)|PR\s*#(\d+)/i)
  const prNumber = prMatch ? (prMatch[1] || prMatch[2]) : null
  const prUrl = prNumber
    ? `https://github.com/vllm-project/vllm/pull/${prNumber}`
    : null

  const firstLine = entry.message?.split('\n')[0] || ''
  // Remove the PR number suffix from the message for display since we show it separately
  const displayMessage = firstLine.replace(/\s*\(#\d+\)\s*$/, '').slice(0, 80)

  // Group test failures by job, then by known_failure within each job
  const byJob: Record<string, FailedJobSummary[]> = {}
  for (const job of testFailures) {
    if (!byJob[job.job_name]) byJob[job.job_name] = []
    byJob[job.job_name].push(job)
  }
  const jobGroups = Object.entries(byJob).sort(([a], [b]) => a.localeCompare(b))

  const infraFailures = entry.failed_jobs.filter(j => j.failure_category === 'infra')

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
      {/* Commit row */}
      <div className="px-4 py-3 transition-colors">
        <div className="flex items-center gap-3">
          {/* Status dot */}
          <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${getStatusDotColor()}`} />

          {/* Commit info */}
          <div className="flex items-center gap-2 min-w-0 flex-1">
            {commitUrl ? (
              <a
                href={commitUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-400 hover:underline text-xs font-mono flex-shrink-0"
              >
                {entry.commit_sha?.slice(0, 8)}
              </a>
            ) : (
              <span className="text-gray-500 text-xs font-mono flex-shrink-0">no commit</span>
            )}
            {prUrl && (
              <a
                href={prUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-purple-400 hover:underline text-xs flex-shrink-0"
              >
                #{prNumber}
              </a>
            )}

            {/* Build pills */}
            <div className="flex items-center gap-1 flex-shrink-0">
              {entry.builds.map((build) => (
                <BuildPill key={build.build_number} build={build} />
              ))}
            </div>

            {/* Commit message */}
            <span className="text-gray-400 text-sm truncate">{displayMessage}</span>
          </div>

          {/* Right side: job counts + time */}
          <div className="flex items-center gap-3 flex-shrink-0">
            {isNotTriaged ? (
              <span className="text-gray-600 text-xs">not triaged</span>
            ) : (
              <div className="flex items-center gap-2 text-xs">
                {passedCount > 0 && (
                  <span className="text-green-400">{passedCount} pass</span>
                )}
                {testFailures.length > 0 && (
                  <span className="text-red-400">{testFailures.length} fails</span>
                )}
                {infraCount > 0 && (
                  <span className="text-yellow-400">{infraCount} infra</span>
                )}
                {notRunCount > 0 && (
                  <span className="text-gray-500">{notRunCount} not run</span>
                )}
              </div>
            )}
            <div className="flex flex-col items-end flex-shrink-0">
              <span className="text-gray-500 text-xs whitespace-nowrap">
                {timeAgo(entry.committed_at)}
              </span>
              {entry.created_at && (
                <span className="text-gray-600 text-xs whitespace-nowrap">
                  triaged {timeAgo(entry.created_at)}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Test failures grouped by job, then by known issue */}
      {jobGroups.length > 0 && (
        <div className="border-t border-gray-700">
          {jobGroups.map(([jobName, failures]) => {
            // Group failures within this job by KF
            const byKF: Record<string, FailedJobSummary[]> = {}
            for (const f of failures) {
              const key = f.known_failure_id ? String(f.known_failure_id) : 'unassigned'
              if (!byKF[key]) byKF[key] = []
              byKF[key].push(f)
            }
            const kfEntries = Object.entries(byKF).sort(([a], [b]) => {
              if (a === 'unassigned') return 1
              if (b === 'unassigned') return -1
              return Number(a) - Number(b)
            })

            // Get Buildkite job URL from the first failure in this job group
            const jobUrl = failures[0]?.job_url

            return (
              <div key={jobName} className="border-b border-gray-700 last:border-b-0">
                <div className="px-4 py-1.5 pl-8 text-xs font-mono text-gray-400 bg-gray-800/50">
                  {jobUrl ? (
                    <a
                      href={jobUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-gray-400 hover:text-blue-400 hover:underline"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {jobName} ↗
                    </a>
                  ) : (
                    jobName
                  )}
                </div>
                {kfEntries.map(([key, kfFailures]) => {
                  const kfId = key !== 'unassigned' ? Number(key) : null
                  const kfTitle = kfId ? kfFailures[0].known_failure_title : null

                  // Collect unique failing tests from all failures in this KF group
                  const testSet = new Set<string>()
                  for (const f of kfFailures) {
                    if (f.failing_test) {
                      if (Array.isArray(f.failing_test)) {
                        f.failing_test.forEach(t => testSet.add(t))
                      } else {
                        testSet.add(f.failing_test)
                      }
                    }
                  }
                  const allTests = Array.from(testSet)

                  const header = (
                    <div className="flex items-center gap-2 py-1.5 px-4 pl-12">
                      {kfId ? (
                        <>
                          <span className="text-gray-500 text-xs font-mono">KF#{kfId}</span>
                          <span className="text-red-400 text-xs truncate">{kfTitle}</span>
                        </>
                      ) : (
                        <span className="text-yellow-400 text-xs">Unassigned</span>
                      )}
                    </div>
                  )

                  const testList = allTests.length > 0 && (
                    <div className="pl-16 pb-1.5">
                      {allTests.map((test, i) => (
                        <div key={i} className="text-xs font-mono text-gray-500 leading-5">
                          {test}
                        </div>
                      ))}
                    </div>
                  )

                  if (kfId) {
                    return (
                      <Link
                        key={key}
                        to={`/known-failures/${kfId}`}
                        className="block hover:bg-gray-750"
                      >
                        {header}
                        {testList}
                      </Link>
                    )
                  }

                  // Unassigned: group by error message to collapse duplicates
                  const byError: Record<string, FailedJobSummary[]> = {}
                  for (const f of kfFailures) {
                    const errKey = f.error_message?.split('\n')[0].slice(0, 120) || 'unknown'
                    if (!byError[errKey]) byError[errKey] = []
                    byError[errKey].push(f)
                  }

                  return (
                    <div key={key}>
                      {header}
                      <div className="pl-16 pb-1.5 space-y-1">
                        {Object.entries(byError).map(([errMsg, errFailures]) => {
                          const errTestSet = new Set<string>()
                          for (const f of errFailures) {
                            if (f.failing_test) {
                              if (Array.isArray(f.failing_test)) f.failing_test.forEach(t => errTestSet.add(t))
                              else errTestSet.add(f.failing_test)
                            }
                          }
                          const allErrTests = Array.from(errTestSet)
                          return (
                            <div key={errMsg}>
                              <div className="text-xs text-yellow-600 leading-5 truncate">
                                {errMsg}
                                {errFailures.length > 1 && (
                                  <span className="text-gray-500 ml-1">({errFailures.length} tests)</span>
                                )}
                              </div>
                              {allErrTests.length > 0 && allErrTests.length <= 5 && allErrTests.map((t, j) => (
                                <div key={j} className="text-xs font-mono text-gray-500 leading-5 pl-2">{t}</div>
                              ))}
                              {allErrTests.length > 5 && (
                                <>
                                  {allErrTests.slice(0, 3).map((t, j) => (
                                    <div key={j} className="text-xs font-mono text-gray-500 leading-5 pl-2">{t}</div>
                                  ))}
                                  <div className="text-xs text-gray-600 leading-5 pl-2">...and {allErrTests.length - 3} more</div>
                                </>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )
                })}
              </div>
            )
          })}
        </div>
      )}

      {/* Infra errors - collapsible, grouped by job */}
      {infraFailures.length > 0 && (() => {
        const infraByJob: Record<string, FailedJobSummary[]> = {}
        for (const f of infraFailures) {
          if (!infraByJob[f.job_name]) infraByJob[f.job_name] = []
          infraByJob[f.job_name].push(f)
        }
        const infraJobGroups = Object.entries(infraByJob).sort(([a], [b]) => a.localeCompare(b))

        return (
          <details className="border-t border-gray-700 group">
            <summary className="px-4 py-1.5 pl-8 text-xs text-yellow-400 cursor-pointer hover:bg-gray-750 select-none list-none flex items-center gap-1.5">
              <span className="text-gray-500 group-open:rotate-90 transition-transform">▶</span>
              {infraFailures.length} infra {infraFailures.length === 1 ? 'error' : 'errors'}
            </summary>
            <div>
              {infraJobGroups.map(([jobName, jobInfraFailures]) => {
                // Deduplicate by error message within the job
                const uniqueErrors: { message: string; tests: string[]; count: number }[] = []
                const seen = new Map<string, number>()
                for (const f of jobInfraFailures) {
                  const msg = f.error_message?.split('\n')[0].slice(0, 150) || 'Unknown infra error'
                  const tests: string[] = f.failing_test
                    ? Array.isArray(f.failing_test) ? f.failing_test : [f.failing_test]
                    : []
                  const idx = seen.get(msg)
                  if (idx !== undefined) {
                    uniqueErrors[idx].count++
                    uniqueErrors[idx].tests.push(...tests)
                  } else {
                    seen.set(msg, uniqueErrors.length)
                    uniqueErrors.push({ message: msg, tests: [...tests], count: 1 })
                  }
                }

                // Get the Buildkite job URL from the first failure in this job group
                const jobUrl = jobInfraFailures[0]?.job_url

                return (
                  <div key={jobName} className="px-4 pl-12 py-1.5 border-t border-gray-700/50">
                    <div className="text-xs font-mono text-gray-400">
                      {jobUrl ? (
                        <a
                          href={jobUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-gray-400 hover:text-blue-400 hover:underline"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {jobName} ↗
                        </a>
                      ) : (
                        jobName
                      )}
                    </div>
                    {uniqueErrors.map((err, i) => (
                      <div key={i} className="mt-0.5">
                        <div className="text-xs text-yellow-600 truncate">
                          {err.message}
                          {err.count > 1 && <span className="text-gray-500 ml-1">({err.count})</span>}
                        </div>
                        {err.tests.length > 0 && err.tests.slice(0, 3).map((t, j) => (
                          <div key={j} className="text-xs font-mono text-gray-500 leading-5 pl-2">{t}</div>
                        ))}
                        {err.tests.length > 3 && (
                          <div className="text-xs text-gray-600 leading-5 pl-2">...and {err.tests.length - 3} more</div>
                        )}
                      </div>
                    ))}
                  </div>
                )
              })}
            </div>
          </details>
        )
      })()}
    </div>
  )
}

export default function Dashboard() {
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<'main' | 'nightly_daily'>('main')

  const { data: knownFailures } = useQuery<KnownFailure[]>({
    queryKey: ['known-failures'],
    queryFn: () => api.knownFailures.list({ status: 'open', category: 'test', is_flaky: false }),
    staleTime: 30_000,
  })

  const { data: flakyFailures } = useQuery<KnownFailure[]>({
    queryKey: ['known-failures-flaky'],
    queryFn: () => api.knownFailures.list({ status: 'open', category: 'all', is_flaky: true }),
    staleTime: 30_000,
  })

  const { data: resolvedFailures } = useQuery<KnownFailure[]>({
    queryKey: ['known-failures-resolved'],
    queryFn: () => api.knownFailures.list({ status: 'resolved', category: 'all', resolved_since_hours: 48 }),
    staleTime: 30_000,
  })

  const { data: timeline, isLoading } = useQuery<CommitTimelineEntry[]>({
    queryKey: ['timeline', activeTab],
    queryFn: () => {
      if (activeTab === 'nightly_daily') {
        return api.builds.timeline({ nightly_daily: 'true' })
      }
      return api.builds.timeline({})
    },
    staleTime: 30_000,
  })

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ['timeline'] })
    queryClient.invalidateQueries({ queryKey: ['known-failures'] })
    queryClient.invalidateQueries({ queryKey: ['known-failures-flaky'] })
    queryClient.invalidateQueries({ queryKey: ['known-failures-resolved'] })
  }

  const syncMutation = useMutation({
    mutationFn: () => api.builds.sync(20),
    onSuccess: invalidateAll,
  })


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

      {knownFailures && (
        <KnownFailuresSection knownFailures={knownFailures} flakyFailures={flakyFailures || []} resolvedFailures={resolvedFailures || []} />
      )}

      {/* Tabs */}
      <div className="flex space-x-1 bg-gray-800 p-1 rounded-lg w-fit">
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
        <div className="space-y-2">
          {timeline?.map((entry, idx) => (
            <CommitTimelineRow key={entry.commit_sha || idx} entry={entry} />
          ))}
          {timeline?.length === 0 && (
            <div className="text-gray-400 text-center py-8">
              No builds found. Click "Sync Builds" to fetch from Buildkite.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

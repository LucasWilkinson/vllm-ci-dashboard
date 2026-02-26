const API_BASE = '/api'

async function fetchApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
    ...options,
  })

  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`)
  }

  return response.json()
}

export interface FailedJobSummary {
  job_id: number
  failure_id: number | null
  job_name: string
  step_key: string | null
  job_url: string | null
  failure_category: string | null
  failure_type: string | null
  failing_test: string | string[] | null
  error_signature: string | null
  error_message: string | null
  log_excerpt: string | null
  flaky_status: string | null
  known_failure_id: number | null
  known_failure_title: string | null
  linked_issue_number: number | null
  linked_issue_state: string | null
  linked_issue_url: string | null
}

export interface BuildWithFailures {
  id: number
  buildkite_build_number: number
  build_type: string | null
  state: string | null
  commit_sha: string | null
  branch: string | null
  message: string | null
  web_url: string | null
  triage_status: string
  created_at: string | null
  total_jobs: number
  failed_jobs: FailedJobSummary[]
}

export interface Failure {
  id: number
  job_id: number
  failure_category: string | null
  failure_type: string | null
  error_signature: string | null
  error_message: string | null
  root_cause: string | null
  is_flaky: boolean
  retry_passed: boolean
  log_excerpt: string | null
}

export interface Job {
  id: number
  build_id: number
  buildkite_job_id: string
  name: string | null
  state: string | null
  step_key: string | null
  web_url: string | null
  retry_count: number
  failures: Failure[]
}

export interface Build {
  id: number
  buildkite_build_number: number
  build_type: string | null
  state: string | null
  commit_sha: string | null
  branch: string | null
  message: string | null
  web_url: string | null
  triage_status: string
  created_at: string | null
  total_jobs: number
  synced_at: string
  jobs: Job[]
}

export interface FailureSuggestion {
  github_issue_number: number
  title: string
  state: string
  github_issue_url: string
  similarity_score: number
  match_reason: string
}

export interface GitHubIssue {
  id: number
  github_issue_number: number
  title: string | null
  state: string | null
  github_issue_url: string | null
}

// KnownFailure interfaces
export interface KnownFailureInstance {
  failure_id: number
  job_id: number
  job_name: string
  job_url: string | null
  failing_test: string | string[] | null
  error_message: string | null
  log_excerpt: string | null
}

export interface FailuresByBuild {
  build_number: number
  build_url: string | null
  commit_sha: string | null
  committed_at: string | null
  created_at: string | null
  commits_behind: number
  failures: KnownFailureInstance[]
}

export interface BuildRef {
  build_number: number
  commit_sha: string | null
  committed_at: string | null
  created_at: string | null
  message: string | null
}

export interface KnownFailure {
  id: number
  title: string
  summary: string | null
  match_prompt: string | null
  category: string | null
  status: string
  is_flaky: boolean
  github_issue: GitHubIssue | null
  resolved_by_pr: number | null
  resolved_by: string | null
  resolved_in_build: BuildRef | null
  first_seen_build: BuildRef | null
  last_seen_build: BuildRef | null
  failure_count: number
  affected_jobs: string[]
  failures_by_build?: FailuresByBuild[]
}

export type HistoryStatus = 'not_run' | 'job_fail' | 'infra_fail' | 'other_fail' | 'diff_fail' | 'fail' | 'pass' | 'flaky_pass'

export interface BuildInHistory {
  build_number: number
  build_url: string | null
  build_type: string | null
  status: HistoryStatus
}

export interface BuildHistoryEntry {
  commit_sha: string | null
  committed_at: string | null
  created_at: string | null
  message: string | null
  status: HistoryStatus  // Aggregate worst status across builds for this commit
  triaged: boolean  // False for commits with no DB builds (filled from GitHub)
  builds: BuildInHistory[]
  failures: KnownFailureInstance[]
}

export interface KnownFailureHistory {
  known_failure_id: number
  title: string
  affected_jobs: string[]
  affected_tests: string[]
  predates_history: boolean
  no_prior_runs: boolean
  is_flaky: boolean
  entries: BuildHistoryEntry[]
}

export interface BuildInTimeline {
  build_number: number
  build_type: string | null
  state: string | null
  web_url: string | null
  triage_status: string
  total_jobs: number
  failed_job_count: number
  passed_job_count: number
  not_run_job_count: number
}

export interface CommitTimelineEntry {
  commit_sha: string | null
  message: string | null
  committed_at: string | null  // git commit time
  created_at: string | null    // build/triage time (null if not triaged)
  status: string  // worst aggregate state across builds
  builds: BuildInTimeline[]
  failed_jobs: FailedJobSummary[]
}

function cleanParams(params?: Record<string, string | undefined>): Record<string, string> {
  if (!params) return {}
  return Object.fromEntries(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== '')
  ) as Record<string, string>
}

export interface HealthCheck {
  status: 'healthy' | 'degraded'
  checks: Record<string, string>
}

export const api = {
  health: () => fetchApi<HealthCheck>('/health'),
  builds: {
    get: (buildNumber: number) =>
      fetchApi<Build>(`/builds/${buildNumber}`),
    sync: (limit?: number) =>
      fetchApi<{ synced: number; triaged: number; message: string }>(
        `/builds/sync?limit=${limit || 20}`,
        { method: 'POST' }
      ),
    timeline: (params?: { branch?: string; nightly_daily?: string }) =>
      fetchApi<CommitTimelineEntry[]>(`/builds/timeline?${new URLSearchParams(cleanParams(params))}`),
  },
  triages: {
    getSuggestions: (failureId: number) =>
      fetchApi<FailureSuggestion[]>(`/triages/failures/${failureId}/suggestions`),
    updateFailure: (failureId: number, update: { failure_category?: string; failure_type?: string; is_flaky?: boolean }) =>
      fetchApi<Failure>(`/triages/failures/${failureId}`, {
        method: 'PATCH',
        body: JSON.stringify(update),
      }),
  },
  jobs: {
    retry: (jobId: number) =>
      fetchApi<{ message: string; retry_count: number }>(`/jobs/${jobId}/retry`, { method: 'POST' }),
  },
  issues: {
    createForFailure: (failureId: number, data: { title: string; body: string; labels?: string[] }) =>
      fetchApi<GitHubIssue>(`/issues/failures/${failureId}/create`, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    linkToFailure: (failureId: number, issueNumber: number) =>
      fetchApi<unknown>(`/issues/failures/${failureId}/link`, {
        method: 'POST',
        body: JSON.stringify({ github_issue_number: issueNumber }),
      }),
    unlinkFromFailure: (failureId: number, issueNumber: number) =>
      fetchApi<{ message: string }>(`/issues/failures/${failureId}/unlink/${issueNumber}`, {
        method: 'DELETE',
      }),
  },
  knownFailures: {
    list: (params?: { status?: string; category?: string; resolved_since_hours?: number; is_flaky?: boolean }) => {
      const searchParams = new URLSearchParams(cleanParams({
        status: params?.status ?? 'open',
        category: params?.category,
        resolved_since_hours: params?.resolved_since_hours?.toString(),
        is_flaky: params?.is_flaky !== undefined ? String(params.is_flaky) : undefined,
      }))
      return fetchApi<KnownFailure[]>(`/known-failures?${searchParams}`)
    },
    get: (id: number) =>
      fetchApi<KnownFailure>(`/known-failures/${id}`),
    getHistory: (id: number) =>
      fetchApi<KnownFailureHistory>(`/known-failures/${id}/history`),
    update: (id: number, update: { title?: string; summary?: string; match_prompt?: string; category?: string; is_flaky?: boolean }) =>
      fetchApi<KnownFailure>(`/known-failures/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(update),
      }),
    resolve: (id: number, resolvedByPr?: number) =>
      fetchApi<{ message: string; id: number }>(`/known-failures/${id}/resolve`, {
        method: 'POST',
        body: JSON.stringify({ resolved_by_pr: resolvedByPr }),
      }),
    reopen: (id: number) =>
      fetchApi<{ message: string; id: number }>(`/known-failures/${id}/reopen`, { method: 'POST' }),
    linkIssue: (id: number, issueNumber: number) =>
      fetchApi<{ message: string; id: number }>(`/known-failures/${id}/link-issue`, {
        method: 'POST',
        body: JSON.stringify({ github_issue_number: issueNumber }),
      }),
    unlinkIssue: (id: number) =>
      fetchApi<{ message: string; id: number }>(`/known-failures/${id}/unlink-issue`, { method: 'DELETE' }),
    merge: (sourceId: number, targetId: number) =>
      fetchApi<{ message: string; target_id: number }>('/known-failures/merge', {
        method: 'POST',
        body: JSON.stringify({ source_id: sourceId, target_id: targetId }),
      }),
    split: (failureIds: number[], newTitle: string) =>
      fetchApi<{ message: string; new_id: number }>('/known-failures/split', {
        method: 'POST',
        body: JSON.stringify({ failure_ids: failureIds, new_title: newTitle }),
      }),
    loadEarlierHistory: (id: number) =>
      fetchApi<{ message: string; builds_found: number }>(`/known-failures/${id}/load-earlier-history`, {
        method: 'POST',
      }),
    triageCommit: (id: number, commitSha: string) =>
      fetchApi<{ message: string }>(`/known-failures/${id}/triage-commit?commit_sha=${encodeURIComponent(commitSha)}`, {
        method: 'POST',
      }),
    getActiveTriages: (id: number) =>
      fetchApi<{ commits: string[]; statuses: Record<string, string> }>(`/known-failures/${id}/active-triages`),
  },
}

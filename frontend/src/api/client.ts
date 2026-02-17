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
  is_flaky: boolean
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

export interface BuildSummary {
  id: number
  buildkite_build_number: number
  build_type: string | null
  state: string | null
  branch: string | null
  message: string | null
  web_url: string | null
  triage_status: string
  created_at: string | null
  total_jobs: number
  failed_jobs: number
  infra_failures: number
  test_failures: number
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
  failure: Failure | null
}

export interface Build extends BuildSummary {
  commit_sha: string | null
  synced_at: string
  jobs: Job[]
}

export interface DashboardSummary {
  total_builds: number
  pending_triages: number
  completed_triages: number
  infra_failures_today: number
  test_failures_today: number
}

export interface FailureSuggestion {
  github_issue_number: number
  title: string
  state: string
  github_issue_url: string
  similarity_score: number
  match_reason: string
}

export interface FailingBuildInfo {
  build_number: number
  commit_sha: string | null
  build_url: string | null
  job_url: string | null
}

export interface CurrentIssue {
  failure_id: number
  job_id: number
  job_name: string
  job_url: string | null
  failing_test: string | string[] | null
  failure_type: string | null
  error_message: string | null
  error_signature: string | null
  log_excerpt: string | null
  first_seen_build: number
  last_seen_build: number
  occurrence_count: number
  is_flaky: boolean
  flaky_rate: number | null
  retry_success_count: number | null
  signature_occurrence_count: number | null
  linked_issue_number: number | null
  linked_issue_url: string | null
  resolved_by_pr: number | null
  failing_builds: FailingBuildInfo[]
}

export interface CurrentIssueGroup {
  error_key: string
  error_message: string | null
  failure_type: string | null
  linked_issue_number: number | null
  linked_issue_url: string | null
  total_affected_tests: number
  first_seen_build: number
  last_seen_build: number
  issues: CurrentIssue[]
}

export interface GitHubIssue {
  id: number
  github_issue_number: number
  title: string | null
  state: string | null
  github_issue_url: string | null
}

function cleanParams(params?: Record<string, string | undefined>): Record<string, string> {
  if (!params) return {}
  return Object.fromEntries(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== '')
  ) as Record<string, string>
}

export const api = {
  builds: {
    list: (params?: { build_type?: string; triage_status?: string; state?: string; branch?: string; nightly_daily?: string }) =>
      fetchApi<BuildWithFailures[]>(`/builds?${new URLSearchParams(cleanParams(params))}`),
    get: (buildNumber: number) =>
      fetchApi<Build>(`/builds/${buildNumber}`),
    sync: (limit?: number) =>
      fetchApi<{ synced: number; triaged: number; message: string }>(
        `/builds/sync?limit=${limit || 20}`,
        { method: 'POST' }
      ),
    syncBuild: (buildNumber: number) =>
      fetchApi<{ synced: boolean; triaged: boolean; message: string }>(
        `/builds/${buildNumber}/sync`,
        { method: 'POST' }
      ),
    dashboardSummary: () =>
      fetchApi<DashboardSummary>('/builds/dashboard/summary'),
    currentIssues: () =>
      fetchApi<CurrentIssue[]>('/builds/current-issues'),
    currentIssuesGrouped: () =>
      fetchApi<CurrentIssueGroup[]>('/builds/current-issues-grouped'),
  },
  triages: {
    getFailure: (failureId: number) =>
      fetchApi<Failure>(`/triages/failures/${failureId}`),
    getSuggestions: (failureId: number) =>
      fetchApi<FailureSuggestion[]>(`/triages/failures/${failureId}/suggestions`),
    retriage: (failureId: number) =>
      fetchApi<Failure>(`/triages/failures/${failureId}/retriage`, { method: 'POST' }),
    updateFailure: (failureId: number, update: { failure_category?: string; failure_type?: string; is_flaky?: boolean }) =>
      fetchApi<Failure>(`/triages/failures/${failureId}`, {
        method: 'PATCH',
        body: JSON.stringify(update),
      }),
    markResolvedByPR: (failureId: number, prNumber: number) =>
      fetchApi<{ message: string; failure_id: number; resolved_by_pr: number }>(
        `/triages/failures/${failureId}/resolved-by-pr`,
        { method: 'POST', body: JSON.stringify({ pr_number: prNumber }) }
      ),
    unmarkResolvedByPR: (failureId: number) =>
      fetchApi<{ message: string; failure_id: number }>(
        `/triages/failures/${failureId}/resolved-by-pr`,
        { method: 'DELETE' }
      ),
  },
  jobs: {
    get: (jobId: number) =>
      fetchApi<Job>(`/jobs/${jobId}`),
    retry: (jobId: number) =>
      fetchApi<{ message: string; retry_count: number }>(`/jobs/${jobId}/retry`, { method: 'POST' }),
    getLog: (jobId: number) =>
      fetchApi<{ job_id: number; log: string }>(`/jobs/${jobId}/log`),
  },
  issues: {
    list: (state?: string) =>
      fetchApi<GitHubIssue[]>(`/issues?${state ? `state=${state}` : ''}`),
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
}

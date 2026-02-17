import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, Build, Job, FailureSuggestion } from '../api/client'

function CategoryBadge({ category }: { category: string | null }) {
  if (category === 'infra') {
    return (
      <span className="bg-yellow-600 text-yellow-100 px-2 py-0.5 rounded text-xs">
        infra
      </span>
    )
  }
  if (category === 'test') {
    return (
      <span className="bg-red-600 text-red-100 px-2 py-0.5 rounded text-xs">
        test
      </span>
    )
  }
  return null
}

function StateBadge({ state }: { state: string | null }) {
  const colors: Record<string, string> = {
    passed: 'bg-github-green text-white',
    failed: 'bg-github-red text-white',
    running: 'bg-blue-600 text-white',
    pending: 'bg-gray-600 text-gray-200',
  }
  return (
    <span className={`px-2 py-0.5 rounded text-xs ${colors[state || ''] || colors.pending}`}>
      {state || 'unknown'}
    </span>
  )
}

function IssueBadge({ state }: { state: string }) {
  const isOpen = state === 'open' || state === 'OPEN'
  return (
    <span
      className={`px-2 py-0.5 rounded text-xs ${
        isOpen ? 'bg-github-green text-white' : 'bg-github-purple text-white'
      }`}
    >
      {state}
    </span>
  )
}

function SuggestionsPanel({ failureId }: { failureId: number }) {
  const { data: suggestions, isLoading } = useQuery<FailureSuggestion[]>({
    queryKey: ['suggestions', failureId],
    queryFn: () => api.triages.getSuggestions(failureId),
  })

  const linkMutation = useMutation({
    mutationFn: (issueNumber: number) => api.issues.linkToFailure(failureId, issueNumber),
  })

  if (isLoading) return <div className="text-gray-500 text-sm">Loading suggestions...</div>
  if (!suggestions?.length) return <div className="text-gray-500 text-sm">No similar issues found</div>

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-medium text-gray-400">Similar Issues</h4>
      {suggestions.map((suggestion) => (
        <div
          key={suggestion.github_issue_number}
          className="bg-gray-700 rounded p-3 flex items-center justify-between"
        >
          <div className="flex-1">
            <div className="flex items-center space-x-2">
              <a
                href={suggestion.github_issue_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-400 hover:underline"
              >
                #{suggestion.github_issue_number}
              </a>
              <IssueBadge state={suggestion.state} />
              <span className="text-gray-400 text-sm">
                {Math.round(suggestion.similarity_score * 100)}% match
              </span>
            </div>
            <div className="text-gray-300 text-sm mt-1 truncate">{suggestion.title}</div>
            <div className="text-gray-500 text-xs">{suggestion.match_reason}</div>
          </div>
          <button
            onClick={() => linkMutation.mutate(suggestion.github_issue_number)}
            disabled={linkMutation.isPending}
            className="px-3 py-1 bg-gray-600 text-gray-200 rounded text-sm hover:bg-gray-500 disabled:opacity-50"
          >
            Link
          </button>
        </div>
      ))}
    </div>
  )
}

function CreateIssueForm({ failureId, jobName, stepKey, errorMessage, rootCause, buildNumber, jobUrl, onClose }: {
  failureId: number
  jobName: string
  stepKey: string | null
  errorMessage: string
  rootCause: string | null
  buildNumber: number
  jobUrl: string | null
  onClose: () => void
}) {
  const pytestCmd = stepKey ? `pytest tests/${stepKey.replace(/_/g, '/')}_test.py -v` : `pytest -k "${jobName}"`
  const buildkiteUrl = jobUrl || `https://buildkite.com/vllm/ci/builds/${buildNumber}`

  const [title, setTitle] = useState(`[CI] ${jobName}: ${errorMessage.slice(0, 50)}`)
  const [body, setBody] = useState(`## Description

CI failure in job: \`${jobName}\`

**Build:** [#${buildNumber}](${buildkiteUrl})

## Error

\`\`\`
${errorMessage}
\`\`\`

${rootCause ? `## Root Cause Analysis\n\n${rootCause}\n\n` : ''}## Reproduce

\`\`\`bash
# Run the failing test locally
${pytestCmd}
\`\`\`

## Links

- [Buildkite Job](${buildkiteUrl})
`)

  const createMutation = useMutation({
    mutationFn: () => api.issues.createForFailure(failureId, { title, body, labels: ['ci-failure'] }),
    onSuccess: () => onClose(),
  })

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
      <div className="bg-gray-800 rounded-lg p-6 w-full max-w-2xl">
        <h3 className="text-lg font-bold text-white mb-4">Create GitHub Issue</h3>
        <div className="space-y-4">
          <div>
            <label className="block text-sm text-gray-400 mb-1">Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full bg-gray-700 text-white px-3 py-2 rounded border border-gray-600 focus:border-blue-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">Body</label>
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={10}
              className="w-full bg-gray-700 text-white px-3 py-2 rounded border border-gray-600 focus:border-blue-500 focus:outline-none font-mono text-sm"
            />
          </div>
          <div className="flex justify-end space-x-3">
            <button
              onClick={onClose}
              className="px-4 py-2 bg-gray-700 text-gray-300 rounded hover:bg-gray-600"
            >
              Cancel
            </button>
            <button
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending}
              className="px-4 py-2 bg-github-green text-white rounded hover:opacity-90 disabled:opacity-50"
            >
              {createMutation.isPending ? 'Creating...' : 'Create Issue'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function JobCard({ job, buildNumber }: { job: Job; buildNumber: number }) {
  const [showCreateIssue, setShowCreateIssue] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const queryClient = useQueryClient()

  const retryMutation = useMutation({
    mutationFn: () => api.jobs.retry(job.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['build', buildNumber] })
    },
  })

  const failure = job.failure

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
      <div
        className="p-4 flex items-center justify-between cursor-pointer hover:bg-gray-750"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center space-x-3">
          <StateBadge state={job.state} />
          <span className="text-white font-medium">{job.name || job.step_key}</span>
          {failure && <CategoryBadge category={failure.failure_category} />}
          {failure?.is_flaky && (
            <span className="text-yellow-400 text-xs">(flaky)</span>
          )}
        </div>
        <div className="flex items-center space-x-2">
          {job.retry_count > 0 && (
            <span className="text-gray-500 text-sm">Retried {job.retry_count}x</span>
          )}
          <span className="text-gray-400">{expanded ? '▼' : '▶'}</span>
        </div>
      </div>

      {expanded && failure && (
        <div className="border-t border-gray-700 p-4 space-y-4">
          <div>
            <h4 className="text-sm font-medium text-gray-400 mb-1">Error Message</h4>
            <p className="text-red-300 text-sm">{failure.error_message}</p>
          </div>

          {failure.root_cause && (
            <div>
              <h4 className="text-sm font-medium text-gray-400 mb-1">Root Cause</h4>
              <p className="text-gray-300 text-sm">{failure.root_cause}</p>
            </div>
          )}

          {failure.log_excerpt && (
            <div>
              <h4 className="text-sm font-medium text-gray-400 mb-1">Log Excerpt</h4>
              <pre className="bg-gray-900 p-3 rounded text-xs text-gray-300 overflow-x-auto max-h-48">
                {failure.log_excerpt.slice(-2000)}
              </pre>
            </div>
          )}

          <div className="flex items-center space-x-3">
            <button
              onClick={(e) => {
                e.stopPropagation()
                retryMutation.mutate()
              }}
              disabled={retryMutation.isPending}
              className="px-3 py-1 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50"
            >
              {retryMutation.isPending ? 'Retrying...' : 'Retry Job'}
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation()
                setShowCreateIssue(true)
              }}
              className="px-3 py-1 bg-github-green text-white rounded text-sm hover:opacity-90"
            >
              Create Issue
            </button>
            {job.web_url && (
              <a
                href={job.web_url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="px-3 py-1 bg-gray-700 text-gray-200 rounded text-sm hover:bg-gray-600"
              >
                View in Buildkite
              </a>
            )}
          </div>

          <SuggestionsPanel failureId={failure.id} />

          {showCreateIssue && (
            <CreateIssueForm
              failureId={failure.id}
              jobName={job.name || job.step_key || 'unknown'}
              stepKey={job.step_key}
              errorMessage={failure.error_message || 'No error message'}
              rootCause={failure.root_cause}
              buildNumber={buildNumber}
              jobUrl={job.web_url}
              onClose={() => setShowCreateIssue(false)}
            />
          )}
        </div>
      )}
    </div>
  )
}

export default function BuildDetail() {
  const { buildNumber } = useParams<{ buildNumber: string }>()
  const buildNum = parseInt(buildNumber || '0', 10)

  const { data: build, isLoading } = useQuery<Build>({
    queryKey: ['build', buildNum],
    queryFn: () => api.builds.get(buildNum),
    enabled: buildNum > 0,
  })

  if (isLoading) {
    return <div className="text-gray-400">Loading build...</div>
  }

  if (!build) {
    return <div className="text-red-400">Build not found</div>
  }

  const failedJobs = build.jobs.filter((j) => j.state === 'failed')
  const passedJobs = build.jobs.filter((j) => j.state === 'passed')

  return (
    <div className="space-y-6">
      <div className="flex items-center space-x-4">
        <Link to="/" className="text-gray-400 hover:text-white">← Back</Link>
        <h1 className="text-2xl font-bold text-white">Build #{build.buildkite_build_number}</h1>
        <StateBadge state={build.state} />
        {build.build_type && (
          <span className="bg-gray-700 px-2 py-1 rounded text-sm text-gray-300">
            {build.build_type}
          </span>
        )}
      </div>

      <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div>
            <span className="text-gray-400">Branch:</span>{' '}
            <span className="text-white">{build.branch}</span>
          </div>
          <div>
            <span className="text-gray-400">Commit:</span>{' '}
            <span className="text-white font-mono">{build.commit_sha?.slice(0, 8)}</span>
          </div>
          <div>
            <span className="text-gray-400">Triage Status:</span>{' '}
            <span className={build.triage_status === 'pending' ? 'text-yellow-400' : 'text-green-400'}>
              {build.triage_status}
            </span>
          </div>
          <div>
            <span className="text-gray-400">Created:</span>{' '}
            <span className="text-white">
              {build.created_at ? new Date(build.created_at).toLocaleString() : 'N/A'}
            </span>
          </div>
        </div>
        {build.message && (
          <div className="mt-3 text-gray-300">{build.message}</div>
        )}
        {build.web_url && (
          <a
            href={build.web_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block mt-3 text-blue-400 hover:underline text-sm"
          >
            View in Buildkite →
          </a>
        )}
      </div>

      {failedJobs.length > 0 && (
        <div>
          <h2 className="text-lg font-semibold text-white mb-3">
            Failed Jobs ({failedJobs.length})
          </h2>
          <div className="space-y-3">
            {failedJobs.map((job) => (
              <JobCard key={job.id} job={job} buildNumber={buildNum} />
            ))}
          </div>
        </div>
      )}

      {passedJobs.length > 0 && (
        <div>
          <h2 className="text-lg font-semibold text-gray-400 mb-3">
            Passed Jobs ({passedJobs.length})
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            {passedJobs.map((job) => (
              <div
                key={job.id}
                className="bg-gray-800 rounded p-2 border border-gray-700 text-sm text-gray-400 truncate"
              >
                {job.name || job.step_key}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

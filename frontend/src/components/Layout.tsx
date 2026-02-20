import { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, HealthCheck } from '../api/client'

interface LayoutProps {
  children: ReactNode
}

export default function Layout({ children }: LayoutProps) {
  const { data: health } = useQuery<HealthCheck>({
    queryKey: ['health'],
    queryFn: () => api.health(),
    refetchInterval: 60000, // check every minute
    retry: false,
  })

  const failedChecks = health?.checks
    ? Object.entries(health.checks).filter(([, v]) => v !== 'ok')
    : []

  return (
    <div className="min-h-screen bg-gray-900">
      <nav className="bg-gray-800 border-b border-gray-700">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center">
              <Link to="/" className="flex items-center space-x-2">
                <span className="text-xl font-bold text-white">CI Triage Bot</span>
              </Link>
            </div>
            <div className="flex items-center space-x-4">
              <Link to="/logs" className="text-gray-400 hover:text-white text-sm transition-colors">
                Logs
              </Link>
              <span className="text-gray-400 text-sm">vLLM CI</span>
            </div>
          </div>
        </div>
      </nav>
      {failedChecks.length > 0 && (
        <div className="bg-red-900/50 border-b border-red-700 px-4 py-2">
          <div className="max-w-7xl mx-auto flex items-center gap-2 text-sm">
            <span className="text-red-400 font-medium">Token errors:</span>
            {failedChecks.map(([name, status]) => (
              <span key={name} className="text-red-300">
                {name} ({status.replace('error:', '')})
              </span>
            ))}
            <span className="text-red-400/70 ml-2">
              Run <code className="bg-red-900 px-1 rounded">gh auth login</code> / refresh Buildkite token
            </span>
          </div>
        </div>
      )}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {children}
      </main>
    </div>
  )
}

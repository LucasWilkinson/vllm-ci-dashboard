import { useState, useEffect, useRef, useCallback } from 'react'

interface LogLine {
  timestamp: string
  build_number: number
  message: string
  level: 'info' | 'warn' | 'error'
}

interface TriageProgress {
  build_number: number
  total_jobs: number
  completed_jobs: number
  current_job: string | null
  status: 'pending' | 'running' | 'completed' | 'error'
  phase: string
}

export default function TriageLogs() {
  const [lines, setLines] = useState<LogLine[]>([])
  const [triages, setTriages] = useState<TriageProgress[]>([])
  const [filterBuild, setFilterBuild] = useState<number | null>(null)
  const [autoScroll, setAutoScroll] = useState(true)
  const [connected, setConnected] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const wsRef = useRef<WebSocket | null>(null)

  // Auto-scroll to bottom when new lines arrive
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [lines, autoScroll])

  // Handle scroll events to detect manual scroll-up
  const handleScroll = useCallback(() => {
    if (!scrollRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current
    const atBottom = scrollHeight - scrollTop - clientHeight < 40
    setAutoScroll(atBottom)
  }, [])

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/triage-status`)
    wsRef.current = ws

    ws.onopen = () => setConnected(true)
    ws.onclose = () => setConnected(false)

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      if (data.type === 'triage_status') {
        setTriages(data.active_triages)
      } else if (data.type === 'triage_log') {
        setLines((prev) => {
          const next = [...prev, ...data.lines]
          // Cap at 2000 lines client-side
          return next.length > 2000 ? next.slice(-2000) : next
        })
      }
    }

    ws.onerror = () => setConnected(false)

    return () => {
      ws.close()
    }
  }, [])

  // Collect unique build numbers for filter dropdown
  const buildNumbers = Array.from(new Set(lines.map((l) => l.build_number))).sort(
    (a, b) => b - a
  )

  const filtered = filterBuild
    ? lines.filter((l) => l.build_number === filterBuild)
    : lines

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Triage Logs</h1>
        <div className="flex items-center gap-3">
          {/* Connection indicator */}
          <div className="flex items-center gap-1.5 text-xs">
            <div
              className={`w-2 h-2 rounded-full ${
                connected ? 'bg-green-400' : 'bg-red-400'
              }`}
            />
            <span className={connected ? 'text-green-400' : 'text-red-400'}>
              {connected ? 'Connected' : 'Disconnected'}
            </span>
          </div>
        </div>
      </div>

      {/* Active triage status bar */}
      {triages.length > 0 && (
        <div className="bg-blue-900/50 border border-blue-700 rounded-lg p-3">
          <div className="flex items-center gap-2 text-blue-300 text-sm mb-1">
            <div className="w-2 h-2 bg-blue-400 rounded-full animate-pulse" />
            <span className="font-medium">Active Triages</span>
          </div>
          {triages.map((t) => (
            <div key={t.build_number} className="text-xs text-blue-200 ml-4">
              <span className="font-mono">Build #{t.build_number}</span>
              <span className="text-blue-400 mx-2">
                {t.completed_jobs}/{t.total_jobs} jobs
              </span>
              {t.current_job && (
                <span className="text-blue-300 truncate">{t.current_job}</span>
              )}
              <span
                className={`ml-2 px-1.5 py-0.5 rounded text-xs ${
                  t.status === 'running'
                    ? 'bg-blue-800 text-blue-300'
                    : t.status === 'completed'
                    ? 'bg-green-800 text-green-300'
                    : t.status === 'error'
                    ? 'bg-red-800 text-red-300'
                    : 'bg-gray-800 text-gray-300'
                }`}
              >
                {t.status}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Controls */}
      <div className="flex items-center gap-3">
        {/* Build filter */}
        <select
          value={filterBuild ?? ''}
          onChange={(e) =>
            setFilterBuild(e.target.value ? Number(e.target.value) : null)
          }
          className="bg-gray-800 border border-gray-700 text-gray-300 text-sm rounded px-2 py-1 focus:outline-none focus:border-blue-500"
        >
          <option value="">All builds</option>
          {buildNumbers.map((n) => (
            <option key={n} value={n}>
              Build #{n}
            </option>
          ))}
        </select>

        {/* Auto-scroll toggle */}
        <button
          onClick={() => setAutoScroll(!autoScroll)}
          className={`text-xs px-2 py-1 rounded border ${
            autoScroll
              ? 'border-green-600 text-green-400 bg-green-900/30'
              : 'border-gray-600 text-gray-400 bg-gray-800'
          }`}
        >
          Auto-scroll {autoScroll ? 'ON' : 'OFF'}
        </button>

        {/* Line count */}
        <span className="text-xs text-gray-500">
          {filtered.length} line{filtered.length !== 1 ? 's' : ''}
        </span>

        {/* Clear */}
        <button
          onClick={() => setLines([])}
          className="text-xs px-2 py-1 rounded border border-gray-600 text-gray-400 bg-gray-800 hover:bg-gray-700"
        >
          Clear
        </button>
      </div>

      {/* Log viewer */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="bg-gray-950 border border-gray-800 rounded-lg p-4 font-mono text-sm overflow-auto"
        style={{ height: 'calc(100vh - 320px)', minHeight: '300px' }}
      >
        {filtered.length === 0 ? (
          <div className="text-gray-600 text-center py-8">
            No log lines yet. Logs will appear here when a triage runs.
          </div>
        ) : (
          filtered.map((line, i) => (
            <LogLineRow key={i} line={line} />
          ))
        )}
      </div>
    </div>
  )
}

function LogLineRow({ line }: { line: LogLine }) {
  const levelColors: Record<string, string> = {
    info: 'text-gray-400',
    warn: 'text-yellow-400',
    error: 'text-red-400',
  }
  const levelBg: Record<string, string> = {
    info: '',
    warn: 'bg-yellow-900/10',
    error: 'bg-red-900/20',
  }

  const ts = line.timestamp.replace('Z', '').split('T')[1]?.slice(0, 8) || ''

  return (
    <div
      className={`flex items-start gap-2 py-0.5 px-1 hover:bg-gray-900/50 ${
        levelBg[line.level] || ''
      }`}
    >
      <span className="text-gray-600 shrink-0 select-none">{ts}</span>
      <span className="bg-gray-800 text-gray-500 px-1.5 rounded text-xs shrink-0 mt-0.5">
        #{line.build_number}
      </span>
      <span className={levelColors[line.level] || 'text-gray-400'}>
        {line.message}
      </span>
    </div>
  )
}

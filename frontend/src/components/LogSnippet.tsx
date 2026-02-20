import { useRef, useEffect } from 'react'
import AnsiToHtml from 'ansi-to-html'

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

export default function LogSnippet({ log }: { log: string }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [log])

  // Strip control characters (BEL \x07, ESC sequences, \r) that leak from Buildkite logs
  const cleanLog = log.replace(/\x07/g, '').replace(/\x1b_[^\x1b]*\x1b\\/g, '').replace(/\r/g, '')

  const linkMatch = cleanLog.match(/^\[View in Buildkite at line (\d+)\]\(([^)]+)\)/)
  const bkLink = linkMatch ? { line: linkMatch[1], url: linkMatch[2] } : null
  const logContent = linkMatch ? cleanLog.slice(linkMatch[0].length).trim() : cleanLog

  // Parse lines and extract line numbers (format: "1234\tcontent" or "L1234: content")
  const lines = logContent.split('\n')
    .filter(line => line.trim() !== '')  // Remove empty lines
    .map(line => {
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

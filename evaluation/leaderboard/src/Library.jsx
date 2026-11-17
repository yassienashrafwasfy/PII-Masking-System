/**
 * Every document this arena has masked, rebuilt from object storage.
 *
 * The console's own list is React state: it holds the runs submitted since the
 * page loaded and forgets them on reload. This view is the durable one -- the
 * API rebuilds it from the manifests NiFi writes, so a document stays reachable
 * after the browser, the session, and the API process itself have all moved on.
 *
 * Masked output only. The original is the unmasked document and the API will
 * not serve it for an archived run, so the pane is not offered either -- see
 * DocumentPane's `maskedOnly`.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  ArrowLeft, FileText, Info, Library as LibraryIcon, Loader2, RefreshCw, Search, TriangleAlert,
} from 'lucide-react'
import DocumentPane from './DocumentPane'
import { RunSummary } from './Console'
import { fetchLibrary, fetchRun } from './api'
import { getTeamDisplayName } from './teamDisplayConfig'
import { timeAgo } from './format'

const PAGE_SIZE = 25

const STATUS_FILTERS = [
  { id: '', label: 'All' },
  { id: 'completed', label: 'Completed' },
  { id: 'failed', label: 'Failed' },
]

function Row({ entry, onOpen, championTeam }) {
  return (
    <tr>
      <td>
        <button className="library-name" onClick={() => onOpen(entry.run_id)}>
          <FileText size={15} />
          <span>{entry.filename}</span>
        </button>
      </td>
      <td>{getTeamDisplayName(entry.team, 0, championTeam)}</td>
      <td>
        <span className={`chip status-${entry.status}`}>{entry.status}</span>
      </td>
      <td title={entry.completed_at || entry.submitted_at || ''}>
        {timeAgo(entry.completed_at || entry.submitted_at)}
      </td>
      <td className="mono library-model">{entry.model_version || '—'}</td>
    </tr>
  )
}

export default function Library({ config, runId, onSelect, championTeam }) {
  const [page, setPage] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [team, setTeam] = useState('')
  const [status, setStatus] = useState('')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(null)
  const [selectError, setSelectError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setPage(await fetchLibrary({ limit: PAGE_SIZE, team, status, q: query }))
    } catch (problem) {
      setError(problem.message)
      setPage(null)
    } finally {
      setLoading(false)
    }
  }, [team, status, query])

  useEffect(() => {
    load()
  }, [load])

  // A run id in the hash opens that document directly. This is what makes a
  // bookmarked or shared link work at all -- and, because the API resolves it
  // from storage, work after a restart rather than 404ing.
  useEffect(() => {
    if (!runId) {
      setSelected(null)
      setSelectError(null)
      return
    }
    let active = true
    setSelectError(null)
    fetchRun(runId)
      .then(run => active && setSelected(run))
      .catch(problem => active && setSelectError(problem.message))
    return () => {
      active = false
    }
  }, [runId])

  if (!config || config.library?.enabled === false) {
    return (
      <section className="library">
        <p className="availability-note">
          <Info size={16} />
          The file library is turned off in this deployment.
        </p>
      </section>
    )
  }

  if (runId) {
    return (
      <section className="library">
        <button className="refresh library-back" onClick={() => onSelect(null)}>
          <ArrowLeft size={16} />All masked files
        </button>
        {selectError ? (
          <p className="console-warning">
            <TriangleAlert size={16} />
            <span>{selectError}</span>
          </p>
        ) : !selected ? (
          <p className="availability-note"><Loader2 size={16} className="spin" />Loading run…</p>
        ) : (
          <>
            <RunSummary run={selected} displayNameFor={(team, idx) => getTeamDisplayName(team, idx, championTeam)} />
            <DocumentPane run={selected} maskedOnly />
          </>
        )}
      </section>
    )
  }

  const entries = page?.entries || []

  return (
    <section className="library">
      <div className="section-heading">
        <div>
          <span className="eyebrow">EVERY MASKED DOCUMENT</span>
          <h2><LibraryIcon size={26} />Masked file library</h2>
          <p>
            Rebuilt from the manifests the pipeline writes, so a document stays here after the
            page, the session, and the service itself have restarted.
          </p>
        </div>
        <div className="heading-actions">
          <button className="refresh" onClick={load} disabled={loading}>
            <RefreshCw size={16} className={loading ? 'spin' : ''} />Refresh
          </button>
        </div>
      </div>

      {/* Restated here rather than only in the console: this list is global and
          durable, so the exposure it describes is wider than a single run's. */}
      <p className="console-warning">
        <TriangleAlert size={16} />
        <span>
          Synthetic documents only. Everything listed here is readable by anyone who can reach
          this site — it is not private. Masked output is shown; the original is never served
          from the library.
        </span>
      </p>

      <div className="library-filters">
        <label className="library-search">
          <Search size={15} />
          <input
            type="search"
            value={query}
            placeholder="Filter by file name"
            onChange={event => setQuery(event.target.value)}
            aria-label="Filter by file name"
          />
        </label>
        <div className="basis-toggle" role="group" aria-label="Filter by status">
          {STATUS_FILTERS.map(option => (
            <button
              key={option.id || 'all'}
              className={status === option.id ? 'active' : ''}
              onClick={() => setStatus(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <div className="basis-toggle" role="group" aria-label="Filter by team">
          <button className={team === '' ? 'active' : ''} onClick={() => setTeam('')}>
            All teams
          </button>
          {(config.teams || []).map(id => (
            <button key={id} className={team === id ? 'active' : ''} onClick={() => setTeam(id)}>
              {getTeamDisplayName(id, 0, championTeam)}
            </button>
          ))}
        </div>
      </div>

      {error ? (
        <p className="console-warning">
          <TriangleAlert size={16} />
          <span>{error}</span>
        </p>
      ) : (
        <div className={`table-wrap ${loading ? 'loading' : ''}`}>
          <table className="library-table">
            <thead>
              <tr>
                {/* Deliberately no size column: knowing it costs one HEAD per
                    row, and the listing is kept to a single bucket scan. The
                    size is shown once a document is actually opened. */}
                <th>File</th><th>Team</th><th>Status</th><th>Masked</th><th>Model</th>
              </tr>
            </thead>
            <tbody>
              {entries.map(entry => (
                <Row key={entry.run_id} entry={entry} onOpen={onSelect} championTeam={championTeam} />
              ))}
              {!entries.length && !loading && (
                <tr>
                  <td className="empty-row" colSpan="5">
                    <FileText size={20} />
                    {query || team || status
                      ? 'No masked document matches these filters.'
                      : 'Nothing has been masked yet. Use the masking console to submit a document.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {page?.truncated && (
        <p className="availability-note">
          <Info size={16} />
          The bucket holds more manifests than one scan reads, so this is a partial view.
        </p>
      )}
      {page && page.total > entries.length && (
        <p className="availability-note">
          <Info size={16} />
          Showing the {entries.length} most recent of {page.total}.
        </p>
      )}
    </section>
  )
}

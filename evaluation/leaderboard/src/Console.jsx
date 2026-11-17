import { useCallback, useEffect, useRef, useState } from 'react'
import {
  CheckCircle2, CircleAlert, CloudUpload, Clock3, FileUp, FolderOpen, History,
  Library as LibraryIcon, Loader2, RotateCw, ServerCrash, ShieldCheck, TriangleAlert, Upload,
} from 'lucide-react'
import { fetchLibrary, fetchRun, fetchSamples, submitRun } from './api'
import { getTeamDisplayName } from './teamDisplayConfig'
import { bytes, duration } from './format'
import DocumentPane from './DocumentPane'

// Safe to run tight: a poll is one cheap manifest-prefix scan per team, not
// the full-history rescan it used to be (the API now stops at the first
// manifest older than the upload instead of downloading every one). At 2s
// the interval itself was adding up to 2s of pure waiting on top of however
// long NiFi actually took; 500ms caps that added wait at a quarter second.
const POLL_INTERVAL_MS = 500
const TERMINAL = new Set(['completed', 'failed', 'timeout'])

const STEPS = [
  { id: 'uploaded', label: 'Uploaded to source', hint: 'Object written under the incoming prefix' },
  { id: 'processing', label: 'Masking in progress', hint: 'NiFi routes the object to the team model' },
  { id: 'completed', label: 'Masked result stored', hint: 'Object written under the destination prefix' },
]

function stepState(status, stepId) {
  const order = ['uploaded', 'processing', 'completed']
  if (status === 'failed' || status === 'timeout') {
    return stepId === 'completed' ? 'error' : 'done'
  }
  const current = order.indexOf(status)
  const position = order.indexOf(stepId)
  if (position < current) return 'done'
  if (position === current) return status === 'completed' ? 'done' : 'active'
  return 'pending'
}

function Stepper({ run }) {
  return (
    <ol className="stepper" aria-label="Masking progress">
      {STEPS.map(step => {
        const state = stepState(run.status, step.id)
        return (
          <li key={step.id} className={`step ${state}`}>
            <span className="step-icon">
              {state === 'done' && <CheckCircle2 size={18} />}
              {state === 'active' && <Loader2 size={18} className="spin" />}
              {state === 'error' && <CircleAlert size={18} />}
              {state === 'pending' && <Clock3 size={18} />}
            </span>
            <span className="step-copy"><b>{step.label}</b><small>{step.hint}</small></span>
          </li>
        )
      })}
    </ol>
  )
}

// Exported so the file library renders an archived run exactly the way the
// console renders a live one -- the payload is the same shape whether it came
// from this process's registry or was rebuilt from a manifest, and two
// renderings of it would drift.
// Which of berts' three checkpoints read a run. The name carries the language
// (`berts-pii-medroberta-nl-int8`, `berts-pii-bert-en-int8`,
// `berts-pii-camembert-bio-fr-int8`), and a reader should not have to decode
// it: the summary spells the language out beside it.
const CHECKPOINT_LANGUAGES = [
  ['medroberta-nl', 'Dutch'],
  ['bert-en', 'English'],
  ['camembert-bio-fr', 'French'],
]

function checkpointLanguage(modelName) {
  if (!modelName) return null
  const match = CHECKPOINT_LANGUAGES.find(([needle]) => modelName.includes(needle))
  return match ? match[1] : null
}

export function RunSummary({ run, displayNameFor }) {
  const failed = run.status === 'failed' || run.status === 'timeout'
  return (
    <div className={`run-summary ${run.status}`}>
      <Stepper run={run} />
      <dl className="run-facts">
        <div><dt>Team</dt><dd>{displayNameFor(run.team, 0)}</dd></div>
        <div><dt>File</dt><dd>{run.filename} <small>{bytes(run.source?.size)}</small></dd></div>
        <div><dt>Elapsed</dt><dd>{duration(run.elapsed_seconds) || '—'}</dd></div>
        <div><dt>Correlation ID</dt><dd className="mono">{run.correlation_id || '—'}</dd></div>
        <div>
          <dt>Checkpoint</dt>
          <dd>
            {run.model_name || '—'}
            {checkpointLanguage(run.model_name) && (
              <small> {checkpointLanguage(run.model_name)}</small>
            )}
          </dd>
        </div>
        <div><dt>Model version</dt><dd>{run.model_version || '—'}</dd></div>
        <div><dt>Found via</dt><dd>{run.discovered_by || '—'}</dd></div>
      </dl>
      {failed && (
        <p className="run-error">
          <TriangleAlert size={16} />
          <span>
            <b>{run.error_code || run.status.toUpperCase()}</b> {run.message || 'The run did not complete.'}
          </span>
        </p>
      )}
      {run.manifest_key && (
        <p className="run-manifest" title={run.manifest_key}>
          Terminal manifest: <span className="mono">{run.manifest_key}</span>
        </p>
      )}
    </div>
  )
}

function TeamPicker({ teams, value, onChange, disabled, displayNameFor, fixedNote }) {
  // A radiogroup containing exactly one option is a poor affordance: it looks
  // like a choice, answers to arrow keys, and invites the user to look for the
  // alternatives. When only one team is offered -- which is the whole point of
  // the champion console -- show it as the fixed destination it actually is.
  if (teams.length === 1) {
    return (
      <div className="team-picker single">
        <div className="team-option selected" aria-label={`Destination team model: ${displayNameFor(teams[0], 0)}`}>
          <ShieldCheck size={16} />
          {/* No raw team_id subtext: for berts it would render the literal
              lowercase id right under its display name. */}
          <span><b>{displayNameFor(teams[0], 0)}</b></span>
          <span className="team-fixed-note">{fixedNote}</span>
        </div>
      </div>
    )
  }
  return (
    <div className="team-picker" role="radiogroup" aria-label="Destination team model">
      {teams.map(team => (
        <button
          key={team}
          type="button"
          role="radio"
          aria-checked={value === team}
          className={value === team ? 'team-option selected' : 'team-option'}
          onClick={() => onChange(team)}
          disabled={disabled}
        >
          <ShieldCheck size={16} />
          <span><b>{displayNameFor(team, 0)}</b></span>
        </button>
      ))}
    </div>
  )
}

// Defaults reproduce the console exactly as it shipped, so a caller that passes
// nothing but `config` gets the original page. Both consoles are this component:
// the champion one narrows `teams` to a single resolved team and overrides the
// label, the team one takes every default.
const DEFAULT_COPY = {
  eyebrow: 'INTERACTIVE MASKING',
  title: 'Run a document through the pipeline',
  intro: 'Upload a document and compare the original with the masked result.',
  fixedNote: 'Top-ranked model',
  legendSingle: '1 · Team model',
  legendMulti: '1 · Choose the team model',
}

// Berts holds one checkpoint per document language, so one of its runs is read
// by one of three models while every other team answers with one. Said where
// the destination is named, because the run summary then reports a checkpoint
// name the reader has no other way to interpret.
const TEAM_ROUTING_NOTES = {
  berts: 'Three checkpoints — Dutch, English or French, chosen per document',
}

export default function Console({
  config,
  configError,
  teams: teamsProp,
  displayNameFor = getTeamDisplayName,
  copy: copyProp,
  pending = null,
}) {
  const copy = { ...DEFAULT_COPY, ...(copyProp || {}) }
  const teams = teamsProp ?? config?.teams ?? []
  const [team, setTeam] = useState('')
  const [file, setFile] = useState(null)
  const [sampleName, setSampleName] = useState(null)
  const [samples, setSamples] = useState([])
  const [showUpload, setShowUpload] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [run, setRun] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const [history, setHistory] = useState([])
  const inputRef = useRef(null)

  // Seed the recent list from the durable library, so a page reload no longer
  // empties it. This list used to be component state alone, which meant the
  // "it stays saved" people relied on only lasted as long as the page did.
  // Best-effort: the library can be turned off, and an empty list here is the
  // ordinary state for a fresh deployment, so a failure is not worth surfacing.
  useEffect(() => {
    let active = true
    fetchLibrary({ limit: 8 })
      .then(page => {
        if (active && page.entries?.length) setHistory(page.entries)
      })
      .catch(() => {})
    return () => {
      active = false
    }
  }, [])

  // Joined rather than depended on directly: `teams` is often a fresh array
  // literal from the caller, so the identity changes every render even when the
  // contents do not. Re-selecting whenever the current team leaves the list is
  // what lets the champion console follow a change of rank 1 without a reload,
  // instead of staying pinned to whoever was winning when the page opened.
  const teamsKey = teams.join(',')
  useEffect(() => {
    const available = teamsKey ? teamsKey.split(',') : []
    setTeam(current => (available.includes(current) ? current : available[0] || ''))
  }, [teamsKey])

  useEffect(() => {
    if (!config?.samples_available) return
    fetchSamples()
      .then(data => setSamples(Array.isArray(data.samples) ? data.samples : []))
      .catch(() => setSamples([]))
  }, [config?.samples_available])

  // Poll the run until it reaches a terminal state.
  useEffect(() => {
    if (!run || TERMINAL.has(run.status)) return
    let active = true
    const timer = window.setInterval(async () => {
      try {
        const next = await fetchRun(run.run_id)
        if (!active) return
        setRun(next)
        if (TERMINAL.has(next.status)) {
          setHistory(current => [next, ...current.filter(item => item.run_id !== next.run_id)].slice(0, 8))
        }
      } catch (pollError) {
        if (active) setError(pollError.message)
      }
    }, POLL_INTERVAL_MS)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [run])

  const chooseFile = useCallback(selected => {
    setFile(selected || null)
    setSampleName(null)
    setError(null)
  }, [])

  const chooseSample = useCallback(name => {
    setSampleName(name)
    setFile(null)
    setError(null)
  }, [])

  const onDrop = event => {
    event.preventDefault()
    setDragging(false)
    const dropped = event.dataTransfer?.files?.[0]
    if (dropped) chooseFile(dropped)
  }

  const start = async event => {
    event.preventDefault()
    if ((!file && !sampleName) || !team) return
    setSubmitting(true)
    setError(null)
    try {
      const created = await submitRun({ team, file, sample: sampleName })
      setRun(created)
      setHistory(current => [created, ...current.filter(item => item.run_id !== created.run_id)].slice(0, 8))
    } catch (submitError) {
      setError(submitError.message)
    } finally {
      setSubmitting(false)
    }
  }

  const reset = () => {
    setRun(null)
    setFile(null)
    setSampleName(null)
    setError(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  if (configError) {
    return (
      <section className="console-blocked">
        <ServerCrash size={30} />
        <h2>The masking console is unavailable</h2>
        <p>{configError}</p>
      </section>
    )
  }

  // The champion console cannot name its destination until the board says who
  // is winning. Saying so beats rendering an empty picker over a submit button
  // that silently refuses to enable.
  if (pending) {
    return (
      <section className="console-blocked">
        <Loader2 size={30} className="spin" />
        <h2>{pending}</h2>
      </section>
    )
  }

  if (config && config.storage_configured === false) {
    return (
      <section className="console-blocked">
        <CircleAlert size={30} />
        <h2>Object storage is not configured</h2>
        <p>
          Set <code>ARENA_S3_ENDPOINT_URL</code>, <code>ARENA_S3_ACCESS_KEY_ID</code>, and{' '}
          <code>ARENA_S3_SECRET_ACCESS_KEY</code> on the arena API to enable interactive masking runs.
        </p>
      </section>
    )
  }

  const paths = config?.paths
  const limitLabel = config ? bytes(config.max_upload_bytes) : null

  return (
    <section className="console" id="console">
      <div className="section-heading">
        <div>
          <span className="eyebrow">{copy.eyebrow}</span>
          <h2>{copy.title}</h2>
          <p>{copy.intro}</p>
        </div>
        {run && (
          <button className="refresh" onClick={reset}><RotateCw size={16} />New run</button>
        )}
      </div>

      {/* Condensed to one line, but never dropped: this is the only place the
          page warns against putting real patient data into shared storage, so
          the instruction stays and only the mechanism folds into it. */}
      <p className="console-warning">
        <TriangleAlert size={16} />
        Use synthetic or already-approved documents only — uploads are written to the shared source bucket.
      </p>

      {!run && (
        <form className="console-form" onSubmit={start}>
          <fieldset>
            <legend>{teams.length === 1 ? copy.legendSingle : copy.legendMulti}</legend>
            <TeamPicker
              teams={teams}
              value={team}
              onChange={setTeam}
              disabled={submitting}
              displayNameFor={displayNameFor}
              fixedNote={TEAM_ROUTING_NOTES[team] ?? copy.fixedNote}
            />
            {paths && team && (
              <p className="path-preview">
                Destination for this request:{' '}
                <span className="mono">{paths.source_bucket}/{paths.incoming_prefix}/{team}/…</span>
                {' → '}
                <span className="mono">{paths.masked_bucket}/{paths.masked_prefix}/{team}/…</span>
              </p>
            )}
          </fieldset>

          <fieldset>
            <legend>2 · Pick a document</legend>

            {samples.length > 0 && !showUpload ? (
              <>
                <ul className="sample-list">
                  {samples.map(sampleItem => (
                    <li key={sampleItem.name}>
                      <button
                        type="button"
                        className={sampleName === sampleItem.name ? 'sample-option selected' : 'sample-option'}
                        onClick={() => chooseSample(sampleItem.name)}
                        disabled={submitting}
                      >
                        <FolderOpen size={16} />
                        <span><b>{sampleItem.name}</b><small>{bytes(sampleItem.size)}</small></span>
                      </button>
                    </li>
                  ))}
                </ul>
                <button type="button" className="link-button" onClick={() => setShowUpload(true)} disabled={submitting}>
                  Or upload a different file
                </button>
              </>
            ) : (
              <>
                <label
                  className={`dropzone ${dragging ? 'dragging' : ''} ${file ? 'filled' : ''}`}
                  onDragOver={event => {
                    event.preventDefault()
                    setDragging(true)
                  }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={onDrop}
                >
                  <input
                    ref={inputRef}
                    type="file"
                    accept=".pdf,.txt,.csv,.json,application/pdf,text/plain,text/csv,application/json"
                    onChange={event => chooseFile(event.target.files?.[0])}
                    disabled={submitting}
                  />
                  {file ? (
                    <>
                      <FileUp size={26} />
                      <b>{file.name}</b>
                      <small>{bytes(file.size)} · {file.type || 'type detected on upload'}</small>
                    </>
                  ) : (
                    <>
                      <CloudUpload size={26} />
                      <b>Drop a document here, or click to browse</b>
                      <small>PDF, plain text, CSV, or JSON{limitLabel ? ` · up to ${limitLabel}` : ''}</small>
                    </>
                  )}
                </label>
                {samples.length > 0 && (
                  <button type="button" className="link-button" onClick={() => setShowUpload(false)} disabled={submitting}>
                    Or pick a sample document
                  </button>
                )}
              </>
            )}
          </fieldset>

          {error && <p className="run-error"><TriangleAlert size={16} /><span>{error}</span></p>}

          <button
            className="primary submit"
            type="submit"
            disabled={(!file && !sampleName) || !team || submitting}
          >
            {submitting ? <Loader2 size={18} className="spin" /> : <Upload size={18} />}
            {submitting ? 'Uploading to the source bucket…' : 'Start masking run'}
          </button>
        </form>
      )}

      {run && (
        <>
          <RunSummary run={run} displayNameFor={displayNameFor} />
          {error && <p className="run-error"><TriangleAlert size={16} /><span>{error}</span></p>}
          <DocumentPane run={run} />
        </>
      )}

      {history.length > 0 && (
        <div className="run-history">
          {/* Not "this session" any more: seeded from the library below, so a
              reload no longer empties it. One completed run is worth showing,
              which is why this is not gated on there being two. */}
          <h3><History size={17} />Recent runs</h3>
          <ul>
            {history.map(item => (
              <li key={item.run_id}>
                <button
                  type="button"
                  className={run?.run_id === item.run_id ? 'current' : ''}
                  onClick={() => setRun(item)}
                >
                  <span className={`chip status-${item.status}`}>{item.status}</span>
                  <b title={item.filename}>{item.filename}</b>
                  <small title={displayNameFor(item.team, 0)}>{displayNameFor(item.team, 0)}</small>
                </button>
              </li>
            ))}
          </ul>
          <a className="run-history-all" href="#/library">
            <LibraryIcon size={15} />Browse all masked files
          </a>
        </div>
      )}
    </section>
  )
}

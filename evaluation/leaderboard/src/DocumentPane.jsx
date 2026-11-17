import { useEffect, useMemo, useState } from 'react'
import { Download, FileText, Loader2, ShieldCheck, TriangleAlert } from 'lucide-react'
import { documentUrl, fetchDocumentText } from './api'
import { bytes } from './format'

const TEXT_TYPES = new Set(['text/plain', 'text/csv', 'application/json'])

// Shared and never mutated. Without the original there is nothing to diff
// against, so no line can be marked changed -- an empty set says that once
// rather than allocating a new one on every render.
const NO_CHANGES = new Set()

/**
 * Line indices that differ between the two documents.
 *
 * Masking substitutes spans in place and preserves document structure, so an
 * index-based comparison is both accurate enough and stable for side-by-side
 * scrolling. A real diff would re-align lines that masking never moves.
 */
function changedLines(originalText, maskedText) {
  if (originalText === null || maskedText === null) return new Set()
  const left = originalText.split('\n')
  const right = maskedText.split('\n')
  const changed = new Set()
  const length = Math.max(left.length, right.length)
  for (let index = 0; index < length; index += 1) {
    if (left[index] !== right[index]) changed.add(index)
  }
  return changed
}

function TextView({ text, changed, side }) {
  const lines = text.split('\n')
  return (
    <pre className={`doc-text ${side}`} tabIndex={0}>
      {lines.map((line, index) => (
        <code key={index} className={changed.has(index) ? 'line changed' : 'line'}>
          <span className="line-number" aria-hidden="true">{index + 1}</span>
          <span className="line-body">{line || ' '}</span>
        </code>
      ))}
    </pre>
  )
}

function Pane({ run, side, text, loading, error, changed }) {
  const isMasked = side === 'masked'
  const target = isMasked ? run.masked : run.source
  const available = isMasked ? Boolean(run.masked?.key) : true
  const isPdf = run.content_type === 'application/pdf'

  return (
    <article className={`doc-pane ${side}`}>
      <header>
        <div className="doc-title">
          {isMasked ? <ShieldCheck size={17} /> : <FileText size={17} />}
          <div>
            <b>{isMasked ? 'Masked result' : 'Original upload'}</b>
            <small>{isMasked ? 'Destination bucket' : 'Source bucket'}</small>
          </div>
        </div>
        {available && (
          <a className="doc-download" href={documentUrl(run.run_id, side)} target="_blank" rel="noreferrer">
            <Download size={14} />Open
          </a>
        )}
      </header>

      {available && target?.key && (
        <p className="doc-key" title={`${target.bucket}/${target.key}`}>
          <span className="doc-bucket">{target.bucket}</span>/{target.key}
          {target.size ? <span className="doc-size">{bytes(target.size)}</span> : null}
        </p>
      )}

      <div className="doc-body">
        {!available ? (
          // A run that ended in failure will never produce a masked document,
          // so a spinner and "waiting" would be a promise that never resolves
          // -- which is exactly how it read in the library, where every entry
          // is already terminal.
          run.status === 'failed' || run.status === 'timeout' ? (
            <div className="doc-placeholder error">
              <TriangleAlert size={22} />
              <p>No masked document was produced: the run {run.status}.</p>
            </div>
          ) : (
            <div className="doc-placeholder">
              <Loader2 size={22} className="spin" />
              <p>Waiting for the masked document to land in the destination bucket.</p>
            </div>
          )
        ) : loading ? (
          <div className="doc-placeholder"><Loader2 size={22} className="spin" /><p>Loading document…</p></div>
        ) : error ? (
          <div className="doc-placeholder error"><TriangleAlert size={22} /><p>{error}</p></div>
        ) : isPdf ? (
          <iframe title={`${side} document`} src={documentUrl(run.run_id, side)} className="doc-frame" />
        ) : text !== null ? (
          <TextView text={text} changed={changed} side={side} />
        ) : (
          <div className="doc-placeholder">
            <FileText size={22} />
            <p>This media type cannot be previewed. Use Open to download it.</p>
          </div>
        )}
      </div>
    </article>
  )
}

/**
 * `maskedOnly` drops the before pane entirely.
 *
 * Not a styling choice: the original is the *unmasked* document, and the API
 * refuses it for any run it had to rebuild from storage, because there is no
 * authentication and a durable URL to raw PII is a different thing from a
 * transient one. Asking for it anyway would render a 403 as a broken pane.
 */
export default function DocumentPane({ run, maskedOnly = false }) {
  const [original, setOriginal] = useState({ text: null, loading: false, error: null })
  const [masked, setMasked] = useState({ text: null, loading: false, error: null })
  const previewable = TEXT_TYPES.has(run.content_type)

  useEffect(() => {
    if (!previewable || maskedOnly) return
    let active = true
    setOriginal({ text: null, loading: true, error: null })
    fetchDocumentText(run.run_id, 'original')
      .then(text => active && setOriginal({ text, loading: false, error: null }))
      .catch(error => active && setOriginal({ text: null, loading: false, error: error.message }))
    return () => {
      active = false
    }
  }, [run.run_id, previewable, maskedOnly])

  const maskedKey = run.masked?.key || null
  useEffect(() => {
    if (!previewable || !maskedKey) {
      setMasked({ text: null, loading: false, error: null })
      return
    }
    let active = true
    setMasked({ text: null, loading: true, error: null })
    fetchDocumentText(run.run_id, 'masked')
      .then(text => active && setMasked({ text, loading: false, error: null }))
      .catch(error => active && setMasked({ text: null, loading: false, error: error.message }))
    return () => {
      active = false
    }
  }, [run.run_id, maskedKey, previewable])

  const changed = useMemo(() => changedLines(original.text, masked.text), [original.text, masked.text])

  if (maskedOnly) {
    return (
      <div className="doc-grid single">
        <Pane run={run} side="masked" {...masked} changed={NO_CHANGES} />
      </div>
    )
  }

  return (
    <div className="doc-grid">
      <Pane run={run} side="original" {...original} changed={changed} />
      <Pane run={run} side="masked" {...masked} changed={changed} />
    </div>
  )
}

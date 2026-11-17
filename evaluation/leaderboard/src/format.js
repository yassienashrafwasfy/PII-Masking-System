// Every QA metric arrives as an {available, value, reason} envelope, so the UI
// has to render "unavailable, and here is why" as a normal state.
export function metricOf(model, name) {
  return model?.metrics?.[name] ?? null
}

export function metricValue(model, name) {
  const metric = metricOf(model, name)
  return metric && metric.available ? metric.value : null
}

// The API serves boards built from either of qa-automation's two document
// kinds, which name the same quantity differently: a per-run
// qa-result.schema.json document calls it pii_recall, the cross-team ranking
// document overlapping_recall. Columns ask for both and take whichever the
// board actually carries, so neither source renders as an empty table.
export function firstMetricName(model, names) {
  return names.find(name => metricOf(model, name) !== null) ?? names[0]
}

export function percent(value, digits = 1) {
  return value === null || value === undefined ? null : `${(value * 100).toFixed(digits)}%`
}

export function decimal(value, digits = 3) {
  return value === null || value === undefined ? null : Number(value).toFixed(digits)
}

export function milliseconds(value) {
  if (value === null || value === undefined) return null
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`
}

export function bytes(value) {
  if (!value && value !== 0) return null
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(2)} MB`
}

// The library lists documents masked over days or weeks, where the exact
// timestamp matters far less than "was this today". Falls back to the date
// once an entry is old enough that a relative reading stops being useful.
export function timeAgo(iso) {
  if (!iso) return '—'
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return '—'
  const seconds = Math.max(0, (Date.now() - then.getTime()) / 1000)
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`
  if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)} d ago`
  return then.toLocaleDateString()
}

export function duration(seconds) {
  if (seconds === null || seconds === undefined) return null
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ${Math.round(seconds - minutes * 60)}s`
}

// Formatters keyed by metric so the comparison table and the medal cards agree.
const METRIC_FORMAT = {
  leaderboard_score: value => Number(value).toFixed(2),
  pii_recall: value => percent(value, 2),
  pii_precision: value => percent(value, 2),
  pii_f1: value => decimal(value),
  overlapping_recall: value => percent(value, 2),
  overlapping_precision: value => percent(value, 2),
  overlapping_f1: value => decimal(value),
  exact_recall: value => percent(value, 2),
  exact_precision: value => percent(value, 2),
  exact_f1: value => decimal(value),
  mean_confidence: value => percent(value, 1),
  model_size_mb: value => `${Number(value).toFixed(1)} MB`,
  parameter_count_millions: value => `${Number(value).toFixed(1)} M`,
  documents_scored: value => `${Number(value)}`,
  critical_entity_recall: value => percent(value, 2),
  document_success_rate: value => percent(value, 1),
  api_error_rate: value => percent(value, 2),
  leakage_rate: value => percent(value, 2),
  overmasking_rate: value => percent(value, 2),
  non_pii_preservation_rate: value => percent(value, 2),
  latency_p95_ms: milliseconds,
  latency_p50_ms: milliseconds,
  throughput_docs_min: value => `${Number(value).toFixed(1)} docs/min`,
  contract_pass_rate: value => percent(value, 1),
}

export function formatMetric(name, value) {
  if (value === null || value === undefined) return '—'
  const formatter = METRIC_FORMAT[name]
  return formatter ? formatter(value) : String(value)
}

export function formatEnvelope(model, name) {
  const metric = metricOf(model, name)
  if (!metric) return { text: '—', reason: 'Not reported in this run' }
  if (!metric.available) return { text: 'n/a', reason: metric.reason || 'Metric unavailable' }
  return { text: formatMetric(name, metric.value), reason: null }
}

// Same as formatEnvelope, but for a column that accepts several metric names.
export function formatFirstEnvelope(model, names) {
  return formatEnvelope(model, firstMetricName(model, names))
}

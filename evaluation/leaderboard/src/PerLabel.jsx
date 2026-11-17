/**
 * The board broken down by entity type.
 *
 * The overall recall on the ranking table answers "is this model good". This
 * answers the question a model developer actually has after a change: *which
 * entity type did I break*. Until now that meant asking the QA team after
 * every tweak.
 *
 * Two layout decisions carry the whole thing:
 *
 * Labels are rows and models are columns. The taxonomy has twelve labels today
 * and will only grow; the number of teams is bounded at three or four. Keyed
 * the other way this table breaks at twelve columns, keyed this way it still
 * reads at forty rows.
 *
 * One metric at a time, chosen by a segmented control. Recall, precision, F1
 * and support are four numbers per cell; showing them together turns a
 * scannable grid into a spreadsheet. Adding a fifth metric later costs a
 * button, not a column.
 *
 * Dataset and language are the dimensions QA has not modelled yet. When they
 * arrive they belong beside the metric control as more chips -- never as extra
 * columns, which is the shape that does not scale.
 */

import { useMemo, useState } from 'react'
import { AlertTriangle, ArrowUpDown, Check, Download, Info, Search, Tags } from 'lucide-react'
import { getTeamDisplayName } from './teamDisplayConfig'
import { toCsv, download, stamp } from './csv'

const METRICS = [
  { id: 'recall', label: 'Recall', hint: 'Of the entities present, how many were masked' },
  { id: 'precision', label: 'Precision', hint: 'Of the spans masked, how many should have been' },
  { id: 'f1', label: 'F1', hint: 'The harmonic mean of recall and precision' },
  { id: 'support', label: 'Support', hint: 'How many of this entity the scored documents held' },
]

const SORTS = [
  { id: 'taxonomy', label: 'Taxonomy order' },
  { id: 'name', label: 'Name' },
  { id: 'value', label: 'Lowest first' },
  // The most useful ordering when benchmarking against rivals, and the reason
  // it is one click away rather than buried.
  { id: 'spread', label: 'Widest spread' },
]

// A sample this thin cannot support a confident reading: 100% of four is not
// the same claim as 100% of four hundred, and the view has to say so.
const THIN_SUPPORT = 20

function valueOf(model, label, metric) {
  const entry = model.per_label?.[label]
  const envelope = entry?.[metric]
  return envelope?.available ? envelope.value : null
}

function reasonOf(model, label, metric) {
  const entry = model.per_label?.[label]
  if (!entry) return 'This model reported no metrics for this entity type.'
  return entry[metric]?.reason || null
}

function supportOf(model, label) {
  return valueOf(model, label, 'support')
}

function formatValue(value, metric) {
  if (value === null) return null
  if (metric === 'support') return String(Math.round(value))
  return `${(value * 100).toFixed(1)}%`
}

/**
 * Every model x label x metric as one row each -- long format, not a grid.
 *
 * Wide format would need a column per metric now and a column per dataset and
 * language once QA models them, so its header changes shape every time the
 * benchmark grows. Long format absorbs all of that as more rows, which is why
 * it is the shape a spreadsheet or a notebook can actually pivot. It is also
 * the artifact the developer would otherwise ask QA for.
 *
 * Unavailable measurements are exported too, carrying `available=false` and
 * QA's reason. Dropping them would leave the reader unable to tell a metric
 * nobody reported from one that happened to be zero.
 */
function exportPerLabelCsv(scored, order, displayOf) {
  const header = [
    'team', 'model', 'label', 'label_display', 'metric',
    'value', 'support', 'available', 'reason',
  ]
  const rows = []
  scored.forEach((model, column) => {
    order.forEach(label => {
      // A label this model reported nothing for still gets its rows, carrying
      // the reason. Omitting it would make "nobody measured this" look
      // identical to "this label does not exist", which is the confusion the
      // whole section exists to remove.
      const entry = model.per_label?.[label] || {}
      const support = supportOf(model, label)
      METRICS.forEach(({ id }) => {
        const envelope = entry[id] || {
          available: false,
          value: null,
          reason: reasonOf(model, label, id),
        }
        rows.push([
          model.team,
          model.model || '',
          label,
          displayOf(label),
          id,
          envelope.available ? envelope.value : '',
          support ?? '',
          envelope.available,
          envelope.available ? '' : envelope.reason || '',
        ])
      })
    })
  })
  download(`pii-masking-per-entity-${stamp()}.csv`, toCsv(header, rows))
}

function Cell({ model, label, metric, weakest }) {
  const value = valueOf(model, label, metric)
  const support = supportOf(model, label)
  const text = formatValue(value, metric)
  const thin = metric !== 'support' && support !== null && support < THIN_SUPPORT

  if (text === null) {
    // Never a zero and never blank: a missing measurement and a measurement of
    // zero are opposite claims, and only one of them is true here.
    return (
      <td className="per-label-cell missing" title={reasonOf(model, label, metric) || undefined}>
        not reported
      </td>
    )
  }

  // A proportional bar for scanning, in one hue rather than a red-to-green
  // ramp: only critical_entity_recall has an actual pass/fail gate, and
  // colouring every label as pass or fail would invent a policy that does not
  // exist.
  const fill = metric === 'support' ? null : Math.max(0, Math.min(1, value))
  return (
    <td className={`per-label-cell ${weakest ? 'weakest' : ''}`}>
      <span className="per-label-value">
        {text}
        {thin && (
          <abbr className="thin-support" title={`Only ${Math.round(support)} in the scored set`}>
            n={Math.round(support)}
          </abbr>
        )}
      </span>
      {fill !== null && (
        <span className="per-label-bar" aria-hidden="true">
          <i style={{ width: `${fill * 100}%` }} />
        </span>
      )}
    </td>
  )
}

export default function PerLabel({ models, coverage, taxonomy, championTeam }) {
  const [metric, setMetric] = useState('recall')
  const [sort, setSort] = useState('taxonomy')
  const [query, setQuery] = useState('')
  const [criticalOnly, setCriticalOnly] = useState(false)
  const [hideUnreported, setHideUnreported] = useState(true)

  const scored = useMemo(() => models.filter(model => model.per_label), [models])

  // Ordering and criticality come from the API's taxonomy, so this file keeps
  // no second copy of the label list to drift from the gateway's.
  const order = useMemo(() => {
    const ids = (taxonomy?.labels || []).map(entry => entry.id)
    const seen = new Set()
    scored.forEach(model => Object.keys(model.per_label).forEach(label => seen.add(label)))
    // Anything the taxonomy does not know is still shown, after the rest.
    const extra = [...seen].filter(label => !ids.includes(label)).sort()
    return [...ids, ...extra]
  }, [taxonomy, scored])

  const criticalIds = useMemo(
    () => new Set((taxonomy?.labels || []).filter(entry => entry.critical).map(entry => entry.id)),
    [taxonomy],
  )
  const displayOf = useMemo(() => {
    const map = new Map((taxonomy?.labels || []).map(entry => [entry.id, entry.display]))
    return label => map.get(label) || label.replace(/_/g, ' ')
  }, [taxonomy])

  const rows = useMemo(() => {
    const built = order
      .map(label => {
        const values = scored.map(model => valueOf(model, label, metric))
        const present = values.filter(value => value !== null)
        return {
          label,
          values,
          reported: present.length > 0,
          lowest: present.length ? Math.min(...present) : null,
          spread: present.length > 1 ? Math.max(...present) - Math.min(...present) : 0,
        }
      })
      .filter(row => (hideUnreported ? row.reported : true))
      .filter(row => (criticalOnly ? criticalIds.has(row.label) : true))
      .filter(row =>
        query ? displayOf(row.label).toLowerCase().includes(query.trim().toLowerCase()) : true,
      )

    if (sort === 'name') built.sort((a, b) => displayOf(a.label).localeCompare(displayOf(b.label)))
    if (sort === 'value') built.sort((a, b) => (a.lowest ?? Infinity) - (b.lowest ?? Infinity))
    if (sort === 'spread') built.sort((a, b) => b.spread - a.spread)
    return built
  }, [order, scored, metric, sort, query, criticalOnly, criticalIds, displayOf, hideUnreported])

  // The lowest-scoring label per model, so the eye lands on what regressed.
  const weakest = useMemo(() => {
    if (metric === 'support') return []
    return scored.map((model, column) => {
      let worst = null
      rows.forEach(row => {
        const value = row.values[column]
        if (value === null) return
        // Ignore a thin sample: a bad number over four cases is noise, and
        // pointing at it would send someone chasing the wrong entity.
        const support = supportOf(model, row.label)
        if (support !== null && support < THIN_SUPPORT) return
        if (worst === null || value < worst.value) worst = { label: row.label, value }
      })
      return worst
    })
  }, [scored, rows, metric])

  const hiddenCount = order.length - rows.length

  return (
    <section className="per-label" id="labels" aria-labelledby="per-label-heading">
      <div className="section-heading">
        <div>
          <span className="eyebrow">WHERE EACH MODEL IS WEAK</span>
          <h2 id="per-label-heading"><Tags size={24} />Per-entity breakdown</h2>
          <p>
            The same benchmark, split by entity type — so a change that helped overall but hurt
            one entity is visible here rather than only in a QA report.
          </p>
        </div>
        {/* Top-right of the heading, matching the ranking table's export.
            Exports the whole matrix rather than the filtered view: the filters
            are for reading on screen, and a half-exported file is the kind of
            thing nobody notices until it is already in a report. */}
        {scored.length > 0 && (
          <button
            className="refresh"
            title="Every model, entity and metric as one row each"
            onClick={() => exportPerLabelCsv(scored, order, displayOf)}
          >
            <Download size={16} />Export CSV
          </button>
        )}
      </div>

      {/* Rendered even with nothing to show. An empty section is exactly how
          this gap stayed invisible: nobody could tell whether the models had
          no weak entities or whether the numbers had never arrived. */}
      {!scored.length ? (
        <p className="console-warning per-label-empty">
          <AlertTriangle size={16} />
          <span>
            {coverage?.reason ||
              'No published document for these models reported per-entity metrics.'}
          </span>
        </p>
      ) : (
        <>
          <div className="per-label-controls">
            <div className="basis-toggle" role="group" aria-label="Metric to show">
              {METRICS.map(option => (
                <button
                  key={option.id}
                  className={metric === option.id ? 'active' : ''}
                  title={option.hint}
                  onClick={() => setMetric(option.id)}
                >
                  {option.label}
                </button>
              ))}
            </div>

            <label className="library-search per-label-search">
              <Search size={15} />
              <input
                type="search"
                value={query}
                placeholder="Filter entities"
                aria-label="Filter entities"
                onChange={event => setQuery(event.target.value)}
              />
            </label>

            <div className="basis-toggle" role="group" aria-label="Sort order">
              <ArrowUpDown size={14} className="per-label-sort-icon" />
              {SORTS.map(option => (
                <button
                  key={option.id}
                  className={sort === option.id ? 'active' : ''}
                  onClick={() => setSort(option.id)}
                >
                  {option.label}
                </button>
              ))}
            </div>

            <label className="per-label-check">
              <input
                type="checkbox"
                checked={criticalOnly}
                onChange={() => setCriticalOnly(!criticalOnly)}
              />
              Critical only
            </label>
            <label className="per-label-check">
              <input
                type="checkbox"
                checked={hideUnreported}
                onChange={() => setHideUnreported(!hideUnreported)}
              />
              Hide unreported
            </label>
          </div>

          {/* Weakest-label strip: the answer to "what did my change break",
              above the grid rather than found by scanning it. */}
          {metric !== 'support' && weakest.some(Boolean) && (
            <div className="weakest-strip">
              {scored.map((model, column) => {
                const worst = weakest[column]
                if (!worst) return null
                return (
                  <div className="weakest-card" key={model.team}>
                    <span className="weakest-team">{getTeamDisplayName(model.team, column, championTeam)}</span>
                    <b>{displayOf(worst.label)}</b>
                    <span className="weakest-value">{formatValue(worst.value, metric)}</span>
                    <small>weakest {metric}</small>
                  </div>
                )
              })}
            </div>
          )}

          <div className="table-wrap">
            <table className="per-label-table">
              <thead>
                <tr>
                  <th>Entity</th>
                  {scored.map((model, index) => (
                    <th key={model.team}>
                      <b>{getTeamDisplayName(model.team, index, championTeam)}</b>
                      <small>{model.model}</small>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map(row => (
                  <tr key={row.label}>
                    <th scope="row">
                      {displayOf(row.label)}
                      {criticalIds.has(row.label) && (
                        <abbr className="critical-flag" title="Gated at 98% by the benchmark policy">
                          critical
                        </abbr>
                      )}
                      {!(taxonomy?.labels || []).some(entry => entry.id === row.label) && (
                        <small className="metric-note">not in qa-pii-v1 v2</small>
                      )}
                    </th>
                    {row.values.map((value, column) => (
                      <Cell
                        key={`${row.label}-${scored[column].team}`}
                        model={scored[column]}
                        label={row.label}
                        metric={metric}
                        weakest={weakest[column]?.label === row.label}
                      />
                    ))}
                  </tr>
                ))}
                {!rows.length && (
                  <tr>
                    <td className="empty-row" colSpan={scored.length + 1}>
                      <Check size={18} />No entity matches these filters.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Never hide rows silently: a filter that quietly removes the very
              entity someone came to look at is worse than a longer table. */}
          {hiddenCount > 0 && (
            <p className="availability-note">
              <Info size={16} />
              {hiddenCount} {hiddenCount === 1 ? 'entity is' : 'entities are'} hidden by the
              filters above.
            </p>
          )}
        </>
      )}
    </section>
  )
}

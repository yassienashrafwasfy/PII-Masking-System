"""Build the public leaderboard page (GitHub Pages) from a benchmark results file.

    python -m evaluation.public_benchmarks.site \
        --results evaluation/public_benchmarks/results/<run>.json --out _site

Writes ``index.html`` (one static file: no external scripts, fonts or requests) and a
copy of the results as ``results.json`` (aggregates only). Every table is rendered
into the page, so it reads without JavaScript; a small script adds benchmark tabs and
sorting by any metric. Only the standard library is used, so the Pages workflow needs
no dependencies. Ranks, the winner and the "comes first" shares are the ones
`compare.py` calculated; nothing here is chosen by hand.
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
from pathlib import Path
from typing import Any

from .compare import CANDIDATES, CLEAR_SHARE, DECLARED_LANGUAGES, in_scope
from .report import BENCHMARKS, NAMES, overall_winner

REPO = "https://github.com/yassienashrafwasfy/PII-Masking-System"
# (key, header, higher is better, decimals)
METRICS = (
    ("f1", "F1", True, 3),
    ("recall", "Recall", True, 3),
    ("precision", "Precision", True, 3),
    ("char_recall", "Char recall", True, 3),
    ("typed_f1", "Typed F1", True, 3),
    ("seconds", "s/doc", False, 1),
)
INTERVALS = {"f1": "merged_f1", "recall": "merged_recall"}
ATTRIBUTION = (
    "MEDDOCAN (Marimon et al., IberLEF 2019; CC BY 4.0), MultiGraSCCo v2 "
    "(CC BY 4.0) and the MedDeID Dutch synthetic benchmark (Hellemans et al.; CC BY 4.0)."
)

Row = dict[str, Any]


def _group_values(group: dict[str, Any]) -> dict[str, float]:
    return {
        "f1": group["merged"]["f1"],
        "recall": group["merged"]["recall"],
        "precision": group["merged"]["precision"],
        "char_recall": group["char_recall"],
        "typed_f1": group["typed"]["f1"],
        "seconds": group["seconds_per_document"],
    }


def _group_rows(results: dict[str, Any], group: str) -> list[Row]:
    rows = []
    for config, data in results["configs"].items():
        g = data["groups"][group]
        ci = g["ci95"]
        rows.append(
            {
                "config": config,
                "values": _group_values(g),
                "ci": {k: ci[v] for k, v in INTERVALS.items()},
                "ranked": config in CANDIDATES and in_scope(config, group),
                "outside": config in CANDIDATES and not in_scope(config, group),
                "share": results["comparison"]["groups"][group][config]["first_share"],
            }
        )
    return rows


def _overall_rows(results: dict[str, Any]) -> list[Row]:
    rows = []
    for config, data in results["configs"].items():
        per_group = [_group_values(g) for g in data["groups"].values()]
        o = results["comparison"]["overall"][config]
        rows.append(
            {
                "config": config,
                "values": {
                    key: sum(v[key] for v in per_group) / len(per_group)
                    for key, *_ in METRICS
                },
                "ci": {"f1": o["ci95_f1"], "recall": o["ci95_recall"]},
                "ranked": config in CANDIDATES,
                "outside": False,
                "share": o["first_share"],
            }
        )
    return rows


def _order(rows: list[Row], metric: str) -> list[Row]:
    higher = next(m[2] for m in METRICS if m[0] == metric)
    sign = -1 if higher else 1

    def key(r: Row) -> tuple[float, int]:
        place = CANDIDATES.index(r["config"]) if r["config"] in CANDIDATES else 99
        return (sign * r["values"][metric], place)

    return sorted([r for r in rows if r["ranked"]], key=key) + sorted(
        [r for r in rows if not r["ranked"]], key=key
    )


def _team_label(row: Row) -> str:
    name = html.escape(NAMES[row["config"]])
    if row["outside"]:
        return f'{name} <abbr title="Outside the languages this team is built for; not ranked here">†</abbr>'
    if not row["ranked"]:
        return f'{name} <span class="tag">ablation, not ranked</span>'
    return name


def _cell(row: Row, key: str, decimals: int, best: float | None, higher: bool) -> str:
    value = row["values"][key]
    ci = row["ci"].get(key)
    bar = (
        f'<i style="width:{max(0.0, min(1.0, value)) * 100:.1f}%"></i>'
        if higher
        else ""
    )
    small = f"<small>{ci[0]:.3f}-{ci[1]:.3f}</small>" if ci else ""
    top = (
        ' class="best"' if row["ranked"] and best is not None and value == best else ""
    )
    return f'<td data-v="{value!r}"{top}><span class="v">{value:.{decimals}f}</span>{small}{bar}</td>'


def _table(rows: list[Row], first_label: str) -> str:
    ordered = _order(rows, "f1")
    heads = "".join(
        f'<th scope="col" data-metric="{k}" aria-sort="{"descending" if k == "f1" else "none"}">'
        f'<button type="button">{h}</button></th>'
        for k, h, *_ in METRICS
    )
    body = []
    for place, row in enumerate(ordered):
        rank = str(place + 1) if row["ranked"] else "&ndash;"
        cells = []
        for key, _, higher, decimals in METRICS:
            ranked_values = [r["values"][key] for r in rows if r["ranked"]]
            best = (max if higher else min)(ranked_values) if ranked_values else None
            cells.append(_cell(row, key, decimals, best, higher))
        share = "&ndash;" if row["share"] is None else f"{round(row['share'] * 100)}%"
        body.append(
            f'<tr data-ranked="{int(row["ranked"])}"><td class="rank">{rank}</td>'
            f'<th scope="row">{_team_label(row)}</th>{"".join(cells)}'
            f'<td class="share">{share}</td></tr>'
        )
    return (
        '<div class="scroll"><table><thead><tr><th scope="col">#</th>'
        f'<th scope="col">Team</th>{heads}<th scope="col">{first_label}</th></tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div>"
    )


def _banner(results: dict[str, Any]) -> str:
    c = results["comparison"]
    winner = overall_winner(c)
    o = c["overall"][winner]
    verdict = "clear" if o["first_share"] >= CLEAR_SHARE else "narrow"
    recall_leader = max(
        (t for t in CANDIDATES if t in c["overall"]),
        key=lambda t: (c["overall"][t]["mean_recall"], -CANDIDATES.index(t)),
    )
    note = ""
    if recall_leader != winner:
        r = c["overall"][recall_leader]["mean_recall"]
        note = (
            f'<p class="muted">Recall alone favours {html.escape(NAMES[recall_leader])} '
            f"({r:.3f} against {o['mean_recall']:.3f}); F1 also charges for over-masking.</p>"
        )
    return (
        '<section class="winner" aria-labelledby="winner-h"><p class="eyebrow">Winner</p>'
        f'<h2 id="winner-h">{html.escape(NAMES[winner])}</h2>'
        f"<p>Best mean F1 across the five benchmarks: <strong>{o['mean_f1']:.3f}</strong> "
        f"(95% interval {o['ci95_f1'][0]:.3f}-{o['ci95_f1'][1]:.3f}). "
        f"It comes first in {round(o['first_share'] * 100)}% of document resamples: "
        f"a <strong>{verdict}</strong> result.</p>{note}</section>"
    )


def _views(results: dict[str, Any]) -> tuple[str, str]:
    c = results["comparison"]
    groups = sorted(
        c["groups"], key=lambda g: list(BENCHMARKS).index(g) if g in BENCHMARKS else 99
    )
    tabs = [
        '<button type="button" data-view="overall" aria-selected="true">Overall</button>'
    ]
    sections = [
        '<section class="view" data-view="overall"><h2>Overall</h2>'
        '<p class="muted">Mean over all five benchmarks; every team is scored on every '
        "language. Ranked by mean F1.</p>"
        f'{_table(_overall_rows(results), "Comes first")}</section>'
    ]
    for group in groups:
        label = BENCHMARKS.get(group, group)
        data = results["configs"][next(iter(results["configs"]))]["groups"][group]
        tabs.append(
            f'<button type="button" data-view="{group}" aria-selected="false">{html.escape(label)}</button>'
        )
        sections.append(
            f'<section class="view" data-view="{group}"><h2>{html.escape(label)}</h2>'
            f'<p class="muted">{data["documents"]} documents, {data["gold_spans"]:,} sensitive '
            "spans. Only teams built for this language are ranked; the others are shown "
            "with &dagger; for reference.</p>"
            f'{_table(_group_rows(results, group), "Comes first")}</section>'
        )
    return '<nav class="tabs" aria-label="Benchmark">' + "".join(
        tabs
    ) + "</nav>", "".join(sections)


def _scope_note() -> str:
    parts = [
        f"{NAMES[t]}: {', '.join(sorted(langs))}"
        for t, langs in sorted(DECLARED_LANGUAGES.items())
    ]
    return "; ".join(parts) + "; Synthify and mDeBERTa declare no restriction."


def render(results: dict[str, Any]) -> str:
    tabs, sections = _views(results)
    prov = results.get("provenance", {})
    commit = str(prov.get("git_commit", "unknown"))[:7]
    return (
        PAGE.replace("{{BANNER}}", _banner(results))
        .replace("{{TABS}}", tabs)
        .replace("{{SECTIONS}}", sections)
        .replace("{{SCOPE}}", html.escape(_scope_note()))
        .replace("{{COMMIT}}", html.escape(commit))
        .replace("{{ATTRIBUTION}}", html.escape(ATTRIBUTION))
        .replace("{{REPO}}", REPO)
    )


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>PII Masking Leaderboard</title>
<meta name="description" content="How four PII-masking models score on three open de-identification benchmarks.">
<style>
:root{--bg:#fbfaf7;--fg:#1c1d21;--muted:#5d606a;--line:#dedbd2;--card:#fff;--accent:#2f5d8a;--bar:#2f5d8a22;--best:#e9f1f8}
@media (prefers-color-scheme:dark){:root{--bg:#16181d;--fg:#ececf0;--muted:#a0a4b0;--line:#33363f;--card:#1e2128;--accent:#8db8e6;--bar:#8db8e633;--best:#26364a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:64rem;margin:0 auto;padding:2rem 1rem 3rem}
h1{font-size:1.6rem;margin:0 0 .25rem}h2{font-size:1.25rem;margin:0 0 .25rem}
.muted,small{color:var(--muted)}.muted{margin:.25rem 0 .75rem;font-size:.92rem}
.winner{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:8px;padding:1rem 1.25rem;margin:1.25rem 0}
.winner p{margin:.25rem 0}.eyebrow{text-transform:uppercase;letter-spacing:.08em;font-size:.75rem;color:var(--accent);font-weight:600}
.tabs{display:flex;flex-wrap:wrap;gap:.4rem;margin:1.5rem 0 1rem}
.tabs button{font:inherit;padding:.35rem .8rem;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;cursor:pointer}
.tabs button[aria-selected=true]{background:var(--accent);border-color:var(--accent);color:var(--bg)}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}
table{border-collapse:collapse;width:100%;min-width:42rem;font-variant-numeric:tabular-nums}
th,td{padding:.55rem .7rem;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
tbody tr:last-child>*{border-bottom:0}
th:nth-child(2),td:nth-child(2),th:first-child,td:first-child{text-align:left}
thead th{font-size:.8rem;color:var(--muted);font-weight:600}
th button{all:unset;cursor:pointer;color:inherit;font:inherit}th button:hover,th button:focus-visible{color:var(--accent);text-decoration:underline}
th[aria-sort=descending] button::after{content:" \\2193"}th[aria-sort=ascending] button::after{content:" \\2191"}
td{position:relative}td i{position:absolute;left:0;bottom:0;height:3px;background:var(--bar);pointer-events:none}
td.best{background:var(--best);font-weight:600}td small{display:block;font-size:.72rem}
td.rank{color:var(--muted);width:2rem}tr[data-ranked="0"]{opacity:.62}
.tag{font-size:.72rem;color:var(--muted);border:1px solid var(--line);border-radius:4px;padding:0 .3rem;margin-left:.25rem}
.js .view[hidden]{display:none}.view{margin-bottom:2rem}
details{margin:1.5rem 0}summary{cursor:pointer;font-weight:600}
footer{margin-top:2.5rem;font-size:.9rem;color:var(--muted)}a{color:var(--accent)}
</style>
</head>
<body>
<main>
<h1>PII Masking Leaderboard</h1>
<p class="muted">Four PII-masking models, scored on three open de-identification benchmarks (MEDDOCAN, MultiGraSCCo v2, MedDeID), on CPU, as the system serves them.</p>
{{BANNER}}
{{TABS}}
{{SECTIONS}}
<details><summary>How ranks and the winner are calculated</summary>
<p>Scores are type-agnostic: did a prediction cover the sensitive span, whatever label it carries (spans separated only by punctuation are merged, then matched exactly). The winner has the highest mean F1 over all five benchmarks. Intervals and "comes first" use a paired bootstrap over documents (the same documents are resampled for every team); a win is <em>clear</em> when a team comes first in at least 95% of resamples. Per benchmark, only teams built for the language are ranked ({{SCOPE}}). Click a column header to sort by that metric. &dagger; marks a team outside its languages. The ablation row is the mDeBERTa checkpoint without the platform's regex rules; it is never ranked.</p>
<p>These are zero-shot results from this project's own scorer. They are not comparable to scores published for these benchmarks, and they do not show how a team does on your documents: review masked output before releasing it.</p></details>
<footer>
<p>Data: {{ATTRIBUTION}} Test splits only; no benchmark text is published here.</p>
<p><a href="{{REPO}}/blob/main/docs/benchmarks.md">Full results and caveats</a> &middot; <a href="{{REPO}}">Source</a> &middot; <a href="results.json">results.json</a> (scorer commit {{COMMIT}})</p>
</footer>
</main>
<script>
(function(){
document.documentElement.classList.add("js");
var views=[].slice.call(document.querySelectorAll(".view")),tabs=[].slice.call(document.querySelectorAll(".tabs button"));
function show(id){views.forEach(function(v){v.hidden=v.dataset.view!==id});tabs.forEach(function(t){t.setAttribute("aria-selected",String(t.dataset.view===id))});}
tabs.forEach(function(t){t.addEventListener("click",function(){show(t.dataset.view);if(history.replaceState)history.replaceState(null,"","#"+t.dataset.view)})});
var start=(location.hash||"#overall").slice(1);show(views.some(function(v){return v.dataset.view===start})?start:"overall");
document.querySelectorAll("table").forEach(function(table){
 var heads=[].slice.call(table.querySelectorAll("th[data-metric]"));
 heads.forEach(function(th){th.querySelector("button").addEventListener("click",function(){
  var col=[].indexOf.call(th.parentNode.children,th),asc=th.dataset.metric==="seconds";
  var rows=[].slice.call(table.tBodies[0].rows);
  function v(r){return parseFloat(r.cells[col].dataset.v)}
  rows.sort(function(a,b){var ra=+a.dataset.ranked,rb=+b.dataset.ranked;if(ra!==rb)return rb-ra;return asc?v(a)-v(b):v(b)-v(a)});
  var n=0;rows.forEach(function(r){r.parentNode.appendChild(r);r.cells[0].textContent=+r.dataset.ranked?String(++n):"\\u2013";});
  heads.forEach(function(h){h.setAttribute("aria-sort",h===th?(asc?"ascending":"descending"):"none")});
 })});
});
})();
</script>
</body>
</html>
"""


def build(results_path: Path, out: Path) -> None:
    results = json.loads(results_path.read_text())
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render(results), encoding="utf-8")
    shutil.copyfile(results_path, out / "results.json")
    (out / ".nojekyll").write_text("")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    build(args.results, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

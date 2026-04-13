"""Generate a self-contained HTML report with Chart.js score graphs from the SQLite DB."""

import json
from pathlib import Path

from research_learner.db import LearnerDB

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Research Learner Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0a0a0a; color: #e5e5e5; padding: 24px;
  }
  h1 { font-size: 1.5rem; margin-bottom: 8px; color: #3fea00; }
  h2 { font-size: 1.1rem; margin: 24px 0 12px; color: #ccc; }
  .summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 16px 0; }
  .stat { background: #141414; border: 1px solid #222; border-radius: 8px; padding: 16px; }
  .stat-label { font-size: 0.75rem; color: #888; text-transform: uppercase; letter-spacing: 0.05em; }
  .stat-value { font-size: 1.5rem; font-weight: 600; margin-top: 4px; }
  .chart-container { background: #141414; border: 1px solid #222; border-radius: 8px; padding: 16px; margin: 16px 0; }
  canvas { max-height: 350px; }
  table { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 0.85rem; }
  th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #222; }
  th { color: #888; font-weight: 500; font-size: 0.75rem; text-transform: uppercase; }
  .lineage { margin: 8px 0; }
  .lineage-step { background: #141414; border-left: 3px solid #3fea00; padding: 10px 14px; margin: 6px 0; border-radius: 0 6px 6px 0; }
  .lineage-step .gen { color: #3fea00; font-weight: 600; font-size: 0.8rem; }
  .lineage-step .score { color: #888; font-size: 0.8rem; margin-left: 12px; }
  .lineage-step .desc { margin-top: 4px; font-size: 0.85rem; }
  .muted { color: #666; }
</style>
</head>
<body>

<h1>Research Learner Report</h1>
<p class="muted">Generated from learner.db</p>

<div class="summary">
  <div class="stat">
    <div class="stat-label">Total Iterations</div>
    <div class="stat-value">TOTAL_ITERATIONS</div>
  </div>
  <div class="stat">
    <div class="stat-label">Total Runs</div>
    <div class="stat-value">TOTAL_RUNS</div>
  </div>
  <div class="stat">
    <div class="stat-label">Best Avg Score</div>
    <div class="stat-value">BEST_AVG_SCORE</div>
  </div>
  <div class="stat">
    <div class="stat-label">Best Prompt</div>
    <div class="stat-value">BEST_PROMPT_ID</div>
  </div>
  <div class="stat">
    <div class="stat-label">Prompts Tried</div>
    <div class="stat-value">TOTAL_PROMPTS</div>
  </div>
</div>

<h2>Score Over Time</h2>
<div class="chart-container">
  <canvas id="scoreChart"></canvas>
</div>

<h2>Sub-Score Breakdown</h2>
<div class="chart-container">
  <canvas id="subScoreChart"></canvas>
</div>

<h2>Score History</h2>
<table>
  <thead>
    <tr><th>Iter</th><th>Prompt</th><th>Avg</th><th>Min</th><th>Max</th><th>Timestamp</th></tr>
  </thead>
  <tbody>
    HISTORY_ROWS
  </tbody>
</table>

<h2>Prompt Lineage (Best Prompt)</h2>
<div class="lineage">
  LINEAGE_HTML
</div>

<script>
const DATA = DATA_JSON;

// Score over time chart
const scoreCtx = document.getElementById('scoreChart').getContext('2d');

const queryKeys = [...new Set(DATA.runs.map(r => r.query_key))];
const iterNums = DATA.iterations.map(i => i.iteration_number);

const datasets = [];

// Per-query lines
const colors = ['#3fea00', '#003bff', '#ff5c00', '#ef4444', '#a855f7', '#06b6d4'];
queryKeys.forEach((qk, idx) => {
  const color = colors[idx % colors.length];
  const points = [];
  DATA.iterations.forEach(it => {
    const matching = DATA.runs.filter(r => r.prompt_id === it.prompt_id && r.query_key === qk);
    if (matching.length > 0) {
      points.push({ x: it.iteration_number, y: matching[0].score });
    }
  });
  if (points.length > 0) {
    datasets.push({
      label: qk.substring(0, 30),
      data: points,
      borderColor: color,
      backgroundColor: color + '20',
      tension: 0.3,
      pointRadius: 3,
      borderWidth: 1.5,
    });
  }
});

// Average line
datasets.push({
  label: 'Average',
  data: DATA.iterations.map(i => ({ x: i.iteration_number, y: i.avg_score })),
  borderColor: '#fff',
  backgroundColor: '#fff3',
  tension: 0.3,
  pointRadius: 4,
  borderWidth: 2.5,
  borderDash: [],
});

new Chart(scoreCtx, {
  type: 'line',
  data: { datasets },
  options: {
    responsive: true,
    interaction: { mode: 'index', intersect: false },
    scales: {
      x: { type: 'linear', title: { display: true, text: 'Iteration', color: '#888' }, ticks: { color: '#888' }, grid: { color: '#222' } },
      y: { min: 0, max: 100, title: { display: true, text: 'Score', color: '#888' }, ticks: { color: '#888' }, grid: { color: '#222' } },
    },
    plugins: {
      legend: { labels: { color: '#ccc', font: { size: 11 } } },
      tooltip: {
        callbacks: {
          afterBody: function(context) {
            const iter = context[0].parsed.x;
            const it = DATA.iterations.find(i => i.iteration_number === iter);
            if (it && it.change_description) return 'Change: ' + it.change_description;
            return '';
          }
        }
      }
    },
  },
});

// Sub-score breakdown chart
const subCtx = document.getElementById('subScoreChart').getContext('2d');
const subData = DATA.sub_scores;
new Chart(subCtx, {
  type: 'bar',
  data: {
    labels: subData.map(s => 'Iter ' + s.iteration),
    datasets: [
      { label: 'Coverage', data: subData.map(s => s.coverage), backgroundColor: '#3fea0099' },
      { label: 'Breadth', data: subData.map(s => s.breadth), backgroundColor: '#003bff99' },
      { label: 'Addressability', data: subData.map(s => s.addressability), backgroundColor: '#ff5c0099' },
      { label: 'Efficiency', data: subData.map(s => s.efficiency), backgroundColor: '#a855f799' },
    ],
  },
  options: {
    responsive: true,
    scales: {
      x: { stacked: true, ticks: { color: '#888' }, grid: { color: '#222' } },
      y: { stacked: true, max: 100, title: { display: true, text: 'Total Score', color: '#888' }, ticks: { color: '#888' }, grid: { color: '#222' } },
    },
    plugins: { legend: { labels: { color: '#ccc', font: { size: 11 } } } },
  },
});
</script>
</body>
</html>
"""


def generate_report(db: LearnerDB, output_path: Path | None = None) -> Path:
    """Generate the HTML report from the DB and return the output path."""
    if output_path is None:
        output_path = Path(__file__).parent / "report.html"

    iterations = db.get_all_learner_iterations()
    runs = db.get_all_runs()
    prompts = db.get_all_prompts()
    best = db.get_best_prompt()

    # Build data for charts
    iter_data = []
    sub_score_data = []
    for it in iterations:
        change_desc = ""
        for p in prompts:
            if p.id == it.prompt_id:
                change_desc = p.change_description or ""
                break
        iter_data.append({
            "iteration_number": it.iteration_number,
            "prompt_id": it.prompt_id,
            "avg_score": it.avg_score,
            "min_score": it.min_score,
            "max_score": it.max_score,
            "change_description": change_desc,
        })

        subs = db.get_sub_scores_for_iteration(it.prompt_id)
        sub_score_data.append({
            "iteration": it.iteration_number,
            **subs,
        })

    run_data = [
        {
            "id": r.id,
            "prompt_id": r.prompt_id,
            "query_key": r.query_key,
            "score": r.score,
            "score_coverage": r.score_coverage,
            "score_breadth": r.score_breadth,
            "score_addressability": r.score_addressability,
            "score_efficiency": r.score_efficiency,
        }
        for r in runs
    ]

    data_json = json.dumps({
        "iterations": iter_data,
        "runs": run_data,
        "sub_scores": sub_score_data,
    })

    # Summary values
    best_avg = 0.0
    best_id = "-"
    if best:
        best_avg = db.get_avg_score_for_prompt(best.id) or 0.0
        best_id = f"#{best.id} (gen {best.generation})"

    # History rows
    history_rows = ""
    for it in iterations:
        avg = f"{it.avg_score:.1f}" if it.avg_score is not None else "-"
        mn = f"{it.min_score:.1f}" if it.min_score is not None else "-"
        mx = f"{it.max_score:.1f}" if it.max_score is not None else "-"
        ts = it.created_at[:19] if it.created_at else ""
        history_rows += f"    <tr><td>{it.iteration_number}</td><td>{it.prompt_id}</td><td>{avg}</td><td>{mn}</td><td>{mx}</td><td>{ts}</td></tr>\n"

    if not history_rows:
        history_rows = '    <tr><td colspan="6" class="muted">No iterations recorded yet.</td></tr>'

    # Lineage HTML
    lineage_html = ""
    if best:
        lineage = db.get_prompt_lineage(best.id)
        for p in lineage:
            p_avg = db.get_avg_score_for_prompt(p.id)
            score_str = f"avg={p_avg:.1f}" if p_avg is not None else "not scored"
            desc = p.change_description or "Seed prompt"
            lineage_html += f'  <div class="lineage-step"><span class="gen">Gen {p.generation}</span><span class="score">{score_str}</span><div class="desc">{desc}</div></div>\n'

    if not lineage_html:
        lineage_html = '<p class="muted">No prompts with scores yet.</p>'

    html = HTML_TEMPLATE
    html = html.replace("TOTAL_ITERATIONS", str(len(iterations)))
    html = html.replace("TOTAL_RUNS", str(len(runs)))
    html = html.replace("BEST_AVG_SCORE", f"{best_avg:.1f}")
    html = html.replace("BEST_PROMPT_ID", best_id)
    html = html.replace("TOTAL_PROMPTS", str(len(prompts)))
    html = html.replace("HISTORY_ROWS", history_rows)
    html = html.replace("LINEAGE_HTML", lineage_html)
    html = html.replace("DATA_JSON", data_json)

    output_path.write_text(html)
    return output_path

export default function DebugPanel({
  driving,
  demoMode,
  killWeather,
  busy,
  onStart,
  onMode,
  onKill,
  onSpike,
  onDriveToggle,
  stats,
}) {
  return (
    <aside className="panel">
      <h2>Demo controls</h2>
      <p className="muted">
        Judges: run <b>Normal drive</b> first (stops at L4, no LLM), then{" "}
        <b>Hazard drive</b>, then a <b>spike</b>, then kill the weather feed.
      </p>

      <div className="btn-row">
        <button onClick={onStart} disabled={busy}>
          New route (Kullu → Manali)
        </button>
        <button className={driving ? "danger" : "primary"} onClick={onDriveToggle} disabled={busy}>
          {driving ? "Pause drive" : "Start drive"}
        </button>
      </div>

      <label>Scenario</label>
      <div className="mode-grid">
        {[
          ["normal", "Normal"],
          ["hazard", "Hazard (landslide)"],
          ["flood", "Flood"],
          ["avalanche", "Avalanche"],
        ].map(([id, label]) => (
          <button
            key={id}
            className={demoMode === id ? "selected" : ""}
            onClick={() => onMode(id)}
          >
            {label}
          </button>
        ))}
      </div>

      <label className="toggle">
        <input type="checkbox" checked={killWeather} onChange={(e) => onKill(e.target.checked)} />
        Kill weather API (fallback demo)
      </label>

      <button onClick={onSpike} disabled={busy}>
        Inject one noisy spike
      </button>

      <h3>This tick</h3>
      <dl className="stats">
        <div><dt>Tick</dt><dd>{stats.tick ?? "—"}</dd></div>
        <div><dt>Stopped at</dt><dd>{stats.stoppedAt ?? "—"}</dd></div>
        <div><dt>Threshold</dt><dd>{stats.threshold ? "yes" : "no"}</dd></div>
        <div><dt>Hysteresis</dt><dd>{stats.hysteresis ? "confirmed" : "held"}</dd></div>
        <div><dt>LLM invoked</dt><dd>{stats.llm ? "yes" : "no"}</dd></div>
        <div><dt>Fallback</dt><dd>{stats.fallback ? "active" : "off"}</dd></div>
        <div><dt>ETA Δ</dt><dd>{stats.eta ?? "—"}</dd></div>
        <div><dt>Avoids flagged</dt><dd>{stats.avoids ?? "—"}</dd></div>
      </dl>

      {stats.notes?.length > 0 && (
        <ul className="notes">
          {stats.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
    </aside>
  );
}

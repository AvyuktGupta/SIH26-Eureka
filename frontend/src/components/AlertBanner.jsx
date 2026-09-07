export default function AlertBanner({ alert }) {
  if (!alert) return null;
  return (
    <div className={`alert-card ${alert.alert_level}`}>
      <div className="alert-kicker">
        Driver alert · {alert.source === "llm" ? "gated LLM JSON" : "schema template (LLM unavailable)"}
      </div>
      <div className="alert-headline">{alert.headline}</div>
      <div className="alert-body">{alert.explanation}</div>
      <div className="alert-meta">
        <span>{alert.recommended_action}</span>
        <span>{alert.hazard_type}</span>
        <span>ΔETA {alert.eta_delta_minutes} min</span>
        <span>conf {Math.round(alert.confidence * 100)}%</span>
      </div>
    </div>
  );
}

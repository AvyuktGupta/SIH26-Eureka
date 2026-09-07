export default function PipelineGate({ gate, fallbackActive }) {
  const stopped = gate?.stopped_at || "L4";
  const layers = ["L1", "L2", "L3", "L4", "L5", "L6"];
  const stopIdx = layers.indexOf(stopped);
  return (
    <div className="pipeline">
      {layers.map((id, i) => {
        const reached = i <= stopIdx;
        const isStop = id === stopped;
        const expensive = id === "L5" || id === "L6";
        const skipped = expensive && stopIdx < 4;
        return (
          <div key={id} className="pipe-wrap">
            <div
              className={
                "pipe-node" +
                (reached ? " on" : "") +
                (isStop ? " stop" : "") +
                (skipped ? " skip" : "")
              }
            >
              {id}
            </div>
            {i < layers.length - 1 && <div className={"pipe-line" + (i < stopIdx ? " on" : "")} />}
          </div>
        );
      })}
      <div className="pipe-caption">
        {fallbackActive ? "Fallback active. " : ""}
        {gate?.reason || "Plan a route to start the tick loop."}
      </div>
    </div>
  );
}

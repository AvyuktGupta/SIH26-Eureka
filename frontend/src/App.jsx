import { useEffect, useMemo, useRef, useState } from "react";
import { getMeta, startSession, tickSession } from "./api";
import MapView from "./components/MapView.jsx";
import PipelineGate from "./components/PipelineGate.jsx";
import AlertBanner from "./components/AlertBanner.jsx";
import DebugPanel from "./components/DebugPanel.jsx";

export default function App() {
  const [meta, setMeta] = useState(null);
  const [session, setSession] = useState(null);
  const [tick, setTick] = useState(null);
  const [demoMode, setDemoMode] = useState("normal");
  const [killWeather, setKillWeather] = useState(false);
  const [driving, setDriving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const driveRef = useRef(null);

  useEffect(() => {
    getMeta().then(setMeta).catch((e) => setError(e.message));
  }, []);

  async function plan(mode = demoMode) {
    setBusy(true);
    setError("");
    setDriving(false);
    try {
      const data = await startSession({ demo_mode: mode });
      setSession(data.session);
      setTick(data.tick);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function step(extra = {}) {
    if (!session) return;
    setBusy(true);
    try {
      const data = await tickSession(session.session_id, {
        demo_mode: demoMode,
        kill_weather: killWeather,
        ...extra,
      });
      setTick(data);
    } catch (e) {
      setError(e.message);
      setDriving(false);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!driving) {
      clearInterval(driveRef.current);
      return;
    }
    driveRef.current = setInterval(() => {
      step();
    }, 1600);
    return () => clearInterval(driveRef.current);
  }, [driving, session, demoMode, killWeather]);

  const overlay = tick?.overlay || [];
  const gate = tick?.gate;
  const eta =
    tick?.alternate_route != null
      ? `${Math.round((tick.alternate_route.eta_delta_s || 0) / 60)} min`
      : "—";

  const stats = useMemo(
    () => ({
      tick: tick?.tick,
      stoppedAt: gate?.stopped_at,
      threshold: gate?.threshold_crossed,
      hysteresis: gate?.hysteresis_confirmed,
      llm: gate?.llm_invoked,
      fallback: tick?.fallback_active,
      eta,
      avoids:
        tick?.alternate_route == null
          ? "—"
          : tick.alternate_route.avoids_flagged
            ? "yes"
            : "no",
      notes: tick?.notes || [],
    }),
    [tick, gate, eta]
  );

  return (
    <div className="app">
      <header className="top">
        <div>
          <div className="brand">APCS</div>
          <div className="sub">Adaptive Path & Collision-avoidance · SIH26037</div>
        </div>
        <div className="region">
          {meta?.demo_region?.name || "Kullu–Manali, Himachal Pradesh"}
        </div>
      </header>

      <PipelineGate gate={gate} fallbackActive={tick?.fallback_active} />
      <AlertBanner alert={tick?.alert} />
      {error && <div className="error">{error}</div>}

      <div className="main">
        <MapView
          overlay={overlay}
          route={tick?.route || session?.route}
          alternate={tick?.alternate_route}
          vehicle={tick?.vehicle}
          origin={session?.origin}
          dest={session?.destination}
        />
        <DebugPanel
          driving={driving}
          demoMode={demoMode}
          killWeather={killWeather}
          busy={busy}
          onStart={() => plan(demoMode)}
          onMode={(m) => {
            setDemoMode(m);
          }}
          onKill={setKillWeather}
          onSpike={() => step({ inject_spike: true, step: false })}
          onDriveToggle={() => {
            if (!session) {
              plan(demoMode).then(() => setDriving(true));
              return;
            }
            setDriving((v) => !v);
          }}
          stats={stats}
        />
      </div>
    </div>
  );
}

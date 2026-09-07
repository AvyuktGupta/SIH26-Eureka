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
  const [planning, setPlanning] = useState(false);
  const [error, setError] = useState("");

  const drivingRef = useRef(false);
  const sessionIdRef = useRef(null);
  const demoModeRef = useRef(demoMode);
  const killWeatherRef = useRef(killWeather);

  drivingRef.current = driving;
  sessionIdRef.current = session?.session_id ?? null;
  demoModeRef.current = demoMode;
  killWeatherRef.current = killWeather;

  useEffect(() => {
    getMeta().then(setMeta).catch((e) => setError(e.message));
  }, []);

  function stopDriving() {
    drivingRef.current = false;
    setDriving(false);
  }

  function startDriving() {
    drivingRef.current = true;
    setDriving(true);
  }

  async function plan(mode = demoModeRef.current) {
    stopDriving();
    setPlanning(true);
    setError("");
    try {
      const data = await startSession({ demo_mode: mode });
      setSession(data.session);
      setTick(data.tick);
      return data;
    } catch (e) {
      setError(e.message);
      return null;
    } finally {
      setPlanning(false);
    }
  }

  async function step(extra = {}, { auto = false } = {}) {
    const sid = sessionIdRef.current;
    if (!sid) return null;
    if (auto && !drivingRef.current) return null;
    const data = await tickSession(sid, {
      demo_mode: demoModeRef.current,
      kill_weather: killWeatherRef.current,
      ...extra,
    });
    if (auto && !drivingRef.current) return data;
    setTick(data);
    return data;
  }

  useEffect(() => {
    if (!driving || !session?.session_id) return undefined;
    let cancelled = false;
    (async () => {
      while (drivingRef.current && !cancelled) {
        try {
          await step({}, { auto: true });
        } catch (e) {
          if (!cancelled) {
            setError(e.message);
            stopDriving();
            break;
          }
        }
        if (!drivingRef.current || cancelled) break;
        await new Promise((r) => setTimeout(r, 500));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [driving, session?.session_id]);

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
      following: tick?.following_alternate ? "alternate" : "original",
      ollama: tick?.ollama?.reachable
        ? tick.ollama.selected_model || "up"
        : tick?.ollama?.error
          ? "down"
          : "—",
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
          followingAlternate={!!tick?.following_alternate}
          vehicle={tick?.vehicle}
          origin={session?.origin}
          dest={session?.destination}
        />
        <DebugPanel
          driving={driving}
          demoMode={demoMode}
          killWeather={killWeather}
          planning={planning}
          hasSession={!!session}
          onStart={() => plan(demoMode)}
          onMode={setDemoMode}
          onKill={setKillWeather}
          onSpike={() => step({ inject_spike: true, step: false })}
          onDriveToggle={async () => {
            if (!session) {
              const data = await plan(demoMode);
              if (data) startDriving();
              return;
            }
            if (drivingRef.current) stopDriving();
            else startDriving();
          }}
          stats={stats}
        />
      </div>
    </div>
  );
}

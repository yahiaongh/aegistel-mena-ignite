import React, { useState } from 'react';
import { Shield, Zap, MapPin, Activity, Terminal, CheckCircle2, AlertTriangle, Cpu } from 'lucide-react';

const PRESET_SCENARIOS = [
  {
    title: "High-Value Fintech Transfer",
    icon: Shield,
    text: "A user is attempting a bank transfer of 50,000 SAR from phone number +99999991000. Verify identity and fraud signals."
  },
  {
    title: "Makkah Pilgrim Safety Zone",
    icon: MapPin,
    text: "Verify if pilgrim device +99999991001 is within the safe geofence zone at 21.4225, 39.8262 before allowing passage."
  },
  {
    title: "Emergency Responder Dispatch",
    icon: Zap,
    text: "Emergency responder unit at scene requires immediate QoD priority boost for device +9999123456 to app server 233.252.0.2."
  }
];

export default function AegisTelDashboard() {
  const [prompt, setPrompt] = useState("");
  const [logs, setLogs] = useState([]);
  const [decision, setDecision] = useState("");
  const [loading, setLoading] = useState(false);

  const handleRunOrchestration = async (inputPrompt) => {
    const textToRun = inputPrompt || prompt;
    if (!textToRun) return;

    setLogs([]);
    setDecision("");
    setLoading(true);

    const ws = new WebSocket(`ws://${window.location.hostname}:8000/ws/agent`);

    ws.onopen = () => {
      ws.send(JSON.stringify({ event_description: textToRun }));
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "tool_start" || data.type === "tool_result") {
        setLogs((prev) => [...prev, data]);
      } else if (data.type === "final_decision") {
        setDecision(data.content);
        setLoading(false);
        ws.close();
      }
    };

    ws.onerror = () => {
      setLoading(false);
    };
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans p-6">
      {/* Header */}
      <header className="flex justify-between items-center border-b border-slate-800 pb-4 mb-6">
        <div className="flex items-center space-x-3">
          <Cpu className="w-8 h-8 text-cyan-400 animate-pulse" />
          <div>
            <h1 className="text-2xl font-bold tracking-wider text-white">AEGISTEL</h1>
            <p className="text-xs text-slate-400">Autonomous Telco-Aware AI Guard · GSMA MENA Ignite 2026</p>
          </div>
        </div>
        <div className="flex space-x-2">
          <span className="px-3 py-1 bg-emerald-950 text-emerald-400 border border-emerald-800 text-xs rounded-full flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping"></span> 6 CAMARA APIs Active
          </span>
        </div>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column - Controls & Input */}
        <div className="lg:col-span-5 space-y-6">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider mb-3">Quick Scenarios</h2>
            <div className="space-y-3">
              {PRESET_SCENARIOS.map((sc, idx) => (
                <button
                  key={idx}
                  onClick={() => { setPrompt(sc.text); handleRunOrchestration(sc.text); }}
                  className="w-full text-left p-3 rounded-lg bg-slate-800/50 hover:bg-slate-800 border border-slate-700/50 hover:border-cyan-500 transition-all flex items-start space-x-3 group"
                >
                  <sc.icon className="w-5 h-5 text-cyan-400 mt-0.5 group-hover:scale-110 transition-transform" />
                  <div>
                    <div className="text-sm font-medium text-slate-200">{sc.title}</div>
                    <div className="text-xs text-slate-400 line-clamp-1">{sc.text}</div>
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider mb-2">Custom Telecom Event</h2>
            <textarea
              rows={4}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Describe a transaction, emergency, or location query..."
              className="w-full bg-slate-950 border border-slate-800 rounded-lg p-3 text-sm text-slate-200 focus:outline-none focus:border-cyan-500"
            />
            <button
              onClick={() => handleRunOrchestration()}
              disabled={loading}
              className="w-full mt-3 bg-cyan-600 hover:bg-cyan-500 text-white font-medium py-2 rounded-lg text-sm transition-colors flex justify-center items-center gap-2"
            >
              {loading ? <Activity className="w-4 h-4 animate-spin" /> : "Orchestrate Network Signals"}
            </button>
          </div>
        </div>

        {/* Right Column - Live Agent Reasoning & Decision */}
        <div className="lg:col-span-7 space-y-6">
          {/* Real-time Tool Call Trace */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <div className="flex justify-between items-center mb-3">
              <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                <Terminal className="w-4 h-4 text-cyan-400" /> Live Agent Execution Trace
              </h2>
            </div>
            <div className="bg-slate-950 border border-slate-800/80 rounded-lg p-4 h-64 overflow-y-auto font-mono text-xs space-y-3">
              {logs.length === 0 && !loading && (
                <span className="text-slate-600">Awaiting event orchestration input...</span>
              )}
              {logs.map((log, i) => (
                <div key={i} className="animate-fade-in">
                  {log.type === "tool_start" && (
                    <div className="text-cyan-400">
                      &gt; Calling CAMARA API: <span className="font-bold">{log.tool}</span>({JSON.stringify(log.args)})
                    </div>
                  )}
                  {log.type === "tool_result" && (
                    <div className="text-emerald-400 pl-4 border-l border-slate-800">
                      &lt; Response: {JSON.stringify(log.result)}
                    </div>
                  )}
                </div>
              ))}
              {loading && <div className="text-slate-500 animate-pulse">&gt; LLM Reasoning & Signal Chaining in Progress...</div>}
            </div>
          </div>

          {/* Autonomous Recommendation Output */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider mb-3">Autonomous Recommendation</h2>
            {decision ? (
              <div className="p-4 rounded-lg bg-slate-950 border border-cyan-900/50">
                <p className="text-sm text-slate-200 leading-relaxed whitespace-pre-line">{decision}</p>
              </div>
            ) : (
              <div className="text-xs text-slate-500 italic">No decision generated yet. Run a scenario above.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
"use client";

import React, { useRef, useState } from "react";
import {
  ShieldAlert,
  ShieldCheck,
  Cpu,
  Terminal,
  RefreshCw,
  ArrowRight,
  Phone,
  DollarSign,
  Activity,
  Zap,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Server,
  Volume2,
  MapPin,
  Smartphone,
  Radio,
  Clock,
} from "lucide-react";
import ThreatStream from "./components/ThreatStream";

interface AgentStep {
  agent: string;
  action: string;
  thought: string;
  status: string;
  detail: string;
}

interface AuditResponse {
  msisdn: string;
  amount: number;
  transaction_type: string;
  location?: {
    latitude: number;
    longitude: number;
  };
  sim_swap_cleared: boolean;
  location_cleared: boolean;
  risk_score: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  status: "APPROVED" | "REJECTED" | "BLOCKED" | "STEP_UP_REQUIRED";
  reasoning: string;
  agent_trace: AgentStep[];
}

export default function AegisTelDashboard() {
  const [msisdn, setMsisdn] = useState("+99999991000");
  const [amount, setAmount] = useState("95000");
  const [loading, setLoading] = useState(false);
  const [auditResult, setAuditResult] = useState<AuditResponse | null>(null);
  const [isSpeaking, setIsSpeaking] = useState(false);

  const activeAudioRef = useRef<HTMLAudioElement | null>(null);

  // Play voice alert via ElevenLabs backend endpoint
  const playVoiceAlert = async (text: string) => {
    // Stop and cleanup any currently playing audio immediately
    if (activeAudioRef.current) {
      activeAudioRef.current.pause();
      activeAudioRef.current.currentTime = 0;
      activeAudioRef.current = null;
    }

    // Cancel any browser-native speech fallback if running
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }

    try {
      setIsSpeaking(true);
      const res = await fetch("http://localhost:8000/api/v1/synthesize-alert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });

      if (!res.ok) throw new Error("ElevenLabs API error");

      const blob = await res.blob();
      const audioUrl = URL.createObjectURL(blob);
      const audio = new Audio(audioUrl);

      activeAudioRef.current = audio;

      audio.onended = () => {
        setIsSpeaking(false);
        URL.revokeObjectURL(audioUrl);
        if (activeAudioRef.current === audio) {
          activeAudioRef.current = null;
        }
      };

      audio.onerror = () => {
        setIsSpeaking(false);
        fallbackWebSpeech(text);
      };

      await audio.play();
    } catch (err) {
      console.warn("TTS stream error, falling back to Web Speech:", err);
      fallbackWebSpeech(text);
    }
  };

  // Fallback using browser-native Web Speech API
  const fallbackWebSpeech = (text: string) => {
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 1.0;
      utterance.onend = () => setIsSpeaking(false);
      utterance.onerror = () => setIsSpeaking(false);
      window.speechSynthesis.speak(utterance);
    } else {
      setIsSpeaking(false);
    }
  };

  const runAudit = async (targetPhone = msisdn, targetAmount = amount) => {
    setLoading(true);
    setAuditResult(null);
    try {
      const res = await fetch("http://localhost:8000/api/v1/audit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          msisdn: targetPhone,
          transaction_type: "WIRE_TRANSFER",
          amount: parseFloat(targetAmount),
          location: { latitude: 24.7136, longitude: 46.6753 },
        }),
      });
      if (!res.ok) throw new Error("Backend audit failed");
      const data: AuditResponse = await res.json();
      setAuditResult(data);

      // Trigger automatic audio briefing upon audit completion
      const briefText = `Audit complete. Verdict: ${data.status}. Risk rating: ${data.risk_score}. ${data.reasoning}`;
      playVoiceAlert(briefText);
    } catch {
      alert("Error reaching FastAPI server at http://localhost:8000");
    } finally {
      setLoading(false);
    }
  };

  // Handler for selecting threats from ThreatStream component
  const handleSelectThreat = (threatMsisdn: string, threatAmount: number) => {
    setMsisdn(threatMsisdn);
    setAmount(threatAmount.toString());
    runAudit(threatMsisdn, threatAmount.toString());
  };

  const getAgentBadge = (agent: string) => {
    switch (agent) {
      case "LLM_Risk_Orchestrator":
        return { name: "LLM Risk Orchestrator", color: "text-cyan-400 border-cyan-900/50 bg-cyan-950/40" };
      case "Nokia_CAMARA_Tool":
        return { name: "Nokia CAMARA Tool", color: "text-sky-400 border-sky-900/50 bg-sky-950/40" };
      case "SecuritySpecialist":
        return { name: "Security Specialist", color: "text-rose-400 border-rose-900/50 bg-rose-950/40" };
      case "NetworkQoDAgent":
        return { name: "Network & QoD Agent", color: "text-indigo-400 border-indigo-900/50 bg-indigo-950/40" };
      case "RiskAuditor":
        return { name: "Risk Auditor Core", color: "text-amber-400 border-amber-900/50 bg-amber-950/40" };
      default:
        return { name: agent, color: "text-purple-400 border-purple-900/50 bg-purple-950/40" };
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "APPROVED":
      case "PASSED":
        return (
          <span className="inline-flex items-center gap-1 text-emerald-400 font-bold bg-emerald-950/60 border border-emerald-800 px-2.5 py-0.5 rounded text-xs">
            <CheckCircle2 className="w-3.5 h-3.5" /> {status}
          </span>
        );
      case "REJECTED":
      case "BLOCKED":
      case "FAILED":
      case "CRITICAL":
        return (
          <span className="inline-flex items-center gap-1 text-rose-400 font-bold bg-rose-950/60 border border-rose-800 px-2.5 py-0.5 rounded text-xs">
            <XCircle className="w-3.5 h-3.5" /> {status}
          </span>
        );
      case "EXECUTING":
        return (
          <span className="inline-flex items-center gap-1 text-cyan-400 font-bold bg-cyan-950/60 border border-cyan-800 px-2.5 py-0.5 rounded text-xs animate-pulse">
            <Radio className="w-3.5 h-3.5 animate-spin" /> {status}
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 text-amber-400 font-bold bg-amber-950/60 border border-amber-800 px-2.5 py-0.5 rounded text-xs">
            <AlertTriangle className="w-3.5 h-3.5" /> {status}
          </span>
        );
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-mono selection:bg-cyan-500 selection:text-slate-950">
      {/* Navbar */}
      <nav className="border-b border-slate-800 bg-slate-900/50 backdrop-blur sticky top-0 z-50 px-6 py-3.5 flex justify-between items-center">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-cyan-950 border border-cyan-800 text-cyan-400">
            <Activity className="w-5 h-5 animate-pulse" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-bold tracking-wider text-slate-100">AEGISTEL</h1>
              <span className="text-[10px] bg-cyan-950 text-cyan-400 border border-cyan-800 px-1.5 py-0.5 rounded font-bold">
                GSMA MENA IGNITE
              </span>
            </div>
            <p className="text-[11px] text-slate-400">Autonomous Telecom Multi-Agent Security Engine</p>
          </div>
        </div>

        <div className="flex items-center gap-4 text-xs">
          {isSpeaking && (
            <div className="flex items-center gap-2 bg-rose-950 border border-rose-800 text-rose-400 px-3 py-1.5 rounded-md animate-pulse">
              <Volume2 className="w-4 h-4" />
              <span className="font-bold">AUDIO BRIEFING LIVE</span>
            </div>
          )}
          <div className="flex items-center gap-2 bg-slate-900 border border-slate-800 px-3 py-1.5 rounded-md">
            <Server className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-slate-400">Engine:</span>
            <span className="text-emerald-400 font-semibold">FastAPI :8000</span>
          </div>
          <div className="flex items-center gap-2 bg-slate-900 border border-slate-800 px-3 py-1.5 rounded-md">
            <Zap className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-slate-400">LLM:</span>
            <span className="text-amber-300 font-semibold">Groq Llama-3.3-70B</span>
          </div>
        </div>
      </nav>

      {/* Main Grid */}
      <div className="p-6 max-w-[1600px] mx-auto grid grid-cols-1 lg:grid-cols-12 gap-6">

        {/* Left Column (4 cols) */}
        <div className="lg:col-span-4 space-y-6">

          {/* Live MENA Threat Stream Component */}
          <ThreatStream onSelectThreat={handleSelectThreat} />

          {/* Manual Controller Card */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 shadow-lg space-y-4">
            <div className="flex justify-between items-center border-b border-slate-800 pb-3">
              <h2 className="text-xs font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                <Terminal className="w-4 h-4 text-cyan-400" /> Manual Override
              </h2>
              <span className="text-[10px] text-slate-500">Nokia NaC Sandbox</span>
            </div>

            <div className="space-y-3">
              <div>
                <label className="text-xs text-slate-400 mb-1 flex items-center gap-1.5">
                  <Phone className="w-3.5 h-3.5 text-cyan-400" /> Target Phone (MSISDN)
                </label>
                <input
                  type="text"
                  value={msisdn}
                  onChange={(e) => setMsisdn(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-cyan-300 focus:outline-none focus:border-cyan-500 transition font-mono"
                />
              </div>

              <div>
                <label className="text-xs text-slate-400 mb-1 flex items-center gap-1.5">
                  <DollarSign className="w-3.5 h-3.5 text-amber-400" /> Transfer Amount (USD)
                </label>
                <input
                  type="number"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-amber-300 focus:outline-none focus:border-amber-500 transition font-mono"
                />
              </div>
            </div>

            <button
              onClick={() => runAudit()}
              disabled={loading}
              className="w-full bg-cyan-500 hover:bg-cyan-400 disabled:opacity-50 text-slate-950 font-bold py-2.5 rounded-lg text-xs flex items-center justify-center gap-2 transition shadow-lg shadow-cyan-950/50 cursor-pointer"
            >
              {loading ? (
                <>
                  <RefreshCw className="w-4 h-4 animate-spin text-slate-950" />
                  <span>EXECUTING NOKIA NaC SWARM...</span>
                </>
              ) : (
                <>
                  <Zap className="w-4 h-4 fill-slate-950" />
                  <span>TRIGGER SECURITY AUDIT</span>
                </>
              )}
            </button>
          </div>

        </div>

        {/* Right Column (8 cols) */}
        <div className="lg:col-span-8 space-y-6">

          {/* Verdict Banner */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-6 shadow-lg">
            <div className="flex justify-between items-center mb-4 pb-3 border-b border-slate-800">
              <span className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-2">
                <Cpu className="w-4 h-4 text-indigo-400" /> Audit Verdict Output
              </span>
              {auditResult && (
                <span className="text-xs text-slate-500 font-mono">MSISDN: {auditResult.msisdn}</span>
              )}
            </div>

            {loading ? (
              <div className="py-8 flex flex-col items-center justify-center space-y-3">
                <RefreshCw className="w-8 h-8 text-cyan-400 animate-spin" />
                <p className="text-xs text-slate-400 animate-pulse">Querying CAMARA Network APIs & Synthesizing Reasoning...</p>
              </div>
            ) : auditResult ? (
              <div className="space-y-4">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 bg-slate-950 p-4 rounded-lg border border-slate-800">
                  <div className="space-y-1">
                    <span className="text-[10px] text-slate-500 uppercase tracking-wider block">Decision Status</span>
                    {getStatusBadge(auditResult.status)}
                  </div>

                  <div className="space-y-1">
                    <span className="text-[10px] text-slate-500 uppercase tracking-wider block">Risk Level</span>
                    <span className={`text-sm font-bold ${auditResult.risk_score === "CRITICAL" || auditResult.risk_score === "HIGH" ? "text-rose-400" : "text-emerald-400"}`}>
                      {auditResult.risk_score}
                    </span>
                  </div>

                  <div className="space-y-1">
                    <span className="text-[10px] text-slate-500 uppercase tracking-wider block">SIM Swap Telemetry</span>
                    <span className="flex items-center gap-1.5 text-xs font-bold">
                      <Smartphone className="w-3.5 h-3.5 text-slate-400" />
                      {auditResult.sim_swap_cleared ? (
                        <span className="text-emerald-400">CLEARED</span>
                      ) : (
                        <span className="text-rose-400">SWAP DETECTED</span>
                      )}
                    </span>
                  </div>

                  <div className="space-y-1">
                    <span className="text-[10px] text-slate-500 uppercase tracking-wider block">Location Verification</span>
                    <span className="flex items-center gap-1.5 text-xs font-bold">
                      <MapPin className="w-3.5 h-3.5 text-slate-400" />
                      {auditResult.location_cleared ? (
                        <span className="text-emerald-400">MATCHED</span>
                      ) : (
                        <span className="text-rose-400">MISMATCH</span>
                      )}
                    </span>
                  </div>
                </div>

                <div className="bg-slate-950/60 border border-slate-800/80 p-4 rounded-lg text-xs leading-relaxed text-slate-300 space-y-2">
                  <div className="flex items-center justify-between border-b border-slate-900 pb-2">
                    <span className="text-cyan-400 font-bold uppercase tracking-wider text-[11px]">Auditor Verdict & Synthesis</span>
                    <span className="text-[10px] text-slate-500">Transaction: {auditResult.transaction_type} (${auditResult.amount.toLocaleString()})</span>
                  </div>
                  <p className="text-slate-200">{auditResult.reasoning}</p>
                </div>
              </div>
            ) : (
              <div className="py-8 text-center text-slate-500 text-xs italic">
                Select an incident from the Live Threat Feed or manually enter parameters to run an audit.
              </div>
            )}
          </div>

          {/* Trace Timeline */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-6 shadow-lg space-y-4">
            <h2 className="text-xs font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
              <Terminal className="w-4 h-4 text-cyan-400" /> Multi-Agent Cognitive Trace
            </h2>

            {auditResult ? (
              <div className="relative pl-4 space-y-6 before:absolute before:left-2 before:top-3 before:bottom-3 before:w-0.5 before:bg-slate-800">
                {auditResult.agent_trace.map((step, idx) => {
                  const badge = getAgentBadge(step.agent);
                  return (
                    <div key={idx} className="relative pl-6 space-y-2 group">
                      <div className="absolute -left-4.25 top-1 w-3 h-3 rounded-full bg-slate-950 border-2 border-cyan-400 group-hover:scale-125 transition" />

                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className={`text-xs font-bold px-2 py-0.5 rounded border ${badge.color}`}>
                            {badge.name}
                          </span>
                          <ArrowRight className="w-3 h-3 text-slate-600" />
                          <span className="text-xs font-semibold text-slate-300">{step.action}</span>
                        </div>
                        {getStatusBadge(step.status)}
                      </div>

                      <div className="bg-slate-950 border border-slate-800 rounded-lg p-3 space-y-2">
                        <p className="text-[11px] text-slate-400 leading-normal">
                          <span className="text-slate-500 font-bold uppercase tracking-wider text-[10px] block mb-0.5">LLM Thought Stream:</span>
                          <span className="italic text-slate-200">"{step.thought}"</span>
                        </p>
                        <div className="pt-2 border-t border-slate-900 flex items-center gap-2 text-[10px] text-slate-500 font-mono">
                          <span className="text-slate-400 font-semibold">API Detail:</span>
                          <code className="bg-slate-900 text-cyan-300 px-2 py-0.5 rounded border border-slate-800 break-all">{step.detail}</code>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="py-12 text-center text-slate-600 text-xs italic border border-dashed border-slate-800 rounded-lg">
                No active execution trace.
              </div>
            )}
          </div>

        </div>

      </div>
    </div>
  );
}
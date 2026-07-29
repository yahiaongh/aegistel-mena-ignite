"use client";

import React, { useState } from "react";
import { ShieldAlert, Zap, Radio, Volume2 } from "lucide-react";

interface ThreatEvent {
  id: string;
  location: string;
  msisdn: string;
  amount: number;
  type: string;
  severity: "HIGH" | "CRITICAL" | "MEDIUM";
}

const MOCK_ATTACKS: ThreatEvent[] = [
  { id: "EVT-809", location: "Riyadh Hub", msisdn: "+9999123456", amount: 50000, type: "SIM_SWAP_WIRE_FRAUD", severity: "CRITICAL" },
  { id: "EVT-810", location: "Dubai Node", msisdn: "+9999876543", amount: 50000, type: "VIP_HIGH_VALUE_XFER", severity: "HIGH" },
  { id: "EVT-811", location: "Cairo Tower", msisdn: "+9999123456", amount: 120000, type: "GEO_LOCATION_MISMATCH", severity: "CRITICAL" },
];

export default function ThreatStream({ onSelectThreat }: { onSelectThreat: (msisdn: string, amount: number) => void }) {
  const [activeThreats] = useState(MOCK_ATTACKS);

  return (
    <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 space-y-3 font-mono">
      <div className="flex justify-between items-center border-b border-slate-800 pb-2">
        <span className="text-xs font-bold text-rose-400 flex items-center gap-2">
          <Radio className="w-4 h-4 text-rose-500 animate-ping" /> LIVE MENA CARRIER THREAT FEED
        </span>
      </div>

      <div className="space-y-2">
        {activeThreats.map((threat) => (
          <div
            key={threat.id}
            onClick={() => onSelectThreat(threat.msisdn, threat.amount)}
            className="cursor-pointer bg-slate-950 hover:bg-slate-900 border border-slate-800 hover:border-cyan-500/50 p-2.5 rounded-lg flex items-center justify-between transition group"
          >
            <div className="space-y-0.5">
              <div className="flex items-center gap-2 text-xs font-bold text-slate-200">
                <span className="text-cyan-400">[{threat.location}]</span>
                <span>{threat.msisdn}</span>
              </div>
              <div className="text-[10px] text-slate-500 flex items-center gap-2">
                <span>{threat.type}</span>
                <span>•</span>
                <span className="text-amber-400">${threat.amount.toLocaleString()}</span>
              </div>
            </div>

            <button 
              onClick={(e) => {
                e.stopPropagation();
                onSelectThreat(threat.msisdn, threat.amount);
              }}
              className="bg-rose-950/60 border border-rose-800 group-hover:border-rose-500 text-rose-300 text-[10px] font-bold px-2.5 py-1 rounded flex items-center gap-1 transition hover:cursor-pointer"
            >
              <Zap className="w-3 h-3 fill-rose-300" /> INTERCEPT
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
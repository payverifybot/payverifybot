import { useEffect, useState } from "react";
import "@/App.css";
import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import Dashboard from "@/components/Dashboard";
import Setup from "@/components/Setup";
import Transactions from "@/components/Transactions";
import TestUpload from "@/components/TestUpload";
import { CircleCheckBig, Settings2, ScrollText, Upload, Bot } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

function Shell({ children }) {
  const [status, setStatus] = useState(null);
  useEffect(() => {
    const load = () => fetch(`${API}/status`).then((r) => r.json()).then(setStatus).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  const link = "px-3 py-2 rounded-md text-sm flex items-center gap-2 transition-colors";
  const active = "bg-emerald-500/10 text-emerald-300 border border-emerald-500/20";
  const idle = "text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/60";

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <header className="border-b border-zinc-800/80 bg-zinc-950/80 backdrop-blur sticky top-0 z-20">
        <div className="max-w-6xl mx-auto px-4 md:px-6 py-3 md:py-4 flex flex-wrap items-center gap-3 md:gap-6">
          <div className="flex items-center gap-2">
            <Bot className="w-5 h-5 md:w-6 md:h-6 text-emerald-400" />
            <span className="font-semibold tracking-tight text-sm md:text-base">PayVerify Bot</span>
            {status?.mock_mode && (
              <span data-testid="mock-badge" className="ml-1 text-[10px] uppercase tracking-widest px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30">
                Mock
              </span>
            )}
          </div>
          <nav className="flex items-center gap-1 overflow-x-auto scrollbar-hide -mx-1 px-1 order-3 w-full md:order-none md:w-auto">
            <NavLink data-testid="nav-dashboard" end to="/" className={({ isActive }) => `${link} ${isActive ? active : idle}`}>
              <CircleCheckBig className="w-4 h-4" /> <span className="hidden sm:inline">Dashboard</span>
            </NavLink>
            <NavLink data-testid="nav-transactions" to="/transactions" className={({ isActive }) => `${link} ${isActive ? active : idle}`}>
              <ScrollText className="w-4 h-4" /> <span className="hidden sm:inline">Transactions</span>
            </NavLink>
            <NavLink data-testid="nav-test" to="/test" className={({ isActive }) => `${link} ${isActive ? active : idle}`}>
              <Upload className="w-4 h-4" /> <span className="hidden sm:inline">Test Upload</span>
            </NavLink>
            <NavLink data-testid="nav-setup" to="/setup" className={({ isActive }) => `${link} ${isActive ? active : idle}`}>
              <Settings2 className="w-4 h-4" /> <span className="hidden sm:inline">Setup</span>
            </NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-2 text-xs">
            <StatusPill label="WhatsApp" ok={status?.whatsapp_connected} testid="pill-wa" />
            <StatusPill label={`GPay ×${status?.gpay_active_count || 0}`} ok={status?.gpay_active_count > 0} testid="pill-gpay" />
          </div>
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-6 py-8">{children}</main>
    </div>
  );
}

function StatusPill({ label, ok, testid }) {
  return (
    <span data-testid={testid} className={`px-2.5 py-1 rounded-full border text-[11px] font-medium ${ok ? "border-emerald-500/40 text-emerald-300 bg-emerald-500/10" : "border-zinc-700 text-zinc-400 bg-zinc-900"}`}>
      <span className={`inline-block w-1.5 h-1.5 rounded-full mr-1.5 ${ok ? "bg-emerald-400" : "bg-zinc-600"}`} />
      {label} {ok ? "online" : "offline"}
    </span>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Shell>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/transactions" element={<Transactions />} />
          <Route path="/test" element={<TestUpload />} />
          <Route path="/setup" element={<Setup />} />
        </Routes>
      </Shell>
    </BrowserRouter>
  );
}

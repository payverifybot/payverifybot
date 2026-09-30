import { useEffect, useState } from "react";
import { API } from "@/App";
import { CheckCircle2, XCircle, HelpCircle, Activity, TrendingUp, Copy, Mail, Loader2, Stethoscope, ClipboardCopy } from "lucide-react";

export default function Dashboard() {
  const [stats, setStats] = useState({ total: 0, received: 0, not_received: 0, utr_not_found: 0, duplicate: 0 });
  const [recent, setRecent] = useState([]);
  const [status, setStatus] = useState(null);
  const [digestMsg, setDigestMsg] = useState(null);
  const [sendingDigest, setSendingDigest] = useState(false);
  const [diag, setDiag] = useState(null);
  const [runningDiag, setRunningDiag] = useState(false);

  const load = async () => {
    try {
      const [s, r, st] = await Promise.all([
        fetch(`${API}/transactions/stats`).then((r) => r.json()),
        fetch(`${API}/transactions?limit=6`).then((r) => r.json()),
        fetch(`${API}/status`).then((r) => r.json()),
      ]);
      setStats(s); setRecent(r); setStatus(st);
    } catch { /* transient — silent, next poll retries */ }
  };

  const sendDigestNow = async () => {
    setSendingDigest(true); setDigestMsg(null);
    try {
      const r = await fetch(`${API}/digest/send-now`, { method: "POST" }).then((r) => r.json());
      if (r.sent) setDigestMsg(`Sent to ${r.to} · ${r.stats.received} received / ${r.stats.not_received} not received`);
      else setDigestMsg(r.reason || "Not sent");
    } catch (e) {
      setDigestMsg(String(e));
    } finally {
      setSendingDigest(false);
    }
  };

  const runDiagnostics = async () => {
    setRunningDiag(true); setDiag(null);
    try {
      const r = await fetch(`${API}/diagnostics`).then((r) => r.json());
      setDiag(r);
    } catch (e) {
      setDiag({ error: String(e) });
    } finally {
      setRunningDiag(false);
    }
  };

  const copyDiag = async () => {
    if (!diag) return;
    await navigator.clipboard.writeText(JSON.stringify(diag, null, 2));
    setDigestMsg("Copied full diagnostics to clipboard — paste it in your support chat.");
    setTimeout(() => setDigestMsg(null), 4000);
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 6000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div data-testid="dashboard" className="space-y-8">
      <section>
        <h1 className="text-3xl font-bold tracking-tight">Overview</h1>
        <p className="text-zinc-400 mt-1 text-sm">Real-time status of your payment verification bot.</p>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-5 gap-4">
        <StatCard testid="stat-total"        icon={<Activity className="w-5 h-5" />}     label="Total processed" value={stats.total} accent="zinc" />
        <StatCard testid="stat-received"     icon={<CheckCircle2 className="w-5 h-5" />} label="Received"        value={stats.received} accent="emerald" />
        <StatCard testid="stat-not-received" icon={<XCircle className="w-5 h-5" />}      label="Not received"    value={stats.not_received} accent="rose" />
        <StatCard testid="stat-utr-missing"  icon={<HelpCircle className="w-5 h-5" />}   label="UTR unreadable"  value={stats.utr_not_found} accent="amber" />
        <StatCard testid="stat-duplicate"    icon={<Copy className="w-5 h-5" />}         label="Duplicate"       value={stats.duplicate || 0} accent="violet" />
      </section>

      <section className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 bg-zinc-900/40 border border-zinc-800 rounded-xl p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold">Recent verifications</h2>
            <span className="text-xs text-zinc-500 flex items-center gap-1"><TrendingUp className="w-3 h-3" /> live</span>
          </div>
          {recent.length === 0 ? (
            <p data-testid="empty-recent" className="text-zinc-500 text-sm py-8 text-center">No transactions yet. Upload a screenshot to test.</p>
          ) : (
            <ul className="divide-y divide-zinc-800">
              {recent.map((t) => (
                <li key={t.id} data-testid={`txn-row-${t.id}`} className="py-3 flex items-center gap-4">
                  <StatusIcon status={t.status} />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium truncate">{t.sender}</div>
                    <div className="text-xs text-zinc-500 truncate">
                      UTR ...{t.utr_last4 || "----"} · ₹{t.amount || "?"} · {new Date(t.created_at).toLocaleString()}
                    </div>
                  </div>
                  <span className={`text-xs px-2 py-1 rounded-md ${badgeCls(t.status)}`}>{t.status.replace(/_/g, " ")}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-5 space-y-4">
          <h2 className="text-lg font-semibold">Bot status</h2>
          <RowKV k="Mock mode" v={String(!!status?.mock_mode)} testid="row-mock" />
          <RowKV k="WhatsApp"  v={status?.whatsapp_connected ? "connected" : "offline"} testid="row-wa" />
          <RowKV k="GPay accounts" v={`${status?.gpay_active_count || 0} active / ${status?.gpay_total_count || 0} total`} testid="row-gpay" />
          <div className="border-t border-zinc-800 pt-4 space-y-2">
            <div className="flex items-center gap-2 text-sm font-medium text-zinc-200">
              <Mail className="w-4 h-4 text-emerald-400" /> Daily digest
            </div>
            <button
              data-testid="btn-digest-now"
              onClick={sendDigestNow}
              disabled={sendingDigest}
              className="w-full inline-flex items-center justify-center gap-2 text-xs py-2 rounded bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 border border-emerald-500/30 disabled:opacity-50"
            >
              {sendingDigest ? <Loader2 className="w-3 h-3 animate-spin" /> : <Mail className="w-3 h-3" />}
              Send digest now
            </button>
            {digestMsg && <p data-testid="digest-msg" className="text-xs text-zinc-400 leading-relaxed">{digestMsg}</p>}
          </div>
          {status?.mock_mode && (
            <p className="text-xs text-amber-300/80 leading-relaxed border-t border-zinc-800 pt-3">
              Mock mode: GPay & WhatsApp are simulated. UTRs ending in an even digit are marked {"\"received\""}, odd digit {"\"not received\""}. Turn off in <code className="text-emerald-300">BOT_MOCK_MODE</code> to use live automation.
            </p>
          )}
          <div className="border-t border-zinc-800 pt-4 space-y-2">
            <div className="flex items-center gap-2 text-sm font-medium text-zinc-200">
              <Stethoscope className="w-4 h-4 text-sky-400" /> System diagnostics
            </div>
            <button
              data-testid="btn-run-diag"
              onClick={runDiagnostics}
              disabled={runningDiag}
              className="w-full inline-flex items-center justify-center gap-2 text-xs py-2 rounded bg-sky-500/15 hover:bg-sky-500/25 text-sky-300 border border-sky-500/30 disabled:opacity-50"
            >
              {runningDiag ? <Loader2 className="w-3 h-3 animate-spin" /> : <Stethoscope className="w-3 h-3" />}
              Run full self-check
            </button>
            {diag?.checks && (
              <>
                <ul data-testid="diag-list" className="space-y-1 mt-2 text-[11px]">
                  {diag.checks.map((c) => (
                    <li key={c.name} className="flex items-start gap-2">
                      {c.ok ? <CheckCircle2 className="w-3 h-3 text-emerald-400 mt-0.5 shrink-0" /> : <XCircle className="w-3 h-3 text-rose-400 mt-0.5 shrink-0" />}
                      <div className="flex-1 min-w-0">
                        <div className="text-zinc-300">{c.name}</div>
                        <div className="text-zinc-500 truncate" title={c.detail}>{c.detail}</div>
                      </div>
                    </li>
                  ))}
                </ul>
                <button
                  data-testid="btn-copy-diag"
                  onClick={copyDiag}
                  className="w-full inline-flex items-center justify-center gap-2 text-xs py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 border border-zinc-700"
                >
                  <ClipboardCopy className="w-3 h-3" /> Copy full report
                </button>
              </>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

function StatCard({ icon, label, value, accent, testid }) {
  const map = {
    emerald: "text-emerald-300 border-emerald-500/30 bg-emerald-500/5",
    rose: "text-rose-300 border-rose-500/30 bg-rose-500/5",
    amber: "text-amber-300 border-amber-500/30 bg-amber-500/5",
    zinc: "text-zinc-200 border-zinc-800 bg-zinc-900/40",
    violet: "text-violet-300 border-violet-500/30 bg-violet-500/5",
  };
  return (
    <div data-testid={testid} className={`rounded-xl border p-4 ${map[accent]}`}>
      <div className="flex items-center gap-2 opacity-80">{icon}<span className="text-xs uppercase tracking-wider">{label}</span></div>
      <div className="text-3xl font-bold mt-2 tabular-nums">{value}</div>
    </div>
  );
}

function RowKV({ k, v, testid }) {
  return (
    <div data-testid={testid} className="flex items-center justify-between text-sm">
      <span className="text-zinc-400">{k}</span>
      <span className="font-mono text-zinc-100">{v}</span>
    </div>
  );
}

export function StatusIcon({ status }) {
  if (status === "received") return <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0" />;
  if (status === "not_received") return <XCircle className="w-5 h-5 text-rose-400 shrink-0" />;
  if (status === "duplicate") return <Copy className="w-5 h-5 text-violet-400 shrink-0" />;
  return <HelpCircle className="w-5 h-5 text-amber-400 shrink-0" />;
}

export function badgeCls(status) {
  if (status === "received") return "bg-emerald-500/10 text-emerald-300 border border-emerald-500/30";
  if (status === "not_received") return "bg-rose-500/10 text-rose-300 border border-rose-500/30";
  if (status === "duplicate") return "bg-violet-500/10 text-violet-300 border border-violet-500/30";
  return "bg-amber-500/10 text-amber-300 border border-amber-500/30";
}

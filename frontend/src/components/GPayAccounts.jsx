import { useEffect, useState } from "react";
import { API } from "@/App";
import { Plus, Trash2, LogIn, Loader2, PauseCircle, PlayCircle, AlertTriangle, CheckCircle2 } from "lucide-react";

export default function GPayAccounts() {
  const [accounts, setAccounts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState({ label: "", email: "", password: "" });
  const [busyId, setBusyId] = useState(null);
  const [msg, setMsg] = useState(null);

  const load = async () => {
    const data = await fetch(`${API}/gpay/accounts`).then((r) => r.json());
    setAccounts(Array.isArray(data) ? data : []);
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const addAccount = async () => {
    if (!form.email || !form.password) { setMsg("Email and password required"); return; }
    setLoading(true); setMsg(null);
    try {
      const r = await fetch(`${API}/gpay/accounts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      const d = await r.json();
      if (r.ok) {
        setMsg(d.login?.ok ? `Added and logged in as ${d.account.email}` : `Added, but login failed: ${d.login?.message || "unknown"}`);
        setForm({ label: "", email: "", password: "" });
      } else {
        setMsg(d.detail || "Failed");
      }
      await load();
    } finally { setLoading(false); }
  };

  const hitLimit = async (id) => {
    setBusyId(id);
    await fetch(`${API}/gpay/accounts/${id}/hit-limit`, { method: "POST" });
    setBusyId(null); await load();
  };
  const reactivate = async (id) => {
    setBusyId(id);
    await fetch(`${API}/gpay/accounts/${id}/reactivate`, { method: "POST" });
    setBusyId(null); await load();
  };
  const remove = async (id) => {
    if (!window.confirm("Remove this account permanently?")) return;
    setBusyId(id);
    await fetch(`${API}/gpay/accounts/${id}`, { method: "DELETE" });
    setBusyId(null); await load();
  };
  const relogin = async (id) => {
    const pw = window.prompt("Enter password to re-login (leave blank to use stored):");
    setBusyId(id);
    const fd = new FormData();
    if (pw) fd.append("password", pw);
    await fetch(`${API}/gpay/accounts/${id}/re-login`, { method: "POST", body: fd });
    setBusyId(null); await load();
  };

  return (
    <section data-testid="gpay-accounts" className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">GPay Business accounts</h2>
          <p className="text-xs text-zinc-500 mt-0.5">Payments are verified across every ACTIVE account in parallel. Toggle limit-hit accounts off with one click.</p>
        </div>
        <span data-testid="gpay-count" className="text-xs px-2 py-0.5 rounded-full border border-zinc-700 text-zinc-400">
          {accounts.filter((a) => a.is_active && a.is_logged_in).length} active / {accounts.length} total
        </span>
      </div>

      {accounts.length === 0 ? (
        <p data-testid="gpay-empty" className="text-zinc-500 text-sm text-center py-6">No accounts yet — add one below.</p>
      ) : (
        <ul className="divide-y divide-zinc-800 -mx-2">
          {accounts.map((a) => (
            <li key={a.id} data-testid={`account-${a.id}`} className="px-2 py-3 flex flex-wrap items-center gap-3">
              <div className="flex-1 min-w-[220px]">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm">{a.label}</span>
                  <span className="text-xs text-zinc-500">· {a.email}</span>
                </div>
                <div className="text-[11px] text-zinc-500 mt-0.5 flex items-center gap-2">
                  <StatusDot ok={a.is_active && a.is_logged_in} inactive={!a.is_active} />
                  {a.is_active ? (a.is_logged_in ? "verifying" : "logging in…") : "paused (limit reached)"}
                  {a.last_error && <span className="text-rose-400" title={a.last_error}> · error</span>}
                </div>
              </div>
              <div className="flex items-center gap-1.5">
                {a.is_active ? (
                  <button
                    data-testid={`btn-hit-limit-${a.label}`}
                    disabled={busyId === a.id}
                    onClick={() => hitLimit(a.id)}
                    className="text-xs inline-flex items-center gap-1 px-2.5 py-1.5 rounded border border-amber-500/30 text-amber-300 bg-amber-500/5 hover:bg-amber-500/15"
                  >
                    <PauseCircle className="w-3.5 h-3.5" /> Hit limit
                  </button>
                ) : (
                  <button
                    data-testid={`btn-reactivate-${a.label}`}
                    disabled={busyId === a.id}
                    onClick={() => reactivate(a.id)}
                    className="text-xs inline-flex items-center gap-1 px-2.5 py-1.5 rounded border border-emerald-500/30 text-emerald-300 bg-emerald-500/5 hover:bg-emerald-500/15"
                  >
                    <PlayCircle className="w-3.5 h-3.5" /> Reactivate
                  </button>
                )}
                <button
                  data-testid={`btn-relogin-${a.label}`}
                  disabled={busyId === a.id}
                  onClick={() => relogin(a.id)}
                  className="text-xs inline-flex items-center gap-1 px-2.5 py-1.5 rounded border border-zinc-700 text-zinc-300 hover:bg-zinc-800"
                  title="Re-login"
                ><LogIn className="w-3.5 h-3.5" /></button>
                <button
                  data-testid={`btn-remove-${a.label}`}
                  disabled={busyId === a.id}
                  onClick={() => remove(a.id)}
                  className="text-xs inline-flex items-center gap-1 px-2.5 py-1.5 rounded border border-rose-500/30 text-rose-300 hover:bg-rose-500/10"
                ><Trash2 className="w-3.5 h-3.5" /></button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="border-t border-zinc-800 pt-4 space-y-2">
        <div className="text-sm font-medium">Add another account</div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
          <input data-testid="input-acct-label" className="input" placeholder="Label (Store-A)" value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} />
          <input data-testid="input-acct-email" className="input" placeholder="Google email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          <input data-testid="input-acct-pass" className="input" placeholder="Password" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </div>
        <button data-testid="btn-add-account" onClick={addAccount} disabled={loading} className="btn-primary">
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />} Add & log in
        </button>
        {msg && <p data-testid="add-msg" className="text-xs text-zinc-400">{msg}</p>}
        <p className="text-[11px] text-zinc-500 flex items-start gap-1.5 pt-1">
          <AlertTriangle className="w-3 h-3 text-amber-400 mt-0.5 shrink-0" />
          Passwords are encrypted at rest with a machine-local Fernet key. Never leave the container.
        </p>
      </div>
    </section>
  );
}

function StatusDot({ ok, inactive }) {
  if (inactive) return <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-400" />;
  if (ok) return <CheckCircle2 className="w-3 h-3 text-emerald-400" />;
  return <span className="inline-block w-1.5 h-1.5 rounded-full bg-zinc-600 animate-pulse" />;
}

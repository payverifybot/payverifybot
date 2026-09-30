import { useEffect, useState } from "react";
import { API } from "@/App";
import { Save, Play, Square, Loader2, X, Plus } from "lucide-react";
import GPayAccounts from "./GPayAccounts";

export default function Setup() {
  const [settings, setSettings] = useState(null);
  const [status, setStatus] = useState(null);
  const [saving, setSaving] = useState(false);
  const [waMsg, setWaMsg] = useState(null);

  const loadAll = async () => {
    const [s, st] = await Promise.all([
      fetch(`${API}/settings`).then((r) => r.json()),
      fetch(`${API}/status`).then((r) => r.json()),
    ]);
    setSettings(s); setStatus(st);
  };
  useEffect(() => {
    loadAll();
    const t = setInterval(async () => {
      // Just re-poll status so mock/live banner stays in sync with header toggle.
      try {
        const st = await fetch(`${API}/status`).then((r) => r.json());
        setStatus(st);
      } catch { /* silent */ }
    }, 4000);
    return () => clearInterval(t);
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, []);

  if (!settings) return <p className="text-zinc-500">Loading…</p>;

  const saveSettings = async () => {
    setSaving(true);
    await fetch(`${API}/settings`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) });
    setSaving(false);
    await loadAll();
  };

  const waStart = async () => {
    setWaMsg("Starting...");
    const r = await fetch(`${API}/whatsapp/start`, { method: "POST" });
    const d = await r.json();
    setWaMsg(d.ok ? "WhatsApp session started" : d.error || "Failed");
    await loadAll();
  };
  const waStop = async () => {
    await fetch(`${API}/whatsapp/stop`, { method: "POST" });
    setWaMsg("Stopped");
    await loadAll();
  };

  return (
    <div data-testid="setup-page" className="space-y-8 max-w-3xl">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Setup</h1>
        <p className="text-zinc-400 text-sm mt-1">Configure the bot&apos;s connections and reply behavior.</p>
      </div>

      {status && !status.mock_mode && (
        <div data-testid="live-banner" className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-4 text-sm text-emerald-200 space-y-2">
          <div className="font-semibold flex items-center gap-2"><span className="inline-block w-2 h-2 rounded-full bg-emerald-400 animate-pulse" /> LIVE mode is on</div>
          <ol className="list-decimal list-inside text-emerald-100/80 text-xs space-y-1">
            <li>Remove any mock/test GPay accounts (the ones you added while in Mock mode) using the <b>Remove</b> button.</li>
            <li>Add your REAL GPay Business account — real Google email + real password. Have your phone ready to approve the 2FA prompt.</li>
            <li>Start the WhatsApp session and scan the QR from your phone (WhatsApp -> Linked devices -> Link a device).</li>
            <li>Post a real payment screenshot in your group and watch the bot reply.</li>
          </ol>
        </div>
      )}
      {status?.mock_mode && (
        <div data-testid="mock-banner" className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 text-sm text-amber-200">
          <div className="font-semibold flex items-center gap-2">MOCK mode is on</div>
          <p className="text-amber-100/80 text-xs mt-1">Everything is simulated — no real WhatsApp, no real Google login. Use this to explore the UI. When ready, click the <b>Mock</b> pill in the header to switch to LIVE.</p>
        </div>
      )}

      {/* Bot settings */}
      <section className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-6 space-y-4">
        <h2 className="text-lg font-semibold">Bot settings</h2>
        <GroupChips
          values={settings.group_names || []}
          onChange={(list) => setSettings({ ...settings, group_names: list })}
        />
        <Field label="Success reply template" testid="input-tpl-ok">
          <input value={settings.reply_template_success} onChange={(e) => setSettings({ ...settings, reply_template_success: e.target.value })} className="input" />
          <p className="hint">Placeholders: {"{utr_last4}"}, {"{amount}"}</p>
        </Field>
        <Field label="Failure reply template" testid="input-tpl-fail">
          <input value={settings.reply_template_fail} onChange={(e) => setSettings({ ...settings, reply_template_fail: e.target.value })} className="input" />
        </Field>
        <Field label="Duplicate UTR reply template" testid="input-tpl-dup">
          <input value={settings.reply_template_duplicate || ""} onChange={(e) => setSettings({ ...settings, reply_template_duplicate: e.target.value })} className="input" />
          <p className="hint">Placeholders: {"{utr_last4}"}, {"{orig_sender}"}, {"{orig_time}"}</p>
        </Field>
        <label className="flex items-center gap-2 text-sm text-zinc-300 select-none">
          <input data-testid="chk-auto-reply" type="checkbox" checked={settings.auto_reply} onChange={(e) => setSettings({ ...settings, auto_reply: e.target.checked })} className="accent-emerald-500" />
          Auto-reply in group when verification completes
        </label>

        <div className="border-t border-zinc-800 pt-4 space-y-3">
          <div className="text-sm font-medium text-zinc-200">Daily digest email</div>
          <Field label="Owner email">
            <input data-testid="input-owner-email" type="email" value={settings.owner_email || ""} onChange={(e) => setSettings({ ...settings, owner_email: e.target.value })} className="input" placeholder="you@yourbusiness.com" />
          </Field>
          <Field label="Owner name (used in email greeting)">
            <input data-testid="input-owner-name" value={settings.owner_name || ""} onChange={(e) => setSettings({ ...settings, owner_name: e.target.value })} className="input" placeholder="Rahul" />
          </Field>
          <label className="flex items-center gap-2 text-sm text-zinc-300 select-none">
            <input data-testid="chk-digest" type="checkbox" checked={!!settings.digest_enabled} onChange={(e) => setSettings({ ...settings, digest_enabled: e.target.checked })} className="accent-emerald-500" />
            Email a daily summary at 9:00 PM IST
          </label>
        </div>

        <button data-testid="btn-save-settings" onClick={saveSettings} disabled={saving} className="btn-primary">
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Save settings
        </button>
      </section>

      {/* GPay accounts pool */}
      <GPayAccounts />

      {/* WhatsApp */}
      <section className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">WhatsApp Web session</h2>
          <span className={`text-xs px-2 py-0.5 rounded-full ${status?.whatsapp_connected ? "bg-emerald-500/10 text-emerald-300 border border-emerald-500/30" : "bg-zinc-800 text-zinc-400 border border-zinc-700"}`}>
            {status?.whatsapp_connected ? "connected" : "offline"}
          </span>
        </div>
        {status?.whatsapp_groups?.length > 0 && (
          <div data-testid="active-groups" className="text-xs text-zinc-400">
            Watching: {status.whatsapp_groups.map((g) => (
              <span key={g} className={`inline-block px-2 py-0.5 mx-0.5 rounded-full border ${g === status?.whatsapp_active_group ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/40" : "border-zinc-700 text-zinc-300"}`}>
                {g}{g === status?.whatsapp_active_group ? " • active" : ""}
              </span>
            ))}
          </div>
        )}
        {status?.whatsapp_qr ? (
          <div className="text-center">
            <img src={status.whatsapp_qr} alt="Scan QR" className="mx-auto w-56 h-56 rounded bg-white p-2" />
            <p className="text-xs text-zinc-500 mt-2">Open WhatsApp → Linked devices → scan this QR.</p>
          </div>
        ) : (
          <p className="text-xs text-zinc-500">Once started, if not yet linked, a QR code appears here.</p>
        )}
        <div className="flex gap-2">
          <button data-testid="btn-wa-start" onClick={waStart} className="btn-primary"><Play className="w-4 h-4" /> Start session</button>
          <button data-testid="btn-wa-stop" onClick={waStop} className="btn-secondary"><Square className="w-4 h-4" /> Stop</button>
        </div>
        {waMsg && <p data-testid="wa-msg" className="text-xs text-zinc-400">{waMsg}</p>}
      </section>
    </div>
  );
}

function Field({ label, children, testid }) {
  return (
    <div data-testid={testid} className="space-y-1.5">
      <label className="text-xs uppercase tracking-wide text-zinc-400">{label}</label>
      {children}
    </div>
  );
}

function GroupChips({ values, onChange }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const v = draft.trim();
    if (!v) return;
    if (values.includes(v)) { setDraft(""); return; }
    onChange([...values, v]);
    setDraft("");
  };
  return (
    <Field label="WhatsApp groups to watch" testid="input-groups">
      <div className="flex flex-wrap gap-2 mb-2 min-h-[28px]">
        {values.length === 0 && (
          <span data-testid="groups-empty" className="text-xs text-zinc-500 py-1">No groups added yet.</span>
        )}
        {values.map((g) => (
          <span key={g} data-testid={`chip-${g}`} className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-xs">
            {g}
            <button
              data-testid={`chip-remove-${g}`}
              onClick={() => onChange(values.filter((x) => x !== g))}
              className="hover:text-rose-300"
              aria-label={`Remove ${g}`}
            ><X className="w-3 h-3" /></button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          data-testid="input-new-group"
          className="input flex-1"
          placeholder="Group name (exactly as it appears in WhatsApp)"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }}
        />
        <button data-testid="btn-add-group" onClick={add} className="btn-secondary">
          <Plus className="w-4 h-4" /> Add
        </button>
      </div>
      <p className="hint">Add as many as you like — the bot rotates through them every few seconds. Save settings after editing, then Start session.</p>
    </Field>
  );
}

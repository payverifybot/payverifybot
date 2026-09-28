import { useEffect, useState } from "react";
import { API } from "@/App";
import { StatusIcon, badgeCls } from "./Dashboard";

export default function Transactions() {
  const [items, setItems] = useState([]);
  const [selected, setSelected] = useState(null);
  const [filter, setFilter] = useState("");

  const load = async () => {
    const q = filter ? `?status_filter=${filter}` : "";
    const data = await fetch(`${API}/transactions${q}&limit=100`.replace("?&", "?")).then((r) => r.json());
    setItems(data);
  };

  useEffect(() => { load(); }, [filter]);

  const mark = async (id, status) => {
    const fd = new FormData();
    fd.append("status", status);
    await fetch(`${API}/transactions/${id}/mark`, { method: "POST", body: fd });
    await load();
    if (selected?.id === id) {
      const fresh = await fetch(`${API}/transactions/${id}`).then((r) => r.json());
      setSelected(fresh);
    }
  };

  return (
    <div data-testid="transactions-page" className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Transactions</h1>
          <p className="text-zinc-400 text-sm mt-1">Every screenshot the bot has processed.</p>
        </div>
        <div className="flex items-center gap-2">
          {["", "received", "not_received", "utr_not_found"].map((f) => (
            <button
              key={f || "all"}
              data-testid={`filter-${f || "all"}`}
              onClick={() => setFilter(f)}
              className={`text-xs px-3 py-1.5 rounded-md border ${filter === f ? "border-emerald-500/40 text-emerald-300 bg-emerald-500/10" : "border-zinc-800 text-zinc-400 hover:text-zinc-200"}`}
            >
              {f || "all"}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 bg-zinc-900/40 border border-zinc-800 rounded-xl overflow-hidden">
          {items.length === 0 ? (
            <p data-testid="empty-txns" className="text-zinc-500 text-sm py-12 text-center">No transactions.</p>
          ) : (
            <ul className="divide-y divide-zinc-800">
              {items.map((t) => (
                <li
                  key={t.id}
                  data-testid={`row-${t.id}`}
                  onClick={() => setSelected(t)}
                  className={`px-5 py-3 flex items-center gap-4 cursor-pointer hover:bg-zinc-900/60 ${selected?.id === t.id ? "bg-zinc-900/80" : ""}`}
                >
                  <StatusIcon status={t.status} />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium">{t.sender}</div>
                    <div className="text-xs text-zinc-500">UTR ...{t.utr_last4 || "----"} · ₹{t.amount || "?"}</div>
                  </div>
                  <span className={`text-xs px-2 py-1 rounded-md ${badgeCls(t.status)}`}>{t.status.replace(/_/g, " ")}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-5 sticky top-24 self-start">
          {!selected ? (
            <p className="text-zinc-500 text-sm">Select a transaction to see details.</p>
          ) : (
            <div className="space-y-3 text-sm" data-testid="txn-detail">
              <h3 className="font-semibold text-base">Transaction details</h3>
              <KV k="Status" v={<span className={`text-xs px-2 py-0.5 rounded ${badgeCls(selected.status)}`}>{selected.status}</span>} />
              <KV k="UTR" v={<code className="text-emerald-300">{selected.utr || "—"}</code>} />
              <KV k="Amount" v={`₹${selected.amount || "—"}`} />
              <KV k="Payer" v={selected.payer_name || "—"} />
              <KV k="Sender" v={selected.sender} />
              <KV k="Reply sent" v={selected.replied ? "yes" : "no"} />
              <KV k="Time" v={new Date(selected.created_at).toLocaleString()} />
              <div className="text-xs text-zinc-500 pt-2">Reply text</div>
              <div className="text-xs bg-zinc-950 border border-zinc-800 rounded p-2 font-mono">{selected.reply_text}</div>
              <div className="pt-3 border-t border-zinc-800 flex gap-2">
                <button data-testid="btn-mark-received" onClick={() => mark(selected.id, "received")} className="flex-1 text-xs py-2 rounded bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 border border-emerald-500/30">Mark received</button>
                <button data-testid="btn-mark-not-received" onClick={() => mark(selected.id, "not_received")} className="flex-1 text-xs py-2 rounded bg-rose-500/15 hover:bg-rose-500/25 text-rose-300 border border-rose-500/30">Mark not received</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function KV({ k, v }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="text-zinc-400 text-xs uppercase tracking-wide">{k}</span>
      <span className="text-right">{v}</span>
    </div>
  );
}

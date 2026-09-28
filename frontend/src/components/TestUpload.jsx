import { useState } from "react";
import { API } from "@/App";
import { Upload, Loader2, CheckCircle2, XCircle, HelpCircle } from "lucide-react";

export default function TestUpload() {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  const onPick = (f) => {
    setFile(f);
    setResult(null);
    if (f) setPreview(URL.createObjectURL(f));
  };

  const submit = async () => {
    if (!file) return;
    setLoading(true); setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("sender", "test-upload");
      fd.append("auto_reply", "false");
      const r = await fetch(`${API}/process-screenshot`, { method: "POST", body: fd });
      const data = await r.json();
      setResult(data);
    } catch (e) {
      setResult({ error: String(e) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div data-testid="test-page" className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Test screenshot</h1>
        <p className="text-zinc-400 text-sm mt-1">Upload a payment screenshot to run the full OCR → GPay verify pipeline.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-6">
          <label data-testid="dropzone" htmlFor="file" className="border-2 border-dashed border-zinc-700 rounded-xl p-10 flex flex-col items-center gap-3 cursor-pointer hover:border-emerald-500/50 hover:bg-emerald-500/5 transition">
            <Upload className="w-8 h-8 text-zinc-500" />
            <span className="text-sm text-zinc-400">Click to choose a PNG / JPG / WEBP</span>
            <input id="file" data-testid="file-input" type="file" accept="image/*" className="hidden" onChange={(e) => onPick(e.target.files?.[0] || null)} />
          </label>
          {preview && <img src={preview} alt="preview" className="mt-4 max-h-64 rounded-lg border border-zinc-800 mx-auto" />}
          <button
            data-testid="submit-btn"
            disabled={!file || loading}
            onClick={submit}
            className="mt-4 w-full py-2.5 rounded-md bg-emerald-500 hover:bg-emerald-400 disabled:bg-zinc-800 disabled:text-zinc-500 text-zinc-900 font-medium flex items-center justify-center gap-2 transition"
          >
            {loading ? <><Loader2 className="w-4 h-4 animate-spin" />Processing…</> : "Verify payment"}
          </button>
        </div>

        <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-6">
          <h3 className="font-semibold mb-4">Result</h3>
          {!result ? (
            <p className="text-zinc-500 text-sm">Result will appear here after processing.</p>
          ) : result.error ? (
            <p data-testid="result-error" className="text-rose-400 text-sm">{result.error}</p>
          ) : (
            <div data-testid="result" className="space-y-3 text-sm">
              <div className="flex items-center gap-3">
                {result.status === "received" && <CheckCircle2 className="w-6 h-6 text-emerald-400" />}
                {result.status === "not_received" && <XCircle className="w-6 h-6 text-rose-400" />}
                {result.status === "utr_not_found" && <HelpCircle className="w-6 h-6 text-amber-400" />}
                <span data-testid="result-status" className="font-medium capitalize">{result.status?.replace(/_/g, " ")}</span>
              </div>
              <KV k="UTR" v={<code className="text-emerald-300">{result.utr || "—"}</code>} />
              <KV k="Last 4" v={result.utr_last4 || "—"} />
              <KV k="Amount" v={`₹${result.amount || "—"}`} />
              <KV k="Payer" v={result.payer_name || "—"} />
              <div className="text-xs text-zinc-500 pt-2 uppercase tracking-wide">Reply that would be sent</div>
              <div data-testid="reply-text" className="text-sm bg-zinc-950 border border-zinc-800 rounded p-3 font-mono">{result.reply_text}</div>
              <details className="text-xs text-zinc-500">
                <summary className="cursor-pointer hover:text-zinc-300">Raw OCR JSON</summary>
                <pre className="mt-2 bg-zinc-950 border border-zinc-800 rounded p-2 overflow-auto">{JSON.stringify(result.ocr_raw, null, 2)}</pre>
              </details>
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
      <span>{v}</span>
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";
import { api, fmtInt, type Asset, type EvalReport } from "../api";
import { Tile } from "../components";
import { Viewer } from "./Viewer";

type Q = { query_id: number; text: string; judged: number; relevant: number };
const POOL = 48;
const GRADES: [number, string][] = [[0, "No"], [1, "Relevant"], [2, "Very"]];

/** Build a labelled query set on your own photos: type a query, mark which of the top results are relevant.
 *  Labels are saved in labels.sqlite inside the library folder and never leave this computer. */
export function EvaluateView() {
  const [qs, setQs] = useState<Q[]>([]);
  const [cur, setCur] = useState<Q | null>(null);
  const [text, setText] = useState("");
  const [items, setItems] = useState<Asset[]>([]);
  const [judg, setJudg] = useState<Record<string, number>>({});
  const [rep, setRep] = useState<EvalReport | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [focus, setFocus] = useState(0);
  const [err, setErr] = useState<string | null>(null);

  const reloadQs = useCallback(() => api.queries().then((r) => setQs(r.queries)).catch((e) => setErr(String(e.message || e))), []);
  const reloadEval = useCallback(() => api.evaluate().then(setRep).catch(() => setRep(null)), []);
  useEffect(() => { reloadQs(); reloadEval(); }, [reloadQs, reloadEval]);

  useEffect(() => {
    if (!cur) { setItems([]); setJudg({}); return; }
    setFocus(0);
    Promise.all([api.search(cur.text, {}, POOL, 0), api.judgments(cur.query_id)])
      .then(([p, j]) => { setItems(p.items); setJudg(j.judgments); })
      .catch((e) => setErr(String(e.message || e)));
  }, [cur]);

  const add = async () => {
    const t = text.trim();
    if (!t) return;
    const { query_id } = await api.addQuery(t);
    setText("");
    await reloadQs();
    setCur({ query_id, text: t, judged: 0, relevant: 0 });
  };
  const grade = async (a: Asset, g: number) => {
    if (!cur) return;
    const next = judg[a.asset_id] === g ? null : g;
    await api.judge(cur.query_id, a.asset_id, next);
    setJudg((j) => { const c = { ...j }; if (next === null) delete c[a.asset_id]; else c[a.asset_id] = next; return c; });
    reloadQs(); reloadEval();
  };
  const markRestNo = async () => {
    if (!cur) return;
    for (const a of items) if (judg[a.asset_id] === undefined) await api.judge(cur.query_id, a.asset_id, 0);
    const j = await api.judgments(cur.query_id);
    setJudg(j.judgments); reloadQs(); reloadEval();
  };
  const remove = async (q: Q) => {
    if (!confirm(`Delete the query "${q.text}" and its labels?`)) return;
    await api.deleteQuery(q.query_id);
    if (cur?.query_id === q.query_id) setCur(null);
    reloadQs(); reloadEval();
  };

  // keyboard labelling: arrows move, 0/1/2 grade
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (open !== null || (e.target as HTMLElement)?.tagName === "INPUT" || !items.length) return;
      if (e.key === "ArrowRight") setFocus((f) => Math.min(items.length - 1, f + 1));
      else if (e.key === "ArrowLeft") setFocus((f) => Math.max(0, f - 1));
      else if (["0", "1", "2"].includes(e.key)) { grade(items[focus], Number(e.key)); setFocus((f) => Math.min(items.length - 1, f + 1)); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  });

  const m = rep?.mean || {};
  const cell = (k: string) => (m[k] == null ? "—" : (m[k] as number).toFixed(3));

  return (
    <div className="view">
      <h1>Evaluate search quality</h1>
      <p className="hint" style={{ marginTop: -6 }}>
        Add queries you'd really search for, then mark which of the top {POOL} results are relevant (keys 0 / 1 / 2, arrows to move).
        Scores below update as you label. Labels stay in this library folder.
      </p>
      <div className="evalwrap">
        <div className="stack">
          <div className="row">
            <input className="input" style={{ flex: 1 }} placeholder="New query, e.g. waterfall in a forest" value={text}
              onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
            <button className="btn primary" onClick={add}>Add</button>
          </div>
          <div className="qlist">
            {qs.map((q) => (
              <div key={q.query_id} className={`q ${cur?.query_id === q.query_id ? "on" : ""}`} role="button" tabIndex={0} onClick={() => setCur(q)}>
                <span>{q.text}</span>
                <small title={`${q.relevant} marked relevant; ${q.judged} of the top ${POOL} labelled. Unlabelled results count as not relevant.`}>
                  {q.relevant} relevant · {q.judged}/{POOL} judged <button className="btn" style={{ padding: "0 6px", border: 0 }} onClick={(e) => { e.stopPropagation(); remove(q); }}>×</button></small>
              </div>
            ))}
            {!qs.length && <div className="hint">No queries yet.</div>}
          </div>
          <div className="panel">
            <b>Scores</b> <span className="hint">({rep ? `${rep.queries} labelled queries, ${fmtInt(rep.assets)} items` : "—"})</span>
            <table className="table" style={{ marginTop: 6 }}>
              <thead><tr><th>K</th><th className="num" title="Capped recall: relevant found in the top K ÷ min(K, relevant labelled). A perfect ranking scores 1.0">Recall</th><th className="num">Precision</th><th className="num">nDCG</th><th className="num" title="Expected recall of a random ranking">Random</th></tr></thead>
              <tbody>
                {[1, 5, 10, 20].map((k) => (
                  <tr key={k}><td>@{k}</td><td className="num">{cell(`recall@${k}`)}</td><td className="num">{cell(`precision@${k}`)}</td>
                    <td className="num">{cell(`ndcg@${k}`)}</td><td className="num">{rep?.random_recall[`@${k}`] != null ? `${(100 * (rep.random_recall[`@${k}`] as number)).toFixed(k < 5 ? 3 : 2)}%` : "—"}</td></tr>
                ))}
              </tbody>
            </table>
            <div className="hint" style={{ marginTop: 6 }}>MRR {m.mrr == null ? "—" : (m.mrr as number).toFixed(3)} · unlabelled results count as not relevant</div>
          </div>
          {err && <div className="error">{err}</div>}
        </div>
        <div>
          {!cur && <div className="empty">Pick or add a query on the left.</div>}
          {cur && (
            <>
              <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
                <b>“{cur.text}”</b>
                <button className="btn" onClick={markRestNo}>Mark unlabelled as not relevant</button>
              </div>
              <div className="grid">
                {items.map((a, i) => {
                  const g = judg[a.asset_id];
                  return (
                    <Tile key={a.asset_id} a={a} onOpen={() => setOpen(i)} showScore className={`${g !== undefined ? `g${g}` : ""}`}>
                      {i === focus && <span className="score" style={{ left: 6, right: "auto", background: "var(--accent)" }}>●</span>}
                      <div className="judge" onClick={(e) => e.stopPropagation()}>
                        {GRADES.map(([v, l]) => <button key={v} className={g === v ? "on" : ""} onClick={() => grade(a, v)}>{l}</button>)}
                      </div>
                    </Tile>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
      {open !== null && <Viewer list={items} index={open} onIndex={setOpen} onClose={() => setOpen(null)} onSimilar={() => setOpen(null)} />}
    </div>
  );
}

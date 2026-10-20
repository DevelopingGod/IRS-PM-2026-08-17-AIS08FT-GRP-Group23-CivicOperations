"""Generates 03_Research/notebooks/01_data_preparation.ipynb and 03_Research/notebooks/02_nlp_model_evaluation.ipynb.
Edit the cells here (reviewable as plain Python), then:  py 03_Research/notebooks/_build_notebooks.py
"""
from pathlib import Path

import nbformat as nbf

HERE = Path(__file__).resolve().parent
md = nbf.v4.new_markdown_cell
code = nbf.v4.new_code_cell

READ_FIRST = """> ## ⚠️ READ ME FIRST: every team member goes through this notebook before building their component
> {what}
>
> **Before you start:** `py -m pip install -r requirements-ml.txt` from the repo root.
> **Rule:** do NOT change the shared tables or the synthetic population on your own. If you need a change, say so in
> the team chat first, because these tables feed everyone's component (see `05_Team_Workspace/`)."""

SETUP = '''import sys, pathlib
ROOT = pathlib.Path.cwd().resolve()
while not (ROOT / "01_Application" / "data" / "pipeline").exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / "01_Application"))
import numpy as np, pandas as pd, matplotlib.pyplot as plt
pd.set_option("display.max_columns", 60); pd.set_option("display.width", 200)
P = ROOT / "01_Application" / "data" / "processed"; R = ROOT / "01_Application" / "data" / "reference"
print("repo:", ROOT)'''

# ------------------------------------------------------------------ 01
nb1 = nbf.v4.new_notebook()
nb1.cells = [
    md("# 01 · Data Preparation: making NYC 311 AI-ready for every component\n"
       "Civic Operations · NUS ISS IRS Practice Module · Group 23"),
    md(READ_FIRST.format(what="It explains **where every table comes from, what is real vs synthetic, which component "
                              "uses which table, and the rules that stop label leakage**. It runs on any laptop (CPU)." )),
    md("""## What this notebook does
| Step | Script | Output | Real or synthetic |
|---|---|---|---|
| 1 Download | `data/pipeline/download.py` | `data/raw/*.csv` (git-ignored) | Real (NYC Open Data, Open-Meteo) |
| 2 Clean + label | `data/pipeline/clean.py` | `requests_real.parquet` | Real |
| 2b Aggregates | `data/pipeline/aggregates.py` | `daily_demand_weather.parquet`, `reference/demand_baselines.csv` | Real |
| 3 Synthetic population | `data/pipeline/synthetic.py` | `synthetic_*.parquet` | **Synthetic, labelled** |
| 4 NLP data | `data/pipeline/nlp_data.py` | `synthetic_nlp_utterances.parquet` | **Synthetic**; the gold test set is hand-written |

All logic lives in the scripts (so it is testable and identical for everybody); this notebook runs them and **shows** the data.
Seed = 2026, so everyone who runs it gets the same tables."""),
    code(SETUP),
    md("## 1 · Run the pipeline\nThe download is skipped when files already exist. The first full run takes 1–3 hours "
       "(the 2020+ daily counts are aggregated on NYC's server). Set `RUN = False` to only load existing tables."),
    code('''RUN = False   # True = (re)build everything from data/raw
if RUN:
    from data.pipeline import build
    import sys; sys.argv = ["build"]; build.main()
import json
manifest = json.loads((P / "manifest.json").read_text()) if (P / "manifest.json").exists() else {"tables": []}
pd.DataFrame(manifest["tables"])'''),
    md("## 2 · The real requests: 5 boroughs × up to 10,000 rows\n"
       "Sampled as **random time windows** across the last 12 months (not random rows). A window keeps every report made "
       "in those hours, so duplicates of one incident stay together for C05, and the windows still cover every season and hour."),
    code('''real = pd.read_parquet(P / "requests_real.parquet")
print(f"{len(real):,} real rows, {real.request_type_code.nunique()} request types, {real.created_at.min():%d %b %Y} to {real.created_at.max():%d %b %Y}")
real.groupby(["borough", "complaint_family"]).size().unstack(fill_value=0)'''),
    code('''fig, ax = plt.subplots(1, 2, figsize=(14, 4))
real.request_type_label.value_counts().head(15).plot.barh(ax=ax[0], title="Top request types"); ax[0].invert_yaxis()
real.groupby(real.created_at.dt.to_period("M")).size().plot(ax=ax[1], title="Rows per month (sample)")
plt.tight_layout()'''),
    md("## 3 · Labels built from real data, and what they mean\n"
       "**`outcome_class`** comes from the crew's closing note (`resolution_description`), using ordered per-agency rules in `clean.py`. "
       "It is a **proxy**: it records what the crew *found*, not what the citizen *intended*.\n\n"
       "* `verified_outcome` = GENUINE (GENUINE or SUBSIDED) · UNFOUNDED · INCONCLUSIVE (everything else)\n"
       "* Caveat: NYPD noise and parking calls often close as 'no evidence of violation' because the car or the noise had already gone. Weigh this in C06/C07.\n"
       "* `resolution_hours` (C09 target) is only valid where `resolution_valid` is True (closed, 0–120 days, not duplicate or administrative)."),
    code('''pd.crosstab(real.agency, real.outcome_class, normalize="index").round(2)'''),
    code('''v = real[real.resolution_valid]
v.groupby("request_type_label").resolution_hours.median().sort_values().plot.barh(figsize=(8, 9), logx=True,
    title="Median hours to close (real, valid rows)");'''),
    md("## 4 · The leakage rules (read carefully)\n"
       "| Column | Allowed as model input? | Why |\n|---|---|---|\n"
       "| `resolution_description`, `outcome_class`, `verified_outcome` | **No, label only** | Not known when a request arrives |\n"
       "| `closed_date`, `resolution_hours`, `status` | **No, label only** | Future information |\n"
       "| `hidden_honesty` (synthetic citizens) | **Never, evaluation only** | It is the answer key for C07 |\n"
       "| `split` | Use it | Time-based 70/15/15. Everybody uses the same split |"),
    code('''assert set(real.split) == {"train", "val", "test"}
assert real.groupby("split").created_at.max()["train"] <= real.groupby("split").created_at.min()["test"], "split must be by time"
print("time-based split OK:", real.split.value_counts().to_dict())'''),
    md("## 5 · Weather joined to every request (C08, C09, C10, C13)"),
    code('''real.groupby(pd.cut(real.wx_precip_mm, [-0.1, 0.5, 5, 15, 200]))["complaint_family"].value_counts(normalize=True).unstack().round(3)'''),
    md("## 6 · Daily demand 2020+ (C13 training table)\nOnly present after the full download has finished."),
    code('''dd_path = P / "daily_demand_weather.parquet"
if dd_path.exists():
    dd = pd.read_parquet(dd_path)
    ws = dd[dd.complaint_family == "WATER_SEWER"]
    ws.groupby(pd.cut(ws.wx_precip_mm, [-0.1, 1, 10, 25, 50, 300]))["n"].mean().plot.bar(title="Water/sewer requests per borough-day vs rain (mm)")
else:
    print("daily_demand_weather.parquet not built yet (download step B still running)")'''),
    md("## 7 · The ONE shared synthetic population\n"
       "Generated once and shared by C05, C06, C07, C10, C11 and C12, so the same citizen is the same person everywhere (as the proposal commits)."),
    code('''cit = pd.read_parquet(P / "synthetic_citizens.parquet")
links = pd.read_parquet(P / "synthetic_reporter_links.parquet")
display(cit.archetype.value_counts())
# sanity: hidden honesty agrees with the real outcomes each citizen was linked to
j = links.merge(real[["request_id", "verified_outcome"]], on="request_id").merge(cit, on="citizen_id")
obs = j[j.verified_outcome != "INCONCLUSIVE"].groupby("citizen_id").agg(n=("request_id", "size"),
        genuine=("verified_outcome", lambda s: (s == "GENUINE").mean()), honesty=("hidden_honesty", "first"))
print("corr(observed genuine rate, hidden honesty) for citizens with >=5 reports:",
      round(obs[obs.n >= 5][["genuine", "honesty"]].corr().iloc[0, 1], 3))'''),
    code('''crews = pd.read_parquet(P / "synthetic_crews.parquet")
print(len(crews), "synthetic crews")
crews.groupby(["borough", "agency"]).size().unstack(fill_value=0)'''),
    code('''urg = pd.read_parquet(P / "synthetic_urgency_scenarios.parquet")
display(urg.urgency_ground_truth.value_counts())
urg[["request_type_label", "hazard_indicator", "people_at_risk", "urgency_ground_truth", "reviewer_rationale"]].head(8)'''),
    code('''qs = pd.read_parquet(P / "synthetic_queue_snapshots.parquet")
qs.groupby(["scenario", "eval_split"]).snapshot_id.nunique()'''),
    md("## 8 · Which component uses which table\n"
       "| Component (owner) | Tables |\n|---|---|\n"
       "| C01 NLP bot, C03 emergency (Sankalp) | `synthetic_nlp_utterances`, `reference/nlp_gold_test.csv`, `reference/emergency_lexicon.csv` |\n"
       "| C02 profanity (Khalid) | `synthetic_nlp_utterances` (profanity column), `reference/profanity_lexicon.csv`, gold test |\n"
       "| C04 agency routing (Mohammad) | `requests_real` (agency, outcome_class = REFERRED), `reference/request_catalog.csv`, `data/reference/NYC311_agency_matrix.md` |\n"
       "| C05 duplicate (Nabeel) | `requests_real` (sample_window_id keeps dense windows), `synthetic_reporter_links` + a hand-labelled pair set |\n"
       "| C06 fake (Nabeel) | `requests_real` (outcome_class), `synthetic_reporter_links`, `synthetic_citizens`, `synthetic_burst_requests` |\n"
       "| C07 credibility (Sankalp) | `synthetic_citizens` (hidden_honesty for evaluation only), `synthetic_reporter_links`, `requests_real` |\n"
       "| C08 external, C12 dispatch (Mohammad) | `requests_real` coordinates, OSM (built by the owner), `synthetic_crews` |\n"
       "| C09 resolution time (Mohammad) | `requests_real` (resolution_hours where resolution_valid) |\n"
       "| C10 urgency, C11 priority (Khalid) | `synthetic_urgency_scenarios`, `synthetic_queue_snapshots` |\n"
       "| C13 alerting (Sankalp) | `daily_demand_weather`, `reference/demand_baselines.csv` |"),
    md("## 9 · Small committed sample\n`data/sample/` (a few MB) lets anyone run the contracts and component tests without downloading anything. "
       "Regenerated by `build.py`."),
    code('''sorted(p.name for p in (ROOT / "01_Application" / "data" / "sample").glob("*.csv"))'''),
]

# ------------------------------------------------------------------ 02
nb2 = nbf.v4.new_notebook()
nb2.cells = [
    md("# 02 · NLP Model Evaluation: intent, slots and emergency detection\nCivic Operations · Group 23 · runs locally (GPU if available)"),
    md(READ_FIRST.format(what="It picks the model behind the **C01 intake bot** and the **C03 emergency safeguard** by benchmarking "
                              "light models against each other on a **hand-written gold test set**. **Mohammad runs the full version "
                              "on his local GPU** (the data never leaves our machines); on a laptop set `QUICK = True` for a fast smoke run. "
                              "Khalid: Section D is the harness for your C02 profanity models.")),
    md("""## Why this design
* **Constraint:** the bot must be *intelligent but not resource heavy* (it serves on a free CPU host) → we compare
  models from a 50 KB TF-IDF model up to a 66M-parameter DistilBERT, and report **accuracy and latency and size** together.
* **Honest testing:** training data are template-generated (`synthetic_nlp_utterances`); the test that counts is
  `data/reference/nlp_gold_test.csv`, 152 hand-written, messy, realistic lines never used in training.
* **Safety:** for emergencies we report **recall** first (a missed 911 case is the worst error), at a fixed precision.

| Task | A0 baseline | A1 | A2 | A3 |
|---|---|---|---|---|
| Intent (37 classes) | keyword rules (current C01) | TF-IDF + LinearSVC | MiniLM sentence embeddings + LogReg | fine-tuned DistilBERT |
| Slots (BIO) | regex (current C01) | – | – | fine-tuned DistilBERT token classifier |
| Emergency | lexicon rules (current C03) | TF-IDF + LogReg | rules OR model | – |"""),
    code(SETUP + '''
import time, json, torch
from sklearn.metrics import accuracy_score, f1_score, classification_report, precision_recall_fscore_support
QUICK = True            # False on the GPU machine for the full benchmark
SEED = 2026
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
print("device:", DEVICE, "| quick:", QUICK)'''),
    code('''utt = pd.read_parquet(P / "synthetic_nlp_utterances.parquet")
gold = pd.read_csv(R / "nlp_gold_test.csv")
train, val = utt[utt.split == "train"], utt[utt.split == "val"]
if QUICK:
    train = train.groupby("intent", group_keys=False).apply(lambda g: g.sample(min(len(g), 80), random_state=SEED))
print(len(train), "train |", len(val), "val |", len(gold), "gold (hand-written)")
LABELS = sorted(utt.intent.unique())'''),
    code('''RESULTS = []
def record(task, model, y_true, y_pred, ms_per_item, size_mb, split):
    RESULTS.append(dict(task=task, model=model, split=split, accuracy=accuracy_score(y_true, y_pred),
                        macro_f1=f1_score(y_true, y_pred, average="macro", zero_division=0),
                        ms_per_item=round(ms_per_item, 3), size_mb=round(size_mb, 2)))
    return RESULTS[-1]

def timed(fn, xs):
    t = time.perf_counter(); out = fn(xs); return out, (time.perf_counter() - t) * 1000 / max(len(xs), 1)'''),
    md("## A · Intent classification"),
    md("### A0 · Keyword baseline (the current C01 placeholder logic)"),
    code('''import re, csv
# Self-contained keyword baseline: one word-overlap score per request type, with the
# emergency lexicon checked first, exactly as the rule-based intake does.
KEYWORDS = {r["request_type_code"]: r["keywords"] for r in csv.DictReader(open(R / "intent_keywords.csv", encoding="utf-8"))}
EM_PATTERNS = [re.compile(r["pattern"], re.I) for r in csv.DictReader(open(R / "emergency_lexicon.csv", encoding="utf-8"))]
def a0_one(text):
    if any(p.search(text) for p in EM_PATTERNS):
        return "EMERGENCY"
    words = set(re.findall(r"[a-z\-]+", text.lower()))
    scores = {code: len(words & set(kw.split())) for code, kw in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "OTHER"
def a0(texts):
    return [a0_one(t) for t in texts]
for name, df in [("val", val), ("gold", gold)]:
    pred, ms = timed(a0, df.text.tolist()); print(record("intent", "A0 keywords", df.intent, pred, ms, 0.01, name))'''),
    md("### A1 · TF-IDF (word + character n-grams) + LinearSVC"),
    code('''from sklearn.pipeline import make_pipeline, make_union
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm import LinearSVC
import pickle
a1 = make_pipeline(make_union(TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=2),
                              TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=2)),
                   LinearSVC(C=0.8))
a1.fit(train.text, train.intent)
size = len(pickle.dumps(a1)) / 1e6
for name, df in [("val", val), ("gold", gold)]:
    pred, ms = timed(a1.predict, df.text.tolist()); print(record("intent", "A1 tfidf+svm", df.intent, pred, ms, size, name))'''),
    md("### A2 · MiniLM sentence embeddings (frozen) + Logistic Regression"),
    code('''from sentence_transformers import SentenceTransformer
from sklearn.linear_model import LogisticRegression
enc = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", device=DEVICE)
Xtr = enc.encode(train.text.tolist(), batch_size=128, show_progress_bar=False, normalize_embeddings=True)
a2 = LogisticRegression(max_iter=2000, C=4.0).fit(Xtr, train.intent)
def a2_predict(texts): return a2.predict(enc.encode(texts, batch_size=128, normalize_embeddings=True))
for name, df in [("val", val), ("gold", gold)]:
    pred, ms = timed(a2_predict, df.text.tolist()); print(record("intent", "A2 minilm+logreg", df.intent, pred, ms, 90.9, name))'''),
    md("### A3 · Fine-tuned DistilBERT (GPU recommended)"),
    code('''from transformers import AutoTokenizer, AutoModelForSequenceClassification
from torch.utils.data import DataLoader
MODEL = "distilbert-base-uncased"; EPOCHS = 1 if QUICK else 3
tok = AutoTokenizer.from_pretrained(MODEL)
l2i = {l: i for i, l in enumerate(LABELS)}
a3 = AutoModelForSequenceClassification.from_pretrained(MODEL, num_labels=len(LABELS)).to(DEVICE)
opt = torch.optim.AdamW(a3.parameters(), lr=5e-5)
batches = DataLoader(list(zip(train.text, train.intent.map(l2i))), batch_size=32, shuffle=True)
a3.train()
for ep in range(EPOCHS):
    for texts, ys in batches:
        b = tok(list(texts), padding=True, truncation=True, max_length=64, return_tensors="pt").to(DEVICE)
        loss = a3(**b, labels=ys.to(DEVICE)).loss; loss.backward(); opt.step(); opt.zero_grad()
    print("epoch", ep + 1, "loss", round(loss.item(), 4))
a3.eval()
@torch.no_grad()
def a3_predict(texts):
    out = []
    for i in range(0, len(texts), 64):
        b = tok(texts[i:i + 64], padding=True, truncation=True, max_length=64, return_tensors="pt").to(DEVICE)
        out += [LABELS[j] for j in a3(**b).logits.argmax(-1).tolist()]
    return out
for name, df in [("val", val), ("gold", gold)]:
    pred, ms = timed(a3_predict, df.text.tolist()); print(record("intent", "A3 distilbert-ft", df.intent, pred, ms, 255.0, name))'''),
    md("### Error analysis on the gold set\nRead the mistakes: they tell us which intents confuse (e.g. RT021 drain vs RT022 flooding) and which phrasing the templates missed."),
    code('''best_pred = a1.predict(gold.text)
err = gold.assign(pred=best_pred)[lambda d: d.pred != d.intent]
print(len(err), "errors of", len(gold)); err[["text", "intent", "pred"]].head(25)'''),
    md("## B · Slot filling (address, cross street, borough, time, hazard)\nSpan-level F1 on the validation set (the gold set has no span labels). The regex baseline is the current C01 logic."),
    code('''def spans(tokens, tags):
    out, cur = set(), None
    for i, t in enumerate(tags):
        if t.startswith("B-"): cur = [t[2:], i, i]; out.add(tuple(cur))
        elif t.startswith("I-") and cur and t[2:] == cur[0]: out.discard(tuple(cur)); cur[2] = i; out.add(tuple(cur))
        else: cur = None
    return out
def span_f1(gold_sets, pred_sets):
    tp = sum(len(g & p) for g, p in zip(gold_sets, pred_sets)); fp = sum(len(p - g) for g, p in zip(gold_sets, pred_sets))
    fn = sum(len(g - p) for g, p in zip(gold_sets, pred_sets)); pr = tp / max(tp + fp, 1); rc = tp / max(tp + fn, 1)
    return dict(precision=round(pr, 3), recall=round(rc, 3), f1=round(2 * pr * rc / max(pr + rc, 1e-9), 3))
sv = val[val.has_slot_labels].head(800 if QUICK else 4000)
gold_sets = [spans(list(t), list(g)) for t, g in zip(sv.tokens, sv.bio_tags)]'''),
    code('''# B0 regex baseline: we only score BOROUGH and ADDRESS token overlap, which is what the regex extracts
import re
def regex_tags(tokens):
    r = c01.run(IntakeParseInput(session_id="nb", utterance=" ".join(tokens))).slots
    tags = ["O"] * len(tokens)
    for lab, val_ in (("ADDRESS", r.incident_address), ("BOROUGH", r.borough.value.lower() if r.borough else None)):
        if not val_: continue
        words = val_.lower().split()
        for i in range(len(tokens) - len(words) + 1):
            if [w.lower().strip(",.") for w in tokens[i:i + len(words)]] == words:
                tags[i] = "B-" + lab; tags[i + 1:i + len(words)] = ["I-" + lab] * (len(words) - 1); break
    return tags
b0 = span_f1(gold_sets, [spans(list(t), regex_tags(list(t))) for t in sv.tokens]); b0'''),
    code('''from transformers import AutoModelForTokenClassification
TAGS = sorted({t for ts in utt[utt.has_slot_labels].bio_tags for t in ts}); t2i = {t: i for i, t in enumerate(TAGS)}
tr = train[train.has_slot_labels]
b1 = AutoModelForTokenClassification.from_pretrained(MODEL, num_labels=len(TAGS)).to(DEVICE)
opt = torch.optim.AdamW(b1.parameters(), lr=5e-5)
def encode(tokens_list, tags_list=None):
    enc_ = tok([list(t) for t in tokens_list], is_split_into_words=True, truncation=True, max_length=64, padding=True, return_tensors="pt")
    if tags_list is None: return enc_
    labels = []
    for i, tags in enumerate(tags_list):
        wid, prev, lab = enc_.word_ids(i), None, []
        for w in wid:
            lab.append(-100 if w is None or w == prev else t2i[tags[w]]); prev = w
        labels.append(lab)
    enc_["labels"] = torch.tensor(labels); return enc_
b1.train()
for ep in range(EPOCHS):
    for i in range(0, len(tr), 32):
        chunk = tr.iloc[i:i + 32]; b = encode(chunk.tokens, chunk.bio_tags).to(DEVICE)
        loss = b1(**b).loss; loss.backward(); opt.step(); opt.zero_grad()
b1.eval()
@torch.no_grad()
def b1_tags(tokens):
    e = encode([tokens]).to(DEVICE); pred = b1(**e).logits.argmax(-1)[0].tolist(); out, prev = ["O"] * len(tokens), None
    for k, w in enumerate(e.word_ids(0)):
        if w is not None and w != prev: out[w] = TAGS[pred[k]]
        prev = w
    return out
b1_score = span_f1(gold_sets, [spans(list(t), b1_tags(list(t))) for t in sv.tokens]); b1_score'''),
    md("## C · Emergency detection (C03): recall first"),
    code('''# The rules run from the shared lexicon, in this notebook, with no package to install.
NEGATION = re.compile(r"\b(no one|nobody|not) (is )?(hurt|injured|inside|stuck|trapped)\b", re.I)
LEX = [(re.compile(r["pattern"], re.I), float(r["weight"])) for r in
       csv.DictReader(open(R / "emergency_lexicon.csv", encoding="utf-8"))]
def rule_hit(text):
    hits = [w for pat, w in LEX if pat.search(text)]
    if not hits:
        return False
    return max(hits) * (0.6 if NEGATION.search(text) else 1.0) > 0
g_is_em = (gold.intent == "EMERGENCY").to_numpy()
rules = np.array([rule_hit(t) for t in gold.text])
from sklearn.linear_model import LogisticRegression as LR
em = make_pipeline(TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True), LR(max_iter=2000, class_weight="balanced"))
em.fit(train.text, train.intent == "EMERGENCY")
proba = em.predict_proba(gold.text)[:, 1]
rows = []
for name, pred in [("C0 rules", rules), ("C1 tfidf+logreg @0.5", proba >= 0.5), ("C2 rules OR model@0.35", rules | (proba >= 0.35))]:
    p, r, f, _ = precision_recall_fscore_support(g_is_em, pred, average="binary", zero_division=0)
    fp_hydrant = int(pred[gold.text.str.contains("hydrant|fire escape", case=False).to_numpy()].sum())
    rows.append(dict(model=name, recall=round(r, 3), precision=round(p, 3), f1=round(f, 3), false_alarms_on_hydrant_lines=fp_hydrant))
pd.DataFrame(rows)'''),
    code('''missed = gold[g_is_em & ~(rules | (proba >= 0.35))]
print("Emergencies still missed (must be zero before demo):"); missed[["text", "emergency_category"]]'''),
    md("## D · Profanity (C02): harness for Khalid\nPlug your models in here; the gold set has a `profanity` column (NONE / MILD / SEVERE). "
       "Report precision, recall and the false-positive rate on clean lines. This notebook deliberately does not implement C02."),
    code('''def evaluate_profanity(predict_fn, name):
    y = gold.profanity.ne("NONE").to_numpy(); pred = np.array([bool(predict_fn(t)) for t in gold.text])
    p, r, f, _ = precision_recall_fscore_support(y, pred, average="binary", zero_division=0)
    return dict(model=name, precision=round(p, 3), recall=round(r, 3), f1=round(f, 3), fpr_clean=round(pred[~y].mean(), 3))
# Example once C02 exists:
# evaluate_profanity(my_predict_fn, "C02 v1")'''),
    md("## E · Results and decision"),
    code('''res = pd.DataFrame(RESULTS)
table = res.pivot_table(index="model", columns="split", values=["accuracy", "macro_f1"]).round(3)
table.columns = [f"{m}_{s}" for m, s in table.columns]
table.join(res.groupby("model")[["ms_per_item", "size_mb"]].first()).sort_values("macro_f1_gold", ascending=False)'''),
    md("""**Decision rule (agreed before looking at the numbers):** choose the smallest model whose **gold macro-F1** is within
2 points of the best, provided CPU latency is < 20 ms per utterance. For emergencies choose the variant with **recall = 1.0 on gold**
and the highest precision. Record the choice and the numbers in `05_Team_Workspace/` and the report."""),
    code('''# Export the chosen artifacts (joblib or HF format) with a manifest, so the API can load exactly this version
import joblib, hashlib, datetime
OUT = ROOT / "models"; OUT.mkdir(exist_ok=True)
joblib.dump(a1, OUT / "c01_intent_tfidf_svm.joblib"); joblib.dump(em, OUT / "c03_emergency_tfidf_lr.joblib")
man = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:16] for p in OUT.glob("*.joblib")}
man["_trained_at"] = datetime.datetime.now().isoformat(timespec="seconds"); man["_quick"] = QUICK
import sklearn; man["_sklearn"] = sklearn.__version__
(OUT / "manifest.json").write_text(json.dumps(man, indent=2)); man'''),
]

for nb, name in [(nb1, "01_data_preparation.ipynb"), (nb2, "02_nlp_model_evaluation.ipynb")]:
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nbf.write(nb, HERE / name)
    print("wrote", name)

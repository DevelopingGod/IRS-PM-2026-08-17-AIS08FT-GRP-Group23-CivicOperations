"""C05 labelling tools: the pair kit, the overlap kappa, and the candidate-set sheet.

    py 06_Workspace_Tools/c05_labelling.py sample [--source processed --n 300 --out-dir 01_Application/data/processed/c05_label_kit]
    py 06_Workspace_Tools/c05_labelling.py kappa --a SHEET_L1.csv --b SHEET_L2.csv
    py 06_Workspace_Tools/c05_labelling.py candidate-sheet [--max-per-set 60]

Library use (eval script, notebooks): load_requests, load_eval_pairs, pair_to_run_inputs.
No model score is written to a sheet. resolution_description is never shown to labellers.
"""
from __future__ import annotations

from pathlib import Path
import argparse
import math
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
# The app folder is 01_Application in the working repository, and the parent folder itself
# when this file is shipped inside the submission's SystemCode/tools.
APP = ROOT / "01_Application" if (ROOT / "01_Application").is_dir() else ROOT
sys.path.insert(0, str(APP))
sys.path.insert(0, str(ROOT / "06_Workspace_Tools"))

from components.c05_duplicate import address_similarity, haversine_m, usable_bbl  # noqa: E402


# ============================================================================
# Pair kit: sampling, sheets, kappa, loading labels
# ============================================================================

REPO = Path(__file__).resolve().parents[1]


SEED = 2026


LABEL_VALUES = ("DUPLICATE", "NEW", "RECURRENCE", "UNSURE")


GOLD_VALUES = ("DUPLICATE", "NEW", "RECURRENCE")


PAIR_COLUMNS = [
    "pair_id", "sample_window_id", "request_type_code", "complaint_type", "complaint_family",
    "borough", "split",
    "request_id_a", "request_id_b", "created_at_a", "created_at_b", "closed_at_a", "closed_at_b",
    "open_closed_a", "open_closed_b", "address_a", "address_b", "address_type_a", "address_type_b",
    "bbl_a", "bbl_b", "latitude_a", "longitude_a", "latitude_b", "longitude_b",
    "channel_a", "channel_b", "free_text_a", "free_text_b",
    "distance_m", "time_gap_hours", "distance_bin", "time_bin",
    "assignment_set", "labeller_id", "label", "notes", "label_source", "is_synthetic",
]


def load_requests(source: str = "auto") -> tuple[pd.DataFrame, str]:
    """Prefer 01_Application/data/processed (full real table). Fall back to the committed sample."""
    processed = APP / "data" / "processed" / "requests_real.parquet"
    sample = APP / "data" / "sample" / "requests_real_sample.csv"
    if source == "processed" or (source == "auto" and processed.exists()):
        if not processed.exists():
            raise FileNotFoundError(
                "01_Application/data/processed/requests_real.parquet is missing. "
                "Unzip the OneDrive 05_Data pack, or run: py -m data.pipeline.build"
            )
        df = pd.read_parquet(processed)
        return df, "processed"
    if not sample.exists():
        raise FileNotFoundError(sample)
    return pd.read_csv(sample), "sample"


def _text_field(row: pd.Series) -> str:
    for col in ("free_text", "descriptor", "complaint_type"):
        val = row.get(col)
        if isinstance(val, str) and val.strip():
            return val.strip()[:400]
    return ""


def _bin_distance(metres: float) -> str | None:
    if metres < 75:
        return "very_close"
    if metres < 300:
        return "medium"
    if metres < 1500:
        return "far"
    return None


def _bin_time(hours: float) -> str | None:
    if hours < 6:
        return "short"
    if hours < 48:
        return "medium"
    if hours < 24 * 14:
        return "long"
    return None


def enumerate_candidate_pairs(df: pd.DataFrame) -> pd.DataFrame:
    """All unordered pairs that share complaint type and sample_window_id and have coordinates."""
    need = {"request_id", "sample_window_id", "request_type_code", "created_at", "latitude", "longitude"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"requests table missing columns: {sorted(missing)}")
    work = df.copy()
    work["created_at"] = pd.to_datetime(work["created_at"], errors="coerce")
    if "closed_date" in work.columns:
        work["closed_at"] = pd.to_datetime(work["closed_date"], errors="coerce")
    elif "closed_at" in work.columns:
        work["closed_at"] = pd.to_datetime(work["closed_at"], errors="coerce")
    else:
        work["closed_at"] = pd.NaT
    work = work[work["created_at"].notna() & work["latitude"].notna() & work["longitude"].notna()]
    rows = []
    grouped = work.groupby(["sample_window_id", "request_type_code"], sort=False)
    for (window, rtype), g in grouped:
        if len(g) < 2:
            continue
        recs = g.to_dict("records")
        for i in range(len(recs)):
            for j in range(i + 1, len(recs)):
                a, b = recs[i], recs[j]
                if pd.Timestamp(a["created_at"]) > pd.Timestamp(b["created_at"]):
                    a, b = b, a
                dist = haversine_m(float(a["latitude"]), float(a["longitude"]),
                                   float(b["latitude"]), float(b["longitude"]))
                dbin = _bin_distance(dist)
                hours = abs((pd.Timestamp(b["created_at"]) - pd.Timestamp(a["created_at"])).total_seconds()) / 3600.0
                tbin = _bin_time(hours)
                if dbin is None or tbin is None:
                    continue
                closed_a = a.get("closed_at")
                still_open = (pd.isna(closed_a)
                              or pd.Timestamp(closed_a) > pd.Timestamp(b["created_at"]))
                rows.append({
                    "sample_window_id": window,
                    "request_type_code": rtype,
                    "complaint_type": a.get("complaint_type") or a.get("request_type_label") or rtype,
                    "complaint_family": a.get("complaint_family") or "",
                    "borough": a.get("borough"),
                    "split": b.get("split") or a.get("split") or "train",
                    "request_id_a": a["request_id"],
                    "request_id_b": b["request_id"],
                    "created_at_a": pd.Timestamp(a["created_at"]).isoformat(),
                    "created_at_b": pd.Timestamp(b["created_at"]).isoformat(),
                    "closed_at_a": "" if pd.isna(closed_a) else pd.Timestamp(closed_a).isoformat(),
                    "closed_at_b": "" if pd.isna(b.get("closed_at")) else pd.Timestamp(b["closed_at"]).isoformat(),
                    "open_closed_a": "OPEN" if still_open else "CLOSED",
                    "open_closed_b": "OPEN",
                    "address_a": a.get("incident_address") or "",
                    "address_b": b.get("incident_address") or "",
                    "address_type_a": a.get("address_type") or "",
                    "address_type_b": b.get("address_type") or "",
                    "bbl_a": a.get("bbl") or "",
                    "bbl_b": b.get("bbl") or "",
                    "latitude_a": float(a["latitude"]),
                    "longitude_a": float(a["longitude"]),
                    "latitude_b": float(b["latitude"]),
                    "longitude_b": float(b["longitude"]),
                    "channel_a": a.get("channel") or a.get("open_data_channel_type") or "",
                    "channel_b": b.get("channel") or b.get("open_data_channel_type") or "",
                    "free_text_a": _text_field(pd.Series(a)),
                    "free_text_b": _text_field(pd.Series(b)),
                    "distance_m": round(float(dist), 1),
                    "time_gap_hours": round(float(hours), 3),
                    "distance_bin": dbin,
                    "time_bin": tbin,
                    "label": "",
                    "notes": "",
                    "label_source": "",
                    "is_synthetic": False,
                    "labeller_id": "",
                    "assignment_set": "",
                })
    return pd.DataFrame(rows)


def stratify_sample(pairs: pd.DataFrame, n: int = 300, seed: int = SEED) -> pd.DataFrame:
    """Draw up to n pairs. Distance is the main stratum so the set is not almost all far negatives."""
    if pairs.empty:
        return pairs
    rng = np.random.default_rng(seed)
    # Target mix: many close pairs (likely duplicates), some medium, some far (true negatives).
    share = {"very_close": 0.42, "medium": 0.33, "far": 0.25}
    picked = []
    remaining_budget = n
    bins = ["very_close", "medium", "far"]
    leftover = pairs.iloc[0:0]
    for i, dbin in enumerate(bins):
        part = pairs[pairs["distance_bin"] == dbin]
        budget = remaining_budget if i == len(bins) - 1 else min(remaining_budget, int(round(n * share[dbin])))
        if part.empty:
            continue
        # Inside a distance bin, mix time bins and closed-vs-open.
        take_idx = _balanced_take(part, budget, rng)
        picked.append(part.loc[take_idx])
        remaining_budget -= len(take_idx)
    chosen = pd.concat(picked, ignore_index=True) if picked else leftover
    if len(chosen) < n:
        extra = pairs[~pairs.set_index(["request_id_a", "request_id_b"]).index.isin(
            chosen.set_index(["request_id_a", "request_id_b"]).index)]
        need = n - len(chosen)
        if not extra.empty and need > 0:
            more = extra.iloc[rng.choice(len(extra), size=min(need, len(extra)), replace=False)]
            chosen = pd.concat([chosen, more], ignore_index=True)
    chosen = chosen.drop_duplicates(["request_id_a", "request_id_b"]).head(n).copy()
    chosen.insert(0, "pair_id", [f"PAIR-{i:04d}" for i in range(1, len(chosen) + 1)])
    return chosen.reset_index(drop=True)


def _balanced_take(part: pd.DataFrame, budget: int, rng: np.random.Generator) -> list:
    if budget <= 0 or part.empty:
        return []
    groups = list(part.groupby(["time_bin", "open_closed_a"], sort=False))
    if not groups:
        idx = part.index.to_numpy()
        return list(rng.choice(idx, size=min(budget, len(idx)), replace=False))
    per = max(1, budget // max(len(groups), 1))
    taken = []
    leftover_idx = []
    for _, g in groups:
        k = min(per, len(g))
        chosen = rng.choice(g.index.to_numpy(), size=k, replace=False)
        taken.extend(chosen.tolist())
        leftover_idx.extend([i for i in g.index.to_numpy() if i not in set(chosen)])
    if len(taken) < budget and leftover_idx:
        extra_n = min(budget - len(taken), len(leftover_idx))
        taken.extend(rng.choice(np.array(leftover_idx), size=extra_n, replace=False).tolist())
    return taken[:budget]


def assign_labellers(pairs: pd.DataFrame, overlap: int = 50,
                     labellers: tuple[str, ...] = ("L1", "L2", "L3", "L4"),
                     seed: int = SEED) -> pd.DataFrame:
    """About `overlap` pairs go to two people. The rest split across four labellers."""
    rng = np.random.default_rng(seed + 7)
    n = len(pairs)
    if n == 0:
        return pairs
    overlap_n = min(overlap, n) if n >= 80 else max(8, n // 5)
    overlap_n = min(overlap_n, n)
    order = rng.permutation(n)
    overlap_idx = set(order[:overlap_n].tolist())
    solo_idx = [i for i in order.tolist() if i not in overlap_idx]
    rows = []
    # Overlap: first two labellers double-code (can be Nabeel + one teammate).
    a_id, b_id = labellers[0], labellers[1] if len(labellers) > 1 else labellers[0]
    for i in sorted(overlap_idx):
        base = pairs.iloc[i].to_dict()
        for lid in (a_id, b_id):
            row = dict(base)
            row["assignment_set"] = "OVERLAP"
            row["labeller_id"] = lid
            rows.append(row)
    # Solo: remaining pairs round-robin across all four.
    for k, i in enumerate(solo_idx):
        row = pairs.iloc[i].to_dict()
        row["assignment_set"] = "SOLO"
        row["labeller_id"] = labellers[k % len(labellers)]
        rows.append(row)
    out = pd.DataFrame(rows)
    return out[[c for c in PAIR_COLUMNS if c in out.columns]]


def weak_label_row(row: pd.Series) -> str:
    """Provisional heuristic only. Never present as a real hand label."""
    dist = float(row["distance_m"])
    hours = float(row["time_gap_hours"])
    closed = str(row.get("open_closed_a", "OPEN")).upper() == "CLOSED"
    if closed and dist <= 150 and hours <= 24 * 30:
        return "RECURRENCE"
    if (not closed) and dist <= 150 and hours <= 48:
        return "DUPLICATE"
    if dist >= 400:
        return "NEW"
    return "UNSURE"


def attach_weak_labels(pairs: pd.DataFrame) -> pd.DataFrame:
    out = pairs.copy()
    out["label"] = out.apply(weak_label_row, axis=1)
    out["label_source"] = "weak_heuristic"
    out["labeller_id"] = "WEAK"
    out["assignment_set"] = "PROVISIONAL"
    return out


def write_kit(pairs: pd.DataFrame, assigned: pd.DataFrame, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    unique = pairs.copy()
    for col in ("labeller_id", "assignment_set", "label", "notes", "label_source"):
        if col not in unique.columns:
            unique[col] = ""
        else:
            unique[col] = unique[col].fillna("")
    unique["label_source"] = ""
    unique["is_synthetic"] = False
    paths = {}
    paths["pairs"] = out_dir / "c05_pairs_to_label.csv"
    unique[[c for c in PAIR_COLUMNS if c in unique.columns]].to_csv(paths["pairs"], index=False)
    paths["assignments"] = out_dir / "c05_label_assignments.csv"
    assigned.to_csv(paths["assignments"], index=False)
    for lid, g in assigned.groupby("labeller_id"):
        p = out_dir / f"c05_label_sheet_{lid}.csv"
        g.to_csv(p, index=False)
        paths[f"sheet_{lid}"] = p
    weak = attach_weak_labels(unique)
    paths["weak"] = out_dir / "c05_weak_eval_pairs.csv"
    weak.to_csv(paths["weak"], index=False)
    return paths


def cohens_kappa(y1: list[str], y2: list[str]) -> float:
    labels = sorted(set(y1) | set(y2))
    if not y1:
        return float("nan")
    idx = {lab: i for i, lab in enumerate(labels)}
    k = len(labels)
    mat = np.zeros((k, k), dtype=float)
    for a, b in zip(y1, y2):
        mat[idx[a], idx[b]] += 1
    n = mat.sum()
    if n == 0:
        return float("nan")
    po = np.trace(mat) / n
    pe = float(np.dot(mat.sum(axis=1) / n, mat.sum(axis=0) / n))
    if math.isclose(pe, 1.0):
        return 1.0
    return float((po - pe) / (1.0 - pe))


def kappa_from_sheets(path_a: Path, path_b: Path) -> tuple[float, pd.DataFrame, pd.DataFrame]:
    a = pd.read_csv(path_a)
    b = pd.read_csv(path_b)
    for frame in (a, b):
        if "label" not in frame.columns:
            raise ValueError("sheet is missing a label column")
        frame["label"] = frame["label"].astype(str).str.strip().str.upper()
        frame["pair_id"] = frame["pair_id"].astype(str)
    merged = a.merge(b, on="pair_id", suffixes=("_1", "_2"))
    both = merged[merged["label_1"].isin(LABEL_VALUES) & merged["label_2"].isin(LABEL_VALUES)]
    kappa = cohens_kappa(both["label_1"].tolist(), both["label_2"].tolist())
    disagree = both[both["label_1"] != both["label_2"]].copy()
    return kappa, both, disagree


def default_label_paths() -> list[Path]:
    import os
    env = os.environ.get("C05_LABELS_CSV")
    paths = []
    if env:
        paths.append(Path(env))
    paths.extend([
        APP / "data" / "processed" / "c05_label_kit" / "c05_labelled_pairs.csv",
        APP / "data" / "sample" / "c05_label_kit" / "c05_labelled_pairs.csv",
        APP / "data" / "sample" / "c05_label_kit" / "c05_weak_eval_pairs.csv",
    ])
    return paths


def _resolve_agreement(gold: pd.DataFrame) -> pd.DataFrame:
    if "pair_id" not in gold.columns or not gold["pair_id"].duplicated().any():
        return gold
    agreed = []
    for _, g in gold.groupby("pair_id"):
        if len(set(g["label"])) == 1:
            agreed.append(g.iloc[0])
    return pd.DataFrame(agreed)


def load_eval_pairs() -> tuple[pd.DataFrame, str]:
    """Drop-in labels: a completed hand CSV replaces the provisional weak set.

    Hand labels win when the file exists and has DUPLICATE/NEW/RECURRENCE values.
    UNSURE rows are dropped. Overlap disagreements are dropped until the team agrees.
    """
    fallback: tuple[pd.DataFrame, str] | None = None
    for path in default_label_paths():
        if not path.exists():
            continue
        df = pd.read_csv(path)
        if "label" not in df.columns:
            continue
        df["label"] = df["label"].astype(str).str.strip().str.upper()
        gold = _resolve_agreement(df[df["label"].isin(GOLD_VALUES)].copy())
        if gold.empty:
            continue
        source = str(gold["label_source"].iloc[0]) if "label_source" in gold.columns else ""
        is_hand = path.name == "c05_labelled_pairs.csv" and source != "weak_heuristic"
        kind = "hand" if is_hand else "provisional_weak"
        if kind == "hand":
            return gold.reset_index(drop=True), "hand"
        if fallback is None:
            fallback = (gold.reset_index(drop=True), "provisional_weak")
    if fallback is not None:
        return fallback
    raise FileNotFoundError("No C05 eval pairs found. Run: py 06_Workspace_Tools/c05_labelling.py sample")


def pair_to_run_inputs(row: pd.Series):
    """Turn one labelled pair into run(facts, candidates) inputs. B is the later report."""
    from components.c05_duplicate import DuplicateCandidate, RequestFacts

    def ts(val):
        if val is None or (isinstance(val, float) and pd.isna(val)) or val == "":
            return None
        stamp = pd.to_datetime(val, errors="coerce")
        if pd.isna(stamp):
            return None
        return stamp.to_pydatetime()

    def boro(val) -> str:
        text = str(val).strip().upper()
        return text or "BROOKLYN"

    def facts(side: str) -> RequestFacts:
        rid = str(row[f"request_id_{side}"])
        tail = rid.replace("NYC-", "").replace("SR-", "")[-6:]
        lat = row.get(f"latitude_{side}")
        lon = row.get(f"longitude_{side}")
        bbl = str(row.get(f"bbl_{side}") or "").strip()
        addr = str(row.get(f"address_{side}") or "").strip()
        atype = str(row.get(f"address_type_{side}") or "").strip()
        text = str(row.get(f"free_text_{side}") or "").strip()
        return RequestFacts(
            request_id=rid,
            created_at=ts(row[f"created_at_{side}"]),
            borough=boro(row.get("borough", "BROOKLYN")),
            request_type_code=str(row["request_type_code"]),
            complaint_type=str(row.get("complaint_type") or "Unknown"),
            complaint_family=str(row.get("complaint_family") or "UNKNOWN"),
            incident_address=addr or None,
            address_type=atype or None,
            bbl=bbl or None,
            latitude=None if pd.isna(lat) else float(lat),
            longitude=None if pd.isna(lon) else float(lon),
            channel=str(row.get(f"channel_{side}") or "UNKNOWN"),
            citizen_id=f"CIT-{side.upper()}{tail}",
            free_text=text or None,
        )

    earlier = facts("a")
    later = facts("b")
    is_open = str(row.get("open_closed_a", "OPEN")).upper() == "OPEN"
    candidate = DuplicateCandidate(
        facts=earlier,
        group_id="DG-" + earlier.request_id.split("-", 1)[-1],
        is_open=is_open,
        closed_at=None if is_open else ts(row.get("closed_at_a")),
        reporters=frozenset({earlier.citizen_id} if earlier.citizen_id else set()),
        channels=frozenset({earlier.channel}),
    )
    return later, [candidate]


# ============================================================================
# Command: sample
# ============================================================================

def main_sample() -> int:
    p = argparse.ArgumentParser(description="Sample C05 pairs for hand labelling")
    p.add_argument("--source", choices=["auto", "sample", "processed"], default="auto")
    p.add_argument("--n", type=int, default=300, help="How many unique pairs to sample")
    p.add_argument("--overlap", type=int, default=50, help="Pairs labelled by two people")
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--labellers", default="L1,L2,L3,L4",
                   help="Four labeller ids. L1=Nabeel, L2=Sankalp, L3=Mohammad, L4=Khalid")
    p.add_argument("--out-dir", default="")
    args = p.parse_args()

    df, origin = load_requests(args.source)
    print(f"Loaded {len(df):,} requests from {origin}")
    pool = enumerate_candidate_pairs(df)
    print(f"Candidate pool: {len(pool):,} pairs (same type and sample_window_id, 0-1500 m, <14 days)")
    if pool.empty:
        print("No pairs found. Check sample_window_id and coordinates.")
        return 1
    print(pool["distance_bin"].value_counts().to_string())
    sampled = stratify_sample(pool, n=args.n, seed=args.seed)
    print(f"Sampled {len(sampled)} unique pairs (seed {args.seed})")
    labellers = tuple(s.strip() for s in args.labellers.split(",") if s.strip())
    assigned = assign_labellers(sampled, overlap=args.overlap, labellers=labellers, seed=args.seed)
    out_dir = Path(args.out_dir) if args.out_dir else (
        APP / "data" / ("processed" if origin == "processed" else "sample") / "c05_label_kit"
    )
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    paths = write_kit(sampled, assigned, out_dir)
    overlap_pairs = assigned[assigned["assignment_set"] == "OVERLAP"]["pair_id"].nunique()
    print(f"Wrote kit to {out_dir}")
    print(f"  unique pairs: {len(sampled)}")
    print(f"  overlap pairs (two labellers): {overlap_pairs}")
    print(f"  assignment rows: {len(assigned)}")
    for k, path in paths.items():
        print(f"  {k}: {path.relative_to(ROOT)}")
    print("Fill label with DUPLICATE / NEW / RECURRENCE / UNSURE.")
    print("Do not add a model score column.")
    print("When done, save one combined file as c05_labelled_pairs.csv in this folder.")
    print("The benchmark reads that file and drops the weak labels.")
    if origin == "sample":
        print("NOTE: this run used the small committed sample, not the full 49,558 rows.")
        print("After you unzip 05_Data into 01_Application/data/processed, run:")
        print("  py 06_Workspace_Tools/c05_labelling.py sample --source processed --n 300")
    return 0


# ============================================================================
# Command: kappa
# ============================================================================

def main_kappa() -> int:
    p = argparse.ArgumentParser(description="C05 overlap agreement (Cohen's kappa)")
    p.add_argument("--a", required=True, help="First labeller CSV")
    p.add_argument("--b", required=True, help="Second labeller CSV")
    p.add_argument("--out", default="", help="Where to write disagreements CSV")
    args = p.parse_args()
    path_a, path_b = Path(args.a), Path(args.b)
    if not path_a.is_absolute():
        path_a = ROOT / path_a
    if not path_b.is_absolute():
        path_b = ROOT / path_b
    kappa, both, disagree = kappa_from_sheets(path_a, path_b)
    n = len(both)
    n_dis = len(disagree)
    print(f"Overlap rows with both labels filled: {n}")
    print(f"Cohen's kappa: {kappa:.3f}" if n else "Cohen's kappa: n/a (no paired labels yet)")
    print(f"Disagreements: {n_dis}")
    if n:
        print("Label pairs:")
        print(both.groupby(["label_1", "label_2"]).size().to_string())
    out = Path(args.out) if args.out else path_a.parent / "c05_overlap_disagreements.csv"
    if not out.is_absolute():
        out = ROOT / out
    keep = [c for c in disagree.columns if c in {
        "pair_id", "request_id_a", "request_id_b", "distance_m", "time_gap_hours",
        "address_a", "address_b", "open_closed_a", "label_1", "label_2",
        "notes_1", "notes_2", "labeller_id_1", "labeller_id_2",
    }]
    if disagree.empty:
        disagree.to_csv(out, index=False)
    else:
        disagree[keep].to_csv(out, index=False)
    print(f"Wrote {out}")
    print("Adjudicate disagreements, then copy agreed rows into c05_labelled_pairs.csv")
    return 0


# ============================================================================
# Command: candidate-sheet
# ============================================================================

SET_LABEL_VALUES = ("SAME", "DIFFERENT", "RECURRENCE", "UNSURE")


COLUMNS = ["set_id", "pair_id", "b_request_id", "candidate_request_id", "is_labelled_a", "request_type_code",
           "b_created_at", "candidate_created_at", "hours_before_b", "distance_m", "same_bbl", "address_similarity",
           "candidate_open_at_b", "b_address", "candidate_address", "b_free_text", "candidate_free_text",
           "pair_gold_label", "label_same_incident", "labeller_id", "notes"]


def _txt(v):
    return None if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v)


def build_candidate_sheet(pairs: pd.DataFrame, requests: pd.DataFrame, *, hours: float = 48.0,
                          radius_m: float = 500.0, max_per_set: int | None = 60) -> pd.DataFrame:
    req = requests.copy()
    req["created_at"] = pd.to_datetime(req["created_at"])
    req["closed_date"] = pd.to_datetime(req.get("closed_date"))
    by_type = {k: g for k, g in req.groupby("request_type_code")}
    rows = []
    for _, p in pairs.iterrows():
        tb = pd.Timestamp(p["created_at_b"])
        g = by_type.get(p["request_type_code"], req.iloc[:0])
        g = g[(g.created_at < tb) & (g.created_at >= tb - pd.Timedelta(hours=hours))
              & (g.request_id != p["request_id_b"]) & g.latitude.notna()]
        if pd.notna(p.get("latitude_b")) and len(g):
            d = np.array([haversine_m(float(p.latitude_b), float(p.longitude_b), a, b)
                          for a, b in zip(g.latitude.values, g.longitude.values)])
            g = g.assign(distance_m=d)[d <= radius_m]
        else:
            g = g.assign(distance_m=np.nan)
        g = g.sort_values("created_at", ascending=False)
        is_a = g.request_id == p["request_id_a"]
        if max_per_set is not None:
            g = pd.concat([g[is_a], g[~is_a].head(max_per_set)])
        if not (g.request_id == p["request_id_a"]).any():
            g = pd.concat([g, pd.DataFrame([{
                "request_id": p["request_id_a"], "created_at": pd.Timestamp(p["created_at_a"]),
                "incident_address": p.get("address_a"), "bbl": p.get("bbl_a"), "free_text": p.get("free_text_a"),
                "closed_date": pd.to_datetime(p.get("closed_at_a")),
                "distance_m": p.get("distance_m")}])])
        bb = usable_bbl(_txt(p.get("bbl_b")))
        for _, c in g.iterrows():
            closed = c.get("closed_date")
            rows.append({
                "set_id": f"SET-{p['pair_id']}", "pair_id": p["pair_id"], "b_request_id": p["request_id_b"],
                "candidate_request_id": c["request_id"], "is_labelled_a": c["request_id"] == p["request_id_a"],
                "request_type_code": p["request_type_code"], "b_created_at": tb, "candidate_created_at": c["created_at"],
                "hours_before_b": round((tb - c["created_at"]).total_seconds() / 3600, 2),
                "distance_m": None if pd.isna(c.get("distance_m")) else round(float(c["distance_m"]), 1),
                "same_bbl": bool(bb and usable_bbl(_txt(c.get("bbl"))) == bb),
                "address_similarity": round(address_similarity(_txt(p.get("address_b")), _txt(c.get("incident_address"))), 3),
                "candidate_open_at_b": bool(pd.isna(closed) or closed > tb),
                "b_address": p.get("address_b"), "candidate_address": c.get("incident_address"),
                "b_free_text": p.get("free_text_b"), "candidate_free_text": c.get("free_text", c.get("descriptor")),
                "pair_gold_label": p.get("label"), "label_same_incident": "", "labeller_id": "", "notes": "",
            })
    return pd.DataFrame(rows, columns=COLUMNS)


def main_candidate_sheet() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--max-per-set", type=int, default=60); a = ap.parse_args()
    kit = APP / "data" / "processed" / "c05_label_kit"
    pairs = pd.read_csv(kit / "c05_labelled_pairs.csv")
    pairs = pairs[pairs.label.astype(str).str.upper() != "UNSURE"]
    req = pd.read_parquet(APP / "data" / "processed" / "requests_real.parquet")
    if "free_text" not in req.columns:
        req["free_text"] = req.get("descriptor")
    sheet = build_candidate_sheet(pairs, req, max_per_set=a.max_per_set)
    out = kit / "candidate_sets"; out.mkdir(parents=True, exist_ok=True)
    sheet.to_csv(out / "c05_candidate_set_sheet.csv", index=False)
    (out / "README.md").write_text(
        "# C05 candidate-set labelling sheet\n\n"
        "One row per (report B, candidate). Fill `label_same_incident` with SAME, DIFFERENT, RECURRENCE, or UNSURE.\n"
        "SAME: the candidate and B describe one incident. RECURRENCE: same place and type, the candidate was closed.\n"
        "Do not change other columns. Real ids. Not in git. Built by 06_Workspace_Tools/c05_labelling.py candidate-sheet.\n", encoding="utf-8")
    print(f"sets {sheet.set_id.nunique()} rows {len(sheet)} -> {out}")
    return 0


COMMANDS = {"sample": main_sample, "kappa": main_kappa, "candidate-sheet": main_candidate_sheet}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        raise SystemExit("usage: " + Path(__file__).name + " {" + ",".join(COMMANDS) + "} [options]")
    cmd = sys.argv.pop(1)
    sys.argv[0] = f"{sys.argv[0]} {cmd}"
    raise SystemExit(COMMANDS[cmd]())

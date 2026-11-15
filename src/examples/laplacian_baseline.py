"""T02 baseline: a single global Laplacian threshold calibrated on self-labeled dev.

Run with:
    python -m src.main --track t02 --pipeline src.examples.laplacian_baseline \
        --output out/baseline_laplacian --offline --seed 42
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..common.context import RunContext
from ..common.decision import Decision, write_decision
from ..common.image_ops import laplacian_variance, load_rgb
from ..common.review import make_review_queue, write_review_queue
from ..common.io import write_json
from ..metrics.tracks import get_track


def run(ctx: RunContext) -> None:
    if ctx.track != "t02":
        raise NotImplementedError("Laplacian baseline supports T02 only")

    images = pd.read_csv(ctx.input_dir / "images.csv")
    labels_path = ctx.config.get("dev_labels", "reports/t02_dev_labels_eval.csv")
    labels = pd.read_csv(labels_path)
    required = {"image_id", "is_defect", "split"}
    if missing := required - set(labels.columns):
        raise ValueError(f"dev labels missing columns: {sorted(missing)}")
    if labels["image_id"].duplicated().any():
        raise ValueError("dev labels contain duplicate image_id values")

    records = []
    for row in images.itertuples(index=False):
        sharpness = laplacian_variance(load_rgb(ctx.input_dir / row.path))
        records.append({
            "image_id": str(row.image_id), "path": str(row.path),
            "light": str(row.light), "split": str(row.split), "sharpness": sharpness,
        })
    scored = pd.DataFrame(records)
    dev = scored[scored["split"] == "dev"].merge(
        labels[labels["split"] == "dev"][["image_id", "is_defect"]],
        on="image_id", how="inner", validate="one_to_one",
    )
    if len(dev) != int((scored["split"] == "dev").sum()) or dev.empty:
        raise ValueError("self-labeled dev rows must cover every dev image exactly once")

    calibration = dev.sort_values("sharpness", kind="mergesort")
    sharpness = calibration["sharpness"].to_numpy(dtype=float)
    truth = calibration["is_defect"].to_numpy(dtype=int) == 1
    candidates = np.unique((sharpness[:-1] + sharpness[1:]) / 2) if len(sharpness) > 1 else sharpness
    candidates = np.unique(np.concatenate(([sharpness.min() - 1e-9], candidates, [sharpness.max() + 1e-9])))
    best = None
    for threshold in candidates:
        predicted = sharpness < threshold
        tp = int((predicted & truth).sum())
        fp = int((predicted & ~truth).sum())
        fn = int((~predicted & truth).sum())
        f1 = 2 * tp / max(2 * tp + fp + fn, 1)
        recall = tp / max(int(truth.sum()), 1)
        candidate = (f1, recall, -float(threshold), float(threshold))
        if best is None or candidate[:3] > best[:3]:
            best = candidate
    threshold = best[3]

    scored["score"] = (threshold - scored["sharpness"]).round(6)
    scored["pred_action"] = np.where(scored["sharpness"] < threshold, "RECAPTURE", "KEEP")
    scored["reason"] = np.where(scored["pred_action"] == "RECAPTURE", "mo", "")
    budget = int(ctx.param("budget", 20))
    queue = make_review_queue(
        scored, "image_id", "score", budget, reason_col="reason", action_col="pred_action",
        slice_col="light", evidence_col="path",
    )
    write_review_queue(queue, ctx.out("review_queue.csv"))
    spec = get_track(ctx.track)
    scored[spec.pred_columns + [c for c in scored.columns if c not in spec.pred_columns]].to_csv(
        ctx.out(spec.pred_file), index=False,
    )
    ctx.stats.update({"n_items": len(scored), "dev_items": len(dev), "laplacian_threshold": threshold})
    write_json(ctx.out("threshold.json"), {
        "feature": "global Laplacian variance",
        "decision_rule": "RECAPTURE if sharpness < threshold",
        "threshold": threshold,
        "calibration": "max F1 on reports/t02_dev_labels_eval.csv (self-labeled dev only)",
        "dev_n": len(dev),
        "dev_defects": int(truth.sum()),
        "seed": ctx.seed,
    })
    write_decision(
        Decision(
            status="HOLD", reason_code="BASELINE_ONLY",
            reason="Laplacian baseline is for comparison only; do not release from it.",
            owner=ctx.config.get("owner", "baseline"), scope=ctx.track,
            evidence=[str(ctx.out(spec.pred_file)), str(ctx.out("threshold.json"))],
        ),
        ctx.out("decision.json"), ctx.out("decision_log.csv"),
    )

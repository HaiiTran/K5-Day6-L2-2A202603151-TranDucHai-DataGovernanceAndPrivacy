"""YOUR PoC PIPELINE — this is the file your group owns.

Lab contract = 5 automated steps, run by `python -m src.main ...`:
    01 load      input + provenance check (manifest.csv)
    02 process   your algorithm / model (the research part)
    03 rank      prioritise for a fixed review budget B
    04 review    human-in-the-loop: queue -> reviewer decisions -> overrides + audit log
    05 decide    GO / HOLD / REWORK (or track equivalent) with reason_code + owner, write outputs

Required output: out/<pred_file> with the columns listed for your track in
docs/tracks/TXX.md (also in src/metrics/tracks.py::TRACKS). Then evaluate with
    python -m src.evaluate --track tXX --pred out/<pred_file>

Rules: never read fixtures/*/gt/ here (the GT guard will stop you); seed everything via ctx.rng;
no network calls; fail loudly on missing models instead of downloading.
See src/examples/random_baseline.py for a minimal end-to-end wiring example.
"""
from __future__ import annotations

import pandas as pd

from .common.context import RunContext
from .common.decision import Decision, write_decision
from .common.manifest import verify_manifest
from .common.review import load_review_decisions, make_review_queue, write_review_queue
from .metrics.tracks import get_track


# ---------------------------------------------------------------- 01 load
def load(ctx: RunContext) -> dict:
    manifest = ctx.input_dir / "manifest.csv"
    problems = verify_manifest(manifest) if manifest.exists() else [{"problem": "no_manifest"}]
    if problems:
        ctx.audit.log(actor="pipeline", action="provenance_problems", item_id=str(manifest), after=problems[:20])
        if ctx.config.get("strict_provenance", True):
            raise ValueError(f"provenance check failed ({len(problems)} problems), first: {problems[:3]}")
    data = {"manifest": pd.read_csv(manifest) if manifest.exists() else None}
    # TODO: load the tables/images your track needs, e.g.
    if ctx.track == "t02":
        data["images"] = pd.read_csv(ctx.input_dir / "images.csv")
        required = {"image_id", "path", "camera_id", "light", "split"}
        missing = required - set(data["images"].columns)
        if missing:
            raise ValueError(f"images.csv missing required columns: {sorted(missing)}")
        if data["images"]["image_id"].duplicated().any():
            raise ValueError("images.csv contains duplicate image_id values")
    return data


# ---------------------------------------------------------------- 02 process
def process(ctx: RunContext, data: dict) -> pd.DataFrame:
    spec = get_track(ctx.track)
    if ctx.track != "t02":
        raise NotImplementedError(f"Implement process() for {spec.track} ({spec.title})")

    # TODO: implement the T02 quality score; high score means review first.
    import numpy as np
    from .common.image_ops import brightness_stats, laplacian_variance, load_rgb, to_gray

    visible_luma_threshold = 40.0
    tile_rows, tile_columns = 4, 6
    action_score_threshold = 0.5

    records = []
    for row in data["images"].itertuples(index=False):
        image = load_rgb(ctx.input_dir / row.path)
        brightness = brightness_stats(image)
        gray = to_gray(image)
        height, width = gray.shape
        y_edges = np.linspace(0, height, tile_rows + 1, dtype=int)
        x_edges = np.linspace(0, width, tile_columns + 1, dtype=int)
        tile_means = [
            float(gray[y_edges[row_index]:y_edges[row_index + 1],
                       x_edges[column_index]:x_edges[column_index + 1]].mean())
            for row_index in range(tile_rows)
            for column_index in range(tile_columns)
        ]
        records.append({
            "image_id": str(row.image_id), "path": str(row.path),
            "camera_id": str(row.camera_id), "light": str(row.light), "split": str(row.split),
            "sharpness": laplacian_variance(image),
            "dark_fraction": brightness["frac_dark"],
            "mean_luminance": brightness["mean"],
            "p95_luminance": brightness["p95"],
            "visible_tile_fraction": float(np.mean(np.asarray(tile_means) >= visible_luma_threshold)),
            "saturated_fraction": brightness["frac_saturated"],
        })
    scored = pd.DataFrame(records)
    dev = scored[scored["split"] == str(ctx.param("dev_split", "dev"))]
    if dev.empty:
        raise ValueError("T02 requires dev images to establish score reference statistics")

    raw_anomalies = pd.DataFrame(index=scored.index)
    lower_is_suspicious = {"sharpness", "mean_luminance", "p95_luminance", "visible_tile_fraction"}
    scale_floors = {"sharpness": 20.0, "dark_fraction": 0.04, "mean_luminance": 8.0,
                    "p95_luminance": 12.0, "visible_tile_fraction": 0.08,
                    "saturated_fraction": 0.01}
    for feature in scale_floors:
        raw_anomalies[feature] = 0.0
        for idx, row in scored.iterrows():
            reference = dev[dev["light"] == row["light"]]
            if feature == "sharpness":
                by_camera = reference[reference["camera_id"] == row["camera_id"]]
                if len(by_camera) >= 8:
                    reference = by_camera
            if reference.empty:
                reference = dev
            values = reference[feature]
            median = float(values.median())
            mad_scale = float((values - median).abs().median()) * 1.4826
            scale = max(mad_scale, float((values.quantile(.75) - values.quantile(.25)) / 1.349),
                        scale_floors[feature])
            delta = median - row[feature] if feature in lower_is_suspicious else row[feature] - median
            raw_anomalies.loc[idx, feature] = min(8.0, max(0.0, delta / scale))

    # Require dark pixels plus independent evidence that useful image content is dim.
    anomalies = pd.DataFrame(index=scored.index)
    anomalies["sharpness"] = raw_anomalies["sharpness"]
    anomalies["saturated_fraction"] = raw_anomalies["saturated_fraction"]
    for idx, row in scored.iterrows():
        dark_pixels = raw_anomalies.loc[idx, "dark_fraction"]
        light_loss = max(raw_anomalies.loc[idx, "mean_luminance"],
                         raw_anomalies.loc[idx, "p95_luminance"],
                         raw_anomalies.loc[idx, "visible_tile_fraction"])
        # Night scenes need both a dim upper tail and low spatial visibility to flag underexposure.
        if row["light"] == "night":
            light_loss = min(raw_anomalies.loc[idx, "p95_luminance"],
                             raw_anomalies.loc[idx, "visible_tile_fraction"])
        anomalies.loc[idx, "darkness"] = min(dark_pixels, light_loss)

    # Treat darkness as a usability scale: usable-but-dim is reviewable (level 1),
    # while near-total loss of scene information is the only automatic recapture case (level 2).
    dark_fraction_min = 0.65
    dark_mean_max = 30.0
    dark_p95_max = 70.0
    dark_visible_tiles_max = 0.25
    blackout_fraction_min = 0.98
    blackout_mean_max = 8.0
    blackout_p95_max = 20.0
    blackout_visible_tiles_max = 0.02
    dark_candidate = (
        scored["dark_fraction"].ge(dark_fraction_min)
        & scored["mean_luminance"].le(dark_mean_max)
        & scored["p95_luminance"].le(dark_p95_max)
        & scored["visible_tile_fraction"].le(dark_visible_tiles_max)
    )
    blackout = (
        scored["dark_fraction"].ge(blackout_fraction_min)
        & scored["mean_luminance"].le(blackout_mean_max)
        & scored["p95_luminance"].le(blackout_p95_max)
        & scored["visible_tile_fraction"].le(blackout_visible_tiles_max)
    )
    # Low light alone is normal in some scenes; only the combined visibility gate counts as dark.
    anomalies.loc[~dark_candidate, "darkness"] = 0.0
    anomalies.loc[dark_candidate, "darkness"] = np.maximum(
        anomalies.loc[dark_candidate, "darkness"], 2.6
    )
    anomalies.loc[blackout, "darkness"] = 8.0

    names = {"sharpness": ("blur", "mo"),
             "darkness": ("dark", "toi mat chi tiet"),
             "saturated_fraction": ("glare", "loa")}
    dominant = anomalies.idxmax(axis=1)
    scored["score"] = (anomalies.max(axis=1) + .15 * anomalies.mean(axis=1)).round(4)
    scored["pred_defect_type"] = dominant.map({key: value[0] for key, value in names.items()})
    scored["reason"] = dominant.map({key: value[1] for key, value in names.items()})
    scored.loc[scored["score"] == 0, "pred_defect_type"] = "none"
    dark_only = dominant.eq("darkness")
    scored["pred_action"] = "KEEP"
    scored.loc[scored["score"].ge(action_score_threshold), "pred_action"] = "RECAPTURE"
    scored.loc[dark_only, "pred_action"] = "KEEP"
    scored.loc[blackout, "pred_action"] = "RECAPTURE"
    return scored[["image_id", "score", "pred_defect_type", "pred_action", "reason", "path", "light", "split"]]


# ---------------------------------------------------------------- 03 rank
def rank(ctx: RunContext, scored: pd.DataFrame) -> pd.DataFrame:
    """Default: top-B by `score`. Adapt id/score/reason columns to your track."""
    budget = int(ctx.param("budget", 20))
    if "score" not in scored.columns:
        raise ValueError("rank(): process() output needs a numeric `score` column (higher = review first), "
                         "or adapt rank() to your track")
    q = make_review_queue(scored, id_col=scored.columns[0], score_col="score", budget=budget,
                          reason_col="reason" if "reason" in scored.columns else None)
    write_review_queue(q, ctx.out("review_queue.csv"))
    return q


# ---------------------------------------------------------------- 04 review
def review(ctx: RunContext, scored: pd.DataFrame, queue: pd.DataFrame) -> pd.DataFrame:
    """If a reviewed queue exists (out/review_queue_reviewed.csv), apply it. Otherwise pass through.

    TODO: decide what an `edit` / `reject` means for your track and apply it
    (e.g. via common.review.apply_overrides), logging every change to ctx.audit.
    """
    reviewed_path = ctx.output_dir / "review_queue_reviewed.csv"
    if reviewed_path.exists():
        # TODO: severity 0/1 => KEEP; severity 2 => RECAPTURE (use edit + override_value if needed).
        from .common.review import apply_overrides

        ctx.stats["reviewed_count"] = 0
        ctx.stats["pending_review_count"] = len(queue)
        ctx.stats["escalated_count"] = 0
        ctx.stats["reviewed_recapture_count"] = 0
        reviewed = load_review_decisions(reviewed_path)
        queue_ids = set(queue["item_id"].astype(str))
        if set(reviewed["item_id"].astype(str)) - queue_ids:
            raise ValueError("review file contains items outside the current review queue")
        if reviewed["item_id"].duplicated().any():
            raise ValueError("review file contains duplicate decisions")
        edits = reviewed.copy()
        rejected = edits["reviewer_decision"].eq("reject") & edits["override_value"].eq("")
        edits.loc[rejected, "override_value"] = "KEEP"
        explicit = edits["override_value"].ne("")
        edits.loc[explicit, "override_value"] = edits.loc[explicit, "override_value"].str.upper()
        if not edits.loc[explicit, "override_value"].isin({"KEEP", "RECAPTURE"}).all():
            raise ValueError("override_value must be KEEP or RECAPTURE")
        if (edits["reviewer_decision"].eq("edit") & ~edits["override_value"].isin({"KEEP", "RECAPTURE"})).any():
            raise ValueError("reviewer_decision=edit requires override_value KEEP or RECAPTURE")
        final = apply_overrides(scored, edits[explicit], "image_id", "pred_action", ctx.audit)
        for row in reviewed.itertuples(index=False):
            if row.reviewer_decision in {"approve", "escalate"}:
                ctx.audit.log(actor=row.reviewer, action=f"review:{row.reviewer_decision}",
                              item_id=row.item_id, reason=row.review_note)
        meaningful = reviewed[reviewed["reviewer_decision"] != "skip"]
        done_ids = set(meaningful["item_id"].astype(str))
        ctx.stats["reviewed_count"] = len(done_ids)
        ctx.stats["pending_review_count"] = max(0, len(queue) - len(done_ids))
        ctx.stats["escalated_count"] = int(reviewed["reviewer_decision"].eq("escalate").sum())
        ctx.stats["reviewed_recapture_count"] = int(
            final[final["image_id"].isin(done_ids)]["pred_action"].eq("RECAPTURE").sum()
        )
        return final
    return scored


# ---------------------------------------------------------------- 05 decide
def decide(ctx: RunContext, final: pd.DataFrame) -> Decision:
    # TODO: score only prioritizes review; reviewer maps severity 0/1 -> KEEP, severity 2 -> RECAPTURE.
    reviewed = int(ctx.stats.get("reviewed_count", 0))
    pending = int(ctx.stats.get("pending_review_count", len(pd.read_csv(ctx.output_dir / "review_queue.csv"))))
    escalated = int(ctx.stats.get("escalated_count", 0))
    recapture = int(ctx.stats.get("reviewed_recapture_count", 0))
    owner = ctx.config.get("owner", "TODO-owner")
    evidence = [str(ctx.output_dir / get_track(ctx.track).pred_file), str(ctx.output_dir / "review_queue.csv")]
    if reviewed == 0 or pending or escalated:
        return Decision(status="HOLD", reason_code="HUMAN_REVIEW_PENDING",
                        reason=f"Review {pending} queued images; {escalated} escalated.",
                        owner=owner, scope=ctx.track, evidence=evidence,
                        metrics={"reviewed": reviewed, "pending": pending, "escalated": escalated})
    if recapture:
        return Decision(status="REWORK", reason_code="SEVERITY_2_RECAPTURE_REQUIRED",
                        reason=f"Review confirmed {recapture} unusable image(s) (severity 2) for recapture.",
                        owner=owner, scope=ctx.track, evidence=evidence,
                        metrics={"reviewed": reviewed, "recapture": recapture})
    return Decision(status="GO", reason_code="NO_SEVERITY_2_REMAINS",
                    reason="All queued images were reviewed; severity 0/1 images are usable and none require recapture.",
                    owner=owner, scope=ctx.track, evidence=evidence,
                    metrics={"reviewed": reviewed, "pending": pending, "recapture": recapture})


def run(ctx: RunContext) -> None:
    spec = get_track(ctx.track)
    data = load(ctx)
    scored = process(ctx, data)
    ctx.stats["n_items"] = len(scored)
    queue = rank(ctx, scored)
    final = review(ctx, scored, queue)
    final[spec.pred_columns + [c for c in final.columns if c not in spec.pred_columns]].to_csv(ctx.out(spec.pred_file), index=False)
    write_decision(decide(ctx, final), ctx.out("decision.json"), ctx.out("decision_log.csv"))

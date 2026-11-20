"""Fixed stage-3 platform model comparison; does not launch a pod or a replay."""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from random import Random
from statistics import mean, median, stdev


PRIOR_SHA = "0dd4b85c7a156b646dbce0c3fe16ed486aac1f7811f4c6ce9fda195992afd4aa"
CONTRACT_SHA = "b275f75ef14b8b9af0586a356e83d8f31677de082aeea4d640bf4db22228a5cb"
BOOT_SEED = 20261110031
BOOT_DRAWS = 10000
CALIBRATION_BLOCKS = 4
CONFIRM_BLOCKS = 8
CELLS = {(m, c, p) for m in "AB" for c in ("contended", "spare") for p in ("early", "late")}
OFFSETS = {"calibration": {"early": 1.5, "late": 6.0},
           "evaluation": {"early": 1.5, "late": 6.0},
           "confirmation": {"early": 2.5, "late": 7.0}}
ALIGN = .002


def need(condition, explanation):
    if not condition:
        raise ValueError(explanation)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, obj):
    with Path(path).open("x", encoding="utf8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def valid_number(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class Trial:
    trial_id: str
    block: int
    mode: str
    competition: str
    phase: str
    offset: float
    # Times are seconds relative to the SAME X retirement intent anchor.
    # Arrival is actual HTTP ingress to the controller, not target/launch time.
    ingress: float
    submit: float
    bind: float
    ready_lower: float
    ready_upper: float
    y_uid: str
    first_memory_failure: float | None
    trace_complete: bool
    # X cache removal interval. An upper alone can prove a zero for a control.
    cache_lower: float | None
    cache_upper: float | None
    free_with_x_bytes: int
    x_memory_bytes: int
    y_memory_bytes: int
    x_only_release_verified: bool
    both_http_complete: bool

    @property
    def headroom_deficit(self):
        return self.free_with_x_bytes < self.y_memory_bytes

    @property
    def memory_event(self):
        return self.first_memory_failure is not None

    @property
    def arrival_to_bind(self):
        return self.bind - self.ingress

    def assert_valid(self, phase):
        need((self.mode, self.competition, self.phase) in CELLS and
             self.offset == OFFSETS[phase][self.phase], ("wrong fixed scenario", self.trial_id))
        need(type(self.block) is int and self.block >= 0 and self.trial_id and self.y_uid,
             "missing block / request / UID identity")
        need(all(valid_number(v) for v in (self.ingress, self.submit, self.bind, self.ready_lower,
                                           self.ready_upper)), ("clock missing", self.trial_id))
        need(self.ingress <= self.submit <= self.bind + ALIGN and self.bind <= self.ready_upper + ALIGN
             and self.ready_lower <= self.ready_upper, ("clock ordering", self.trial_id))
        need(self.trace_complete is True and self.both_http_complete is True and
             self.x_only_release_verified is True, ("bind/HTTP/capacity closure not established", self.trial_id))
        need(all(type(v) is int for v in (self.free_with_x_bytes, self.x_memory_bytes, self.y_memory_bytes))
             and self.free_with_x_bytes >= 0 and self.x_memory_bytes > 0 and self.y_memory_bytes > 0
             and self.free_with_x_bytes + self.x_memory_bytes >= self.y_memory_bytes
             and self.headroom_deficit == (self.competition == "contended"),
             ("resource geometry not as prescribed", self.trial_id))
        if self.memory_event:
            need(valid_number(self.first_memory_failure) and self.headroom_deficit
                 and self.submit - ALIGN <= self.first_memory_failure <= self.bind + ALIGN,
                 ("memory failure not in matching submit→bind path", self.trial_id))
        if self.cache_lower is not None:
            need(valid_number(self.cache_lower) and self.cache_lower >= 0 and self.cache_upper is not None
                 and self.cache_lower <= self.cache_upper, ("bad release interval", self.trial_id))
        if self.cache_upper is not None:
            need(valid_number(self.cache_upper) and self.cache_upper >= 0, ("bad release upper", self.trial_id))


def pack_data(path, phase, count):
    data = read(path)
    need(data["schema"] == "RETURN_HTTP_NORMALIZED_V1" and data["phase"] == phase and
         data["trace_proof"] == "SAME_UID_API_BIND_AND_SCHEDULER_LOG",
         "wrong or unproven acquisition")
    need(len(data["raw_status_sha256"]) == len(data["raw_plan_sha256"]) == 64 and data["run_id"],
         "missing acquisition lineage")
    need(len(data["normalizer_source_sha256"]) == 64, "normalized source lineage absent")
    trials = [Trial(**t) for t in data["trials"]]
    need(len(trials) == 8 * count and len({r.trial_id for r in trials}) == len(trials)
         and len({r.y_uid for r in trials}) == len(trials), "complete, unique trial denominator required")
    grouped = defaultdict(dict)
    for t in trials:
        t.assert_valid(phase)
        key = (t.mode, t.competition, t.phase)
        need(key not in grouped[t.block], "duplicated block × arm")
        grouped[t.block][key] = t
    need(len(grouped) == count and all(set(g) == CELLS for g in grouped.values()), "incomplete factorial block")
    return data, sorted(trials, key=lambda r: (r.block, r.mode, r.competition, r.phase)), grouped


def did_by_block(groups, phase):
    return [
        (g["B", "contended", phase].arrival_to_bind - g["A", "contended", phase].arrival_to_bind)
        - (g["B", "spare", phase].arrival_to_bind - g["A", "spare", phase].arrival_to_bind)
        for _, g in sorted(groups.items())
    ]


def model_delay(t, delay_by_mode):
    """Inputs are only predeclared resource geometry, actual submit, and intent=0.

    In both arms X's current occupancy precedes intent even if assumed return is
    instantaneous. Held-out X cache times/Y bind/Ready/fail are not accessed.
    """
    return max(0.0, delay_by_mode[t.mode] - t.submit) if t.headroom_deficit else 0.0


def timing_predictions(t, terms, delays):
    block = model_delay(t, delays)
    bind_after_ingress = t.submit - t.ingress + block + terms["ordinary_submit_to_bind_s"]
    return dict(block_s=block, memory_event=block > 0.0, arrival_to_bind_s=bind_after_ingress,
                arrival_to_ready_s=bind_after_ingress + terms["ordinary_bind_to_ready_host_upper_s"])


def observed_block(t):
    if t.memory_event:
        if t.cache_lower is None or t.cache_upper is None:
            return dict(kind="POSITIVE_UNRESOLVED_CACHE")
        lo = max(0.0, t.cache_lower - t.first_memory_failure)
        hi = max(0.0, t.cache_upper - t.submit)
        need(lo <= hi and hi > 0 and t.cache_upper <= t.bind + ALIGN,
             ("positive cache/failure observation invalid", t.trial_id))
        return dict(kind="POSITIVE_BRACKET", lower_s=lo, upper_s=hi, midpoint_s=(lo+hi)/2)
    if not t.headroom_deficit or (t.cache_upper is not None and t.cache_upper + ALIGN <= t.submit):
        return dict(kind="VERIFIED_ZERO", lower_s=0.0, upper_s=0.0, midpoint_s=0.0)
    return dict(kind="NO_MEMORY_EVENT_UNPROVEN_HEADROOM_AT_SUBMIT")


def fit(path, prior_path, out, contract):
    need(sha(contract) == CONTRACT_SHA, "prospective contract changed")
    need(sha(prior_path) == PRIOR_SHA, "prior A source changed")
    prior = read(prior_path)
    a_law, a_up = prior["lower_return_samples_seconds"]["A"], prior["upper_return_samples_seconds"]["A"]
    need(len(a_law) == len(a_up) == 6 and all(0 <= x <= y for x,y in zip(a_law, a_up)),
         "prior A bracket inventory")
    source, trials, groups = pack_data(path, "calibration", CALIBRATION_BLOCKS)
    b_trials = [t for t in trials if (t.mode,t.competition,t.phase)==("B","contended","early")]
    need(len(b_trials) == CALIBRATION_BLOCKS and all(observed_block(t)["kind"] == "POSITIVE_BRACKET" for t in b_trials),
         "calibration B-early lacks paired capacity witness")
    spare = [t for t in trials if t.competition == "spare"]
    need(all(not t.memory_event for t in spare), "ordinary scheduling calibration not uncontended")
    c = {
        "ordinary_submit_to_bind_s": median(t.bind-t.submit for t in spare),
        "ordinary_bind_to_ready_host_upper_s": median(t.ready_upper-t.bind for t in spare),
        "ordinary_ingress_to_submit_s_reference_only": median(t.submit-t.ingress for t in spare),
        "calibration_spare_n": len(spare),
    }
    need(c["ordinary_submit_to_bind_s"] >= 0 and c["ordinary_bind_to_ready_host_upper_s"] >= 0,
         "ordinary scheduling and readiness must be nonnegative")
    early_did = did_by_block(groups,"early")
    sd = stdev(early_did)
    uncapped = max(8, math.ceil(((1.96+.84)*max(sd,.20)/1.0)**2))
    seal = dict(schema="RETURN_HTTP_MODEL_SEAL_V1", model_source_sha256=sha(__file__),
        contract_sha256=sha(contract), calibration_normalized_sha256=sha(path),
        calibration_raw_status_sha256=source["raw_status_sha256"],
        calibration_raw_plan_sha256=source["raw_plan_sha256"],
        normalized_source_sha256=source["normalizer_source_sha256"],
        calibration_run_id=source["run_id"], calibration_blocks=sorted(groups),
        calibration_y_uids=sorted(t.y_uid for t in trials),
        calibration_trial_ids=sorted(t.trial_id for t in trials),
        prior_A_sha256=sha(prior_path),
        learned_terms=c,
        return_delay_s={"A":median((lo+hi)/2 for lo,hi in zip(a_law,a_up)),
                        "B":median((t.cache_lower+t.cache_upper)/2 for t in b_trials)},
        B_calibration_source_ids=[t.trial_id for t in b_trials],
        calibration_early_DID_s_by_block=early_did, calibration_DID_SD_s=sd,
        evaluation_n_uncapped=uncapped, evaluation_n=min(16,uncapped), evaluation_n_capped=uncapped>16,
        frozen_confirmation_n=CONFIRM_BLOCKS, offsets_by_phase=OFFSETS,
        bootstrap=dict(seed=BOOT_SEED,draws=BOOT_DRAWS,method="whole-block paired percentile"),
        timing_origin="observed ingress→submit overhead common to both arms; fixed terms at submission",
        withheld_features=["Y failure","Y bind","Y ready","held-out X cache removal"])
    write(out,seal)
    return seal


def percentile(sorted_vals, p):
    idx=(len(sorted_vals)-1)*p
    i=int(idx)
    return sorted_vals[i]+(sorted_vals[min(i+1,len(sorted_vals)-1)]-sorted_vals[i])*(idx-i)


def block_ci(values):
    need(len(values) >= 2, "at least two complete blocks for uncertainty")
    rng=Random(BOOT_SEED)
    estimates=sorted(mean(rng.choices(values,k=len(values))) for _ in range(BOOT_DRAWS))
    return dict(mean=mean(values), lower_95=percentile(estimates,.025),
                upper_95=percentile(estimates,.975), block_n=len(values), draws=BOOT_DRAWS,
                descriptive_small_block_uncertainty=True)


def confusion(rows, arm):
    pred=[r[arm]["memory_event"] for r in rows]
    actual=[r["same_UID_memory_failure"] for r in rows]
    tp=sum(a and b for a,b in zip(actual,pred))
    fn=sum(a and not b for a,b in zip(actual,pred))
    fp=sum(not a and b for a,b in zip(actual,pred))
    tn=len(rows)-tp-fn-fp
    return dict(tp=tp,fn=fn,fp=fp,tn=tn,n=len(rows),
                recall=tp/(tp+fn) if tp+fn else None, precision=tp/(tp+fp) if tp+fp else None,
                F1=2*tp/(2*tp+fn+fp) if 2*tp+fn+fp else None)


def mae(rows, arm, observation, prediction):
    return mean(abs(r[observation]-r[arm][prediction]) for r in rows) if rows else None


def block_error(rows):
    out={"n":len(rows)}
    for field in ("lower_s","midpoint_s","upper_s"):
        if rows:
            h0=mean(abs(r["block_observed"][field]-r["immediate"]["block_s"]) for r in rows)
            h1=mean(abs(r["block_observed"][field]-r["delayed"]["block_s"]) for r in rows)
            out[field]=dict(immediate_MAE_s=h0,delayed_MAE_s=h1,improvement_s=h0-h1)
        else:
            out[field]=None
    return out


def summary(rows):
    return dict(n=len(rows),direct_duration_kind_counts={x:sum(r["block_observed"]["kind"]==x for r in rows)
                    for x in sorted({r["block_observed"]["kind"] for r in rows})},
                observed_mean_arrival_to_bind_s=mean(r["observed_arrival_to_bind_s"] for r in rows),
                observed_mean_arrival_to_ready_host_upper_s=mean(r["observed_arrival_to_ready_host_upper_s"] for r in rows),
                immediate=confusion(rows,"immediate"),delayed=confusion(rows,"delayed"),
                immediate_arrival_to_bind_MAE_s=mae(rows,"immediate","observed_arrival_to_bind_s","arrival_to_bind_s"),
                delayed_arrival_to_bind_MAE_s=mae(rows,"delayed","observed_arrival_to_bind_s","arrival_to_bind_s"),
                immediate_arrival_to_ready_upper_MAE_s=mae(rows,"immediate","observed_arrival_to_ready_host_upper_s","arrival_to_ready_s"),
                delayed_arrival_to_ready_upper_MAE_s=mae(rows,"delayed","observed_arrival_to_ready_host_upper_s","arrival_to_ready_s"),
                direct_observed_positives_only=block_error([r for r in rows if r["block_observed"]["kind"]=="POSITIVE_BRACKET"]),
                direct_all_known=block_error([r for r in rows if r["block_observed"]["kind"] in {"POSITIVE_BRACKET","VERIFIED_ZERO"}]))


def evaluate(path, model_path, expected_model_sha, contract, phase, out, previous_evaluation=None):
    need(sha(contract) == CONTRACT_SHA, "prospective contract changed")
    need(sha(model_path)==expected_model_sha, "evaluation model seal mismatch")
    model = read(model_path)
    need(model["schema"]=="RETURN_HTTP_MODEL_SEAL_V1" and model["model_source_sha256"]==sha(__file__)
         and model["contract_sha256"]==sha(contract) and model["offsets_by_phase"]==OFFSETS,
         "evaluation model or contract was changed")
    n = model["evaluation_n"] if phase=="evaluation" else model["frozen_confirmation_n"]
    need(phase in {"evaluation","confirmation"} and 8 <= n <= 16,"fixed evaluation stage/count")
    source, trials, groups = pack_data(path, phase, n)
    need(source["model_seal_sha256_before_acquisition"]==expected_model_sha and
         source["normalizer_source_sha256"]==model["normalized_source_sha256"] and
         source["run_id"]!=model["calibration_run_id"] and
         source["raw_plan_sha256"]!=model["calibration_raw_plan_sha256"] and
         source["raw_status_sha256"]!=model["calibration_raw_status_sha256"] and
         not ({t.y_uid for t in trials} & set(model["calibration_y_uids"])) and
         not ({t.trial_id for t in trials} & set(model["calibration_trial_ids"])) and
         not (set(groups) & set(model["calibration_blocks"])), "acquisition/cell split contamination")
    if phase=="confirmation":
        need(previous_evaluation is not None and
             sha(previous_evaluation)==source["previous_evaluation_sha256"],
             "confirmation preacquisition evaluation receipt mismatch")
        previous=read(previous_evaluation)
        earlier=previous["trial_predictions"]
        need(previous["joint_platform_and_model_gate"] is True and previous["phase"]=="evaluation" and
             previous["model_seal_sha256"]==expected_model_sha and
             not ({t.y_uid for t in trials} & {r["y_uid"] for r in earlier}) and
             not (set(groups) & {r["block"] for r in earlier}), "confirmation not independent of prior stage")
    else:
        need(previous_evaluation is None and source.get("previous_evaluation_sha256") is None,
             "evaluation should not use future confirmation or prior eval output")
    rows=[]
    for t in trials:
        a=timing_predictions(t,model["learned_terms"],{"A":0.0,"B":0.0})
        b=timing_predictions(t,model["learned_terms"],model["return_delay_s"])
        truth=observed_block(t)
        rows.append(dict(trial_id=t.trial_id,block=t.block,mode=t.mode,competition=t.competition,
            phase=t.phase,y_uid=t.y_uid,actual_ingress_rel_retire_s=t.ingress,
            actual_submit_rel_retire_s=t.submit,observed_arrival_to_bind_s=t.arrival_to_bind,
            observed_arrival_to_ready_host_lower_s=t.ready_lower-t.ingress,
            observed_arrival_to_ready_host_upper_s=t.ready_upper-t.ingress,
            observed_bind_to_ready_host_lower_s=max(0.0,t.ready_lower-t.bind),
            observed_bind_to_ready_host_upper_s=max(0.0,t.ready_upper-t.bind),
            same_UID_memory_failure=t.memory_event,block_observed=truth,
            x_only_release_verified=t.x_only_release_verified,both_http_complete=t.both_http_complete,
            immediate=a,delayed=b))
    all_rows=summary(rows)
    by_arm={}
    for m,c,p in sorted(CELLS):
        by_arm[f"{m}_{c}_{p}"]=summary([r for r in rows if (r["mode"],r["competition"],r["phase"])==(m,c,p)])
    blocks=defaultdict(list)
    for r in rows:
        truth=r["observed_arrival_to_bind_s"]
        blocks[r["block"]].append(abs(truth-r["immediate"]["arrival_to_bind_s"])
                                -abs(truth-r["delayed"]["arrival_to_bind_s"]))
    early=block_ci(did_by_block(groups,"early"))
    late=block_ci(did_by_block(groups,"late"))
    gain=block_ci([mean(v) for _,v in sorted(blocks.items())])
    h0=all_rows["immediate_arrival_to_bind_MAE_s"]
    relative=gain["mean"]/h0 if h0 else None
    f0=all_rows["immediate"]["F1"]
    f1=all_rows["delayed"]["F1"]
    f_gain=f1-f0 if f1 is not None and f0 is not None else None
    recall=all_rows["delayed"]["recall"]
    controls=[r for r in rows if r["competition"]=="spare" or r["phase"]=="late"]
    controls_fp=sum(r["delayed"]["memory_event"] and not r["same_UID_memory_failure"] for r in controls)
    b_late_predicted=sum(r["delayed"]["memory_event"] for r in rows if r["mode"]=="B" and r["phase"]=="late")
    late_actual_positive=sum(r["same_UID_memory_failure"] for r in rows if r["phase"]=="late")
    be=all_rows["direct_observed_positives_only"]
    positives=all_rows["delayed"]["tp"]+all_rows["delayed"]["fn"]
    gates=dict(
        platform_early_DID=early["mean"]>=1.0 and early["lower_95"]>0,
        platform_late_negative_control=abs(late["mean"])<=.25 and not late_actual_positive and not b_late_predicted and not controls_fp,
        all_arrival_bind_MAE=gain["mean"]>=.25 and relative is not None and relative>=.20 and gain["lower_95"]>0,
        memory_classification=f_gain is not None and f_gain>=.20 and recall is not None and recall>=.75 and not controls_fp,
        observed_positive_block_duration=be["n"]==positives and positives>0 and
            be["midpoint_s"]["improvement_s"]>=.5 and be["lower_s"]["improvement_s"]>0 and be["upper_s"]["improvement_s"]>0,
        x_only_closure=all(r["x_only_release_verified"] and r["both_http_complete"] for r in rows))
    result=dict(schema="RETURN_HTTP_HELDOUT_EVALUATION_V1",phase=phase,held_out=True,
       new_workload_generalization=False,controller_observed_submit_common=True,
       empirical_platform_scope="fixed controlled X/Y load, scheduler cache, no PPO and no IBM replay",
       model_seal_sha256=expected_model_sha, normalized_evaluation_sha256=sha(path),
       evaluation_raw_plan_sha256=source["raw_plan_sha256"], evaluation_raw_status_sha256=source["raw_status_sha256"],
       previous_evaluation_sha256=source.get("previous_evaluation_sha256"),
       evaluator_sha256=sha(__file__), unique_blocks=len(groups), all=all_rows, per_scenario=by_arm,
       platform_early_DID_s=early,platform_late_DID_s=late,paired_arrival_to_bind_MAE_improvement_s=gain,
       relative_arrival_to_bind_MAE_improvement=relative,F1_improvement=f_gain,
       negative_controls_n=len(controls),negative_control_false_positives=controls_fp,
       B_late_predicted_positive=b_late_predicted,late_observed_memory_failures=late_actual_positive,gates=gates,
       platform_mechanism_supported=all(gates[x] for x in ("platform_early_DID","platform_late_negative_control","x_only_closure")),
       delayed_model_supported=all(gates[x] for x in ("all_arrival_bind_MAE","memory_classification",
                                                      "observed_positive_block_duration","x_only_closure")),
       joint_platform_and_model_gate=all(gates.values()),
       trial_predictions=rows)
    write(out,result)
    return result


def cli():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="action",required=True)
    for name in ("calibrate","evaluate"):
        s=sub.add_parser(name)
        s.add_argument("--normalized",type=Path,required=True)
        s.add_argument("--contract",type=Path,required=True)
        s.add_argument("--out",type=Path,required=True)
        if name=="calibrate":
            s.add_argument("--prior-A",type=Path,required=True)
        else:
            s.add_argument("--model-seal",type=Path,required=True)
            s.add_argument("--model-sha",required=True)
            s.add_argument("--phase",choices=("evaluation","confirmation"),default="evaluation")
            s.add_argument("--previous-evaluation",type=Path)
    args=p.parse_args()
    if args.action=="calibrate":
        result=fit(args.normalized,args.prior_A,args.out,args.contract)
        print(json.dumps(dict(evaluation_n=result["evaluation_n"],return_delay_s=result["return_delay_s"],
                              model_seal_sha256=sha(args.out)),indent=2))
    else:
        result=evaluate(args.normalized,args.model_seal,args.model_sha,args.contract,args.phase,args.out,args.previous_evaluation)
        print(json.dumps(dict(gates=result["gates"],platform_mechanism_supported=result["platform_mechanism_supported"],
                              delayed_model_supported=result["delayed_model_supported"],result_sha256=sha(args.out)),indent=2))


if __name__=="__main__":
    cli()

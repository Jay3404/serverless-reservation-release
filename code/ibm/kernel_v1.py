"""Independent causal lifecycle pilot kernel, not the prior frozen simulator.

Four disjoint physical states: init/busy/idle/retiring. Reservation is charged
from birth until the confirmed-return event. Retiring never accepts requests.
All policies share the same physical masks. Prior support/risk/idle-age gates
are intentionally absent. This is a new experimental contract, not a bug fix
or an equivalent implementation of the old policy environment.

Requests occupy pooled configured concurrency slots. Unavailable-at-arrival
(`warm_miss`) and assigned-to-initialization (`cold`) are distinct. In-flight
completion, preparation and return times exist only in the private event heap.
The public observation never includes actual future durations or deadlines.
"""
from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence as ReadOnlySequence
from dataclasses import dataclass, field
import heapq
import math
from numbers import Integral
from typing import Callable, Mapping, Sequence


STATES = ("init", "busy", "idle", "retiring")
ACTION_MAP = {
    0: "delegate_default_TTL600",
    1: "reclaim_one_used_idle",
    2: "prewarm_one_unused_lease10",
    3: "prewarm_one_unused_lease30",
    4: "prewarm_one_unused_lease60",
    5: "prewarm_up_to_two_unused_lease30",
    6: "cancel_one_unused_idle",
    7: "assign_one_used_idle_TTL60_from_idle_since",
    8: "assign_one_used_idle_TTL600_from_idle_since",
}
LEASE_ACTIONS = {2: (1, 10.0), 3: (1, 30.0), 4: (1, 60.0), 5: (2, 30.0)}
_PRIORITY = {"complete": 0, "ready": 1, "returned": 2, "ttl": 3, "arrival": 4}


@dataclass(frozen=True)
class NativeApp:
    app_id: str
    memory_gb: float
    cpu: float = 1.0
    concurrency: int = 1
    min_scale: int = 0


@dataclass(frozen=True)
class Request:
    request_id: str
    app_id: str
    arrival: float
    duration: float


@dataclass
class _RequestState:
    source: Request
    warm_miss: bool = False
    cold: bool = False
    claim_at: float | None = None
    instance_id: int | None = None
    started: float | None = None
    completed: float | None = None
    initialization_wait: float = 0.0
    other_wait: float = 0.0


@dataclass
class _Instance:
    ident: int
    app_id: str
    born: float
    state: str = "init"
    state_since: float = 0.0
    speculative: bool = False
    lease: float = 30.0
    used: int = 0
    running: set[str] = field(default_factory=set)
    claims: list[str] = field(default_factory=list)
    idle_since: float | None = None
    idle_token: int = 0
    ttl_assigned: bool = False
    idle_ttl_seconds: float = 600.0
    ready_observed: float | None = None
    retirement_intent: float | None = None
    first_use_kind: str | None = None
    cost_gbs: dict[str, float] = field(default_factory=lambda: dict.fromkeys(STATES, 0.0))


class KernelError(RuntimeError):
    pass


class TailTimeout(KernelError):
    """Explicit failed experiment; unfinished requests are never discarded."""


class DelaySequence:
    """Private deterministic delay law; finite sequence, no silent cycling."""
    def __init__(self, values: Sequence[float]):
        self._values = iter(tuple(values))

    def __call__(self, app: NativeApp, origin: str, now: float) -> float:
        try:
            return float(next(self._values))
        except StopIteration as exc:
            raise KernelError("Private delay sequence exhausted; no automatic repeat") from exc


class _ObservedPrefix(ReadOnlySequence):
    """Read-only fixed-length view over already observed history, no O(N) copy."""
    def __init__(self, values):
        self._values, self._length = values, len(values)

    def __len__(self):
        return self._length

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self._values[i] for i in range(*index.indices(self._length)))
        if index < 0:
            index += self._length
        if not 0 <= index < self._length:
            raise IndexError(index)
        return self._values[index]


class Kernel:
    """Control API: run_until(t), observe(), apply_actions(), finish(T).

    app iteration is sorted app_id. Shared capacity contention is therefore
    order-sensitive, fixed for every policy, and must be disclosed. Action5
    preserves the old 'up to two' meaning and records its realized quantity.
    """
    def __init__(self, configs: Sequence[NativeApp], requests: Sequence[Request], *,
                 capacity_gb: float = 8.0, prep_sampler: Callable | None = None,
                 return_sampler: Callable | None = None, start: float = 0.0,
                 default_ttl: float = 600.0, delay_ewma_alpha: float = 0.2,
                 prep_prior: float = 0.808, return_prior: float = 0.0,
                 record_events: bool = True):
        if not math.isfinite(capacity_gb) or capacity_gb <= 0:
            raise ValueError("Finite positive physical memory capacity required")
        if default_ttl != 600.0:
            raise ValueError("This nine-action contract delegates to TTL600")
        if not 0 < delay_ewma_alpha <= 1:
            raise ValueError("EWMA alpha outside (0,1]")
        self.configs = {str(x.app_id): x for x in configs}
        if len(self.configs) != len(configs):
            raise ValueError("Duplicate app identifier")
        for app in configs:
            if not isinstance(app.app_id, str) or not app.app_id:
                raise ValueError("app_id must be a nonempty string")
            if (not math.isfinite(app.memory_gb) or app.memory_gb <= 0
                    or not math.isfinite(app.cpu) or app.cpu < 0
                    or type(app.concurrency) is not int or app.concurrency < 1):
                raise ValueError("Invalid native memory/CPU/concurrency configuration")
            if app.min_scale != 0:
                raise ValueError("Pilot supports native min_scale=0 only; no implicit floor release")
        self.app_ids = tuple(sorted(self.configs))
        self.capacity_gb, self.default_ttl = float(capacity_gb), float(default_ttl)
        self.start = self.now = float(start)
        if not math.isfinite(self.start):
            raise ValueError("Finite origin required")
        self._prep_sampler = prep_sampler or (lambda app, origin, now: prep_prior)
        self._return_sampler = return_sampler or (lambda app, origin, now: return_prior)
        self._alpha = delay_ewma_alpha
        self._delay_estimates = {"prep": float(prep_prior), "return": float(return_prior)}
        if any(not math.isfinite(x) or x < 0 for x in self._delay_estimates.values()):
            raise ValueError("Finite nonnegative prior delays required")
        self._delay_samples = {"prep": 0, "return": 0}
        self._events = []
        self._seq = self._next_instance = 0
        self._instances: dict[int, _Instance] = {}
        self._requests: dict[str, _RequestState] = {}
        self._queues = {a: deque() for a in self.app_ids}
        self._by_app = {a: set() for a in self.app_ids}
        self._history = {a: {"arrivals": [], "completed_services": []} for a in self.app_ids}
        self._service_sums = {a: [0, 0.0, 0.0] for a in self.app_ids}
        self._reserved = dict.fromkeys(STATES, 0.0)
        self._cpu_reserved = dict.fromkeys(STATES, 0.0)
        self._cost = dict.fromkeys(STATES, 0.0)
        self._cpu_cost = dict.fromkeys(STATES, 0.0)
        self._app_cost = dict.fromkeys(self.app_ids, 0.0)
        self._app_cold = dict.fromkeys(self.app_ids, 0)
        self._app_warm_miss = dict.fromkeys(self.app_ids, 0)
        self._app_voluntary = dict.fromkeys(self.app_ids, 0)
        self._waiting_init = dict.fromkeys(self.app_ids, 0)
        self._waiting_other = dict.fromkeys(self.app_ids, 0)
        self._app_init_wait = dict.fromkeys(self.app_ids, 0.0)
        self._app_other_wait = dict.fromkeys(self.app_ids, 0.0)
        self._app_unused_expired_gbs = dict.fromkeys(self.app_ids, 0.0)
        self._record_events = record_events
        self._log = []
        self._retired_rows = []
        self._counts = defaultdict(int)
        self._actions_requested = [0] * 9
        self._actions_executed = [0] * 9
        self._last_retire = {}
        self._completed = self._observed = 0
        self._closing = self._finished = False
        self._evaluation_end = None
        self._cost_at_T = self._tail_end = None
        self._retention_episodes = defaultdict(int)
        self._cost_before_control = 0.0
        for request in requests:
            if (request.request_id in self._requests or request.app_id not in self.configs
                    or not math.isfinite(request.arrival) or request.arrival < self.start
                    or not math.isfinite(request.duration) or request.duration < 0):
                raise ValueError("Invalid, duplicate or pre-origin request")
            self._requests[request.request_id] = _RequestState(request)
            self._schedule(request.arrival, "arrival", request.request_id)

    def _emit(self, kind, **fields):
        if self._record_events:
            self._log.append(dict(at=self.now, event=kind, **fields))

    def _schedule(self, at, kind, payload):
        if not math.isfinite(at) or at < self.now - 1e-10:
            raise KernelError("Event scheduled in the past/nonfinite time")
        self._seq += 1
        heapq.heappush(self._events, (max(self.now, float(at)), _PRIORITY[kind],
                                      self._seq, kind, payload))

    def _integrate(self, at):
        dt = at - self.now
        if dt < -1e-10:
            raise KernelError("Clock moved backwards")
        for state in STATES:
            self._cost[state] += self._reserved[state] * dt
            self._cpu_cost[state] += self._cpu_reserved[state] * dt
        for app_id in self.app_ids:
            self._app_init_wait[app_id] += self._waiting_init[app_id] * dt
            self._app_other_wait[app_id] += self._waiting_other[app_id] * dt
        self.now = float(at)

    def _set_state(self, instance, state):
        app = self.configs[instance.app_id]
        segment = self.now - instance.state_since
        instance.cost_gbs[instance.state] += segment * app.memory_gb
        self._app_cost[instance.app_id] += segment * app.memory_gb
        self._reserved[instance.state] -= app.memory_gb
        self._cpu_reserved[instance.state] -= app.cpu
        instance.state, instance.state_since = state, self.now
        if state is not None:
            self._reserved[state] += app.memory_gb
            self._cpu_reserved[state] += app.cpu

    @property
    def reserved_gb(self):
        return math.fsum(self._reserved.values())

    def _sample(self, sampler, app, origin):
        value = float(sampler(app, origin, self.now))
        if not math.isfinite(value) or value < 0:
            raise KernelError("Private delay law returned invalid delay")
        return value

    def _new(self, app_id, speculative=False, lease=30.0):
        app = self.configs[app_id]
        if self.reserved_gb + app.memory_gb > self.capacity_gb + 1e-10:
            return None
        delay = self._sample(self._prep_sampler, app, "prewarm" if speculative else "reactive")
        ident = self._next_instance
        self._next_instance += 1
        instance = _Instance(ident, app_id, self.now, state_since=self.now,
                             speculative=speculative, lease=lease)
        self._instances[ident] = instance
        self._by_app[app_id].add(ident)
        self._reserved["init"] += app.memory_gb
        self._cpu_reserved["init"] += app.cpu
        self._counts["creations"] += 1
        self._counts["prewarm_creations" if speculative else "reactive_creations"] += 1
        if app_id in self._last_retire and self.now - self._last_retire[app_id] <= 60:
            self._counts["recreation_within60_after_retirement_observational"] += 1
        self._emit("birth", instance=ident, app=app_id, speculative=speculative,
                   memory_gb=app.memory_gb, cpu=app.cpu)
        self._schedule(self.now + delay, "ready", ident)
        return instance

    def _update_delay(self, kind, sample):
        if self._delay_samples[kind] == 0:
            self._delay_estimates[kind] = sample
        else:
            self._delay_estimates[kind] += self._alpha * (sample-self._delay_estimates[kind])
        self._delay_samples[kind] += 1

    def _idle(self, instance):
        self._set_state(instance, "idle")
        instance.idle_since = self.now
        instance.idle_token += 1
        instance.ttl_assigned = False
        instance.idle_ttl_seconds = self.default_ttl if instance.used else instance.lease
        expiry = self.now + instance.idle_ttl_seconds
        if instance.used:
            self._retention_episodes["opened"] += 1
        self._schedule(expiry, "ttl", (instance.ident, instance.idle_token))

    def _start_request(self, instance, request):
        if instance.state not in ("idle", "busy"):
            raise KernelError("Only ready instances may serve")
        if len(instance.running) >= self.configs[instance.app_id].concurrency:
            raise KernelError("Concurrency capacity exceeded")
        if request.claim_at is None:
            self._waiting_other[instance.app_id] -= 1
        if instance.state == "idle":
            if instance.used:
                self._retention_episodes["reused"] += 1
            instance.idle_token += 1
            instance.ttl_assigned = False
            self._set_state(instance, "busy")
        if instance.speculative and not instance.used:
            instance.first_use_kind = ("timely" if instance.ready_observed <= request.source.arrival
                                       else "late")
            self._counts["prewarm_" + instance.first_use_kind] += 1
        instance.used += 1
        request.started = self.now
        request.instance_id = instance.ident
        request.other_wait = self.now-request.source.arrival-request.initialization_wait
        if request.other_wait < -1e-8:
            raise KernelError("Wait decomposition invalid")
        request.other_wait = max(0.0, request.other_wait)
        instance.running.add(request.source.request_id)
        self._emit("request_start", request=request.source.request_id, app=instance.app_id,
                   instance=instance.ident, cold=request.cold, warm_miss=request.warm_miss,
                   initialization_wait=request.initialization_wait, other_wait=request.other_wait)
        self._schedule(self.now+request.source.duration, "complete", request.source.request_id)

    def _claim(self, instance, request):
        if request.cold:
            raise KernelError("A request cannot be claimed by multiple initializations")
        request.cold = True
        self._app_cold[instance.app_id] += 1
        self._waiting_other[instance.app_id] -= 1
        self._waiting_init[instance.app_id] += 1
        request.claim_at = self.now
        request.instance_id = instance.ident
        instance.claims.append(request.source.request_id)

    def _dispatch(self, app_id):
        queue = self._queues[app_id]
        if not queue:
            return
        app = self.configs[app_id]
        ready = [self._instances[i] for i in sorted(self._by_app[app_id])
                 if self._instances[i].state in ("idle", "busy")]
        for instance in ready:
            while queue and len(instance.running) < app.concurrency:
                self._start_request(instance, self._requests[queue.popleft()])
        initializing = [self._instances[i] for i in sorted(self._by_app[app_id])
                        if self._instances[i].state == "init"]
        for instance in initializing:
            while queue and len(instance.claims) < app.concurrency:
                self._claim(instance, self._requests[queue.popleft()])
        while queue:
            instance = self._new(app_id)
            if instance is None:
                self._make_room(app_id)
                break
            while queue and len(instance.claims) < app.concurrency:
                self._claim(instance, self._requests[queue.popleft()])

    def _make_room(self, app_id):
        """Common reactive capacity eviction, separate from policy reclaim.

        Returned capacity is never credited before its event. Existing retiring
        reservation counts as a pending promise only for deciding whether *more*
        idle victims are necessary, not as allocatable capacity. This prevents
        repeated arrivals from causing a premature-availability eviction cascade.
        """
        required = self.configs[app_id].memory_gb
        if required > self.capacity_gb:
            return  # Explicit tail timeout later, never a dropped request.
        free = self.capacity_gb-self.reserved_gb
        pending = sum(self.configs[p.app_id].memory_gb for p in self._instances.values()
                      if p.state == "retiring")
        candidates = sorted((p for p in self._instances.values() if p.state == "idle"),
                            key=lambda p: (p.idle_since, p.ident))
        for instance in candidates:
            if free+pending >= required-1e-10:
                break
            self._retire(instance, "capacity")
            pending += self.configs[instance.app_id].memory_gb

    def _dispatch_all(self):
        # Fixed deterministic ordering is not a performance-optimal scheduler.
        for app in self.app_ids:
            self._dispatch(app)

    def _retire(self, instance, reason):
        if instance.running or instance.claims or instance.state != "idle":
            raise KernelError("Unsafe retirement of a busy/claimed/nonready instance")
        if instance.used:
            self._retention_episodes["unused_expired" if reason == "ttl" else "reclaimed"] += 1
        delay = self._sample(self._return_sampler, self.configs[instance.app_id], reason)
        instance.idle_token += 1
        instance.retirement_intent = self.now
        self._last_retire[instance.app_id] = self.now
        self._set_state(instance, "retiring")
        self._counts["retirements"] += 1
        self._counts["retire_" + reason] += 1
        if reason in ("voluntary", "unused_cancel"):
            self._app_voluntary[instance.app_id] += 1
        self._emit("retirement_intent", instance=instance.ident, app=instance.app_id,
                   reason=reason, accepts_new_requests=False)
        self._schedule(self.now+delay, "returned", instance.ident)

    def _event(self, kind, payload):
        if kind == "arrival":
            request = self._requests[payload]
            app_id = request.source.app_id
            request.warm_miss = not any(
                self._instances[i].state in ("idle", "busy")
                and len(self._instances[i].running) < self.configs[app_id].concurrency
                for i in self._by_app[app_id])
            self._app_warm_miss[app_id] += int(request.warm_miss)
            self._observed += 1
            self._history[app_id]["arrivals"].append(self.now)
            self._emit("arrival", request=payload, app=app_id, warm_miss=request.warm_miss)
            self._queues[app_id].append(payload)
            self._waiting_other[app_id] += 1
            self._dispatch(app_id)
        elif kind == "ready":
            instance = self._instances.get(payload)
            if instance is None or instance.state != "init":
                return
            instance.ready_observed = self.now
            self._update_delay("prep", self.now-instance.born)
            self._emit("ready", instance=payload, app=instance.app_id,
                       observed_prep_delay=self.now-instance.born)
            claims, instance.claims = instance.claims, []
            self._waiting_init[instance.app_id] -= len(claims)
            self._idle(instance)
            for ident in claims:
                request = self._requests[ident]
                request.initialization_wait = self.now-request.claim_at
                self._start_request(instance, request)
            self._dispatch(instance.app_id)
        elif kind == "complete":
            request = self._requests[payload]
            instance = self._instances[request.instance_id]
            instance.running.remove(payload)
            request.completed = self.now
            self._completed += 1
            self._history[instance.app_id]["completed_services"].append(
                (self.now, self.now-request.started))
            statistics = self._service_sums[instance.app_id]
            statistics[0] += 1
            statistics[1] += self.now-request.started
            statistics[2] += (self.now-request.started)**2
            self._emit("request_complete", request=payload, app=instance.app_id,
                       instance=instance.ident, observed_service=self.now-request.started)
            if not instance.running:
                self._idle(instance)
            self._dispatch(instance.app_id)
        elif kind == "ttl":
            ident, token = payload
            instance = self._instances.get(ident)
            if instance is not None and instance.state == "idle" and instance.idle_token == token:
                self._retire(instance, "ttl")
        elif kind == "returned":
            instance = self._instances[payload]
            if instance.state != "retiring":
                raise KernelError("Return without retirement")
            observed = self.now-instance.retirement_intent
            self._update_delay("return", observed)
            self._set_state(instance, None)
            self._emit("confirmed_return", instance=payload, app=instance.app_id,
                       observed_return_delay=observed)
            if instance.speculative and not instance.used:
                self._counts["prewarm_unused"] += 1
                self._app_unused_expired_gbs[instance.app_id] += sum(instance.cost_gbs.values())
            self._retired_rows.append(dict(instance=payload, app=instance.app_id,
                born=instance.born, confirmed_return=self.now, speculative=instance.speculative,
                uses=instance.used, first_use_kind=instance.first_use_kind,
                cost_gbs=dict(instance.cost_gbs)))
            self._by_app[instance.app_id].remove(payload)
            del self._instances[payload]
            self._dispatch_all()
        else:
            raise KernelError("Unknown event")
        if self.reserved_gb > self.capacity_gb + 1e-8:
            raise KernelError("Physical memory invariant violated")

    def run_until(self, at):
        if self._finished or at < self.now or not math.isfinite(at):
            raise ValueError("Invalid or post-finish clock movement")
        while self._events and self._events[0][0] <= at:
            when, _, _, kind, payload = heapq.heappop(self._events)
            self._integrate(when)
            self._event(kind, payload)
        self._integrate(at)

    def _targets(self, app_id):
        idle = [self._instances[i] for i in self._by_app[app_id]
                if self._instances[i].state == "idle"]
        idle.sort(key=lambda p: (p.idle_since, p.ident))
        used = [p for p in idle if p.used]
        unused = [p for p in idle if p.speculative and not p.used]
        ttl = [p for p in used if not p.ttl_assigned]
        return used, unused, ttl

    def legal_actions(self, app_id):
        if self._closing:
            raise KernelError("No controller calls after fixed evaluation horizon")
        if app_id not in self.configs:
            raise KeyError(app_id)
        used, unused, ttl = self._targets(app_id)
        room = self.reserved_gb+self.configs[app_id].memory_gb <= self.capacity_gb+1e-10
        return (True, bool(used), room, room, room, room, bool(unused), bool(ttl), bool(ttl))

    def apply_actions(self, actions: Mapping[str, int] | Sequence[int]):
        if self._closing or self._finished:
            raise KernelError("No controller decisions during termination tail")
        if isinstance(actions, Mapping):
            unknown = set(actions)-set(self.app_ids)
            if unknown:
                raise ValueError("Unknown action app")
            proposed = {a: actions.get(a, 0) for a in self.app_ids}
        else:
            if len(actions) != len(self.app_ids):
                raise ValueError("Action array must match sorted app_ids")
            proposed = dict(zip(self.app_ids, actions))
        if any(not isinstance(x, Integral) or isinstance(x, bool) or x not in ACTION_MAP
               for x in proposed.values()):
            raise ValueError("Actions must be Python integers 0..8")
        proposed = {a: int(x) for a, x in proposed.items()}
        result = {}
        for app_id in self.app_ids:
            action = proposed[app_id]
            self._actions_requested[action] += 1
            legal = self.legal_actions(app_id)[action]
            realized, quantity = (action if legal else 0), 0
            used, unused, ttl = self._targets(app_id)
            if legal and action in (1, 6):
                self._retire((used if action == 1 else unused)[0],
                             "voluntary" if action == 1 else "unused_cancel")
                quantity = 1
            elif legal and action in LEASE_ACTIONS:
                target, lease = LEASE_ACTIONS[action]
                for _ in range(target):
                    if self._new(app_id, True, lease) is None:
                        break
                    quantity += 1
                if quantity == 0:
                    realized = 0
                # Already-arrived queued requests claim any new preparation;
                # policy-triggered birth must not turn an old queue wait into a
                # fake warm/timely prewarming success at the ready event.
                self._dispatch(app_id)
            elif legal and action in (7, 8):
                instance = ttl[0]
                instance.ttl_assigned = True
                instance.idle_token += 1
                seconds = 60.0 if action == 7 else 600.0
                instance.idle_ttl_seconds = seconds
                # Removing the old age<60 gate makes a late TTL60 decision
                # expire immediately; the idle clock is never reset to now.
                self._schedule(max(self.now, instance.idle_since+seconds), "ttl",
                               (instance.ident, instance.idle_token))
                self._emit("ttl_assignment", instance=instance.ident, app=app_id,
                           ttl=seconds, idle_since=instance.idle_since)
                quantity = 1
            self._actions_executed[realized] += 1
            result[app_id] = dict(proposed=action, executed=realized, legal=legal, quantity=quantity)
            self._emit("action", app=app_id, **result[app_id])
        # Zero-duration physical events occur only after the full app-ordered
        # action batch; capacity is not released early for the next app.
        self.run_until(self.now)
        return result

    def causal_history(self):
        return {a: {k: tuple(v) for k, v in h.items()} for a, h in self._history.items()}

    def observe(self, *, forecaster=None, cdf_horizons=(10.0, 30.0, 60.0, 600.0),
                include_cdf=True, include_return_features=True):
        """Forecaster(app_id, now, past_history, horizons)->CDF sequence.

        A fixed caller-owned causal predictor is injected, not trained here.
        Zeroing CDF/return features never changes physics or legal masks.
        """
        apps = {}
        for app_id in self.app_ids:
            cfg = self.configs[app_id]
            instances = [self._instances[i] for i in sorted(self._by_app[app_id])]
            by_state = {s: [p for p in instances if p.state == s] for s in STATES}
            pending = by_state["init"]
            retiring = by_state["retiring"]
            ready = by_state["busy"]+by_state["idle"]
            used_idle = sorted((p for p in by_state["idle"] if p.used),
                               key=lambda p: (p.idle_since, p.ident))
            unused_idle = sorted((p for p in by_state["idle"] if p.speculative and not p.used),
                                 key=lambda p: (p.idle_since, p.ident))
            history = self._history[app_id]
            n_service, service_sum, service_square_sum = self._service_sums[app_id]
            service_mean = service_sum/n_service if n_service else 0.0
            cdf = [0.0]*len(cdf_horizons)
            if include_cdf and forecaster is not None:
                history_view = {key: _ObservedPrefix(value) for key, value in history.items()}
                cdf = list(forecaster(app_id, self.now, history_view, tuple(cdf_horizons)))
                if (len(cdf) != len(cdf_horizons)
                        or any(not math.isfinite(x) or not 0 <= x <= 1 for x in cdf)):
                    raise KernelError("Injected forecaster returned invalid probabilities")
            apps[app_id] = dict(memory_gb=cfg.memory_gb, cpu=cfg.cpu,
                concurrency=cfg.concurrency, min_scale=cfg.min_scale,
                state_counts={s: len(by_state[s]) if s != "retiring" or include_return_features
                              else 0 for s in STATES},
                ready_free_slots=sum(cfg.concurrency-len(p.running) for p in ready),
                pending_free_slots=sum(cfg.concurrency-len(p.claims) for p in pending),
                pending_claimed_requests=sum(len(p.claims) for p in pending),
                running_requests=sum(len(p.running) for p in ready),
                queued_requests=len(self._queues[app_id]),
                pending_reserved_gb=len(pending)*cfg.memory_gb,
                pending_elapsed=tuple(self.now-p.born for p in pending),
                pending_leases=tuple(p.lease if p.speculative else None for p in pending),
                pending_claimed_slots=tuple(len(p.claims) for p in pending),
                retiring_count=len(retiring) if include_return_features else 0,
                retiring_reserved_gb=len(retiring)*cfg.memory_gb if include_return_features else 0.0,
                retiring_elapsed=tuple(self.now-p.retirement_intent for p in retiring)
                    if include_return_features else (),
                observed_prep_delay_estimate=self._delay_estimates["prep"],
                observed_return_delay_estimate=self._delay_estimates["return"]
                    if include_return_features else 0.0,
                idle_used_ages=tuple(self.now-p.idle_since for p in used_idle),
                idle_used_ttl_remaining=tuple(max(0., p.idle_since+p.idle_ttl_seconds-self.now)
                                              for p in used_idle),
                idle_used_ttl_assigned=tuple(p.ttl_assigned for p in used_idle),
                ttl_target_index=next((i for i, p in enumerate(used_idle) if not p.ttl_assigned), None),
                idle_unused_lease_remaining=tuple(max(0., p.idle_since+p.lease-self.now)
                                                  for p in unused_idle),
                idle_unused_elapsed=tuple(self.now-p.idle_since for p in unused_idle),
                idle_unused_paid_gb_seconds=tuple(sum(p.cost_gbs.values())
                    +(self.now-p.state_since)*cfg.memory_gb for p in unused_idle),
                prep_observed_samples=self._delay_samples["prep"],
                return_observed_samples=self._delay_samples["return"] if include_return_features else 0,
                seen_requests=len(history["arrivals"]),
                time_since_last_arrival=self.now-history["arrivals"][-1] if history["arrivals"] else None,
                completed_mean_service=service_mean,
                completed_service_variance=max(0.,service_square_sum/n_service-service_mean**2)
                    if n_service else 0.0,
                cdf=tuple(cdf), action_mask=self.legal_actions(app_id))
        return dict(now=self.now, capacity_gb=self.capacity_gb, reserved_gb=self.reserved_gb,
                    reserved_cpu=sum(self._cpu_reserved.values()),
                    reservation_gb_seconds=sum(self._cost.values()), apps=apps,
                    physics_contract="init+busy+idle+retiring reserve memory; CPU accounting only")

    def meter(self):
        """Causal cumulative per-app accounting in the fixed app_ids order.

        unused_expired is final, realized cost of a returned never-used prewarm.
        unclaimed_accrued also includes live unclaimed prewarms and is *not*
        monotone: its provisional classification can change on first use.
        No request denominator or future completion information is returned.
        """
        cost = dict(self._app_cost)
        unclaimed = dict(self._app_unused_expired_gbs)
        for instance in self._instances.values():
            accrued = ((self.now-instance.state_since)
                       * self.configs[instance.app_id].memory_gb)
            cost[instance.app_id] += accrued
            if instance.speculative and not instance.used and not instance.claims:
                unclaimed[instance.app_id] += sum(instance.cost_gbs.values())+accrued
        if not math.isclose(sum(cost.values()), sum(self._cost.values()),
                            rel_tol=1e-10, abs_tol=1e-7):
            raise KernelError("Causal app and global cost meters mismatch")
        return dict(app_ids=self.app_ids, now=self.now,
            cost_gbs=tuple(cost[a] for a in self.app_ids),
            cold=tuple(self._app_cold[a] for a in self.app_ids),
            warm_miss=tuple(self._app_warm_miss[a] for a in self.app_ids),
            initialization_wait=tuple(self._app_init_wait[a] for a in self.app_ids),
            other_wait=tuple(self._app_other_wait[a] for a in self.app_ids),
            voluntary=tuple(self._app_voluntary[a] for a in self.app_ids),
            unused_expired_gbs=tuple(self._app_unused_expired_gbs[a] for a in self.app_ids),
            unclaimed_accrued_gbs=tuple(unclaimed[a] for a in self.app_ids))

    def finish(self, evaluation_end: float, max_tail: float = 86400.0):
        if self._finished:
            return self.metrics()
        if (self._closing or evaluation_end < self.now or not math.isfinite(evaluation_end)
                or not math.isfinite(max_tail) or max_tail <= 0):
            raise ValueError("Invalid fixed evaluation/tail horizon")
        if any(r.source.arrival >= evaluation_end for r in self._requests.values()):
            raise ValueError("Requests must lie in [start,T); denominator cannot be silently truncated")
        self.run_until(evaluation_end)
        self._evaluation_end = float(evaluation_end)
        self._cost_at_T = dict(self._cost)
        self._closing = True
        limit = evaluation_end+max_tail
        while self._instances or self._completed != len(self._requests):
            if not self._events or self._events[0][0] > limit:
                self._integrate(limit)
                raise TailTimeout("Full natural tail failed: incomplete requests/reservation remain; no censoring")
            when, _, _, kind, payload = heapq.heappop(self._events)
            self._integrate(when)
            self._event(kind, payload)
        self._tail_end = self.now
        self._finished = True
        self._emit("natural_tail_complete", original_requests=len(self._requests))
        result = self.metrics()
        if not math.isclose(sum(result["cost_by_state_gbs"].values()),
                            sum(sum(x["cost_gbs"].values()) for x in self._retired_rows),
                            rel_tol=1e-10, abs_tol=1e-7):
            raise KernelError("Aggregate and per-instance ledger mismatch")
        return result

    def metrics(self):
        rows = []
        for request in self._requests.values():
            if request.completed is not None:
                rows.append(dict(request_id=request.source.request_id, app_id=request.source.app_id,
                    arrival=request.source.arrival, started=request.started, completed=request.completed,
                    cold=request.cold, warm_miss=request.warm_miss,
                    initialization_wait=request.initialization_wait, other_wait=request.other_wait,
                    response=request.completed-request.source.arrival))
        denominator = len(self._requests)
        cold = sum(x["cold"] for x in rows)
        unused_cost = sum(sum(p["cost_gbs"].values()) for p in self._retired_rows
                          if p["speculative"] and p["uses"] == 0)
        return dict(finished=self._finished, original_request_denominator=denominator,
            observed_requests=self._observed, completed_requests=self._completed,
            incomplete_requests=denominator-self._completed,
            completion_rate=self._completed/denominator if denominator else 1.0,
            cold_requests=cold, cold_ratio=cold/denominator if denominator else 0.0,
            cold_claimed_requests=sum(self._app_cold.values()),
            cold_completed_requests=cold,
            warm_misses=sum(r.warm_miss for r in self._requests.values()),
            initialization_wait=sum(x["initialization_wait"] for x in rows),
            other_wait=sum(x["other_wait"] for x in rows),
            reservation_gb_seconds=sum(self._cost.values()), cost_by_state_gbs=dict(self._cost),
            cost_through_T_by_state_gbs=self._cost_at_T,
            cpu_vcpu_seconds=sum(self._cpu_cost.values()), cpu_by_state_seconds=dict(self._cpu_cost),
            remaining_reserved_gb=self.reserved_gb, evaluation_end=self._evaluation_end,
            evaluation_start=self.start,
            evaluation_duration=self._evaluation_end-self.start if self._evaluation_end is not None else None,
            natural_tail_end=self._tail_end, unused_prewarm_gb_seconds=unused_cost,
            counts=dict(self._counts), retention_episodes=dict(self._retention_episodes),
            requested_actions=list(self._actions_requested), executed_actions=list(self._actions_executed),
            completed_request_rows=rows, instance_rows=list(self._retired_rows),
            events=list(self._log), capacity_gb=self.capacity_gb,
            cpu_constraint_enabled=False, sorted_app_order=list(self.app_ids),
            mask_contract="physical_only; prior support/risk/age gates removed",
            tail_contract="no controller after T; complete requests+natural TTL+confirmed returns")


__all__ = ["Kernel", "NativeApp", "Request", "DelaySequence", "KernelError", "TailTimeout",
           "STATES", "ACTION_MAP"]

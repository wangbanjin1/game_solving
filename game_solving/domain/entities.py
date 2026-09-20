"""Immutable domain records. No configuration, filesystem or solver imports."""

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass(frozen=True)
class Bandwidth:
    ul: float = 0.0
    dl: float = 0.0

    def __add__(self, other):
        return Bandwidth(self.ul + other.ul, self.dl + other.dl)

    def __sub__(self, other):
        return Bandwidth(self.ul - other.ul, self.dl - other.dl)

    def fits(self, capacity, tolerance=0.0):
        return self.ul <= capacity.ul + tolerance and self.dl <= capacity.dl + tolerance


@dataclass(frozen=True)
class Stream:
    bitrate_kbps: float
    resolution: float
    width: int
    height: int
    rtt_ms: float
    loss_ratio: float
    stall_ratio: float
    min_kbps: float
    max_kbps: float
    buffer_ms: Optional[float] = None
    phase: str = "steady"
    jitter_ms: Optional[float] = None


@dataclass(frozen=True)
class User:
    user_id: str
    business: str
    package: str
    position: str
    tolerance: str
    profile: str
    current: Bandwidth
    streams: dict[str, Stream]
    observed_mos: Optional[float]
    target: float
    baseline: float
    contract: Bandwidth = field(default_factory=Bandwidth)
    bitrate_adaptation: bool = True
    hard_mos: bool = False
    allow_soft_degrade: bool = True
    history_mos: Optional[float] = None
    direction_targets: dict[str, float] = field(default_factory=dict)
    direction_baselines: dict[str, float] = field(default_factory=dict)
    cap: Optional[Bandwidth] = None
    total_cap_kbps: Optional[float] = None
    qoe_category: Optional[str] = None
    app_id: Optional[str] = None


@dataclass(frozen=True)
class Scene:
    scene_id: str
    model_hash: str
    capacity: Bandwidth
    users: tuple[User, ...]
    unmanaged: Bandwidth = field(default_factory=Bandwidth)
    reserve: Bandwidth = field(default_factory=Bandwidth)
    schema_version: str = "2.0"
    source: str = "synthetic"

    @property
    def available(self):
        return self.capacity - self.unmanaged - self.reserve


@dataclass(frozen=True)
class Action:
    user_id: str
    action_id: str
    bandwidth: Bandwidth
    direction_mos: dict[str, float]
    mos: Optional[float]
    experience: float
    h: float
    stability_cost: float
    basic_met: bool
    target_met: bool
    gap: float
    anchor: bool = False
    predicted_kqi: dict[str, dict] = field(default_factory=dict)
    quality_guarantee_met: bool = True
    quality_violations: tuple[str, ...] = ()
    target_evaluated: bool = True


@dataclass(frozen=True)
class InverseResult:
    feasible: bool
    bandwidth_kbps: Optional[float]
    mos: Optional[float]
    reason: Optional[str] = None


@dataclass
class SolveResult:
    scene_id: str
    solution_status: str
    stop_reason: str
    mode: str
    decisions: list[Action]
    certificates: dict
    trace: list[dict]
    iterations: int
    total_steps: int
    elapsed_ms: float
    best_iteration: int = 0
    candidate_truncated: bool = False
    violations: list[str] = field(default_factory=list)
    run_status: str = "FAILED"
    executable: bool = False
    convergence_scope: str = "evaluated_candidates"
    terminal_decisions: list[Action] = field(default_factory=list)
    returned_matches_terminal: bool = False
    output_selection: str = "best_feasible_by_policy"
    work_counts: dict = field(default_factory=dict)


def to_dict(value):
    return asdict(value)


def scene_from_dict(data):
    data = dict(data)
    if data.get("schema_version") != "2.0":
        raise ValueError("UNSUPPORTED_SCHEMA: regenerate old single-bandwidth data")
    users = []
    for record in data.pop("users"):
        record = dict(record)
        for name in ("current", "contract", "cap"):
            if record.get(name) is not None:
                record[name] = Bandwidth(**record[name])
        record["streams"] = {d: Stream(**s) for d, s in record["streams"].items()}
        users.append(User(**record))
    for name in ("capacity", "unmanaged", "reserve"):
        if name in data:
            data[name] = Bandwidth(**data[name])
    return Scene(users=tuple(users), **data)

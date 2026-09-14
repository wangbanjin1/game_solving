"""Small structural interfaces: extensions need not inherit concrete implementations."""

from typing import Protocol
from .entities import Stream, InverseResult


class ExperienceModel(Protocol):
    def forward(self, business: str, stream: Stream, budget=None) -> float: ...
    def inverse(
        self, business: str, target: float, stream: Stream, budget=None
    ) -> InverseResult: ...


class CandidateProvider(Protocol):
    def build(self, user, budget, required=()): ...
    def scope(self, user, pool, round_index): ...


class Coordinator(Protocol):
    def coordinate(self, scene, raw, pools, budget, mode, prices): ...

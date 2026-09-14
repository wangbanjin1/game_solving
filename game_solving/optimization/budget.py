"""One shared deterministic work counter; reserved verification cannot be spent by search."""

import time


class BudgetExceeded(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class Budget:
    def __init__(self, max_steps, time_ms, reserve=0, clock=time.perf_counter):
        if reserve > max_steps:
            raise ValueError("INVALID_WORK_BUDGET: final validation reserve")
        self.limit = max_steps
        self.reserve = reserve
        self.clock = clock
        self.start = clock()
        self.deadline = self.start + time_ms / 1000
        self.used = 0
        self.finalizing = False
        self.counts = {}

    def consume(self, count=1, kind="work"):
        ceiling = self.limit if self.finalizing else self.limit - self.reserve
        if self.used + count > ceiling:
            raise BudgetExceeded("MAX_TOTAL_STEPS")
        if not self.finalizing and self.clock() >= self.deadline:
            raise BudgetExceeded("TIME_BUDGET")
        self.used += count
        self.counts[kind] = self.counts.get(kind, 0) + count

    @property
    def elapsed_ms(self):
        return (self.clock() - self.start) * 1000

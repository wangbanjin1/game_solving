"""Composition root: replace a model through a registry or direct dependency injection."""

from game_solving.models.mos import MosModel
from game_solving.models.lookup import LookupMosModel
from game_solving.optimization.policy import Policy
from game_solving.optimization.solver import Solver
from game_solving.simulation.generator import DatasetGenerator


class ModelRegistry:
    def __init__(self):
        self.factories = {
            "user_formula_v2": MosModel,
            "lookup_table_v1": LookupMosModel,
        }

    def register(self, name, factory):
        if name in self.factories:
            raise ValueError("MODEL_ALREADY_REGISTERED")
        self.factories[name] = factory

    def create(self, config):
        name = config["models"]["name"]
        if name not in self.factories:
            raise ValueError("UNREGISTERED_MODEL: " + name)
        return self.factories[name](config)


class Services:
    def __init__(self, config, model=None, registry=None):
        self.config = config
        self.model = (
            model if model is not None else (registry or ModelRegistry()).create(config)
        )
        self.policy = Policy(config, self.model)

    def generator(self):
        return DatasetGenerator(self.config, self.model, self.policy)

    def solver(self):
        return Solver(self.config, self.policy)

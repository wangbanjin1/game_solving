"""Atomic JSON artifacts; an unfinished manifest never denotes a completed run."""

import json
from pathlib import Path
from dataclasses import is_dataclass, asdict


def normalize(value):
    if is_dataclass(value):
        return asdict(value)
    return value


class JsonStore:
    def __init__(self, directory):
        self.path = Path(directory)
        self.path.mkdir(parents=True, exist_ok=True)
        if any(self.path.iterdir()):
            raise ValueError("OUTPUT_NOT_EMPTY: choose a new directory")

    def write(self, name, value, lines=False):
        path = self.path / name
        temp = path.with_suffix(path.suffix + ".tmp")
        if lines:
            text = "".join(
                json.dumps(
                    normalize(row), ensure_ascii=False, sort_keys=True, allow_nan=False
                )
                + "\n"
                for row in value
            )
        else:
            text = (
                json.dumps(
                    normalize(value),
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                    allow_nan=False,
                )
                + "\n"
            )
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)


def read_jsonl(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

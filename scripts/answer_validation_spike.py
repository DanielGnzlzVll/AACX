"""Compare answer validation pipelines on a labeled sample (ADR 0005).

    python scripts/answer_validation_spike.py --ollama-url http://localhost:11434 \\
        --models qwen2.5:1.5b qwen2.5:3b

Answers are shuffled into rounds of 56 (8 players, 7 categories). Unverified
answers count as accepted, as they are in the game.
"""

import argparse
import csv
import json
import os
import random
import statistics
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "asacx.settings_test")

import django  # noqa: E402

django.setup()

from core import answer_validation  # noqa: E402
from core.models import normalize_answer  # noqa: E402

ROUND_SIZE = 56


def load_sample(path):
    with open(path) as sample:
        return [
            ((row["field"], normalize_answer(row["value"])), row["valid"] == "1")
            for row in csv.DictReader(sample)
        ]


def rounds(sample, shuffles):
    for seed in range(shuffles):
        shuffled = sample[:]
        random.Random(seed).shuffle(shuffled)
        for start in range(0, len(shuffled), ROUND_SIZE):
            yield shuffled[start : start + ROUND_SIZE]


def run_chain(validators, pairs):
    verdicts = {}
    for validator in validators:
        pending = pairs - verdicts.keys()
        if pending:
            verdicts |= validator.validate(pending)
    return verdicts


def ollama(url, path, body=None):
    data = json.dumps(body).encode() if body else None
    request = urllib.request.Request(
        f"{url}{path}", data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.load(response)


def evaluate(name, validators, sample, shuffles):
    latencies, outcomes = [], []
    for answers in rounds(sample, shuffles):
        pairs = {pair for pair, _ in answers}
        started = time.perf_counter()
        verdicts = run_chain(validators, pairs)
        latencies.append(time.perf_counter() - started)
        outcomes += [(verdicts.get(pair), label) for pair, label in answers]
    accepted = [(verdict is not False, label) for verdict, label in outcomes]
    valid = [ok for ok, label in accepted if label]
    invalid = [ok for ok, label in accepted if not label]
    p95 = statistics.quantiles(latencies, n=20)[-1] if len(latencies) > 1 else 0
    return {
        "pipeline": name,
        "accuracy": sum(ok == label for ok, label in accepted) / len(accepted),
        "false_rejects": valid.count(False) / len(valid),
        "false_accepts": invalid.count(True) / len(invalid),
        "unverified": sum(verdict is None for verdict, _ in outcomes) / len(outcomes),
        "p50_s": statistics.median(latencies),
        "p95_s": p95,
        "rounds": len(latencies),
    }


def with_fields(validator_class, attribute, fields):
    validator = validator_class()
    setattr(validator, attribute, frozenset(fields))
    return validator


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sample", default=ROOT / "scripts/answer_validation_sample.csv"
    )
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--models", nargs="*", default=[])
    parser.add_argument("--shuffles", type=int, default=3)
    parser.add_argument("--num-thread", type=int)
    args = parser.parse_args()

    sample = load_sample(args.sample)
    lexicon = answer_validation.PartialLexiconValidator()
    dictionary = answer_validation.DictionaryValidator()
    strict = answer_validation.LexiconValidator()
    pipelines = {
        "lists": [lexicon],
        "lists + dictionary (thing)": [lexicon, dictionary],
        "lists + dictionary (thing, animal, color)": [
            lexicon,
            with_fields(
                answer_validation.DictionaryValidator,
                "fields",
                ["thing", "animal", "color"],
            ),
        ],
        "strict lists + dictionary (thing)": [strict, dictionary],
        "strict lists (name, last_name, country, city) + dictionary (rest)": [
            with_fields(
                answer_validation.LexiconValidator,
                "closed_fields",
                ["name", "last_name", "country", "city"],
            ),
            with_fields(
                answer_validation.DictionaryValidator,
                "fields",
                ["thing", "animal", "color"],
            ),
        ],
    }
    results = [
        evaluate(name, v, sample, args.shuffles) for name, v in pipelines.items()
    ]

    for model in args.models:
        validator = answer_validation.OllamaValidator(
            url=args.ollama_url,
            model=model,
            timeout=600,
            options={"num_thread": args.num_thread} if args.num_thread else None,
        )
        validator.validate({("animal", "perro")})
        memory = sum(m["size"] for m in ollama(args.ollama_url, "/api/ps")["models"])
        for name, validators in {
            f"{model} only": [validator],
            f"lists + dictionary (thing) + {model}": [lexicon, dictionary, validator],
        }.items():
            result = evaluate(name, validators, sample, args.shuffles)
            result["model_memory_gb"] = memory / 1e9
            results.append(result)
            print(json.dumps(result), file=sys.stderr)
        ollama(args.ollama_url, "/api/generate", {"model": model, "keep_alive": 0})

    print(
        "| Pipeline | Accuracy | False rejects | False accepts | Unverified "
        "| p50 (s) | p95 (s) | Model RAM (GB) |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for r in results:
        memory = f"{r['model_memory_gb']:.1f}" if "model_memory_gb" in r else "-"
        print(
            f"| {r['pipeline']} | {r['accuracy']:.0%} | {r['false_rejects']:.0%} "
            f"| {r['false_accepts']:.0%} | {r['unverified']:.0%} | {r['p50_s']:.2f} "
            f"| {r['p95_s']:.2f} | {memory} |"
        )


if __name__ == "__main__":
    main()

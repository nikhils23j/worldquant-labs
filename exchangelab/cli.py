import argparse
from dataclasses import asdict
import json
from pathlib import Path
from .orders import Command
from .experiments import workload, replay, differential, benchmark, simulate, latency_sweep

def main():
    p = argparse.ArgumentParser(description="Synthetic exchange experiments")
    p.add_argument("command", choices=["generate", "replay", "validate", "benchmark", "compare-latency", "demo"])
    p.add_argument("--count", type=int, default=100000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--repetitions", type=int, default=5)
    p.add_argument("--latency", type=float, default=0)
    p.add_argument("--input", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.count < 1 or args.repetitions < 1 or args.latency < 0:
        p.error("Positive count/repetitions and nonnegative latency required")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.command == "generate":
        with args.output.open("w") as f:
            for c in workload(args.count, args.seed):
                f.write(json.dumps(asdict(c))+"\n")
        return
    if args.command == "replay":
        if args.input:
            with args.input.open() as f:
                result = replay((Command(**json.loads(line)) for line in f), check_every=1000)
        else:
            result = replay(workload(args.count, args.seed), check_every=1000)
    elif args.command == "validate": result = differential(args.count, args.seed)
    elif args.command == "benchmark": result = benchmark(args.count, args.repetitions)
    elif args.command == "compare-latency": result = latency_sweep()
    else: result = simulate(args.seed, args.latency, duration_ms=300, trace=True)
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    print(f"Saved {args.output}")

if __name__ == "__main__": main()

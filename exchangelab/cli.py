import argparse
from dataclasses import asdict
import json
from pathlib import Path
from .orders import Command
from .experiments import workload, replay, differential, differential_suite, benchmark, simulate, latency_sweep

COMMANDS = ["generate", "replay", "validate", "validate-suite", "benchmark", "compare-latency", "demo",
            "market", "latency-study"]


def main():
    p = argparse.ArgumentParser(description="Synthetic exchange experiments")
    p.add_argument("command", choices=COMMANDS)
    p.add_argument("--count", type=int, default=100000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--repetitions", type=int, default=5)
    p.add_argument("--latency", type=float, default=0, help="ms for demo/compare-latency, us for market")
    p.add_argument("--mode", choices=["taker", "passive", "off"], default="taker", help="probe mode for `market`")
    p.add_argument("--mix", choices=["v1", "v2"], default="v1", help="workload for `validate`")
    p.add_argument("--stp", choices=["none", "cancel_resting", "cancel_taker"], default="none")
    p.add_argument("--seeds", type=int, default=30, help="seeds per latency for `latency-study`")
    p.add_argument("--v2-count", type=int, default=200000, help="per-STP-mode count for `validate-suite`")
    p.add_argument("--no-quiet-wait", action="store_true", help="benchmark: do not wait for low load")
    p.add_argument("--input", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.count < 1 or args.repetitions < 1 or args.latency < 0 or args.seeds < 2:
        p.error("Positive count/repetitions, >=2 seeds and nonnegative latency required")
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
    elif args.command == "validate":
        result = differential(args.count, args.seed, mix=args.mix, stp=args.stp)
    elif args.command == "validate-suite":
        result = differential_suite(args.count, args.v2_count)
    elif args.command == "benchmark":
        result = benchmark(args.count, args.repetitions, quiet_wait=not args.no_quiet_wait)
    elif args.command == "compare-latency":
        result = latency_sweep()
    elif args.command == "market":
        from .market import run_market
        result = run_market(args.seed, probe_latency_us=int(args.latency), probe_mode=args.mode)
    elif args.command == "latency-study":
        from .latency_study import study
        result = study(args.seeds)
    else:
        result = simulate(args.seed, args.latency, duration_ms=300, trace=True)
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()

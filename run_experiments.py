# run_experiments.py
"""
Master Runner Script for SAPEX-F Simulation Experiments

Implements the nested experiment loop:
  1. Select Comparison Algorithm
  2. Select Stress Scenario
  3. Select Parameter Set
  4. Collect experiment data, store results

Usage:
    python run_experiments.py                              # Run default experiments
    python run_experiments.py --algorithms sapex random     # Specific algorithms
    python run_experiments.py --scenarios thundering_herd   # Specific scenario
    python run_experiments.py --dry-run                     # Preview without running
    python run_experiments.py --list                        # List all options
"""

import argparse
import concurrent.futures
import itertools
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from result_logger import ResultLogger


# ============================================================================
# CONFIGURATION
# ============================================================================

# All algorithms to compare
ALGORITHMS = [
    "lowest_latency",
    "lowest_hop_count",
    "random",
    "round_robin",
    "sapex",
]

# Topology files
TOPOLOGIES = {
    "small": "topology.json",
    "sciera_large": "topologies/sciera_large.json",
}

# Stress scenarios (traffic files)
SCENARIOS = {
    "thundering_herd": "scenarios/thundering_herd.json",
    "path_failure_recovery": "scenarios/path_failure_recovery.json",
    "shared_bottleneck": "scenarios/shared_bottleneck.json",
}

# ---- Parameter sets ----
# Network related
NUM_PACKETS_OPTIONS = [1000, 2000, 10000, 100000]
PACKET_SIZE_BYTES = 1500  # Fixed per spec

# Algorithm related
T_ROUND_OPTIONS_MS = [1000, 2000, 10000]    # Allocation epoch (jitter)
COOLDOWN_OPTIONS_MS = [2000, 5000, 10000]          # Expiry times for cooldown
LAMBDA_DIV_OPTIONS = [0.3, 0.5, 0.7, 1.0]         # Weight for diversity reward
POINT_BUDGET_OPTIONS = [100, 250, 500]             # Application point budgets


def _safe_label(value):
    """Convert arbitrary values into path-safe labels for file names."""
    text = str(value)
    for ch in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
        text = text.replace(ch, '_')
    return text.replace(' ', '_')


# ---- Predefined experiment sets ----
EXPERIMENT_SETS = {
    "quick": {
        "description": "Quick smoke test (1 algorithm × 1 scenario × minimal params)",
        "algorithms": ["sapex"],
        "topologies": ["small"],
        "scenarios": ["thundering_herd"],
        "num_packets": [1000],
        "t_round_ms": [2000],
        "cooldown_ms": [5000],
        "lambda_div": [0.5],
        "point_budget": [100],
    },
    "sapex_lambda_div": {
        "description": "Compare SAPEX lambda_div settings against single baseline results from other algorithms",
        "algorithms": ALGORITHMS,
        "topologies": ["sciera_large"],
        "scenarios": list(SCENARIOS.keys()),
        "num_packets": [2000],
        "t_round_ms": [2000],
        "cooldown_ms": [5000],
        "lambda_div": LAMBDA_DIV_OPTIONS,
        "point_budget": [100],
        "baseline_algorithms": ["lowest_latency", "lowest_hop_count", "random", "round_robin"],
        "baseline_lambda_div": 0.5,
    },
    "sapex_t_round": {
        "description": "Compare SAPEX t_round settings against single baseline results from other algorithms",
        "algorithms": ALGORITHMS,
        "topologies": ["sciera_large"],
        "scenarios": list(SCENARIOS.keys()),
        "num_packets": [2000],
        "t_round_ms": T_ROUND_OPTIONS_MS,
        "cooldown_ms": [5000],
        "lambda_div": [0.5],
        "point_budget": [100],
        "baseline_algorithms": ["lowest_latency", "lowest_hop_count", "random", "round_robin"],
        "baseline_t_round_ms": 2000,
    },
    "number_of_packets": {
        "description": "Compare all algorithms at 1k, 10k, and 100k packet counts",
        "algorithms": ALGORITHMS,
        "topologies": ["sciera_large"],
        "scenarios": list(SCENARIOS.keys()),
        "num_packets": [1000, 10000, 100000],
        "t_round_ms": [2000],
        "cooldown_ms": [5000],
        "lambda_div": [0.5],
        "point_budget": [100],
    },
    "full_sweep": {
        "description": "Full parameter sweep (WARNING: very many combinations!)",
        "algorithms": ALGORITHMS,
        "topologies": ["sciera_large"],
        "scenarios": list(SCENARIOS.keys()),
        "num_packets": NUM_PACKETS_OPTIONS,
        "t_round_ms": T_ROUND_OPTIONS_MS,
        "cooldown_ms": COOLDOWN_OPTIONS_MS,
        "lambda_div": LAMBDA_DIV_OPTIONS,
        "point_budget": POINT_BUDGET_OPTIONS,
    },
}


# ============================================================================
# EXPERIMENT RUNNER
# ============================================================================

class ExperimentRunner:
    def __init__(
        self,
        output_base_dir="results",
        dry_run=False,
        verbose=True,
        timeout_sec=600,
        max_workers=1,
        run_label=None,
    ):
        self.output_base_dir = Path(output_base_dir)
        self.dry_run = dry_run
        self.verbose = verbose
        self.timeout_sec = timeout_sec
        self.max_workers = max_workers
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_label = _safe_label(run_label) if run_label else None
        self.run_id = f"{self.timestamp}_{self.run_label}" if self.run_label else self.timestamp
        self.run_results = []
        self._results_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self.logger = ResultLogger(base_dir=str(self.output_base_dir))

    def log(self, msg):
        if self.verbose:
            with self._log_lock:
                print(msg)

    def generate_experiment_configs(
        self,
        algorithms,
        topologies,
        scenarios,
        num_packets_list,
        t_round_list,
        cooldown_list,
        lambda_div_list,
        point_budget_list,
        baseline_algorithms=None,
        baseline_lambda_div=None,
        baseline_t_round_ms=None,
    ):
        """
        Generate all experiment configurations using the nested loop:
          1. Algorithm
          2. Scenario
          3. Parameter set
        """
        configs = []
        baseline_algorithms = set(baseline_algorithms or [])

        # Loop 1: Algorithms
        for algo in algorithms:
            if algo in baseline_algorithms:
                algo_lambda_values = [baseline_lambda_div if baseline_lambda_div is not None else lambda_div_list[0]]
                algo_t_round_values = [baseline_t_round_ms if baseline_t_round_ms is not None else t_round_list[0]]
            else:
                algo_lambda_values = lambda_div_list
                algo_t_round_values = t_round_list

            # Loop 2: Topologies
            for topo_name in topologies:
                topo_file = TOPOLOGIES.get(topo_name, topo_name)

                # Loop 3: Stress Scenarios
                for scenario_name in scenarios:
                    traffic_file = SCENARIOS.get(scenario_name, scenario_name)

                    # Loop 4: Parameter combinations
                    # For non-SAPEX algorithms, many params are irrelevant—
                    # but we still run them to have comparable baselines
                    param_combos = list(itertools.product(
                        num_packets_list,
                        algo_t_round_values,
                        cooldown_list,
                        algo_lambda_values,
                        point_budget_list,
                    ))

                    for (n_pkts, t_round, cooldown, lam_div, budget) in param_combos:
                        scenario_label = _safe_label(scenario_name)
                        topo_label = _safe_label(topo_name)
                        exp_name = (
                            f"{algo}__{scenario_label}__{topo_label}"
                            f"__np{n_pkts}_tr{t_round}_cd{cooldown}"
                            f"_ld{lam_div}_b{budget}"
                        )

                        config = {
                            "topology": topo_file,
                            "traffic": traffic_file,
                            "algorithm": algo,
                            "scenario": scenario_name,
                            "output_dir": str(
                                self.output_base_dir / self.run_id / scenario_name / algo
                            ),
                            "experiment_name": exp_name,
                            "parameters": {
                                "num_packets": n_pkts,
                                "packet_size_bytes": PACKET_SIZE_BYTES,
                                "t_round_ms": t_round,
                                "cooldown_ms": cooldown,
                                "lambda_div": lam_div,
                                "point_budget": budget,
                            },
                        }
                        configs.append(config)

        return configs

    def write_config_file(self, config):
        """Write a temporary config JSON for main.py --config."""
        config_dir = Path(config["output_dir"])
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / f"{config['experiment_name']}_config.json"
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        return str(config_path)

    def run_single(self, config, index, total):
        """Run a single experiment."""
        name = config["experiment_name"]
        self.log(f"\n[{index}/{total}] {name}")
        self.log(f"  Algorithm:  {config['algorithm']}")
        self.log(f"  Scenario:   {config['scenario']}")
        self.log(f"  Topology:   {config['topology']}")
        self.log(f"  Params:     np={config['parameters']['num_packets']}, "
                 f"t_round={config['parameters']['t_round_ms']}, "
                 f"cd={config['parameters']['cooldown_ms']}, "
                 f"ld={config['parameters']['lambda_div']}, "
                 f"budget={config['parameters']['point_budget']}")

        if self.dry_run:
            self.log(f"  [DRY RUN] skipped")
            return {"experiment": name, "status": "dry_run"}

        config_path = self.write_config_file(config)

        cmd = [
            sys.executable, "main.py",
            "--config", config_path,
        ]

        try:
            # Stream subprocess output live so long simulations do not look stuck.
            result = subprocess.run(
                cmd,
                text=True,
                cwd=str(Path(__file__).parent),
                timeout=self.timeout_sec,
            )

            if result.returncode == 0:
                self.log(f"  [OK] Completed")
                status = "success"
            else:
                self.log(f"  [FAIL] Failed (exit {result.returncode})")
                status = "failed"

            return {
                "experiment": name,
                "status": status,
                "returncode": result.returncode,
            }

        except subprocess.TimeoutExpired:
            self.log(f"  [TIMEOUT] Timeout (>{self.timeout_sec}s)")
            return {"experiment": name, "status": "timeout"}
        except Exception as e:
            self.log(f"  [ERROR] Error: {e}")
            return {"experiment": name, "status": "error", "error": str(e)}

    def run_experiments(self, configs):
        """Run all experiment configs, in parallel when max_workers > 1."""
        total = len(configs)

        self.log(f"\n{'='*70}")
        self.log(f"  SAPEX-F Experiment Runner")
        self.log(f"  Timestamp:    {self.timestamp}")
        if self.run_label:
            self.log(f"  Setting:      {self.run_label}")
        self.log(f"  Output:       {self.output_base_dir / self.run_id}")
        self.log(f"  Total runs:   {total}")
        self.log(f"  Workers:      {self.max_workers}")
        self.log(f"  Dry run:      {self.dry_run}")
        self.log(f"{'='*70}")

        def run_and_collect(args):
            i, config = args
            result = self.run_single(config, i, total)
            with self._results_lock:
                self.run_results.append(result)
            return result

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            list(executor.map(run_and_collect, enumerate(configs, 1)))

        self.save_summary()
        self.aggregate_all_stats()
        self.print_summary()

    def run_experiments_with_plots(self, configs, plots_dir="plots", skip_plots=False):
        """Run all experiments and automatically generate plots afterward."""
        self.run_experiments(configs)
        if not skip_plots:
            self.plot_results(plots_dir)

    def save_summary(self):
        """Save experiment run summary."""
        if self.dry_run:
            return

        summary_dir = self.output_base_dir / self.run_id
        summary_dir.mkdir(parents=True, exist_ok=True)
        summary_path = summary_dir / "experiment_summary.json"

        summary = {
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "setting": self.run_label,
            "total_experiments": len(self.run_results),
            "successful": sum(1 for r in self.run_results if r["status"] == "success"),
            "failed": sum(1 for r in self.run_results if r["status"] == "failed"),
            "timeout": sum(1 for r in self.run_results if r["status"] == "timeout"),
            "errors": sum(1 for r in self.run_results if r["status"] == "error"),
            "experiments": self.run_results,
        }

        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2)

    def aggregate_all_stats(self):
        """Aggregate all stats.csv and fairness_summary.csv files into master CSVs."""
        if self.dry_run:
            return

        run_root = self.output_base_dir / self.run_id
        all_run_dirs = []

        # Walk through all result directories looking for stats.csv
        for dirpath, dirnames, filenames in os.walk(run_root):
            if "stats.csv" in filenames:
                all_run_dirs.append(dirpath)

        if all_run_dirs:
            # Aggregate main stats
            aggregate_path = str(run_root / "all_results.csv")
            ResultLogger.aggregate_stats_csvs(all_run_dirs, aggregate_path)
            self.log(f"\n  Aggregated CSV: {aggregate_path}")
            
            # Aggregate fairness data
            fairness_aggregate_path = str(run_root / "all_fairness_results.csv")
            ResultLogger.aggregate_fairness_csvs(all_run_dirs, fairness_aggregate_path)
            if Path(fairness_aggregate_path).exists():
                self.log(f"  Aggregated Fairness CSV: {fairness_aggregate_path}")

    def plot_results(self, plots_dir="plots"):
        """Automatically generate plots from aggregated results."""
        if self.dry_run:
            return

        run_root = self.output_base_dir / self.run_id
        all_results_csv = run_root / "all_results.csv"

        if not all_results_csv.exists():
            self.log(f"  [SKIP PLOTS] No all_results.csv found at {all_results_csv}")
            return

        # Plot main metrics
        cmd = [
            sys.executable, "plot_results.py",
            "--input", str(all_results_csv),
            "--out-dir", plots_dir,
        ]

        try:
            self.log(f"\n  [PLOTS] Starting plot generation...")
            result = subprocess.run(
                cmd,
                text=True,
                cwd=str(Path(__file__).parent),
                capture_output=False,
                timeout=300,  # 5 minute timeout for plotting
            )
            if result.returncode == 0:
                self.log(f"  [PLOTS] Completed successfully")
            else:
                self.log(f"  [PLOTS] Failed (exit {result.returncode})")
        except subprocess.TimeoutExpired:
            self.log(f"  [PLOTS] Timeout (>300s)")
        except Exception as e:
            self.log(f"  [PLOTS] Error: {e}")
        
        # Plot per-link fairness if available
        fairness_csv = run_root / "all_fairness_results.csv"
        if fairness_csv.exists():
            cmd_fairness = [
                sys.executable, "plot_results.py",
                "--input", str(fairness_csv),
                "--out-dir", plots_dir,
            ]
            try:
                self.log(f"  [PLOTS] Generating fairness plots...")
                result = subprocess.run(
                    cmd_fairness,
                    text=True,
                    cwd=str(Path(__file__).parent),
                    capture_output=False,
                    timeout=300,
                )
                if result.returncode == 0:
                    self.log(f"  [PLOTS] Fairness plots completed successfully")
                else:
                    self.log(f"  [PLOTS] Fairness plots failed (exit {result.returncode})")
            except subprocess.TimeoutExpired:
                self.log(f"  [PLOTS] Fairness plots timeout (>300s)")
            except Exception as e:
                self.log(f"  [PLOTS] Fairness plots error: {e}")

    def print_summary(self):
        """Print final summary."""
        success = sum(1 for r in self.run_results if r["status"] == "success")
        failed = sum(1 for r in self.run_results if r["status"] == "failed")
        timeout = sum(1 for r in self.run_results if r["status"] == "timeout")
        errors = sum(1 for r in self.run_results if r["status"] == "error")
        dry = sum(1 for r in self.run_results if r["status"] == "dry_run")

        self.log(f"\n{'='*70}")
        self.log(f"  SUMMARY")
        self.log(f"{'='*70}")
        self.log(f"  Total:      {len(self.run_results)}")
        self.log(f"  Successful: {success}")
        self.log(f"  Failed:     {failed}")
        self.log(f"  Timeout:    {timeout}")
        self.log(f"  Errors:     {errors}")
        if dry:
            self.log(f"  Dry-run:    {dry}")
        self.log(f"{'='*70}")


# ============================================================================
# CLI HELPERS
# ============================================================================

def list_options():
    """Print all available options."""
    print("\n" + "="*70)
    print("  AVAILABLE EXPERIMENT OPTIONS")
    print("="*70)

    print("\n  ALGORITHMS:")
    for algo in ALGORITHMS:
        print(f"    - {algo}")

    print("\n  TOPOLOGIES:")
    for name, path in TOPOLOGIES.items():
        print(f"    - {name}: {path}")

    print("\n  STRESS SCENARIOS:")
    for name, path in SCENARIOS.items():
        print(f"    - {name}: {path}")

    print("\n  PARAMETER RANGES:")
    print(f"    num_packets:   {NUM_PACKETS_OPTIONS}")
    print(f"    packet_size:   {PACKET_SIZE_BYTES} bytes (fixed)")
    print(f"    t_round_ms:    {T_ROUND_OPTIONS_MS}")
    print(f"    cooldown_ms:   {COOLDOWN_OPTIONS_MS}")
    print(f"    lambda_div:    {LAMBDA_DIV_OPTIONS}")
    print(f"    point_budget:  {POINT_BUDGET_OPTIONS}")

    print("\n  PREDEFINED EXPERIMENT SETS:")
    for name, cfg in EXPERIMENT_SETS.items():
        baseline_algorithms = set(cfg.get("baseline_algorithms", []))
        varying_algorithms = [algo for algo in cfg["algorithms"] if algo not in baseline_algorithms]
        baseline_count = len(baseline_algorithms)
        varying_count = len(varying_algorithms)

        baseline_t_round_count = 1 if cfg.get("baseline_t_round_ms") is not None else len(cfg["t_round_ms"])
        baseline_lambda_count = 1 if cfg.get("baseline_lambda_div") is not None else len(cfg["lambda_div"])

        baseline_combos = (
            baseline_count *
            len(cfg["topologies"]) *
            len(cfg["scenarios"]) *
            len(cfg["num_packets"]) *
            baseline_t_round_count *
            len(cfg["cooldown_ms"]) *
            baseline_lambda_count *
            len(cfg["point_budget"])
        )
        varying_combos = (
            varying_count *
            len(cfg["topologies"]) *
            len(cfg["scenarios"]) *
            len(cfg["num_packets"]) *
            len(cfg["t_round_ms"]) *
            len(cfg["cooldown_ms"]) *
            len(cfg["lambda_div"]) *
            len(cfg["point_budget"])
        )
        n_combos = baseline_combos + varying_combos
        print(f"    - {name} ({n_combos} runs): {cfg['description']}")

    print("\n  OUTPUT & PLOTTING OPTIONS:")
    print("    --output-dir <path>     Base directory for results (default: results)")
    print("    --plots-dir <path>      Directory for generated plots (default: plots)")
    print("    --no-plot               Skip automatic plot generation after experiments")
    print("                            (useful for headless/batch runs)")

    print()


def _load_json_if_exists(path):
    """Return parsed JSON for an existing file path, else None."""
    if not path:
        return None

    p = Path(path)
    if not p.is_file():
        return None

    try:
        with open(p, 'r') as f:
            return json.load(f)
    except Exception:
        return None


def _apply_scenario_file_defaults(
    scenarios,
    algorithms,
    topologies,
    num_packets,
    t_round,
    cooldown,
    lambda_div,
    budget,
    cli_args,
):
    """
    Apply defaults from a single config-style scenario file when CLI did not
    explicitly provide those values.

    Config-style scenario files are JSON files with keys like topology,
    traffic, algorithm, and parameters.
    """
    if not scenarios or len(scenarios) != 1:
        return algorithms, topologies, num_packets, t_round, cooldown, lambda_div, budget

    scenario_data = _load_json_if_exists(scenarios[0])
    if not isinstance(scenario_data, dict):
        return algorithms, topologies, num_packets, t_round, cooldown, lambda_div, budget

    # Heuristic: treat as config-style scenario only when it looks like one.
    if "topology" not in scenario_data or "traffic" not in scenario_data:
        return algorithms, topologies, num_packets, t_round, cooldown, lambda_div, budget

    params = scenario_data.get("parameters", {})

    if cli_args.algorithms is None and scenario_data.get("algorithm"):
        algorithms = [scenario_data["algorithm"]]

    if cli_args.topologies is None and scenario_data.get("topology"):
        topologies = [scenario_data["topology"]]

    if cli_args.num_packets is None and params.get("num_packets") is not None:
        num_packets = [params["num_packets"]]

    if cli_args.t_round is None and params.get("t_round_ms") is not None:
        t_round = [params["t_round_ms"]]

    if cli_args.cooldown is None and params.get("cooldown_ms") is not None:
        cooldown = [params["cooldown_ms"]]

    # Accept both lambda_div and legacy lambda key.
    if cli_args.lambda_div is None:
        lam_value = params.get("lambda_div", params.get("lambda"))
        if lam_value is not None:
            lambda_div = [lam_value]

    if cli_args.budget is None and params.get("point_budget") is not None:
        budget = [params["point_budget"]]

    return algorithms, topologies, num_packets, t_round, cooldown, lambda_div, budget


def _build_run_label(args) -> str:
    """Build a short setting label used in top-level result folder names."""
    if args.preset:
        return args.preset
    return "custom"


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Master Runner for SAPEX-F Simulation Experiments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_experiments.py --preset quick              # Quick smoke test
  python run_experiments.py --preset sapex_lambda_div --dry-run
  python run_experiments.py --preset sapex_t_round --dry-run
  python run_experiments.py --preset number_of_packets --dry-run

  # Custom selection:
  python run_experiments.py \\
      --algorithms sapex lowest_latency round_robin \\
      --scenarios thundering_herd shared_bottleneck \\
      --topologies sciera_large \\
      --num-packets 1000 5000 \\
      --t-round 2000 5000 \\
      --cooldown 5000 \\
      --lambda-div 0.5 \\
      --budget 100
        """,
    )

    # Predefined sets
    parser.add_argument("--preset", type=str, choices=list(EXPERIMENT_SETS.keys()),
                        help="Use a predefined experiment set")

    # Custom selections
    parser.add_argument("--algorithms", nargs="+", choices=ALGORITHMS, default=None)
    parser.add_argument("--scenarios", nargs="+", default=None,
                        help="Scenario names, scenario JSON paths, or config-style scenario files")
    parser.add_argument("--topologies", nargs="+", default=None,
                        help="Topology names or custom topology JSON paths")

    # Parameter overrides
    parser.add_argument("--num-packets", nargs="+", type=int, default=None)
    parser.add_argument("--t-round", nargs="+", type=int, default=None)
    parser.add_argument("--cooldown", nargs="+", type=int, default=None)
    parser.add_argument("--lambda-div", nargs="+", type=float, default=None)
    parser.add_argument("--budget", nargs="+", type=int, default=None)

    # General options
    parser.add_argument("--output-dir", type=str, default="results",
                        help="Base directory for results (default: results)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview experiments without running them")
    parser.add_argument("--list", action="store_true", dest="list_options",
                        help="List all available options and exit")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress verbose output")
    parser.add_argument("--timeout-sec", type=int, default=600,
                        help="Per-experiment timeout in seconds (default: 600)")
    parser.add_argument("--workers", type=int, default=1,
                        help="Number of parallel experiment workers (default: 1)")
    parser.add_argument("--no-plot", action="store_true",
                        help="Skip automatic plot generation after experiments")
    parser.add_argument("--plots-dir", type=str, default="plots",
                        help="Directory for generated plots (default: plots)")

    args = parser.parse_args()

    if args.list_options:
        list_options()
        return 0

    # Determine experiment parameters
    if args.preset:
        preset = EXPERIMENT_SETS[args.preset]
        algorithms = args.algorithms or preset["algorithms"]
        topologies = args.topologies or preset["topologies"]
        scenarios_ = args.scenarios or preset["scenarios"]
        num_packets = args.num_packets or preset["num_packets"]
        t_round = args.t_round or preset["t_round_ms"]
        cooldown = args.cooldown or preset["cooldown_ms"]
        lambda_div = args.lambda_div or preset["lambda_div"]
        budget = args.budget or preset["point_budget"]
    else:
        # Default to a quick comparison if nothing specified
        algorithms = args.algorithms or ["sapex"]
        topologies = args.topologies or ["small"]
        scenarios_ = args.scenarios or ["thundering_herd"]
        num_packets = args.num_packets or [1000]
        t_round = args.t_round or [2000]
        cooldown = args.cooldown or [5000]
        lambda_div = args.lambda_div or [0.5]
        budget = args.budget or [100]

    # If a config-style scenario file is provided (for example scenario_B.json),
    # use its topology/algorithm/parameter defaults unless CLI overrides them.
    algorithms, topologies, num_packets, t_round, cooldown, lambda_div, budget = _apply_scenario_file_defaults(
        scenarios_,
        algorithms,
        topologies,
        num_packets,
        t_round,
        cooldown,
        lambda_div,
        budget,
        args,
    )

    runner = ExperimentRunner(
        output_base_dir=args.output_dir,
        dry_run=args.dry_run,
        verbose=not args.quiet,
        timeout_sec=args.timeout_sec,
        max_workers=args.workers,
        run_label=_build_run_label(args),
    )

    configs = runner.generate_experiment_configs(
        algorithms=algorithms,
        topologies=topologies,
        scenarios=scenarios_,
        num_packets_list=num_packets,
        t_round_list=t_round,
        cooldown_list=cooldown,
        lambda_div_list=lambda_div,
        point_budget_list=budget,
        baseline_algorithms=preset.get("baseline_algorithms") if args.preset else None,
        baseline_lambda_div=preset.get("baseline_lambda_div") if args.preset else None,
        baseline_t_round_ms=preset.get("baseline_t_round_ms") if args.preset else None,
    )

    if not configs:
        print("No experiment configurations generated. Check your arguments.")
        return 1

    runner.run_experiments_with_plots(
        configs,
        plots_dir=args.plots_dir,
        skip_plots=args.no_plot,
    )
    return 0


if __name__ == "__main__":
    exit(main())

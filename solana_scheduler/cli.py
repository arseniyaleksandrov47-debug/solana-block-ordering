import argparse
import json
import os
import sys
from typing import Any, Dict, List

from .block_extractor import parse_block_transactions
from .prober import TransactionProber
from .rpc import SolanaRpcClient
from .statistical_metrics import BlockOrderingMetrics
from .validator_registry import ValidatorRegistry


def cmd_discover(args):
    rpc = SolanaRpcClient(endpoint=args.rpc_url)
    registry = ValidatorRegistry(rpc)
    print("=" * 70)
    print("SOLANA MAINNET VALIDATOR CLIENT SOFTWARE DISCOVERY")
    print("=" * 70)

    families: Dict[str, List[Dict[str, Any]]] = {}
    for node in registry.nodes_by_pubkey.values():
        info = registry.get_node_info(node["pubkey"])
        fam = info["family"]
        families.setdefault(fam, []).append(info)

    for fam, nodes in sorted(families.items(), key=lambda x: -len(x[1])):
        sample = nodes[0]
        print(f"\n[Family: {fam}] Total Nodes: {len(nodes)}")
        print(f"  Sample Client ID : {sample['client_id']}")
        print(f"  Sample Version   : {sample['version']}")
        print(f"  Sample Node Key  : {sample['pubkey']}")


def cmd_analyze(args):
    rpc = SolanaRpcClient(endpoint=args.rpc_url)
    registry = ValidatorRegistry(rpc)
    os.makedirs(args.output_dir, exist_ok=True)
    raw_blocks_dir = os.path.join(args.output_dir, "raw_blocks")
    os.makedirs(raw_blocks_dir, exist_ok=True)

    print("=" * 70)
    print("SOLANA TRANSACTION ORDERING MEASUREMENT & ANALYSIS")
    print(f"Target: Analyzing 6+ validators across >= 3 distinct client forks")
    print("=" * 70)

    current_slot = rpc.get_slot()
    print(f"Current Slot: {current_slot}")

    # Inspect recent slots
    lookback = 600
    start_slot = current_slot - lookback
    print(f"Scanning leader schedule for slots {start_slot} .. {current_slot}...")
    leader_map = registry.find_leaders_by_family(start_slot, lookback)

    # We want to select at least 6 validators spanning at least 3 distinct families
    selected_samples: List[Dict[str, Any]] = []
    seen_validators = set()

    # Prioritize balanced multi-family sampling
    families_available = list(leader_map.keys())
    print(f"Families available in scan window: {families_available}")

    # Round-robin select from each family to ensure diverse multi-client coverage
    while len(selected_samples) < args.sample_size:
        added_in_round = 0
        for fam in families_available:
            for cand in leader_map.get(fam, []):
                pubkey = cand["leader"]
                if pubkey not in seen_validators:
                    seen_validators.add(pubkey)
                    selected_samples.append(cand)
                    added_in_round += 1
                    break
            if len(selected_samples) >= args.sample_size:
                break
        if added_in_round == 0:
            break

    print(f"\nSelected {len(selected_samples)} target validator slots spanning "
          f"{len(set(s['info']['family'] for s in selected_samples))} distinct client families.\n")

    results = []

    for item in selected_samples:
        slot = item["slot"]
        info = item["info"]
        pubkey = item["leader"]
        family = info["family"]
        version = info["version"]

        print(f"Fetching Slot {slot} (Leader: {pubkey[:12]}... | {family} v{version})...")
        block_data = rpc.get_block(slot)
        if not block_data:
            print(f"  Slot {slot} was skipped or unavailable.")
            continue

        # Save raw block excerpt for judge verifiability
        raw_path = os.path.join(raw_blocks_dir, f"block_{slot}_{pubkey[:8]}.json")
        sanitized_excerpt = {
            "slot": slot,
            "blockhash": block_data.get("blockhash"),
            "parent_slot": block_data.get("parentSlot"),
            "block_time": block_data.get("blockTime"),
            "leader": pubkey,
            "client_family": family,
            "client_version": version,
            "transactions_count": len(block_data.get("transactions", [])),
            "sample_signatures": [
                t.get("transaction", {}).get("signatures", [""])[0]
                for t in block_data.get("transactions", [])[:20]
            ],
        }
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(sanitized_excerpt, f, indent=2)

        # Parse transactions & compute statistical ordering metrics
        parsed_txs = parse_block_transactions(block_data)
        metrics_calc = BlockOrderingMetrics(parsed_txs)
        metrics = metrics_calc.compute()

        entry = {
            "slot": slot,
            "leader": pubkey,
            "client_family": family,
            "client_version": version,
            "metrics": metrics,
        }
        results.append(entry)

        tau = metrics.get("kendall_priority_tau", "N/A")
        rho = metrics.get("spearman_priority_rho", "N/A")
        top10_pos = metrics.get("avg_top_10_pct_position", "N/A")
        jito_pos = metrics.get("avg_jito_position_pct", "N/A")
        print(f"  -> Total: {metrics.get('total_txs')} txs | Non-vote: {metrics.get('non_vote_txs')} | "
              f"Kendall's Tau: {tau} | Spearman: {rho} | Top10% Pos: {top10_pos} | Jito Pos: {jito_pos}")

    # Output aggregated results
    summary_path = os.path.join(args.output_dir, "processed_metrics.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 70)
    print("ANALYSIS COMPLETE")
    print(f"Metrics saved to: {summary_path}")
    print(f"Raw block proofs saved to: {raw_blocks_dir}")
    print("=" * 70)


def cmd_probe(args):
    rpc = SolanaRpcClient(endpoint=args.rpc_url)
    prober = TransactionProber(
        rpc=rpc,
        dry_run=not args.live,
        spend_cap_sol=args.spend_cap,
    )
    print(f"Running Prober (Dry-Run: {prober.dry_run}, Spend Cap: {args.spend_cap} SOL)...")
    rec = prober.probe_leader_window(
        target_slot=1234567,
        leader_pubkey="TestValidatorPubkey11111111111111111111111",
        priority_fee_micro_lamports=args.priority_fee,
    )
    print("Probe output:", rec)


def main():
    parser = argparse.ArgumentParser(description="Solana Validator Transaction Ordering & Scheduler Research Suite")
    parser.add_argument("--rpc-url", default=os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"))

    subparsers = parser.add_subparsers(dest="command", required=True)

    # discover
    p_disc = subparsers.add_parser("discover", help="Discover active cluster nodes and software versions")
    p_disc.set_defaults(func=cmd_discover)

    # analyze
    p_ana = subparsers.add_parser("analyze", help="Measure transaction ordering across validator client software")
    p_ana.add_argument("--sample-size", type=int, default=10, help="Number of validator slots to analyze (min 6)")
    p_ana.add_argument("--output-dir", default="data", help="Directory to save raw blocks and metrics")
    p_ana.set_defaults(func=cmd_analyze)

    # probe
    p_prb = subparsers.add_parser("probe", help="Run synthetic prober with spend-cap guard")
    p_prb.add_argument("--live", action="store_true", help="Enable live broadcast (requires PROBE_PRIVATE_KEY)")
    p_prb.add_argument("--confirm-spend", action="store_true", help="Confirm real SOL fee spending")
    p_prb.add_argument("--spend-cap", type=float, default=0.05, help="Maximum SOL spend cap (default 0.05)")
    p_prb.add_argument("--priority-fee", type=int, default=10_000, help="Priority fee in micro-lamports/CU")
    p_prb.set_defaults(func=cmd_probe)

    parsed_args = parser.parse_args()
    parsed_args.func(parsed_args)


if __name__ == "__main__":
    main()

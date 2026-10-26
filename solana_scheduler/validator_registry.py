from typing import Any, Dict, List, Optional
from .rpc import SolanaRpcClient


class ValidatorRegistry:
    """Discovers, tracks, and categorizes Solana Mainnet validators by client software and fork."""

    def __init__(self, rpc: SolanaRpcClient):
        self.rpc = rpc
        self.nodes_by_pubkey: Dict[str, Dict[str, Any]] = {}
        self._refresh_nodes()

    def _refresh_nodes(self):
        nodes = self.rpc.get_cluster_nodes()
        self.nodes_by_pubkey = {n["pubkey"]: n for n in nodes if "pubkey" in n}

    def get_node_info(self, pubkey: str) -> Dict[str, Any]:
        node = self.nodes_by_pubkey.get(pubkey)
        if not node:
            return {
                "pubkey": pubkey,
                "client_id": "Unknown",
                "version": "Unknown",
                "family": "Unknown",
            }

        client_id = node.get("clientId") or "Agave"
        version = node.get("version") or "Unknown"

        # Categorize software family
        if "Jito" in client_id:
            family = "Jito-Solana"
        elif "Firedancer" in client_id:
            family = "Firedancer"
        elif "Harmonic" in client_id:
            family = "Harmonic-Agave"
        elif "Bam" in client_id:
            family = "Agave-BAM"
        elif "Agave" in client_id:
            family = "Agave-Standard"
        else:
            family = f"Fork-{client_id}"

        return {
            "pubkey": pubkey,
            "client_id": client_id,
            "version": version,
            "family": family,
            "feature_set": node.get("featureSet"),
            "gossip": node.get("gossip"),
        }

    def find_leaders_by_family(self, start_slot: int, limit: int = 200) -> Dict[str, List[Dict[str, Any]]]:
        """Maps upcoming/recent slot leaders to their validator client family."""
        leaders = self.rpc.get_slot_leaders(start_slot, limit)
        categorized: Dict[str, List[Dict[str, Any]]] = {}

        for idx, leader_pubkey in enumerate(leaders):
            slot = start_slot + idx
            info = self.get_node_info(leader_pubkey)
            family = info["family"]

            if family not in categorized:
                categorized[family] = []

            categorized[family].append({
                "slot": slot,
                "leader": leader_pubkey,
                "info": info
            })

        return categorized

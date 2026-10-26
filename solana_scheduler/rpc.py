import json
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional


DEFAULT_MAINNET_RPCS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
]


class SolanaRpcClient:
    """Robust, dependency-free Solana JSON-RPC client with failover and rate-limit backoff."""

    def __init__(self, endpoint: Optional[str] = None, fallback_endpoints: Optional[List[str]] = None):
        self.endpoints = [endpoint] if endpoint else []
        self.endpoints.extend(fallback_endpoints or DEFAULT_MAINNET_RPCS)
        # Deduplicate while preserving order
        seen = set()
        self.endpoints = [x for x in self.endpoints if not (x in seen or seen.add(x))]
        self.current_idx = 0
        self._request_id = 1

    @property
    def current_endpoint(self) -> str:
        return self.endpoints[self.current_idx]

    def _rotate_endpoint(self):
        self.current_idx = (self.current_idx + 1) % len(self.endpoints)

    def call(self, method: str, params: Optional[List[Any]] = None, max_retries: int = 4) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or [],
        }
        self._request_id += 1
        data_bytes = json.dumps(payload).encode("utf-8")

        delay = 0.5
        last_error = None

        for attempt in range(max_retries):
            endpoint = self.current_endpoint
            req = urllib.request.Request(
                endpoint,
                data=data_bytes,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "SolanaSchedulerProbe/1.0",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    if "error" in resp_data:
                        err = resp_data["error"]
                        # Handle specific Solana RPC error codes
                        raise RuntimeError(f"RPC Error ({err.get('code')}): {err.get('message')}")
                    return resp_data.get("result")
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError) as e:
                last_error = e
                # Check for rate limit 429
                if isinstance(e, urllib.error.HTTPError) and e.code == 429:
                    time.sleep(delay * 2)
                    self._rotate_endpoint()
                else:
                    self._rotate_endpoint()
                    time.sleep(delay)
                delay *= 1.5

        raise RuntimeError(f"Failed to execute RPC method '{method}' after {max_retries} attempts: {last_error}")

    def get_slot(self) -> int:
        return self.call("getSlot", [{"commitment": "confirmed"}])

    def get_cluster_nodes(self) -> List[Dict[str, Any]]:
        return self.call("getClusterNodes")

    def get_slot_leaders(self, start_slot: int, limit: int = 100) -> List[str]:
        return self.call("getSlotLeaders", [start_slot, limit])

    def get_block(self, slot: int, max_supported_version: int = 1) -> Optional[Dict[str, Any]]:
        try:
            return self.call(
                "getBlock",
                [
                    slot,
                    {
                        "encoding": "json",
                        "transactionDetails": "full",
                        "rewards": False,
                        "maxSupportedTransactionVersion": max_supported_version,
                    },
                ],
            )
        except RuntimeError as e:
            # If block was skipped or cleaned up by RPC
            if "was skipped" in str(e).lower() or "not available" in str(e).lower():
                return None
            raise

import struct
from typing import Any, Dict, List, Optional, Set, Tuple


B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
B58_MAP = {c: i for i, c in enumerate(B58_ALPHABET)}

COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
VOTE_PROGRAM = "Vote111111111111111111111111111111111111111"

# Recognized Jito Tip Payment accounts
JITO_TIP_ACCOUNTS: Set[str] = {
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
}


def b58decode(s: str) -> bytes:
    """Zero-dependency Base58 decoder."""
    n = 0
    for char in s:
        if char not in B58_MAP:
            raise ValueError(f"Invalid base58 character: {char}")
        n = n * 58 + B58_MAP[char]
    res = []
    while n > 0:
        res.append(n % 256)
        n //= 256
    res = bytes(reversed(res))
    pad = 0
    for c in s:
        if c == "1":
            pad += 1
        else:
            break
    return b"\x00" * pad + res


class ParsedTransaction:
    """Normalized transaction ordering and priority fee metadata."""

    def __init__(
        self,
        block_index: int,
        signature: str,
        fee: int,
        cu_price: int,
        cu_limit: int,
        cu_consumed: int,
        is_vote: bool,
        is_jito_bundle: bool,
        write_locks: List[str],
        read_locks: List[str],
        err: Optional[Any],
    ):
        self.block_index = block_index
        self.signature = signature
        self.fee = fee
        self.cu_price = cu_price  # micro-lamports per CU
        self.cu_limit = cu_limit
        self.cu_consumed = cu_consumed
        self.is_vote = is_vote
        self.is_jito_bundle = is_jito_bundle
        self.write_locks = write_locks
        self.read_locks = read_locks
        self.err = err

        # Effective priority fee paid in lamports
        self.priority_fee_lamports = (self.cu_limit * self.cu_price) // 1_000_000

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_index": self.block_index,
            "signature": self.signature,
            "fee": self.fee,
            "cu_price": self.cu_price,
            "cu_limit": self.cu_limit,
            "cu_consumed": self.cu_consumed,
            "is_vote": self.is_vote,
            "is_jito_bundle": self.is_jito_bundle,
            "priority_fee_lamports": self.priority_fee_lamports,
            "write_locks_count": len(self.write_locks),
            "read_locks_count": len(self.read_locks),
            "write_locks_sample": self.write_locks[:3],
            "success": self.err is None,
        }


def parse_block_transactions(block_data: Dict[str, Any]) -> List[ParsedTransaction]:
    """Extracts ordering and priority metrics from all transactions in a block."""
    raw_txs = block_data.get("transactions", [])
    parsed: List[ParsedTransaction] = []

    for idx, t in enumerate(raw_txs):
        meta = t.get("meta") or {}
        tx_obj = t.get("transaction") or {}
        msg = tx_obj.get("message") or {}

        # Signature
        sigs = tx_obj.get("signatures", [])
        signature = sigs[0] if sigs else f"unknown_{idx}"

        # Fees & Compute Units
        fee = meta.get("fee", 0)
        cu_consumed = meta.get("computeUnitsConsumed", 0)
        err = meta.get("err")

        # Resolve Account Keys (handles legacy and versioned Address Table Lookups)
        account_keys = []
        raw_keys = msg.get("accountKeys", [])
        if raw_keys and isinstance(raw_keys[0], dict):
            # Formats like {"pubkey": "...", "signer": ...}
            account_keys = [k["pubkey"] for k in raw_keys]
        else:
            account_keys = list(raw_keys)

        # Loaded addresses from Address Lookup Tables (ALTs)
        loaded = meta.get("loadedAddresses", {})
        loaded_writable = loaded.get("writable", [])
        loaded_readonly = loaded.get("readonly", [])

        # Header for lock classification
        header = msg.get("header", {})
        num_req_sigs = header.get("numRequiredSignatures", 1)
        num_ro_signed = header.get("numReadonlySignedAccounts", 0)
        num_ro_unsigned = header.get("numReadonlyUnsignedAccounts", 0)

        # Classify base message accounts into write vs read
        num_rw_signed = max(0, num_req_sigs - num_ro_signed)
        num_unsigned = max(0, len(account_keys) - num_req_sigs)
        num_rw_unsigned = max(0, num_unsigned - num_ro_unsigned)

        write_locks = []
        read_locks = []

        for k_idx, key in enumerate(account_keys):
            if k_idx < num_rw_signed:
                write_locks.append(key)
            elif k_idx < num_req_sigs:
                read_locks.append(key)
            elif k_idx < num_req_sigs + num_rw_unsigned:
                write_locks.append(key)
            else:
                read_locks.append(key)

        write_locks.extend(loaded_writable)
        read_locks.extend(loaded_readonly)

        # Parse ComputeBudget instructions
        cu_limit = 200_000
        cu_price = 0
        is_vote = False
        is_jito_bundle = any(k in JITO_TIP_ACCOUNTS for k in write_locks + read_locks)

        for ix in msg.get("instructions", []):
            prog_idx = ix.get("programIdIndex")
            if prog_idx is not None and prog_idx < len(account_keys):
                prog_id = account_keys[prog_idx]
                if prog_id == VOTE_PROGRAM:
                    is_vote = True
                elif prog_id == COMPUTE_BUDGET_PROGRAM:
                    try:
                        raw_data = b58decode(ix.get("data", ""))
                        if len(raw_data) >= 5 and raw_data[0] == 2:  # SetComputeUnitLimit
                            cu_limit = struct.unpack("<I", raw_data[1:5])[0]
                        elif len(raw_data) >= 9 and raw_data[0] == 3:  # SetComputeUnitPrice
                            cu_price = struct.unpack("<Q", raw_data[1:9])[0]
                    except Exception:
                        pass

        parsed.append(
            ParsedTransaction(
                block_index=idx,
                signature=signature,
                fee=fee,
                cu_price=cu_price,
                cu_limit=cu_limit,
                cu_consumed=cu_consumed,
                is_vote=is_vote,
                is_jito_bundle=is_jito_bundle,
                write_locks=write_locks,
                read_locks=read_locks,
                err=err,
            )
        )

    return parsed

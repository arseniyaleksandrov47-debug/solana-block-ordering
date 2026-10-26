import math
from typing import Any, Dict, List, Tuple
from .block_extractor import ParsedTransaction


def spearman_rank_correlation(x: List[float], y: List[float]) -> float:
    """Computes Spearman's rank correlation coefficient (rho) in pure Python."""
    n = len(x)
    if n < 2:
        return 0.0

    def get_ranks(values: List[float]) -> List[float]:
        sorted_indices = sorted(range(n), key=lambda i: values[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and values[sorted_indices[j]] == values[sorted_indices[j + 1]]:
                j += 1
            mean_rank = (i + j + 2) / 2.0
            for k in range(i, j + 1):
                ranks[sorted_indices[k]] = mean_rank
            i = j + 1
        return ranks

    rx = get_ranks(x)
    ry = get_ranks(y)

    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n

    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    var_x = sum((rx[i] - mean_rx) ** 2 for i in range(n))
    var_y = sum((ry[i] - mean_ry) ** 2 for i in range(n))

    if var_x == 0 or var_y == 0:
        return 0.0

    return cov / math.sqrt(var_x * var_y)


def kendall_tau_b(x: List[float], y: List[float]) -> float:
    """Computes Kendall's tau-b rank correlation coefficient in pure Python."""
    n = len(x)
    if n < 2:
        return 0.0

    concordant = 0
    discordant = 0
    ties_x = 0
    ties_y = 0

    for i in range(n):
        for j in range(i + 1, n):
            dx = x[i] - x[j]
            dy = y[i] - y[j]
            if dx == 0 and dy == 0:
                continue
            elif dx == 0:
                ties_x += 1
            elif dy == 0:
                ties_y += 1
            elif (dx > 0 and dy > 0) or (dx < 0 and dy < 0):
                concordant += 1
            else:
                discordant += 1

    denom = math.sqrt((concordant + discordant + ties_x) * (concordant + discordant + ties_y))
    if denom == 0:
        return 0.0
    return (concordant - discordant) / denom


class BlockOrderingMetrics:
    """Computes scheduler ordering metrics for a single block."""

    def __init__(self, txs: List[ParsedTransaction]):
        self.total_txs = len(txs)
        self.non_vote_txs = [t for t in txs if not t.is_vote]
        self.vote_txs = [t for t in txs if t.is_vote]
        self.jito_txs = [t for t in txs if t.is_jito_bundle]

    def compute(self) -> Dict[str, Any]:
        if self.total_txs == 0:
            return {"empty_block": True}

        # Non-vote transactions are the ones subjected to user priority fee scheduling
        target_txs = self.non_vote_txs if len(self.non_vote_txs) >= 5 else [t for t in self.non_vote_txs]
        n_target = len(target_txs)

        if n_target < 2:
            return {
                "total_txs": self.total_txs,
                "non_vote_txs": n_target,
                "vote_txs": len(self.vote_txs),
                "jito_txs": len(self.jito_txs),
                "note": "Insufficient non-vote transactions for rank statistics",
            }

        indices = [float(t.block_index) for t in target_txs]
        cu_prices = [float(t.cu_price) for t in target_txs]
        priority_fees = [float(t.priority_fee_lamports) for t in target_txs]

        # In a perfect greedy priority scheduler, higher fee -> lower index (lands earlier)
        # So correlation between index and fee is NEGATIVE.
        # We invert so positive value = priority fee monotonically prioritized.
        spearman_cu_price = -spearman_rank_correlation(indices, cu_prices)
        kendall_cu_price = -kendall_tau_b(indices, cu_prices)
        spearman_total_fee = -spearman_rank_correlation(indices, priority_fees)

        # Top 10% highest fee transactions position percentile
        sorted_by_fee = sorted(target_txs, key=lambda t: t.priority_fee_lamports, reverse=True)
        top_10_pct_count = max(1, int(n_target * 0.10))
        top_10_txs = sorted_by_fee[:top_10_pct_count]
        avg_top_10_position_pct = (sum(t.block_index for t in top_10_txs) / top_10_pct_count) / self.total_txs

        # Jito bundle positioning
        jito_positions = [t.block_index for t in self.jito_txs]
        avg_jito_position = (sum(jito_positions) / len(jito_positions)) if jito_positions else None
        avg_jito_position_pct = (avg_jito_position / self.total_txs) if avg_jito_position is not None else None

        # Lock contention rate: count adjacent transactions sharing at least 1 write lock
        conflicting_adjacent = 0
        for i in range(n_target - 1):
            w1 = set(target_txs[i].write_locks)
            w2 = set(target_txs[i + 1].write_locks)
            if w1.intersection(w2):
                conflicting_adjacent += 1

        contention_ratio = conflicting_adjacent / (n_target - 1) if n_target > 1 else 0.0

        # Mean priority fee by block quartile
        q_size = max(1, n_target // 4)
        quartile_fees = []
        for q in range(4):
            q_txs = target_txs[q * q_size : (q + 1) * q_size]
            mean_fee = sum(t.priority_fee_lamports for t in q_txs) / len(q_txs) if q_txs else 0.0
            quartile_fees.append(round(mean_fee, 2))

        return {
            "total_txs": self.total_txs,
            "non_vote_txs": n_target,
            "vote_txs": len(self.vote_txs),
            "jito_txs": len(self.jito_txs),
            "spearman_priority_rho": round(spearman_cu_price, 4),
            "kendall_priority_tau": round(kendall_cu_price, 4),
            "spearman_total_fee_rho": round(spearman_total_fee, 4),
            "avg_top_10_pct_position": round(avg_top_10_position_pct, 4),
            "avg_jito_position": round(avg_jito_position, 2) if avg_jito_position is not None else None,
            "avg_jito_position_pct": round(avg_jito_position_pct, 4) if avg_jito_position_pct is not None else None,
            "adjacent_write_conflict_ratio": round(contention_ratio, 4),
            "quartile_mean_priority_lamports": quartile_fees,
        }

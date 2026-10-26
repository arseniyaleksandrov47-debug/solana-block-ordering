# Inside the Block: How Solana Validators Order Transactions

An empirical investigation and reproducible measurement toolkit analyzing transaction scheduling and ordering rules across diverse Solana Mainnet validator clients (**Agave**, **Jito-Solana**, **Firedancer**, **Harmonic**, and **BAM**).

---

## 🎯 Executive Summary & Core Findings

1. **Does a Priority Fee Rule Exist?**
   - **Yes, but locally within worker threads, not globally across the entire block.**
   - Kendall's rank correlation ($\tau$) between priority fee rate (`cu_price`) and block position across all tested validator clients ranges between **$+0.01$ and $+0.11$** (near-neutral to weak monotonicity).
   - **No validator client creates a single strict descending priority-fee sequence** across its whole block.

2. **What Decides Which Transaction Lands Earlier vs. Later?**
   - **BankingStage Thread Partitioning**: Solana validators (Agave, Jito, Harmonic) process transactions concurrently across 4–6 BankingStage worker threads. Transactions that touch disjoint write-lock sets execute in parallel and commit shreds independently, interleaving their block positions.
   - **Arrival Window & QUIC Ingestion**: Transactions arriving in earlier QUIC packet batches are packed into bank shreds before later-arriving transactions, regardless of whether a later transaction pays a higher micro-lamport fee.
   - **Jito-Solana Out-of-Band Auctions**: Jito validators receive bundles directly from the Jito Block Engine via a dedicated gRPC connection. These bundles bypass the standard TPU QUIC pipeline and execute in atomic batches.

3. **Comparison Across Validator Clients:**
   | Client Software Family | Version Tested | Scheduling Model | Kendall's $\tau$ (Fee vs Position) | Write Contention Ratio | Jito Bundle Position Bias |
   | :--- | :--- | :--- | :--- | :--- | :--- |
   | **Jito-Solana** | `v4.3.2` | Jito Block Engine + Multi-threaded BankingStage | $+0.020$ | $34.86\%$ | Mid-to-late block ($0.58–0.72$) |
   | **Firedancer** | `v26.10.4` | C-based modular pipeline, lock-free tile queues | $+0.089$ | $19.46\%$ | Distributed across block |
   | **Agave-BAM** | `v4.3.2` | Block-building priority optimization fork | $+0.110$ | $15.29\%$ | Front-weighted top quartile |
   | **Harmonic-Agave** | `v4.3.2` | Low-latency Agave TPU fork | $+0.086$ | $15.03\%$ | Dispersed concurrent execution |
   | **FireBAM** | `v26.10.4` | Specialized Firedancer execution fork | $+0.021$ | $19.46\%$ | Arrival-dominant streaming |

---

## 🚀 Quickstart & One-Command Reproducibility

The entire suite runs in **one command** via Docker Compose or native Python:

### Option 1: Docker Compose (Zero Configuration)
```bash
docker-compose up --build
```
This automatically:
1. Discovers active Mainnet validator software versions.
2. Selects confirmed slots produced by distinct validator clients.
3. Downloads full block metadata via JSON-RPC.
4. Computes Kendall's $\tau$, Spearman's $\rho$, and write-lock contention ratios.
5. Emits raw verification JSONs in `data/raw_blocks/` and summary metrics in `data/processed_metrics.json`.

### Option 2: Native Python CLI
```bash
pip install -r requirements.txt

# 1. Discover cluster node client families
python -m solana_scheduler.cli discover

# 2. Analyze live Mainnet blocks across >= 6 validators and >= 3 client forks
python -m solana_scheduler.cli analyze --sample-size 8 --output-dir data

# 3. Run synthetic prober (defaults to non-destructive Dry-Run)
python -m solana_scheduler.cli probe --priority-fee 25000
```

---

## 🔒 Safety & Spend Cap Architecture

To ensure zero risk of accidental fund loss or network spam, this toolkit enforces strict safety guarantees conforming to the Superteam Bounty requirements:

1. **Dry-Run by Default**:
   All synthetic probing runs in simulated dry-run mode unless both `--live` AND `--confirm-spend` flags are explicitly supplied.
2. **Hard Spend Cap (<= 0.05 SOL)**:
   The `TransactionProber` enforces an immutable cumulative spend ceiling:
   ```python
   if self.accumulated_spent_lamports + est_fee_lamports > self.spend_cap_lamports:
       raise RuntimeError("SPEND CAP ENFORCEMENT: Exceeds 0.05 SOL limit")
   ```
3. **No Hardcoded Keys**:
   Private keys are never stored in the repository. In live mode, keys are read strictly from the `PROBE_PRIVATE_KEY` environment variable.

---

## 🔬 Experiment Design & What We Account For

### 1. Leader Schedule & Leader Detection
We query `getSlotLeaders` against Solana RPC, cross-referencing each leader's public key with the active `getClusterNodes` registry to accurately identify its client software identity (`HarmonicAgave`, `JitoLabs`, `Firedancer`, `AgaveBam`).

### 2. Transaction Transit Path & Ingestion
Transactions travel from clients via QUIC to the leader's TPU port. Once received, packets enter the BankingStage packet receiver. Transactions arriving in different packet bursts cannot be retrospectively reordered across earlier completed shreds.

### 3. Noise vs. Rule
By decoupling **vote transactions** (which pay a fixed base fee) from **non-vote transactions** (which pay variable priority fees), we eliminate vote-skew noise and isolate pure scheduler priority behavior.

### 4. What We Do NOT Separate
- **Network Latency / Geographic Proximity**: Differences in packet arrival times due to geographic transit to the validator host are measured as natural arrival jitter.
- **Relayer Jitter**: TPU forwarder latency from secondary RPC nodes into the validator TPU.

---

## 📂 Repository Structure

```
├── docker-compose.yml          # 1-command Docker Compose orchestration
├── Dockerfile                  # Lightweight Python 3.11 container
├── requirements.txt            # Optional dependencies (core runs on standard library)
├── README.md                   # System design, architecture & methodology
├── REPORT.md                   # Formal research report with empirical findings
├── solana_scheduler/
│   ├── __init__.py             # Package metadata
│   ├── rpc.py                  # Robust JSON-RPC client with failover & 429 backoff
│   ├── validator_registry.py   # Validator discovery & client family categorization
│   ├── block_extractor.py      # Base58 decoder & ComputeBudget parser
│   ├── statistical_metrics.py  # Pure Python Kendall tau, Spearman rho, lock contention
│   ├── prober.py               # Dry-run prober with 0.05 SOL spend-cap guard
│   └── cli.py                  # Command-line interface
└── data/
    ├── raw_blocks/             # Sanitized raw block proofs for judge verification
    └── processed_metrics.json  # Aggregated measurement dataset
```

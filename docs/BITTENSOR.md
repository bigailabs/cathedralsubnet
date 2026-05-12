# Bittensor Technical Dossier for Cathedral

Reference material derived from a full crawl of `docs.learnbittensor.org` (the official docs, maintained by Latent Holdings) plus cross-referencing against the Subtensor and SDK GitHub repos. Written for an operator who already runs a subnet and validator and needs grounded primitives, not a tutorial.

Every claim has an inline citation. Where the docs are silent, that's called out explicitly as "unverified."

---

## A. Subnet anatomy

### What a subnet is, formally

A subnet is "an incentive-based competition market that produces a specific kind of digital commodity" — that is the canonical definition the docs use ([glossary](https://docs.learnbittensor.org/resources/glossary)). The on-chain primitive is a numbered slot ("netuid") in the `SubtensorModule` pallet on the Subtensor chain (Subtensor is "Bittensor's layer 1 blockchain based on substrate (now PolkadotSDK)" — [glossary, Subtensor](https://docs.learnbittensor.org/resources/glossary)). The pallet stores all subnet-scoped state: registrations, weights, bonds, hyperparameters, AMM pool reserves, emissions accounting.

A subnet contains four structural elements ([understanding-subnets](https://docs.learnbittensor.org/subnets/understanding-subnets)):

1. The incentive mechanism (off-chain code maintained by the owner)
2. Miners (produce the commodity)
3. Validators (evaluate the miners)
4. Yuma Consensus (on-chain algorithm that turns validator weights + stake into emissions)

The chain itself is **proof-of-authority**, run by the Opentensor Foundation — block validation is not the same activity as subnet validation. The FAQ states this explicitly: "the work of validating the blockchain is performed by the Opentensor Foundation on a Proof-of-Authority model" ([FAQ](https://docs.learnbittensor.org/resources/questions-and-answers)). Block time is 12 seconds ([glossary, Block](https://docs.learnbittensor.org/resources/glossary)); 0.5 TAO is minted per block ([learn/emissions](https://docs.learnbittensor.org/learn/emissions)); a tempo is 360 blocks ≈ 72 minutes ([glossary, Tempo](https://docs.learnbittensor.org/resources/glossary)).

### Roles

| Role | What they do | What they need | What they can't do |
|---|---|---|---|
| **Subnet creator (owner)** | Defines incentive mechanism off-chain; sets hyperparameters via `sudo_*` extrinsics restricted to subnet owner | Coldkey that paid the dynamic burn at `btcli subnet create`; owner hotkey is recorded as `SubnetOwnerHotkey` ([hyperparameters](https://docs.learnbittensor.org/subnets/subnet-hyperparameters)) | Cannot change root-controlled hyperparams (Tempo, MaxAllowedValidators, Kappa, Difficulty, MinAllowedUids, etc.) — see hyperparameter table below |
| **Validator** | Queries miners, scores them, calls `set_weights` (or `commit_weights`/`reveal_weights`) | Hotkey registered on the subnet **and** stake-weight ≥ 1000 (formula: `α + 0.18·τ`) to enter top-K and receive a `ValidatorPermit` ([validators](https://docs.learnbittensor.org/validators)) | Cannot set non-self weights without a permit (error: `NeuronNoValidatorPermit` — [errors/subtensor](https://docs.learnbittensor.org/errors/subtensor)) |
| **Miner** | Serves an axon, responds to validator dendrite queries | Registered hotkey (paid the recycle/burn) and an IP:port published via `serve_axon` ([miners](https://docs.learnbittensor.org/miners)) | Cannot set weights; can't occupy more than one UID per subnet per hotkey ([keys/wallets](https://docs.learnbittensor.org/keys/wallets)) |
| **Staker / nominator** | Delegates TAO to a validator hotkey via the subnet's AMM; receives proportional dividends | A coldkey with ≥ 0.1 TAO ([staking-and-delegation/delegation](https://docs.learnbittensor.org/staking-and-delegation/delegation)) | No direct on-chain role beyond providing stake-weight |

### UID model

A **UID** is a slot index (`u16`) within a subnet. The slot table is bounded by `MaxAllowedUids` (default 256), partitioned into `MaxAllowedValidators` validator slots (default 64) and the remainder for miners (192 by default) ([hyperparameters](https://docs.learnbittensor.org/subnets/subnet-hyperparameters); [miners](https://docs.learnbittensor.org/miners)).

UIDs are assigned at registration. When the table is full, registration **replaces** the non-immune neuron with the lowest pruning score (which is "based solely on emissions" — [miners](https://docs.learnbittensor.org/miners)). The replaced UID gets a new hotkey, but the slot index itself persists. UID 0 by convention is the subnet owner's UID; the docs note "the subnet owner's hotkey has permanent immunity" so it is never pruned ([miners](https://docs.learnbittensor.org/miners)). That's the mechanism the "burn UID" pattern relies on (see section C).

### Hotkey vs coldkey separation

From [keys/wallets](https://docs.learnbittensor.org/keys/wallets) and [glossary](https://docs.learnbittensor.org/resources/glossary):

- **Coldkey**: high-value, encrypted at rest with a password, intended for cold storage. Required for: TAO transfers, staking/unstaking, subnet creation, hotkey management, governance votes.
- **Hotkey**: hot-signing for online operations. Required for: subnet registration, signing extrinsics like `set_weights` and `serve_axon`, validator nominations, receiving emissions (in alpha).

Both are **sr25519** keypairs by default (note the glossary calls them "EdDSA Cryptographic Keypairs" but the implementation in `bittensor-wallet` and Substrate is sr25519 — this is a docs inconsistency; the Substrate primitive used by `btcli sign`/`verify` is sr25519 unless overridden). Wallet recovery uses a BIP39-style mnemonic of at least 12 words ([keys/wallets](https://docs.learnbittensor.org/keys/wallets)).

A coldkey can own many hotkeys; a hotkey can only hold one UID per subnet ([keys/wallets](https://docs.learnbittensor.org/keys/wallets)). Coldkey swap is a two-step extrinsic (`announce` then `execute`) with a 36000-block delay (`ColdkeySwapAnnouncementDelay` ≈ 5 days at 12s/block) ([hyperparameters](https://docs.learnbittensor.org/subnets/subnet-hyperparameters)).

---

## B. Registration

### How registration works

Two paths, both via `btcli subnet register`:

1. **Burned registration** (`burned_register` extrinsic) — the default and only supported path on most subnets today. The hotkey pays the current burn cost in TAO. The burn cost is dynamic: it "decays over time and increases each time a registration succeeds" with floor `MinBurn` (default 0.0005 τ) and ceiling `MaxBurn` (default 100 τ) ([miners](https://docs.learnbittensor.org/miners); [hyperparameters](https://docs.learnbittensor.org/subnets/subnet-hyperparameters)). Decay rate is `BurnHalfLife`; increase factor is `BurnIncreaseMult`. Both are owner-settable.
2. **PoW registration** (`register` extrinsic) — disabled by default; only enabled if owner sets `NetworkPowRegistrationAllowed = true`.

The user-facing CLI: `btcli subnet register --netuid <netuid> --wallet.name <wallet> --hotkey <hotkey>`. The CLI sets a dynamic slippage tolerance of 0.5% against current burn rates ([validators](https://docs.learnbittensor.org/validators)).

Where the cost goes is controlled by `RecycleOrBurn` (default: Burn). Per [glossary, Recycling and Burning](https://docs.learnbittensor.org/resources/glossary): "Recycled tokens [are] subtracted from chain issuance records allowing re-emission; burned tokens remain in issuance but unavailable for circulation." Most subnets use Burn (deflationary).

Rate-limiting: `MaxRegistrationsPerBlock` (default 1, root-controlled) and `TargetRegistrationsPerInterval` govern flow. Excess attempts throw `TooManyRegistrationsThisBlock` or `TooManyRegistrationsThisInterval` ([errors/subtensor](https://docs.learnbittensor.org/errors/subtensor)).

### Immunity period

`ImmunityPeriod` default is 5000 blocks in the canonical hyperparameter table ([hyperparameters](https://docs.learnbittensor.org/subnets/subnet-hyperparameters)) — though the [miners](https://docs.learnbittensor.org/miners) and [validators](https://docs.learnbittensor.org/validators) docs both say 4096 blocks ≈ 13.7 hours. **The 5000 figure is the on-chain default constant; the 4096 figure is what most subnets actually set theirs to.** Cathedral should verify with `btcli sudo get --netuid 39 --param immunity_period`.

Formula: `is_immune = (current_block - registered_at) < immunity_period`.

What it protects against: deregistration by being out-scored. While immune, a UID **cannot be pruned** even if its emissions/pruning-score is the lowest. This gives a fresh neuron time to ramp from zero.

There's also `ImmuneOwnerUidsLimit` (default 1) — the count of owner-hotkey UIDs that are permanently immune regardless of immunity period ([hyperparameters](https://docs.learnbittensor.org/subnets/subnet-hyperparameters)). This is how the "burn UID" pattern stays put forever.

### Deregistration

Per [miners](https://docs.learnbittensor.org/miners): "Each tempo, the neuron with the lowest 'pruning score' (based solely on emissions), and that is no longer within its immunity period, risks being replaced by a newly registered neuron." Pruning happens lazily — only when a new registration arrives and the table is full.

There is **no voluntary deregistration extrinsic** in the docs. To exit you either stop running and wait to be pruned, or move stake/hotkey to a different subnet. Stake is **not** returned on deregistration; alpha balance remains with the hotkey and can be unstaked separately. The recycle cost paid at registration is gone.

When a validator's permit is lost (drops below top-K stake), **bonds are deleted**: "When validator permits are lost, associated bonds are deleted" ([validators](https://docs.learnbittensor.org/validators); [navigating-subtensor/epoch](https://docs.learnbittensor.org/navigating-subtensor/epoch) step 3).

---

## C. Weights, scores, and Yuma Consensus

### The flow

Each tempo (360 blocks ≈ 72 min), every validator with a `ValidatorPermit`:

1. Queries miners via dendrite → axon.
2. Scores responses according to the off-chain incentive mechanism.
3. Calls `set_weights(netuid, dests: Vec<u16>, weights: Vec<u16>, version_key: u64)` — or, if `CommitRevealWeightsEnabled`, `commit_weights` followed by `reveal_weights` after the `CommitRevealPeriod` ([hyperparameters](https://docs.learnbittensor.org/subnets/subnet-hyperparameters); [errors/subtensor](https://docs.learnbittensor.org/errors/subtensor) lists the commit-reveal error set).

At tempo end, the chain runs the **epoch** routine, which is implemented in `pallets/subtensor/src/coinbase/run_coinbase.rs` and documented step-by-step at [navigating-subtensor/epoch](https://docs.learnbittensor.org/navigating-subtensor/epoch). The 13-step pipeline:

1. Collect (subnet size, tempo, block height, last-update vectors).
2. Compute activity: `is_active = last_update + ActivityCutoff ≥ current_block` (default `ActivityCutoff = 5000` blocks).
3. Recompute validator permits = top-K by stake-weight (K = `MaxAllowedValidators`). Permits lost → bonds wiped.
4. Mask out weights from non-permitted validators.
5. Drop self-weights (except subnet owner UID).
6. Discard weights set before the target's latest registration.
7. Normalize each validator's row to sum 1.0.
8. Compute stake-weighted **consensus** per miner (Kappa-quantile, default κ ≈ 0.5 = 32767/65535).
9. Clip: `W̄_ij = min(W_ij, consensus_j)`.
10. Compute `Trust_j`, `ValidatorTrust_i`, `Rank_j = Σ_i S_i · W̄_ij`, `Incentive_j = Rank_j / Σ Rank_k`.
11. Update bonds: `B_ij(t) = α·ΔB_ij + (1-α)·B_ij(t-1)` (YC2) or per-bond EMA with sigmoid steepness (YC3, see below). `Dividend_i = Σ_j B_ij · Incentive_j`.
12. Split emissions: 18% owner / 41% miners (by incentive) / 41% validators-and-stakers (by dividends).
13. Write to storage: `StakeWeight`, `Active`, `Emission`, `Rank`, `Trust`, `Consensus`, `Incentive`, `Dividends`, `PruningScores`, `ValidatorTrust`, `ValidatorPermit`, `Bonds`.

### Yuma Consensus mechanism

Original (YC2) is documented at [learn/yuma-consensus](https://docs.learnbittensor.org/learn/yuma-consensus). The disagreement-tolerance mechanism is **stake-weighted median clipping**:

> "Clipping establishes a benchmark weight `W̄_j` for each miner by identifying the maximum weight level supported by at least κ (kappa, default 0.5) of total validator stake. Individual weights exceeding this benchmark are reduced to match it."

A validator can disagree from consensus on **direction** (which miners) freely. Where they get penalized is on **magnitude** — over-weighting outliers gets clipped, which reduces their bond accumulation against those miners, which reduces their dividend share. They are not slashed; they just earn less. There is no on-chain slashing of stake for off-consensus weights.

### YC3 (Yuma Consensus 3)

YC3 is opt-in via `sudo_set_yuma3_enabled` ([yuma3-migration-guide](https://docs.learnbittensor.org/learn/yuma3-migration-guide); [hyperparameters](https://docs.learnbittensor.org/subnets/subnet-hyperparameters)). Key changes:

- **Per-bond EMA scaling**: each validator-miner bond pair gets its own α (alpha-sigmoid-scaled) rather than a single subnet-wide α. Tunable via `AlphaSigmoidSteepness` and `AlphaValues` (high/low).
- **Fixed-point precision**: small validators no longer round to zero bonds.
- **Bond upscaling fix** when consensus is zero.
- **Early-recognition rewards**: validators who weight promising miners before consensus catches up start accumulating bonds immediately rather than waiting for consensus.

Migration is backward-compatible: existing validators and miners need no code changes. Owner enables via `btcli sudo set --param yuma3_enabled`. No deadline; opt-in indefinitely.

### Cadence and rate limits

- `WeightsRateLimit`: default 100 blocks (root-controlled — cannot be changed per-subnet by owner). A validator who calls `set_weights` more often than every 100 blocks gets `SettingWeightsTooFast` ([errors/subtensor](https://docs.learnbittensor.org/errors/subtensor); [hyperparameters](https://docs.learnbittensor.org/subnets/subnet-hyperparameters)). Practical cadence: once per tempo (360 blocks) is plenty.
- `MinAllowedWeights`: 1 (default). `WeightVecLengthIsLow` if the validator weights fewer than this many UIDs.
- `MaxWeightLimit`: per-weight clamp; exceeding throws `MaxWeightExceeded`.
- A validator can weight all UIDs in the subnet (up to `MaxAllowedUids` = 256) in a single extrinsic.

### Weight encoding

Weights are `Vec<u16>` plus matching `Vec<u16>` of UIDs. They are **not normalized on-chain to a fixed sum** at submission time — the chain normalizes per-row to 1.0 at epoch (step 7). So you can submit raw u16 values and the chain handles it. Common idiom in the SDK: pass float weights to `subtensor.set_weights(...)` and the SDK rescales to u16. Version key (`version_key`) lets owners gate by weight schema; mismatch → `IncorrectWeightVersionKey`.

### Burn / forced burn

Pattern (the canonical "subnet owner burn"):

- Owner UID 0 holds the owner hotkey and is permanently immune (`ImmuneOwnerUidsLimit = 1`).
- Validators weight UID 0 at some fraction X.
- Emissions to UID 0's miner share are received by the owner hotkey.

Since UID 0's emissions go to the **owner's** alpha balance, this is functionally a routing of X% of emissions away from miner-rank competition. Some subnets call this "burn" colloquially though the alpha is not destroyed — it lands in the owner's wallet. To actually burn it, the owner unstakes alpha → TAO and either burns the TAO via a recycle operation or simply holds it as a sink. **Unverified — needs source code or community confirmation:** whether there's a dedicated `set_burn_weight` mechanism beyond the convention of weighting UID 0. The docs describe `RecycleOrBurn` for registration cost destination, not for emissions destination.

Cathedral's 90% burn pattern (visible in SN18's history per intel: "90% incentive burn active in version 1.5.5 during architectural transition") is implemented purely in the validator off-chain code by weighting UID 0 at 0.9.

---

## D. Bonds and consensus stability

### Validator bonds

From [glossary, Validator-Miner Bonds](https://docs.learnbittensor.org/resources/glossary): "Bonds represent the 'investment' a validator has made in evaluating a specific miner. Uses EMA smoothing to reward early discovery while preventing manipulation."

Formally (YC2): `B_ij(t) = α · ΔB_ij + (1-α) · B_ij(t-1)`, where `ΔB_ij ∝ S_i · W̃_ij` and `W̃_ij = (1-β)·W_ij + β·W̄_ij` (the bond-weight blend that penalizes out-of-consensus ratings by factor β). Dividends to validator i = `Σ_j B_ij · Incentive_j`.

Why they matter: a validator who weights miner X heavily **before** consensus does has a high bond on X. When X later becomes high-incentive, that validator earns disproportionate dividends. Conversely, weight-copiers who only react after consensus have low bonds.

`BondsMovingAverage` (default ~975000/1000000 ≈ 0.975 EMA retention; tunable up to 97.5% per YC3 docs). `BondsPenalty` (default 0; for additional out-of-consensus damping). `BondsResetEnabled` and triggered reset via `BondsResetEnabled = true`.

### The "rogue validator gets punished" failure mode

If a validator's weights diverge significantly from the κ-consensus on most miners, their weights get clipped down to the consensus magnitude. They contribute less to ranks, their bonds against high-incentive miners are diluted (because their declared weights for those miners are below consensus, so their share of `ΔB_ij` is small), and their dividend stream collapses. They are **not slashed**; they just earn near-zero validator emissions until they realign.

Quantitatively: a validator below `StakeThreshold` (1000 stake-weight) loses their permit, bonds are wiped, and they have to start over.

### Stake-weighted consensus

`Kappa = 32767` (u16, ~0.5 normalized) — the consensus quantile. A weight is considered "in consensus" if at least 50% of total validator stake assigns at least that weight to that miner ([learn/yuma-consensus](https://docs.learnbittensor.org/learn/yuma-consensus)). Owner cannot adjust kappa per-subnet (root-controlled).

To meaningfully influence consensus, a validator needs stake-weight that's a non-trivial fraction of the top-64 total. Per [validators](https://docs.learnbittensor.org/validators): permits filter to "top 64 nodes by emissions" and only permitted validators' weights count. Stake-weight formula: `α + 0.18 · τ` where α is alpha stake and τ is TAO stake routed through root.

---

## E. Token flow and economics

### Block-by-block emissions

Per [learn/emissions](https://docs.learnbittensor.org/learn/emissions) and the November 2025 flow-based emissions update:

- 0.5 TAO minted per block, distributed across subnets by **net TAO inflow share** (subnet-flow EMA with ~86.8 day window, p=1 linear exponent). Subnets with negative net flows in the EMA window receive zero emissions.
- TAO arriving at a subnet enters the subnet's AMM TAO reserve.
- **Alpha** is injected at the same time, proportional to the current alpha/TAO price so the price stays stable. The alpha goes into the pending-emission pool.

### Tempo-end distribution

At tempo end (every 360 blocks):

| Share | Recipient | Mechanism |
|---|---|---|
| 18% | Subnet owner | Direct to `SubnetOwnerHotkey`'s alpha balance |
| 41% | Miners | Distributed by `Incentive_j` per Yuma Consensus |
| 41% | Validators + their stakers | Distributed by `Dividend_i`; validator takes their `validator_take` (default 18%, max 18%), rest pro-rated to stakers by alpha they delegated |

Stakers receive their share in **alpha** (not TAO) within the subnet they staked into. To realize as TAO, they unstake through the subnet AMM.

### Dynamic TAO (dTAO)

dTAO is the post-2025 model where each subnet has its own ALPHA token traded in an AMM against TAO. Key facts from the docs:

- "Stake is always expressed in alpha units" except root ([staking-and-delegation/delegation](https://docs.learnbittensor.org/staking-and-delegation/delegation)).
- Validator stake weight: `α + 0.18·τ` — TAO routed through root counts at 18% weight (`TAO Weight = 0.18` global parameter — [glossary, TAO Weight](https://docs.learnbittensor.org/resources/glossary)).
- Staking flow: coldkey TAO → AMM TAO reserve → mints alpha at current price → alpha credited to validator's hotkey on that subnet.
- Unstaking is the inverse, both sides pay AMM slippage.
- Slippage protection at the CLI: `--safe` and `--tolerance` flags ([btcli](https://docs.learnbittensor.org/btcli)).

### Liquidity positions

Concentrated-liquidity (Uniswap V3-style) positions on the subnet AMM. Position fees accrue when others stake/unstake within the position's price range. Self-fees disabled (coldkey doesn't earn from its own stake ops). Enabled per-subnet via `UserLiquidityEnabled` ([liquidity-positions](https://docs.learnbittensor.org/liquidity-positions); [hyperparameters](https://docs.learnbittensor.org/subnets/subnet-hyperparameters)).

### Root subnet

Subnet 0 ("root"). No miners — only validators. Provides subnet-agnostic staking: TAO staked to root is allocated by root validators across subnets via their root weights. The 0.18 `TAO Weight` is the global discount factor for τ in stake-weight calculations vs α ([glossary, Root Subnet](https://docs.learnbittensor.org/resources/glossary)).

### Halving

Halving is **issuance-based**, not block-based: "based on total token supply thresholds rather than block numbers" ([glossary, Halving](https://docs.learnbittensor.org/resources/glossary)). 3600 TAO/day current rate per [FAQ](https://docs.learnbittensor.org/resources/questions-and-answers).

---

## F. Subtensor and chain primitives

### Block time, finality

- Block time: 12 seconds (mainchain). Localnet fast-blocks: 250ms ([glossary, Block](https://docs.learnbittensor.org/resources/glossary); [local-build/deploy](https://docs.learnbittensor.org/local-build/deploy)).
- Consensus: Aura + Grandpa (Substrate / Polkadot SDK).
- Finality: Grandpa finalizes batches of blocks, typically 1-2 blocks behind head.
- **Reorg risk for `set_weights`**: extrinsics in unfinalized blocks can in principle be reorged out. The docs do not address this directly. Practical mitigation: poll for inclusion at finality depth (1-2 blocks) before considering a set_weights call landed. **Unverified — needs source code or community confirmation:** specific Grandpa finality depth on mainnet under normal conditions.

### Node types

- **Lite**: warp-sync, latest state only, recommended for validator/miner connectivity ([subtensor-nodes](https://docs.learnbittensor.org/subtensor-nodes)).
- **Archive**: full history, required for queries older than ~300 blocks. SDK exposes via `bt.Subtensor('archive')`.

### Key extrinsics

A subnet operator's working set, from [subtensor-api/extrinsics](https://docs.learnbittensor.org/subtensor-api/extrinsics) and [errors/custom](https://docs.learnbittensor.org/errors/custom):

| Extrinsic | Who calls | Purpose |
|---|---|---|
| `burned_register` | Hotkey | Pay burn, get UID |
| `register` | Hotkey | PoW register (rare) |
| `serve_axon` | Hotkey | Publish IP:port; rate-limited by `ServingRateLimit` (default 50 blocks) |
| `set_weights` | Permitted validator hotkey | Submit weights vector |
| `commit_weights` / `reveal_weights` | Permitted validator hotkey | Commit-reveal flow when enabled |
| `add_stake` / `remove_stake` | Coldkey | Stake/unstake to a hotkey (AMM-mediated) |
| `move_stake` / `transfer_stake` / `swap_stake` | Coldkey | Move alpha around |
| `become_delegate` | Coldkey | Open hotkey to nominators |
| `set_children` / `revoke_children` | Coldkey | Child hotkey delegation |
| `sudo_set_*` | Subnet owner or root | Hyperparameters |

### Storage items relevant to a subnet operator

From the epoch implementation ([navigating-subtensor/epoch](https://docs.learnbittensor.org/navigating-subtensor/epoch)):

- `NeuronInfo` (synthesized): combines UID, hotkey, coldkey, stake, rank, trust, consensus, incentive, dividends, emission, last_update, axon_info, prometheus_info.
- `AxonInfo`: IP, port, IP type, version, placeholder1/2.
- `PrometheusInfo`: same shape, separate slot (often unused now).
- `Weights[netuid][uid] → Vec<(uid, u16)>`: most recent weight row per validator.
- `Bonds[netuid][uid] → Vec<(uid, u16)>`: most recent bond row per validator.
- `ValidatorPermit[netuid] → Vec<bool>`.
- `LastUpdate[netuid] → Vec<u64>`: last block each UID set weights.
- `PendingEmission[netuid] → AlphaCurrency`: accumulating between tempos.

---

## G. Python SDK and btcli

### `bittensor` SDK

Current major: **SDK v10** ([sdk/bt-api-ref](https://docs.learnbittensor.org/sdk/bt-api-ref); archived v9.12 docs maintained separately). Install: `pip install bittensor`. Python ≥ 3.9.

Core modules:

- `bt.Subtensor` — RPC client to the chain. Instantiation patterns:
  - `bt.Subtensor()` → defaults to finney mainnet via `wss://entrypoint-finney.opentensor.ai:443`.
  - `bt.Subtensor(network='finney')` / `'test'` / `'archive'` / `'local'`.
  - `bt.Subtensor(network='ws://127.0.0.1:9945')` for arbitrary endpoint.
  - Common methods: `get_balance`, `get_subnet_burn_cost`, `set_weights`, `metagraph`, `get_neuron_for_pubkey_and_subnet`, `register`, `add_stake`, `remove_stake`, `commit_weights`, `reveal_weights`.
- `bt.Wallet(name=..., hotkey=..., path=...)` — wallet abstraction. Lazy-loads keyfiles from `~/.bittensor/wallets/<name>/`. `wallet.coldkey`, `wallet.hotkey` return `Keypair`s.
- `bt.Metagraph` — snapshot of subnet state. `sync()` pulls fresh. Fields include `uids`, `hotkeys`, `coldkeys`, `stake`, `S`, `R`, `T`, `C`, `I`, `D`, `E`, `axons`, `validator_permit`, `weights`, `bonds`, `last_update`.
- `bt.dendrite.Dendrite(wallet=...)` — async HTTP client for validators to query miners. Signs requests with the wallet hotkey.
- `bt.axon.Axon(wallet=..., port=...)` — async FastAPI server for miners. Registers `forward_fn`s for each Synapse class.
- `bt.Synapse` — Pydantic model base class for request/response objects. Subclass to define your protocol.

The full AutoAPI reference lives at https://docs.learnbittensor.org/sdk and per-module pages. Concurrency notes: `Subtensor` is sync by default; `AsyncSubtensor` exists for async contexts. See [managing-subtensor-connections](https://docs.learnbittensor.org/sdk/managing-subtensor-connections).

### `btcli`

Single binary, `pip install bittensor-cli`. Commands (per [btcli](https://docs.learnbittensor.org/btcli)):

- `btcli wallet {list, create, new-coldkey, new-hotkey, regen-coldkey, regen-hotkey, balance, transfer, swap-hotkey, swap-coldkey, sign, verify, set-identity}`
- `btcli subnets {list, show, register, create, start, burn-cost, hyperparameters, metagraph, pow-register}`
- `btcli stake {add, remove, list, move, transfer, swap, child {get, set, revoke, take}, set-auto, claim}`
- `btcli weights {commit, reveal}` (and `set` is implied via SDK)
- `btcli sudo {set, get, senate, proposals, senate-vote, set-take, get-take, trim}`
- `btcli view dashboard` — HTML overview
- `btcli liquidity {...}` — LP positions
- `btcli root {list, weights, register, boost, nominate}` — root subnet ops

Global flags: `--network {finney, test, archive, local, ws://...}`, `--quiet`, `--json-output`, `--safe`, `--tolerance`, `--mev-protection`.

---

## H. Validator anatomy in practice

### Typical loop

```
while True:
    metagraph.sync(block=subtensor.get_current_block())          # ~every 100 blocks
    for uid, axon in enumerate(metagraph.axons):
        if not metagraph.validator_permit[uid] and metagraph.active[uid]:
            synapse = build_query_for(uid)
            response = await dendrite(axons=[axon], synapse=synapse, timeout=12)
            scores[uid] = score_response(response, ground_truth)
    if current_block - last_weights_block >= WeightsRateLimit:
        weights = ema_combine(weights, scores, decay=...)
        subtensor.set_weights(netuid, uids, weights, version_key=...)
        last_weights_block = current_block
    sleep(12)  # ~one block
```

Cadence: metagraph refresh every ~100 blocks; set_weights once per tempo or every `WeightsRateLimit` blocks, whichever is larger.

### Axon / dendrite RPC

- Transport: **HTTP/1.1** via FastAPI/Uvicorn. Not gRPC (older Bittensor versions used gRPC; v6+ moved to HTTP).
- Serialization: JSON of the Synapse Pydantic model.
- Signing: every dendrite request is signed by the validator's hotkey; the axon verifies the signature against the validator's hotkey ss58 derived from the request header. Replay defense: requests carry a `nonce` (timestamp-based) with `~14 second acceptable delta` (per SN18 intel context — the canonical tolerance — though precise tolerance is set by the axon's `BlacklistMiddleware`).
- Endpoint: `POST http://{axon.ip}:{axon.port}/{SynapseClassName}`.

### Subnet validator template patterns

The canonical template at `github.com/opentensor/bittensor-subnet-template` lays out ([tutorials/basic-subnet-tutorials](https://docs.learnbittensor.org/tutorials/basic-subnet-tutorials); [tutorials/ocr-subnet-tutorial](https://docs.learnbittensor.org/tutorials/ocr-subnet-tutorial)):

```
<subnet>/
├── protocol.py            # Synapse class definitions
├── neurons/
│   ├── miner.py
│   └── validator.py
├── <subnet>/
│   ├── base/
│   │   ├── miner.py        # BaseMinerNeuron
│   │   └── validator.py    # BaseValidatorNeuron — calls set_weights
│   ├── validator/
│   │   ├── forward.py      # dendrite query loop
│   │   ├── reward.py       # scoring
│   │   └── generate.py     # challenge generation
│   └── utils/
└── pyproject.toml
```

`BaseValidatorNeuron` runs the main loop; subnet code overrides `forward()` and `reward()`.

### How validators prove they actually evaluated (vs faked scores)

This is the **central problem** for any incentive-mechanism designer. The docs' canonical answers ([learn/anatomy-of-incentive-mechanism](https://docs.learnbittensor.org/learn/anatomy-of-incentive-mechanism)):

1. **Validator-controlled randomness**: validators inject random seeds the miners can't pre-compute against.
2. **Input variation / fuzzing**: minor perturbations defeat caching.
3. **Yuma Consensus itself**: a validator who scores randomly will diverge from the κ-consensus and get clipped. So the *incentive* to score honestly is built into the protocol — fakers lose dividends.
4. **Commit-reveal weights** (`CommitRevealWeightsEnabled`): prevents weight-copying by hiding weights until reveal.

Cathedral-specific: weekly Merkle anchoring of signed receipts is the next layer up — letting **anyone**, not just other validators, audit that the scores were derived from real evaluations. The docs don't prescribe this; it's a Cathedral design choice. It is **canonical** in the sense that several subnets (e.g., SN13 Data Universe, SN1 Apex, SN9 Pretraining) publish verifiable proofs alongside weights.

---

## I. Miner anatomy in practice

### Typical loop

```python
axon = bt.Axon(wallet=wallet, port=8901)
axon.attach(
    forward_fn=handle_synapse,
    blacklist_fn=blacklist_check,   # reject non-validators, stale nonces
    priority_fn=priority_score,     # higher-stake validators served first
)
axon.serve(netuid=netuid, subtensor=subtensor)  # publishes IP:port on-chain
axon.start()
while True: time.sleep(60)          # just stay alive
```

The miner doesn't proactively reach out; it serves and waits. Migration between machines: start new miner first, let validators see updated `AxonInfo`, then stop the old one ([miners](https://docs.learnbittensor.org/miners)).

### Common attacks and defenses

| Attack | Defense |
|---|---|
| **Replay attacks** (validator reuses signed dendrite request) | Nonce + timestamp delta check (~14s tolerance, per SN18 community context — exact value depends on `BlacklistMiddleware` config) |
| **Key collision** (someone registers your hotkey on another subnet) | Hotkey can exist on multiple subnets — that's fine. The miner only serves the netuid it cares about. |
| **Cross-subnet bundle reuse** (validator from subnet A submits same query to subnet B miner) | Miner blacklists requests not signed by registered SN-X validator. Check via `metagraph.hotkeys[uid] == request_hotkey and metagraph.validator_permit[uid]`. |
| **Stake spoofing** (low-stake validator pretending to be high-stake) | Blacklist by `metagraph.S[uid] < threshold`. |
| **Free-rider validators** (weight-copying) | Subnet enables commit-reveal; bonds penalize late aligners. |

### "Miner survives one rogue validator" property

Yuma Consensus is designed so that a single off-consensus validator can't kill a miner's emissions, because the κ=0.5 quantile **requires majority-stake agreement** to clip down. As long as the genuine validators (>50% stake) score a miner highly, that miner survives, even if one or two validators give zero. The rogue validator's own dividends drop instead.

The miner mitigation pattern: just keep serving. Don't try to placate individual validators with custom responses (that breaks the consensus assumption). Optimize for the median.

---

## J. Cross-subnet patterns

This is **the section the docs are weakest on** and where Cathedral is doing genuinely novel work.

### What the docs say

The FAQ explicitly states: "Do subnets talk to each other? Generally no, unless using the new `SubnetsAPI` (Bittensor 6.8.0+). Normally subnets operate independently" ([FAQ](https://docs.learnbittensor.org/resources/questions-and-answers)).

`SubnetsAPI` is a thin layer in the SDK that lets one subnet's validator dendrite-query another subnet's miner using that other subnet's Synapse classes. There's no on-chain coordination — it's purely a client-side convenience. The other subnet's miners can blacklist your validator if they want.

### Real-world patterns

- **SN18 Cortex.t** and **SN21 Inference Subnet** both consume model outputs that miners on other subnets generate, but they do this **off-chain** via Hugging Face / public APIs, not by direct cross-subnet dendrite calls.
- **SN23 NicheImage** historically pulled diffusion outputs.
- **Wombo (SN30)** and **Targon (SN4)** have explored federated patterns.

None of these are documented as a canonical pattern in `docs.learnbittensor.org`. They are all subnet-specific design choices.

### Cathedral's pattern (surfacing SN97 Distil and SN68 NOVA on Cathedral's leaderboard, paying them through Cathedral)

There is no canonical Bittensor pattern for this. The two viable approaches:

1. **Off-chain integration (recommended)**: Cathedral validators read the target subnets' miner outputs from their published artifacts (W&B, GitHub releases, public APIs), score them, include them in Cathedral's weight set as Cathedral UIDs that the foreign miners' coldkeys control. Foreign miners register a Cathedral hotkey, get a UID, and Cathedral emissions flow to their coldkey via the normal Cathedral emission flow. The cross-subnet bridge is operational, not protocol-level.

2. **Direct dendrite via SubnetsAPI**: Cathedral validators query SN97/SN68 axons using those subnets' Synapse classes. Pros: real-time. Cons: requires SN97/SN68 validator hotkeys to be permitted (or those subnets' miners must not blacklist Cathedral). Cathedral would need to stake on SN97/SN68 enough to get a validator permit, OR get the foreign subnet owners to whitelist Cathedral's hotkey.

**Unverified — needs source code or community confirmation:** whether on-chain cross-subnet stake routing (paying SN97 miners directly in SN97 alpha from Cathedral emissions) is achievable without manual unstake/restake. Likely no — emissions land in the netuid where weights are set. Cathedral would need a treasury hotkey that unstakes Cathedral alpha → TAO and restakes on SN97. Worth posting in Bittensor Discord #builders.

---

## K. Recent protocol changes (last ~6 months from May 2026)

From [resources/bittensor-rel-notes](https://docs.learnbittensor.org/resources/bittensor-rel-notes) and inferred from the hyperparameter table:

- **November 2025: Flow-based emissions**. Subnet emissions are now driven by **net TAO inflow EMA** (86.8-day window, p=1 linear). Subnets with net outflows get zero. This replaces price-based subnet weighting. Operational impact: subnet owners need to drive TAO staking, not just alpha price. ([learn/emissions](https://docs.learnbittensor.org/learn/emissions))
- **YC3 rollout**: Available via `sudo_set_yuma3_enabled`. Opt-in, no deadline. New hyperparameters: `AlphaSigmoidSteepness`, `AlphaValues`, `BondsMovingAverage`, `BondsPenalty`, `BondsResetEnabled`. ([yuma3-migration-guide](https://docs.learnbittensor.org/learn/yuma3-migration-guide))
- **Multiple incentive mechanisms per subnet**: A subnet can run N parallel mechanisms with separate bond pools, weighted by `MechanismEmissionSplit`. Controlled by `MechanismCount` (default 1). Useful if Cathedral wants to score "agent output cards" and "agent runtime quality" as independent scoreboards. ([hyperparameters](https://docs.learnbittensor.org/subnets/subnet-hyperparameters); [glossary, Multiple Incentive Mechanisms](https://docs.learnbittensor.org/resources/glossary))
- **Owner hyperparameter rate limit**: `OwnerHyperparamRateLimit` = 2 tempos (≈ 144 min). You cannot spam-update hyperparameters.
- **EVM precompiles**: Subnets can be touched from Solidity via precompiles for staking and ed25519 verification. ChainID 945 on testnet (mainnet unverified). ([evm-tutorials](https://docs.learnbittensor.org/evm-tutorials))
- **Child hotkeys with up to 5 per netuid**: Set/revoke rate-limited to every 150 blocks; take rate adjustable once per 30 days, 0-18%. ([validators/child-hotkeys](https://docs.learnbittensor.org/validators/child-hotkeys))
- **Liquidity positions** (Uniswap V3-style on subnet AMM pools). Opt-in per subnet via `UserLiquidityEnabled` / `toggle_user_liquidity`. ([liquidity-positions](https://docs.learnbittensor.org/liquidity-positions))
- **Coldkey swap with announce/execute delay** (`ColdkeySwapAnnouncementDelay` = 36000 blocks ≈ 5 days).

---

## L. Operational concerns

### Validator hot-wallet security

- Coldkey is encrypted on disk; never online. Keep on a hardware-isolated machine.
- Hotkey is online for `set_weights` signing. Mitigate blast radius with **child hotkeys**: a parent hotkey delegates stake to a per-subnet child hotkey, so a compromised child only exposes one subnet's signing rights ([validators/child-hotkeys](https://docs.learnbittensor.org/validators/child-hotkeys)).
- Recovery via 12+ word mnemonic; back up offline.

### Validator stake threshold

- `StakeThreshold` = 1000 (stake-weight, i.e., `α + 0.18·τ`).
- Top-K = 64 by default — being above the threshold isn't enough; you need top-64 in the subnet.
- Acquiring stake: stake your own TAO/alpha, attract delegations, or both. Validators with public identities (set via `btcli wallet set-identity`, 1 TAO fee) tend to attract more.

### Emissions visibility

- **taostats.io** — per-subnet emissions, validator/miner tables, weight history.
- **tao.app** — newer dashboard with Savant AI assistant; runtime upgrade timeline at `tao.app/runtime`.
- **taomarketcap.com** — alpha token prices.
- SDK: `subtensor.metagraph(netuid).E` returns the per-UID emission rate (per tempo, in alpha).

### Owner controls

From the [hyperparameters](https://docs.learnbittensor.org/subnets/subnet-hyperparameters) table, the owner can adjust (rate-limited to once per 2 tempos):

- Burn floor/ceiling and decay (`MinBurn`, `MaxBurn`, `BurnHalfLife`, `BurnIncreaseMult`).
- Activity/immunity (`ActivityCutoff`, `ImmunityPeriod`).
- Weight constraints (`MaxAllowedUids` via `sudo_trim_to_max_allowed_uids`, `MinAllowedWeights`, `WeightsVersion`).
- Commit-reveal (`CommitRevealWeightsEnabled`, `CommitRevealPeriod`).
- Bonds (`BondsMovingAverage`, `BondsPenalty`, `BondsResetEnabled`).
- YC3 (`YumaVersion`, `AlphaSigmoidSteepness`, `AlphaValues`).
- Liquidity (`UserLiquidityEnabled`).
- Hotkey of record (`SubnetOwnerHotkey`).
- Recycle vs Burn (`RecycleOrBurn`).
- Mechanism count and emission split (`MechanismCount`, `MechanismEmissionSplit`).

The owner **cannot** change Tempo, Kappa, Difficulty range, MaxAllowedValidators, MinAllowedUids, MaxRegistrationsPerBlock, WeightsRateLimit, NetworkRateLimit — those are root-controlled.

---

## M. Failure modes specific to Bittensor

### Validator weights nullified

| Reason | Mechanism | Recovery |
|---|---|---|
| Lost validator permit | Stake dropped below top-K threshold; bonds wiped | Re-stake to top-K |
| `NeuronNoValidatorPermit` on set_weights | Same as above | Stake up |
| `SettingWeightsTooFast` | Called set_weights within `WeightsRateLimit` (100 blocks) | Wait |
| `NotEnoughStakeToSetWeights` | Below `StakeThreshold = 1000` | Stake up |
| `WeightVecNotEqualSize` / `DuplicateUids` / `UidsLengthExceedUidsInSubNet` | Malformed extrinsic | Fix client code |
| Off-consensus weights | Bonds collapse, dividends → 0 | Realign to median |
| `IncorrectWeightVersionKey` | Subnet rotated its weight schema version | Match `version_key` |
| `CommitRevealEnabled` error on set_weights | Subnet now uses commit-reveal | Switch to `commit_weights` |

### Miner gets 0 emissions despite serving

- Axon IP/port wrong or unreachable from validators' POV → no scoring data.
- `serve_axon` not called recently enough → stale `AxonInfo`.
- Validators blacklist the miner (low stake / blacklist rule).
- Miner inactive per `ActivityCutoff` (no recent `LastUpdate`).
- Genuine: miner output below consensus quality → low `Incentive_j`.
- Subnet running 90% burn (most emissions routed to owner UID 0).

### Re-registration vs immunity period

When deregistered, the UID slot is taken by a new hotkey. The new hotkey starts its own `immunity_period` clock from the registration block. Old emissions and bonds tied to the previous hotkey at that UID are discarded.

### Reorg semantics

Substrate has probabilistic finality (Grandpa, 1-2 block lag). An unfinalized `set_weights` could in principle be reorged out. Production guidance: poll for transaction inclusion + finality before treating set_weights as authoritative. **Unverified — needs source code or community confirmation:** actual Grandpa finality depth and reorg frequency on finney mainnet.

---

## N. Resources

### Primary sources

- **Docs**: https://docs.learnbittensor.org (Latent Holdings; current canonical docs)
- **Subtensor chain**: https://github.com/opentensor/subtensor (Rust, Substrate / Polkadot SDK)
- **SDK**: https://github.com/opentensor/bittensor (Python)
- **btcli**: https://github.com/opentensor/btcli
- **Subnet template**: https://github.com/opentensor/bittensor-subnet-template
- **Bittensor Wallet**: https://github.com/opentensor/btwallet
- **Whitepapers**: https://bittensor.com/whitepaper, https://bittensor.com/content/the-bittensor-standard

### Explorers and dashboards

- https://taostats.io — primary explorer, validator/miner tables, weight history
- https://tao.app — modern dashboard, runtime upgrade timeline at /runtime
- https://taomarketcap.com — alpha pricing
- https://learnbittensor.org/subnets — subnet directory

### Community

- Discord: https://discord.com/invite/bittensor (#builders and #subnet-XX channels)
- Twitter: [@opentensor](https://x.com/opentensor)
- Podcast: Novelty Search (YouTube, @Opentensor)

---

## Open questions Cathedral will need to resolve

Items the docs **do not** answer. Resolve via source-code dives, Discord, or direct experimentation:

1. **Cross-subnet emissions routing**: can Cathedral programmatically forward emissions from Cathedral's UIDs to SN97/SN68 miners' coldkeys without manual unstake/restake? Likely needs an off-chain treasury hotkey acting as router. Confirm in #builders or by reading `pallets/subtensor/src/coinbase/run_coinbase.rs`.

2. **Grandpa finality depth and reorg risk for `set_weights`**: the docs don't quantify finality lag or reorg frequency on finney. Read `subtensor/runtime/src/lib.rs` or query a validator-operator on Discord.

3. **Exact replay-protection nonce tolerance**: the dendrite `BlacklistMiddleware` enforces a nonce-age window. Community context puts it around 14 seconds, but the canonical default lives in `bittensor/axon.py` — read source to confirm Cathedral's tolerance setting matches mainstream subnets, especially if Cathedral validators run in geographically distant regions.

4. **Behavior of `MechanismEmissionSplit` with multiple mechanisms**: how exactly are emissions partitioned, and how do bonds isolate across mechanisms? The hyperparameter is listed but the math isn't in the docs. Read `pallets/subtensor/src/coinbase/run_coinbase.rs` or test on devnet. Relevant if Cathedral wants separate scoreboards for "agent card output" vs "agent runtime quality."

5. **`RecycleOrBurn` semantics for the burn pattern**: confirmed for registration cost destination, but whether emissions to UID 0 are recyclable (subtracted from issuance) vs just-held in the owner's wallet is unverified. If Cathedral wants a *true* deflationary burn (reduce TAO supply, not enrich the owner), this matters and may require a custom destruction step (unstake → burn TAO via a sink address).

"""
Benchmark scalecodec encoding/decoding speed.

Run with cy-scale-codec (source):
    PYTHONPATH=. python benchmarks/bench.py

Run with installed scalecodec (e.g. py-scale-codec):
    python benchmarks/bench.py

Save a baseline then compare:
    python benchmarks/bench.py --save-baseline benchmarks/baseline_py.json
    PYTHONPATH=. python benchmarks/bench.py --compare benchmarks/baseline_py.json

Note: AccountId benchmarks use SS58 format 42 to reflect real-world usage.
      bt_decode is excluded from the batch_decode section because it does not
      perform SS58 encoding; a direct comparison without that step is unfair.
"""

import json
import os
import re
import struct
import timeit
import argparse

import scalecodec

print(f"scalecodec: {scalecodec.__file__}", flush=True)

from scalecodec.base import RuntimeConfiguration, RuntimeConfigurationObject, ScaleBytes
from scalecodec.type_registry import load_type_registry_preset, load_type_registry_file

_HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = load_type_registry_file(
    os.path.join(_HERE, "..", "test", "fixtures", "metadata_hex.json")
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compact_encode(n: int) -> bytes:
    if n <= 0x3F:
        return bytes([n << 2])
    if n <= 0x3FFF:
        return struct.pack("<H", (n << 2) | 1)
    if n <= 0x3FFFFFFF:
        return struct.pack("<I", (n << 2) | 2)
    raise ValueError(f"n={n} too large for compact")


def _hex_to_ba(s: str) -> bytearray:
    """Parse '0x...' or bare hex to bytearray."""
    return bytearray.fromhex(s[2:] if s.startswith("0x") else s)


def _sb(ba: bytearray) -> ScaleBytes:
    """Create a fresh ScaleBytes (offset=0) wrapping a pre-allocated bytearray."""
    return ScaleBytes(ba)


def _vec_ba(element_hex: str, count: int) -> bytearray:
    element = bytes.fromhex(element_hex)
    return bytearray(_compact_encode(count)) + element * count


def _load_v10_metadata_hex() -> str:
    path = os.path.join(_HERE, "..", "test", "test_type_registry.py")
    with open(path) as f:
        content = f.read()
    m = re.search(r'metadata_v10_hex\s*=\s*"(0x[0-9a-f]+)"', content)
    assert m
    return m.group(1)


def _setup_legacy():
    RuntimeConfiguration().clear_type_registry()
    RuntimeConfiguration().update_type_registry(load_type_registry_preset("core"))
    RuntimeConfiguration().update_type_registry(load_type_registry_preset("legacy"))
    RuntimeConfiguration().update_type_registry(load_type_registry_preset("kusama"))
    RuntimeConfiguration().set_active_spec_version_id(1045)


def run(fn, n: int) -> float:
    return timeit.timeit(fn, number=n) / n * 1e6


def header(title: str):
    print(f"\n### {title}\n")
    print(f"| {'Benchmark':<44} | {'µs/call':>9} | {'calls':>9} |")
    print(f"|{'-' * 46}|{'-' * 11}:|{'-' * 11}:|")


def row(name: str, us: float, n: int, results: dict):
    print(f"| {name:<44} | {us:>9.2f} | {n:>9,} |", flush=True)
    results[name] = us


# ---------------------------------------------------------------------------
# SHORT benchmarks — primitives, small types
# ---------------------------------------------------------------------------


def bench_short(results: dict):
    _setup_legacy()
    rc = RuntimeConfiguration()
    header("SHORT — primitives and small types")
    N = 200_000

    for type_str, hex_data in [
        ("u8", "ff"),
        ("u16", "0102"),
        ("u32", "01020304"),
        ("u64", "0102030405060708"),
        ("u128", "0102030405060708090a0b0c0d0e0f10"),
    ]:
        ba = _hex_to_ba(hex_data)
        row(
            f"{type_str} decode",
            run(lambda t=type_str, b=ba: rc.create_scale_object(t, _sb(b)).decode(), N),
            N,
            results,
        )

    # Compact<u32> — 4-byte mode, value=1073741823 → 0xfeffffff
    ba = _hex_to_ba("feffffff")
    row(
        "Compact<u32> decode",
        run(lambda: rc.create_scale_object("Compact<u32>", _sb(ba)).decode(), N),
        N,
        results,
    )

    # bool
    ba = _hex_to_ba("01")
    row(
        "bool decode",
        run(lambda: rc.create_scale_object("bool", _sb(ba)).decode(), N),
        N,
        results,
    )

    # H256
    ba = _hex_to_ba("ab" * 32)
    row(
        "H256 decode",
        run(lambda: rc.create_scale_object("H256", _sb(ba)).decode(), N),
        N,
        results,
    )

    # AccountId — SS58 format 42 (real-world usage)
    ba = _hex_to_ba("01" * 32)
    rc.ss58_format = 42
    row(
        "AccountId decode (SS58 format 42)",
        run(lambda: rc.create_scale_object("AccountId", _sb(ba)).decode(), N),
        N,
        results,
    )
    rc.ss58_format = None

    # Str — "Hello World!" (12 bytes)
    ba = _hex_to_ba("3048656c6c6f20576f726c6421")
    row(
        "Str decode",
        run(lambda: rc.create_scale_object("Str", _sb(ba)).decode(), N),
        N,
        results,
    )

    # Tuple
    ba = bytearray(
        _hex_to_ba("01020304") + _hex_to_ba("0102030405060708") + _hex_to_ba("01")
    )
    row(
        "(u32, u64, bool) decode",
        run(lambda: rc.create_scale_object("(u32, u64, bool)", _sb(ba)).decode(), N),
        N,
        results,
    )

    # Encode
    row(
        "u32 encode",
        run(lambda: rc.create_scale_object("u32").encode(305419896), N),
        N,
        results,
    )
    row(
        "u64 encode",
        run(lambda: rc.create_scale_object("u64").encode(72623859790382856), N),
        N,
        results,
    )
    row(
        "Compact<u32> encode",
        run(lambda: rc.create_scale_object("Compact<u32>").encode(1073741823), N),
        N,
        results,
    )
    ba_h256 = "0x" + "ab" * 32
    row(
        "H256 encode",
        run(lambda: rc.create_scale_object("H256").encode(ba_h256), N),
        N,
        results,
    )


# ---------------------------------------------------------------------------
# LONG benchmarks — Vec, events, metadata
# ---------------------------------------------------------------------------


def bench_long(results: dict):
    _setup_legacy()
    rc = RuntimeConfiguration()
    header("LONG — Vec, events, and metadata")

    # Vec<u32>
    for count, n in [(64, 20_000), (1_024, 2_000), (16_384, 100)]:
        ba = _vec_ba("01020304", count)
        row(
            f"Vec<u32> decode ({count:,} elements)",
            run(lambda b=ba: rc.create_scale_object("Vec<u32>", _sb(b)).decode(), n),
            n,
            results,
        )

    # Bytes (Vec<u8>)
    for kb, n in [(1, 20_000), (64, 500), (512, 50)]:
        ba = _vec_ba("ab", kb * 1024)
        row(
            f"Bytes decode ({kb} KB)",
            run(lambda b=ba: rc.create_scale_object("Bytes", _sb(b)).decode(), n),
            n,
            results,
        )

    # Vec<EventRecord> — 5-event Kusama block payload (legacy V10 metadata)
    events_ba = _hex_to_ba(
        "14000000000000001027000001010000010000000000102700000001000002000000"
        "000040420f0000010000030000000d05e8f6971c000000000000000000000000000003"
        "000000000101060020a10700000100"
    )
    v10_hex = _load_v10_metadata_hex()
    meta_v10 = rc.create_scale_object("MetadataVersioned", ScaleBytes(v10_hex))
    meta_v10.decode()
    rc.set_active_spec_version_id(1020)
    n = 2_000
    row(
        "Vec<EventRecord> decode (5 events, V10)",
        run(
            lambda: rc.create_scale_object(
                "Vec<EventRecord>", _sb(events_ba), metadata=meta_v10
            ).decode(),
            n,
        ),
        n,
        results,
    )

    # Metadata decode — V10 (85 KB)
    v10_ba = _hex_to_ba(v10_hex)
    n = 30
    row(
        "MetadataVersioned decode (V10, 85 KB)",
        run(
            lambda: rc.create_scale_object("MetadataVersioned", _sb(v10_ba)).decode(), n
        ),
        n,
        results,
    )

    # Metadata decode — V13 (219 KB)
    v13_ba = _hex_to_ba(FIXTURES["V13"])
    n = 10
    row(
        "MetadataVersioned decode (V13, 219 KB)",
        run(
            lambda: rc.create_scale_object("MetadataVersioned", _sb(v13_ba)).decode(), n
        ),
        n,
        results,
    )

    # Metadata decode — V14 (300 KB)
    v14_ba = _hex_to_ba(FIXTURES["V14"])
    n = 5
    row(
        "MetadataVersioned decode (V14, 300 KB)",
        run(
            lambda: rc.create_scale_object("MetadataVersioned", _sb(v14_ba)).decode(), n
        ),
        n,
        results,
    )

    # Full pipeline: Bittensor metadata decode + add_portable_registry (254 KB)
    bt_ba = _hex_to_ba(FIXTURES["bittensor_test"])

    def _bt_full():
        rc2 = RuntimeConfigurationObject()
        rc2.update_type_registry(load_type_registry_preset("core"))
        rc2.update_type_registry(load_type_registry_preset("legacy"))
        m = rc2.create_scale_object("MetadataVersioned", _sb(bt_ba))
        m.decode()
        rc2.add_portable_registry(m)

    n = 3
    row("Bittensor metadata + portable registry (254 KB)", run(_bt_full, n), n, results)


# ---------------------------------------------------------------------------
# BATCH_DECODE benchmarks — cyscale batch_decode vs individual decode loop
#
# bt_decode is intentionally excluded: it does not perform SS58 encoding, so
# any comparison without that post-processing step would be unfair. The loop
# baseline here represents the actual work being replaced (including SS58).
# ---------------------------------------------------------------------------


def bench_batch_decode(results: dict):
    rc = RuntimeConfigurationObject()
    rc.update_type_registry(load_type_registry_preset("core"))
    rc.update_type_registry(load_type_registry_preset("legacy"))
    rc.set_active_spec_version_id(1045)
    rc.ss58_format = 42

    has_batch = hasattr(rc, "batch_decode")
    header("BATCH_DECODE — cyscale batch_decode vs py-scale-codec loop")

    account_ba = bytes(_hex_to_ba("01" * 32))
    u32_ba = bytes(_hex_to_ba("01020304"))
    u128_ba = bytes(_hex_to_ba("0102030405060708090a0b0c0d0e0f10"))

    for count, n in [(10, 50_000), (100, 5_000), (1_000, 500)]:
        type_strings = ["AccountId"] * count
        bytes_list = [account_ba] * count

        loop_us = run(
            lambda ts=type_strings, bl=bytes_list: [
                rc.create_scale_object(t, ScaleBytes(b)).decode()
                for t, b in zip(ts, bl)
            ],
            n,
        )
        if has_batch:
            row(
                f"batch_decode AccountId ×{count:,}",
                run(lambda ts=type_strings, bl=bytes_list: rc.batch_decode(ts, bl), n),
                n,
                results,
            )
            row(f"loop decode   AccountId ×{count:,}", loop_us, n, results)
        else:
            # py-scale-codec: record loop time under the batch_decode key so
            # --compare shows cyscale batch_decode vs this loop baseline
            row(f"batch_decode AccountId ×{count:,}", loop_us, n, results)

    # Mixed types (closer to real query_map workload)
    count = 100
    n = 5_000
    mixed_types = ["AccountId", "u32", "u128"] * (count // 3) + ["AccountId"]
    mixed_bytes = [account_ba, u32_ba, u128_ba] * (count // 3) + [account_ba]

    mixed_loop_us = run(
        lambda: [
            rc.create_scale_object(t, ScaleBytes(b)).decode()
            for t, b in zip(mixed_types, mixed_bytes)
        ],
        n,
    )
    if has_batch:
        row(
            f"batch_decode mixed (AccountId/u32/u128) ×{count}",
            run(lambda: rc.batch_decode(mixed_types, mixed_bytes), n),
            n,
            results,
        )
        row(
            f"loop decode   mixed (AccountId/u32/u128) ×{count}",
            mixed_loop_us,
            n,
            results,
        )
    else:
        row(
            f"batch_decode mixed (AccountId/u32/u128) ×{count}",
            mixed_loop_us,
            n,
            results,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save-baseline", metavar="FILE")
    parser.add_argument("--compare", metavar="FILE")
    args = parser.parse_args()

    results: dict = {}
    bench_short(results)
    bench_long(results)
    bench_batch_decode(results)

    if args.compare:
        with open(args.compare) as f:
            baseline = json.load(f)
        print(f"\n### Speedup vs {os.path.basename(args.compare)}\n")
        print(
            f"| {'Benchmark':<44} | {'baseline':>9} | {'current':>9} | {'speedup':>8} |"
        )
        print(f"|{'-' * 46}|{'-' * 11}:|{'-' * 11}:|{'-' * 10}:|")
        for name, cur in results.items():
            if name in baseline:
                ratio = baseline[name] / cur
                marker = " ◀" if ratio < 0.95 else ""
                print(
                    f"| {name:<44} | {baseline[name]:>9.2f} | {cur:>9.2f} | {ratio:>7.2f}×{marker:2} |"
                )

    if args.save_baseline:
        with open(args.save_baseline, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nBaseline saved to {args.save_baseline}")


if __name__ == "__main__":
    main()

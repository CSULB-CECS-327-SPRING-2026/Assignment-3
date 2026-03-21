"""
simulator.py — Test Harness / Experiment Driver
CECS 327 Assignment — Thanh Tran (033793017)

Runs three required experiments:
  1. Concurrent conflicting updates (put + append on the same key)
  2. High contention  (30 deposits to the same account across 4 replicas)
  3. Non-conflicting updates (increments to different keys — total order still preserved)

Each experiment:
  - Launches N replicas connected by a SimulatedNetwork
  - Fires client updates concurrently from random target replicas
  - Waits for convergence
  - Verifies that every replica has the same final store AND the same delivery order

Usage:
    python simulator.py                 # runs all three experiments
    python simulator.py --verbose       # also prints per-replica logs
"""

import argparse
import random
import sys
import threading
import time
from typing import List, Tuple, Any

from network import SimulatedNetwork
from replica import Replica


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------

def run_experiment(
    name: str,
    num_replicas: int,
    updates: List[Tuple[int, str, Tuple]],   # (target_replica_id, update_id, op)
    delay_min: float = 0.002,
    delay_max: float = 0.05,
    seed: int = 42,
    settle_time: float = 1.5,
    verbose: bool = False,
) -> bool:
    """
    Run one experiment.  Returns True iff all correctness checks pass.

    Parameters
    ----------
    updates : list of (target_replica_id, update_id, op)
        Describes what to send and to which replica.
    settle_time : float
        Seconds to wait after all sends before checking results.
    """
    SEP = "=" * 68
    print(f"\n{SEP}")
    print(f"  EXPERIMENT: {name}")
    print(f"  Replicas: {num_replicas}  |  Updates: {len(updates)}"
          f"  |  Delay: [{delay_min*1000:.0f}ms, {delay_max*1000:.0f}ms]"
          f"  |  Seed: {seed}")
    print(SEP)

    # Build network + replicas
    net = SimulatedNetwork(delay_min=delay_min, delay_max=delay_max, seed=seed)
    replicas = [Replica(i, num_replicas, net) for i in range(num_replicas)]
    for r in replicas:
        net.register(r)
    net.start()

    # Fire all updates concurrently (with a tiny random jitter to maximise reordering)
    rng = random.Random(seed + 1)
    threads = []
    for (target, uid, op) in updates:
        jitter = rng.uniform(0, 0.008)

        def _send(r=target, u=uid, o=op, j=jitter):
            time.sleep(j)
            replicas[r].client_update(u, o)

        t = threading.Thread(target=_send, daemon=True)
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Wait for all in-flight messages to settle
    time.sleep(settle_time)
    net.stop()

    # ------------------------------------------------------------------
    # Correctness check
    # ------------------------------------------------------------------
    print("\n--- Final States ---")
    for r in replicas:
        print(f"  R{r.id}:  store={r.store}")
        print(f"       delivered_order={r.delivered_log}")

    stores_ok  = all(r.store        == replicas[0].store        for r in replicas)
    order_ok   = all(r.delivered_log == replicas[0].delivered_log for r in replicas)
    deliver_ok = all(len(r.delivered_log) == len(updates) for r in replicas)

    print()
    print(f"  All stores identical   : {'✓' if stores_ok  else '✗ FAIL'}")
    print(f"  All delivery orders identical : {'✓' if order_ok   else '✗ FAIL'}")
    print(f"  All updates delivered  : {'✓' if deliver_ok else '✗ FAIL'}")

    passed = stores_ok and order_ok and deliver_ok
    print(f"\n  {'✓ CORRECTNESS CHECK PASSED' if passed else '✗ CORRECTNESS CHECK FAILED'}")

    if verbose or not passed:
        for r in replicas:
            print(f"\n--- R{r.id} full trace ---")
            for line in r.log:
                print(f"  {line}")

    print(SEP)
    return passed


# ---------------------------------------------------------------------------
# Experiment definitions
# ---------------------------------------------------------------------------

def experiment_1_conflicting(verbose=False) -> bool:
    """
    Experiment 1 — Concurrent conflicting updates.

    Four operations on the same key 'x' sent to different replicas concurrently.
    Without total-order multicast, replicas could apply them in different orders
    and diverge.  With TOB, every replica must end up with the same final value.
    """
    updates = [
        (0, 'upd-01', ('put',    'x', 'hello')),   # R0 sends put
        (1, 'upd-02', ('append', 'x', '_world')),   # R1 sends append
        (2, 'upd-03', ('put',    'x', 'reset')),    # R2 sends put
        (0, 'upd-04', ('append', 'x', '_final')),   # R0 sends append again
    ]
    return run_experiment(
        name="Concurrent Conflicting Updates (put + append on same key)",
        num_replicas=3,
        updates=updates,
        delay_min=0.002,
        delay_max=0.04,
        seed=42,
        settle_time=1.5,
        verbose=verbose,
    )


def experiment_2_high_contention(verbose=False) -> bool:
    """
    Experiment 2 — High contention.

    30 deposit operations to the same 'account' key spread across 4 replicas.
    Correct result: every replica sees account = 30 * 100 = 3000.
    """
    updates = []
    for i in range(30):
        target = i % 4
        updates.append((target, f'upd-{i:02d}', ('deposit', 'account', 100)))

    return run_experiment(
        name="High Contention (30 deposits, 4 replicas)",
        num_replicas=4,
        updates=updates,
        delay_min=0.001,
        delay_max=0.03,
        seed=7,
        settle_time=2.5,
        verbose=verbose,
    )


def experiment_3_non_conflicting(verbose=False) -> bool:
    """
    Experiment 3 — Non-conflicting updates (different keys).

    15 increments spread across 5 keys and 3 replicas.
    Even though operations don't conflict, total order must still be consistent —
    the delivered_log must match across all replicas.
    """
    keys = ['a', 'b', 'c', 'd', 'e']
    updates = []
    for i in range(15):
        target = i % 3
        key    = keys[i % len(keys)]
        updates.append((target, f'upd-{i:02d}', ('incr', key)))

    return run_experiment(
        name="Non-Conflicting Updates (different keys, total order still verified)",
        num_replicas=3,
        updates=updates,
        delay_min=0.002,
        delay_max=0.05,
        seed=99,
        settle_time=1.5,
        verbose=verbose,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="TOB-cast experiment runner")
    parser.add_argument('--verbose', '-v', action='store_true',
                        help="Print full per-replica trace logs")
    args = parser.parse_args()

    results = {
        "Experiment 1 (conflicting)":     experiment_1_conflicting(args.verbose),
        "Experiment 2 (high contention)": experiment_2_high_contention(args.verbose),
        "Experiment 3 (non-conflicting)": experiment_3_non_conflicting(args.verbose),
    }

    print("\n" + "=" * 68)
    print("  SUMMARY")
    print("=" * 68)
    all_passed = True
    for name, ok in results.items():
        status = "PASS ✓" if ok else "FAIL ✗"
        print(f"  {status}  {name}")
        if not ok:
            all_passed = False

    print("=" * 68)
    sys.exit(0 if all_passed else 1)


if __name__ == '__main__':
    main()

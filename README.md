# Total-Order Multicast — Replicated Key-Value Store
**CECS 327 Distributed Systems**  
**Group Members:** Thanh Tran, Win Quy Nguyen, Kent Do
---

## Architecture Diagram

```
Clients
  |          \          (any client can target any replica)
  v           v
+----+    +----+    +----+    +----+
| R0 |    | R1 |    | R2 |    | R3 |
+----+    +----+    +----+    +----+
   \          |         |         /
    \---------|---------|--------/
         Total-Order Multicast
    (TOBCAST + ACK, holdback queues)

Per-replica state:
  clock_i        — Lamport logical clock
  holdback_queue — min-heap ordered by (ts, sender_id)
  max_seen[k]    — max timestamp seen in any message FROM replica k
  store          — key-value application state

Message flow for one update (N=3):

Client → R0                          R1                           R2
           |                          |                            |
           |--TOBCAST(ts=5,sid=0)---->|                            |
           |--TOBCAST(ts=5,sid=0)------------------------>        |
           |                          |                            |
           |<--ACK(ack_ts=6,from=R1)--|                            |
           |<--ACK(ack_ts=6,from=R2)------------------------|      |
           |                          |<--ACK(ack_ts=6,from=R0)----|
           |                          |<--ACK(ack_ts=6,from=R2)----|
           ...
           |-- (when max_seen[k] > 5 for all k) DELIVER upd-01 --|
```

---

## File Structure

```
tobcast/
├── replica.py          # Core: Lamport clock, holdback queue, delivery rule, KV store
├── network.py          # SimulatedNetwork: FIFO-per-sender, random delays
├── simulator.py        # Test harness: 3 experiments + correctness checker
├── experiment_logs.txt # Captured verbose output from all experiments
├── README.md           # This file
└── part_c.md           # Written answers for Part C
```

---

## How to Run

**Requirements:** Python 3.8+ (standard library only — no pip installs needed)

```bash
cd tobcast

# Run all three experiments (summary output)
python simulator.py

# Run with full per-replica trace logs
python simulator.py --verbose
```

**Expected output (all three experiments):**
```
  PASS ✓  Experiment 1 (conflicting)
  PASS ✓  Experiment 2 (high contention)
  PASS ✓  Experiment 3 (non-conflicting)
```

---

## System Model (Assumptions)

| Assumption | Implementation |
|---|---|
| Reliable message delivery | `SimulatedNetwork` never drops messages |
| FIFO per (sender, receiver) channel | One `queue.Queue` + one worker thread per ordered pair |
| Random network delays | Each message sleeps `Uniform(delay_min, delay_max)` on its channel thread |
| Messages from *different* senders may be reordered at receiver | Independent channel threads run in parallel |
| Lamport clocks with tie-break by replica ID | `_tick()` + `TOBMessage.__lt__` using `(ts, sender_id)` |

---

## Algorithm Summary (Part A)

### Message Types

| Message | Fields | Purpose |
|---|---|---|
| `TOBCAST` | `update_id, op, ts, sender_id` | Announce a new update to all replicas |
| `ACK` | `update_id, orig_ts, orig_sender, ack_ts, ack_sender` | Prove that `ack_sender`'s clock is `ack_ts > orig_ts` |

### Timestamp Rule

When replica R_i sends a TOBCAST for an update with id `u`:
1. `clock_i += 1` → `ts`
2. Stamp the message `(ts, i)`
3. `clock_i += 1` → `ack_ts` (for self-ACK, ensuring `max_seen[i] = ack_ts > ts`)

### Holdback Queue Ordering

Messages in the queue are ordered by `(ts, sender_id)` (min-heap). Ties broken by lower `sender_id`. This gives a deterministic total order consistent with causality.

### Delivery Condition

Replica R_i may deliver (apply) the head message `m` iff:

> **For every replica R_k: `max_seen[k] > m.ts`**

**Why this is correct:** `max_seen[k] > m.ts` means we've seen a message from R_k with timestamp strictly greater than `m.ts`. Because Lamport clocks are monotonically increasing, R_k will never send a future TOBCAST with `ts ≤ m.ts`. Combined with FIFO channels (any in-flight message from R_k with `ts' ≤ max_seen[k]` is already in our queue), we know we have all messages that could precede `m` in the total order.

---

## Experiments (Part B)

### Experiment 1 — Concurrent Conflicting Updates

- **Setup:** 3 replicas, 4 operations on the same key `x` (put + append), fired concurrently
- **Why it tests correctness:** Without total order, replicas would apply puts/appends in different orders and diverge
- **Result:** All replicas converge to `x = 'reset_final'` with identical delivery order

### Experiment 2 — High Contention

- **Setup:** 4 replicas, 30 `deposit(account, 100)` operations fired concurrently
- **Why it tests correctness:** With commutative operations, stores will still match, but the delivery order must still be identical everywhere (and final balance must be exactly 3000)
- **Result:** All replicas: `account = 3000`, identical 30-item delivery sequence

### Experiment 3 — Non-Conflicting Updates

- **Setup:** 3 replicas, 15 `incr` operations on 5 different keys
- **Why it tests correctness:** Even when operations don't conflict, total order must still be consistent — the delivered_log must match across all replicas
- **Result:** All replicas: `{a:3, b:3, c:3, d:3, e:3}`, identical 15-item delivery sequence

---

## Design Decisions

**Why `ack_ts` in ACK instead of just echoing `orig_ts`?**
The delivery condition requires `max_seen[k] > m.ts` (strict). If we only echoed `orig_ts`, seeing an ACK from k would set `max_seen[k] = orig_ts`, which is not `> orig_ts`. By including the sender's *current* clock in the ACK (`ack_ts = max(clock_k, orig_ts) + 1`), we guarantee `max_seen[k] = ack_ts > orig_ts` as soon as we receive the ACK.

**Why not send to self via the network?**
The sender handles its own self-ACK directly in `client_update` by incrementing the clock a second time and updating `max_seen[self.id]` locally. This avoids a race condition where the sender's own ACK might not arrive before it tries to deliver.

**Thread safety:**
Each `Replica` has a single `threading.Lock` that serialises all clock increments, queue mutations, and delivery checks. The `SimulatedNetwork` worker threads acquire this lock (via `receive()`) when delivering messages.

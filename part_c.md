# Part C — Written Questions
**CECS 327 — Distributed Systems**
**Student:** Thanh Tran | **ID:** 033793017

---

## C1. Why does replication need total ordering for conflicting operations?

Without a total order, different replicas may apply conflicting writes in different sequences and permanently diverge.

**Concrete example.** Two replicas R1 and R2 share key `x`. Client A sends `put(x, "Alice")` to R1 and client B simultaneously sends `put(x, "Bob")` to R2. Without total ordering:

- R1 applies Alice first, then Bob → `x = "Bob"`
- R2 applies Bob first, then Alice → `x = "Alice"`

The replicas now disagree and no further messages will reconcile them. Total-order multicast forces every replica to agree on one ordering — say Alice before Bob — so every replica converges to `x = "Bob"`. Commutativity saves non-conflicting operations (e.g., incrementing two different keys), but any write-write or write-read conflict on the same key requires global agreement on order.

---

## C2. What do Lamport clocks guarantee and what do they not guarantee?

**What they guarantee (the Clock Condition):** If event *a* happened-before event *b* (i.e., *a → b* in Lamport's relation), then L(a) < L(b). Equivalently, the logical clock respects causality: a message's timestamp at the sender is strictly less than the receiver's updated timestamp after receiving it.

**What they do not guarantee:**

| Claim | True? |
|---|---|
| L(a) < L(b) implies a happened-before b | ❌ No — concurrent events can have any clock ordering |
| Clocks reflect real (wall-clock) time | ❌ No — a slow process's clock can lag far behind real time |
| Clocks alone give a total order | ❌ No — ties (L(a) = L(b)) are possible for concurrent events |

**Total order with tie-breaking:** By appending the replica's ID and resolving ties by the lower ID, we extend the partial causal order into a *consistent* total order. This is the tie-break used in the holdback queue: messages are sorted by `(ts, sender_id)`, and a smaller `sender_id` wins any tie. The resulting order is consistent (it extends →) but is not the *only* valid total order — any total extension of the partial order would also be correct.

---

## C3. What breaks if messages can be lost or delivered out of FIFO order?

**Lost messages.** The delivery condition requires `max_seen[k] > m.ts` for every replica k. If a TOBCAST from k is lost, the recipient never adds it to its holdback queue. Other replicas that *did* receive it will eventually deliver it, while the replica that missed it never will — causing permanent divergence. Even worse, if the missing message's ACK never arrives, some replicas' holdback queues may block forever (waiting for `max_seen[k]` to advance past `m.ts`).

**Out-of-FIFO delivery.** The algorithm relies on FIFO channels for a critical safety property: if replica k sends an ACK(ack_ts) *after* a TOBCAST(ts), we can be sure we already have the TOBCAST by the time we process the ACK. If messages can arrive out of order, a replica could receive ACK(ack_ts > m.ts) from k — advancing `max_seen[k]` and triggering delivery of m — while k's TOBCAST for a message with *ts' < m.ts* is still in transit. The replica would deliver m prematurely without having seen every message that precedes it, breaking the total order.

---

## C4. Where is the "coordination" happening in your implementation?

The coordination lives entirely in the **middleware layer** — the `Replica` class's message-handling methods (`_handle_tobcast`, `_handle_ack`, `_try_deliver`). Specifically:

- **Holdback queue** — enforces ordering before delivery; messages are buffered here until safe.
- **ACK protocol** — each replica broadcasts an ACK after receiving a TOBCAST, advancing `max_seen` at all other replicas and enabling the delivery condition.
- **Delivery condition** (`max_seen[k] > m.ts` for all k) — the actual coordination gate, implemented in `_can_deliver`.

The **application** (`_apply`) has zero coordination logic: it simply executes the operation (put/append/deposit/etc.) on the local store. It is completely unaware of the distributed setting. This is the classic middleware vs. application split: total-order multicast is a reusable service that any replicated application can sit on top of, with no changes to application logic.

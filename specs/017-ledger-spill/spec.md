# 017 — Spend rows survive a process that exits while the store is locked

## Problem
Spec 014 keeps a ledger row in memory when the store is locked and writes it with the next call. A short-lived
process (a hook, the session worker) can exit before the lock is released, and its rows are lost. Measured on
2026-11-16 with two real processes: one held the write lock for 6 seconds, the other recorded a call and exited;
the row was gone.

## Change
- `Brain.close()` writes rows it still could not store to `.cairn/ledger.pending`, one JSON row per line.
- `Brain.flush_calls()` first takes that file (an atomic rename, so only one process gets it) and writes its rows
  with its own.

## Not covered
A process that is killed without closing the store still loses the rows it held.

## Done when
The two-process check shows every row in the ledger after the next process records a call, and the pending file
is gone.

# Tasks — 017 ledger spill
- [x] T001 Spill unwritten rows at close, take them at the next flush (src/cairn/store.py)
- [x] T002 Test: close while locked, next process writes both rows, damaged lines skipped (tests/test_dogfood_fixes.py)
- [x] T003 Two real processes against one store: rows 2 of 2 while the writer lives, 4 of 4 after a writer exited locked

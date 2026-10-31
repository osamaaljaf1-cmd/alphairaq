# Phenix → Alpha Group debts sync

Makes the debts in Alpha Group agree with the accountant's Phenix export. Phenix stays the
only place debts are paid; Alpha Group mirrors it (read-only for Phenix, nothing is written there).

## What a run does
- the export must be the **full list of open invoices** every time;
- new invoice of a registered customer → added; known invoice → amount / remaining / date updated;
  invoice gone from the export → settled in Phenix → closed here;
- customer not registered (or only similarly named) → refused and listed in the report;
- returns (negative amounts) are ignored and counted;
- a run that would close more than 30 % of the open debts is **held back** (a truncated export);
- every applied run can be **undone** (`undo RUN_ID`, latest run only).

## Setup (the PC with the exports)
1. `pip install openpyxl` (Python 3 only, no other packages).
2. Copy `config.example.ini` to `config.ini`, fill the server URL and the sync key.
3. Server side: set the `DEBT_SYNC_KEY` variable (≥ 24 random characters). Until it is set the
   endpoints answer 503 and nothing can be synced.
4. Try first: `python phenix_sync.py check FILE.xlsx`, then `dry FILE.xlsx` (reports, writes nothing),
   then `apply FILE.xlsx`. Reports are written to `reports/`.
5. Automatic: `python phenix_sync.py watch` (run it at Windows start / Task Scheduler). The accountant
   exports to the inbox folder; each file is applied and moved to `done/`, `held/` (blocked, needs a
   manual `apply --force` after reading the report) or `failed/` (with the reason).

## If something looks wrong
`python phenix_sync.py runs` then `python phenix_sync.py undo RUN_ID`.

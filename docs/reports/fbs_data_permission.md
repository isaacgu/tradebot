# FBS data permission and demo account scope

Recorded on 2026-09-05 from Isaac Gumbi's direct message in the acquisition task.

Isaac reports that he already emailed FBS and received permission to use the data,
with training restricted to a demo account until the bot is completed and refined.
Isaac subsequently clarified that FBS said the data is freely available to account
holders and that no further verification or inquiry is needed for his personal
project. This is an attributed permission record, not a verbatim broker document;
the original email and its date/reference are not attached. Their absence is not
treated as a blocker on the present private/demo preparation work. No duplicate
permission inquiry or further verification request is planned.

The operator also confirmed an initial **USD 1,000 demo balance** to match planned
live starting capital. The machine-readable baseline is
[`configs/accounts/demo_usd_1000.json`](../../configs/accounts/demo_usd_1000.json).
It records starting capital and execution disabled. It does not set leverage,
position sizes, loss limits or authorize live trading. Existing account observations
are checked against this baseline; this file does not change broker balances.

Use scope for the present work is private data engineering, demo training and
refinement. Keep raw broker files private. Redistribution, public raw-data uploads,
additional accounts and production use are not inferred from the reported reply.
Permission does not establish the accuracy of any particular historical tick,
the 2024 instrument session calendar, historical UTC interpretation, or acceptance
of the failed 2016 partitions.

| Evidence field | Current record |
|---|---|
| Permission source | Direct statement from the Principal reporting FBS's reply |
| Personal use / account-holder availability | Principal reports FBS requires no further verification for this personal project |
| Original FBS wording / reply date / case reference | Not attached; not a blocker on present private/demo preparation |
| Demo training restriction | Reported by the Principal; applied to current work |
| Initial demo / planned live capital | USD 1,000 / USD 1,000, confirmed by the Principal |
| Historical trading-hours and timezone confirmation | Not provided in the message |
| Gate 1 and later trading approval | Tracked separately in the gate evidence |

If the reply is later retained, append its exact scope, date/reference and a hash of
the retained original. Do not relabel this attributed record as an independently
verified broker document or copy an approval to a different dataset or use case.

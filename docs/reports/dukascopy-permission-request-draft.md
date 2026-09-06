# Draft: written permission request for historical data use

**Status: DRAFT — not sent.** Review, adjust the specifics, and send from your own account.
ADR-0007 makes this a precondition: a third-party source is a *candidate* until its terms permit the
intended use in writing, and **silence is not consent**.

Why it is needed: the published Terms of Use restrict the site to non-commercial use, state that the
information may not be used to construct a database, and prohibit automated/robot access without
prior express written consent. The separate XML data licence was checked and is narrower, not
broader — it covers one-minute statistical averages, forbids redistribution and prohibits commercial
use, and does not mention historical file downloads. So there is no permissive alternative to fall
back on.

Send to the address their site lists for data or legal enquiries, and keep the reply with the ADR.

---

**Subject:** Request for written permission — automated historical data download for private research

Dear Sir or Madam,

I am an individual trader and I would like written permission for a specific, private use of your
historical market data. I am asking in advance because your Terms of Use prohibit automated access
without prior express written consent, and restrict use of the information to construct a database.

**Who I am.** An individual, trading my own capital only. I do not manage third-party money, do not
operate a fund, and do not sell signals, software or data services.

**What I would like to download.** Historical bid/ask tick data for:

- GBP/USD and EUR/USD — the full available history
- Your index CFD instruments for the US 500, UK 100 and Germany 40 — the full available history

**How I would access it.** By automated download from your published bulk data route, at a modest,
rate-limited concurrency. I would prefer to agree an acceptable request rate with you rather than
guess one; please tell me what you consider reasonable, and I will keep to it. I am also happy to
identify my client with a contact string in the user agent.

**How the data would be stored.** Downloaded files retained unmodified as an immutable archive, plus
a derived columnar copy for analysis, both with checksums and backups. Retention is indefinite, for
reproducibility: my research process requires that a past result can be re-derived from exactly the
data it was computed on.

**What it would be used for.** Private quantitative research and trading my own account through a
retail broker. Nothing is published or sold.

**What would not happen.** I would not redistribute the data, in raw or processed form, to anyone. I
would not make it available through any product, service, website or API. I would not share it with
another person or organisation.

**Four things I would like clarified in your reply:**

1. Whether automated download of historical data for the above use is permitted, and at what request
   rate.
2. Whether retaining raw and derived copies as described falls within, or outside, the restriction on
   constructing a database.
3. Whether derived aggregate outputs — summary statistics, charts and performance figures computed
   from the data, containing no redistributable data itself — may be shown to a third party such as an
   accountant, adviser or reviewer.
4. Whether a commercial, supplementary or paid licence would be required or preferable for this use,
   in which case please send the terms.

If any part of this is not permitted, I would rather know now than proceed on an assumption. I will
not begin automated downloading until I have your written answer.

Thank you for your time.

Kind regards,

Isaac Gumbi

---

## After the reply arrives

Record the outcome in ADR-0007 as one of:

- **Permitted, with conditions** — record the agreed rate and any constraints; the source becomes
  authorised for P1, and the conditions become part of the ingester's configuration.
- **Permitted only under a paid licence** — this collides with the free-sources-only constraint and
  is a decision for you, not the ADR.
- **Refused, or no reply within a stated period** — the source stays a candidate and is **not**
  ingested. P1 proceeds on the broker-sourced fallback, and the ≥8-year deep-history requirement
  stays where the freeze put it: a per-strategy P3 entry requirement, not a Gate-1 blocker.

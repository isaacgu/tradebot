# FBS historical-session inquiry

Prepared September 5, 2026. This is a technical-information request, not a trading
instruction, complaint or authorization to disclose account identifiers or raw data.

## Contact status

Update 2026-09-05: Isaac now reports that he independently emailed FBS and was
granted data use for demo training until the bot is completed/refined. See the
[attributed permission record](fbs_data_permission.md). The statements below
describe the earlier, unsubmitted assistant draft; they do not mean the user has
not contacted FBS. Isaac further reports that FBS said account holders may use the
data for a personal project without further verification. No duplicate permission
inquiry is planned; retaining the original reply is not a blocker on this work.
No historical session/timezone answer was included in the user's update, so those
technical questions remain open.

The Principal authorized contacting FBS while continuing provisional engineering
diagnostics in parallel. The official [FBS Help Center](https://fbs.com/hc) was opened
and its embedded LiveChat form was reached. The form requires a name and email
before the questions can be submitted. Those contact details have been requested
from the Principal; **the inquiry has not been submitted** and no reply or ticket
number exists yet. The same official page lists `support@fbs.com`.

The current FBS homepage also links to the
[official contact form](https://fbs.helpcenter.io/en/contacts). A concise version of
the questions below is entered there as an **unsubmitted draft**. This form also
requires name and email and displays reCAPTCHA protection. No contact details have
been entered, no CAPTCHA has been completed, and Submit has not been pressed. The
form is kept for continuation once the Principal supplies the chosen contact details.

Do not interpret a delayed or unavailable reply as approval of any calendar,
timestamp transformation, quality threshold or gate. Engineering diagnostics need
not wait for a response; they remain explicitly provisional and non-trading.

## Prepared message

Hello FBS support. I am an AI assistant helping a user validate historical market
data. This is a general technical inquiry about FBS-Demo EURUSD and GBPUSD, not an
account-specific trade issue. No account identifier or raw tick file is attached.

Could you please provide an archived instrument specification, dated notice, or
written technical confirmation covering October 1–31, 2024:

1. The quote and trading sessions for EURUSD and GBPUSD on FBS-Demo, including
   Sunday opening, Friday closing, daily maintenance breaks and any holidays or
   exceptional closures. Please state the timezone, UTC offsets and effective dates.
2. The precise daylight-saving transition applicable to these instruments in
   October/November 2024. Did FX sessions change on October 27, November 3, or another
   date, and were demo-server sessions different from live-server sessions?
3. Whether historical `time` and `time_msc` values returned by the MetaTrader 5
   Python `copy_ticks_range` API represent UTC Unix timestamps or server-local clock
   labels encoded as Unix values for this historical period. Were there any relevant
   server migrations, historical corrections or known time-label differences?
4. Whether the published trading-hours timetable also defines expected quote
   availability, and whether any narrower instrument-specific liquid intervals are
   documented. We do not want to equate advertised opening hours with guaranteed
   continuous quoting or liquidity.
5. Whether venue-matched October 2024 M1 Bid OHLC history remains available through
   another official export or archive if the connected terminal does not return the
   requested historical minutes. Please identify the price basis and timestamp
   convention for any such export.

A public archived URL or written answer with its effective dates is sufficient;
please distinguish current generic hours from historical instrument-specific
evidence. If this needs technical escalation, please provide a case/reference number.
Thank you.

## Parallel engineering path

The current [MetaQuotes Python API reference](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py)
documents UTC for retrieved ticks and recommends UTC-aware request times. FBS's
archived server-clock policy concerns platform/session clock labels; it does not by
itself override the API contract. Consequently, the unchanged-epoch/UTC case remains
the documented baseline. Subtracting two or three hours is a counterfactual diagnostic,
not an adopted correction. A better overlap score alone cannot establish a timezone
conversion. These documentation observations were checked on September 5, 2026, not
assumed to be historical point-in-time evidence.

Keep the immutable source data and canonical timestamps unchanged. Compare separately
labelled session/time hypotheses against checksum-verified observations; keep dates
without validated acquisition distinct from minutes with no observations in validated
fetches. Report sensitivity and unresolved assumptions, not a best-fit correction.
Do not load a provisional timetable as an approved expected-liquidity calendar.
Formal Gate 1 acceptance remains subject to its existing evidence and sign-off rules.

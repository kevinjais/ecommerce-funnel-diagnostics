# Ecommerce Funnel Diagnostics & Revenue Modelling

Three months of raw GA4 event data from the **Google Merchandise Store** — its peak
holiday quarter — sequenced into time-ordered session funnels in BigQuery to find
where the store loses revenue, and what closing that gap is worth.

**270,154 shoppers · 4,295,584 events · 109 countries · Nov 2020 – Jan 2021**

## Headline finding

**The checkout is not broken. First-time visitors are.**

The obvious hypothesis was device — mobile checkout being worse than desktop is the
single most common diagnosis in ecommerce. **It is wrong here**, and testing it was
worth more than assuming it:

| split | cart → checkout | gap |
|---|---|---|
| desktop | 34.55% | |
| mobile | 35.45% | **0.89pp** — mobile is *ahead* |
| | | |
| new visitor | 29.75% | |
| returning visitor | 42.15% | **12.39pp** |

**Device explains essentially nothing. Visitor type explains a lot.** Extended to the
full cart→purchase path the gap widens to **28.3pp** (20.11% vs 48.38%).

Where the funnel actually leaks:

```
sessions ----21%----> viewed item ----17%----> added to cart ----33%----> purchased
             ^^^^                    ^^^^
             the real losses are here, not at checkout
```

Over 90% of sessions that begin checkout complete it. The money is lost at discovery
and at first-visit trust, not at the payment step.

## What the gap is worth

`$85,916` mean, `$54,202` median, **80% interval `$14,063` – `$181,345`** per quarter.

25,000 Monte Carlo runs propagating three sources of uncertainty:

1. **Each segment's true conversion rate** — Beta posterior, not a point estimate
2. **How much of the gap is closeable** — `Beta(2,2)`, centred on half. Closing a
   behavioural gap in full is not a real option; half-gap closure is the standard
   conservative sizing assumption
3. **Order value** — bootstrapped from the empirical distribution, which is
   right-skewed (AOV $69.09, median order $48.00) and badly described by its mean

The spread is the point. A single multiplication would have produced one confident
number; the honest answer is a range wide enough to change what you would fund.

## Method

- **Session reconstruction** — `user_pseudo_id` + `ga_session_id` unnested from GA4's
  repeated `event_params`, which is the only way to get a session key out of the
  raw export
- **Time-ordered funnels** — each step counted only if its first occurrence is *after*
  the previous step's. Presence-based funnels overcount badly
- **Segment comparison** before committing to a cause, rather than assuming one
- **Monte Carlo** in NumPy, seeded at 42

## Data

[GA4 obfuscated sample ecommerce](https://console.cloud.google.com/marketplace/product/obfuscated-ga360-data/obfuscated-ga360-data)
— `bigquery-public-data.ga4_obfuscated_sample_ecommerce`, public, free to query.

## Reproduce

```bash
pip install -r requirements.txt
gcloud auth application-default login
python run_analysis.py
```

Set `PROJECT` in `run_analysis.py` to your own GCP project. The BigQuery sandbox
gives 1TB of free queries a month; this uses roughly 5GB.

## Caveats

- **`ga_session_number` is imperfect** for new-vs-returning. It resets when a user
  clears cookies or switches device, so some "new" sessions are returning visitors
  in disguise. That biases the measured gap *downward* — the true effect is likely
  larger, not smaller.
- **Returning-visitor parity is an upper bound, not a target.** Returning visitors
  are self-selected — they came back because they already liked the store. No
  intervention makes a first-time visitor behave like one. That is exactly why the
  simulation prices partial closure rather than full parity.
- **This is opportunity sizing, not a measured outcome.** Nothing was shipped and no
  revenue was recovered. The output is a range to prioritise against.
- Correlation only. A/B testing would be required to establish that any specific
  intervention moves the gap.

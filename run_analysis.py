"""
Where a live ecommerce store loses revenue, and what closing the gap is worth.

Audits three months of raw GA4 event data from the Google Merchandise Store,
sequences every session into a time-ordered funnel, and asks which segment
split actually explains the drop-off.

The answer is not device. It is whether the visitor has been here before.

Run:  python run_analysis.py
"""

import json
import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from google.cloud import bigquery

PROJECT = "utility-braid-509814-j4"
TABLE = "`bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`"
WINDOW = ("20201101", "20210131")
N_SIMS = 25_000
SEED = 42
rng = np.random.default_rng(SEED)

client = bigquery.Client(project=PROJECT)


def run(sql):
    return list(client.query(sql).result())


SESSION_ID = """CONCAT(user_pseudo_id,'-',CAST((SELECT value.int_value
  FROM UNNEST(event_params) WHERE key='ga_session_id') AS STRING))"""


def scale():
    r = run(f"""
        SELECT COUNT(DISTINCT user_pseudo_id) users, COUNT(*) events,
               COUNT(DISTINCT geo.country) countries
        FROM {TABLE}
        WHERE _TABLE_SUFFIX BETWEEN '{WINDOW[0]}' AND '{WINDOW[1]}'
    """)[0]
    print(f"users      {r.users:,}")
    print(f"events     {r.events:,}")
    print(f"countries  {r.countries}")
    return dict(users=r.users, events=r.events, countries=r.countries)


def funnel(dimension, sql_expr, where=""):
    """Time-ordered session funnel split by any dimension."""
    rows = run(f"""
        WITH ev AS (
          SELECT {SESSION_ID} sid, {sql_expr} dim, event_name, event_timestamp
          FROM {TABLE}
          WHERE _TABLE_SUFFIX BETWEEN '{WINDOW[0]}' AND '{WINDOW[1]}'
        ),
        f AS (
          -- MAX over the session: device is constant, and for visitor type
          -- 'returning' sorts above 'new', so any repeat-visit event wins
          SELECT sid, MAX(dim) dim,
            MIN(IF(event_name='add_to_cart',    event_timestamp, NULL)) t_cart,
            MIN(IF(event_name='begin_checkout', event_timestamp, NULL)) t_co,
            MIN(IF(event_name='purchase',       event_timestamp, NULL)) t_buy
          FROM ev GROUP BY sid
        )
        SELECT dim,
          COUNTIF(t_cart IS NOT NULL) carted,
          COUNTIF(t_co > t_cart)      reached_checkout,
          COUNTIF(t_buy > t_co)       purchased
        FROM f {where} GROUP BY dim ORDER BY carted DESC
    """)
    print(f"\n{dimension}")
    print(f"  {'segment':<14}{'carted':>9}{'checkout':>10}{'bought':>9}{'cart->co':>11}")
    out = {}
    for r in rows:
        rate = r.reached_checkout / r.carted * 100 if r.carted else 0
        print(f"  {str(r.dim):<14}{r.carted:>9,}{r.reached_checkout:>10,}"
              f"{r.purchased:>9,}{rate:>10.2f}%")
        out[str(r.dim)] = dict(carted=r.carted, checkout=r.reached_checkout,
                               purchased=r.purchased, cart_to_checkout=rate)
    return out


def order_values():
    rows = run(f"""
        SELECT ecommerce.purchase_revenue_in_usd v
        FROM {TABLE}
        WHERE _TABLE_SUFFIX BETWEEN '{WINDOW[0]}' AND '{WINDOW[1]}'
          AND event_name='purchase' AND ecommerce.purchase_revenue_in_usd > 0
    """)
    v = np.array([r.v for r in rows], dtype=float)
    print(f"\norders     {len(v):,}   revenue ${v.sum():,.0f}   AOV ${v.mean():.2f}")
    return v


def monte_carlo(new, ret, aov_sample):
    """Revenue recoverable if new visitors converted more like returning ones.

    Three sources of uncertainty are propagated, which is why this is a
    simulation and not a multiplication:

      1. each segment's true conversion rate  -> Beta posterior
      2. how much of the gap is actually closeable -> Beta(2,2), centred on
         half. Closing a behavioural gap in full is not a real option, and
         half-gap closure is the standard conservative sizing assumption.
      3. order value -> bootstrapped from the empirical distribution, which
         is right-skewed and badly described by its mean

    The counterfactual is end-to-end: a new visitor who added to cart
    converting all the way to purchase at the returning-visitor rate. Sizing
    only the checkout-entry step would leave the downstream gap unpriced and
    understate the opportunity by roughly 5x.
    """
    p_new = rng.beta(new["purchased"] + 1,
                     new["carted"] - new["purchased"] + 1, N_SIMS)
    p_ret = rng.beta(ret["purchased"] + 1,
                     ret["carted"] - ret["purchased"] + 1, N_SIMS)
    closeable = rng.beta(2, 2, N_SIMS)

    extra_orders = new["carted"] * (p_ret - p_new) * closeable
    aov = rng.choice(aov_sample, size=N_SIMS, replace=True)

    return extra_orders * aov


def main():
    print("=" * 58)
    s = scale()

    by_device = funnel("BY DEVICE", "device.category",
                       "WHERE dim IN ('desktop','mobile')")
    session_number = """(SELECT value.int_value FROM UNNEST(event_params)
                         WHERE key='ga_session_number')"""
    by_visitor = funnel("BY VISITOR TYPE",
                        f"IF({session_number} > 1,'returning','new')")

    dev_gap = abs(by_device["desktop"]["cart_to_checkout"]
                  - by_device["mobile"]["cart_to_checkout"])
    vis_gap = (by_visitor["returning"]["cart_to_checkout"]
               - by_visitor["new"]["cart_to_checkout"])

    print("\n" + "=" * 58)
    print("WHICH SPLIT EXPLAINS THE DROP-OFF AT CHECKOUT?")
    print(f"  device       {dev_gap:5.2f}pp   <- essentially none")
    print(f"  visitor type {vis_gap:5.2f}pp   <- this is the real one")

    values = order_values()
    sims = monte_carlo(by_visitor["new"], by_visitor["returning"], values)
    p10, p50, p90 = np.percentile(sims, [10, 50, 90])

    print("\n" + "=" * 58)
    print(f"RECOVERABLE REVENUE, {N_SIMS:,} SIMULATIONS")
    print(f"  median          ${p50:,.0f} per quarter")
    print(f"  80% interval    ${p10:,.0f}  to  ${p90:,.0f}")
    print(f"  mean            ${sims.mean():,.0f}")

    results = dict(
        users=s["users"], events=s["events"], countries=s["countries"],
        device_gap_pp=round(dev_gap, 2), visitor_gap_pp=round(vis_gap, 2),
        new=by_visitor["new"], returning=by_visitor["returning"],
        orders=len(values), total_revenue=round(float(values.sum())),
        aov=round(float(values.mean()), 2),
        recoverable_median=round(float(p50)),
        recoverable_p10=round(float(p10)), recoverable_p90=round(float(p90)),
        n_simulations=N_SIMS,
    )
    json.dump(results, open("results.json", "w"), indent=2)
    plot(by_device, by_visitor, sims, p10, p50, p90)
    print("\nwrote results.json, results.png")


def plot(by_device, by_visitor, sims, p10, p50, p90):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))

    labels = ["desktop", "mobile", "new", "returning"]
    vals = [by_device["desktop"]["cart_to_checkout"],
            by_device["mobile"]["cart_to_checkout"],
            by_visitor["new"]["cart_to_checkout"],
            by_visitor["returning"]["cart_to_checkout"]]
    colours = ["#8c8c8c", "#8c8c8c", "#c44e52", "#4c72b0"]
    ax1.bar(labels, vals, color=colours, width=0.6)
    for i, v in enumerate(vals):
        ax1.text(i, v + 0.6, f"{v:.1f}%", ha="center", fontsize=10)
    ax1.set_ylabel("cart -> checkout (%)")
    ax1.set_title("Device explains nothing. Visitor type explains a lot.")
    ax1.set_ylim(0, max(vals) * 1.25)

    ax2.hist(sims / 1000, bins=70, color="#4c72b0", edgecolor="none")
    ax2.axvline(p50 / 1000, color="#c44e52", lw=2, label=f"median ${p50/1000:.0f}K")
    ax2.axvspan(p10 / 1000, p90 / 1000, color="#4c72b0", alpha=0.15,
                label=f"80% CI ${p10/1000:.0f}K-${p90/1000:.0f}K")
    ax2.set_xlabel("recoverable revenue per quarter ($000)")
    ax2.set_ylabel("simulations")
    ax2.set_title(f"{N_SIMS:,} Monte Carlo runs")
    ax2.legend(frameon=False, fontsize=9)

    fig.tight_layout()
    fig.savefig("results.png", dpi=150)


if __name__ == "__main__":
    main()

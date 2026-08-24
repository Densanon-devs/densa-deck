"""Acquisition economics: cost basis, resale estimates, and P&L.

This is where the tool stops describing cardboard and starts informing money
decisions, so three things are non-negotiable.

**Market value is not proceeds.** A $1,284 binder does not put $1,284 in your
pocket. Marketplace fees, payment processing and shipping come off first, and
a tool that shows gross market value beside a purchase price and calls the
difference "profit" is lying by omission. Every figure here runs through the
fee model.

**The fee model is the user's, not ours.** Rates differ by platform, by
seller level, by month. `FeeModel` ships with documented defaults and is
meant to be edited. They are inputs, never claims of fact.

**Prices are estimates and are disclosed as such.** Scryfall's bulk feed —
our only price source — says in its own documentation that it is "not
updated frequently enough to power a storefront or sales system" and that
prices are "dangerously stale after 24 hours". Estimating what a collection
is worth is squarely inside that. Deciding what to pay a stranger for it is
pushing the edge, so every output carries its price age, its unpriced count,
and a confidence band rather than a single confident number.

The unpriced count matters more than it looks: 8.6% of paper printings carry
no price at all. On a 700-card box that is roughly 60 cards contributing
exactly zero to a number someone is about to hand over real money against.
Hiding that would be the most damaging thing this module could do.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone


@dataclass(frozen=True)
class FeeModel:
    """What it costs to turn cardboard into money.

    Defaults are mid-range for a US singles seller in 2026 and exist so the
    first run produces a sane number, not because they are true for anyone
    in particular. Every field is meant to be overridden.
    """

    marketplace_pct: float = 0.1025   # typical TCGplayer-style commission
    payment_pct: float = 0.025        # payment processing
    payment_flat_usd: float = 0.30    # per-order processing fee
    shipping_per_order_usd: float = 1.20
    cards_per_order: float = 12.0     # bulk sells in orders, not singles
    # Cards below this are not individually sellable at a profit; they go out
    # as bulk at a flat rate. Ignoring this is how paper valuations of large
    # collections end up 3-5x optimistic.
    bulk_threshold_usd: float = 0.50
    bulk_rate_per_card_usd: float = 0.02

    def as_dict(self) -> dict:
        return {
            "marketplace_pct": self.marketplace_pct,
            "payment_pct": self.payment_pct,
            "payment_flat_usd": self.payment_flat_usd,
            "shipping_per_order_usd": self.shipping_per_order_usd,
            "cards_per_order": self.cards_per_order,
            "bulk_threshold_usd": self.bulk_threshold_usd,
            "bulk_rate_per_card_usd": self.bulk_rate_per_card_usd,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "FeeModel":
        base = cls()
        if not data:
            return base
        clean = {k: float(v) for k, v in data.items()
                 if k in base.as_dict() and v is not None}
        return replace(base, **clean)


DEFAULT_FEES = FeeModel()


@dataclass
class ResaleEstimate:
    market_value_usd: float = 0.0
    sellable_value_usd: float = 0.0
    bulk_value_usd: float = 0.0
    marketplace_fees_usd: float = 0.0
    payment_fees_usd: float = 0.0
    shipping_usd: float = 0.0
    net_proceeds_usd: float = 0.0
    sellable_cards: int = 0
    bulk_cards: int = 0
    unpriced_cards: int = 0

    def to_dict(self) -> dict:
        return {
            "market_value_usd": round(self.market_value_usd, 2),
            "sellable_value_usd": round(self.sellable_value_usd, 2),
            "bulk_value_usd": round(self.bulk_value_usd, 2),
            "marketplace_fees_usd": round(self.marketplace_fees_usd, 2),
            "payment_fees_usd": round(self.payment_fees_usd, 2),
            "shipping_usd": round(self.shipping_usd, 2),
            "total_costs_usd": round(
                self.marketplace_fees_usd + self.payment_fees_usd + self.shipping_usd, 2),
            "net_proceeds_usd": round(self.net_proceeds_usd, 2),
            "sellable_cards": self.sellable_cards,
            "bulk_cards": self.bulk_cards,
            "unpriced_cards": self.unpriced_cards,
        }


def estimate_resale(lines, fees: FeeModel = DEFAULT_FEES) -> ResaleEstimate:
    """Estimate net proceeds from a set of (unit_price, quantity) lines.

    `lines` is an iterable of (unit_price_usd_or_None, quantity).

    Cards priced under the bulk threshold are modelled as bulk rather than
    as individual sales. That single distinction is the difference between a
    realistic number and the wildly optimistic one you get by multiplying a
    box of commons by their nominal market price.
    """
    est = ResaleEstimate()
    for unit_price, qty in lines:
        qty = int(qty or 0)
        if qty <= 0:
            continue
        if unit_price is None:
            # Unknown price contributes nothing and is reported. Never
            # silently treated as either zero value or average value.
            est.unpriced_cards += qty
            continue
        line_value = float(unit_price) * qty
        est.market_value_usd += line_value
        if float(unit_price) < fees.bulk_threshold_usd:
            est.bulk_cards += qty
            est.bulk_value_usd += fees.bulk_rate_per_card_usd * qty
        else:
            est.sellable_cards += qty
            est.sellable_value_usd += line_value

    est.marketplace_fees_usd = est.sellable_value_usd * fees.marketplace_pct
    est.payment_fees_usd = est.sellable_value_usd * fees.payment_pct

    orders = 0.0
    if fees.cards_per_order > 0:
        orders = est.sellable_cards / fees.cards_per_order
    est.payment_fees_usd += orders * fees.payment_flat_usd
    est.shipping_usd = orders * fees.shipping_per_order_usd

    est.net_proceeds_usd = max(
        0.0,
        est.sellable_value_usd + est.bulk_value_usd
        - est.marketplace_fees_usd - est.payment_fees_usd - est.shipping_usd,
    )
    return est


def collection_resale_lines(store, card_db, *, acquisition_id: int | None = None):
    """(unit_price, quantity) pairs for owned cards, condition-adjusted."""
    from densa_deck.collection.prices import (
        _attached,
        _condition_case_sql,
        _finish_price_sql,
    )

    unit = _finish_price_sql()
    adj = _condition_case_sql(f"({unit})")
    where = "WHERE ci.quantity > 0"
    params: list = []
    if acquisition_id is not None:
        where += " AND ci.acquisition_id = ?"
        params.append(int(acquisition_id))

    conn = _attached(store, card_db)
    try:
        rows = conn.execute(
            f"""SELECT {adj}, ci.quantity
                FROM collection_items ci
                LEFT JOIN cards.card_printings p ON p.printing_id = ci.printing_id
                {where}""",
            params,
        ).fetchall()
    finally:
        conn.close()
    return [(r[0], r[1]) for r in rows]


# ----------------------------------------------------------------- acquisitions


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def create_acquisition(store, name: str, purchase_price_usd: float, *,
                       purchased_on: str | None = None, source: str = "",
                       notes: str = "") -> dict:
    """Record a lot purchase. Cards are attached to it as they're scanned."""
    if not (name or "").strip():
        raise ValueError("acquisition name is required")
    purchased_on = purchased_on or date.today().isoformat()
    with store._connect() as conn:
        cur = conn.execute(
            """INSERT INTO acquisitions
               (name, purchased_on, purchase_price_usd, source, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name.strip(), purchased_on, float(purchase_price_usd or 0),
             source, notes, _now()),
        )
        conn.commit()
        return {"acquisition_id": cur.lastrowid, "name": name.strip(),
                "purchased_on": purchased_on,
                "purchase_price_usd": float(purchase_price_usd or 0)}


def list_acquisitions(store) -> list[dict]:
    with store._connect() as conn:
        rows = conn.execute(
            """SELECT a.acquisition_id, a.name, a.purchased_on, a.purchase_price_usd,
                      a.source, a.notes, a.basis_allocated_at,
                      COALESCE(SUM(ci.quantity), 0)
               FROM acquisitions a
               LEFT JOIN collection_items ci
                 ON ci.acquisition_id = a.acquisition_id AND ci.quantity > 0
               GROUP BY a.acquisition_id
               ORDER BY a.purchased_on DESC, a.acquisition_id DESC"""
        ).fetchall()
    keys = ("acquisition_id", "name", "purchased_on", "purchase_price_usd",
            "source", "notes", "basis_allocated_at", "cards")
    return [dict(zip(keys, r)) for r in rows]


def allocate_cost_basis(store, card_db, acquisition_id: int) -> dict:
    """Spread a lot's purchase price across its cards by market value.

    Buying 486 cards for $600 gives no per-card price, but a per-card basis
    is what makes profit-per-sale meaningful. Allocating in proportion to
    each card's share of the lot's total market value is the standard
    approach and needs no manual entry.

    The allocation is written to `unit_cost_usd` and stamped, deliberately
    freezing it: recomputing later against today's prices would let the cost
    basis drift with the market, which defeats the point of a basis.

    Cards with no price get an equal share of nothing — they cannot be
    weighted by a value we don't have — and are reported so the user knows
    the allocation didn't cover everything.
    """
    from densa_deck.collection.prices import (
        _attached,
        _condition_case_sql,
        _finish_price_sql,
    )

    acq = None
    for row in list_acquisitions(store):
        if row["acquisition_id"] == acquisition_id:
            acq = row
            break
    if acq is None:
        raise ValueError(f"no acquisition {acquisition_id}")

    unit = _finish_price_sql()
    adj = _condition_case_sql(f"({unit})")
    conn = _attached(store, card_db)
    try:
        rows = conn.execute(
            f"""SELECT ci.item_id, {adj}, ci.quantity
                FROM collection_items ci
                LEFT JOIN cards.card_printings p ON p.printing_id = ci.printing_id
                WHERE ci.acquisition_id = ? AND ci.quantity > 0""",
            (int(acquisition_id),),
        ).fetchall()
    finally:
        conn.close()

    priced = [(item_id, value, qty) for item_id, value, qty in rows if value is not None]
    unpriced = [r for r in rows if r[1] is None]
    total_market = sum(v * q for _, v, q in priced)

    stamped = _now()
    allocated = 0.0
    with store._connect() as conn:
        if total_market > 0:
            price = float(acq["purchase_price_usd"] or 0)
            for item_id, value, qty in priced:
                share = (value * qty) / total_market
                unit_cost = (price * share) / qty if qty else 0.0
                allocated += unit_cost * qty
                conn.execute(
                    "UPDATE collection_items SET unit_cost_usd = ? WHERE item_id = ?",
                    (round(unit_cost, 4), item_id),
                )
        conn.execute(
            "UPDATE acquisitions SET basis_allocated_at = ? WHERE acquisition_id = ?",
            (stamped, int(acquisition_id)),
        )
        conn.commit()

    return {
        "acquisition_id": acquisition_id,
        "purchase_price_usd": float(acq["purchase_price_usd"] or 0),
        "market_value_usd": round(total_market, 2),
        "allocated_usd": round(allocated, 2),
        "priced_stacks": len(priced),
        "unpriced_stacks": len(unpriced),
        "allocated_at": stamped,
    }


def acquisition_summary(store, card_db, acquisition_id: int,
                        fees: FeeModel = DEFAULT_FEES) -> dict:
    """Purchase cost vs estimated net proceeds for one lot."""
    acq = next((a for a in list_acquisitions(store)
                if a["acquisition_id"] == acquisition_id), None)
    if acq is None:
        raise ValueError(f"no acquisition {acquisition_id}")

    lines = collection_resale_lines(store, card_db, acquisition_id=acquisition_id)
    est = estimate_resale(lines, fees)
    cost = float(acq["purchase_price_usd"] or 0)
    out = est.to_dict()
    out.update({
        "acquisition_id": acquisition_id,
        "name": acq["name"],
        "purchased_on": acq["purchased_on"],
        "purchase_price_usd": round(cost, 2),
        "cards": acq["cards"],
        # Explicitly "estimated": this is a model with visible inputs, not a
        # realised figure. Realised profit only exists once cards are sold.
        "estimated_profit_usd": round(est.net_proceeds_usd - cost, 2),
        "estimated_roi_pct": (round((est.net_proceeds_usd - cost) / cost * 100, 1)
                              if cost > 0 else None),
        "fees": fees.as_dict(),
    })
    return out


# ------------------------------------------------------------------- sales


def record_sale(store, *, printing_id: str, card_name: str, sale_price_usd: float,
                quantity: int = 1, fees_usd: float = 0.0, shipping_usd: float = 0.0,
                platform: str = "", finish: str = "nonfoil", condition: str = "NM",
                item_id: int | None = None, sold_on: str | None = None,
                notes: str = "", remove_from_collection: bool = True) -> dict:
    """Record a sale, capturing cost basis at the moment of sale.

    Basis is copied onto the sale row rather than referenced, because the
    stack it came from is about to shrink or disappear. A realised-profit
    figure that silently changes later is not a record of anything.
    """
    sold_on = sold_on or date.today().isoformat()
    quantity = max(1, int(quantity))

    basis = None
    with store._connect() as conn:
        if item_id is not None:
            row = conn.execute(
                "SELECT unit_cost_usd FROM collection_items WHERE item_id = ?",
                (int(item_id),),
            ).fetchone()
            if row and row[0] is not None:
                basis = float(row[0]) * quantity

        cur = conn.execute(
            """INSERT INTO sales
               (item_id, printing_id, card_name, finish, condition, quantity,
                sale_price_usd, fees_usd, shipping_usd, cost_basis_usd,
                platform, sold_on, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item_id, printing_id, card_name, finish, condition, quantity,
             float(sale_price_usd or 0), float(fees_usd or 0), float(shipping_usd or 0),
             basis, platform, sold_on, notes, _now()),
        )
        sale_id = cur.lastrowid
        conn.commit()

    if remove_from_collection:
        store.add_copies(printing_id, card_name, quantity=-quantity,
                         finish=finish, condition=condition, reason="sold")

    net = float(sale_price_usd or 0) - float(fees_usd or 0) - float(shipping_usd or 0)
    return {
        "sale_id": sale_id,
        "net_usd": round(net, 2),
        "cost_basis_usd": round(basis, 2) if basis is not None else None,
        "realized_profit_usd": round(net - basis, 2) if basis is not None else None,
    }


def list_sales(store, limit: int = 200) -> list[dict]:
    with store._connect() as conn:
        rows = conn.execute(
            """SELECT sale_id, card_name, finish, condition, quantity,
                      sale_price_usd, fees_usd, shipping_usd, cost_basis_usd,
                      platform, sold_on
               FROM sales ORDER BY sold_on DESC, sale_id DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
    keys = ("sale_id", "card_name", "finish", "condition", "quantity",
            "sale_price_usd", "fees_usd", "shipping_usd", "cost_basis_usd",
            "platform", "sold_on")
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["net_usd"] = round(d["sale_price_usd"] - d["fees_usd"] - d["shipping_usd"], 2)
        d["realized_profit_usd"] = (
            round(d["net_usd"] - d["cost_basis_usd"], 2)
            if d["cost_basis_usd"] is not None else None)
        out.append(d)
    return out


def reseller_dashboard(store, card_db, fees: FeeModel = DEFAULT_FEES) -> dict:
    """Capital in, sales out, what's still on the shelf."""
    with store._connect() as conn:
        capital = conn.execute(
            "SELECT COALESCE(SUM(purchase_price_usd), 0) FROM acquisitions"
        ).fetchone()[0]
        srow = conn.execute(
            """SELECT COALESCE(SUM(sale_price_usd), 0),
                      COALESCE(SUM(fees_usd + shipping_usd), 0),
                      COALESCE(SUM(cost_basis_usd), 0),
                      COUNT(*),
                      COUNT(cost_basis_usd)
               FROM sales"""
        ).fetchone()

    gross_sales, sale_costs, basis_sold, sale_count, basis_known = srow
    net_sales = gross_sales - sale_costs
    est = estimate_resale(collection_resale_lines(store, card_db), fees)

    return {
        "capital_invested_usd": round(capital, 2),
        "gross_sales_usd": round(gross_sales, 2),
        "selling_costs_usd": round(sale_costs, 2),
        "net_sales_usd": round(net_sales, 2),
        "cost_basis_sold_usd": round(basis_sold, 2),
        "realized_profit_usd": round(net_sales - basis_sold, 2),
        "sales_count": int(sale_count),
        # Sales without an allocated basis make realised profit incomplete.
        # Say so rather than reporting a number that quietly excludes them.
        "sales_missing_basis": int(sale_count - basis_known),
        "inventory_market_value_usd": est.to_dict()["market_value_usd"],
        "inventory_net_estimate_usd": est.to_dict()["net_proceeds_usd"],
        "inventory_unpriced_cards": est.unpriced_cards,
        "roi_pct": (round((net_sales - basis_sold) / capital * 100, 1)
                    if capital > 0 else None),
        "fees": fees.as_dict(),
    }


# ------------------------------------------------- phase 6: buy-side analysis


def analyze_acquisition(lines, fees: FeeModel = DEFAULT_FEES,
                        *, price_age_hours: float | None = None,
                        margins=(0.45, 0.55, 0.65)) -> dict:
    """Model what a pile of cards is worth paying for.

    `margins` are the fractions of estimated net proceeds to offer —
    conservative, normal, aggressive. They are negotiating positions, not
    recommendations, and the output deliberately contains no verdict.

    There is no "BUY" field here on purpose. The price feed's own publisher
    states it is not suitable for powering a sales system; turning that into
    a green tick over someone's $1,400 decision would be indefensible. What
    the tool can honestly do is show the arithmetic, the assumptions, and how
    much of the pile it could not price.
    """
    est = estimate_resale(lines, fees)
    net = est.net_proceeds_usd

    conservative, normal, aggressive = (round(net * m, 2) for m in margins)

    total_cards = est.sellable_cards + est.bulk_cards + est.unpriced_cards
    coverage = ((total_cards - est.unpriced_cards) / total_cards
                if total_cards else 0.0)

    # Confidence reflects what we could actually price, plus how old the
    # prices are. It is about the data, not about the deal.
    if coverage >= 0.95:
        confidence = "high"
    elif coverage >= 0.85:
        confidence = "medium"
    else:
        confidence = "low"
    if price_age_hours is not None and price_age_hours > 24:
        confidence = "low" if confidence == "medium" else confidence
        if confidence == "high":
            confidence = "medium"

    out = est.to_dict()
    out.update({
        "target_prices": {
            "conservative_usd": conservative,
            "normal_usd": normal,
            "aggressive_usd": aggressive,
        },
        "margins": {"conservative": margins[0], "normal": margins[1],
                    "aggressive": margins[2]},
        "total_cards": total_cards,
        "price_coverage_pct": round(coverage * 100, 1),
        "confidence": confidence,
        "price_age_hours": price_age_hours,
        "fees": fees.as_dict(),
        "caveats": _acquisition_caveats(est, coverage, price_age_hours),
    })
    return out


def _acquisition_caveats(est: ResaleEstimate, coverage: float,
                         price_age_hours: float | None) -> list[str]:
    """Everything the number doesn't cover, in plain language.

    Surfaced as data so the UI cannot render the estimate without them.
    """
    notes: list[str] = []
    if est.unpriced_cards:
        notes.append(
            f"{est.unpriced_cards} card(s) have no price and contribute $0 to "
            f"this estimate ({round((1 - coverage) * 100, 1)}% of the pile).")
    if est.bulk_cards:
        notes.append(
            f"{est.bulk_cards} card(s) are valued as bulk rather than as "
            f"individual sales.")
    if price_age_hours is not None and price_age_hours > 24:
        notes.append(
            f"Prices are {price_age_hours:.0f} hours old. The price source "
            f"considers data over 24 hours stale.")
    notes.append(
        "Prices are estimates from daily bulk data, not live market quotes. "
        "Fees and shipping are your configured assumptions.")
    notes.append(
        "Estimate assumes every sellable card eventually sells at market. "
        "Real liquidation takes time and rarely clears in full.")
    return notes

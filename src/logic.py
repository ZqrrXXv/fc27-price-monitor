from __future__ import annotations

import re
import unicodedata
from typing import Any


def normalize(text: str) -> str:
    # Retirer les marques avant NFKD : sinon ™ peut devenir les lettres "TM".
    text = (text or "").replace("™", " ").replace("®", " ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text).lower()
    return re.sub(r"\s+", " ", text).strip()


def money(value: float | None, currency: str = "EUR") -> str:
    if value is None:
        return "—"
    if currency.upper() == "EUR":
        return f"{value:.2f}".replace(".", ",") + " €"
    return f"{value:.2f} {currency.upper()}"


def determine_reasons(
    previous: dict[str, Any] | None,
    new_price: float,
    budget: float,
    significant_eur: float,
    significant_pct: float,
    edition_lowest_before: float | None,
    alert_on_first_seen_under_budget: bool,
    alert_on_new_low: bool,
) -> list[str]:
    reasons: list[str] = []

    if previous is None:
        if alert_on_first_seen_under_budget and new_price <= budget:
            reasons.append("budget")
        if alert_on_new_low and edition_lowest_before is not None and new_price < edition_lowest_before:
            reasons.append("new_low")
        return reasons

    if previous.get("in_stock") is False:
        reasons.append("restock")

    old_price = previous.get("last_price")
    if old_price is not None:
        old_price = float(old_price)
        drop = old_price - new_price
        pct = (drop / old_price * 100.0) if old_price > 0 else 0.0

        if old_price > budget >= new_price:
            reasons.append("budget")
        if drop > 0 and (drop >= significant_eur or pct >= significant_pct):
            reasons.append("significant_drop")

    if alert_on_new_low and edition_lowest_before is not None and new_price < edition_lowest_before:
        reasons.append("new_low")

    return list(dict.fromkeys(reasons))

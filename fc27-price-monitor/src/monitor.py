#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from logic import determine_reasons, money, normalize

BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "data" / "state.json"

ITAD_BASE = "https://api.isthereanydeal.com"
USER_AGENT = "fc27-price-monitor/1.0 (personal-use)"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("fc27-monitor")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def http_json(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    payload: Any = None,
    retries: int = 4,
    timeout: int = 30,
) -> Any:
    if params:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{url}?{query}"

    body = None
    req_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=body, headers=req_headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            detail = exc.read(500).decode("utf-8", errors="replace")
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= retries:
                raise RuntimeError(f"HTTP {exc.code} pour {url} — {detail}") from exc
            retry_after = exc.headers.get("Retry-After")
            try:
                wait = max(float(retry_after), 1.0) if retry_after else 1.5 * (2 ** attempt)
            except ValueError:
                wait = 1.5 * (2 ** attempt)
            log.warning("HTTP %s, nouvel essai dans %.1fs...", exc.code, wait)
            time.sleep(wait)
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt >= retries:
                raise RuntimeError(f"Erreur réseau pour {url}: {exc}") from exc
            wait = 1.5 * (2 ** attempt)
            log.warning("Erreur réseau, nouvel essai dans %.1fs...", wait)
            time.sleep(wait)

    raise RuntimeError(f"Échec HTTP: {last_error}")


def post_discord(webhook_url: str, payload: dict[str, Any]) -> None:
    # Discord peut répondre 204 sans corps.
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                response.read()
                return
        except urllib.error.HTTPError as exc:
            last_error = exc
            detail = exc.read(300).decode("utf-8", errors="replace")
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise RuntimeError(f"Discord HTTP {exc.code} — {detail}") from exc
            retry_after = exc.headers.get("Retry-After")
            try:
                wait = max(float(retry_after), 1.0) if retry_after else 1.5 * (2 ** attempt)
            except ValueError:
                wait = 1.5 * (2 ** attempt)
            time.sleep(wait)
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt == 3:
                raise RuntimeError(f"Erreur réseau Discord: {exc}") from exc
            time.sleep(1.5 * (2 ** attempt))
    raise RuntimeError(f"Échec Discord: {last_error}")


@dataclass
class Offer:
    edition_key: str
    edition_name: str
    game_id: str
    shop_id: int | str
    shop: str
    price: float
    regular_price: float | None
    cut_pct: float | None
    currency: str
    launcher: str
    platforms: list[str]
    url: str
    source_timestamp: str | None

    @property
    def key(self) -> str:
        return f"{self.shop_id}:{normalize(self.launcher) or 'pc'}"


class ITADClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    @property
    def headers(self) -> dict[str, str]:
        return {"ITAD-API-Key": self.api_key}

    def search_games(self, title: str, results: int = 30) -> list[dict[str, Any]]:
        data = http_json(
            "GET",
            f"{ITAD_BASE}/games/search/v1",
            params={"title": title, "results": results},
            headers=self.headers,
        )
        if not isinstance(data, list):
            raise RuntimeError("Réponse ITAD inattendue lors de la recherche.")
        return data

    def prices(self, game_ids: list[str], country: str) -> list[dict[str, Any]]:
        data = http_json(
            "POST",
            f"{ITAD_BASE}/games/prices/v3",
            params={"country": country, "deals": "false", "vouchers": "true", "capacity": 0},
            headers=self.headers,
            payload=game_ids,
        )
        if not isinstance(data, list):
            raise RuntimeError("Réponse ITAD inattendue lors de la récupération des prix.")
        return data


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temp.replace(path)


def title_score(candidate_title: str, edition_cfg: dict[str, Any], query: str) -> int | None:
    candidate = normalize(candidate_title)
    required = [normalize(x) for x in edition_cfg.get("required_words", [])]
    forbidden = [normalize(x) for x in edition_cfg.get("forbidden_phrases", [])]

    words = set(candidate.split())
    if any(word not in words for word in required):
        return None
    if any(phrase and phrase in candidate for phrase in forbidden):
        return None

    q = normalize(query)
    score = 0
    if candidate == q:
        score += 100
    if candidate.startswith("ea sports fc 27"):
        score += 30
    score += 5 * len(required)
    score -= abs(len(candidate) - len(q))
    return score


def discover_game_id(client: ITADClient, edition_cfg: dict[str, Any]) -> tuple[str, str]:
    query = edition_cfg["search_query"]
    ranked: list[tuple[int, dict[str, Any]]] = []
    for item in client.search_games(query):
        score = title_score(str(item.get("title", "")), edition_cfg, query)
        if score is not None:
            ranked.append((score, item))

    if not ranked:
        raise RuntimeError(f"Aucun jeu ITAD fiable trouvé pour « {query} ».")

    ranked.sort(key=lambda item: item[0], reverse=True)
    best = ranked[0][1]
    return str(best["id"]), str(best.get("title", query))


def shop_allowed(shop_name: str, cfg: dict[str, Any]) -> bool:
    name = normalize(shop_name)
    allow = [normalize(x) for x in cfg.get("shop_allowlist", []) if str(x).strip()]
    block = [normalize(x) for x in cfg.get("shop_blocklist", []) if str(x).strip()]
    if any(item in name for item in block):
        return False
    return not allow or any(item in name or name in item for item in allow)


def launcher_from_deal(deal: dict[str, Any]) -> str:
    names = [
        str(item.get("name"))
        for item in (deal.get("drm") or [])
        if isinstance(item, dict) and item.get("name")
    ]
    if names:
        return " / ".join(names)

    shop = normalize(str((deal.get("shop") or {}).get("name", "")))
    if "steam" in shop:
        return "Steam"
    if "epic" in shop:
        return "Epic"
    if "ea store" in shop or shop == "ea app":
        return "EA App"
    return "PC"


def parse_offers(
    edition_key: str,
    edition_name: str,
    game_id: str,
    payload: dict[str, Any],
    cfg: dict[str, Any],
) -> list[Offer]:
    wanted_currency = str(cfg.get("currency", "EUR")).upper()
    offers: list[Offer] = []

    for deal in payload.get("deals", []) or []:
        shop_obj = deal.get("shop") or {}
        shop = str(shop_obj.get("name") or "Boutique inconnue")
        if not shop_allowed(shop, cfg):
            continue

        price_obj = deal.get("price") or {}
        try:
            price = float(price_obj["amount"])
        except (KeyError, TypeError, ValueError):
            continue

        currency = str(price_obj.get("currency", "")).upper()
        if wanted_currency and currency != wanted_currency:
            continue

        platforms = [
            str(item.get("name"))
            for item in (deal.get("platforms") or [])
            if isinstance(item, dict) and item.get("name")
        ]
        # ITAD peut laisser ce champ vide pour certains produits PC.
        if platforms and not any(normalize(name) == "windows" for name in platforms):
            continue

        regular_obj = deal.get("regular") or {}
        regular = regular_obj.get("amount")
        try:
            regular = float(regular) if regular is not None else None
        except (TypeError, ValueError):
            regular = None

        cut = deal.get("cut")
        try:
            cut = float(cut) if cut is not None else None
        except (TypeError, ValueError):
            cut = None

        offers.append(
            Offer(
                edition_key=edition_key,
                edition_name=edition_name,
                game_id=game_id,
                shop_id=shop_obj.get("id", shop),
                shop=shop,
                price=price,
                regular_price=regular,
                cut_pct=cut,
                currency=currency or wanted_currency,
                launcher=launcher_from_deal(deal),
                platforms=platforms or ["Windows"],
                url=str(deal.get("url") or ""),
                source_timestamp=deal.get("timestamp"),
            )
        )

    # Une seule offre par boutique + launcher, en gardant la moins chère.
    cheapest: dict[str, Offer] = {}
    for offer in offers:
        current = cheapest.get(offer.key)
        if current is None or offer.price < current.price:
            cheapest[offer.key] = offer
    return list(cheapest.values())


def append_history(state: dict[str, Any], event: dict[str, Any], max_events: int) -> None:
    history = state.setdefault("history", [])
    history.append(event)
    state["history"] = history[-max_events:]


def process_edition(
    state: dict[str, Any],
    edition_key: str,
    edition_name: str,
    offers: list[Offer],
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    now = utc_now()
    edition_state = state.setdefault("editions", {}).setdefault(
        edition_key,
        {"display_name": edition_name, "lowest_seen": None, "offers": {}},
    )
    edition_state["display_name"] = edition_name
    offer_states = edition_state.setdefault("offers", {})

    edition_lowest_before = edition_state.get("lowest_seen")
    if edition_lowest_before is not None:
        edition_lowest_before = float(edition_lowest_before)

    lowest_this_run = min((offer.price for offer in offers), default=None)
    edition_lowest_after = edition_lowest_before
    if lowest_this_run is not None:
        edition_lowest_after = (
            lowest_this_run
            if edition_lowest_before is None
            else min(edition_lowest_before, lowest_this_run)
        )

    current_keys: set[str] = set()
    alerts: list[dict[str, Any]] = []
    max_history = int(cfg.get("max_history_events", 1500))

    for offer in sorted(offers, key=lambda item: (item.price, normalize(item.shop))):
        current_keys.add(offer.key)
        previous = copy.deepcopy(offer_states.get(offer.key))
        previous_price = (
            float(previous["last_price"])
            if previous and previous.get("last_price") is not None
            else None
        )

        reasons = determine_reasons(
            previous=previous,
            new_price=offer.price,
            budget=float(cfg["budget_eur"]),
            significant_eur=float(cfg["significant_drop_eur"]),
            significant_pct=float(cfg["significant_drop_pct"]),
            edition_lowest_before=edition_lowest_before,
            alert_on_first_seen_under_budget=bool(cfg.get("alert_on_first_seen_under_budget", True)),
            alert_on_new_low=bool(cfg.get("alert_on_new_low", True)),
        )

        changed = previous is None or previous_price != offer.price
        restocked = previous is not None and previous.get("in_stock") is False

        if changed or restocked:
            append_history(
                state,
                {
                    "timestamp": now,
                    "type": "price_observation" if changed else "restock",
                    "edition": edition_key,
                    "shop": offer.shop,
                    "launcher": offer.launcher,
                    "old_price": previous_price,
                    "new_price": offer.price,
                    "currency": offer.currency,
                    "url": offer.url,
                },
                max_history,
            )

        if reasons:
            alerts.append(
                {
                    "offer": asdict(offer),
                    "previous": previous,
                    "reasons": reasons,
                    "edition_lowest_after": edition_lowest_after if edition_lowest_after is not None else offer.price,
                }
            )
            append_history(
                state,
                {
                    "timestamp": now,
                    "type": "alert",
                    "reasons": reasons,
                    "edition": edition_key,
                    "shop": offer.shop,
                    "launcher": offer.launcher,
                    "old_price": previous_price,
                    "new_price": offer.price,
                    "currency": offer.currency,
                    "url": offer.url,
                },
                max_history,
            )

        offer_low = offer.price
        if previous and previous.get("lowest_seen") is not None:
            offer_low = min(float(previous["lowest_seen"]), offer.price)

        offer_states[offer.key] = {
            "shop_id": offer.shop_id,
            "shop": offer.shop,
            "launcher": offer.launcher,
            "last_price": offer.price,
            "regular_price": offer.regular_price,
            "cut_pct": offer.cut_pct,
            "currency": offer.currency,
            "url": offer.url,
            "in_stock": True,
            "miss_count": 0,
            "lowest_seen": offer_low,
            "first_seen": previous.get("first_seen", now) if previous else now,
            "last_change": now if changed or restocked else (previous.get("last_change") if previous else now),
        }

    # Anti faux-positif : il faut plusieurs snapshots manquants avant "rupture".
    for key, previous in list(offer_states.items()):
        if key in current_keys or previous.get("in_stock") is False:
            continue
        misses = int(previous.get("miss_count", 0)) + 1
        previous["miss_count"] = misses
        if misses >= int(cfg["out_of_stock_after_misses"]):
            previous["in_stock"] = False
            previous["last_change"] = now
            append_history(
                state,
                {
                    "timestamp": now,
                    "type": "out_of_stock",
                    "edition": edition_key,
                    "shop": previous.get("shop"),
                    "launcher": previous.get("launcher"),
                    "last_price": previous.get("last_price"),
                    "currency": previous.get("currency", "EUR"),
                },
                max_history,
            )

    edition_state["lowest_seen"] = edition_lowest_after
    return alerts


def alert_title(reasons: list[str], edition_name: str) -> str:
    if "restock" in reasons:
        return f"📦 FC 27 {edition_name} de nouveau disponible !"
    if "budget" in reasons:
        return f"🔥 FC 27 {edition_name} passe sous 50 € !"
    if "new_low" in reasons:
        return f"📉 Nouveau meilleur prix — FC 27 {edition_name}"
    return f"💸 FC 27 {edition_name} vient de baisser !"


def build_embed(alert: dict[str, Any], budget: float) -> dict[str, Any]:
    offer = alert["offer"]
    previous = alert.get("previous")
    reasons = alert["reasons"]
    old_price = previous.get("last_price") if previous else None
    new_price = float(offer["price"])

    fields = [
        {"name": "Vendeur", "value": str(offer["shop"]), "inline": True},
        {"name": "Édition", "value": str(offer["edition_name"]), "inline": True},
        {"name": "Launcher / DRM", "value": str(offer["launcher"]), "inline": True},
        {"name": "Nouveau prix", "value": money(new_price, offer["currency"]), "inline": True},
    ]

    if old_price is not None:
        old_price = float(old_price)
        fields.append({"name": "Ancien prix", "value": money(old_price, offer["currency"]), "inline": True})
        if old_price > new_price:
            drop = old_price - new_price
            pct = (drop / old_price * 100.0) if old_price else 0.0
            fields.append(
                {
                    "name": "Baisse",
                    "value": f"-{money(drop, offer['currency'])} (-{pct:.1f} %)".replace(".", ","),
                    "inline": True,
                }
            )

    if offer.get("regular_price") is not None:
        fields.append(
            {
                "name": "Prix habituel",
                "value": money(float(offer["regular_price"]), offer["currency"]),
                "inline": True,
            }
        )

    fields.extend(
        [
            {
                "name": "Plus bas observé par le bot",
                "value": money(float(alert["edition_lowest_after"]), offer["currency"]),
                "inline": True,
            },
            {
                "name": "Budget",
                "value": "✅ Sous 50 €"
                if new_price <= budget
                else f"⏳ {money(new_price - budget, offer['currency'])} au-dessus",
                "inline": True,
            },
        ]
    )

    labels = {
        "budget": "passage sous le budget",
        "significant_drop": "baisse importante",
        "new_low": "nouveau plus bas observé",
        "restock": "retour en stock",
    }
    embed = {
        "title": alert_title(reasons, str(offer["edition_name"])),
        "description": " • ".join(labels[r] for r in reasons if r in labels),
        "fields": fields,
        "footer": {"text": "Source prix : IsThereAnyDeal • PC France"},
        "timestamp": utc_now(),
    }
    if offer.get("url"):
        embed["url"] = offer["url"]
    return embed


def send_discord(webhook_url: str, alerts: list[dict[str, Any]], budget: float) -> None:
    for start in range(0, len(alerts), 10):
        batch = alerts[start : start + 10]
        post_discord(
            webhook_url,
            {
                "username": "FC 27 Price Watch",
                "content": "🔔 **Évolution de prix détectée sur EA SPORTS FC 27 PC**",
                "embeds": [build_embed(alert, budget) for alert in batch],
                "allowed_mentions": {"parse": []},
            },
        )
        time.sleep(0.4)


def send_test_discord(webhook_url: str) -> None:
    post_discord(
        webhook_url,
        {
            "username": "FC 27 Price Watch",
            "content": "✅ **Test réussi : le robot FC 27 peut envoyer des notifications ici.**",
            "embeds": [
                {
                    "title": "🔥 Exemple de baisse de prix",
                    "description": "Ceci est uniquement une notification de test.",
                    "fields": [
                        {"name": "Jeu", "value": "EA SPORTS FC 27 PC", "inline": True},
                        {"name": "Édition", "value": "Standard", "inline": True},
                        {"name": "Exemple", "value": "69,99 € → 47,99 €", "inline": False},
                    ],
                    "timestamp": utc_now(),
                }
            ],
            "allowed_mentions": {"parse": []},
        },
    )
    log.info("Notification Discord de test envoyée.")


def resolve_game_ids(
    client: ITADClient,
    cfg: dict[str, Any],
    state: dict[str, Any],
    force_rediscover: bool,
) -> dict[str, str]:
    ids: dict[str, str] = {}
    state_ids = state.setdefault("game_ids", {})

    for edition_key, edition_cfg in cfg["editions"].items():
        configured = edition_cfg.get("game_id")
        cached = state_ids.get(edition_key, {}).get("id")

        if force_rediscover or (not configured and not cached):
            game_id, title = discover_game_id(client, edition_cfg)
            state_ids[edition_key] = {"id": game_id, "title": title, "discovered_at": utc_now()}
            ids[edition_key] = game_id
            log.info("%s -> %s (%s)", edition_key, title, game_id)
        else:
            game_id = str(configured or cached)
            ids[edition_key] = game_id
            if edition_key not in state_ids:
                state_ids[edition_key] = {
                    "id": game_id,
                    "title": edition_cfg.get("search_query"),
                    "discovered_at": utc_now(),
                }

    if len(set(ids.values())) != len(ids):
        raise RuntimeError(
            "Standard et Ultimate pointent vers le même Game ID ITAD. "
            "Lance avec --rediscover ou corrige config.json."
        )
    return ids


def run_monitor(args: argparse.Namespace) -> int:
    cfg = load_json(CONFIG_PATH, {})
    if not cfg:
        raise RuntimeError("config.json est manquant ou vide.")

    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if args.test_discord:
        if not webhook:
            raise RuntimeError("Secret DISCORD_WEBHOOK_URL manquant.")
        send_test_discord(webhook)
        return 0

    api_key = os.getenv("ITAD_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Secret ITAD_API_KEY manquant.")
    if not webhook and not args.dry_run:
        raise RuntimeError("Secret DISCORD_WEBHOOK_URL manquant.")

    state = load_json(STATE_PATH, {"version": 1, "game_ids": {}, "editions": {}, "history": []})
    initial_state = copy.deepcopy(state)
    client = ITADClient(api_key)

    game_ids = resolve_game_ids(client, cfg, state, args.rediscover)
    payloads = client.prices(list(game_ids.values()), str(cfg.get("country", "FR")))
    by_id = {str(item.get("id")): item for item in payloads if isinstance(item, dict)}

    all_alerts: list[dict[str, Any]] = []

    for edition_key, edition_cfg in cfg["editions"].items():
        game_id = game_ids[edition_key]
        payload = by_id.get(game_id)
        if payload is None:
            log.error(
                "Aucune réponse prix pour %s (%s). L'état de stock n'est PAS modifié.",
                edition_cfg["display_name"],
                game_id,
            )
            continue

        offers = parse_offers(
            edition_key,
            edition_cfg["display_name"],
            game_id,
            payload,
            cfg,
        )
        log.info("%s : %d offre(s) PC/EUR.", edition_cfg["display_name"], len(offers))
        for offer in sorted(offers, key=lambda item: item.price):
            log.info(
                "  %s | %s | %s | %s",
                offer.shop,
                offer.launcher,
                money(offer.price, offer.currency),
                offer.url,
            )

        all_alerts.extend(process_edition(state, edition_key, edition_cfg["display_name"], offers, cfg))

    if all_alerts:
        log.info("%d alerte(s) détectée(s).", len(all_alerts))
        if args.dry_run:
            for alert in all_alerts:
                offer = alert["offer"]
                log.info(
                    "[DRY-RUN] %s | %s | %s | raisons=%s",
                    offer["edition_name"],
                    offer["shop"],
                    money(offer["price"], offer["currency"]),
                    ",".join(alert["reasons"]),
                )
        else:
            send_discord(webhook, all_alerts, float(cfg["budget_eur"]))
    else:
        log.info("Aucune alerte à envoyer.")

    if args.dry_run:
        log.info("Dry-run : data/state.json reste intact.")
        return 0

    if state != initial_state:
        save_json_atomic(STATE_PATH, state)
        log.info("État mis à jour.")
    else:
        log.info("État inchangé.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Surveillance EA SPORTS FC 27 PC.")
    parser.add_argument("--test-discord", action="store_true", help="Envoie une notification Discord de test.")
    parser.add_argument("--dry-run", action="store_true", help="N'envoie rien et ne sauvegarde pas l'état.")
    parser.add_argument("--rediscover", action="store_true", help="Redécouvre les Game IDs ITAD.")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        sys.exit(run_monitor(parse_args()))
    except Exception as exc:
        log.exception("Échec du robot : %s", exc)
        sys.exit(1)

"""Money-safe normalization, analytics and a closed-event prop replay.

Rules are a documented research snapshot, never a verified account profile.
No function in this module connects to a broker or places orders.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP
from zoneinfo import ZoneInfo

CENT = Decimal("0.01")
NY = ZoneInfo("America/New_York")
SIZES = {
    25000: (1000, 1250, 20),
    50000: (2000, 3000, 40),
    100000: (3000, 6000, 60),
    150000: (4500, 9000, 100),
}
RULES = {
    "program": "LucidFlex",
    "snapshot_date": "2026-10-18",
    "account_verified": False,
    "scope": "Research snapshot; confirm purchased account and rules before use",
    "sources": {
        "drawdown": "https://support.lucidtrading.com/en/articles/12945815-lucidflex-drawdown",
        "evaluation": "https://support.lucidtrading.com/en/articles/12945790-lucidflex-evaluation-account",
        "scaling": "https://support.lucidtrading.com/en/articles/12945808-lucidflex-scaling-plan",
        "customization": "https://support.lucidtrading.com/en/articles/16226050-lucidflex-customization",
    },
}


class ValidationError(ValueError):
    pass


def number(value, field="montant", *, cents=False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValidationError(f"{field}: nombre requis")
    s = str(value).strip().replace("\u202f", "").replace("\u00a0", "")
    # Unambiguous French decimals; thousands separators must be removed upstream.
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        raise ValidationError(f"{field}: nombre invalide") from None
    if not d.is_finite() or abs(d) > Decimal("1000000000"):
        raise ValidationError(f"{field}: valeur hors limites")
    if cents and d.as_tuple().exponent < -2:
        raise ValidationError(f"{field}: maximum 2 décimales, aucun séparateur de milliers")
    return d.quantize(CENT, rounding=ROUND_HALF_UP) if cents else d


def money(d: Decimal) -> float:
    return float(d.quantize(CENT, rounding=ROUND_HALF_UP))


def instant(value) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise ValidationError("closed_at: date ISO 8601 requise") from None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValidationError("closed_at: fuseau explicite requis, ex. 2026-10-01T14:30:00Z")
    return dt.astimezone(timezone.utc)


def session_for(dt: datetime) -> str:
    """Research convention: New York session rolls at 18:00, including DST."""
    local = dt.astimezone(NY)
    d = local.date() + (timedelta(days=1) if local.hour >= 18 else timedelta())
    return d.isoformat()


def normalize(rows: list[dict], mapping: dict | None = None, account: str = "") -> list[dict]:
    mapping = mapping or {}
    allowed = {"event_id", "event_type", "closed_at", "pnl_net_usd", "pnl_gross_usd", "fees_usd", "withdrawal_usd", "symbol", "account_id", "session_date"}
    if set(mapping) - allowed:
        raise ValidationError("Mapping: champ non reconnu")
    if not rows or len(rows) > 50000:
        raise ValidationError("Le fichier doit contenir entre 1 et 50 000 événements")
    out = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValidationError(f"Ligne {i}: objet attendu")

        def get(key):
            val = row.get(mapping.get(key) or key)
            return None if val is None or str(val).strip() == "" else val

        try:
            eid = str(get("event_id") or "").strip()
            if not eid or len(eid) > 200:
                raise ValidationError("event_id: identifiant stable requis (trade fermé, pas fill)")
            dt = instant(get("closed_at"))
            kind = str(get("event_type") or "trade").lower().strip()
            if kind not in {"trade", "withdrawal"}:
                raise ValidationError("event_type: trade ou withdrawal uniquement")
            aid = str(get("account_id") or account or "").strip()
            if not aid or len(aid) > 200:
                raise ValidationError("account_id: compte obligatoire dans le fichier ou comme compte de secours")
            fee = number(get("fees_usd"), "fees_usd", cents=True) if get("fees_usd") is not None else None
            if fee is not None and fee < 0:
                raise ValidationError("fees_usd doit être positif ou nul")
            net, withdrawal = None, None
            if kind == "trade":
                n, g = get("pnl_net_usd"), get("pnl_gross_usd")
                if n is None and g is None:
                    raise ValidationError("pnl_net_usd ou pnl_gross_usd requis")
                if g is not None and fee is None:
                    raise ValidationError("PnL brut: frais requis pour calculer le net")
                gross = number(g, "pnl_gross_usd", cents=True) if g is not None else None
                net = number(n, "pnl_net_usd", cents=True) if n is not None else gross - fee
                if gross is not None and net != gross - fee:
                    raise ValidationError("PnL brut, net et frais incohérents")
            else:
                withdrawal = number(get("withdrawal_usd"), "withdrawal_usd", cents=True)
                if withdrawal <= 0:
                    raise ValidationError("withdrawal_usd doit être strictement positif")
            supplied = get("session_date")
            if supplied is not None:
                if date.fromisoformat(str(supplied)).isoformat() != str(supplied):
                    raise ValidationError("session_date: format YYYY-MM-DD strict requis")
            sess = str(supplied) if supplied is not None else session_for(dt)
            event = {
                "event_id": eid, "event_type": kind, "closed_at": dt.isoformat(),
                "pnl_net_usd": str(net) if net is not None else None,
                "fees_usd": str(fee) if fee is not None else None,
                "withdrawal_usd": str(withdrawal) if withdrawal is not None else None,
                "symbol": str(get("symbol") or "NON_RENSEIGNE")[:80],
                "account_id": aid, "session_date": sess,
                "session_source": "provided" if supplied is not None else "inferred_18h_New_York",
            }
            out.append(event)
        except (ValidationError, ValueError) as exc:
            raise ValidationError(f"Ligne {i}: {exc}") from None
    if len({e["account_id"] for e in out}) != 1:
        raise ValidationError("Import refusé: plusieurs comptes. Utiliser un journal par compte.")
    seen = {}
    for e in out:
        k = e["event_id"]
        if k in seen and seen[k] != e:
            raise ValidationError("Identifiant dupliqué avec contenu différent; import refusé")
        seen[k] = e
    out = sorted(seen.values(), key=lambda e: (e["closed_at"], e["event_id"]))
    # A supplied session cannot go backwards while event time moves forward.
    if any(a["session_date"] > b["session_date"] for a, b in zip(out, out[1:])):
        raise ValidationError("session_date: séquence non chronologique")
    return out


def parse_file(text: str, filename: str) -> list[dict]:
    text = text.lstrip("\ufeff")
    if filename.lower().endswith((".jsonl", ".ndjson")):
        try:
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError:
            raise ValidationError("JSONL invalide") from None
    if filename.lower().endswith(".json"):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            raise ValidationError("JSON invalide") from None
        if not isinstance(obj, list):
            raise ValidationError("JSON: liste d’événements attendue")
        return obj
    if not filename.lower().endswith(".csv"):
        raise ValidationError("Formats autorisés: CSV, JSONL, JSON")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValidationError("En-têtes CSV absents ou dupliqués")
        rows = list(reader)
        if any(None in row or any(v is None for v in row.values()) for row in rows):
            raise ValidationError("Nombre de colonnes CSV incohérent")
        return rows
    except csv.Error:
        raise ValidationError("CSV invalide: vérifier séparateur et en-têtes") from None


def fingerprint(event: dict) -> str:
    semantic = {k:v for k,v in event.items() if k != "session_source"}
    return hashlib.sha256(json.dumps(semantic, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def metrics(events: list[dict], initial_balance) -> dict:
    initial = number(initial_balance, "Solde initial", cents=True)
    if initial <= 0:
        raise ValidationError("Solde initial strictement positif requis")
    pnl, equity, peak, max_dd = Decimal(0), initial, initial, Decimal(0)
    dd_pct = Decimal(0)
    wins, losses, flats, known_fees = [], [], 0, Decimal(0)
    missing_fees = 0
    days = defaultdict(lambda: Decimal(0))
    curve = [{"at": None, "equity": money(initial), "trading_equity": money(initial), "drawdown": 0}]
    cash = initial
    for e in events:
        if e["event_type"] == "withdrawal":
            cash -= number(e["withdrawal_usd"])
            continue
        v = number(e["pnl_net_usd"])
        pnl += v
        equity = initial + pnl  # excludes cash withdrawals for trading-performance DD
        cash += v
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if peak > 0:
            dd_pct = max(dd_pct, (peak - equity) / peak * 100)
        if v > 0: wins.append(v)
        elif v < 0: losses.append(v)
        else: flats += 1
        if e["fees_usd"] is None: missing_fees += 1
        else: known_fees += number(e["fees_usd"])
        days[e["session_date"]] += v
        curve.append({"at": e["closed_at"], "equity": money(cash), "trading_equity": money(equity), "drawdown": money(peak-equity)})
    count = len(wins) + len(losses) + flats
    total_loss = -sum(losses, Decimal(0))
    return {
        "trade_count": count, "net_pnl": money(pnl), "cash_balance": money(cash),
        "win_rate": round(len(wins)/count*100, 2) if count else None,
        "profit_factor": round(float(sum(wins, Decimal(0))/total_loss), 4) if total_loss else None,
        "expectancy": money(pnl/count) if count else None,
        "fees_known": money(known_fees), "trades_missing_fee_breakdown": missing_fees,
        "max_drawdown_closed": money(max_dd), "max_drawdown_pct_closed": float(dd_pct),
        "withdrawals": money(sum((number(e["withdrawal_usd"]) for e in events if e["event_type"] == "withdrawal"), Decimal(0))),
        "curve": curve,
        "days": [{"session": k, "pnl": money(v)} for k,v in sorted(days.items())],
        "first_at": events[0]["closed_at"] if events else None,
        "last_at": events[-1]["closed_at"] if events else None,
        "sharpe": None,
        "limitations": ["Drawdown sur trades fermés seulement: le risque intratrade reste inconnu.", "La couverture du fichier ne prouve pas un fonctionnement continu.", "Le net est déclaré par le fichier; aucune réconciliation broker effectuée."],
    }


def max_micros(size: int, stage: str, profit: Decimal) -> int:
    full = SIZES[size][2]
    if stage == "evaluation": return full
    thresholds = [(0, {25000:10,50000:20,100000:30,150000:40}),
                  (1000,{25000:20,50000:30,100000:40,150000:50}),
                  (2000,{25000:20,50000:40,100000:50,150000:60}),
                  (3000,{25000:20,50000:40,100000:60,150000:80}),
                  (4500,{25000:20,50000:40,100000:60,150000:100})]
    limit = thresholds[0][1][size]
    for threshold, limits in thresholds:
        if profit >= threshold: limit = limits[size]
    return limit


def replay(events: list[dict], profile: dict) -> dict:
    try: size = int(profile.get("size", 50000))
    except (ValueError,TypeError): raise ValidationError("Taille de compte invalide") from None
    if size not in SIZES: raise ValidationError("Taille LucidFlex non prise en charge")
    stage = profile.get("stage", "evaluation")
    if stage not in {"evaluation", "funded"}: raise ValidationError("Phase invalide")
    nominal = Decimal(size)
    cash = number(profile.get("initial_balance", size), "Solde initial", cents=True)
    if cash <= 0: raise ValidationError("Solde initial invalide")
    allowance, target, _ = SIZES[size]
    locked_floor = nominal + 100
    if "initial_floor" in profile and profile["initial_floor"] not in (None, ""):
        floor = number(profile["initial_floor"], "Plancher initial", cents=True)
    else:
        if cash != nominal:
            raise ValidationError("Solde différent du nominal: fournir le plancher réel au début de l’historique")
        floor = nominal - allowance
    if floor >= cash or floor > locked_floor or floor < nominal - allowance:
        raise ValidationError("Plancher initial incompatible avec le solde")
    dll = profile.get("daily_loss_limit")
    dll = number(dll, "DLL", cents=True) if dll not in (None, "") else None
    if dll is not None and dll <= 0: raise ValidationError("DLL strictement positive requise")
    init_cash = cash
    high_close = max(nominal, floor + allowance)
    current_session = None
    session_start = cash
    session_pnl = Decimal(0)
    rows, violations = [], []
    breached = False
    dll_latched = False
    total_pnl = Decimal(0)
    daily_pnl = defaultdict(lambda: Decimal(0))
    current_limit = max_micros(size, stage, cash - nominal)

    def finish_session():
        nonlocal high_close, floor, current_limit
        high_close = max(high_close, cash)
        floor = min(locked_floor, max(floor, high_close - allowance))
        current_limit = max_micros(size, stage, cash - nominal)

    for e in events:
        sess = e["session_date"]
        if current_session is not None and sess != current_session:
            finish_session()
            session_start, session_pnl, dll_latched = cash, Decimal(0), False
        current_session = sess
        limit_before = current_limit
        if e["event_type"] == "withdrawal":
            if stage != "funded":
                raise ValidationError("Retrait présent dans un replay d’évaluation; sélectionner funded")
            cash -= number(e["withdrawal_usd"])
            floor = locked_floor  # published rule: payout locks MLL
        else:
            v = number(e["pnl_net_usd"])
            if dll_latched:
                violations.append({"event_id": e["event_id"], "kind": "AFTER_DLL", "message": "Trade après verrouillage DLL dans la même séance"})
            cash += v
            total_pnl += v
            session_pnl += v
            daily_pnl[sess] += v
        if cash <= floor:
            breached = True
            violations.append({"event_id":e["event_id"],"kind":"MLL", "message":"Solde fermé au niveau ou sous le plancher"})
        if dll is not None and session_pnl <= -dll:
            dll_latched = True
        rows.append({"event_id": e["event_id"], "session": sess, "cash": money(cash),
                     "floor": money(floor), "cushion": money(cash-floor), "max_micros_during_session": limit_before,
                     "dll_locked": dll_latched, "breached_so_far": breached})
    floor_during_last = floor
    if current_session is not None: finish_session()
    best_day = max(daily_pnl.values(), default=Decimal(0))
    consistency = float(best_day/total_pnl*100) if total_pnl > 0 else None
    return {
        "size": size, "stage": stage, "account_verified": False,
        "breached_on_closed_events": breached, "daily_loss_locked": dll_latched,
        "cash": money(cash), "floor_during_last_session": money(floor_during_last),
        "floor_next_session": money(floor), "cushion_next_session": money(cash-floor),
        "max_micros_next_session": current_limit, "violations": violations,
        "target_pnl": target, "imported_net_pnl": money(total_pnl),
        "evaluation_consistency_pct": consistency if stage == "evaluation" else None,
        "evaluation_conditions_on_closed_data": bool(stage == "evaluation" and total_pnl >= target and consistency is not None and consistency <= 50 and not breached),
        "timeline": rows,
        "limitations": ["Pas de preuve de conformité: positions latentes et ordres en attente absents.",
                        "Historique supposé complet depuis le solde et le plancher saisis.",
                        "Séance inférée à 18h New York sauf session_date fourni. Cutoffs et jours fériés non validés.",
                        "Le cushion de cohérence et l’éligibilité des payouts ne sont pas simulés.",
                        "Les plafonds sont affichés; sans quantités/positions le respect du scaling n’est pas vérifiable.",
                        "Dernière séance supposée terminée pour calculer le plancher de la suivante."],
    }


def risk_estimate(params: dict) -> dict:
    def n(k): return number(params.get(k), k)
    equity, floor, buffer = n("equity"), n("floor"), n("buffer")
    remaining, reserved, cap = n("daily_remaining"), n("reserved_risk"), n("risk_cap")
    ticks, value, fees, slip = n("stop_ticks"), n("tick_value"), n("fees"), n("slippage_ticks")
    if min(equity, ticks, value, cap) <= 0 or min(buffer, reserved, fees, slip, remaining) < 0:
        raise ValidationError("Valeurs de risque incohérentes")
    maximum = n("max_contracts")
    if maximum < 0 or maximum != int(maximum) or maximum > 100:
        raise ValidationError("max_contracts: entier entre 0 et 100")
    if ticks != int(ticks): raise ValidationError("stop_ticks: entier requis")
    age = n("snapshot_age_seconds")
    if age < 0: raise ValidationError("Âge du snapshot invalide")
    fresh = age <= Decimal(5)
    budget = max(Decimal(0), min(cap, equity-floor-buffer-reserved, remaining-reserved))
    per_contract = ticks*value + fees + slip*value
    qty = min(int(maximum), int((budget/per_contract).to_integral_value(rounding=ROUND_FLOOR))) if fresh else 0
    return {"mode":"SIMULATION_MANUELLE", "execution_authorized":False,
            "quantity_estimate":qty, "budget":money(budget), "risk_per_contract":money(per_contract),
            "reserved_risk":money(reserved), "snapshot_fresh":fresh,
            "reason":"Snapshot périmé: estimation bloquée" if not fresh else "Budget insuffisant" if qty == 0 else "Taille théorique seulement; aucune autorisation d’ordre",
            "limitations":"Valeurs manuelles, absence de flux broker. Gap et glissement peuvent dépasser la provision."}


def example_rows() -> list[dict]:
    values = [75,-44,90,-60,120,-85,55,80,-110,140,-35,95]
    return [{"event_id":f"FICTIF-{i+1:03}","closed_at":f"2026-10-{1+i:02}T15:00:00Z",
             "pnl_net_usd":v,"fees_usd":"4.00","symbol":"MES-FICTIF","account_id":"EXEMPLE_FICTIF"}
            for i,v in enumerate(values)]

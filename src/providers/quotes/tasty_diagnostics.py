"""Precise Tastytrade quote-path diagnostics — READ-ONLY.

Answers ONE question the operator hits during RTH: *"ZerσSigma structure renders,
but why is Tasty market data unavailable for SPX?"* It walks the full quote path
and surfaces the EXACT stage that broke:

    config  →  auth/session  →  root resolution  →  expiry/DTE  →  chain  →  validation

Every stage is non-fatal: a network/auth error is caught and reported as a
sanitized reason (exception TYPE only), never a traceback. The result dict is
SAFE TO PRINT — it never contains a token, password, client_secret, refresh_token,
Authorization header, or full account number (only present/missing booleans and
the env/base_url, which are not secrets).

This module does NOT change strategy/selector logic, does NOT place orders, and
does NOT preview orders. It reuses the existing `TastyProbeClient` (read-only) and
the existing `QuoteValidation` thresholds.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from src.providers.quotes.tasty_probe import (
    TastyProbeClient,
    TastyProbeConfig,
)
from src.providers.quotes.tasty_probe import (
    config_from_env as _probe_config_from_env,
)
from src.providers.quotes.types import OptionQuote, OptionType, QuoteValidation
from src.utils.time import now_et

# Symbol → strike grid step for the small ATM probe ladder. SPX/SPXW use 5-pt
# strikes; SPY/QQQ use 1-pt. Default 5 (the SPX family is the live case).
_STRIKE_STEP = {"SPX": 5.0, "SPXW": 5.0, "XSP": 5.0, "SPY": 1.0, "QQQ": 1.0, "IWM": 1.0}


def _strike_step(symbol: str) -> float:
    return _STRIKE_STEP.get((symbol or "").upper().strip(), 5.0)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        v = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _median(values: list[float]) -> float | None:
    xs = sorted(v for v in values if isinstance(v, (int, float)))
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _atm_ladder(center: float | None, step: float, n: int = 5) -> list[float]:
    if center is None:
        return []
    base = round(center / step) * step
    half = n // 2
    return sorted({round(base + i * step, 4) for i in range(-half, half + 1)})


def _occ_strike_right(occ: str) -> tuple[float, str] | None:
    """Recover (strike, 'C'|'P') from an OCC symbol ROOT(6)YYMMDD R STRIKE(8)."""
    try:
        right = occ[12]
        strike = int(occ[13:21]) / 1000.0
    except (IndexError, ValueError, TypeError):
        return None
    return (strike, right)


def _blank_result(symbol: str, target_dte: int) -> dict[str, Any]:
    return {
        "symbol": (symbol or "SPX").upper(),
        "target_dte": int(target_dte),
        # config — OAuth + legacy reported SEPARATELY (OAuth is preferred)
        "configured": False,
        "auth_mode": "none",                  # oauth | legacy_session | none
        "oauth_configured": False,
        "legacy_configured": False,
        "usable_auth_modes": [],
        "oauth_missing_fields": [],
        "legacy_missing_fields": [],
        "missing_config_fields": [],          # OAuth-led when nothing is configured
        "auth_summary": None,
        "env": None,
        "base_url": None,
        # quote-provider + trade-scope context (read-only; never enables execution)
        "quote_provider": None,
        "quote_provider_warning": None,
        "trade_scope_present": False,
        "allow_trade_scope": False,
        "order_submission_enabled": False,
        "trade_scope_warning": None,
        # auth
        "auth_attempted": False,
        "auth_success": None,
        "auth_reason": None,
        "auth_http_status": None,
        # root
        "resolved_root": None,
        "root_resolution_source": None,
        "available_roots": [],
        # expiry
        "resolved_expiration": None,
        "expiration_source": None,
        "has_0dte_today": None,
        "expiry_reason": None,
        # chain / quotes
        "chain_returned": False,
        "chain_http_status": None,
        "quote_count": 0,
        "requested_strikes": [],
        "strike_min": None,
        "strike_max": None,
        "sample_strikes": [],
        "bid_ask_populated_count": 0,
        "bid_ask_missing_count": 0,
        # validation
        "validation_passed_count": 0,
        "validation_failed_count": 0,
        "validation_blockers": {},
        "stale_count": 0,
        "invalid_bid_ask_count": 0,
        "missing_strikes": [],
        # outcome
        "last_error": None,
        "blocker": None,
        "final_status": "",
    }


def diagnose_quote_path(
    cfg: TastyProbeConfig,
    *,
    symbol: str = "SPX",
    target_dte: int = 0,
    validation: QuoteValidation | None = None,
    client_factory: Any = None,
    spot_hint: float | None = None,
    now: datetime | None = None,
    n_strikes: int = 5,
) -> dict[str, Any]:
    """Walk config → auth → root → expiry → chain → validation, returning a
    sanitized diagnostics dict. Never raises; never echoes secrets."""
    r = _blank_result(symbol, target_dte)
    sym = r["symbol"]
    validation = validation or QuoteValidation()
    now_dt = (now or now_et())
    now_utc = now_dt.astimezone(UTC) if now_dt.tzinfo else now_dt.replace(tzinfo=UTC)

    # ── 1) configured? Report OAuth + legacy SEPARATELY; OAuth is preferred. ──
    # An account with OAuth creds (CLIENT_ID + CLIENT_SECRET + REFRESH_TOKEN) is
    # fully configured — username/password are an OPTIONAL legacy fallback and
    # must NOT make the diagnostic claim Tasty is unconfigured.
    r["env"] = cfg.env
    r["base_url"] = cfg.resolved_base_url()
    r["auth_mode"] = cfg.auth_mode()                    # oauth | legacy_session | none
    r["oauth_configured"] = cfg.has_oauth()
    r["legacy_configured"] = cfg.has_legacy_session()
    r["oauth_missing_fields"] = cfg.oauth_missing_fields()
    r["legacy_missing_fields"] = cfg.legacy_missing_fields()
    r["usable_auth_modes"] = cfg.usable_auth_modes()
    r["configured"] = cfg.is_configured()

    # QUOTE_PROVIDER context — the app only PRICES via Tasty when this resolves to
    # tastytrade (or the Live data-source override picks it). The diagnostic still
    # probes Tasty directly regardless, but flags the mismatch.
    qp = (os.environ.get("QUOTE_PROVIDER") or "").strip().lower()
    r["quote_provider"] = qp or "(unset → mock)"
    if qp != "tastytrade":
        r["quote_provider_warning"] = (
            f"QUOTE_PROVIDER={r['quote_provider']}, so the app will NOT use live Tasty quotes "
            "unless the Live data-source override is selected or QUOTE_PROVIDER=tastytrade."
        )

    # Trade-scope SAFETY read — purely informational. This never enables order
    # submission and there is no submit path in this module.
    r["trade_scope_present"] = cfg.trade_scope_present()
    r["allow_trade_scope"] = bool(cfg.allow_trade_scope)
    r["order_submission_enabled"] = bool(cfg.enable_order_submission)
    if (cfg.trade_scope_present() or cfg.allow_trade_scope) and not cfg.enable_order_submission:
        r["trade_scope_warning"] = (
            "Trade scope is present, but TASTY_ENABLE_ORDER_SUBMISSION=false. "
            "Quote fetching remains read-only."
        )

    # Human auth summary — OAuth-led (never mislead toward legacy when OAuth is set).
    if cfg.has_oauth():
        r["auth_summary"] = "OAuth credentials found. Using OAuth refresh-token auth."
    elif cfg.has_legacy_session():
        r["auth_summary"] = (
            "Legacy username/password found. Using legacy session auth "
            "(OAuth refresh-token is preferred when set)."
        )
    else:
        r["auth_summary"] = (
            "Tasty OAuth credentials missing: " + ", ".join(cfg.oauth_missing_fields())
            + ". (Optional legacy fallback: TASTY_USERNAME, TASTY_PASSWORD.)"
        )

    if not r["configured"]:
        # Lead with the OAuth requirement, NOT legacy username/password.
        r["missing_config_fields"] = cfg.oauth_missing_fields()
        r["blocker"] = "not_configured"
        r["final_status"] = r["auth_summary"]
        return r

    probe = TastyProbeClient(cfg, client_factory=client_factory)

    # ── 2) auth / session valid? ────────────────────────────────────────────
    r["auth_attempted"] = True
    try:
        auth = probe.login()
    except Exception as exc:                      # never propagate
        r["auth_success"] = False
        r["last_error"] = type(exc).__name__
        r["auth_reason"] = type(exc).__name__
        r["blocker"] = "auth_failed"
        r["final_status"] = "Tasty auth failed / session invalid."
        return r
    r["auth_success"] = bool(auth.get("auth_success"))
    r["auth_http_status"] = auth.get("http_status")
    if not r["auth_success"]:
        r["auth_reason"] = (
            auth.get("reason") or auth.get("error_type")
            or (f"http={auth.get('http_status')}" if auth.get("http_status") else "unknown")
        )
        r["last_error"] = r["auth_reason"]
        r["blocker"] = "auth_failed"
        r["final_status"] = "Tasty auth failed / session invalid."
        return r

    # ── 3) chain summary (roots + expirations) ──────────────────────────────
    try:
        summary = probe.get_option_chain_summary(sym)
    except Exception as exc:
        r["last_error"] = type(exc).__name__
        r["blocker"] = "root_unresolved"
        r["final_status"] = "SPX root/expiry unresolved (chain summary unavailable)."
        return r
    if not summary.get("ok"):
        r["chain_http_status"] = summary.get("http_status")
        r["last_error"] = f"chain_summary_http={summary.get('http_status')}"
        r["blocker"] = "root_unresolved"
        r["final_status"] = "SPX root/expiry unresolved (option-chains endpoint failed)."
        return r
    roots = summary.get("roots") or []
    r["available_roots"] = [x.get("root_symbol") for x in roots if x.get("root_symbol")]
    r["has_0dte_today"] = summary.get("has_0dte_today")
    all_expirations = sorted({
        e for x in roots for e in (x.get("expirations") or [])
    })

    # ── 4) expiry / DTE resolution ──────────────────────────────────────────
    expiry = _resolve_expiration(all_expirations, target_dte, now_utc, r)
    if expiry is None:
        # `_resolve_expiration` already set expiry_reason/final_status/blocker.
        return r

    # ── 5) root resolution for that expiry ──────────────────────────────────
    try:
        root_meta = probe.resolve_root_for(sym, expiry)
    except Exception as exc:
        r["last_error"] = type(exc).__name__
        r["blocker"] = "root_unresolved"
        r["final_status"] = "SPX root/expiry unresolved."
        return r
    if not root_meta.get("ok"):
        r["expiry_reason"] = root_meta.get("reason")
        r["blocker"] = "root_unresolved"
        r["final_status"] = f"SPX root/expiry unresolved ({root_meta.get('reason')})."
        return r
    r["resolved_root"] = root_meta.get("root_symbol")
    r["root_resolution_source"] = root_meta.get("source")

    # ── 6) chain / quote pull (small ATM ladder) ────────────────────────────
    center = spot_hint if spot_hint else _median(
        [s for x in roots for s in (x.get("sample_strikes") or [])]
    )
    strikes = _atm_ladder(center, _strike_step(sym), n_strikes)
    r["requested_strikes"] = strikes
    if not strikes:
        r["blocker"] = "no_strikes_to_probe"
        r["final_status"] = "Tasty chain has no strikes to probe (empty sample)."
        return r
    try:
        qres = probe.get_option_quotes_for_strikes(sym, expiry, strikes, "C",
                                                   root_symbol=r["resolved_root"])
    except Exception as exc:
        r["last_error"] = type(exc).__name__
        r["blocker"] = "no_chain"
        r["final_status"] = "Tasty returned no chain (quote fetch error)."
        return r
    r["chain_http_status"] = qres.get("http_status")
    if not qres.get("ok"):
        r["last_error"] = qres.get("reason") or f"http={qres.get('http_status')}"
        r["blocker"] = "no_chain"
        r["final_status"] = (
            f"Tasty returned no chain (reason: {qres.get('reason') or qres.get('http_status')})."
        )
        return r
    r["chain_returned"] = True
    quotes = qres.get("quotes") or []
    r["quote_count"] = len(quotes)

    # ── 7) populate + validate ──────────────────────────────────────────────
    _summarize_quotes(quotes, expiry, sym, validation, now_utc, strikes, r)

    if r["quote_count"] == 0:
        r["blocker"] = "empty_chain"
        r["final_status"] = "Tasty chain returned 0 quotes for the probed strikes."
    elif r["validation_passed_count"] == 0:
        top = max(r["validation_blockers"], key=r["validation_blockers"].get) \
            if r["validation_blockers"] else "unknown"
        r["blocker"] = "quotes_invalid"
        r["final_status"] = (
            f"Tasty chain returned but ALL {r['quote_count']} quotes failed validation "
            f"(top reason: {top})."
        )
    else:
        r["blocker"] = None
        r["final_status"] = (
            f"Tasty quotes OK — {r['validation_passed_count']}/{r['quote_count']} valid "
            f"on {r['resolved_root']} @ {expiry}."
        )
    return r


def _resolve_expiration(
    all_expirations: list[str], target_dte: int, now_utc: datetime, r: dict[str, Any],
) -> str | None:
    """Resolve the target expiration date from the chain's expirations.

    0DTE → today's date; ≥1DTE → the Nth available trading expiration on/after
    today. Sets r['resolved_expiration']/['expiration_source'] on success, or
    r['expiry_reason']/['final_status']/['blocker'] and returns None on failure.
    """
    today = now_utc.date().isoformat()
    future = [e for e in all_expirations if e >= today]
    if int(target_dte) == 0:
        if today in all_expirations:
            r["resolved_expiration"] = today
            r["expiration_source"] = "today_0dte"
            return today
        r["expiry_reason"] = (
            f"no 0DTE expiration listed for today ({today}) — market closed / holiday / "
            "weekend, or today's contracts not yet listed"
        )
        r["blocker"] = "expiry_unavailable"
        r["final_status"] = f"SPX 0DTE expiration unavailable: {r['expiry_reason']}."
        return None
    # target_dte >= 1: pick the Nth distinct expiration on/after today.
    idx = int(target_dte)
    if idx < len(future):
        exp = future[idx]
        r["resolved_expiration"] = exp
        r["expiration_source"] = f"nth_available(dte={idx})"
        return exp
    r["expiry_reason"] = (
        f"requested DTE {idx} but only {len(future)} expirations on/after {today}"
    )
    r["blocker"] = "expiry_unavailable"
    r["final_status"] = f"Tasty expiration for DTE {idx} unavailable: {r['expiry_reason']}."
    return None


def _summarize_quotes(
    quotes: list[dict[str, Any]], expiry: str, symbol: str,
    validation: QuoteValidation, now_utc: datetime, requested_strikes: list[float],
    r: dict[str, Any],
) -> None:
    """Populate bid/ask + validation aggregates onto the result dict `r`."""
    populated = missing_ba = passed = failed = stale = invalid_ba = 0
    blockers: dict[str, int] = {}
    strikes_seen: list[float] = []
    returned_keys: set[tuple[float, str]] = set()

    for q in quotes:
        occ = q.get("symbol") or ""
        sr = _occ_strike_right(occ)
        if sr is None:
            continue
        strike, right = sr
        strikes_seen.append(strike)
        returned_keys.add((strike, right))
        bid, ask, mid = q.get("bid"), q.get("ask"), q.get("mid")
        if bid is not None and ask is not None:
            populated += 1
        else:
            missing_ba += 1
        oq = OptionQuote(
            underlying=symbol, expiry=expiry,
            option_type=OptionType.CALL if right == "C" else OptionType.PUT,
            strike=strike, bid=bid, ask=ask,
            mid=mid if mid is not None else (
                (bid + ask) / 2.0 if bid is not None and ask is not None else None),
            volume=None, open_interest=None,
            quote_time=_parse_ts(q.get("ts")) or now_utc,
        )
        ok, reason = validation.validate(oq, now=now_utc)
        if ok:
            passed += 1
        else:
            failed += 1
            key = (reason or "unknown").split("(")[0]    # strip numeric detail
            blockers[key] = blockers.get(key, 0) + 1
            if reason and "stale" in reason:
                stale += 1
            if reason and ("missing_bid_or_ask" in reason or "crossed" in reason
                           or "zero_bid" in reason):
                invalid_ba += 1

    # Missing strikes: requested call strikes with no returned C quote.
    missing = [k for k in requested_strikes if (k, "C") not in returned_keys]

    r["bid_ask_populated_count"] = populated
    r["bid_ask_missing_count"] = missing_ba
    r["validation_passed_count"] = passed
    r["validation_failed_count"] = failed
    r["validation_blockers"] = blockers
    r["stale_count"] = stale
    r["invalid_bid_ask_count"] = invalid_ba
    r["missing_strikes"] = missing
    r["sample_strikes"] = sorted(set(strikes_seen))[:8]
    r["strike_min"] = min(strikes_seen) if strikes_seen else None
    r["strike_max"] = max(strikes_seen) if strikes_seen else None


# ── convenience: build from env (CLI + UI entry point) ───────────────────────

def diagnose_from_env(
    *,
    symbol: str = "SPX",
    target_dte: int = 0,
    client_factory: Any = None,
    spot_hint: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build config + validation from TASTY_* env and run the diagnostic."""
    from src.providers.quotes.tastytrade_provider import validation_from_env
    return diagnose_quote_path(
        _probe_config_from_env(),
        symbol=symbol, target_dte=target_dte,
        validation=validation_from_env(),
        client_factory=client_factory, spot_hint=spot_hint, now=now,
    )


# ── presentation (one source of truth for CLI + Streamlit) ───────────────────

def summary_rows(diag: dict[str, Any]) -> list[tuple[str, str]]:
    """Ordered (label, value) rows for the CLI block + the cockpit expander."""
    def _s(v: Any) -> str:
        return "—" if v is None or v == "" or v == [] or v == {} else str(v)

    auth = ("success" if diag.get("auth_success") else
            (f"FAILED — {diag.get('auth_reason')}" if diag.get("auth_attempted")
             else "not attempted"))
    val = (f"{diag.get('validation_passed_count')} passed / "
           f"{diag.get('validation_failed_count')} failed")
    if diag.get("validation_blockers"):
        val += f"  {diag['validation_blockers']}"
    rows: list[tuple[str, str]] = [
        ("configured", _s(diag.get("configured"))),
        ("OAuth/API configured", _s(diag.get("oauth_configured"))),
        ("legacy user/pass configured", _s(diag.get("legacy_configured"))),
        ("selected auth mode", _s(diag.get("auth_mode"))),
        ("missing OAuth vars", _s(diag.get("oauth_missing_fields"))),
        ("missing legacy vars", _s(diag.get("legacy_missing_fields"))),
        ("TASTY_ENV", _s(diag.get("env"))),
        ("TASTY_BASE_URL", _s(diag.get("base_url"))),
        ("QUOTE_PROVIDER", _s(diag.get("quote_provider"))),
        ("auth summary", _s(diag.get("auth_summary"))),
        ("auth / session", auth),
        ("resolved root", f"{_s(diag.get('resolved_root'))} "
                          f"(source {_s(diag.get('root_resolution_source'))})"),
        ("available roots", _s(diag.get("available_roots"))),
        ("resolved expiration", f"{_s(diag.get('resolved_expiration'))} "
                                f"(source {_s(diag.get('expiration_source'))})"),
        ("0DTE today exists", _s(diag.get("has_0dte_today"))),
        ("expiry note", _s(diag.get("expiry_reason"))),
        ("chain returned", _s(diag.get("chain_returned"))),
        ("quote count", _s(diag.get("quote_count"))),
        ("strike min / max", f"{_s(diag.get('strike_min'))} / {_s(diag.get('strike_max'))}"),
        ("sample strikes", _s(diag.get("sample_strikes"))),
        ("bid/ask populated", f"{_s(diag.get('bid_ask_populated_count'))} populated / "
                              f"{_s(diag.get('bid_ask_missing_count'))} missing"),
        ("validation", val),
        ("stale / invalid b-a / missing", f"{_s(diag.get('stale_count'))} / "
                                          f"{_s(diag.get('invalid_bid_ask_count'))} / "
                                          f"{_s(diag.get('missing_strikes'))}"),
        ("last_error", _s(diag.get("last_error"))),
    ]
    if diag.get("quote_provider_warning"):
        rows.append(("WARN quote provider", diag["quote_provider_warning"]))
    if diag.get("trade_scope_warning"):
        rows.append(("WARN trade scope", diag["trade_scope_warning"]))
    rows.append(("FINAL", _s(diag.get("final_status"))))
    return rows

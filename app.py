"""
Liquidation Contrarian Agent
一键启动: python app.py → http://localhost:5000
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any

from claw402 import Claw402Error
from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_cors import CORS

from providers import LLM_PROVIDERS
from services.agent_chat import chat_with_agent
from services.backtest import run_simple_backtest
from services.claw_docs import CLAW402_COINANK_ENDPOINTS, CLAW402_SUMMARY
from services.coinank import create_client, fetch_market_snapshot, unwrap_data
from services.evolution import EvolutionEngine
from services.heatmap_manager import HeatmapSnapshotManager
from services.liquidation_maps import normalize_liquidation_map
from services.llm import _as_dict, analyze_with_llm
from services.market_data import MarketDataError, MarketDataRestrictedError, market_data_service
from services.strategy_agent import apply_llm_review, review_trade_with_llm
from services.x_sentiment import get_service as get_x_service
from services.xai_chat import get_chat_service as get_xai_chat
from services.x_pipeline import get_pipeline_service as get_x_pipeline
from services.x_poster import get_poster_service as get_x_poster
from state import AgentState
from strategy.models import MarketSnapshot, StrategyConfig
from strategy.signals import LiquidationContrarianSignalEngine
from trading.execution import BinanceExecutionAdapter, PaperExecutionAdapter
from trading.risk import RiskManager

import sys
if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
CORS(
    app,
    resources={r"/api/*": {"origins": os.getenv("ALLOWED_ORIGINS", "*").split(",")}},
)

agent_state = AgentState()
signal_engine = LiquidationContrarianSignalEngine()
heatmap_manager = HeatmapSnapshotManager()
evolution_engine = EvolutionEngine()
risk_manager = RiskManager()
paper_executor = PaperExecutionAdapter()
binance_executor = BinanceExecutionAdapter()
worker_stop = threading.Event()
worker_thread: threading.Thread | None = None
worker_private_key = ""
tick_lock = threading.Lock()

SYMBOL_RE = re.compile(r"^[A-Z0-9]{3,20}$")
INTERVAL_RE = re.compile(r"^[0-9]+[mhdwM]$")
EXCHANGE_RE = re.compile(r"^[a-z0-9_-]{2,32}$")


@app.route("/js/<path:filename>")
def js_files(filename):
    return send_from_directory(os.path.join(BASE_DIR, "templates"), filename)


def _parse_size(value, min_value=1, max_value=200):
    try:
        size = int(value)
    except (TypeError, ValueError):
        return None, f"Size must be an integer between {min_value} and {max_value}"
    if not min_value <= size <= max_value:
        return None, f"Size must be between {min_value} and {max_value}"
    return size, ""


def _json_error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _get_private_key(payload: dict[str, Any] | None = None, *, allow_worker_key: bool = False) -> str:
    payload = payload or {}
    return (payload.get("pk") or (worker_private_key if allow_worker_key else "") or os.getenv("CLAW402_PRIVATE_KEY", "")).strip()


def _normalize_market_payload(payload: dict[str, Any]) -> tuple[str, str, str, str, int, str]:
    coin = str(payload.get("coin", "BTC")).strip().upper()
    symbol = str(payload.get("symbol", "BTCUSDT")).strip().upper()
    exchange = str(payload.get("exchange", "binance")).strip().lower()
    interval = str(payload.get("interval", "1h")).strip()
    size, size_error = _parse_size(payload.get("size", 24))
    if size_error:
        return coin, symbol, exchange, interval, 0, size_error
    if not SYMBOL_RE.match(coin):
        return coin, symbol, exchange, interval, 0, "Invalid coin"
    if not SYMBOL_RE.match(symbol):
        return coin, symbol, exchange, interval, 0, "Invalid symbol"
    if not EXCHANGE_RE.match(exchange):
        return coin, symbol, exchange, interval, 0, "Invalid exchange"
    if not INTERVAL_RE.match(interval):
        return coin, symbol, exchange, interval, 0, "Invalid interval"
    return coin, symbol, exchange, interval, size or 24, ""


def _snapshot_from_market(config: StrategyConfig, market: dict[str, Any]) -> MarketSnapshot:
    symbol = str(market.get("symbol") or config.symbol).upper()
    coin = symbol[:-4] if symbol.endswith("USDT") else config.coin
    exchange = str(market.get("exchange") or config.exchange).lower()
    return MarketSnapshot(
        coin=coin,
        symbol=symbol,
        exchange=exchange,
        interval=config.interval,
        price=float(market.get("price") or 0),
        oi=[{"symbol": symbol, "exchange": exchange, "openInterest": market.get("open_interest", 0)}],
        funding=[{"symbol": symbol, "exchange": exchange, "rate": market.get("funding_rate", 0)}],
        market=market,
        data_warnings=[],
    )


def _apply_market_to_snapshot(snapshot: MarketSnapshot, market: dict[str, Any]):
    snapshot.price = float(market.get("price") or snapshot.price or 0)
    snapshot.funding = [{"symbol": snapshot.symbol, "exchange": snapshot.exchange, "rate": market.get("funding_rate", 0)}]
    snapshot.oi = [{"symbol": snapshot.symbol, "exchange": snapshot.exchange, "openInterest": market.get("open_interest", 0)}]
    snapshot.market = market


@app.route("/")
def index():
    return render_template("index.html", providers=LLM_PROVIDERS)


@app.route("/api/providers")
def providers():
    return jsonify({"providers": LLM_PROVIDERS})


@app.route("/api/llm/models", methods=["POST"])
def llm_models():
    """Test connection and fetch available models from a provider.

    Body: {provider: str, api_key: str, base_url?: str}
    Returns: {models: [{id, name, context_length?}], provider}

    For xAI: filters to grok models only.
    For Anthropic: returns hardcoded known models (no /models endpoint).
    For all others: calls GET {base_url}/models (OpenAI-compatible).
    """
    import json as _json
    from urllib.request import Request as _Req, urlopen as _urlopen
    from urllib.error import HTTPError as _HErr, URLError as _UErr

    payload = request.get_json() or {}
    provider_id = str(payload.get("provider") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    base_url = str(payload.get("base_url") or "").strip()

    if not api_key:
        return _json_error("API key is required")
    if not provider_id:
        return _json_error("Provider is required")

    # Anthropic: no /models endpoint, return known models
    if provider_id == "anthropic":
        models = [
            {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "context_length": 200000},
            {"id": "claude-opus-4-7", "name": "Claude Opus 4.7", "context_length": 1000000},
            {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5", "context_length": 200000},
        ]
        return jsonify({"models": models, "provider": provider_id})

    # Resolve base URL
    if not base_url:
        for p in LLM_PROVIDERS:
            if p["id"] == provider_id:
                base_url = p.get("base", "")
                break
    if not base_url:
        return _json_error(f"No base URL for provider '{provider_id}'")

    url = base_url.rstrip("/") + "/models"
    req = _Req(url, headers={"Authorization": f"Bearer {api_key}"}, method="GET")
    try:
        with _urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except _HErr as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        return jsonify({"error": f"HTTP {exc.code}: {detail}"}), exc.code
    except _UErr as exc:
        return jsonify({"error": f"Connection failed: {exc.reason}"}), 502
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    # Parse response (OpenAI format: {data: [{id, ...}]})
    if isinstance(data, dict):
        raw_models = data.get("data") or data.get("models") or []
    elif isinstance(data, list):
        raw_models = data
    else:
        return _json_error(f"Unexpected models response JSON: {type(data).__name__}", 502)

    models = []
    for m in raw_models:
        if not isinstance(m, dict):
            continue
        mid = m.get("id") or m.get("model") or ""
        # For xAI: only show grok models
        if provider_id == "xai" and "grok" not in mid.lower():
            continue
        name = m.get("name") or mid
        ctx = m.get("context_length") or m.get("context_window") or m.get("max_tokens") or None
        models.append({"id": mid, "name": name, "context_length": ctx})

    # Sort: prefer newer/larger models first
    models.sort(key=lambda x: x["id"], reverse=True)
    return jsonify({"models": models, "provider": provider_id})


@app.route("/api/claw402/docs")
def claw402_docs():
    return jsonify({"summary": CLAW402_SUMMARY, "endpoints": CLAW402_COINANK_ENDPOINTS})


@app.route("/api/analyze", methods=["POST"])
def analyze():
    payload = request.get_json() or {}
    try:
        text = analyze_with_llm(
            provider_id=payload.get("provider", "anthropic"),
            api_key=(payload.get("api_key") or "").strip(),
            model=(payload.get("model") or "").strip(),
            payload=payload.get("data") or {},
            user_prompt=(payload.get("prompt") or "").strip(),
        )
        return jsonify({"analysis": text})
    except Exception as exc:
        return _json_error(str(exc))


@app.route("/api/wallet", methods=["POST"])
def wallet():
    pk = (request.get_json() or {}).get("pk", "").strip()
    if not pk:
        return _json_error("Enter private key")
    _, addr, err = create_client(pk)
    if err:
        return _json_error(err)
    return jsonify({"address": addr})


@app.route("/api/liquidation", methods=["POST"])
def liquidation():
    payload = request.get_json() or {}
    pk = _get_private_key(payload)
    coin, symbol, exchange, interval, size, payload_error = _normalize_market_payload(payload)

    if not pk:
        return _json_error("Enter wallet key")
    if payload_error:
        return _json_error(payload_error)

    client, addr, err = create_client(pk)
    if err:
        return _json_error(err)

    try:
        snapshot = fetch_market_snapshot(client, coin, symbol, exchange, interval, size)
        snapshot.wallet = addr
        data = snapshot.to_dict()
        data["snapshot"] = snapshot.to_dict()
        return jsonify(data)
    except Claw402Error as exc:
        return jsonify({"wallet": addr, "error": f"Payment failed: {exc}"})
    except Exception as exc:
        return jsonify({"wallet": addr, "error": str(exc)})


@app.route("/api/market/overview")
def market_overview():
    """Multi-coin market snapshot (light data only, no liq map).
    Reads cached last-snapshots from agent_state to avoid extra API calls.
    Optional ?coins=BTC,ETH,SOL — if backend has fresh data, returns it;
    otherwise returns derived values from agent's latest snapshot."""
    coins_param = request.args.get("coins", "BTC,ETH,SOL,BNB,XRP,DOGE")
    coins = [c.strip().upper() for c in coins_param.split(",") if c.strip()]
    snapshot = _as_dict(agent_state.last_snapshot)
    cfg = agent_state.config
    primary_coin = (cfg.coin or "BTC").upper()
    primary_price = snapshot.get("price") or 0
    primary_funding = snapshot.get("funding_rate") or 0
    primary_oi = snapshot.get("open_interest") or snapshot.get("oi") or 0
    # Build only from cached real exchange data; unknown symbols stay null.
    result = []
    for c in coins:
        if c == primary_coin and primary_price:
            result.append({
                "symbol": c,
                "price": float(primary_price or 0),
                "change_24h": 0.0,
                "volume_24h": 0,
                "oi": float(primary_oi or 0),
                "funding_rate": float(primary_funding or 0),
            })
        else:
            result.append({
                "symbol": c,
                "price": None,
                "change_24h": None,
                "volume_24h": None,
                "oi": None,
                "funding_rate": None,
            })
    return jsonify({"coins": result, "primary": primary_coin})


@app.route("/api/market/refresh", methods=["POST"])
def market_refresh():
    payload = request.get_json() or {}
    try:
        config = agent_state.config
        config_patch = dict(payload.get("config") or {})
        for key in ("coin", "symbol", "exchange", "interval"):
            if payload.get(key):
                config_patch[key] = payload.get(key)
        if config_patch:
            config = agent_state.update_config(config_patch)
        symbol = str(payload.get("symbol") or config.symbol).upper().replace("/", "")
        exchange = str(payload.get("exchange") or config.exchange).lower()
        interval = str(payload.get("interval") or config.interval)
        market = market_data_service.fetch_snapshot(symbol, exchange=exchange, interval=interval)
        snapshot = _snapshot_from_market(config, market)
        agent_state.record_snapshot(snapshot)
        paper_executor.load_orders(agent_state.orders)
        paper_executor.mark_to_market(snapshot.symbol, snapshot.price)
        if paper_executor.broker.orders:
            agent_state.replace_orders(paper_executor.broker.orders)
        agent_state.add_event("market", "exchange market data refreshed", {
            "source": market.get("source"),
            "symbol": symbol,
            "exchange": exchange,
            "price": market.get("price"),
            "klines": len(market.get("klines") or []),
        }, module="market_data", action="refresh")
        return jsonify({"market": market, "snapshot": snapshot.to_dict(), "status": agent_state.status()})
    except MarketDataRestrictedError as exc:
        agent_state.add_event("error", str(exc), {"symbol": payload.get("symbol"), "exchange": payload.get("exchange"), "reason": "restricted_location"}, level="error", module="market_data", action="refresh")
        return jsonify({"error": str(exc), "code": "restricted_location"}), 451
    except MarketDataError as exc:
        agent_state.add_event("error", str(exc), {"symbol": payload.get("symbol"), "exchange": payload.get("exchange")}, level="error", module="market_data", action="refresh")
        return jsonify({"error": str(exc)}), 502


@app.route("/api/market/sentiment")
def market_sentiment():
    """Market fear & greed proxy. Derived from agent's last signal direction + heatmap intensity if available."""
    snapshot = _as_dict(agent_state.last_snapshot)
    sig = _as_dict(agent_state.last_signal)
    # Default neutral
    score = 50
    action = (sig.get("action") if isinstance(sig, dict) else None) or "wait"
    confidence = float((sig.get("confidence") if isinstance(sig, dict) else 0) or 0)
    if action == "long":
        score = int(55 + confidence * 30)
    elif action == "short":
        score = int(45 - confidence * 30)
    # Adjust by funding rate
    fr = snapshot.get("funding_rate")
    try:
        if fr is not None:
            score += int(float(fr) * 1000)
    except Exception:
        pass
    score = max(5, min(95, score))
    label = "极度贪婪" if score >= 75 else "贪婪" if score >= 55 else "中性" if score >= 45 else "恐惧" if score >= 25 else "极度恐惧"
    return jsonify({"score": score, "label": label})


@app.route("/api/market/volatility")
def market_volatility():
    snapshot = _as_dict(agent_state.last_snapshot)
    if not snapshot:
        return jsonify({"coins": [], "error": "No real exchange market data loaded yet"}), 404
    price = float(snapshot.get("price") or 0)
    return jsonify({"coins": [{"symbol": snapshot.get("symbol"), "value": price, "change": None, "source": "exchange"}]})


@app.route("/api/market/sectors")
def market_sectors():
    return jsonify({"sectors": [], "error": "No sector data provider configured"}), 501


@app.route("/api/market/flows")
def market_flows():
    return jsonify({"flows": [], "error": "No exchange flow data provider configured"}), 501


# ===================== X (Twitter) sentiment =====================

@app.route("/api/x/configure", methods=["POST"])
def x_configure():
    """Configure xAI Grok API key. Body: {api_key, model?, cache_ttl?, base_url?}"""
    payload = request.get_json() or {}
    api_key = str(payload.get("api_key") or payload.get("bearer_token") or "").strip()
    if not api_key:
        return jsonify({"error": "missing api_key"}), 400
    base_url = str(payload.get("base_url") or "").strip() or None
    svc = get_x_service()
    try:
        svc.configure(
            api_key,
            model=payload.get("model"),
            cache_ttl=payload.get("cache_ttl"),
            base_url=base_url,
        )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    try:
        get_x_pipeline().configure(api_key, model=payload.get("model"))
    except Exception:
        pass
    return jsonify({"configured": svc.is_configured()})


@app.route("/api/x/status")
def x_status():
    svc = get_x_service()
    return jsonify(svc.status_info())


@app.route("/api/x/data")
def x_data():
    """Single unified endpoint: returns sentiment + trending + top_tweets + narrative from ONE Grok call.

    Use ?force=1 to bypass 15-min cache (consumes xAI API quota).
    """
    svc = get_x_service()
    if not svc.is_configured():
        return jsonify({"error": "xAI API not configured"}), 400
    force = request.args.get("force", "").lower() in ("1", "true", "yes")
    try:
        return jsonify(svc.fetch_all(force=force))
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/api/x/analyze", methods=["POST"])
def x_analyze():
    """Returns AI narrative. Uses cached data — no extra Grok call unless cache expired."""
    svc = get_x_service()
    if not svc.is_configured():
        return jsonify({"error": "xAI API not configured"}), 400
    try:
        data = svc.fetch_all()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({
        "overview": data["sentiment"],
        "trending": data["trending"][:5],
        "top_tweets": data["top_tweets"][:5],
        "kol_views": data.get("kol_views", []),
        "dimensions": data.get("dimensions", {}),
        "actionable_signals": data.get("actionable_signals", []),
        "narrative": data.get("narrative", ""),
        "analysis": data.get("narrative", ""),  # alias for frontend compat
        "meta": data.get("meta", {}),
    })


# ===================== xAI Grok Analyst Chat =====================

@app.route("/api/xai/configure", methods=["POST"])
def xai_configure():
    """Configure xAI chat agent. Body: {api_key, model?, base_url?}"""
    payload = request.get_json() or {}
    api_key = str(payload.get("api_key") or "").strip()
    if not api_key:
        return jsonify({"error": "missing api_key"}), 400
    base_url = str(payload.get("base_url") or "").strip() or None
    chat = get_xai_chat()
    chat.configure(api_key, model=payload.get("model"), base_url=base_url)
    # Also configure the data service with the same key (they share xAI backend)
    try:
        get_x_service().configure(api_key, model=payload.get("model"), base_url=base_url)
    except Exception:
        pass
    # Also configure the pipeline service
    try:
        get_x_pipeline().configure(api_key, model=payload.get("model"))
    except Exception:
        pass
    return jsonify(chat.status_info())


@app.route("/api/xai/status")
def xai_status():
    return jsonify(get_xai_chat().status_info())


@app.route("/api/xai/system-prompt")
def xai_system_prompt():
    """Return the x_analyst.md system prompt for direct frontend use."""
    return jsonify({"prompt": get_xai_chat()._system_prompt})


@app.route("/api/xai/chat", methods=["POST"])
def xai_chat():
    """Send a message to the Grok analyst agent.

    Body: {
      message: str,
      session_id?: str (default 'default'),
      force_live_search?: bool,
      force_json?: bool
    }
    """
    payload = request.get_json() or {}
    message = str(payload.get("message") or "").strip()
    if not message:
        return jsonify({"error": "empty message"}), 400

    chat = get_xai_chat()
    if not chat.is_configured():
        return jsonify({"error": "xAI API not configured"}), 400

    session_id = str(payload.get("session_id") or "default")
    force_live = payload.get("force_live_search")
    force_json = payload.get("force_json")

    try:
        result = chat.chat(
            session_id=session_id,
            user_message=message,
            force_live_search=force_live,
            force_json=force_json,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


@app.route("/api/xai/history")
def xai_history():
    session_id = request.args.get("session_id", "default")
    return jsonify({"history": get_xai_chat().get_history(session_id)})


@app.route("/api/xai/reset", methods=["POST"])
def xai_reset():
    payload = request.get_json() or {}
    session_id = str(payload.get("session_id") or "default")
    get_xai_chat().reset_session(session_id)
    return jsonify({"ok": True, "session_id": session_id})


# ===================== X Poster (Tweet Publishing) =====================

@app.route("/api/x/poster/configure", methods=["POST"])
def x_poster_configure():
    """Configure OAuth 1.0a user context for posting.
    Body: {api_key, api_secret, access_token, access_token_secret}
    """
    payload = request.get_json() or {}
    poster = get_x_poster()
    try:
        poster.configure(
            api_key=str(payload.get("api_key") or ""),
            api_secret=str(payload.get("api_secret") or ""),
            access_token=str(payload.get("access_token") or ""),
            access_token_secret=str(payload.get("access_token_secret") or ""),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    # Verify by calling /2/users/me
    try:
        me = poster.verify_credentials()
    except Exception as e:
        return jsonify({"error": f"credentials rejected: {e}"}), 401
    return jsonify({"configured": True, "account": me})


@app.route("/api/x/poster/status")
def x_poster_status():
    return jsonify(get_x_poster().status_info())


@app.route("/api/x/poster/post", methods=["POST"])
def x_poster_post():
    """Post a tweet. Body: {text, reply_to_id?, dry_run?}"""
    payload = request.get_json() or {}
    text = str(payload.get("text") or "").strip()
    if not text:
        return jsonify({"error": "empty text"}), 400

    poster = get_x_poster()
    dry_run = bool(payload.get("dry_run"))
    if not dry_run and not poster.is_configured():
        return jsonify({"error": "X poster not configured"}), 400

    try:
        result = poster.post_tweet(
            text=text,
            reply_to_id=payload.get("reply_to_id"),
            dry_run=dry_run,
            media_data=payload.get("media_data"),
            media_alt_text=payload.get("media_alt_text"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify(result)


@app.route("/api/x/poster/thread", methods=["POST"])
def x_poster_thread():
    """Post a thread. Body: {tweets: [str], dry_run?}"""
    payload = request.get_json() or {}
    tweets = payload.get("tweets") or []
    if not isinstance(tweets, list) or not tweets:
        return jsonify({"error": "tweets must be a non-empty list"}), 400

    poster = get_x_poster()
    dry_run = bool(payload.get("dry_run"))
    if not dry_run and not poster.is_configured():
        return jsonify({"error": "X poster not configured"}), 400

    try:
        result = poster.post_thread([str(t) for t in tweets], dry_run=dry_run)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify(result)


@app.route("/api/x/poster/log")
def x_poster_log():
    limit = int(request.args.get("limit", 50))
    return jsonify({"posted": get_x_poster().get_posted_log(limit=limit)})


# ===================== Tweet Pipeline =====================

@app.route("/api/x/pipeline/generate", methods=["POST"])
def x_pipeline_generate():
    """Generate candidate tweets from combined data sources (market + signal + X sentiment).

    Body (all optional — falls back to configured defaults):
    {
      provider: "xai" | "openai" | "anthropic" | "deepseek" | "custom" | ...,
      api_key: "sk-...",
      model: "grok-4-fast",
      custom_base_url: "https://openrouter.ai/api/v1"
    }
    """
    payload = request.get_json() or {}
    pipeline = get_x_pipeline()

    provider_id = (payload.get("provider") or "").strip() or None
    api_key = (payload.get("api_key") or "").strip() or None
    model = (payload.get("model") or "").strip() or None
    custom_base_url = (payload.get("custom_base_url") or "").strip() or None
    image_provider_id = (payload.get("image_provider") or "").strip() or None
    image_api_key = (payload.get("image_api_key") or "").strip() or None
    image_model = (payload.get("image_model") or "").strip() or None
    image_custom_base_url = (payload.get("image_custom_base_url") or "").strip() or None

    if not pipeline.is_configured() and not api_key:
        return jsonify({"error": "Pipeline LLM not configured. Provide api_key or configure in settings."}), 400
    try:
        result = pipeline.generate(
            agent_state, get_x_service(),
            provider_id=provider_id,
            api_key=api_key,
            model=model,
            custom_base_url=custom_base_url,
            image_provider_id=image_provider_id,
            image_api_key=image_api_key,
            image_model=image_model,
            image_custom_base_url=image_custom_base_url,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


@app.route("/api/x/pipeline/publish", methods=["POST"])
def x_pipeline_publish():
    """Publish a reviewed tweet. Body: {text, dry_run?, media_data?, media_alt_text?}"""
    payload = request.get_json() or {}
    text = str(payload.get("text") or "").strip()
    if not text:
        return jsonify({"error": "empty text"}), 400

    poster = get_x_poster()
    dry_run = bool(payload.get("dry_run"))
    if not dry_run and not poster.is_configured():
        return jsonify({"error": "X poster not configured"}), 400

    try:
        result = poster.post_tweet(
            text=text,
            dry_run=dry_run,
            media_data=payload.get("media_data"),
            media_alt_text=payload.get("media_alt_text"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify(result)


@app.route("/api/x/pipeline/history")
def x_pipeline_history():
    """Get recent pipeline generation history."""
    pipeline = get_x_pipeline()
    limit = int(request.args.get("limit", 20))
    return jsonify({"history": pipeline.get_history(limit=limit)})


@app.route("/api/x/pipeline/status")
def x_pipeline_status():
    return jsonify(get_x_pipeline().status_info())


@app.route("/api/liqmap", methods=["POST"])
def liqmap():
    payload = request.get_json() or {}
    pk = _get_private_key(payload)
    coin = str(payload.get("coin", "BTC")).strip().upper()
    symbol = str(payload.get("symbol") or f"{coin}USDT").strip().upper().replace("/", "")
    exchange = str(payload.get("exchange") or agent_state.config.exchange).strip().lower()
    interval = str(payload.get("interval", "1h")).strip()

    if not pk:
        return _json_error("Enter wallet key")
    if not SYMBOL_RE.match(coin):
        return _json_error("Invalid coin")
    if not SYMBOL_RE.match(symbol):
        return _json_error("Invalid symbol")
    if not EXCHANGE_RE.match(exchange):
        return _json_error("Invalid exchange")
    if not INTERVAL_RE.match(interval):
        return _json_error("Invalid interval")

    client, addr, err = create_client(pk)
    if err:
        return _json_error(err)

    try:
        raw_liq = client.coinank.liquidation.liq_map(symbol=symbol, exchange=exchange, interval=interval)
        raw_heat = client.coinank.liquidation.heat_map(exchange=exchange, symbol=symbol, interval=interval)
        normalized = normalize_liquidation_map(raw_heat if raw_heat else raw_liq, symbol=symbol, exchange=exchange, interval=interval)
        if not normalized.get("has_data"):
            normalized = normalize_liquidation_map(raw_liq, symbol=symbol, exchange=exchange, interval=interval)
        snapshot = {
            "symbol": symbol,
            "coin": coin,
            "exchange": exchange,
            "interval": interval,
            "timestamp": time.time(),
            "epoch": time.time(),
            "price": _as_dict(agent_state.last_snapshot).get("price", 0),
            "liq_map": normalized,
            "heatmap": heatmap_manager.analyzer.analyze(normalized, float(_as_dict(agent_state.last_snapshot).get("price") or 0), symbol, agent_state.config.heatmap_bucket_pct, agent_state.config.min_heatmap_cluster_score, agent_state.config.max_heatmap_distance_pct, agent_state.config.allowed_heatmap_leverage_tiers),
            "cost_usdc": agent_state.config.liq_map_cost_usdc,
        }
        agent_state.record_heatmap_snapshot(snapshot, agent_state.config.max_heatmap_snapshots)
        return jsonify({"wallet": addr, "liq_map": normalized, "raw_liq_map": raw_liq, "raw_heat_map": raw_heat, "heatmap": snapshot["heatmap"]})
    except Claw402Error as exc:
        agent_state.add_event("error", f"Payment failed: {exc}", {"symbol": symbol, "exchange": exchange, "interval": interval}, level="error", module="claw402", action="liqmap")
        return jsonify({"wallet": addr, "error": f"Payment failed: {exc}"}), 402
    except Exception as exc:
        agent_state.add_event("error", str(exc), {"symbol": symbol, "exchange": exchange, "interval": interval}, level="error", module="claw402", action="liqmap")
        return jsonify({"wallet": addr, "error": str(exc)}), 500


@app.route("/api/agent/status")
def agent_status():
    return jsonify(agent_state.status())


@app.route("/api/agent/evolve", methods=["POST"])
def agent_evolve():
    payload = request.get_json() or {}
    lookback = int(payload.get("lookback") or 50)
    return jsonify({"evolution": evolution_engine.analyze(agent_state, agent_state.config, lookback=lookback)})


@app.route("/api/agent/evolve/apply", methods=["POST"])
def agent_evolve_apply():
    payload = request.get_json() or {}
    recommendations = payload.get("recommendations") or []
    selected = payload.get("selected") or []
    result = evolution_engine.apply(agent_state.config, recommendations, selected)
    if result["updates"]:
        config = agent_state.update_config(result["updates"])
        result["config"] = config.to_dict()
    return jsonify(result)


@app.route("/api/backtest/run", methods=["POST"])
def backtest_run():
    payload = request.get_json() or {}
    config = agent_state.config
    try:
        market = _as_dict(_as_dict(agent_state.last_snapshot).get("market"))
        klines = payload.get("klines") if isinstance(payload.get("klines"), list) else market.get("klines", [])
        result = run_simple_backtest(
            klines,
            symbol=str(payload.get("symbol") or config.symbol).upper().replace("/", ""),
            seed_usd=float(payload.get("seed_usd") or config.paper_seed_usd),
            notional_usd=float(payload.get("notional_usd") or config.notional_usd),
            leverage=int(payload.get("leverage") or config.leverage),
            stop_loss_pct=float(payload.get("stop_loss_pct") or config.stop_loss_pct / 100),
            take_profit_pct=float(payload.get("take_profit_pct") or config.take_profit_pct / 100),
        )
        agent_state.add_event("backtest", "backtest completed", result["summary"], module="backtest", action="run")
        return jsonify(result)
    except Exception as exc:
        agent_state.add_event("error", str(exc), {}, level="error", module="backtest", action="run")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/agent/chat", methods=["POST"])
def agent_chat():
    payload = request.get_json() or {}
    try:
        config = agent_state.config
        api_routes = [
            {"method": "POST", "path": "/api/wallet", "purpose": "connect wallet and derive address"},
            {"method": "POST", "path": "/api/liquidation", "purpose": "fetch basic liquidation and market data"},
            {"method": "POST", "path": "/api/liqmap", "purpose": "fetch one paid liquidation heatmap"},
            {"method": "POST", "path": "/api/agent/tick", "purpose": "run one strategy decision cycle"},
            {"method": "GET", "path": "/api/heatmap/snapshots", "purpose": "read stored heatmap snapshots"},
            {"method": "GET", "path": "/api/orders", "purpose": "read orders"},
            {"method": "GET", "path": "/api/events", "purpose": "read events"},
        ]
        context = {
            "api_routes": api_routes,
            "claw402_coinank_endpoints": CLAW402_COINANK_ENDPOINTS,
            "status": agent_state.status(),
            "last_snapshot": agent_state.last_snapshot,
            "last_signal": agent_state.last_signal,
            "last_risk": agent_state.last_risk,
            "last_llm_review": agent_state.last_llm_review,
            "orders": agent_state.orders[:10],
            "events": agent_state.events[:20],
            "heatmap": agent_state.heatmap_status(),
            "custom_base_url": payload.get("custom_base_url") or config.llm_base_url,
        }
        answer = chat_with_agent(
            provider_id=payload.get("provider") or config.llm_provider or "anthropic",
            api_key=(payload.get("api_key") or "").strip(),
            model=(payload.get("model") or config.llm_model or "").strip(),
            question=(payload.get("question") or "").strip(),
            context=context,
        )
        return jsonify({"answer": answer})
    except Exception as exc:
        return _json_error(str(exc))


@app.route("/api/agent/config", methods=["GET", "POST"])
def agent_config():
    if request.method == "GET":
        return jsonify({"config": agent_state.config.to_dict()})
    try:
        config = agent_state.update_config(request.get_json() or {})
        return jsonify({"config": config.to_dict()})
    except Exception as exc:
        return _json_error(str(exc))


@app.route("/api/agent/tick", methods=["POST"])
def agent_tick():
    payload = request.get_json() or {}
    result, status = run_agent_tick(payload)
    return jsonify(result), status


@app.route("/api/agent/start", methods=["POST"])
def agent_start():
    global worker_thread, worker_private_key
    payload = request.get_json() or {}
    private_key = _get_private_key(payload)
    if not private_key:
        return _json_error("Enter wallet key before starting agent")
    if worker_thread and worker_thread.is_alive():
        return jsonify(agent_state.status())
    worker_private_key = private_key
    worker_stop.clear()
    agent_state.set_running(True)
    worker_thread = threading.Thread(target=_worker_loop, daemon=True)
    worker_thread.start()
    return jsonify(agent_state.status())


@app.route("/api/agent/stop", methods=["POST"])
def agent_stop():
    global worker_private_key
    worker_stop.set()
    worker_private_key = ""
    agent_state.set_running(False)
    return jsonify(agent_state.status())


@app.route("/api/orders")
def orders():
    return jsonify({"orders": agent_state.orders})


@app.route("/api/events")
def events():
    return jsonify({"events": agent_state.events})


@app.route("/api/logs")
def logs():
    """Return recent logs: events + errors + phase changes."""
    events = agent_state.events[:100]
    return jsonify({"logs": events, "count": len(events)})


@app.route("/api/data/stats")
def data_stats():
    """Return data storage statistics."""
    from state import STATE_DIR, HEATMAP_SNAPSHOTS_PATH, ORDERS_PATH, EVENTS_PATH
    def _file_size(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    return jsonify({
        "orders": {"count": len(agent_state.orders), "size_bytes": _file_size(ORDERS_PATH)},
        "heatmap_snapshots": {"count": len(agent_state.heatmap_snapshots), "size_bytes": _file_size(HEATMAP_SNAPSHOTS_PATH)},
        "events": {"count": len(agent_state.events), "size_bytes": _file_size(EVENTS_PATH)},
        "data_dir": STATE_DIR,
    })


@app.route("/api/heatmap/snapshots")
def heatmap_snapshots():
    return jsonify({"heatmap": agent_state.heatmap_status(), "snapshots": agent_state.heatmap_snapshots})


def _worker_loop():
    consecutive_errors = 0
    max_consecutive_errors = 10
    while not worker_stop.is_set():
        try:
            run_agent_tick({"live_confirmed": False}, allow_worker_key=True)
            consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            agent_state.add_event("error", f"[{consecutive_errors}/{max_consecutive_errors}] {exc}")
            if consecutive_errors >= max_consecutive_errors:
                agent_state.add_event("error", "连续错误过多，Agent 自动停止")
                agent_state.set_running(False)
                break
        wait_seconds = max(10, agent_state.config.poll_seconds)
        if consecutive_errors > 0:
            wait_seconds = min(300, wait_seconds * (2 ** min(consecutive_errors, 5)))
        worker_stop.wait(wait_seconds)
    agent_state.set_running(False)


def run_agent_tick(payload: dict[str, Any] | None = None, *, allow_worker_key: bool = False) -> tuple[dict[str, Any], int]:
    payload = payload or {}
    wait_for_tick = bool(payload.get("manual") or payload.get("force_heatmap"))
    acquired = tick_lock.acquire(timeout=60) if wait_for_tick else tick_lock.acquire(blocking=False)
    if not acquired:
        return {"error": "agent tick already running", "phase": agent_state.agent_phase}, 409
    try:
        config = agent_state.config
        config_patch = dict(payload.get("config") or {})
        for key in ("coin", "symbol", "exchange", "interval"):
            if payload.get(key):
                config_patch[key] = payload.get(key)
        if config_patch:
            config = agent_state.update_config(config_patch)
        pk = _get_private_key(payload, allow_worker_key=allow_worker_key)
        if not pk:
            return {"error": "Enter wallet key"}, 400

        client, addr, err = create_client(pk)
        if err:
            return {"error": err}, 400

        agent_state.set_phase("SCANNING")
        market = market_data_service.fetch_snapshot(config.symbol, exchange=config.exchange, interval=config.interval)
        snapshot = fetch_market_snapshot(client, config.coin, config.symbol, config.exchange, config.interval, config.size, include_map=False)
        _apply_market_to_snapshot(snapshot, market)
        snapshot.wallet = addr
        agent_state.record_snapshot(snapshot)
        paper_executor.load_orders(agent_state.orders)
        paper_executor.mark_to_market(snapshot.symbol, snapshot.price)
        if paper_executor.broker.orders:
            agent_state.replace_orders(paper_executor.broker.orders)
        agent_state.add_event("market", "exchange market data loaded", {
            "source": market.get("source"),
            "symbol": config.symbol,
            "exchange": config.exchange,
            "price": snapshot.price,
        }, module="market_data", action="tick")

        force_heatmap = bool(payload.get("force_heatmap"))
        candidate = signal_engine.generate(snapshot, config, require_heatmap=False)
        liquidation_event = candidate.valid and candidate.action != "WAIT"
        if liquidation_event:
            agent_state.set_phase("LIQ_EVENT_DETECTED", {"side": candidate.side})
            force_heatmap = force_heatmap or config.event_triggered_liq_map

        heatmap_result = heatmap_manager.get_for_decision(client, agent_state, config, snapshot.price, force=force_heatmap)
        snapshot.liq_map = heatmap_result.get("liq_map") or {}
        if heatmap_result.get("heatmap"):
            snapshot.heatmap = heatmap_result["heatmap"]
        if heatmap_result.get("reason"):
            snapshot.data_warnings.append(heatmap_result["reason"])

        heatmap_age = heatmap_result.get("age_seconds")
        if snapshot.heatmap is not None:
            snapshot.heatmap["age_seconds"] = heatmap_age

        if config.use_heatmap_confirmation and not heatmap_result.get("usable"):
            agent_state.set_phase("HEATMAP_STALE", {"reason": heatmap_result.get("reason")})

        signal = signal_engine.generate(snapshot, config)
        agent_state.record_signal(signal)

        live_confirmed = bool(payload.get("live_confirmed"))
        decision = risk_manager.validate(signal, config, agent_state, live_confirmed=live_confirmed)
        llm_review = None
        if decision.approved and config.llm_review_enabled:
            agent_state.set_phase("LLM_REVIEWING")
            llm_review = review_trade_with_llm(
                payload.get("llm_review_provider") or config.llm_review_provider,
                (payload.get("llm_review_api_key") or "").strip(),
                (payload.get("llm_review_model") or config.llm_review_model or "").strip(),
                {
                    "signal": signal.to_dict(),
                    "risk": decision.to_dict(),
                    "snapshot": snapshot.to_dict(),
                    "status": agent_state.status(),
                    "config": config.to_dict(),
                    "custom_base_url": payload.get("llm_review_base_url"),
                },
            )
            if llm_review.get("enabled") and llm_review.get("confidence") is not None and float(llm_review.get("confidence", 1)) < config.llm_review_min_confidence:
                llm_review = {**llm_review, "decision": "wait", "reason": "LLM review confidence below threshold"}
            decision = apply_llm_review(decision, llm_review, config, signal)
            agent_state.record_llm_review(llm_review)
        agent_state.record_risk(decision)

        order = None
        if decision.approved:
            agent_state.set_phase("ORDER_OPEN")
            executor = paper_executor if config.mode == "paper" else binance_executor
            order = executor.execute(signal, decision, config)
            agent_state.record_order(order)
            if config.mode == "paper":
                agent_state.replace_orders(paper_executor.broker.orders)
        elif signal.valid:
            agent_state.set_phase("SIGNAL_REJECTED", {"reasons": decision.reasons})
        elif not liquidation_event:
            agent_state.set_phase("SCANNING")

        return {
            "snapshot": snapshot.to_dict(),
            "signal": signal.to_dict(),
            "risk": decision.to_dict(),
            "llm_review": llm_review,
            "order": order.to_dict() if order else None,
            "heatmap_result": {k: v for k, v in heatmap_result.items() if k != "snapshot"},
            "status": agent_state.status(),
        }, 200
    except Claw402Error as exc:
        agent_state.add_event("error", f"Payment failed: {exc}")
        return {"error": f"Payment failed: {exc}"}, 402
    except MarketDataRestrictedError as exc:
        agent_state.set_phase("ERROR", {"code": "restricted_location"})
        agent_state.add_event("error", str(exc), {"symbol": agent_state.config.symbol, "exchange": agent_state.config.exchange, "reason": "restricted_location"}, level="error", module="market_data", action="tick")
        return {"error": str(exc), "code": "restricted_location"}, 451
    except MarketDataError as exc:
        agent_state.set_phase("ERROR", {"code": "market_data_error"})
        agent_state.add_event("error", str(exc), {"symbol": agent_state.config.symbol, "exchange": agent_state.config.exchange}, level="error", module="market_data", action="tick")
        return {"error": str(exc), "code": "market_data_error"}, 502
    except Exception as exc:
        agent_state.add_event("error", str(exc))
        return {"error": str(exc)}, 500
    finally:
        tick_lock.release()


def _pick_port(preferred: int) -> int:
    import socket
    for candidate in (preferred, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", candidate))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError("no port available")


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = _pick_port(int(os.getenv("PORT", "47891")))
    print(f"LISTENING_ON_PORT={port}", flush=True)
    print(f"Liquidation Contrarian Agent — http://{host}:{port}", flush=True)
    sys.stdout.flush()
    app.run(host=host, port=port, debug=False, use_reloader=False)

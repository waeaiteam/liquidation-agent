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
from services.decision import build_decision_report, opportunity_score
from services.evolution import EvolutionEngine
from services.heatmap_manager import HeatmapSnapshotManager
from services.liquidation_maps import normalize_liquidation_map
from services.llm import _as_dict, analyze_with_llm
from services.market_data import MarketDataError, MarketDataRestrictedError, compute_volatility, market_data_service
from services.paper_trading import SharedPaperTrading
from services.potential_analyzer import PotentialAnalyzer, analysis_allows_entry
from services.potential_scanner import PotentialScanner, PotentialStore
from services.pub_agent import PubAgent
from services.square_publisher import SquarePublisher
from services.strategy_agent import apply_llm_review, review_trade_with_llm
from services.x_sentiment import get_service as get_x_service
from services.xai_chat import get_chat_service as get_xai_chat
from services.x_pipeline import get_pipeline_service as get_x_pipeline
from services.x_poster import get_poster_service as get_x_poster
from state import AgentState
from strategy.models import MarketSnapshot, RiskDecision, StrategyConfig, utc_now
from strategy.signals import LiquidationContrarianSignalEngine
from trading.execution import BinanceExecutionAdapter, PaperExecutionAdapter
from trading.position_manager import DynamicPositionRules, atr_from_klines
from trading.risk import RiskManager
from evolution.evolve import PromptEvolutionEngine

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
potential_store = PotentialStore()
potential_scanner = PotentialScanner(potential_store)
potential_analyzer = PotentialAnalyzer(potential_store)
pub_agent = PubAgent(potential_store)
square_publisher = SquarePublisher(potential_store)
shared_paper = SharedPaperTrading(potential_store)
prompt_evolution = PromptEvolutionEngine(potential_store)
position_rules = DynamicPositionRules()
worker_stop = threading.Event()
worker_thread: threading.Thread | None = None
worker_private_key = ""
worker_llm_review_key = ""
tick_lock = threading.Lock()
scanner_stop = threading.Event()
scanner_thread: threading.Thread | None = None
scanner_lock = threading.Lock()

SYMBOL_RE = re.compile(r"^[A-Z0-9]{3,20}$")
INTERVAL_RE = re.compile(r"^[0-9]+[mhdwM]$")
EXCHANGE_RE = re.compile(r"^[a-z0-9_-]{2,32}$")


@app.route("/js/<path:filename>")
def js_files(filename):
    return send_from_directory(os.path.join(BASE_DIR, "templates"), filename)


@app.route("/favicon.ico")
def favicon():
    return "", 204


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


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


@app.route("/api/market/binance-symbols")
def market_binance_symbols():
    try:
        return jsonify({"symbols": market_data_service.list_binance_usdt_perpetual_symbols()})
    except MarketDataError as exc:
        return jsonify({"symbols": [], "error": str(exc)}), 502


@app.route("/api/market/sentiment")
def market_sentiment():
    try:
        return jsonify(market_data_service.fear_greed())
    except Exception as exc:
        return jsonify({"error": str(exc), "source": "alternative.me"}), 502


@app.route("/api/market/volatility")
def market_volatility():
    snapshot = _as_dict(agent_state.last_snapshot)
    if not snapshot:
        return jsonify({"coins": [], "error": "No real exchange market data loaded yet"}), 404
    market = _as_dict(snapshot.get("market"))
    volatility = compute_volatility(market.get("klines") if isinstance(market.get("klines"), list) else [])
    return jsonify({"coins": [{"symbol": snapshot.get("symbol"), "value": volatility, "source": "exchange_klines"}]})


@app.route("/api/market/sectors")
def market_sectors():
    try:
        return jsonify({"sectors": market_data_service.sectors()})
    except Exception as exc:
        return jsonify({"sectors": [], "error": str(exc), "source": "coingecko"}), 502


@app.route("/api/market/flows")
def market_flows():
    try:
        global_data = market_data_service.global_market()
        eth = market_data_service.fetch_snapshot("ETHUSDT", exchange="binance", interval="1h", limit=24)
        return jsonify({"global": global_data, "eth": eth, "source": "coingecko+binance"})
    except Exception as exc:
        return jsonify({"flows": [], "error": str(exc)}), 502


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
    interval = str(payload.get("interval", "1d")).strip().lower()

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
        base_cost = agent_state.config.liq_map_cost_usdc
        if not agent_state.can_spend_api_budget(base_cost, agent_state.config.daily_api_budget_usdc):
            return jsonify({"wallet": addr, "error": "Daily Claw402 liquidation map budget exhausted"}), 429
        raw_agg = client.coinank.liquidation.agg_liq_map(base_coin=coin, interval=interval)
        agent_state.record_claw402_raw_sample("agg_liq_map", symbol, exchange, interval, raw_agg)
        normalized = normalize_liquidation_map(raw_agg, symbol=symbol, exchange=exchange, interval=interval)
        normalized["scope"] = "aggregate"
        raw_agg_data = _as_dict(unwrap_data(raw_agg, {}))
        actual_cost = base_cost
        last_snapshot = _as_dict(agent_state.last_snapshot)
        price = (
            _to_float(raw_agg_data.get("lastPrice"))
            or _to_float(last_snapshot.get("price"))
            or 0.0
        )
        analysis = heatmap_manager.analyzer.analyze(
            normalized,
            price,
            symbol,
            agent_state.config.heatmap_bucket_pct,
            agent_state.config.min_heatmap_cluster_score,
            agent_state.config.max_heatmap_distance_pct,
            agent_state.config.allowed_heatmap_leverage_tiers,
        )
        snapshot = {
            "symbol": symbol,
            "coin": coin,
            "exchange": exchange,
            "map_scope": "aggregate",
            "interval": interval,
            "timestamp": utc_now(),
            "epoch": time.time(),
            "price": price,
            "liq_map": normalized,
            "heatmap": analysis,
            "cost_usdc": actual_cost,
        }
        agent_state.record_api_cost("liq_map", actual_cost, symbol)
        agent_state.record_api_cost("liq_map_manual", actual_cost, symbol)
        agent_state.record_heatmap_snapshot(snapshot, agent_state.config.max_heatmap_snapshots)
        agent_state.add_event(
            "heatmap",
            "manual liquidation map refreshed",
            {
                "symbol": symbol,
                "exchange": exchange,
                "map_scope": "aggregate",
                "interval": interval,
                "source": normalized.get("source"),
                "shape": normalized.get("shape"),
                "cost_usdc": actual_cost,
            },
            module="claw402",
            action="liqmap",
        )
        return jsonify({
            "wallet": addr,
            "liq_map": normalized,
            "raw_agg_liq_map": raw_agg,
            "heatmap": snapshot["heatmap"],
            "snapshot": snapshot,
            "status": agent_state.status(),
        })
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
            {"method": "POST", "path": "/api/liqmap", "purpose": "fetch one paid CoinAnk liquidation map"},
            {"method": "POST", "path": "/api/agent/tick", "purpose": "run one strategy decision cycle"},
            {"method": "GET", "path": "/api/heatmap/snapshots", "purpose": "read stored liquidation-map snapshots"},
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


@app.route("/api/agents/config", methods=["GET", "POST"])
def multi_agent_config():
    if request.method == "GET":
        return jsonify({
            "agents": potential_store.all_agent_configs(redact=True),
            "scanner": potential_store.get_scanner_config().to_dict(),
            "trading": {
                "mode": agent_state.config.safety_mode,
                "live_enabled": agent_state.config.live_enabled,
                "paper_seed_usd": agent_state.config.paper_seed_usd,
            },
        })
    payload = request.get_json() or {}
    agents = potential_store.update_agent_configs(payload.get("agents") or {})
    scanner_cfg = potential_store.update_scanner_config(payload.get("scanner") or {}) if payload.get("scanner") else potential_store.get_scanner_config().to_dict()
    trading_patch = payload.get("trading") or {}
    if trading_patch:
        patch = {}
        if trading_patch.get("mode"):
            patch["safety_mode"] = trading_patch.get("mode")
        if "live_enabled" in trading_patch:
            patch["live_enabled"] = bool(trading_patch.get("live_enabled"))
        agent_state.update_config(patch)
    return jsonify({"agents": agents, "scanner": scanner_cfg, "trading": {"mode": agent_state.config.safety_mode, "live_enabled": agent_state.config.live_enabled}})


@app.route("/api/scanner/status")
def scanner_status():
    status = potential_scanner.status()
    agent_state.record_scanner_status(status)
    return jsonify(status)


@app.route("/api/scanner/start", methods=["POST"])
def scanner_start():
    global scanner_thread
    if scanner_thread and scanner_thread.is_alive():
        return jsonify(potential_scanner.status())
    scanner_stop.clear()
    potential_scanner.set_running(True)
    scanner_thread = threading.Thread(target=_scanner_loop, daemon=True)
    scanner_thread.start()
    agent_state.add_event("scanner", "started", module="pot", action="scanner.start")
    status = potential_scanner.status()
    agent_state.record_scanner_status(status)
    return jsonify(status)


@app.route("/api/scanner/stop", methods=["POST"])
def scanner_stop_route():
    scanner_stop.set()
    potential_scanner.set_running(False)
    agent_state.add_event("scanner", "stopped", module="pot", action="scanner.stop")
    status = potential_scanner.status()
    agent_state.record_scanner_status(status)
    return jsonify(status)


@app.route("/api/scanner/run", methods=["POST"])
def scanner_run():
    result, status = run_scanner_tick(manual=True)
    return jsonify(result), status


@app.route("/api/scanner/watchlist")
def scanner_watchlist():
    status = request.args.get("status")
    return jsonify({"watchlist": potential_store.list_watchlist(status=status, limit=500), "status": potential_scanner.status()})


@app.route("/api/scanner/watchlist/<symbol>", methods=["GET", "POST", "DELETE"])
def scanner_watchlist_symbol(symbol: str):
    symbol = str(symbol or "").upper().replace("/", "")
    if symbol and not symbol.endswith("USDT"):
        symbol += "USDT"
    if request.method == "GET":
        item = potential_store.get_watchlist_item(symbol)
        if not item:
            return _json_error("watchlist item not found", 404)
        return jsonify({"item": item, "ticks": potential_store.watchlist_ticks(symbol, 100)})
    if request.method == "DELETE":
        potential_store.remove_watchlist_item(symbol, "manual")
        return jsonify({"removed": True, "symbol": symbol})
    try:
        ticker = (potential_scanner.client.tickers_24hr().get(symbol) or {})
        premium = {item.get("symbol"): item for item in potential_scanner.client.premium_index()}
        fr = float((premium.get(symbol) or {}).get("lastFundingRate") or 0)
        price = float(ticker.get("lastPrice") or 0)
        volume = float(ticker.get("quoteVolume") or 0)
        hist = potential_scanner.client.open_interest_hist(symbol, period="1h", limit=2)
        oi_values = []
        for row in hist:
            raw = row.get("sumOpenInterestValue") if row.get("sumOpenInterestValue") is not None else row.get("sumOpenInterest")
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                oi_values.append(value)
        item = potential_store.upsert_watchlist_item(symbol, fr=fr, oi=oi_values[-1] if oi_values else 0, volume=volume, price=price)
        return jsonify({"item": item})
    except Exception as exc:
        return _json_error(str(exc), 400)


@app.route("/api/scanner/signals")
def scanner_signals():
    limit, _ = _parse_size(request.args.get("limit", 100), 1, 500)
    return jsonify({"signals": potential_store.list_signals(limit or 100, request.args.get("status")), "status": potential_scanner.status()})


@app.route("/api/scanner/signals/<int:signal_id>")
def scanner_signal_detail(signal_id: int):
    signal = potential_store.get_signal(signal_id)
    if not signal:
        return _json_error("signal not found", 404)
    return jsonify({"signal": signal})


@app.route("/api/scanner/signals/<int:signal_id>/analyze", methods=["POST"])
def scanner_analyze_signal(signal_id: int):
    try:
        payload = request.get_json() or {}
        analysis = potential_analyzer.analyze_signal(signal_id, user_prompt=str(payload.get("prompt") or ""))
        signal = potential_store.get_signal(signal_id)
        if signal:
            status = "trading" if shared_paper.open_orders("pot") and any(str(o.get("symbol") or "").upper() == str(signal.get("symbol") or "").upper() for o in shared_paper.open_orders("pot")) else "analyzing"
            potential_store.mark_watchlist_status(str(signal.get("symbol") or ""), status, ai_analysis=analysis, signal_id=signal_id)
        return jsonify({"analysis": analysis, "signal": potential_store.get_signal(signal_id)})
    except Exception as exc:
        return _json_error(str(exc), 400)


@app.route("/api/scanner/signals/<int:signal_id>/paper-entry", methods=["POST"])
def scanner_signal_paper_entry(signal_id: int):
    try:
        signal = potential_store.get_signal(signal_id)
        if not signal:
            return _json_error("signal not found", 404)
        analysis = signal.get("ai_analysis") if isinstance(signal.get("ai_analysis"), dict) else None
        order = _open_pot_paper_position(signal, analysis, manual=True)
        if not order:
            return _json_error("paper entry was not opened; check price, position limits, or entry confirmation mode", 400)
        potential_store.mark_watchlist_status(str(signal.get("symbol") or ""), "trading", ai_analysis=analysis, signal_id=signal_id)
        return jsonify({"order": order, "positions": shared_paper.open_orders("pot")})
    except Exception as exc:
        return _json_error(str(exc), 400)


@app.route("/api/scanner/positions")
def scanner_positions():
    return jsonify({"positions": shared_paper.open_orders("pot")})


@app.route("/api/scanner/positions/<int:position_id>/review", methods=["POST"])
def scanner_review_position(position_id: int):
    try:
        position = shared_paper.get_order(position_id)
        payload = request.get_json() or {}
        review = potential_analyzer.review_position(position, payload.get("market_context") or {})
        if review.get("action") == "close":
            price = float((payload.get("market_context") or {}).get("price") or position.get("entry_price") or 0)
            shared_paper.close_order(position_id, price, "ai_decision", reduce_pct=100)
        elif review.get("action") == "reduce":
            price = float((payload.get("market_context") or {}).get("price") or position.get("entry_price") or 0)
            shared_paper.close_order(position_id, price, "ai_reduce", reduce_pct=float(review.get("reduce_pct") or 50))
        return jsonify({"review": review, "position": shared_paper.get_order(position_id)})
    except Exception as exc:
        return _json_error(str(exc), 400)


@app.route("/api/publisher/generate/<int:signal_id>", methods=["POST"])
def publisher_generate(signal_id: int):
    try:
        payload = request.get_json() or {}
        result = pub_agent.generate_for_signal(signal_id, mode=str(payload.get("mode") or "draft"))
        return jsonify(result)
    except Exception as exc:
        return _json_error(str(exc), 400)


@app.route("/api/publisher/drafts")
def publisher_drafts():
    return jsonify({"drafts": potential_store.list_drafts()})


@app.route("/api/publisher/drafts/<int:draft_id>/publish", methods=["POST"])
def publisher_publish(draft_id: int):
    try:
        payload = request.get_json() or {}
        cfg = potential_store.get_scanner_config()
        api_key = str(payload.get("square_api_key") or cfg.square_api_key or "").strip()
        if payload.get("square_api_key"):
            potential_store.update_scanner_config({"square_api_key": api_key})
        result = square_publisher.publish(draft_id, api_key, force=bool(payload.get("force")))
        return jsonify(result), 200 if result.get("published") else 400
    except Exception as exc:
        return _json_error(str(exc), 400)


@app.route("/api/publisher/history")
def publisher_history():
    return jsonify({"history": potential_store.list_drafts(200)})


@app.route("/api/trading/config", methods=["GET", "POST"])
def trading_config():
    if request.method == "GET":
        return jsonify({"mode": agent_state.config.safety_mode, "live_enabled": agent_state.config.live_enabled})
    payload = request.get_json() or {}
    patch = {}
    if payload.get("mode"):
        patch["safety_mode"] = payload.get("mode")
    if "live_enabled" in payload:
        patch["live_enabled"] = bool(payload.get("live_enabled"))
    config = agent_state.update_config(patch)
    return jsonify({"mode": config.safety_mode, "live_enabled": config.live_enabled})


@app.route("/api/trading/paper/orders")
def trading_paper_orders():
    return jsonify({"orders": shared_paper.list_orders(request.args.get("agent_type"), 500)})


@app.route("/api/trading/paper/stats")
def trading_paper_stats():
    return jsonify({"stats": shared_paper.stats(request.args.get("agent_type"))})


@app.route("/api/trading/paper/equity-curve")
def trading_paper_equity():
    return jsonify({"curve": shared_paper.equity_curve(request.args.get("agent_type"))})


@app.route("/api/evolution/trigger/<agent_type>", methods=["POST"])
def evolution_trigger(agent_type: str):
    try:
        payload = request.get_json() or {}
        result = prompt_evolution.trigger(agent_type, trigger=str(payload.get("trigger") or "manual"))
        return jsonify({"evolution": result})
    except Exception as exc:
        return _json_error(str(exc), 400)


@app.route("/api/evolution/log/<agent_type>")
def evolution_log(agent_type: str):
    try:
        return jsonify({"logs": prompt_evolution.logs(agent_type)})
    except Exception as exc:
        return _json_error(str(exc), 400)


@app.route("/api/evolution/current/<agent_type>")
def evolution_current(agent_type: str):
    try:
        return jsonify({"current": prompt_evolution.current(agent_type)})
    except Exception as exc:
        return _json_error(str(exc), 400)


@app.route("/api/agent/tick", methods=["POST"])
def agent_tick():
    payload = request.get_json() or {}
    result, status = run_agent_tick(payload)
    return jsonify(result), status


@app.route("/api/agent/start", methods=["POST"])
def agent_start():
    global worker_thread, worker_private_key, worker_llm_review_key
    payload = request.get_json() or {}
    private_key = _get_private_key(payload)
    if not private_key:
        return _json_error("Enter wallet key before starting agent")
    if worker_thread and worker_thread.is_alive():
        return jsonify(agent_state.status())
    worker_private_key = private_key
    worker_llm_review_key = str(payload.get("llm_review_api_key") or "").strip()
    worker_stop.clear()
    agent_state.set_running(True)
    worker_thread = threading.Thread(target=_worker_loop, daemon=True)
    worker_thread.start()
    return jsonify(agent_state.status())


@app.route("/api/agent/stop", methods=["POST"])
def agent_stop():
    global worker_private_key, worker_llm_review_key
    worker_stop.set()
    worker_private_key = ""
    worker_llm_review_key = ""
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
    from state import STATE_DIR, HEATMAP_SNAPSHOTS_PATH, ORDERS_PATH, EVENTS_PATH, DECISION_REPORTS_PATH, CLAW402_RAW_SAMPLES_PATH
    def _file_size(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    return jsonify({
        "orders": {"count": len(agent_state.orders), "size_bytes": _file_size(ORDERS_PATH)},
        "heatmap_snapshots": {"count": len(agent_state.heatmap_snapshots), "size_bytes": _file_size(HEATMAP_SNAPSHOTS_PATH)},
        "events": {"count": len(agent_state.events), "size_bytes": _file_size(EVENTS_PATH)},
        "decision_reports": {"count": len(agent_state.decision_reports), "size_bytes": _file_size(DECISION_REPORTS_PATH)},
        "claw402_raw_samples": {"count": len(agent_state.claw402_raw_samples), "size_bytes": _file_size(CLAW402_RAW_SAMPLES_PATH)},
        "data_dir": STATE_DIR,
    })


@app.route("/api/heatmap/snapshots")
def heatmap_snapshots():
    return jsonify({"heatmap": agent_state.heatmap_status(), "snapshots": agent_state.heatmap_snapshots})


@app.route("/api/debug/fixture/heatmap", methods=["POST"])
def debug_fixture_heatmap():
    if os.getenv("ENABLE_DEBUG_FIXTURES") != "1":
        return _json_error("debug fixtures disabled", 404)
    payload = request.get_json() or {}
    raw = payload.get("raw")
    if raw is None:
        return _json_error("raw fixture required")
    symbol = str(payload.get("symbol") or agent_state.config.symbol).upper().replace("/", "")
    exchange = str(payload.get("exchange") or agent_state.config.exchange).lower()
    interval = str(payload.get("interval") or agent_state.config.interval)
    normalized = normalize_liquidation_map(raw, symbol=symbol, exchange=exchange, interval=interval)
    price = float(payload.get("price") or _as_dict(agent_state.last_snapshot).get("price") or 0)
    analysis = heatmap_manager.analyzer.analyze(
        normalized,
        price,
        symbol,
        agent_state.config.heatmap_bucket_pct,
        agent_state.config.min_heatmap_cluster_score,
        agent_state.config.max_heatmap_distance_pct,
        agent_state.config.allowed_heatmap_leverage_tiers,
    )
    snapshot = {
        "symbol": symbol,
        "coin": symbol.replace("USDT", ""),
        "exchange": exchange,
        "interval": interval,
        "timestamp": utc_now(),
        "epoch": time.time(),
        "price": price,
        "liq_map": normalized,
        "heatmap": analysis,
        "cost_usdc": 0,
    }
    agent_state.record_heatmap_snapshot(snapshot, agent_state.config.max_heatmap_snapshots)
    return jsonify({"liq_map": normalized, "heatmap": analysis, "status": agent_state.status()})


@app.route("/api/agent/reports")
def agent_reports():
    limit = max(1, min(int(request.args.get("limit", 20)), 100))
    return jsonify({"reports": agent_state.decision_reports[:limit], "last": agent_state.last_decision_report})


@app.route("/api/exchange/status")
def exchange_status():
    cfg = agent_state.config
    result = {
        "selected": cfg.exchange,
        "exchanges": {
            "binance": {"configured": bool(os.getenv("BINANCE_API_KEY") and os.getenv("BINANCE_API_SECRET")), "uses_public_market_data": True, "restricted": False, "message": "public futures market data"},
            "okx": {"configured": bool(os.getenv("OKX_API_KEY") and os.getenv("OKX_API_SECRET")), "uses_public_market_data": True, "restricted": False, "message": "public swap market data"},
            "bybit": {"configured": bool(os.getenv("BYBIT_API_KEY") and os.getenv("BYBIT_API_SECRET")), "uses_public_market_data": True, "restricted": False, "message": "public linear market data"},
        },
    }
    for exchange in result["exchanges"]:
        try:
            market_data_service.fetch_snapshot(cfg.symbol, exchange=exchange, interval=cfg.interval, limit=2)
            result["exchanges"][exchange]["ok"] = True
        except MarketDataRestrictedError as exc:
            result["exchanges"][exchange].update({"ok": False, "restricted": True, "message": str(exc)})
        except Exception as exc:
            result["exchanges"][exchange].update({"ok": False, "message": str(exc)})
    return jsonify(result)


@app.route("/api/agent/diagnostics")
def agent_diagnostics():
    checks = []
    cfg = agent_state.config

    def add(name, status, message, data=None):
        checks.append({"name": name, "status": status, "message": message, "data": data or {}})

    add("wallet", "pass" if bool(worker_private_key or os.getenv("CLAW402_PRIVATE_KEY")) else "warn", "worker/env wallet available" if bool(worker_private_key or os.getenv("CLAW402_PRIVATE_KEY")) else "wallet only checked when provided by frontend")
    try:
        market = market_data_service.fetch_snapshot(cfg.symbol, exchange=cfg.exchange, interval=cfg.interval, limit=2)
        add("market_data", "pass", f"{cfg.exchange} public market data ok", {"price": market.get("price"), "source": market.get("source")})
    except MarketDataRestrictedError as exc:
        add("market_data", "fail", str(exc), {"code": "restricted_location"})
    except Exception as exc:
        add("market_data", "fail", str(exc))

    latest_heatmap = agent_state.latest_heatmap_snapshot(cfg.symbol, cfg.interval)
    heatmap_age = agent_state.heatmap_snapshot_age_seconds(latest_heatmap)
    add("heatmap_cache", "pass" if latest_heatmap and heatmap_age <= cfg.max_heatmap_snapshot_age_seconds else "warn", "fresh heatmap snapshot available" if latest_heatmap and heatmap_age <= cfg.max_heatmap_snapshot_age_seconds else "no fresh heatmap snapshot cached", {"age_seconds": None if not latest_heatmap else round(heatmap_age, 1)})
    add("llm_review", "pass" if not cfg.llm_review_enabled or cfg.llm_review_model else "warn", "LLM review disabled or configured")
    add("x_service", "pass" if get_x_service().is_configured() else "warn", "xAI configured" if get_x_service().is_configured() else "xAI not configured")
    try:
        from state import STATE_DIR
        os.makedirs(STATE_DIR, exist_ok=True)
        test_path = os.path.join(STATE_DIR, ".diagnostic_write_test")
        with open(test_path, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(test_path)
        add("data_dir", "pass", "data directory writable", {"path": STATE_DIR})
    except Exception as exc:
        add("data_dir", "fail", str(exc))
    heartbeat = agent_state.status().get("heartbeat", {})
    add("worker_heartbeat", "warn" if heartbeat.get("stale") else "pass", "heartbeat stale" if heartbeat.get("stale") else "heartbeat ok", heartbeat)
    overall = "fail" if any(c["status"] == "fail" for c in checks) else "warn" if any(c["status"] == "warn" for c in checks) else "pass"
    summary = {"overall": overall, "checks": checks, "checked_at": time.time()}
    agent_state.record_diagnostics_summary({"overall": overall, "checked_at": summary["checked_at"]})
    return jsonify(summary)


@app.route("/api/agent/replay", methods=["POST"])
def agent_replay():
    payload = request.get_json() or {}
    cfg = StrategyConfig.from_dict({**agent_state.config.to_dict(), **(payload.get("config") or {}), "safety_mode": "observe"})
    klines = payload.get("klines") if isinstance(payload.get("klines"), list) else []
    snapshots = payload.get("snapshots") if isinstance(payload.get("snapshots"), list) else []
    heatmaps = payload.get("heatmap_snapshots") if isinstance(payload.get("heatmap_snapshots"), list) else []
    if not snapshots and klines:
        snapshots = [
            {
                "price": item.get("close"),
                "market": {"klines": klines[: idx + 1], "source": "replay"},
                "intervals": item.get("intervals") or {},
            }
            for idx, item in enumerate(klines)
        ]
    reports = []
    for idx, raw in enumerate(snapshots[:200]):
        snap = MarketSnapshot(
            coin=cfg.coin,
            symbol=str(raw.get("symbol") or cfg.symbol).upper(),
            exchange=str(raw.get("exchange") or cfg.exchange).lower(),
            interval=str(raw.get("interval") or cfg.interval),
            price=float(raw.get("price") or 0),
            intervals=raw.get("intervals") if isinstance(raw.get("intervals"), dict) else {},
            history=raw.get("history") if isinstance(raw.get("history"), list) else [],
            oi=raw.get("oi") if isinstance(raw.get("oi"), list) else [],
            long_short=raw.get("long_short") if isinstance(raw.get("long_short"), list) else [],
            funding=raw.get("funding") if isinstance(raw.get("funding"), list) else [],
            liq_map=(heatmaps[idx].get("liq_map") if idx < len(heatmaps) and isinstance(heatmaps[idx], dict) else raw.get("liq_map") or {}),
            market=raw.get("market") if isinstance(raw.get("market"), dict) else {},
        )
        signal = signal_engine.generate(snap, cfg)
        risk = risk_manager.validate(signal, cfg, agent_state, live_confirmed=False)
        score = opportunity_score(signal, {"usable": bool(snap.liq_map), "age_seconds": 0}, cfg, risk)
        blockers = []
        if not signal.valid:
            blockers.extend(signal.reasons)
        if not score["passed"]:
            blockers.append(f"opportunity score {score['score']} below {score['threshold']}")
        if not risk.approved:
            blockers.extend(risk.reasons)
        reports.append(build_decision_report(
            config=cfg,
            snapshot=snap,
            candidate=signal,
            signal=signal,
            heatmap_result={"usable": bool(snap.liq_map), "reason": "replay", "age_seconds": 0},
            risk=risk,
            llm_review=None,
            order=None,
            score=score,
            final_action="REPLAY_APPROVED" if signal.valid and risk.approved and score["passed"] else "REPLAY_WAIT",
            blockers=blockers,
            tick_count=idx + 1,
        ))
    return jsonify({"reports": reports, "count": len(reports)})


def _worker_loop():
    consecutive_errors = 0
    max_consecutive_errors = 10
    while not worker_stop.is_set():
        result, status = {}, 500
        try:
            payload = {
                "live_confirmed": False,
                "llm_review_api_key": worker_llm_review_key,
            }
            result, status = run_agent_tick(payload, allow_worker_key=True)
            if status >= 400:
                raise RuntimeError(result.get("error") or f"agent tick failed with HTTP {status}")
            consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            agent_state.add_event("error", f"[{consecutive_errors}/{max_consecutive_errors}] {exc}", module="agent", action="worker_loop")
            if consecutive_errors >= max_consecutive_errors:
                agent_state.add_event("error", "连续错误过多，Agent 自动停止")
                agent_state.set_running(False)
                break
        wait_seconds = max(10, agent_state.config.poll_seconds)
        if consecutive_errors > 0:
            wait_seconds = min(300, wait_seconds * (2 ** min(consecutive_errors, 5)))
        agent_state.set_next_tick_due(wait_seconds)
        worker_stop.wait(wait_seconds)
    agent_state.set_running(False)


def _scanner_loop():
    consecutive_errors = 0
    max_consecutive_errors = 10
    while not scanner_stop.is_set():
        try:
            result, status = run_scanner_tick(manual=False)
            if status >= 400:
                raise RuntimeError(result.get("error") or f"scanner tick failed with HTTP {status}")
            consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            agent_state.add_event("error", f"POT scanner [{consecutive_errors}/{max_consecutive_errors}] {exc}", module="pot", action="scanner.loop")
            if consecutive_errors >= max_consecutive_errors:
                potential_scanner.set_running(False)
                agent_state.add_event("error", "POT scanner stopped after repeated failures", module="pot", action="scanner.loop")
                break
        cfg = potential_store.get_scanner_config()
        wait_seconds = max(10, cfg.poll_seconds)
        if consecutive_errors:
            wait_seconds = min(300, wait_seconds * (2 ** min(consecutive_errors, 5)))
        potential_scanner.set_next_run(wait_seconds)
        scanner_stop.wait(wait_seconds)
    potential_scanner.set_running(False)


def run_scanner_tick(*, manual: bool = False) -> tuple[dict[str, Any], int]:
    if not scanner_lock.acquire(blocking=False):
        return {"error": "scanner tick already running", "status": potential_scanner.status()}, 409
    try:
        result = potential_scanner.scan_once(manual=manual)
        _check_pot_positions(result)
        _auto_process_pot_signals(result)
        agent_state.add_event("scanner", result.get("status", "ok"), {
            "checked": result.get("checked"),
            "inserted": result.get("inserted"),
            "watch_candidates": result.get("watch_candidates"),
            "watchlist_updates": result.get("watchlist_updates"),
        }, module="pot", action="scanner.tick")
        status = potential_scanner.status()
        agent_state.record_scanner_status(status)
        return {"result": result, "status": status}, 200
    except Exception as exc:
        agent_state.add_event("error", str(exc), module="pot", action="scanner.tick")
        status = potential_scanner.status()
        agent_state.record_scanner_status(status)
        return {"error": str(exc), "status": status}, 502
    finally:
        scanner_lock.release()


def _auto_process_pot_signals(scan_result: dict[str, Any]) -> None:
    cfg = potential_store.get_scanner_config()
    for signal in scan_result.get("signals") or []:
        signal_id = signal.get("id")
        if not signal_id:
            continue
        analysis = None
        if cfg.auto_analyze and float(signal.get("potential_score") or 0) >= cfg.min_score_for_analysis:
            try:
                analysis = potential_analyzer.analyze_signal(int(signal_id))
            except Exception as exc:
                agent_state.add_event("error", f"POT analysis failed for {signal.get('symbol')}: {exc}", module="pot", action="analysis.auto")
        if cfg.auto_trade and analysis and analysis_allows_entry(analysis) and agent_state.config.safety_mode == "paper":
            order = _open_pot_paper_position(signal, analysis)
            if order:
                potential_store.mark_watchlist_status(str(signal.get("symbol") or ""), "trading", ai_analysis=analysis)


def _open_pot_paper_position(signal: dict[str, Any], analysis: dict[str, Any] | None = None, *, manual: bool = False) -> dict[str, Any] | None:
    cfg = potential_store.get_scanner_config()
    open_count = len(shared_paper.open_orders("pot"))
    if open_count >= cfg.max_concurrent_positions:
        agent_state.add_event(
            "risk",
            "POT max concurrent positions reached",
            {"open_count": open_count, "limit": cfg.max_concurrent_positions, "symbol": signal.get("symbol")},
            module="pot",
            action="trade.paper",
        )
        return None
    if not manual and cfg.entry_confirmation_mode != "aggressive":
        agent_state.add_event(
            "scanner",
            "POT entry confirmation required",
            {"mode": cfg.entry_confirmation_mode, "symbol": signal.get("symbol")},
            module="pot",
            action="trade.paper",
        )
        return None
    symbol = str(signal.get("symbol") or "").upper()
    price = float(signal.get("price") or 0)
    if not symbol or price <= 0:
        return None
    try:
        klines = potential_scanner.client.klines(symbol, interval="1h", limit=60)
        atr = atr_from_klines(klines, period=14)
    except Exception:
        atr = 0
    stop = position_rules.initial_stop(price, atr, side="long")
    order = shared_paper.open_order(
        agent_type="pot",
        symbol=symbol,
        side="long",
        entry_price=price,
        notional_usdt=cfg.trading_notional_usdt,
        stop_price=stop,
        signal_id=int(signal.get("id") or 0) or None,
        trajectory_id=str(signal.get("id") or ""),
        slippage_pct=cfg.paper_slippage_pct,
    )
    agent_state.add_event(
        "order",
        "POT paper entry opened",
        {"symbol": symbol, "requested_entry": price, "entry": order.get("entry_price"), "stop": stop, "slippage_pct": cfg.paper_slippage_pct},
        module="pot",
        action="trade.paper",
    )
    return order


def _check_pot_positions(scan_result: dict[str, Any] | None = None) -> None:
    positions = shared_paper.open_orders("pot")
    if not positions:
        return
    tickers = {}
    premium_items = {}
    try:
        tickers = potential_scanner.client.tickers_24hr()
    except Exception as exc:
        agent_state.add_event("error", f"POT ticker load failed during position check: {exc}", module="pot", action="position.check")
    try:
        premium_items = {item.get("symbol"): item for item in potential_scanner.client.premium_index()}
    except Exception as exc:
        agent_state.add_event("error", f"POT premiumIndex load failed during position check: {exc}", module="pot", action="position.check")
    for position in positions:
        symbol = str(position.get("symbol") or "").upper()
        try:
            ticker = tickers.get(symbol) or {}
            price = float(ticker.get("lastPrice") or position.get("entry_price") or 0)
            klines = potential_scanner.client.klines(symbol, interval="1h", limit=60)
            atr = atr_from_klines(klines, period=14)
            new_stop = position_rules.trailing_stop(
                price,
                atr,
                float(position.get("trailing_stop") or position.get("stop_price") or 0),
                side="long",
                entry_price=float(position.get("entry_price") or 0),
            )
            updated = shared_paper.update_mark(int(position["id"]), price, trailing_stop=new_stop)
            oi_declining = _pot_oi_declining(symbol)
            context = {
                "price": price,
                "fr_current": float((premium_items.get(symbol) or {}).get("lastFundingRate") or -1),
                "volume_24h": float(ticker.get("quoteVolume") or 0),
                "oi_declining": oi_declining,
            }
            reason = position_rules.exit_reason(updated, context)
            if reason:
                reduce_pct = 50 if reason == "fr_flip" else 100
                shared_paper.close_order(int(position["id"]), price, reason, reduce_pct=reduce_pct)
        except Exception as exc:
            agent_state.add_event("error", f"POT position check failed for {symbol}: {exc}", module="pot", action="position.check")


def _pot_oi_declining(symbol: str) -> bool:
    hist = potential_scanner.client.open_interest_hist(symbol, period="1h", limit=12)
    values = []
    for item in hist:
        raw = item.get("sumOpenInterestValue") if item.get("sumOpenInterestValue") is not None else item.get("sumOpenInterest")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            values.append(value)
    if len(values) < 12:
        return False
    first = sum(values[:6]) / 6
    second = sum(values[6:]) / 6
    return second < first


def run_agent_tick(payload: dict[str, Any] | None = None, *, allow_worker_key: bool = False) -> tuple[dict[str, Any], int]:
    payload = payload or {}
    wait_for_tick = bool(payload.get("manual") or payload.get("force_heatmap"))
    acquired = tick_lock.acquire(timeout=60) if wait_for_tick else tick_lock.acquire(blocking=False)
    if not acquired:
        return {"error": "agent tick already running", "phase": agent_state.agent_phase}, 409
    agent_state.record_tick_start()
    result: dict[str, Any] | None = None
    status = 500
    report_written = False
    config = agent_state.config
    snapshot = None
    candidate = None
    signal = None
    decision = None
    llm_review = None
    order = None
    heatmap_result: dict[str, Any] = {}
    score: dict[str, Any] | None = None
    try:
        config_patch = dict(payload.get("config") or {})
        for key in ("coin", "symbol", "exchange", "interval"):
            if payload.get(key):
                config_patch[key] = payload.get(key)
        if config_patch:
            config = agent_state.update_config(config_patch)
        pk = _get_private_key(payload, allow_worker_key=allow_worker_key)
        if not pk:
            result, status = {"error": "Enter wallet key"}, 400
            return result, status

        client, addr, err = create_client(pk)
        if err:
            result, status = {"error": err}, 400
            return result, status

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
        score = opportunity_score(signal, heatmap_result, config, decision)
        score_blocked = not score.get("passed")
        if score_blocked and decision.approved:
            decision = RiskDecision(
                False,
                [f"opportunity score {score['score']} below threshold {score['threshold']}", *decision.reasons],
                decision.mode,
                decision.notional_usd,
                decision.leverage,
                decision.stop_loss,
                decision.take_profit,
            )
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
        final_action = "WAIT"
        if decision.approved and config.safety_mode == "observe":
            final_action = "OBSERVE_ONLY"
            agent_state.set_phase("SIGNAL_REJECTED", {"reasons": ["observe mode: no orders are placed"]})
        elif decision.approved and config.safety_mode == "confirm":
            final_action = "PENDING_CONFIRMATION"
            agent_state.set_phase("SIGNAL_REJECTED", {"reasons": ["confirm mode: waiting for user confirmation"]})
        elif decision.approved and config.safety_mode == "live" and not config.live_enabled:
            final_action = "LIVE_BLOCKED"
            decision = RiskDecision(False, ["live safety mode requires live_enabled and implemented adapter", *decision.reasons], decision.mode, decision.notional_usd, decision.leverage, decision.stop_loss, decision.take_profit)
            agent_state.record_risk(decision)
            agent_state.set_phase("SIGNAL_REJECTED", {"reasons": decision.reasons})
        elif decision.approved:
            agent_state.set_phase("ORDER_OPEN")
            executor = paper_executor if config.mode == "paper" else binance_executor
            order = executor.execute(signal, decision, config)
            agent_state.record_order(order)
            if config.mode == "paper":
                agent_state.replace_orders(paper_executor.broker.orders)
            final_action = "ORDER_OPENED" if order else "APPROVED"
        elif signal.valid:
            agent_state.set_phase("SIGNAL_REJECTED", {"reasons": decision.reasons})
            final_action = "REJECTED"
        elif not liquidation_event:
            agent_state.set_phase("SCANNING")
            final_action = "WAIT"

        blockers = []
        if not signal.valid:
            blockers.extend(signal.reasons)
        if score and not score.get("passed"):
            blockers.append(f"opportunity score {score['score']} below threshold {score['threshold']}")
        if decision and not decision.approved:
            blockers.extend(decision.reasons)
        report = build_decision_report(
            config=config,
            snapshot=snapshot,
            candidate=candidate,
            signal=signal,
            heatmap_result=heatmap_result,
            risk=decision,
            llm_review=llm_review,
            order=order,
            score=score,
            final_action=final_action,
            blockers=list(dict.fromkeys(str(item) for item in blockers if item)),
            tick_count=agent_state.tick_count,
        )
        agent_state.record_decision_report(report)
        report_written = True

        result, status = {
            "snapshot": snapshot.to_dict(),
            "signal": signal.to_dict(),
            "risk": decision.to_dict(),
            "llm_review": llm_review,
            "order": order.to_dict() if order else None,
            "heatmap_result": {k: v for k, v in heatmap_result.items() if k != "snapshot"},
            "decision_report": report,
            "status": agent_state.status(),
        }, 200
        return result, status
    except Claw402Error as exc:
        agent_state.add_event("error", f"Payment failed: {exc}")
        result, status = {"error": f"Payment failed: {exc}"}, 402
        return result, status
    except MarketDataRestrictedError as exc:
        agent_state.set_phase("ERROR", {"code": "restricted_location"})
        agent_state.add_event("error", str(exc), {"symbol": agent_state.config.symbol, "exchange": agent_state.config.exchange, "reason": "restricted_location"}, level="error", module="market_data", action="tick")
        result, status = {"error": str(exc), "code": "restricted_location"}, 451
        return result, status
    except MarketDataError as exc:
        agent_state.set_phase("ERROR", {"code": "market_data_error"})
        agent_state.add_event("error", str(exc), {"symbol": agent_state.config.symbol, "exchange": agent_state.config.exchange}, level="error", module="market_data", action="tick")
        result, status = {"error": str(exc), "code": "market_data_error"}, 502
        return result, status
    except Exception as exc:
        agent_state.add_event("error", str(exc))
        result, status = {"error": str(exc)}, 500
        return result, status
    finally:
        if not report_written and status >= 400:
            report = build_decision_report(
                config=config,
                snapshot=snapshot,
                candidate=candidate,
                signal=signal,
                heatmap_result=heatmap_result,
                risk=decision,
                llm_review=llm_review,
                order=order,
                score=score,
                final_action="ERROR",
                blockers=[result.get("error")] if isinstance(result, dict) and result.get("error") else ["tick failed"],
                error=result.get("error") if isinstance(result, dict) else "tick failed",
                tick_count=agent_state.tick_count,
            )
            agent_state.record_decision_report(report)
        agent_state.record_tick_finish(status, result)
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

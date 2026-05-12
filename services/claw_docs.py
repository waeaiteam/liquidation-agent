CLAW402_SUMMARY = {
    "base_url": "https://claw402.ai",
    "catalog_url": "https://claw402.ai/api/v1/catalog",
    "network": "Base mainnet / eip155:8453",
    "asset": "USDC",
    "payment": "x402 exact payment, SDK signs locally with wallet private key; private key is not sent to Claw402.",
    "price_usdc": "Most indexed CoinAnk routes in the catalog report 0.001 USDC per call.",
}

CLAW402_COINANK_ENDPOINTS = [
    {"name": "Latest price", "method": "GET", "path": "/api/v1/coinank/price/last", "params": ["symbol", "exchange", "productType"], "natural": "查询某交易所交易对最新价格。"},
    {"name": "Liquidation intervals", "method": "GET", "path": "/api/v1/coinank/liquidation/intervals", "params": ["baseCoin"], "natural": "查询某币种 1h/24h 等周期的多空清算统计。"},
    {"name": "Aggregated liquidation history", "method": "GET", "path": "/api/v1/coinank/liquidation/agg-history", "params": ["baseCoin", "interval", "endTime", "size"], "natural": "查询全市场聚合清算历史，用于判断清算趋势。"},
    {"name": "Liquidation orders", "method": "GET", "path": "/api/v1/coinank/liquidation/orders", "params": ["baseCoin", "exchange", "side", "amount", "endTime"], "natural": "查询大额清算订单列表，用于捕捉极端清算事件。"},
    {"name": "Aggregated liquidation map", "method": "GET", "path": "/api/v1/coinank/liquidation/agg-liq-map", "params": ["baseCoin", "interval"], "natural": "查询聚合清算地图，观察价格层级上的潜在清算密集区。"},
    {"name": "Open interest", "method": "GET", "path": "/api/v1/coinank/oi/all", "params": ["baseCoin"], "natural": "查询各交易所实时持仓量，辅助判断清算占 OI 的冲击强度。"},
    {"name": "Long/short realtime", "method": "GET", "path": "/api/v1/coinank/longshort/realtime", "params": ["baseCoin", "interval"], "natural": "查询各交易所实时多空比，辅助判断市场拥挤方向。"},
    {"name": "Current funding", "method": "GET", "path": "/api/v1/coinank/funding-rate/current", "params": ["type"], "natural": "查询当前资金费率排行，辅助判断多空情绪是否过热。"},
    {"name": "Liquidation ranking", "method": "GET", "path": "/api/v1/coinank/rank/liquidation", "params": ["sortBy", "sortType", "page", "size"], "natural": "查询清算金额排行榜，快速发现异常币种。"},
]

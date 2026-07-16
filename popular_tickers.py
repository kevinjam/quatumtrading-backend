"""Popular US tickers seed list — used as autocomplete fallback when Finnhub
search is rate-limited. Roughly top-200 by market cap + popular ETFs + well-known
small-caps that retail traders search frequently."""

POPULAR_TICKERS = [
    # Mega-cap tech
    ("AAPL", "Apple Inc.", "Common Stock"),
    ("MSFT", "Microsoft Corporation", "Common Stock"),
    ("NVDA", "NVIDIA Corporation", "Common Stock"),
    ("GOOGL", "Alphabet Inc. Class A", "Common Stock"),
    ("GOOG", "Alphabet Inc. Class C", "Common Stock"),
    ("AMZN", "Amazon.com Inc.", "Common Stock"),
    ("META", "Meta Platforms Inc.", "Common Stock"),
    ("TSLA", "Tesla Inc.", "Common Stock"),
    ("BRK.A", "Berkshire Hathaway Inc. Class A", "Common Stock"),
    ("BRK.B", "Berkshire Hathaway Inc. Class B", "Common Stock"),
    ("AVGO", "Broadcom Inc.", "Common Stock"),
    ("ORCL", "Oracle Corporation", "Common Stock"),
    ("CRM", "Salesforce Inc.", "Common Stock"),
    ("ADBE", "Adobe Inc.", "Common Stock"),
    ("CSCO", "Cisco Systems Inc.", "Common Stock"),
    ("INTC", "Intel Corporation", "Common Stock"),
    ("AMD", "Advanced Micro Devices Inc.", "Common Stock"),
    ("QCOM", "Qualcomm Incorporated", "Common Stock"),
    ("TXN", "Texas Instruments Incorporated", "Common Stock"),
    ("IBM", "International Business Machines", "Common Stock"),
    ("NOW", "ServiceNow Inc.", "Common Stock"),
    ("INTU", "Intuit Inc.", "Common Stock"),
    ("PYPL", "PayPal Holdings Inc.", "Common Stock"),
    ("SHOP", "Shopify Inc.", "Common Stock"),
    ("UBER", "Uber Technologies Inc.", "Common Stock"),
    ("LYFT", "Lyft Inc.", "Common Stock"),
    ("ABNB", "Airbnb Inc.", "Common Stock"),
    ("SNOW", "Snowflake Inc.", "Common Stock"),
    ("PLTR", "Palantir Technologies Inc.", "Common Stock"),
    ("NFLX", "Netflix Inc.", "Common Stock"),
    ("DIS", "The Walt Disney Company", "Common Stock"),
    ("ROKU", "Roku Inc.", "Common Stock"),
    ("SPOT", "Spotify Technology S.A.", "Common Stock"),
    ("SQ", "Block Inc.", "Common Stock"),
    ("COIN", "Coinbase Global Inc.", "Common Stock"),
    ("HOOD", "Robinhood Markets Inc.", "Common Stock"),
    ("RBLX", "Roblox Corporation", "Common Stock"),
    ("U", "Unity Software Inc.", "Common Stock"),
    ("DDOG", "Datadog Inc.", "Common Stock"),
    ("CRWD", "CrowdStrike Holdings Inc.", "Common Stock"),
    ("ZS", "Zscaler Inc.", "Common Stock"),
    ("PANW", "Palo Alto Networks Inc.", "Common Stock"),
    ("NET", "Cloudflare Inc.", "Common Stock"),
    ("MDB", "MongoDB Inc.", "Common Stock"),
    ("DOCN", "DigitalOcean Holdings Inc.", "Common Stock"),
    ("MU", "Micron Technology Inc.", "Common Stock"),
    ("AMAT", "Applied Materials Inc.", "Common Stock"),
    ("LRCX", "Lam Research Corporation", "Common Stock"),
    ("KLAC", "KLA Corporation", "Common Stock"),
    ("ASML", "ASML Holding N.V.", "Common Stock"),
    ("TSM", "Taiwan Semiconductor Manufacturing", "Common Stock"),
    ("ARM", "Arm Holdings plc", "Common Stock"),
    # Financials
    ("JPM", "JPMorgan Chase & Co.", "Common Stock"),
    ("BAC", "Bank of America Corporation", "Common Stock"),
    ("WFC", "Wells Fargo & Company", "Common Stock"),
    ("C", "Citigroup Inc.", "Common Stock"),
    ("GS", "The Goldman Sachs Group Inc.", "Common Stock"),
    ("MS", "Morgan Stanley", "Common Stock"),
    ("V", "Visa Inc.", "Common Stock"),
    ("MA", "Mastercard Incorporated", "Common Stock"),
    ("AXP", "American Express Company", "Common Stock"),
    ("BLK", "BlackRock Inc.", "Common Stock"),
    ("SCHW", "Charles Schwab Corporation", "Common Stock"),
    ("USB", "U.S. Bancorp", "Common Stock"),
    ("PNC", "PNC Financial Services Group", "Common Stock"),
    ("TFC", "Truist Financial Corporation", "Common Stock"),
    # Healthcare
    ("UNH", "UnitedHealth Group Incorporated", "Common Stock"),
    ("JNJ", "Johnson & Johnson", "Common Stock"),
    ("LLY", "Eli Lilly and Company", "Common Stock"),
    ("PFE", "Pfizer Inc.", "Common Stock"),
    ("MRK", "Merck & Co. Inc.", "Common Stock"),
    ("ABBV", "AbbVie Inc.", "Common Stock"),
    ("ABT", "Abbott Laboratories", "Common Stock"),
    ("TMO", "Thermo Fisher Scientific Inc.", "Common Stock"),
    ("DHR", "Danaher Corporation", "Common Stock"),
    ("BMY", "Bristol-Myers Squibb Company", "Common Stock"),
    ("CVS", "CVS Health Corporation", "Common Stock"),
    ("AMGN", "Amgen Inc.", "Common Stock"),
    ("GILD", "Gilead Sciences Inc.", "Common Stock"),
    ("MRNA", "Moderna Inc.", "Common Stock"),
    ("BNTX", "BioNTech SE", "Common Stock"),
    ("NVAX", "Novavax Inc.", "Common Stock"),
    # Consumer
    ("WMT", "Walmart Inc.", "Common Stock"),
    ("COST", "Costco Wholesale Corporation", "Common Stock"),
    ("HD", "The Home Depot Inc.", "Common Stock"),
    ("LOW", "Lowe's Companies Inc.", "Common Stock"),
    ("TGT", "Target Corporation", "Common Stock"),
    ("MCD", "McDonald's Corporation", "Common Stock"),
    ("SBUX", "Starbucks Corporation", "Common Stock"),
    ("NKE", "NIKE Inc.", "Common Stock"),
    ("PEP", "PepsiCo Inc.", "Common Stock"),
    ("KO", "The Coca-Cola Company", "Common Stock"),
    ("PG", "The Procter & Gamble Company", "Common Stock"),
    ("UL", "Unilever PLC", "Common Stock"),
    ("MO", "Altria Group Inc.", "Common Stock"),
    ("PM", "Philip Morris International", "Common Stock"),
    ("EL", "The Estee Lauder Companies Inc.", "Common Stock"),
    ("LULU", "Lululemon Athletica Inc.", "Common Stock"),
    ("CMG", "Chipotle Mexican Grill Inc.", "Common Stock"),
    ("YUM", "Yum! Brands Inc.", "Common Stock"),
    # Energy / Industrial
    ("XOM", "Exxon Mobil Corporation", "Common Stock"),
    ("CVX", "Chevron Corporation", "Common Stock"),
    ("COP", "ConocoPhillips", "Common Stock"),
    ("OXY", "Occidental Petroleum Corporation", "Common Stock"),
    ("EOG", "EOG Resources Inc.", "Common Stock"),
    ("SLB", "Schlumberger N.V.", "Common Stock"),
    ("BA", "The Boeing Company", "Common Stock"),
    ("CAT", "Caterpillar Inc.", "Common Stock"),
    ("GE", "General Electric Company", "Common Stock"),
    ("MMM", "3M Company", "Common Stock"),
    ("HON", "Honeywell International Inc.", "Common Stock"),
    ("LMT", "Lockheed Martin Corporation", "Common Stock"),
    ("RTX", "RTX Corporation", "Common Stock"),
    ("UPS", "United Parcel Service Inc.", "Common Stock"),
    ("FDX", "FedEx Corporation", "Common Stock"),
    # Auto / EV
    ("F", "Ford Motor Company", "Common Stock"),
    ("GM", "General Motors Company", "Common Stock"),
    ("RIVN", "Rivian Automotive Inc.", "Common Stock"),
    ("LCID", "Lucid Group Inc.", "Common Stock"),
    ("NIO", "NIO Inc.", "Common Stock"),
    ("XPEV", "XPeng Inc.", "Common Stock"),
    ("LI", "Li Auto Inc.", "Common Stock"),
    ("BYDDY", "BYD Company Limited", "Common Stock"),
    # Crypto / Fintech
    ("MSTR", "MicroStrategy Incorporated", "Common Stock"),
    ("RIOT", "Riot Platforms Inc.", "Common Stock"),
    ("MARA", "Marathon Digital Holdings Inc.", "Common Stock"),
    ("CLSK", "CleanSpark Inc.", "Common Stock"),
    # ETFs
    ("SPY", "SPDR S&P 500 ETF Trust", "ETF"),
    ("QQQ", "Invesco QQQ Trust", "ETF"),
    ("IWM", "iShares Russell 2000 ETF", "ETF"),
    ("DIA", "SPDR Dow Jones Industrial Average ETF", "ETF"),
    ("VOO", "Vanguard S&P 500 ETF", "ETF"),
    ("VTI", "Vanguard Total Stock Market ETF", "ETF"),
    ("VEA", "Vanguard FTSE Developed Markets ETF", "ETF"),
    ("VWO", "Vanguard FTSE Emerging Markets ETF", "ETF"),
    ("EFA", "iShares MSCI EAFE ETF", "ETF"),
    ("EEM", "iShares MSCI Emerging Markets ETF", "ETF"),
    ("TLT", "iShares 20+ Year Treasury Bond ETF", "ETF"),
    ("GLD", "SPDR Gold Shares", "ETF"),
    ("SLV", "iShares Silver Trust", "ETF"),
    ("XLK", "Technology Select Sector SPDR Fund", "ETF"),
    ("XLE", "Energy Select Sector SPDR Fund", "ETF"),
    ("XLF", "Financial Select Sector SPDR Fund", "ETF"),
    ("XLV", "Health Care Select Sector SPDR Fund", "ETF"),
    ("XLY", "Consumer Discretionary Select Sector SPDR Fund", "ETF"),
    ("XLP", "Consumer Staples Select Sector SPDR Fund", "ETF"),
    ("XLI", "Industrial Select Sector SPDR Fund", "ETF"),
    ("XLU", "Utilities Select Sector SPDR Fund", "ETF"),
    ("XLB", "Materials Select Sector SPDR Fund", "ETF"),
    ("XLRE", "Real Estate Select Sector SPDR Fund", "ETF"),
    ("ARKK", "ARK Innovation ETF", "ETF"),
    ("SOXL", "Direxion Daily Semiconductor Bull 3X", "ETF"),
    ("TQQQ", "ProShares UltraPro QQQ", "ETF"),
    ("UVXY", "ProShares Ultra VIX Short-Term Futures ETF", "ETF"),
    ("VXX", "iPath Series B S&P 500 VIX Short-Term Futures", "ETF"),
    # Memes / Retail favorites
    ("GME", "GameStop Corp.", "Common Stock"),
    ("AMC", "AMC Entertainment Holdings Inc.", "Common Stock"),
    ("BB", "BlackBerry Limited", "Common Stock"),
    ("BBBY", "Bed Bath & Beyond Inc.", "Common Stock"),
    ("DJT", "Trump Media & Technology Group Corp.", "Common Stock"),
    ("SOFI", "SoFi Technologies Inc.", "Common Stock"),
    ("OPEN", "Opendoor Technologies Inc.", "Common Stock"),
    ("WISH", "ContextLogic Inc.", "Common Stock"),
    ("CLOV", "Clover Health Investments Corp.", "Common Stock"),
    ("F", "Ford Motor Company", "Common Stock"),
    # Misc popular
    ("T", "AT&T Inc.", "Common Stock"),
    ("VZ", "Verizon Communications Inc.", "Common Stock"),
    ("TMUS", "T-Mobile US Inc.", "Common Stock"),
    ("CMCSA", "Comcast Corporation", "Common Stock"),
    ("NEE", "NextEra Energy Inc.", "Common Stock"),
    ("DUK", "Duke Energy Corporation", "Common Stock"),
    ("SO", "The Southern Company", "Common Stock"),
    ("CCL", "Carnival Corporation", "Common Stock"),
    ("NCLH", "Norwegian Cruise Line Holdings", "Common Stock"),
    ("RCL", "Royal Caribbean Cruises Ltd.", "Common Stock"),
    ("DAL", "Delta Air Lines Inc.", "Common Stock"),
    ("UAL", "United Airlines Holdings Inc.", "Common Stock"),
    ("AAL", "American Airlines Group Inc.", "Common Stock"),
    ("LUV", "Southwest Airlines Co.", "Common Stock"),
]


def local_symbol_search(query: str, limit: int = 8) -> list[dict]:
    """Prefix-match against the curated POPULAR_TICKERS list.
    Matches against either symbol or description (case-insensitive)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    starts = []
    contains = []
    for sym, desc, typ in POPULAR_TICKERS:
        s_l = sym.lower()
        d_l = desc.lower()
        if s_l.startswith(q) or d_l.startswith(q):
            starts.append({"symbol": sym, "description": desc, "type": typ})
        elif q in s_l or q in d_l:
            contains.append({"symbol": sym, "description": desc, "type": typ})
        if len(starts) >= limit:
            break
    out = starts + contains
    # de-dupe by symbol while preserving order
    seen = set()
    deduped = []
    for r in out:
        if r["symbol"] in seen:
            continue
        seen.add(r["symbol"])
        deduped.append(r)
        if len(deduped) >= limit:
            break
    return deduped

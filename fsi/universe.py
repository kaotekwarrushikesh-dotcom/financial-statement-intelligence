"""The 20-company analysis universe.

Chosen as large, liquid US filers with long EDGAR histories, spread across sectors so
the sector-relative weakness of absolute scoring thresholds becomes visible rather than
hidden. Sector labels drive the peer comparison in the report.

Banks, insurers and other financials are deliberately excluded: they do not report COGS
or gross profit, and their balance sheets are unclassified, so current ratio and working
capital are undefined. They need a different data model, not a wider one.
"""

UNIVERSE: dict[str, tuple[str, str]] = {
    # ticker: (company name, sector)
    "AAPL": ("Apple Inc.", "Technology"),
    "MSFT": ("Microsoft Corp.", "Technology"),
    "GOOGL": ("Alphabet Inc.", "Communication Services"),
    "AMZN": ("Amazon.com Inc.", "Consumer Discretionary"),
    "NVDA": ("NVIDIA Corp.", "Technology"),
    "META": ("Meta Platforms Inc.", "Communication Services"),
    "TSLA": ("Tesla Inc.", "Consumer Discretionary"),
    "ORCL": ("Oracle Corp.", "Technology"),
    "WMT": ("Walmart Inc.", "Consumer Staples"),
    "COST": ("Costco Wholesale Corp.", "Consumer Staples"),
    "HD": ("Home Depot Inc.", "Consumer Discretionary"),
    "PG": ("Procter & Gamble Co.", "Consumer Staples"),
    "KO": ("Coca-Cola Co.", "Consumer Staples"),
    "PEP": ("PepsiCo Inc.", "Consumer Staples"),
    "JNJ": ("Johnson & Johnson", "Healthcare"),
    "MRK": ("Merck & Co. Inc.", "Healthcare"),
    "PFE": ("Pfizer Inc.", "Healthcare"),
    "ABBV": ("AbbVie Inc.", "Healthcare"),
    "XOM": ("Exxon Mobil Corp.", "Energy"),
    "CVX": ("Chevron Corp.", "Energy"),
}

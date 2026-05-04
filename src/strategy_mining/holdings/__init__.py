"""Holdings ingestion pipeline.

Flow:
    screenshot (.jpg/.png) -> VLM extractor -> structured JSON
                           -> stock_resolver (fuzzy prefix + price match via akshare)
                           -> snapshot_repo (write holding_snapshots + holding_items)
                           -> diff_engine  (infer buy/add/reduce/sell across dates)
                           -> pnl_calculator (realized P&L via move-weighted cost)
"""

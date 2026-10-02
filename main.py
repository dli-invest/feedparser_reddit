import logging
from parser.scanner import ScanSRs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

if __name__ == "__main__":
    # Ready to make calls! Equivalent to reddit.ScanSRs("cmd/scan_stock_sr/stock.yml")
    ScanSRs("config/stock.yml")
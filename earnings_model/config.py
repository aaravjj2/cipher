import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'earnings.sqlite')

YFINANCE_DELAY = 0.5
YFINANCE_BATCH_SIZE = 10
MAX_EARNINGS_LOOKBACK = 40
CAP_TIERS_PRIORITY = ['mega', 'large', 'medium', 'small']

UNIVERSE_PATH = '/home/aarav/Aarav/cipher/cipher-system/data/optionable_universe_by_cap.json'

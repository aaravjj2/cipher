import json, os
path = '/home/aarav/Aarav/cipher/cipher-system/data/live_option_chains/'
for fname in sorted(os.listdir(path)):
    if fname.startswith('latest_') and fname.endswith('.json'):
        fpath = os.path.join(path, fname)
        try:
            with open(fpath) as f:
                data = json.load(f)
            print(f'{fname}: {data.get("captured_at", "N/A")}')
        except Exception as e:
            print(f'{fname}: ERROR - {e}')
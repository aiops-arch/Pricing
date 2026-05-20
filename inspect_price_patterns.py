import zipfile, io, re
from datetime import datetime
from collections import defaultdict

zip_path = r"E:\Pricing\backup.zip"

def parse_date_from_name(name):
    # Try DD-MM-YYYY pattern
    m = re.search(r'(\d{2})-(\d{2})-(\d{4})', name)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except:
            pass
    return None

def read_round_rows(z, filename):
    with z.open(filename) as f:
        lines = f.read().decode('utf-8', errors='replace').splitlines()
    if not lines:
        return {}
    header = lines[0].split(',')
    # Find column indices
    def ci(name):
        for i, h in enumerate(header):
            if h.strip() == name:
                return i
        return None
    color_i = ci('Color')
    shape_i = ci('Shape')
    clarity_i = ci('Clarity')
    cut_i = ci('Cut')
    fluor_i = ci('Fluorescence')
    from_i = ci('From Size')
    to_i = ci('To Size')
    disc_i = ci('Disc Per')
    if any(x is None for x in [color_i, shape_i, clarity_i, cut_i, fluor_i, from_i, to_i, disc_i]):
        return {}
    rows = {}
    for line in lines[1:]:
        c = line.split(',')
        if len(c) <= max(color_i, shape_i, clarity_i, cut_i, fluor_i, from_i, to_i, disc_i):
            continue
        shape = c[shape_i].strip()
        try:
            from_s = float(c[from_i])
            to_s = float(c[to_i])
            disc = float(c[disc_i])
        except:
            continue
        if shape == 'ROUND' and from_s >= 0.30 and to_s <= 2.00:
            key = (c[color_i].strip(), c[clarity_i].strip(), c[cut_i].strip(),
                   c[fluor_i].strip(), from_s, to_s)
            rows[key] = disc
    return rows

print("Loading all backup CSV files for ROUND 0.30-2.00ct...")
with zipfile.ZipFile(zip_path, 'r') as z:
    csvs = sorted([n for n in z.namelist() if n.endswith('.csv')])
    print("Total CSV files: " + str(len(csvs)))

    # Build time series: date -> {key -> disc}
    timeline = []
    for name in csvs:
        dt = parse_date_from_name(name)
        if dt is None:
            continue
        rows = read_round_rows(z, name)
        if rows:
            timeline.append((dt, name, rows))

timeline.sort(key=lambda x: x[0])
print("Files with parseable dates: " + str(len(timeline)))
print("Date range: " + str(timeline[0][0].date()) + " to " + str(timeline[-1][0].date()))

# Per-key price history
key_history = defaultdict(list)
for dt, name, rows in timeline:
    for key, disc in rows.items():
        key_history[key].append((dt, disc))

print("Unique ROUND 0.30-2.00ct criteria keys tracked: " + str(len(key_history)))

# Analyse changes per key
changes_per_key = {}
for key, hist in key_history.items():
    hist_sorted = sorted(set(hist), key=lambda x: x[0])
    changes = 0
    diffs = []
    for i in range(1, len(hist_sorted)):
        if hist_sorted[i][1] != hist_sorted[i-1][1]:
            changes += 1
            diffs.append(hist_sorted[i][1] - hist_sorted[i-1][1])
    if diffs:
        changes_per_key[key] = {
            'changes': changes,
            'min_disc': min(d for _, d in hist_sorted),
            'max_disc': max(d for _, d in hist_sorted),
            'range': max(d for _, d in hist_sorted) - min(d for _, d in hist_sorted),
            'avg_change': sum(abs(d) for d in diffs) / len(diffs),
            'first': hist_sorted[0][1],
            'last': hist_sorted[-1][1],
            'obs': len(hist_sorted),
        }

print()
print("Keys with at least one price change: " + str(len(changes_per_key)))

# Top 20 most-changed keys
sorted_by_changes = sorted(changes_per_key.items(), key=lambda x: -x[1]['changes'])
print()
print("TOP 20 most frequently changed criteria (Color/Clarity/Cut/Fluor/Size):")
for key, stats in sorted_by_changes[:20]:
    color, clarity, cut, fluor, from_s, to_s = key
    print("  " + color + "/" + clarity + "/" + cut + "/" + fluor +
          " [" + str(from_s) + "-" + str(to_s) + "]" +
          "  changes=" + str(stats['changes']) +
          "  range=" + str(round(stats['range'],2)) + "%" +
          "  disc: " + str(round(stats['first'],2)) + " -> " + str(round(stats['last'],2)) +
          "  avg_move=" + str(round(stats['avg_change'],2)))

# Distribution of change sizes
all_diffs = []
for key, hist in key_history.items():
    hist_sorted = sorted(set(hist), key=lambda x: x[0])
    for i in range(1, len(hist_sorted)):
        diff = hist_sorted[i][1] - hist_sorted[i-1][1]
        if diff != 0:
            all_diffs.append(diff)

print()
print("Price change distribution (all ROUND 0.30-2.00ct moves):")
print("  Total moves: " + str(len(all_diffs)))
if all_diffs:
    increases = [d for d in all_diffs if d > 0]
    decreases = [d for d in all_diffs if d < 0]
    print("  Increases (disc up = price down): " + str(len(increases)) +
          "  avg=" + str(round(sum(increases)/len(increases),2) if increases else 0))
    print("  Decreases (disc down = price up): " + str(len(decreases)) +
          "  avg=" + str(round(sum(decreases)/len(decreases),2) if decreases else 0))
    abs_diffs = sorted([abs(d) for d in all_diffs])
    n = len(abs_diffs)
    print("  Median abs change: " + str(round(abs_diffs[n//2],2)))
    print("  95th pct abs change: " + str(round(abs_diffs[int(n*0.95)],2)))
    print("  Max abs change: " + str(round(abs_diffs[-1],2)))

# Unique size buckets
size_buckets = sorted(set((from_s, to_s) for (_, _, _, _, from_s, to_s) in key_history.keys()))
print()
print("Size buckets present in ROUND data:")
for sb in size_buckets:
    cnt = sum(1 for k in key_history if k[4] == sb[0] and k[5] == sb[1])
    print("  " + str(sb[0]) + "-" + str(sb[1]) + "ct: " + str(cnt) + " unique criteria keys")

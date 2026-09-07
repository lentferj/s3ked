"""Preroll bleed detected against the run's own FLOOR, not the next note's peak.

measure.py's `contaminated` field tests preroll within 40 dB of the following
peak. That criterion is wrong in BOTH directions and neither error is visible
without a floor comparison:

    quiet note, clean window    -> FIRES    (11 of 48 on one s3ked run whose
                                             prerolls span only 2.80 dB, i.e.
                                             every one of them sits on the floor)
    loud note, bleeding window  -> SILENT   (missed: a sibling run had five
                                             programs +8 to +12 dB above its
                                             floor that the rule never flagged,
                                             because loud peaks keep the margin
                                             satisfied)

A bleeding previous note RAISES the preroll. That is the signal, and it is only
readable relative to the quietest prerolls in the same run. A run whose prerolls
span 2.8 dB has no bleed anywhere; one spanning 27 dB has it somewhere specific.

Post-pass only: applies to files already written, so a whole campaign stays
comparable and nothing needs re-recording.
"""
import json, statistics, sys

def analyse(path, elevated_db=6.0):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    pr = [n['preroll_db'] for r in d for n in r.get('notes', []) if 'preroll_db' in n]
    if not pr:
        return None
    floor = statistics.median(pr)
    rows = []
    for r in d:
        for n in r.get('notes', []):
            if 'preroll_db' not in n:
                continue
            lift = n['preroll_db'] - floor
            rows.append({'program': r['program'], 'note': n['note'],
                         'peak_db': n.get('peak_db'), 'preroll_db': n['preroll_db'],
                         'lift_db': lift, 'bleed': lift >= elevated_db,
                         'old_flag': (n.get('peak_db') is not None
                                      and n['peak_db'] - n['preroll_db'] < 40)})
    return {'floor_db': floor, 'spread_db': max(pr) - min(pr), 'rows': rows}

if __name__ == '__main__':
    for p in sys.argv[1:]:
        a = analyse(p)
        if not a:
            print('%s: no preroll data' % p); continue
        rows = a['rows']
        bleed = [r for r in rows if r['bleed']]
        old = [r for r in rows if r['old_flag']]
        both = [r for r in rows if r['bleed'] and r['old_flag']]
        print('\n%s' % p.rsplit('/', 1)[-1])
        print('  floor %.2f dB   preroll spread %.2f dB   %d windows'
              % (a['floor_db'], a['spread_db'], len(rows)))
        print('  floor-relative bleed (>= 6 dB lift): %d' % len(bleed))
        print('  old 40 dB-margin flag              : %d' % len(old))
        print('  agree                              : %d' % len(both))
        for r in sorted(bleed, key=lambda r: -r['lift_db'])[:8]:
            print('     prog %-3d note %-3d  lift %+6.2f dB  peak %7.2f'
                  % (r['program'], r['note'], r['lift_db'], r['peak_db']))

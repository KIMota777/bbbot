import json, io
rows = json.load(io.open('webapp/data/all_configs_honest.json', encoding='utf-8'))['rows']
def lbl(r):
    src = r['src'].replace('_winners','').replace('_final','')
    tag = (src + '/' + r['tag']) if r['src'] != 'config.py' else r['tag']
    return '%s/%s' % (r['sym'].replace('USDT',''), tag)
byr = sorted(rows, key=lambda x: -min(x['train']['rob'], x['hold']['rob']))
print('PO HUDSHEY USTOYCHIVOSTI - pervye shest')
for i, r in enumerate(byr[:6], 1):
    print('  %d. %-24s hudshaya %+6.1f%%  (obuch %+6.1f%% / hold %+6.1f%%)  holdout %+6.1f%%'
          % (i, lbl(r), min(r['train']['rob'], r['hold']['rob']),
             r['train']['rob'], r['hold']['rob'], r['hold']['comp']))

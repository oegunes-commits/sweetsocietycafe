# -*- coding: utf-8 -*-
"""Sweet Society Cafe gallery updater.

Checks @kuzumcookies on Instagram (via the pixwox.com mirror) for posts that
are not yet in gallery/img/, downloads and optimizes the new ones, categorizes
them from the caption, and prepends them to the DATA array in gallery/index.html
(also updating the filter-pill counts and the header total).

Run from anywhere:  python tools/update_gallery.py [max_api_pages]
Requires: Pillow (pip install pillow), curl on PATH.
Exits 0 always; prints CHANGED=<n> on the last line (0 means nothing to commit).
"""
import re, json, html, hashlib, os, sys, time, subprocess
from io import BytesIO
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG_DIR = os.path.join(ROOT, 'gallery', 'img')
PAGE = os.path.join(ROOT, 'gallery', 'index.html')
CODES = os.path.join(ROOT, 'tools', 'known_codes.json')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')
MAX_PAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 6

def curl(url, referer='https://imginn.com/'):
    cmd = ['curl', '-s', '-L', '--max-time', '30', '-A', UA,
           '-H', f'Referer: {referer}',
           '-H', 'Accept: application/json, text/plain, */*',
           '-H', 'X-Requested-With: XMLHttpRequest', url]
    r = subprocess.run(cmd, capture_output=True)
    return r.stdout

def norm(s):
    s = (s or '').lower()
    s = re.sub(r'#\S+', ' ', s)
    s = re.sub(r'@\S+', ' ', s)
    return re.sub(r'\s+', ' ', s)

CATS = [
 ('wedding',  ['wedding','bridal','bride','mr & mrs','mr&mrs','engag','she said yes','said yes','nişan','nisan','henna','kına','kina gecesi','düğün','dugun','nikah','just married','groom']),
 ('baby',     ['baby','bebek','stork','gender reveal','boyyy','girlll','hosgeldin','hoşgeldin','welcome baby','oh baby','baby shower','little prince','little princess',"it's a boy","it's a girl"]),
 ('grad',     ['graduation','congrat','mezun','class of','diploma','dr. ']),
 ('macaron',  ['tower','croquembouche','cream puff','creampuff','madeline','madeleine','profiterol','kule','macaron','macaroon','makaron','pavlova']),
 ('birthday', ['birthday','doğdun','dogdun','iyi ki dog','sweet 16','chapter ','hello 30','turns one',' is one','doğum günü','dogum gunu','yaş gün','happy bday','bday','happy first']),
 ('cookies',  ['cookie','biscoff','kurabiye','brownie','cheesecake','tart','pie ','cupcake','cake pop','sourdough','lemon bar','shortcake','cone cake','dessert','çilek','muffin','scone']),
]

def cat_of(cap):
    c = norm(cap)
    for name, kws in CATS:
        for k in kws:
            if k in c:
                return name
    return 'more'

# ---- 1. scrape newest posts -------------------------------------------------
# Primary source: imginn.com profile page (12 newest posts, IG shortcodes).
# Fallback: pixwox.com profile + API (numeric shortcodes; behind Cloudflare
# since Aug 2026, may start working again).
posts = []  # newest first

profile = curl('https://imginn.com/kuzumcookies/').decode('utf-8', 'replace')
for block in re.findall(r'<div class="item">.*?</div>\s*</div>', profile, re.S):
    code = re.search(r'href="/p/([0-9A-Za-z_-]+)/"', block)
    src = re.search(r'<img[^>]+src="([^"]+)"', block)
    alt = re.search(r'<img[^>]+alt="([^"]*)"', block)
    if code and src and '{code}' not in code.group(1):
        posts.append({'cap': html.unescape(alt.group(1)) if alt else '',
                      'thum': html.unescape(src.group(1)), 'code': code.group(1)})

if len(posts) < 5:
    print('imginn failed, trying pixwox...')
    posts = []
    profile = curl('https://www.pixwox.com/profile/kuzumcookies/',
                   referer='https://www.pixwox.com/').decode('utf-8', 'replace')
    for alt, src in re.findall(r'<img alt="([^"]*)" class="lazyload img" data-src="([^"]+)"', profile):
        posts.append({'cap': html.unescape(alt), 'thum': html.unescape(src), 'code': None})
    codes = re.findall(r'href="/post/([0-9A-Za-z_-]+)/"', profile)
    for i, p in enumerate(posts):
        if i < len(codes):
            p['code'] = codes[i]
    m = re.search(r'name="next" value="([^"]+)"', profile) or re.search(r'data-next="([^"]+)"', profile)
    cur = m.group(1) if m else None
    for page in range(MAX_PAGES):
        if not cur:
            break
        try:
            j = json.loads(curl(f'https://www.pixwox.com/api/posts?userid=1311383307&next={cur}',
                                referer='https://www.pixwox.com/profile/kuzumcookies/'))
        except Exception:
            break
        pp = j.get('posts') or {}
        for it in (pp.get('items') or []):
            posts.append({'cap': html.unescape(it.get('sum') or ''),
                          'thum': it.get('thum'), 'code': str(it.get('shortcode') or '')})
        cur = pp.get('next') or ''
        if not pp.get('has_next'):
            break
        time.sleep(0.5)

print(f'scraped {len(posts)} recent posts')
if len(posts) < 5:
    print('WARN: scrape looks broken (pixwox down or markup changed) - aborting safely')
    print('CHANGED=0')
    sys.exit(0)

# ---- 2. find genuinely new ones --------------------------------------------
# Dedupe is by Instagram post code: tools/known_codes.json lists every post
# already processed (added, or deliberately skipped e.g. blurry video covers).
# Content hashing is unreliable here because mirror sites crop differently.
known = set()
if os.path.exists(CODES):
    known = set(json.load(open(CODES)))

new_items = []  # newest first, order preserved
for p in posts:
    if not p['thum'] or not p['code'] or p['code'] in known:
        continue
    fid = hashlib.md5(p['code'].encode()).hexdigest()[:12]
    dst = os.path.join(IMG_DIR, fid + '.jpg')
    if os.path.exists(dst):
        known.add(p['code'])
        continue
    try:
        data = curl(p['thum'])
        if len(data) < 3000:
            raise ValueError('tiny response')
        im = Image.open(BytesIO(data)).convert('RGB')
    except Exception as e:
        print('skip (download failed, will retry next run):', p['code'], str(e)[:60])
        continue
    # blur guard against video placeholder covers
    small = im.convert('L').resize((160, 160))
    px = list(small.getdata())
    lap = 0.0
    for y in range(1, 159):
        row = y * 160
        for x in range(1, 159, 2):
            v = 4*px[row+x] - px[row+x-1] - px[row+x+1] - px[row-160+x] - px[row+160+x]
            lap += v * v
    var = lap / (159 * 80)
    if var < 260:
        print('skip (blurry placeholder):', p['code'], f'var={var:.0f}')
        known.add(p['code'])
        continue
    im.thumbnail((560, 560), Image.LANCZOS)
    im.save(dst, quality=74, optimize=True, progressive=True)
    known.add(p['code'])
    new_items.append({'f': fid + '.jpg', 'c': cat_of(p['cap']),
                      't': ' '.join((p['cap'] or '').split())[:120]})
    print('added:', fid, new_items[-1]['c'], '|', new_items[-1]['t'][:60])

json.dump(sorted(known), open(CODES, 'w'))
if not new_items:
    print('nothing new')
    print('CHANGED=0')
    sys.exit(0)

# ---- 3. update gallery/index.html -------------------------------------------
page = open(PAGE, encoding='utf-8').read()
m = re.search(r'const DATA = (\[.*?\]);\n', page, re.S)
data = json.loads(m.group(1))
data = new_items + data
counts = {}
for d in data:
    counts[d['c']] = counts.get(d['c'], 0) + 1
total = len(data)

page = page[:m.start()] + 'const DATA = ' + json.dumps(data, ensure_ascii=False, separators=(',', ':')) + ';\n' + page[m.end():]
page = re.sub(r'[\d,]+ sweet moments', f'{total:,} sweet moments', page)
def fix_pill(mt):
    key = mt.group(1)
    n = total if key == 'all' else counts.get(key, 0)
    return mt.group(0).split('<span>')[0] + f'<span>{n}</span>'
page = re.sub(r'data-filter="(\w+)">[^<]*<span>\d+</span>', fix_pill, page)

open(PAGE, 'w', encoding='utf-8').write(page)
print(f'gallery updated: +{len(new_items)} -> {total} total')
print('CHANGED=' + str(len(new_items)))

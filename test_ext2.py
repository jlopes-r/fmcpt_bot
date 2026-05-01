import urllib.request, re, asyncio
req = urllib.request.Request('https://www.instagram.com/p/DXwgCvJkVrl/embed/', headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
try:
 html = urllib.request.urlopen(req).read().decode('utf-8')
 img = re.findall(r'class=.EmbeddedMediaImage.[^>]*src=.([^>]+).', html)
 print(img)
except Exception as e:
 print('Error:', e)

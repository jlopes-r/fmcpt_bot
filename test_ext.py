import urllib.request
req = urllib.request.Request('https://www.instagram.com/p/DXwgCvJkVrl/embed/', headers={'User-Agent': 'Mozilla/5.0'})
print(len(urllib.request.urlopen(req).read()))

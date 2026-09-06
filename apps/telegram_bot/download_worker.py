"""Isolated yt-dlp worker; stdout contains only JSON protocol messages."""
import json
import sys
from pathlib import Path

from apps.telegram_bot.downloaders import processar_com_fallback, limite_duracao_filter


def main():
    request = json.loads(sys.stdin.readline())
    opts = request['options']
    limit = opts.pop('_duration_limit', None)
    if limit:
        opts['match_filter'] = limite_duracao_filter(limit)
    def progress(data):
        print(json.dumps({'progress': data.get('downloaded_bytes', 0)}), flush=True)
    opts['progress_hooks'] = [progress]
    try:
        info = processar_com_fallback(request['url'], opts)
        # Keep only serializable metadata, with exact final paths after merging.
        import yt_dlp
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.sanitize_info(info)
        def paths(item):
            if not item:
                return
            for child in item.get('entries') or []:
                paths(child)
            for dl in item.get('requested_downloads') or []:
                if dl.get('filepath') and Path(dl['filepath']).is_file():
                    item['filepath'] = dl['filepath']
        paths(info)
        print(json.dumps({'result': info}), flush=True)
    except Exception as exc:
        print(json.dumps({'error': str(exc)}), flush=True)
        sys.exit(1)


if __name__ == '__main__':
    main()

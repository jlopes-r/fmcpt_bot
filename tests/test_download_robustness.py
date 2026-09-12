import asyncio
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from apps.telegram_bot import downloaders as dl
from apps.telegram_bot.facebook import parse_public_post
from packages.url_utils import normalizar_link_social


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_low_disk_does_not_start_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(dl, '_download_lock', asyncio.Lock()), patch.object(dl.shutil, 'disk_usage', return_value=SimpleNamespace(free=1)), patch.object(dl.asyncio, 'create_subprocess_exec') as spawn:
                with self.assertRaisesRegex(RuntimeError, 'Espaço'):
                    await dl.baixar_com_ytdlp('https://example.com', {'paths': {'home': folder}})
                spawn.assert_not_called()
                self.assertFalse(dl._download_lock.locked())

    async def test_deadline_kills_worker(self):
        original = asyncio.create_subprocess_exec
        processes = []
        async def launch(*args, **kwargs):
            if args[0] == 'taskkill':
                return await original(*args, **kwargs)
            process = await original(sys.executable, '-c', 'import sys,time; sys.stdin.readline(); time.sleep(60)', **kwargs)
            processes.append(process)
            return process
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(dl, '_download_lock', asyncio.Lock()), patch.object(dl.asyncio, 'create_subprocess_exec', side_effect=launch):
                with self.assertRaises(asyncio.TimeoutError):
                    await dl.baixar_com_ytdlp('https://example.com', {'paths': {'home': folder}}, timeout=.1)
                self.assertIsNotNone(processes[0].returncode)
                self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_cancel_kills_worker_and_removes_partial_files(self):
        original = asyncio.create_subprocess_exec
        processes = []
        async def launch(*args, **kwargs):
            if args[0] == 'taskkill':
                return await original(*args, **kwargs)
            process = await original(sys.executable, '-c', 'import sys,time; sys.stdin.readline(); time.sleep(60)', **kwargs)
            processes.append(process)
            return process
        with tempfile.TemporaryDirectory() as folder:
            event = threading.Event()
            with patch.object(dl, '_download_lock', asyncio.Lock()), patch.object(dl.asyncio, 'create_subprocess_exec', side_effect=launch):
                task = asyncio.create_task(dl.baixar_com_ytdlp('https://example.com', {'paths': {'home': folder}}, cancel_event=event))
                for _ in range(100):
                    if processes:
                        break
                    await asyncio.sleep(.02)
                self.assertTrue(processes)
                event.set()
                with self.assertRaises(dl.DownloadCancelled):
                    await asyncio.wait_for(task, 10)
            self.assertIsNotNone(processes[0].returncode)
            self.assertEqual(list(Path(folder).iterdir()), [])


class MetadataTests(unittest.TestCase):
    def test_worker_never_serializes_blank_errors(self):
        source = Path(dl.__file__).with_name('download_worker.py').read_text(encoding='utf-8')
        self.assertIn("str(exc).strip() or type(exc).__name__", source)

    def test_safe_options_disable_native_progress_output(self):
        self.assertTrue(dl._aplicar_opcoes_seguras({})['noprogress'])

    def test_facebook_ids_and_case_are_preserved(self):
        self.assertNotEqual(normalizar_link_social('https://facebook.com/watch/?v=1'), normalizar_link_social('https://facebook.com/watch/?v=2'))

    def test_fallback_preserves_cancel_hook(self):
        first, second = MagicMock(), MagicMock()
        first.__enter__.return_value.extract_info.side_effect = RuntimeError('first failed')
        with patch.object(dl.yt_dlp, 'YoutubeDL', side_effect=[first, second]) as factory:
            dl.processar_com_fallback('https://youtube.com/watch?v=1', {}, object(), object())
        self.assertEqual(len(factory.call_args_list[1].args[0]['progress_hooks']), 1)

    def test_public_text_and_photos_exclude_video_thumbnail(self):
        post = {'__typename': 'Story', 'message': {'text': 'Hello'}, 'attachments': [
            {'media': {'__typename': 'Photo', 'image': {'uri': 'https://scontent.fbcdn.net/photo.jpg'}}},
            {'media': {'__typename': 'Video', 'image': {'uri': 'https://scontent.fbcdn.net/thumb.jpg'}}},
        ]}
        result = parse_public_post('<script type="application/json">' + json.dumps(post) + '</script>')
        self.assertEqual(result['photos'], ['https://scontent.fbcdn.net/photo.jpg'])
        self.assertTrue(result['has_video'])
        self.assertEqual(result['text'], 'Hello')

    def test_text_only_post(self):
        post = {'__typename': 'Story', 'message': {'text': 'Public post'}}
        self.assertEqual(parse_public_post('<script type="application/json">' + json.dumps(post) + '</script>')['text'], 'Public post')

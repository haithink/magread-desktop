"""Cached official series artwork, with local typographic covers as a fallback."""
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import re
import tempfile
from io import BytesIO
from urllib.parse import urlsplit, urljoin

import requests

from PIL import Image, ImageDraw, ImageFont

from kandu import core_parsers as store

SIZE = (480, 640)
PALETTES = [('#193f35', '#a8bb95'), ('#263e50', '#a4b9c4'),
            ('#514431', '#c5b08a'), ('#3f3d50', '#b5afc8'), ('#334b46', '#aac1b3')]


@lru_cache(maxsize=24)
def _font(size, serif=False):
    windows = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    candidates = ([str(windows/'georgia.ttf'), 'DejaVuSerif.ttf',
        '/System/Library/Fonts/Supplemental/Georgia.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf']
        if serif else [str(windows/'segoeui.ttf'), 'DejaVuSans.ttf',
        '/System/Library/Fonts/Supplemental/Arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'])
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Older Pillow still has its built-in bitmap font.
        return ImageFont.load_default()


def _wrap(draw, text, font, width, max_lines):
    """Wrap by pixel width, including long words; ellipsize only when needed."""
    text = re.sub(r'\s+', ' ', str(text)).strip()[:1800]
    lines, line = [], ''
    for character in text:
        if line and draw.textlength(line+character, font=font) > width:
            split = line.rfind(' ')
            if split > len(line)//3:
                lines.append(line[:split].strip())
                line = line[split+1:]+character
            else:
                lines.append(line.strip())
                line = character.lstrip()
        else:
            line += character
    if line.strip():
        lines.append(line.strip())
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        tail = lines[-1].rstrip(' ,;:-')
        while tail and draw.textlength(tail+'...', font=font) > width:
            tail = tail[:-1]
        lines[-1] = tail.rstrip()+'...'
    return lines


def render(meta):
    """A stable palette per episode, with number, topic, guest and publication date."""
    identity = str(meta['id'])
    ink, accent = PALETTES[hashlib.sha256(identity.encode()).digest()[0] % len(PALETTES)]
    paper = '#f5f1e7'
    image = Image.new('RGB', SIZE, ink)
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 459, 619), outline=accent, width=1)
    series = re.sub(r'\s+Podcast$', '', meta.get('publication') or 'Lex Fridman Podcast', flags=re.I).upper()
    draw.text((42, 43), _wrap(draw, series, _font(21), 330, 1)[0], font=_font(21), fill=paper)
    draw.text((43, 73), 'P O D C A S T', font=_font(13), fill=accent)
    # A simple microphone mark, drawn locally instead of depending on emoji fonts.
    draw.rounded_rectangle((398, 43, 418, 78), radius=10, outline=accent, width=2)
    draw.arc((390, 53, 426, 88), 0, 180, fill=accent, width=2)
    draw.line((408, 89, 408, 99), fill=accent, width=2)
    draw.line((397, 100, 419, 100), fill=accent, width=2)
    number = str(meta.get('episodeNumber') or '')
    draw.text((39, 116), '#'+number if number else 'PODCAST', font=_font(82 if number else 48, True), fill=paper)
    draw.line((43, 231, 436, 231), fill=accent, width=2)
    title = re.sub(r'^#\d+\s*[·:—-]?\s*', '', str(meta.get('title') or 'Transcript'))
    title_font = _font(29, True)
    lines = _wrap(draw, title, title_font, 394, 5)
    if len(lines) == 5 and lines[-1].endswith('...'):
        title_font = _font(26, True)
        lines = _wrap(draw, title, title_font, 394, 5)
    for i, line in enumerate(lines):
        draw.text((43, 258+i*37), line, font=title_font, fill=paper)
    guest_font = _font(17)
    for i, line in enumerate(_wrap(draw, meta.get('guest') or '', guest_font, 394, 2)):
        draw.text((43, 480+i*24), line, font=guest_font, fill=accent)
    draw.line((43, 553, 436, 553), fill=accent, width=1)
    draw.text((43, 574), str(meta.get('publishedAt') or 'TRANSCRIPT'), font=_font(15), fill=paper)
    draw.text((331, 574), 'TRANSCRIPT', font=_font(13), fill=accent)
    return image


def ensure_cover(meta):
    """Create only a missing cover and atomically publish it for concurrent clients."""
    if meta.get('sourceType') != 'podcast':
        return None
    out = store.cover_file(meta['id'])
    if out.is_file():
        return str(out)
    image = render(meta)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=out.parent, suffix='.jpg', delete=False) as file:
            temp_path = Path(file.name)
        image.save(temp_path, 'JPEG', quality=85, optimize=True)
        os.replace(temp_path, out)
    finally:
        image.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return str(out)


def _official_image(spec, control=None):
    """Fetch only a built-in official host, validating each redirect and image."""
    target = spec['coverUrl']
    for _ in range(4):
        parts = urlsplit(target)
        if (parts.scheme != 'https' or parts.netloc.lower() != spec['host'] or
                parts.username or parts.password or parts.query or parts.fragment):
            raise ValueError('封面地址不属于播客官网')
        if control:
            control.check()
        with requests.get(target, timeout=(8, 20), stream=True, allow_redirects=False,
                headers={'User-Agent':'KanduWeb/0.1', 'Accept':'image/*'}) as response:
            if control:
                control.bind(response)
            try:
                if response.status_code in (301, 302, 303, 307, 308):
                    target = urljoin(target, response.headers.get('Location', ''))
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    if control:
                        control.check()
                    size += len(chunk)
                    if size > 10 * 1024**2:
                        raise ValueError('官网封面超过大小限制')
                    chunks.append(chunk)
                with Image.open(BytesIO(b''.join(chunks))) as original:
                    if original.width * original.height > 25_000_000:
                        raise ValueError('官网封面尺寸过大')
                    original.thumbnail((640, 640))
                    return original.convert('RGB')
            finally:
                if control:
                    control.unbind()
    raise ValueError('官网封面重定向过多')


def ensure_series_cover(spec, download=False, control=None):
    """Network is allowed only in a background import, never in page requests.

    A source marker distinguishes official artwork from an offline fallback so
    the next import can retry a previously unavailable website.
    """
    out = store.cover_file('podcast-series-' + spec['id'])
    marker = out.with_suffix('.source.json')
    saved = store._json_read(marker) or {}
    if out.is_file() and (not download or saved.get('url') == spec.get('coverUrl')):
        return str(out)
    artwork, official = None, False
    if download and spec.get('coverUrl'):
        try:
            artwork = _official_image(spec, control)
            official = True
        except (requests.RequestException, ValueError, OSError):
            if control:
                control.check()
    if artwork is None:
        if out.is_file():
            return str(out)
        artwork = render(dict(id='podcast-series-'+spec['id'], publication=spec['name'], title=spec['name']))
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=out.parent, suffix='.jpg', delete=False) as file:
            temp_path = Path(file.name)
        artwork.save(temp_path, 'JPEG', quality=88, optimize=True)
        os.replace(temp_path, out)
        store._atomic_json_write(marker, dict(url=spec['coverUrl'] if official else '', official=official))
    finally:
        artwork.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return str(out)

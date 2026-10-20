import hashlib
import re


def digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def parse_segments(content: str, format: str = 'text') -> list[dict]:
    if len(content.encode('utf-8')) > 5 * 1024 * 1024:
        raise ValueError('CONTENT_TOO_LARGE')
    if re.search(r'<\s*(script|iframe|object|html)\b', content, re.I):
        raise ValueError('EXECUTABLE_CONTENT')
    content = content.replace('\r\n', '\n').replace('\r', '\n').lstrip('\ufeff')
    segments = []
    if format in ('srt', 'vtt'):
        def ms(t):
            bits = t.replace(',', '.').split(':')
            return round(sum(float(v) * (60 ** i) for i, v in enumerate(reversed(bits))) * 1000)
        pattern = r'(?m)^(?P<start>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+(?P<end>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})[^\n]*\n(?P<text>.*?)(?=\n\s*\n|\Z)'
        for m in re.finditer(pattern, content, re.S):
            start, end = ms(m['start']), ms(m['end'])
            if end < start:
                raise ValueError('INVALID_TIMESTAMP')
            segments.append({'text': m['text'].strip(), 'start_ms': start, 'end_ms': end})
        if not segments:
            raise ValueError('INVALID_SUBTITLE')
    else:
        segments = [{'text': t.strip(), 'paragraph_no': i + 1} for i, t in enumerate(re.split(r'\n\s*\n', content)) if t.strip()]
    for i, seg in enumerate(segments):
        seg.update(segment_id=f'seg_{i+1}', content_hash=digest(seg['text']))
    if not segments:
        raise ValueError('EMPTY_CONTENT')
    return segments


def validate_anchor(segments: list[dict], anchor: dict):
    seg = next((s for s in segments if s['segment_id'] == anchor.get('segment_id')), None)
    if not seg:
        raise ValueError('UNKNOWN_SEGMENT')
    start, end = anchor.get('char_start'), anchor.get('char_end')
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(seg['text']):
        raise ValueError('INVALID_OFFSET')
    quote = seg['text'][start:end]
    if quote != anchor.get('quote') or digest(quote) != anchor.get('quote_hash'):
        raise ValueError('QUOTE_MISMATCH')
    for key in ('start_ms', 'end_ms'):
        if key in anchor and anchor[key] != seg.get(key):
            raise ValueError('TIMESTAMP_MISMATCH')
    return seg

"""Small paid protocol smoke test; does not replace real Kiro UI acceptance."""
import argparse
import json
from pathlib import Path
import struct
import urllib.request
import uuid
import zlib


def events(data):
    offset = 0
    while offset < len(data):
        if len(data) - offset < 16:
            raise ValueError('Truncated frame')
        total, headers, crc = struct.unpack_from('>III', data, offset)
        if total < 16 or total > 16 * 1024 * 1024 or headers > total - 16:
            raise ValueError('Invalid frame length')
        frame = data[offset:offset + total]
        if len(frame) != total or zlib.crc32(frame[:8]) != crc:
            raise ValueError('Invalid prelude CRC')
        if zlib.crc32(frame[:-4]) != struct.unpack('>I', frame[-4:])[0]:
            raise ValueError('Invalid message CRC')
        yield json.loads(frame[12 + headers:-4])
        offset += total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model')
    parser.add_argument('--effort', choices=['low', 'medium', 'high', 'xhigh'])
    args = parser.parse_args()
    model = args.model or json.loads(Path(__file__).with_name('config.json').read_text(encoding='utf-8-sig'))['defaultModel']
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, payload=None, target=None):
        headers = {'Content-Type': 'application/json'}
        if target:
            headers['x-amz-target'] = 'AmazonCodeWhispererStreamingService.' + target
        req = urllib.request.Request('http://127.0.0.1:20129' + path,
            data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        with opener.open(req, timeout=120) as response:
            data = response.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:
                raise ValueError('Response too large')
            return response.headers.get('Content-Type', ''), data

    catalog = json.loads(request('/List-Available-Models')[1])
    rpc = json.loads(request('/', {}, 'ListAvailableModels')[1])
    if catalog != rpc:
        raise ValueError('GET and RPC catalog mismatch')
    entry = next(m for m in catalog['models'] if m['modelId'] == model)
    if args.effort:
        choices = entry['additionalModelRequestFieldsSchema']['properties']['reasoning']['properties']['effort']['enum']
        if args.effort not in choices:
            raise ValueError('Effort absent from catalog')
    payload = {'conversationState': {'conversationId': str(uuid.uuid4()), 'chatTriggerType': 'MANUAL',
        'currentMessage': {'userInputMessage': {'content': 'Reply with exactly OK. Do not use tools or edit files.',
                                              'modelId': model, 'origin': 'AI_EDITOR'}}}}
    if args.effort:
        payload['additionalModelRequestFields'] = {'reasoning': {'effort': args.effort}}
    content_type, data = request('/generateAssistantResponse', payload, 'GenerateAssistantResponse')
    if 'eventstream' not in content_type:
        raise ValueError('Expected AWS EventStream')
    frames = list(events(data))
    answer = ''.join(e.get('content', '') for e in frames)
    if answer.strip() != 'OK' or sum(e.get('stopReason') == 'END_TURN' for e in frames) != 1:
        raise ValueError('Expected exactly OK and one END_TURN')
    if any(e.get('toolUseId') for e in frames):
        raise ValueError('Unexpected tool call')
    print('PASS: catalogs, model, CRC, OK, END_TURN. Verify effort forwarding in local log and test real Kiro separately.')


if __name__ == '__main__':
    main()

"""Validate aggregate audit JSON and optionally compare a previous snapshot.

No database, network, or write access. stdout is the report; stderr contains only
fixed error messages. Callers choose storage/retention. Raw input is never echoed.
"""
import argparse
import json
import sys
from datetime import datetime

MAX_BYTES = 16_384
METRICS = frozenset('''ingredients products aliases unverifiedIngredients
uncertainIdentities emptyNames namesWithoutLetters headerCandidates
duplicateNameGroups duplicateNameExtraRows eNumberIngredients invalidENumberFormat
missingDescriptions eNumbersMissingDescriptions missingReferenceIds
productsWithMissingReferences unreferencedIngredients reviewedBgTranslations'''.split())


def read_snapshot(stream):
    raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Snapshot exceeds size limit')
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {'schemaVersion', 'generatedAt', 'metrics'}:
        raise ValueError('Unexpected snapshot fields')
    if type(data['schemaVersion']) is not int or data['schemaVersion'] != 1:
        raise ValueError('Unsupported snapshot version')
    timestamp = data['generatedAt']
    if not isinstance(timestamp, str) or len(timestamp) != 20:
        raise ValueError('Invalid timestamp')
    datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%SZ')
    metrics = data['metrics']
    if not isinstance(metrics, dict) or set(metrics) != METRICS:
        raise ValueError('Unexpected metric fields')
    if any(type(v) is not int or not 0 <= v <= 10**15 for v in metrics.values()):
        raise ValueError('Invalid metric value')
    return data


def build_report(current, previous=None):
    result = dict(current)
    result['interpretation'] = 'Review signals only; not a scientific quality score or repair authorization.'
    result['notChecked'] = ['scientific accuracy', 'translation hash freshness',
                            'summary coverage', 'candidate queue', 'E-number regulatory validity']
    if previous is not None:
        if previous['generatedAt'] >= current['generatedAt']:
            raise ValueError('Previous snapshot must be older')
        result['delta'] = {key: current['metrics'][key] - previous['metrics'][key]
                           for key in sorted(METRICS)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', help='Earlier raw snapshot from the SAME database; never a formatted report')
    args = parser.parse_args()
    try:
        current = read_snapshot(sys.stdin.buffer)
        previous = None
        if args.previous:
            with open(args.previous, 'rb') as stream:
                previous = read_snapshot(stream)
        print(json.dumps(build_report(current, previous), ensure_ascii=True, sort_keys=True, indent=2))
    except (ValueError, TypeError, OSError):
        print('Audit report rejected: invalid, incompatible, oversized, or unreadable snapshot.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

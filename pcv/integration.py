"""Conservative content grouping; observations remain independently traceable."""
import hashlib
import json
import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def integrate(observations):
    """Input: normalized observations with explicit platform/content identities.

    A homepage URL or a matching title is insufficient to identify content.
    Cross-platform semantic matches must be proposed separately for review.
    """
    groups = {}
    seen = set()
    for observation in observations:
        observation_id = observation.get('observation_id')
        platform = observation.get('platform')
        if not isinstance(observation_id, str) or not observation_id:
            raise ValueError('observation_id required')
        if not isinstance(platform, str) or not platform:
            raise ValueError('platform required')
        source_id = observation.get('source_id')
        if not isinstance(source_id, (str, int)) or isinstance(source_id, bool):
            raise ValueError('source_id required')
        scoped_observation = (str(source_id), platform, observation_id)
        serialized = json.dumps(observation, ensure_ascii=False, sort_keys=True)
        revision = hashlib.sha256(serialized.encode()).hexdigest()
        if (scoped_observation, revision) in seen:
            continue
        seen.add((scoped_observation, revision))
        content_id = observation.get('platform_content_id')
        if content_id is not None and (not isinstance(content_id, str) or not content_id):
            raise ValueError('platform_content_id must be nonempty string or null')
        # Keep authorization scopes separate even for identical public content.
        key = (str(source_id), platform, 'content' if content_id else 'observation',
               content_id or observation_id)
        group = groups.setdefault(key, {
            'source_id': source_id, 'platform': platform,
            'platform_content_id': content_id,
            'identity_status': 'platform_id' if content_id else 'unresolved',
            'observations': [], 'available_modalities': [],
        })
        modalities = observation.get('available_modalities', [])
        if not isinstance(modalities, list) or any(
            item not in {'text', 'image', 'audio', 'video', 'transcript'} for item in modalities):
            raise ValueError('unsupported available_modalities')
        group['observations'].append(json.loads(serialized))
        group['available_modalities'] = sorted(set(group['available_modalities']) | set(modalities))
    return list(groups.values())


def from_database(database, source_id):
    connection = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    observations = []
    try:
        rows = connection.execute('''SELECT e.id,e.content,s.expires_at FROM evidence e
            JOIN sources s ON s.id=e.source_id WHERE s.id=? AND s.status='active'
            ORDER BY e.id''', (source_id,))
        for row in rows:
            if row['expires_at']:
                expiry = datetime.fromisoformat(row['expires_at'].replace('Z', '+00:00'))
                if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
                    continue
            try:
                record = json.loads(row['content'])
            except ValueError:
                continue
            if not isinstance(record, dict) or record.get('event_type') != 'page_observed':
                continue
            observations.append({**record, 'source_id': source_id,
                'observation_id': str(row['id']), 'evidence_id': row['id'],
                'platform': record.get('platform', 'browser'),
                'available_modalities': record.get('available_modalities', ['text'])})
        return integrate(observations)
    finally:
        connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Group authorized browser evidence without deleting observations')
    parser.add_argument('--db', default='.pcv/vault.db')
    parser.add_argument('--source', type=int, required=True)
    parser.add_argument('--output', type=Path, help='Optional private JSON export including content')
    args = parser.parse_args()
    groups = from_database(args.db, args.source)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'groups': len(groups), 'observations': sum(len(g['observations']) for g in groups),
        'unresolved_groups': sum(g['identity_status'] == 'unresolved' for g in groups)}, indent=2))

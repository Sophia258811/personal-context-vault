"""Conservative content grouping; observations remain independently traceable."""
import hashlib
import json


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

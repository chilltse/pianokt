"""Non-sensitive attempt context shared by local replay and Worker results."""


def attempt_context(row):
    metadata = row.get('metadata') or {}
    settings = metadata.get('settings') or {}
    activity = (row.get('finalization') or {}).get('activity') or {}
    visit_id = row.get('visit_id') or (activity.get('visit') or {}).get('id')
    keyboard = settings.get('midi_keyboard_used')
    return dict(attempt_id=str(row['id']), song_id=row.get('song_id'),
                midi_keyboard_used=keyboard if isinstance(keyboard, bool) else None,
                visit_id=str(visit_id) if visit_id is not None else None)


def attach_attempt_context(result, context):
    # Counts are derived from the actual labeling output, not client estimates.
    result.update({key: context.get(key) for key in
                   ('song_id', 'midi_keyboard_used', 'visit_id')})
    result['reference_events_count'] = (len(result['events_data']['onset_seconds'])
                                        if 'events_data' in result else None)
    return result

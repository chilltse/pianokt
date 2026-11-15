"""Explicit accuracy units; legacy summary.accuracy is never reinterpreted."""
def backend_event_accuracy(events_data, quality_valid):
    labels = events_data.get('reject_reason', {}).get('event', [])
    return sum(label == 0 for label in labels) / len(labels) if labels and quality_valid else None

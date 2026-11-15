# Accuracy metrics

`frontend_note_accuracy` is the final client `(Perfect + Good) / (Perfect + Good + Miss + Error)`, uploaded in attempt settings in [0,1]. Real-time scoring and Challenge Success are unchanged. It is projected independently to challenge_recordings.frontend_note_accuracy.

`backend_event_accuracy` is the fraction of zero labels in events_data.reject_reason.event. Empty labels or failed alignment quality produce null. Summary, result.json, pipeline summaries and dataset export carry both explicit metrics. Note counts remain available; new results do not emit the ambiguous legacy accuracy field.

challenge_recordings.accuracy_pct is the event fraction times 100 for new results, including null for invalid results. accuracy_metric identifies the semantics. Existing historical percentages are not backfilled or relabeled; legacy result exports leave the new metrics null. Old result objects remain immutable, and the output contract identity changes for new results.

Apply migration 021_explicit_accuracy_metrics.sql before rolling out these application changes. No migration was applied to production. Historical rows with null accuracy_metric must not be treated as event accuracy in analysis. Frontend accuracy absent in older metadata remains null rather than being inferred from a backend score.

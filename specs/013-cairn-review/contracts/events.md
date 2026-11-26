# Events contract

Webhook route: existing project webhook; accepted new event types: GitHub `pull_request` (opened, synchronize, reopened), GitLab `merge_request` (open, update), Gitea `pull_request`. Same signature checks as push. Ignored: other actions, drafts (configurable), forks without access. Idempotency key: (project, head_sha, base_sha). Response is acknowledged immediately; the review runs in the background and its progress is broadcast on the existing hub.

Timeline events written: `review.completed` (with counts), `review.superseded`, `review.finding_decided`.

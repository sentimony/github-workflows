# AGENTS.md

1. Native Claude Code installation uses the moving `latest` channel at the owner's
   explicit request. Trusting the official remote installer and latest CLI is an
   accepted trade-off, not a defect for review: updates must be available without
   waiting for a new action pin. Preserve the action SHA and the install step's
   secret-free, read-only job context. The installed version is recorded in the
   step summary for diagnosis; a pinned-version override is outside this contract.

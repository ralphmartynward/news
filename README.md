# Toulouse Daily Digest

Daily email digest of Toulouse news, events, and culture, delivered to Gmail at 07:00 Europe/Paris.

See [toulouse_digest_spec.md](toulouse_digest_spec.md) for the full specification.

## Local run

```powershell
py src/main.py
```

## Deployment

GitHub Actions runs `src/main.py` on a daily cron (`0 5 * * *` UTC).
Required repo secrets: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `RESEND_API_KEY`, `RECIPIENT_EMAIL`.

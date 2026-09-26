# Security

Report vulnerabilities privately. Do not open a public issue with secrets, keys, or a working exploit.

## What this project stores

- Optional API keys in the process environment or a local `.env` (gitignored)
- Optional TTS cache under `data/voices/` (gitignored)
- An empty `data/archive/` on a clean checkout

## Rules of thumb

- Never commit keys, tokens, or home-directory paths.
- Generation and rumor scanning stay opt-in.
- Demo mode must not call vendor APIs.

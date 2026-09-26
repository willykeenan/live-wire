# Live Wire

**Watch the live channel: [livewire.show](https://livewire.show)** · Try the keyless demo on [Hugging Face](https://huggingface.co/spaces/willykeenan/live-wire)

**A 24/7 AI satirical news channel with cartoon anchors, mouth sync, LLM-written scripts, and ElevenLabs voices.**

*A KE Studios product. Source: [github.com/willykeenan/live-wire](https://github.com/willykeenan/live-wire).*

Live Wire is a local studio: fictional cartoon anchors read a rundown, lips move on character timestamps, and you can optionally plug in a language model plus ElevenLabs. The first run is a **demo mode** that needs no keys.

## Content policy

- Satire must be labelled. The demo chyron, trust bar, and canned scripts all say **SATIRE**.
- Never present rumors as confirmed. Rumor scanning is optional and **off by default**.
- No trading, investment, or betting advice. Business and markets stories are news and satire only; the model prompts forbid buy/sell calls and price targets.
- Operators are responsible for what they broadcast. This software does not make you a newsroom and does not verify claims.

## 60-second quickstart

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
LIVE_WIRE_PORT=8899 LIVE_WIRE_NO_BROWSER=1 LIVE_WIRE_DEMO=1 ./.venv/bin/python app/server.py
```

Open `http://127.0.0.1:8899`. Press **Start demo**. Cartoon anchors play canned satire scripts with mouth sync. No `ANTHROPIC_API_KEY` or `ELEVENLABS_API_KEY` is required.

Optional read-only editorial preview:

```bash
./.venv/bin/python app/gate1_server.py --port 8901
```

### Going live (optional)

```bash
cp .env.example .env
# edit .env: LIVE_WIRE_GENERATION_ENABLED=1, plus ANTHROPIC_API_KEY (or have the `claude` CLI on PATH)
# and optionally ELEVENLABS_API_KEY for real voices
./.venv/bin/python app/server.py
```

With generation on, demo mode switches off and the background loops start by
themselves: the wire loop pulls public RSS feeds, and while a browser has the
studio open the rundown loop asks the model for a fresh rundown (at most every
few minutes, never with an empty wire). Until the first rundown lands the studio
shows a "standing by" card. With `ELEVENLABS_API_KEY` set the anchors speak;
without it they mime silently.

## Features

- Cartoon SVG anchors with viseme mouth sync
- Demo rundown of invented companies and places (Harbor Grid, Aster Labs, Northstar Energy, Vale Crossing)
- Optional LLM scripts via `ANTHROPIC_API_KEY` (official API) or the `claude` CLI
- Optional ElevenLabs voices via `ELEVENLABS_API_KEY`
- Empty on-disk archive out of the box (`data/archive/`)
- Public site in `site/` with a GitHub link to this repo
- No markets desk, trading signals, charts, prediction-market odds, or investment advice in this edition

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `LIVE_WIRE_GENERATION_ENABLED` | `0` | `1` allows LLM scripts + vendor TTS, turns demo off, and starts the background loops |
| `LIVE_WIRE_DEMO` | on when generation is off | `1` forces the canned demo even with generation on; `0` disables it |
| `LIVE_WIRE_START_LOOPS` | follows generation | `0`/`1` overrides whether the wire, rundown, breaking and intel loops run |
| `ANTHROPIC_API_KEY` | unset | Official Anthropic API for scripts (otherwise the `claude` CLI on PATH) |
| `ELEVENLABS_API_KEY` | unset | ElevenLabs TTS (live mode only) |
| `LIVE_WIRE_RUMORS_ENABLED` | `0` | Optional rumor scout (off by default) |
| `LIVE_WIRE_HOST` | `127.0.0.1` | Bind address. `0.0.0.0` exposes the studio to your network; see below |
| `LIVE_WIRE_ALLOWED_HOSTS` | unset | Comma-separated extra `Host` names to accept (e.g. your LAN name) |
| `LIVE_WIRE_PORT` | `8899` | Port |
| `LIVE_WIRE_NO_BROWSER` | unset | Skip opening a browser |
| `LIVE_WIRE_VOICE_CACHE` | `data/voices/` | TTS cache directory |

Copy `.env.example` to `.env` if you want keys in a file. The server loads project `.env` only. It does not read home-directory key files. Variables already set in your shell win over `.env`.

## Security and privacy

- Do not commit `.env`, voice caches, or generated archives.
- Keys come from environment variables. There is no home-directory key file lookup.
- Demo sample data uses invented names only.
- The rumor ledger ships empty.
- The server binds `127.0.0.1` and has no login. Every request must carry a loopback `Host` (or one you list in `LIVE_WIRE_ALLOWED_HOSTS`), which blocks DNS-rebinding pages.
- Routes that can spend credits or start work (`POST /api/tts`, `POST /api/rumors/scan`, `GET /api/media`) also refuse cross-site browser requests (`Origin` / `Sec-Fetch-Site`), and POSTs must be `application/json`. TTS only accepts the roster's voice ids, caps text at 1,500 characters, and is rate limited. `/api/media` (YouTube lookup) only runs with generation on.
- Binding `LIVE_WIRE_HOST=0.0.0.0` lets anyone on your network who can reach the port use your keys. Only do it on a network you trust, and add the name you browse to in `LIVE_WIRE_ALLOWED_HOSTS`.
- Outbound feed fetches verify TLS certificates.

## Tests

```bash
python3 -m pytest -q tests
```

## Roadmap

- Operator-authenticated control room
- Durable rundown storage
- Accessibility pass on the studio chrome
- Optional rumor desk with human confirmation only

## Credits

KE Studios. Cartoon anchors, voices, and commentary are generated. Not affiliated with any existing news organization.

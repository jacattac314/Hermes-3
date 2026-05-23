# Hermes2 Android Access

Hermes2 now ships an Android-friendly PWA at `/mobile`. The intended path is a private network, preferably Tailscale, from Android to this Mac.

## Run The Mac Server

Build the web app once, then start Hermes2 on a private interface. Hermes2 refuses non-loopback hosts such as `0.0.0.0` unless `HERMES2_MOBILE_TOKEN` is set.

```bash
cd "/Users/jack/Documents/Hermes 2.0"
cd frontend && npm run build && cd ..
grep -q '^HERMES2_MOBILE_TOKEN=..' "$HOME/.hermes/.env" 2>/dev/null || \
  printf 'HERMES2_MOBILE_TOKEN=%s\n' "$(openssl rand -base64 24)" >> "$HOME/.hermes/.env"
uv run hermes2 serve --host 0.0.0.0 --port 8765 --profile default
```

Open this from Android Chrome over Tailscale or another trusted private network:

```text
http://<mac-tailscale-name-or-ip>:8765/mobile
```

Chrome can install it from the browser menu as a home-screen app.

## Optional Mobile Token

Set a token in `~/.hermes/.env` to require Android requests to prove they are allowed before chat, workflow runs, tools, history, or reports work.

```bash
openssl rand -base64 24
```

Add the generated value to `~/.hermes/.env`:

```text
HERMES2_MOBILE_TOKEN=<generated-value>
```

Restart `hermes2 serve`, open `/mobile`, and paste the token into the token field. The token is stored only in that browser's local storage and is sent as `Authorization: Bearer ...` plus `X-Hermes2-Mobile-Token`.

## Optional Android Notifications

Hermes2 can send best-effort workflow completion notices through ntfy. Use a private topic and keep it in `~/.hermes/.env`.

```text
HERMES2_NTFY_TOPIC=<private-topic>
HERMES2_NTFY_TOKEN=
```

Then set `mobile.ntfy.enabled: true` in `config/config.yaml` and restart the server.

## Safety Model

The mobile PWA can chat with Hermes2 and start the same command-gated workflows as the desktop UI. It does not bypass approval rules. Risky commands still fail closed from non-interactive server mode unless the request explicitly enables bypass approval.

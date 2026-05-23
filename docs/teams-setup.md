# Hermes2 Microsoft Teams setup

Hermes2 now exposes a Teams Outgoing Webhook endpoint:

```text
POST /teams/outgoing
```

This is the lightest Teams conversation path. In a Teams channel, users mention the webhook name, Teams sends Hermes2 a JSON activity, and Hermes2 replies with a Teams message response.

## Requirements

Teams must be able to reach Hermes2 over HTTPS. For local development, expose the local server with a tunnel:

```bash
uv run hermes2 serve --host 127.0.0.1 --port 8765 --profile default

# Example only. Use the tunnel you trust.
cloudflared tunnel --url http://127.0.0.1:8765
```

Your callback URL will look like:

```text
https://YOUR-TUNNEL.example.com/teams/outgoing
```

## Create the outgoing webhook in Teams

1. Open the team/channel where Hermes2 should answer.
2. Open the team menu and choose **Manage team**.
3. Open **Apps**.
4. Choose **Create an outgoing webhook**.
5. Use a name such as `Hermes2`.
6. Paste the HTTPS callback URL ending in `/teams/outgoing`.
7. Save the webhook and copy the HMAC security token that Teams shows.

Store the HMAC token in `~/.hermes/.env`:

```bash
printf '\nHERMES2_TEAMS_OUTGOING_SECRET=%s\n' 'PASTE_TEAMS_HMAC_TOKEN_HERE' >> ~/.hermes/.env
```

Restart Hermes2 after changing `.env`.

## Use it

In the configured Teams channel:

```text
@Hermes2 summarize what you can do
```

Outgoing webhooks are synchronous. Teams expects a quick response, so Hermes2 uses a short Teams timeout. Longer workflows should be started from the Hermes2 desktop app.

## Important limitations

Outgoing Webhooks are team/channel scoped and require an `@mention`. They do not work as a personal/private chat bot. If the provided `teams.live.com` community does not expose outgoing webhook creation, the next implementation step is a full Microsoft Teams bot app with Azure Bot registration and a Teams app package.

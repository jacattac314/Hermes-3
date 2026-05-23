# Hermes2 Workbench Frontend Specification

## Product Shape

Hermes2 Workbench is a local-first control surface for the Hermes2 runtime. It should feel like an operator console, not a marketing site: compact, fast, and built around repeated use. The first screen should show the current runtime state and let the user immediately chat, run a workflow, inspect logs, or start the local server.

The frontend should assume Hermes2 is running on the same Mac and talks to the local server started with:

```bash
uv run hermes2 serve --host 127.0.0.1 --port 8765 --profile default
```

Primary API surface:

- `GET /health`
- `GET /models?profile=code`
- `POST /chat`
- `POST /run`

## Layout

The app uses a three-column desktop layout with a responsive two-level mobile layout.

Left rail:

- Profile switcher: `default`, `code`, `research`.
- Primary views: Chat, Workflows, Runs, Models, Settings.
- Runtime status indicator: server reachable, LM Studio reachable, current model, Google Gemini/OpenAI/Anthropic fallback availability.
- Compact command launcher button for common actions.

Main panel:

- The active task surface.
- Chat view, workflow builder, run detail, log viewer, or model inspector depending on navigation.
- Main content should be dense but readable, with restrained spacing and no nested cards.

Right inspector:

- Context about the selected run, model, command, profile, or workflow.
- Approval status, skipped model candidates, and command output summary.
- Collapsible on laptop widths and hidden behind an inspector button on mobile.

## Visual System

Style direction:

- Background: neutral off-white or very dark neutral, depending on OS preference.
- Accent color: use one clear action color for selected state and primary buttons, plus separate semantic colors for success, warning, and danger.
- Avoid a one-color blue or purple dashboard. Model state, approval state, and command state should have visually distinct signals.
- Cards are only used for repeated run rows and modal dialogs. Main sections are unframed panels.
- Border radius: 6 to 8 px.
- Typography: compact system font stack, no viewport-scaled text.

Interaction controls:

- Use icon buttons for refresh, copy, open report, stop server, and clear chat.
- Use segmented controls for profile selection and model candidate view.
- Use toggles for `bypass_approvals`, server mode, and auto-refresh.
- Use checkboxes for choosing workflow commands before execution.
- Use tabs inside a run detail only for `Summary`, `Trace`, `Report`, and `Commands`.

## View: Runtime Overview

This is the default screen after load.

Top status band:

- Hermes2 server: reachable or offline.
- LM Studio: reachable or offline.
- Active profile.
- Effective model: provider, model id, base URL.
- Fallbacks: skipped or available.
- Google Gemini, OpenAI, and Anthropic keys: show `set` or `empty`, never show values.

Main content:

- Recent runs from `~/.hermes/logs/hermes2/`.
- Quick actions:
  - Start chat with current profile.
  - Run `default_task`.
  - Run `code_build` against `/Users/jack/Documents/Hermes 2.0`.
  - Open latest Markdown report.

Empty states:

- If server is offline, show the exact command to start it.
- If LM Studio is offline, show base URL and the failed endpoint.
- If cloud keys are empty, show that local-first mode is still available.

## View: Chat

The chat view is for lightweight interaction with the current profile. It must not imply shell execution.

Elements:

- Profile segmented control.
- Model badge showing provider and model id.
- Message transcript.
- Prompt composer with send button.
- `/model` style inspector showing current fallback chain.
- Clear chat button.

Behavior:

- Send `POST /chat` with `message`, `profile`, `temperature`, and `max_tokens`.
- Show selected model returned by the server.
- Show skipped fallbacks in a small details panel.
- If all candidates fail, show the exact failure summary without exposing secrets.

Guardrail copy:

- Chat can answer and plan.
- Workflow runs execute command-gated tasks.
- Shell commands are only run through `code_build` or another workflow with explicit commands.

## View: Workflows

This view turns `workflows.yaml` into an operator form.

Workflow list:

- `default_task`: general planning and reporting.
- `code_build`: repo review and command-gated validation.

Workflow form:

- Profile selector.
- Workflow selector.
- Input textarea.
- Workspace path field.
- Repeated command rows for `--command`.
- Risk preview beside each command.
- Run button.

Approval behavior:

- Safe commands can run immediately.
- Risky commands are marked before submission.
- In browser/server mode, risky commands should be blocked unless `bypass_approvals` is explicitly enabled.
- The bypass toggle should require a confirmation modal with the exact risky commands listed.

Submission:

Send `POST /run`:

```json
{
  "profile": "code",
  "workflow": "code_build",
  "workspace": "/Users/jack/Documents/Hermes 2.0",
  "input": "Validate repo state",
  "commands": ["git status --short"],
  "bypass_approvals": false
}
```

## View: Runs

The Runs view is a log and report browser.

Run table:

- Time.
- Workflow.
- Profile.
- Status.
- Selected model.
- Command count.
- Approval count.
- Report link.

Run detail:

- Summary: final status, input, selected models, skipped candidates.
- Trace: JSONL events rendered as a chronological timeline.
- Report: Markdown report rendered in-app.
- Commands: stdout, stderr, return code, approval status.

Rules:

- Redacted values must stay redacted.
- Never display full API keys.
- Long command output should be collapsed by default.

## View: Models

The Models view explains routing, not just model names.

Sections:

- Active profile.
- Effective candidate chain.
- Selected local model and reason.
- Rejected model overrides, such as stale `QWEN_MODEL=qwen-local`.
- Skipped cloud fallbacks with credential reasons.
- Raw `/v1/models` list from LM Studio.

Actions:

- Refresh models.
- Copy selected model id.
- Open LM Studio base URL check.
- Set local override instruction, displayed as a command or `.env` line without editing secrets in the browser.

## View: Settings

Settings should be mostly read-only at first.

Sections:

- Paths:
  - Hermes home.
  - Log directory.
  - Report directory.
  - Repo config directory.
- Runtime:
  - Server host and port.
  - Request timeout.
  - Command timeout.
  - Workspace allow-list.
- Keys:
- `GEMINI_API_KEY`: set or empty.
- `OPENAI_API_KEY`: set or empty.
- `ANTHROPIC_API_KEY`: set or empty.
  - `LMSTUDIO_API_KEY`: set or empty.
  - Values are never shown.

Future editable settings:

- Preferred local model.
- Profile definitions.
- Workflow descriptions.
- Approval patterns.

## Data Flow

Startup:

1. Call `GET /health`.
2. Load profiles and model candidate chain from the health payload.
3. Call `GET /models` for the active profile.
4. Populate status, profile selector, and model inspector.

Chat:

1. User sends a message.
2. UI posts to `/chat`.
3. Server resolves local-first model chain.
4. UI renders response, selected model, and skipped fallbacks.

Workflow:

1. User chooses profile and workflow.
2. UI validates workspace and command rows client-side.
3. UI posts to `/run`.
4. Server validates profile workflow allow-list.
5. Server writes JSONL and Markdown artifacts.
6. UI links directly to returned artifact paths.

## Frontend Implementation Recommendation

Use a small Vite React app with TypeScript.

Suggested structure:

```text
frontend/
  package.json
  src/
    main.tsx
    App.tsx
    api/hermes2.ts
    components/
      AppShell.tsx
      StatusBand.tsx
      ProfileSwitcher.tsx
      ModelBadge.tsx
      RiskBadge.tsx
      CommandEditor.tsx
      RunTimeline.tsx
    views/
      OverviewView.tsx
      ChatView.tsx
      WorkflowsView.tsx
      RunsView.tsx
      ModelsView.tsx
      SettingsView.tsx
```

The first implementation should use simple fetch calls and in-memory state. Add persisted UI preferences later only if repeated use proves it is needed.

## MVP Acceptance Criteria

- The app opens to a runtime overview and confirms Hermes2 server and LM Studio status.
- It can send a chat message through LM Studio and display the selected model.
- It can run `code_build` with `git status --short`.
- It shows the returned JSONL and Markdown report paths.
- It shows cloud key status as set or empty without exposing values.
- It blocks risky commands in browser mode unless bypass is explicitly enabled.
- It remains usable on a laptop-width viewport without overlapping text.

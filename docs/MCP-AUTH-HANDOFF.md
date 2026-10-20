# modkvm ongoing MCP authentication

The harness does not need an Ubuntu administrator password to call the existing
MCP server. It needs the existing web/MCP URL and the existing web access token,
provided locally to its MCP client. The extra restricted SSH account is not part
of this architecture and cannot retrieve camera frames with its installed grant.

`capture_existing_mcp.py` is a one-shot setup diagnostic. It uses the human's
ordinary `ismail@192.168.4.36` SSH login so the existing web token stays on Ubuntu:
the remote script authenticates to the already-running loopback gateway and
returns only the selected C7 image/metadata over encrypted SSH. The completed
2026-10-19 18:53:28 UTC test returned a fresh native MCP JPEG from that card,
but visual inspection shows a black image with no desktop or no-signal text.
The pipeline returned an image; an active HDMI picture remains unverified.
It does not add
an account, key, tunnel, listener, input bridge or harness dependency. The SSH
password prompt belongs to that diagnostic transport, not to MCP itself.

No authenticated MCP connector is available to this Codex task. The inspected
Hermes config has no MCP section; the small OpenCode JSONC file was not parsed,
so its complete state is not asserted. This task has not copied Ubuntu's existing
web token to the Mac or exposed it. A separate preexisting Mac checkout token
config was deliberately excluded from the Pontic consolidation; its identity
is not assumed to match Ubuntu's credential. Do not infer that the working Ubuntu MCP endpoint is
broken from this task's lack of a connection.

## Smallest ongoing handoff

1. Use `opencode.modkvm.remote.example.json` as a disabled review example for a
   dedicated modkvm session. Keep the provider/model setup the user manages.
   Copy only the `mcp.modkvm` entry when merging with an existing client config;
   the example's global `permission` deny rule is for an isolated control session
   and must not replace permissions for unrelated projects. The example contains
   no credential and is not installed as active client configuration.
2. Confirm an existing encrypted transport/endpoint. The observed LAN endpoint
   is `http://192.168.4.36:8800/mcp`; this is plain HTTP and does not encrypt
   bearer credentials or frames. The example deliberately uses an inert HTTPS
   placeholder. Replace it locally only with a confirmed existing trusted HTTPS
   endpoint that reaches the same server. No such endpoint has been verified
   in this task, so ongoing activation is pending that transport detail. No new
   proxy, relay, tunnel, public listener, certificate or credential is created.
   Do not put the token in a URL or disable TLS verification.
3. Enter the **existing web token** locally into the MCP client's credential/header
   mechanism or provide `MODKVM_WEB_TOKEN` in the launching process environment.
   This is a reuse of the web credential, not an Ubuntu login password or a new
   server credential. No token is requested in chat. No secret-manager or
   persistent credential-storage change is performed by this handoff.
4. Once transport and local credential handoff are ready, enable the MCP entry.
   Use ordinary MCP `initialize`, `tools/list`
   and `phone_status`; then one setup `phone_look` with the detected C7 name
   explicitly selected. Confirm capture-to-Bluetooth target identity before
   input. The setup example denies all input tools and permits status/look only
   after prompting; confirm the actual discovered tool names before changing
   these rules. Do not call `phone_use` automatically or capture the built-in
   webcam. Do not use OpenCode auto-approval mode for this setup.

For a terminal-launched OpenCode session, zsh can receive the existing token
without echoing it or putting its literal value in command history:

```sh
read -rs 'MODKVM_WEB_TOKEN?Existing modkvm web token: '
print
export MODKVM_WEB_TOKEN
```

Launch OpenCode from that environment using the user's existing provider setup.
A Desktop instance already running from Finder may not inherit a terminal's
environment; use its supported local configuration/launch method. Do not assume
that exporting a variable updates an already-running app. The example uses
`oauth: false` because this server uses its existing bearer token, not an OAuth
registration flow. No provider/model request has been made here.

Authentication is per MCP HTTP request using the header; there is no sudo or SSH
password prompt per tool call. Optional persistent local storage of the existing
web token needs the user's explicit storage choice; none has been configured.
The setup example allows prompted inspection only. It grants no automatic input
or target switching. The existing `phone_*` server tools and manual PBKEY behavior
remain unchanged. A future authorized input test requires a visible harmless
desktop and physical confirmation that the selected Bluetooth target is the PC
connected to the C7 HDMI input. The fresh frame must precede each short serialized
action; refuse stale, missing, black/unusable or mismatched views. The existing
MCP surface does not expose a dedicated release-all/cancel tool, so do not claim
that it supplies a disconnect watchdog or cancellation of an in-flight drag.

The JPEG receipt proves an MCP image result reached the local diagnostic and was
visually inspected here. It does not prove image delivery through OpenCode to the
user's exact Grok model/version. That still needs a harmless synthetic image
round-trip in the user's chosen installed client/model after connection, with
provider use authorized separately. No provider/model request has been made.
The installed OpenCode version check (`opencode --version`) was blocked by its
attempt to open `~/.local/share/opencode/log/opencode.log` outside the task's
writable area; no workaround or rerun was attempted.

Official supported interfaces:
- https://opencode.ai/docs/mcp-servers/ (remote headers and OAuth disabled example)
- https://opencode.ai/docs/permissions/ (per-tool rules)

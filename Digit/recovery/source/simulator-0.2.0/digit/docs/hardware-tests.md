# Hardware acceptance matrix

All rows below are **not run**. The checkpoint has no real backend. Complete the
matrix against the selected off-the-shelf KVM before enabling real input. Use a
dedicated test target with an empty text editor and no valuable unsaved work.
Do not use production payment, credential, terminal, or privileged sessions.

| Area | Test | Required evidence |
| --- | --- | --- |
| Identity | Select and reconnect the intended KVM/target | Device identity and target screen verified; no silent target switch |
| Capture | Read a current screen; stop capture and replay an old frame | Capture IDs/timestamps tracked; stale or missing frames prevent action |
| Keyboard | A–Z, digits, punctuation, modifiers, arrows | Expected characters/keys on known US layout; all reports released |
| Layout | Caps Lock, non-US layout, IME, lock screen | Supported behavior documented; unsupported modes rejected |
| Approval | Change plan, session, target, or observation after approval | Old approval rejected before input |
| Bounds | Oversized text, unknown action, long hold, too many keys | Whole batch rejected without partial input |
| Stop | Stop during a key hold and during a batch | Measured release deadline and queue clearing; no later queued action |
| Lost ACK | Drop the response after delivered input | No automatic replay; uncertain status and release attempt |
| Duplicate | Repeat same operation ID; reuse ID with changed content | No repeated input; conflicts fail closed |
| Network | Drop link mid-hold; block request beyond deadline | Verified independent release; bounded controller return; no replay |
| Crash | Kill controller process and adapter process mid-hold | Device releases within measured bound without parent cooperation |
| Device fault | Stall input worker; reboot or power-cycle KVM | Release behavior measured on target, fresh enable needed |
| USB | Pull target USB, reconnect; test powered-off host | No stuck key on reconnect, no stale input queue replay |
| Target | Editor focus change, modal dialog, multi-monitor change | New observation and operator review before more input |
| Observation | Verify post-action screen against intended change | Distinguish delivered key from completed task |
| Permissions | Wrong credentials, expired login, certificate error | Fail closed; do not bypass warnings or broaden access |
| Data | Private content in capture; provider transmission disabled | No unapproved image upload or persistent capture |
| Mouse later | Calibration center/corners, scale, letterboxing | Bounded coordinate error and correct monitor before enabling mouse |

Record device model, hardware revision, firmware/API versions, adapter commit,
target OS and layout, exact steps, measured timings, and captured evidence for
each run. Link limitations to the capability exposed by the adapter. A passing
simulator test is not evidence that a physical device meets a row above.

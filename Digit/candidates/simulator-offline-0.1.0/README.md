# Digit offline simulator qualification

Status: candidate; primary portfolio review pending.

All119checks pass:115recovered baseline tests plus4new fake interaction journeys. Runtime source is byte-identical to the recovered0.2.0source. The new qualification runner blocks socket operations, child processes/fork, raw /dev/ opens and ctypes library loading; zero forbidden attempts occurred.

The journeys cover two cycles without duplicate text, revoked replacement approvals, stopping before a follow-up capture and no replay after second-cycle acknowledgment loss. Every observed result remains explicitly unverified. The operator approval calls are test fixtures, not permission for real input.

No actual Bluetooth, OS keyboard/mouse, camera, app automation, native build, service, model/provider, network or package install was used. WindowsBLE and MacNOTworking candidates remain unchanged.

Files:INPUTS.json pins all42inherited simulator files; TEST-RESULT.json and TEST-OUTPUT.txt contain the final finite run; QUALIFICATION.patch contains only the four new qualification/documentation files; HANDOFF.json seals exact pins and remaining gates. Earlier run evidence is retained separately as initial.

Remaining gates: primary exact-source review, source ownership/selection, native build/profile closure, reviewed real observation/input adapters and explicit physical-device acceptance before any final release approval.

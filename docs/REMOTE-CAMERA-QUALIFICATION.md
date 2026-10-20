# Browser-camera local qualification

modkvm **0.0.00001**, source build **0d144cf3151e**. This is a tested local candidate in `~/Pontic/mk`; it has not been released or deployed. `mk.py` is primary and `portabotty.py` remains its compatibility symlink. The Plan identity remains `pls-project-portabotty`.

## Source and historical comparison

The retained Git HEAD is `f196a943015adb5e88f5b7935d45017bdce373f5` with existing local migration, camera and reconnect edits. The pre-qualification source build was `fd303f9e5e1b`. Qualification copied the current Pontic files into a separate review directory before editing and preserves unrelated local changes.

Historical delivery receipts recorded camera/controller publication through `349de7b0379baf1cd43ab4401215b914159babff` and 42 synthetic checks. That object is absent from the current local repository; no remote fetch was performed. The current source is therefore pinned independently rather than claimed identical to that published commit.

| Qualification | Python camera/disconnect | JavaScript controller | Python reconnect | Total |
| --- | ---: | ---: | ---: | ---: |
| Current source before this patch | 23 | 19 | 24 | 66 |
| Candidate after this patch | 25 | 21 | 24 | 70 |

All checks pass. The original 42-check camera/controller scope is included in both rows. The 24 reconnect tests qualify preserved local behavior, not a new change in this camera patch.

## Concrete fix

A source browser's delayed Stop previously acted only on a camera name and could stop a newer sharing session with that name. Source Stop and canceled-registration cleanup now send the existing in-memory publishing token. The camera handler checks that token atomically with stopping, including after a delayed request body. An authenticated controller can still deliberately use **Stop remote camera** on the existing Views row.

Four new regression checks reproduced missing session scoping and the delayed-body race before the fix. They now pass. No persistent credential, new port, camera provider list or input route was added.

## Verification limits and reproduction

Python fixtures generate JPEGs, mock device discovery/input and block capture subprocesses. An additional qualification runner permits connections only to temporary loopback ports bound by that test run and checks that fixture listeners close. JavaScript runs the bundled console in Node with fake media tracks and mocked uploads. No physical camera, microphone, Bluetooth target, SSH connection, production token route or Linux service is used.

The existing Python 3.12 runtime with Pillow 12.2.0 and NumPy 2.5.1 was used; nothing was installed. Node runs the UI fixtures. With the normal camera test dependencies already available, the suites are:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
node --test tests/remote_camera_ui.test.cjs tests/device_switch_ui.test.cjs
```

The local review directory `~/Pontic/.modkvm-camera-review-20261019` retains baseline/candidate copies, SHA-256 pins, the exact patch, failure reproductions, guarded test runner and final verification receipt. These are local qualification artifacts, not deployment state.

## Release and real-device acceptance gate

The next usable-deliverable gate is the user's final approval of this pinned local candidate. Public publication, deployment and runtime adoption remain pending.

For live acceptance, the existing Linux session owner must:

1. Identify the actual checkout, gateway/Cam process owner and existing verified HTTPS or authenticated encrypted route. Current Linux process/build, selected target, SSH identity and route remain **unverified**. Provide origin and identity information without credentials; do not reuse a historical plaintext token route.
2. After release approval and authorization for the specific runtime actions, adopt the approved source, preserve configuration, enable `remote_cameras` and restart the existing owning processes without creating a competing instance. Confirm gateway and Cam both report build `0d144cf3151e` through that secure route. Refresh source-browser tabs so their cleanup code also matches the candidate.
3. Have the camera-source user explicitly click **Link / Camera → Share this computer's camera** and grant browser permission. Confirm that its named row appears beside a server-local row in the existing Views list and that selection/linking, live image, snapshot and calibration work.
4. Check source Stop, controller Stop, hidden tab, connection loss and manual restart with the same name. Confirm the camera indicator turns off, expired footage clears and sharing never starts automatically. Any physical keyboard/pointer/Bluetooth acceptance must be explicitly authorized for the paired target.

No current Linux deployment or successful real-camera hookup is claimed by these tests.

# Agent instructions — projector-root

This is a standalone owner-handoff repository for P30 / H713 root research and read-only image diagnostics. The launcher lives in the separate focal-launcher repository. Start with README.md, docs/REMOTE-ROOT.md and docs/RECOVERY.md.

Target family is P30_H713_V1.1_CHIHI_emcp / sun50iw12p1 / ares / Android 11 API 30 / armeabi-v7a. The exact identity and ADB address are deliberately absent; use the ignored local/device.json template. Preserve strict identity, firmware hash, existing-daemon and staging guards. Never auto-trust whichever device happens to connect.

DO NOT write raw block devices, flash, erase, unlock, change AVB/boot-control state, switch slots, reboot, enter fastboot/FEL/recovery, install root, or start/restart a root daemon until BOTH an independent safe recovery route has been demonstrated and the device owner explicitly approves that exact action. Independent recovery remains unverified. Original active slot is _a; saved _b images are zero-filled. Never read Allwinner usb_device / usb_host sysfs trigger nodes or change persist.sys.usb0device: reads can change USB roles. Preserve original images, bounds, GPT CRC checks and hashes; never mix remote stderr into binary dd output.

scripts/remote_root.py may reuse only the ALREADY RUNNING factory qw daemon for specifically authorized diagnostics. No start-daemon, remount, weakened-check or boot-property fallback. The default check may stage a regular copy of the stock shell; it is not a firmware or daemon installation. Read-only backup scripts never gain a flash step.

`make check` is entirely offline: source audit and identity-config tests. Missing private images/configuration are normal in this distribution and must not be invented, downloaded from arbitrary mirrors or committed. AVB inspection needs the owner's own images; Magisk candidate preparation needs the separately obtained pinned official APK. None of that is needed to publish the source on GitHub.

For a publishing/onboarding request, DO NOT connect to the projector or run live root/backup/install commands. Read OWNER_SETUP.md and docs/PUBLISHING.md. The owner may explicitly request publishing these public-ready sources to their own GitHub; this preparer's handoff has no remote and does not itself authorize external actions. Determine the owner's account, display name, email and visibility instead of using the sender's account. scripts/set_owner.py is only for the single unpublished bootstrap commit, before remote setup. Never force-push or rewrite already published history.

Preserve AOSP/Magisk notices and provenance. Changing the bootstrap Git identity does not change upstream authorship. Do not publish private data, keys, runtime evidence, firmware images, vendor executables, archives of the transfer itself or local config. Do not use git add -f or broad git clean -fdx/-X. Explain results plainly and distinguish measured behavior from hypotheses.

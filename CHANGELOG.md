# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-12
First release. **Design and documentation only:** the modules are stubs, and the tool does not process files yet.

### Added
- `HOWTO.md`: a step-by-step guide to setting up and using monfile, with a worked example and common recipes. It ships in the tarball, is installed to `/opt/monfile/docs/`, and is attached to each GitHub release.
- MIT license (`LICENSE`).
- GitHub Actions release workflow: on a `v*` tag, it runs the tests, builds the tarball and `SHA256SUMS`, and publishes them as a GitHub release.
- Initial project scaffolding: design docs, requirements, ADRs 0001–0009, audit documentation, the third-party inventory, the example config, stub package, systemd units, and install/build script drafts.
- Optional per-folder `ready_marker` setting (e.g. `.done`): a file is processed only after its marker exists, and the marker is removed on success (ADR-0009). New audit events: `MARKER_SEEN`, `MARKER_ORPHAN`, `MARKER_REMOVED`, `MARKER_REMOVE_FAIL`.
- Optional per-folder `skip_empty` setting (default `false`), with the `FILE_SKIPPED` event.
- `FILE_LARGE` warning event for files over 10 GiB (no size limit).

### Decided
- Watch folders are local disk. Delivered files are mode `0640`. Changed DONE files are reprocessed as new versions. State and manifests are kept indefinitely. Ubuntu universe is allowed for `python3.11` on 22.04. Failure alerting is syslog/SIEM only (DESIGN §13 Q4–Q7, Q9, Q12, Q13).
- The proposed defaults were accepted for the remaining open questions: 30 s scan interval, the default exclude list, restart-only config reload (SIGHUP ignored), no custom SELinux policy in v1, `move` as copy + verify + unlink, and sequential processing (DESIGN §13 Q1–Q3, Q8, Q10, Q11, Q14, Q15). All design questions are now closed.

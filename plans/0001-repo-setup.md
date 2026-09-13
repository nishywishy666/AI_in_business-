# 0001 — Repo setup for hackathon

**Status:** Done
**Date:** 2026-09-13

## Goal
Set up process scaffolding (CLAUDE.md, plans/, lessons/) so the hackathon build stays organized across many sessions, before the web-app's tech stack is decided.

## Context
This repo is the main codebase for the AI for Businesses Hackathon. The user wants every change planned in `plans/` and every mistake recorded in `lessons/`, kept up to date throughout development, so context isn't lost between sessions. The app's tech stack/spec will be provided separately before app scaffolding begins.

## Approach
- Add root `CLAUDE.md` describing the working agreement (write a plan before non-trivial work, write a lesson after any bug/mistake, keep both updated).
- Add `plans/` with a README (convention + status values) and `TEMPLATE.md`.
- Add `lessons/` with a README (convention) and `TEMPLATE.md`.
- Add a root `.gitignore` covering common JS/Python/OS artifacts, to be refined once the stack is picked.
- Update root `README.md` to point at the new structure.
- Hold off on any app-specific folders (frontend/backend/etc.) until the stack is confirmed.

## Outcome
Scaffolding created as planned. Next step is a new plan once the stack/spec doc arrives, covering initial app scaffolding.

# Oracle Reply Ownership

## Purpose

This document defines Oracle's reply ownership contract.

It defines:

- the canonical owner of spoken reply text
- the required `reply_text` coverage on `/api/conversation/command`
- the allowed fallback behavior for satellites

## Ownership Rule

The Oracle brain is the canonical owner of spoken reply text.

- `reply_text` from `POST /api/conversation/command` is the canonical spoken source of truth for normal success paths
- `reply_text` from `POST /api/conversation/command` is the canonical spoken source of truth for normal failure paths
- satellites must not reconstruct normal spoken replies from `dispatch.result`
- satellites may keep only minimal fallback behavior for missing `reply_text`, transport failure, or intentionally silent responses

## Brain Responsibilities

The brain owns:

- normal success reply wording
- normal failure reply wording
- pending confirmation prompts
- pending clarification prompts
- reply shaping for normal spoken outcomes
- one pure target-specific shaper per dispatch target, selected by the
  Brain-owned reply registry

The satellite owns only:

- playback of the provided reply text through `/tts`
- minimal local behavior when the brain response is missing or intentionally silent
- local reply interruption and follow-up capture mechanics

## `reply_text` Coverage Matrix

- `executed`:
  - `reply_text` is required for normal spoken outcomes
  - exception: intentional silent no-op paths
- `failed`:
  - `reply_text` is required for normal spoken failure outcomes
  - exception: intentional silence only
- `pending_confirmation`:
  - `reply_text` is required
- `pending_clarification`:
  - `reply_text` is required
- `ignore` or silent no-op:
  - `reply_text` may be empty

## Allowed Fallback Matrix For Satellites

Allowed:

- use `reply_text` directly when present
- use `dispatch.result.prompt` for pending confirmation or clarification only when `reply_text` is absent
- remain silent for intentional ignore paths
- use one minimal generic failure fallback when `reply_text` is absent
- use one minimal generic success fallback when `reply_text` is absent

Disallowed:

- reconstructing domain-specific spoken replies locally
- treating `dispatch.result` as the normal speech source of truth

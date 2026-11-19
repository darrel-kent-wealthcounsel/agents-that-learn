---
name: write-a-story
version: 1
description: Add or update a Storybook story for a Vue component.
---

# Write a story

## When to use

Adding or updating a Storybook story for a Vue component.

## Steps

1. Create `stories/<Component>.stories.ts` beside the other stories.
2. Start the file with `import { fn } from '@storybook/test'` so click handlers can be spied on.
3. Export a default object naming the component, then one named export per state.
4. Run `pnpm build-storybook` and fix anything it reports.

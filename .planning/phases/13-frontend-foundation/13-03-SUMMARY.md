---
phase: 13-frontend-foundation
plan: "03"
subsystem: frontend
tags: [react-router, layout, sidebar, tanstack-query, placeholder-pages]
dependency_graph:
  requires: [13-01, 13-02]
  provides: [app entry wiring, layout shell, 9 routed placeholder pages]
  affects: [all future UI plans 14-16]
tech_stack:
  added: []
  patterns: [nested routes with layout outlet, NavLink active highlighting, QueryClient at root]
key_files:
  created:
    - packages/ui/src/App.tsx
    - packages/ui/src/lib/cn.ts
    - packages/ui/src/lib/constants.ts
    - packages/ui/src/components/layout/Layout.tsx
    - packages/ui/src/components/layout/Sidebar.tsx
    - packages/ui/src/components/layout/Header.tsx
    - packages/ui/src/features/dashboard/DashboardPage.tsx
    - packages/ui/src/features/providers/ProvidersPage.tsx
    - packages/ui/src/features/providers/ProviderDetailPage.tsx
    - packages/ui/src/features/groups/GroupsPage.tsx
    - packages/ui/src/features/executions/ExecutionsPage.tsx
    - packages/ui/src/features/metrics/MetricsPage.tsx
    - packages/ui/src/features/events/EventsPage.tsx
    - packages/ui/src/features/discovery/DiscoveryPage.tsx
    - packages/ui/src/features/auth/AuthPage.tsx
    - packages/ui/src/features/config/ConfigPage.tsx
  modified:
    - packages/ui/src/main.tsx
decisions:
  - "NavLink end=true on Dashboard route to prevent it matching all paths"
  - "Zustand stores need no provider wrapper; module-level singletons used directly"
  - "SystemStatusBadge polls /api/system/info every 30s with retry=2"
metrics:
  duration: ~20min
  tasks_completed: 2
  files_created: 16
  files_modified: 1
  completed_date: "2026-03-14"
---

# Phase 13 Plan 03: App Wiring, Layout Shell, and Placeholder Pages Summary

**One-liner:** React Router v7 nested layout with NavLink sidebar, TanStack Query-driven status header, and 10 routed placeholder feature pages.

## What Was Built

### App Entry Wiring (`src/main.tsx`)

Updated to wrap the app with `BrowserRouter`, `QueryClientProvider` (staleTime 30s, retry 2), and `ReactQueryDevtools`. Zustand requires no provider.

### Layout Shell

- **`src/lib/cn.ts`** — `clsx` + `tailwind-merge` utility for conditional class composition
- **`src/lib/constants.ts`** — `ROUTES` constant object + `NAV_ITEMS` array (used at build time; Sidebar inlines its own icon-mapped copy for type safety)
- **`src/components/layout/Sidebar.tsx`** — Fixed-width (224px) sidebar with `NavLink` for each of the 9 routes; active item gets `bg-blue-50 text-blue-700 font-medium`; Dashboard uses `end={true}` to prevent matching all paths; lucide-react icons per nav item
- **`src/components/layout/Header.tsx`** — `SystemStatusBadge` polls `/api/system/info` every 30s via `useQuery`; shows green dot + version/ready count on success, red dot + "Backend offline" on error, pulsing gray dot while loading; static title "Management Console"
- **`src/components/layout/Layout.tsx`** — Full-height flex shell: `<Sidebar>` + column of `<Header>` over `<main><Outlet /></main>`

### Routes (`src/App.tsx`)

Nested route tree: one parent `<Route element={<Layout />}>` wrapping 10 child routes (index + 9 named paths including `/providers/:id`).

### Placeholder Pages (10 files)

All export named functions. `ProviderDetailPage` reads `useParams<{ id: string }>()` to display the provider ID. Each page has a heading and a one-line description of what the full implementation will provide (Plans 14-16).

## Verification

```
npm run type-check  ->  zero errors
git log --oneline   ->  e9c5185 feat(13-13-03): app wiring, layout shell, and placeholder pages
```

All 10 page files exist. All must-have artifacts present and contain the required patterns.

## Deviations from Plan

None — plan executed exactly as written.

## Checkpoint Pending

A `checkpoint:human-verify` gate follows Task 2. The user must:

1. Run `npm run dev` from `packages/ui/`
2. Open http://localhost:5173
3. Verify: sidebar shows 9 nav links, each navigates without 404, active item highlights blue, header shows "Management Console" + status badge
4. Run `npm run type-check` — must pass with zero errors

## Self-Check: PASSED

- All 16 new files exist on disk
- `npm run type-check` exits 0
- Commit `e9c5185` exists in git log

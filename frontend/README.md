# Web app

Next.js (App Router), React and TypeScript, with Tailwind CSS and shadcn/ui. The plan is in
`docs/PHASE4.md`.

```sh
make web-install   # npm ci (Node 22 or later)
make web           # the dev server on :3000; /api is forwarded to the backend on :8000 (API_URL to change)
make web-lint      # type check and ESLint
make web-test      # Vitest
make web-build     # production build; run it with `npm start` in frontend/
```

Add a shadcn/ui component with `npx shadcn@latest add <name>`; it is copied into `src/components/ui/`
and is edited like any other file.

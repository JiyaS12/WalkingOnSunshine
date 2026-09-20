# GaitGuard AI frontend

This App Router frontend requires Node.js 20.19.x and npm 10 or newer.
The root `.nvmrc` selects the supported Node 20 release.

## Getting started

Install the locked dependencies and run the development server:

```bash
npm ci
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in a browser. Copy
`.env.example` to `.env.local` only when the API is not available at its
default `http://localhost:8000` URL.

The home page is informational and does not expose patient lookup or demo
creation. Patient screening requires the complete expiring URL returned by
`POST /api/submit-survey`; a bare `/patient/[id]` route cannot load a record.
See the root README for the local-only signed-link demo procedure.

## Verification

```bash
npm ci
npm audit --omit=dev --audit-level=high
npm test
npm run lint
npm run type-check
npm run build
```

See the repository README for the Python 3.12 backend setup and complete
clean-checkout verification sequence.

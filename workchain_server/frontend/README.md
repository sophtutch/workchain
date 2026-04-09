# Workchain Designer — Frontend

A React + Vite single-page app that powers the `/designer/` route of
`workchain_server`.  Source lives here; the build output lands in
`../static/designer/` and is served by FastAPI's `StaticFiles` mount.

## Stack

| Piece            | Library                                    |
|------------------|--------------------------------------------|
| Framework        | React 18                                   |
| Build tool       | Vite 5                                     |
| Graph canvas     | [React Flow](https://reactflow.dev/)       |
| JSON-schema form | [`@rjsf/core`](https://rjsf-team.github.io/react-jsonschema-form/) with the Bootstrap 4 theme |
| Styles           | Bootstrap 4 + hand-rolled `src/styles.css` |

## Prerequisites

- **Node 20+** (tested with 24)
- **npm** (ships with Node)

## Quick start

```bash
# from repository root
hatch run frontend:install   # npm install
hatch run frontend:build     # tsc --noEmit + vite build -> ../static/designer/
```

After `frontend:build`, restart `hatch run server:serve` (or let it auto-reload)
and visit <http://localhost:8000/designer/>.

## Hot reload

Two terminals:

```bash
# terminal 1
hatch run server:serve                  # FastAPI on :8000

# terminal 2
hatch run frontend:dev                  # Vite dev server on :5173
```

Vite proxies `/api/*` to the FastAPI backend, so visit
<http://localhost:5173/> and API calls Just Work.  Hot reload is enabled
for all source files.

## Layout

```
src/
├── main.tsx              Entry point, mounts <App/>
├── App.tsx               State container + composition
├── styles.css            Layout + theme
├── api/
│   ├── types.ts          Wire-format types mirroring designer_router.py DTOs
│   └── client.ts         Typed fetch wrappers + DraftValidationError
├── hooks/
│   └── useHandlers.ts    Loads /api/v1/handlers once on mount
├── lib/
│   ├── graphToDraft.ts   React Flow nodes/edges -> WorkflowDraft JSON
│   └── draftValidate.ts  Client-side cycle/name/orphan checks
└── components/
    ├── Toolbar.tsx       Top bar: workflow name, Run, Clear, status
    ├── HandlerPalette.tsx Left sidebar: draggable handler list
    ├── DesignerCanvas.tsx Middle: React Flow wrapper with drop support
    ├── StepNode.tsx      Custom React Flow node renderer
    └── ConfigPanel.tsx   Right sidebar: RJSF form for the selected node
```

## Wire format

The designer serialises its graph to the shape accepted by
`POST /api/v1/workflows`:

```json
{
  "name": "user-onboarding",
  "steps": [
    {
      "name": "create_account",
      "handler": "myapp.steps.create_account",
      "config": {"email": "x@y.z"},
      "depends_on": []
    }
  ]
}
```

The backend derives the typed `StepConfig` subclass from each handler's
signature — the client never sends dotted paths.

## Notes

- **`base: "/designer/"`** in `vite.config.ts` ensures the built asset URLs
  resolve under the FastAPI `StaticFiles(html=True)` mount.
- **`build.outDir: "../static/designer"`** writes straight to the path the
  server expects.  `../static/designer/` is gitignored.
- **`@rjsf/bootstrap-4`** was chosen over `@rjsf/mui` to avoid the Emotion
  runtime cost.  Dark-theme harmonization with the existing dashboard is a
  follow-up.

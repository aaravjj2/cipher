"use client";

// Third-party stylesheet for the docking grid. Next.js explicitly permits importing a
// component's own CSS from node_modules at the component that needs it
// (node_modules/next/dist/docs/01-app/01-getting-started/11-css.md, "Import styles from
// node_modules"), which keeps it out of globals.css and out of every other route.
import "dockview-react/dist/styles/dockview.css";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  DockviewReact,
  themeAbyss,
  type DockviewApi,
  type DockviewReadyEvent,
  type DockviewTheme,
  type IDockviewPanelProps,
  type SerializedDockview,
} from "dockview-react";
import { PanelHost, panelTitle } from "@/components/PanelHost";
import {
  deleteWorkspaceLayout,
  fetchWorkspaceLayout,
  fetchWorkspaceLayouts,
  saveWorkspaceLayout,
  type WorkspaceLayoutMeta,
} from "@/lib/api";

/**
 * Workspace mode — several existing panels open at once in a tiled/docked grid, instead
 * of one at a time.
 *
 * Nothing here reimplements a panel. Each tile is a `<PanelHost/>` rendering the exact
 * same component the single-panel view renders, so a panel gains docking without any
 * change to its own file.
 *
 * dockview mounts every tile through `ReactDOM.createPortal` from this component's
 * position in the React tree (see dockview-react/dist/esm/react.js), which is why the
 * active ticker can reach tiles through context below rather than through dockview's
 * `params` — no manual `updateParameters()` call on every ticker change, and no risk of
 * a tile holding a stale symbol.
 */

const TickerContext = createContext<string>("AAPL");

/** Base theme is abyss (dark, spaced); `dockview-theme-cipher` in globals.css re-points
 *  its colour variables at Cipher's own tokens so tabs and sashes match the app. */
const CIPHER_THEME: DockviewTheme = {
  ...themeAbyss,
  name: "cipher",
  className: "dockview-theme-abyss dockview-theme-cipher",
  colorScheme: "dark",
};

type TileParams = { label: string };

function Tile(props: IDockviewPanelProps<TileParams>) {
  const ticker = useContext(TickerContext);
  return (
    <div className="h-full w-full overflow-auto p-3">
      <PanelHost panel={props.params.label} ticker={ticker} />
    </div>
  );
}

const DOCK_COMPONENTS = { panel: Tile };

function Watermark() {
  return (
    <div
      className="grid h-full w-full place-items-center px-6 text-center text-[12px]"
      style={{ fontFamily: "var(--font-mono)", color: "var(--text-mute)" }}
    >
      Pick a panel in the sidebar to open it as a tile. Drag a tab to an edge to split.
    </div>
  );
}

/** A dockview panel id is just the sidebar label — labels are already unique, and it
 *  makes a serialized layout readable when inspecting data/workspace_layouts. */
function addOrFocus(api: DockviewApi, label: string) {
  const existing = api.getPanel(label);
  if (existing) {
    existing.api.setActive();
    return;
  }
  const openPanels = api.panels;
  api.addPanel<TileParams>({
    id: label,
    component: "panel",
    title: panelTitle(label),
    params: { label },
    // The second tile opens to the right rather than as another tab: the whole point of
    // this mode is seeing two panels at once, and a user who never discovers tab-dragging
    // would otherwise get a tabbed stack that looks exactly like single-panel mode.
    // Third and later tiles join the focused group as tabs, which is what stops the grid
    // shrinking every panel into an unreadable column.
    ...(openPanels.length === 1
      ? { position: { referencePanel: openPanels[0].id, direction: "right" as const } }
      : {}),
  });
}

type WorkspaceProps = {
  /** Active ticker, shared with the header and every tile. */
  ticker: string;
  /**
   * A panel-open request from the sidebar or command palette. The `seq` counter is what
   * makes repeat requests for the same label work: after a user closes a tile, clicking
   * that same sidebar entry again has an unchanged `label`, so only a changing `seq`
   * re-triggers the open.
   */
  openRequest: { label: string; seq: number };
};

export function Workspace({ ticker, openRequest }: WorkspaceProps) {
  const apiRef = useRef<DockviewApi | null>(null);
  const [ready, setReady] = useState(false);
  const [layouts, setLayouts] = useState<WorkspaceLayoutMeta[]>([]);
  const [layoutName, setLayoutName] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Read inside onReady only, so the first tile matches whatever was selected at the
  // moment the grid mounted without making onReady depend on a changing prop.
  const initialLabel = useRef(openRequest.label);

  const refreshLayouts = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await fetchWorkspaceLayouts(signal);
      setLayouts(res.layouts);
    } catch {
      // A failed list just means no saved-layout chips; the grid itself still works.
    }
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    refreshLayouts(ctrl.signal);
    return () => ctrl.abort();
  }, [refreshLayouts]);

  const onReady = useCallback((event: DockviewReadyEvent) => {
    apiRef.current = event.api;
    addOrFocus(event.api, initialLabel.current);
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready || !apiRef.current) return;
    addOrFocus(apiRef.current, openRequest.label);
  }, [ready, openRequest]);

  const handleSave = useCallback(async () => {
    const api = apiRef.current;
    if (!api) return;
    const name = layoutName.trim();
    if (!name) {
      setStatus("Name the layout first.");
      return;
    }
    setBusy(true);
    try {
      const blob = api.toJSON() as unknown as Record<string, unknown>;
      await saveWorkspaceLayout(name, blob);
      setStatus(`Saved "${name}".`);
      await refreshLayouts();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Save failed.");
    } finally {
      setBusy(false);
    }
  }, [layoutName, refreshLayouts]);

  const handleLoad = useCallback(async (name: string) => {
    const api = apiRef.current;
    if (!api) return;
    setBusy(true);
    try {
      const record = await fetchWorkspaceLayout(name);
      api.fromJSON(record.layout as unknown as SerializedDockview);
      setLayoutName(name);
      setStatus(`Loaded "${name}".`);
    } catch (err) {
      // A blob dockview refuses can leave the grid half-built, so rebuild a known-good
      // single tile rather than leaving the user staring at a broken grid.
      try {
        api.clear();
        addOrFocus(api, openRequest.label);
      } catch {
        /* nothing further to recover to */
      }
      setStatus(err instanceof Error ? err.message : "Load failed.");
    } finally {
      setBusy(false);
    }
  }, [openRequest.label]);

  const handleDelete = useCallback(async (name: string) => {
    setBusy(true);
    try {
      await deleteWorkspaceLayout(name);
      setStatus(`Deleted "${name}".`);
      await refreshLayouts();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Delete failed.");
    } finally {
      setBusy(false);
    }
  }, [refreshLayouts]);

  const handleReset = useCallback(() => {
    const api = apiRef.current;
    if (!api) return;
    api.clear();
    addOrFocus(api, openRequest.label);
    setStatus(null);
  }, [openRequest.label]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Layout bar */}
      <div
        className="flex flex-row flex-wrap items-center gap-2 px-3 py-2"
        style={{
          borderBottom: "1px solid var(--line)",
          background: "color-mix(in srgb, var(--panel) 70%, transparent)",
          fontFamily: "var(--font-mono)",
          fontSize: "11px",
        }}
      >
        <span style={{ color: "var(--text-mute)", letterSpacing: "0.08em" }}>LAYOUT</span>
        <input
          value={layoutName}
          onChange={(e) => setLayoutName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleSave();
          }}
          placeholder="name"
          aria-label="Layout name"
          className="w-[130px] rounded-[6px] px-2 py-1 outline-none"
          style={{
            background: "var(--panel-2)",
            border: "1px solid var(--line)",
            color: "var(--text)",
            fontFamily: "var(--font-mono)",
          }}
        />
        <button
          type="button"
          onClick={handleSave}
          disabled={busy}
          className="rounded-[6px] px-[10px] py-1 font-semibold disabled:opacity-50"
          style={{ border: "1px solid var(--line)", color: "var(--text-dim)" }}
        >
          Save
        </button>
        <button
          type="button"
          onClick={handleReset}
          className="rounded-[6px] px-[10px] py-1 font-semibold"
          style={{ border: "1px solid var(--line)", color: "var(--text-dim)" }}
        >
          Reset
        </button>

        {layouts.length > 0 && (
          <span style={{ color: "var(--text-mute)" }}>|</span>
        )}
        {layouts.map((meta) => (
          <span
            key={meta.name}
            className="flex flex-row items-center gap-1 rounded-[6px] pl-2 pr-1 py-[2px]"
            style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}
          >
            <button
              type="button"
              onClick={() => handleLoad(meta.name)}
              disabled={busy}
              title={`Load layout, saved ${meta.updated_at}`}
              className="font-semibold disabled:opacity-50"
              style={{ color: "var(--text-dim)" }}
            >
              {meta.name}
            </button>
            <button
              type="button"
              onClick={() => handleDelete(meta.name)}
              disabled={busy}
              aria-label={`Delete layout ${meta.name}`}
              className="px-1 disabled:opacity-50"
              style={{ color: "var(--text-mute)" }}
            >
              ×
            </button>
          </span>
        ))}

        {status && (
          <span className="ml-auto" style={{ color: "var(--text-mute)" }}>
            {status}
          </span>
        )}
      </div>

      <TickerContext.Provider value={ticker}>
        <div className="min-h-0 flex-1">
          <DockviewReact
            components={DOCK_COMPONENTS}
            watermarkComponent={Watermark}
            noPanelsOverlay="watermark"
            theme={CIPHER_THEME}
            onReady={onReady}
          />
        </div>
      </TickerContext.Provider>
    </div>
  );
}

export default Workspace;

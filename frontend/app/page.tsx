"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// Matches engine/previewer.py ROOM_COLORS so the sidebar dots align with the canvas.
const ROOM_COLORS: Record<string, string> = {
  bedroom: "#AED6F1",
  bathroom: "#A9DFBF",
  kitchen: "#FAD7A0",
  living: "#F9E79F",
  dining: "#F5CBA7",
  hallway: "#D7DBDD",
  office: "#D2B4DE",
  garage: "#BFC9CA",
  other: "#EAEDED",
};

const EXAMPLES = [
  "Add a 300 sqft living room",
  "Add a kitchen next to the living room, 150 sqft",
  "Add a master bedroom of 200 sqft",
  "Make the kitchen bigger",
  "Remove the hallway",
];

interface RoomInfo {
  name: string;
  room_type: string;
  area_sqft: number | null;
}

interface PlanState {
  plan_id: string;
  version: number;
  room_count: number;
  rooms: Record<string, RoomInfo>;
}

export default function Home() {
  const [planId, setPlanId] = useState<string | null>(null);
  const [instruction, setInstruction] = useState("");
  const [rooms, setRooms] = useState<Record<string, RoomInfo>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const roomCount = Object.keys(rooms).length;

  const refreshPreview = useCallback((id: string) => {
    setPreviewLoading(true);
    setPreviewUrl(`/api/plan/${id}/preview?width=800&height=600&t=${Date.now()}`);
  }, []);

  const refreshState = useCallback(async (id: string) => {
    try {
      const res = await fetch(`/api/plan/${id}/state`);
      if (!res.ok) return;
      const data: PlanState = await res.json();
      setRooms(data.rooms || {});
    } catch {
      // non-fatal — state refresh is best-effort
    }
  }, []);

  const handleApiError = useCallback(async (res: Response, fallback: string) => {
    try {
      const data = await res.json();
      setError(data?.detail ? String(data.detail) : fallback);
    } catch {
      setError(fallback);
    }
  }, []);

  const newPlan = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      const res = await fetch("/api/plan/new", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        await handleApiError(res, "Failed to create a new plan.");
        return;
      }
      const data = await res.json();
      setPlanId(data.plan_id);
      setRooms({});
      refreshPreview(data.plan_id);
    } catch {
      setError("Could not connect to server. Is the backend running?");
    } finally {
      setBusy(false);
    }
  }, [handleApiError, refreshPreview]);

  const uploadPlan = useCallback(
    async (file: File) => {
      setError(null);
      setBusy(true);
      try {
        const form = new FormData();
        form.append("file", file);
        const res = await fetch("/api/plan/load", {
          method: "POST",
          body: form,
        });
        if (!res.ok) {
          await handleApiError(res, "Failed to load the uploaded plan.");
          return;
        }
        const data = await res.json();
        setPlanId(data.plan_id);
        await refreshState(data.plan_id);
        refreshPreview(data.plan_id);
      } catch {
        setError("Could not connect to server. Is the backend running?");
      } finally {
        setBusy(false);
      }
    },
    [handleApiError, refreshPreview, refreshState]
  );

  const onFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) uploadPlan(file);
      e.target.value = ""; // allow re-uploading the same file
    },
    [uploadPlan]
  );

  const sendInstruction = useCallback(async () => {
    if (!planId || !instruction.trim()) return;
    setError(null);
    setBusy(true);
    try {
      const res = await fetch(`/api/plan/${planId}/instruct`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction: instruction.trim() }),
      });
      if (!res.ok) {
        await handleApiError(res, "The instruction could not be applied.");
        return;
      }
      setInstruction("");
      await refreshState(planId);
      refreshPreview(planId);
    } catch {
      setError("Could not connect to server. Is the backend running?");
    } finally {
      setBusy(false);
    }
  }, [planId, instruction, handleApiError, refreshPreview, refreshState]);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        sendInstruction();
      }
    },
    [sendInstruction]
  );

  const openExport = useCallback(
    (format: "dxf" | "pdf") => {
      if (!planId) return;
      window.open(`/api/plan/${planId}/export?format=${format}`, "_blank");
    },
    [planId]
  );

  // Initial preview load once a plan exists but no preview yet.
  useEffect(() => {
    if (planId && !previewUrl) refreshPreview(planId);
  }, [planId, previewUrl, refreshPreview]);

  return (
    <main style={styles.page}>
      {/* ── Left column: controls ───────────────────────────────── */}
      <section style={styles.left}>
        <header style={{ marginBottom: 24 }}>
          <h1 style={styles.title}>Cognitect</h1>
          <p style={styles.subtitle}>AI Floor Plan Engine</p>
        </header>

        {error && (
          <div style={styles.errorBanner}>
            <span style={{ flex: 1 }}>{error}</span>
            <button
              onClick={() => setError(null)}
              style={styles.errorClose}
              aria-label="Dismiss error"
            >
              ×
            </button>
          </div>
        )}

        <div style={styles.buttonRow}>
          <button onClick={newPlan} disabled={busy} style={styles.primaryBtn}>
            New Plan
          </button>
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={busy}
            style={styles.secondaryBtn}
          >
            Upload Plan
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".json,.dxf"
            onChange={onFileChange}
            style={{ display: "none" }}
          />
        </div>

        {planId && (
          <p style={styles.planId}>
            Plan ID: <code>{planId}</code>
          </p>
        )}

        {planId && (
          <div style={styles.section}>
            <textarea
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Describe your floor plan or give an instruction..."
              rows={3}
              style={styles.textarea}
            />
            <div style={styles.examples}>
              {EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  onClick={() => setInstruction(ex)}
                  style={styles.exampleChip}
                  type="button"
                >
                  {ex}
                </button>
              ))}
            </div>
            <button
              onClick={sendInstruction}
              disabled={busy || !instruction.trim()}
              style={{ ...styles.primaryBtn, width: "100%", marginTop: 10 }}
            >
              {busy ? (
                <span style={styles.spinnerRow}>
                  <span style={styles.spinner} /> Working…
                </span>
              ) : (
                "Send  (⌘/Ctrl + Enter)"
              )}
            </button>
          </div>
        )}

        {planId && roomCount > 0 && (
          <div style={styles.section}>
            <h2 style={styles.sectionTitle}>Rooms ({roomCount})</h2>
            <ul style={styles.roomList}>
              {Object.entries(rooms).map(([id, room]) => (
                <li key={id} style={styles.roomItem}>
                  <span
                    style={{
                      ...styles.dot,
                      background: ROOM_COLORS[room.room_type] || ROOM_COLORS.other,
                    }}
                  />
                  <span style={{ flex: 1 }}>{room.name}</span>
                  <span style={styles.roomArea}>
                    {room.area_sqft ? `${Math.round(room.area_sqft)} sqft` : "—"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {planId && roomCount > 0 && (
          <div style={styles.exportRow}>
            <button onClick={() => openExport("dxf")} style={styles.secondaryBtn}>
              Download DXF
            </button>
            <button onClick={() => openExport("pdf")} style={styles.secondaryBtn}>
              Download PDF
            </button>
          </div>
        )}
      </section>

      {/* ── Right column: canvas ────────────────────────────────── */}
      <section style={styles.right}>
        {!planId && (
          <p style={styles.placeholder}>
            Start a new plan or upload an existing one
          </p>
        )}
        {planId && (
          <div style={styles.canvasWrap}>
            {previewLoading && <div style={styles.shimmer} />}
            {previewUrl && (
              <img
                key={previewUrl}
                src={previewUrl}
                alt="Floor plan preview"
                style={styles.canvasImg}
                onLoad={() => setPreviewLoading(false)}
                onError={() => setPreviewLoading(false)}
              />
            )}
          </div>
        )}
      </section>
    </main>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    display: "flex",
    width: "100%",
    minWidth: 900,
    height: "100vh",
    overflow: "hidden",
  },
  left: {
    width: "35%",
    minWidth: 340,
    height: "100%",
    padding: "28px 26px",
    borderRight: "1px solid var(--border)",
    overflowY: "auto",
  },
  right: {
    width: "65%",
    height: "100%",
    background: "var(--panel)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: 32,
  },
  title: { fontSize: 32, fontWeight: 800, letterSpacing: -0.5 },
  subtitle: { fontSize: 14, color: "var(--muted)", marginTop: 2 },
  buttonRow: { display: "flex", gap: 10 },
  primaryBtn: {
    background: "var(--accent)",
    color: "#fff",
    border: "none",
    borderRadius: 8,
    padding: "10px 16px",
    fontSize: 14,
    fontWeight: 600,
    cursor: "pointer",
  },
  secondaryBtn: {
    background: "#fff",
    color: "var(--accent)",
    border: "1px solid var(--accent)",
    borderRadius: 8,
    padding: "10px 16px",
    fontSize: 14,
    fontWeight: 600,
    cursor: "pointer",
  },
  planId: { marginTop: 12, fontSize: 12, color: "var(--muted)" },
  section: {
    marginTop: 22,
    paddingTop: 20,
    borderTop: "1px solid var(--border)",
  },
  sectionTitle: {
    fontSize: 13,
    fontWeight: 700,
    textTransform: "uppercase",
    letterSpacing: 0.6,
    color: "var(--muted)",
    marginBottom: 10,
  },
  textarea: {
    width: "100%",
    resize: "vertical",
    padding: "10px 12px",
    fontSize: 14,
    fontFamily: "inherit",
    border: "1px solid var(--border)",
    borderRadius: 8,
    color: "var(--text)",
    outline: "none",
  },
  examples: {
    display: "flex",
    flexWrap: "wrap",
    gap: 6,
    marginTop: 8,
  },
  exampleChip: {
    background: "var(--panel)",
    border: "1px solid var(--border)",
    borderRadius: 14,
    padding: "4px 10px",
    fontSize: 11,
    color: "var(--muted)",
    cursor: "pointer",
  },
  spinnerRow: { display: "inline-flex", alignItems: "center", gap: 8 },
  spinner: {
    width: 14,
    height: 14,
    border: "2px solid rgba(255,255,255,0.5)",
    borderTopColor: "#fff",
    borderRadius: "50%",
    display: "inline-block",
    animation: "spin 0.7s linear infinite",
  },
  roomList: { listStyle: "none", display: "flex", flexDirection: "column", gap: 6 },
  roomItem: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    fontSize: 14,
    padding: "4px 0",
  },
  dot: {
    width: 12,
    height: 12,
    borderRadius: 3,
    border: "1px solid #2c3e50",
    flexShrink: 0,
  },
  roomArea: { fontSize: 12, color: "var(--muted)" },
  exportRow: { display: "flex", gap: 10, marginTop: 22 },
  errorBanner: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    background: "var(--danger-bg)",
    color: "var(--danger)",
    border: "1px solid #f5b7b1",
    borderRadius: 8,
    padding: "10px 12px",
    fontSize: 13,
    marginBottom: 16,
  },
  errorClose: {
    background: "none",
    border: "none",
    color: "var(--danger)",
    fontSize: 20,
    lineHeight: 1,
    cursor: "pointer",
  },
  placeholder: { color: "var(--muted)", fontSize: 16 },
  canvasWrap: {
    position: "relative",
    maxWidth: "100%",
    maxHeight: "100%",
    borderRadius: 10,
    overflow: "hidden",
    boxShadow: "0 10px 40px rgba(0,0,0,0.12)",
    background: "#fff",
  },
  canvasImg: {
    display: "block",
    maxWidth: "100%",
    maxHeight: "calc(100vh - 64px)",
    objectFit: "contain",
  },
  shimmer: {
    position: "absolute",
    inset: 0,
    zIndex: 1,
    background:
      "linear-gradient(90deg, #f0f2f4 0px, #e6e9ec 200px, #f0f2f4 400px)",
    backgroundSize: "800px 100%",
    animation: "shimmer 1.4s infinite linear",
  },
};

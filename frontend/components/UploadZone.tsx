// frontend/components/UploadZone.tsx
// -----------------------------------------------------------------------------
// Zone de dépôt des PDF Huawei. Drag & drop + sélection manuelle, upload avec
// barre de progression via ingestFile(), puis router.refresh() pour que la
// liste "Documents indexés" (Server Component dans page.tsx) se remette à
// jour sans recharger toute la page.
// -----------------------------------------------------------------------------
"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ingestFile } from "@/lib/api";

type UploadStatus = "uploading" | "success" | "error";

interface UploadItem {
  id: string;
  filename: string;
  status: UploadStatus;
  progress: number;
  message?: string;
}

function makeId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export default function UploadZone() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [uploads, setUploads] = useState<UploadItem[]>([]);

  async function handleFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;

    const pdfFiles = Array.from(fileList).filter((f) =>
      f.name.toLowerCase().endsWith(".pdf")
    );
    const rejected = fileList.length - pdfFiles.length;

    if (pdfFiles.length === 0) {
      setUploads((prev) => [
        ...prev,
        {
          id: makeId(),
          filename: "fichier(s) rejeté(s)",
          status: "error",
          progress: 0,
          message: "Seuls les PDF sont acceptés.",
        },
      ]);
      return;
    }

    for (const file of pdfFiles) {
      const id = makeId();
      setUploads((prev) => [
        ...prev,
        { id, filename: file.name, status: "uploading", progress: 0 },
      ]);

      try {
        const result = await ingestFile(file, (percent) => {
          setUploads((prev) =>
            prev.map((u) => (u.id === id ? { ...u, progress: percent } : u))
          );
        });

        setUploads((prev) =>
          prev.map((u) =>
            u.id === id
              ? {
                  ...u,
                  status: "success",
                  progress: 100,
                  message:
                    result.chunks_indexed != null
                      ? `${result.chunks_indexed} chunks indexés`
                      : "indexé",
                }
              : u
          )
        );
        router.refresh(); // met à jour la liste "Documents indexés" server-side
      } catch (err) {
        setUploads((prev) =>
          prev.map((u) =>
            u.id === id
              ? {
                  ...u,
                  status: "error",
                  message: err instanceof Error ? err.message : "Échec de l'upload",
                }
              : u
          )
        );
      }
    }

    if (rejected > 0) {
      setUploads((prev) => [
        ...prev,
        {
          id: makeId(),
          filename: `${rejected} fichier(s) ignoré(s)`,
          status: "error",
          progress: 0,
          message: "Seuls les PDF sont acceptés.",
        },
      ]);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setIsDragging(false);
          handleFiles(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
        style={{
          border: `1px dashed ${isDragging ? "var(--accent-live)" : "var(--border)"}`,
          borderRadius: "var(--radius)",
          padding: "1.1rem 0.9rem",
          textAlign: "center",
          cursor: "pointer",
          background: isDragging ? "var(--surface-raised)" : "var(--surface)",
          transition: "border-color 0.15s ease, background 0.15s ease",
        }}
      >
        <p
          style={{
            margin: 0,
            fontFamily: "var(--font-mono)",
            fontSize: "0.78rem",
            color: "var(--text-dim)",
          }}
        >
          glisser un PDF Huawei ici
        </p>
        <p
          style={{
            margin: "0.2rem 0 0",
            fontSize: "0.72rem",
            color: "var(--text-faint)",
          }}
        >
          ou cliquer pour parcourir
        </p>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf"
          multiple
          hidden
          onChange={(e) => handleFiles(e.target.files)}
        />
      </div>

      {uploads.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
          {uploads.map((u) => (
            <UploadRow key={u.id} item={u} />
          ))}
        </div>
      )}
    </div>
  );
}

function UploadRow({ item }: { item: UploadItem }) {
  const dotClass =
    item.status === "success"
      ? "status-dot--live"
      : item.status === "error"
      ? "status-dot--critical"
      : "status-dot--warn";

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        padding: "0.5rem 0.65rem",
        fontSize: "0.78rem",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
        <span className={`status-dot ${dotClass}`} />
        <span style={{ color: "var(--text)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {item.filename}
        </span>
      </div>

      {item.status === "uploading" && (
        <div
          style={{
            marginTop: "0.35rem",
            height: "3px",
            borderRadius: "2px",
            background: "var(--surface-raised)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              height: "100%",
              width: `${item.progress}%`,
              background: "var(--accent-live)",
              transition: "width 0.2s ease",
            }}
          />
        </div>
      )}

      {item.message && (
        <div
          style={{
            marginTop: "0.25rem",
            fontFamily: "var(--font-mono)",
            fontSize: "0.68rem",
            color:
              item.status === "error" ? "var(--accent-critical)" : "var(--text-faint)",
          }}
        >
          {item.message}
        </div>
      )}
    </div>
  );
}
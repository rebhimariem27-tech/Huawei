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
    <div className="upload-zone">
      <div
        className="upload-dropzone"
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
        data-dragging={isDragging ? "true" : "false"}
      >
        <UploadIcon active={isDragging} />
        <p className="upload-dropzone__title">
          glisser un PDF Huawei ici
        </p>
        <p className="upload-dropzone__subtitle">
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
        <div className="upload-list">
          {uploads.map((u) => (
            <UploadRow key={u.id} item={u} />
          ))}
        </div>
      )}
    </div>
  );
}

function UploadIcon({ active }: { active: boolean }) {
  const color = active ? "var(--brand-red)" : "var(--text-faint)";
  return (
    <svg
      width="22"
      height="22"
      viewBox="0 0 24 24"
      fill="none"
      style={{ transition: "stroke 0.15s ease" }}
      aria-hidden="true"
    >
      <path
        d="M12 15.5V4M12 4L7.5 8.5M12 4L16.5 8.5"
        stroke={color}
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M4.5 15.5V17.5C4.5 18.6 5.4 19.5 6.5 19.5H17.5C18.6 19.5 19.5 18.6 19.5 17.5V15.5"
        stroke={color}
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function UploadRow({ item }: { item: UploadItem }) {
  const dotClass =
    item.status === "success"
      ? "status-dot--live"
      : item.status === "error"
      ? "status-dot--critical"
      : "status-dot--warn";

  const accent =
    item.status === "success"
      ? "var(--accent-live)"
      : item.status === "error"
      ? "var(--accent-critical)"
      : "var(--accent-warn)";

  return (
    <div
      className="upload-row"
      style={{
        borderLeftColor: accent,
      }}
    >
      <div className="upload-row__head">
        <span className={`status-dot ${dotClass}`} />
        <span className="upload-row__name">
          {item.filename}
        </span>
      </div>

      {item.status === "uploading" && (
        <div className="upload-row__progress">
          <div className="upload-row__progress-fill" style={{ width: `${item.progress}%` }} />
        </div>
      )}

      {item.message && (
        <div className="upload-row__message">
          {item.message}
        </div>
      )}
    </div>
  );
}